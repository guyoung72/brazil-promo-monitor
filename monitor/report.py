"""일일 변화 계산(digest) + 정적 HTML 대시보드 생성."""
from __future__ import annotations

import html
import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

CAT_KO = {"tv": "TV", "refrigerator": "냉장고", "washer": "세탁기", "other": "사이트 전체"}
PAGE_KO = {"home": "메인/오퍼", "tv": "TV", "refrigerator": "냉장고", "washer": "세탁기"}
TYPE_KO = {"self": "자사", "ecommerce": "이커머스", "d2c": "제조사 D2C"}
CAT_EN = {"tv": "TV", "refrigerator": "Refrigerators", "washer": "Washers", "other": "Site-wide"}
PAGE_EN = {"home": "Home/Offers", "tv": "TV", "refrigerator": "Refrigerators", "washer": "Washers"}
TYPE_EN = {"self": "LG (own)", "ecommerce": "E-commerce", "d2c": "Manufacturer D2C"}
MECH_EN = {"percent_off": "% off", "price_drop": "Price cut", "coupon": "Coupon", "cashback": "Cashback",
           "installments": "Interest-free installments", "pix_discount": "PIX discount",
           "free_shipping": "Free shipping", "gift_bundle": "Gift/bundle", "trade_in": "Trade-in",
           "flash_sale": "Flash sale", "other": "Other"}
MECH_KO = {"percent_off": "% 할인", "price_drop": "가격 인하", "coupon": "쿠폰", "cashback": "캐시백",
           "installments": "무이자 할부", "pix_discount": "PIX 할인", "free_shipping": "무료배송",
           "gift_bundle": "사은품/번들", "trade_in": "보상판매", "flash_sale": "타임세일", "other": "기타"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _brand(b: str) -> str:
    b = (b or "").strip()
    return "LG" if _norm(b) in ("lg", "lgelectronics") else b.title() if b.islower() else b


def build_digest(store, cfg: dict, run_date: str) -> dict:
    sites = {s["id"]: s for s in cfg["sites"]}
    pages = store.pages_on(run_date)
    camps = store.campaigns_on(run_date)
    ended = store.ended_on(run_date)
    prods = store.products_on(run_date)
    prev_date = store.previous_date(run_date)
    prev_prods = store.products_on(prev_date)

    # 가격 하락: (사이트, 모델) 기준 전일 판매가 대비
    thr = cfg["teams"].get("price_drop_alert_pct", 5)
    prev_idx = {(p["site_id"], _norm(p["model"])): p for p in prev_prods if p["sale_price"]}
    drops, seen = [], set()
    for p in prods:
        key = (p["site_id"], _norm(p["model"]))
        if key in seen or not p["sale_price"] or key not in prev_idx:
            continue
        seen.add(key)
        old = prev_idx[key]["sale_price"]
        chg = (p["sale_price"] - old) / old * 100
        if chg <= -thr:
            drops.append({**p, "old_price": old, "change_pct": round(chg, 1)})
    drops.sort(key=lambda d: d["change_pct"])

    # 브랜드별 평균 할인율(전 사이트, 카테고리별)
    by_brand = defaultdict(lambda: defaultdict(list))
    by_site = defaultdict(lambda: defaultdict(list))
    for p in prods:
        if p["discount_pct"] is not None and p["category"] in CAT_KO and p["category"] != "other":
            by_brand[p["category"]][_brand(p["brand"])].append(p["discount_pct"])
            by_site[p["site_id"]][p["category"]].append(p["discount_pct"])

    def avg(v):
        return round(sum(v) / len(v), 1) if v else None

    brand_stats = {c: sorted(({"brand": b, "avg": avg(v), "n": len(v)} for b, v in d.items() if len(v) >= 2),
                             key=lambda x: -x["avg"])[:8]
                   for c, d in by_brand.items()}
    site_stats = {s: {c: {"avg": avg(v), "n": len(v)} for c, v in d.items()} for s, d in by_site.items()}

    return {
        "run_date": run_date, "prev_date": prev_date, "drop_thr": thr,
        "sites": sites, "pages": pages,
        "failures": [p for p in pages if p["status"] != "ok"],
        "campaigns": camps,
        "new_campaigns": [c for c in camps if c["status"] == "new"],
        "ended_campaigns": ended,
        "products": prods, "price_drops": drops,
        "brand_stats": brand_stats, "site_stats": site_stats,
        "blocks": build_blocks(store, run_date),
    }


def build_blocks(store, run_date: str) -> list[dict]:
    """일별 캠페인 기록 → 프로모션 블록(한 프로모션 = 한 블록, 시작일~마지막 확인일).

    - 같은 페이지에서 'continuing'(previous_id)으로 이어진 기록은 하나의 블록.
    - 같은 사이트의 다른 페이지(메인/카테고리)에 같은 문구로 동시에 걸린 행사는 하나로 합친다.
    - 가장 최근 정상 수집일에도 보였으면 진행 중(ongoing), 아니면 마지막 확인일에 종료된 것으로 본다.
    """
    rows = store.campaigns_until(run_date)
    ended = store.ended_until(run_date)
    last_ok = store.last_ok_dates(run_date)

    root, blocks = {}, {}
    for r in rows:  # 날짜순이므로 previous_id의 root가 항상 먼저 정해진다
        rid = root.get(r["previous_id"], r["id"]) if r["previous_id"] else r["id"]
        root[r["id"]] = rid
        b = blocks.get(rid)
        if b is None:
            b = blocks[rid] = {"site_id": r["site_id"], "start": r["run_date"], "pages": set(),
                               "categories": set(), "mechanics": set(), "brands": set(),
                               "hero": False, "ids": []}
        b["end"] = r["run_date"]
        b["ids"].append(r["id"])
        b["pages"].add(r["page_key"])
        b["categories"].update(r["categories"])
        b["mechanics"].update(r["mechanics"])
        b["brands"].update(r["brands"])
        b["hero"] = b["hero"] or bool(r["is_hero"])
        # 문구는 가장 최근 기록 기준
        b.update(title_pt=r["title_pt"], title_ko=r["title_ko"], title_en=r.get("title_en") or r["title_pt"],
                 discount=r["discount_text"], conditions_ko=r["conditions_ko"],
                 conditions_en=r.get("conditions_en") or r["conditions_ko"], valid_until=r["valid_until"])

    # 같은 사이트·같은 문구·기간이 겹치거나 맞닿은 블록 병합
    by_key = defaultdict(list)
    for b in blocks.values():
        by_key[(b["site_id"], _norm(b["title_pt"]))].append(b)
    merged = []
    for group in by_key.values():
        group.sort(key=lambda b: b["start"])
        cur = group[0]
        for b in group[1:]:
            if date.fromisoformat(b["start"]) <= date.fromisoformat(cur["end"]) + timedelta(days=1):
                newer = b if b["end"] >= cur["end"] else cur
                cur = {**cur, **{k: newer[k] for k in ("title_pt", "title_ko", "title_en", "discount",
                                                       "conditions_ko", "conditions_en", "valid_until")},
                       "end": max(cur["end"], b["end"]), "pages": cur["pages"] | b["pages"],
                       "categories": cur["categories"] | b["categories"],
                       "mechanics": cur["mechanics"] | b["mechanics"], "brands": cur["brands"] | b["brands"],
                       "hero": cur["hero"] or b["hero"], "ids": cur["ids"] + b["ids"]}
            else:
                merged.append(cur)
                cur = b
        merged.append(cur)

    out = []
    for b in merged:
        ongoing = any(last_ok.get((b["site_id"], pk)) == b["end"] for pk in b["pages"])
        ended_seen = min((ended[i] for i in b["ids"] if i in ended), default=None)
        out.append({
            "site": b["site_id"], "start": b["start"], "end": b["end"], "ongoing": ongoing,
            "ended_seen": None if ongoing else ended_seen,
            "days": (date.fromisoformat(b["end"]) - date.fromisoformat(b["start"])).days + 1,
            "title_pt": b["title_pt"], "title_ko": b["title_ko"], "title_en": b["title_en"],
            "discount": b["discount"], "conditions_ko": b["conditions_ko"],
            "conditions_en": b["conditions_en"], "valid_until": b["valid_until"],
            "pages": sorted(b["pages"]), "categories": sorted(b["categories"]),
            "mechanics": sorted(b["mechanics"]), "brands": sorted(b["brands"]), "hero": b["hero"],
        })
    out.sort(key=lambda b: (b["site"], b["start"], b["end"]))
    return out


def digest_for_llm(d: dict) -> dict:
    """요약용으로 가볍게 줄인 데이터."""
    name = lambda sid: d["sites"].get(sid, {}).get("name", sid)
    return {
        "date": d["run_date"], "previous_date": d["prev_date"],
        "new_campaigns": [{"site": name(c["site_id"]), "title": c["title_pt"], "title_ko": c["title_ko"],
                           "discount": c["discount_text"], "categories": c["categories"],
                           "brands": c["brands"], "hero": bool(c["is_hero"])} for c in d["new_campaigns"]],
        "ended_campaigns": [{"site": name(c["site_id"]), "title_ko": c["title_ko"]} for c in d["ended_campaigns"]],
        "price_drops": [{"site": name(p["site_id"]), "brand": p["brand"], "model": p["model"],
                         "category": p["category"], "old": p["old_price"], "new": p["sale_price"],
                         "change_pct": p["change_pct"]} for p in d["price_drops"][:25]],
        "avg_discount_by_brand": d["brand_stats"],
        "failed_pages": [f'{name(p["site_id"])}/{p["page_key"]}: {p["status"]}' for p in d["failures"]],
    }




# ---------------------------------------------------------------- HTML
CSS = """
.viz-root{color-scheme:light;--surface-0:#f4f4f2;--surface-1:#fcfcfb;--border:#e2e1dc;
 --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7974;--series-1:#2a78d6;
 --series-1-soft:#d9e7f8;--muted-bar:#c9c8c2;--ended-soft:#e9e8e4;--good:#008300;--critical:#d03b3a;
 --warning:#b87900;--chip:#ecebe7;--grid-line:#ecebe7}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
 --surface-0:#121211;--surface-1:#1a1a19;--border:#2f2f2d;--text-primary:#f0efec;--text-secondary:#c3c2b7;
 --text-muted:#8f8e86;--series-1:#3987e5;--series-1-soft:#1d3350;--muted-bar:#4a4a46;--ended-soft:#2a2a28;
 --good:#3fa23f;--critical:#e66767;--warning:#d9a12c;--chip:#2a2a28;--grid-line:#242423}}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--surface-0);color:var(--text-primary);font:14px/1.5 -apple-system,"Segoe UI",
 "Malgun Gothic","Apple SD Gothic Neo",sans-serif;min-height:100vh;padding:24px}
.wrap{max-width:1280px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}h3{font-size:15px;margin:0 0 8px}
.sub{color:var(--text-secondary);margin:0 0 16px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--surface-1)}
.seg button{border:0;background:transparent;color:var(--text-secondary);padding:6px 12px;font:inherit;cursor:pointer}
.seg button.on{background:var(--text-primary);color:var(--surface-1)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin:0 0 20px}
.tabs button{border:0;background:none;font:inherit;font-weight:600;color:var(--text-secondary);
 padding:10px 14px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs button.on{color:var(--text-primary);border-bottom-color:var(--text-primary)}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi .v{font-size:28px;font-weight:650}.kpi .l{color:var(--text-secondary);font-size:13px}
.summary ul{margin:0;padding-left:20px}.summary li{margin:4px 0}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}
.filters button{border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);
 padding:6px 12px;border-radius:999px;cursor:pointer;font:inherit}
.filters button.on{background:var(--text-primary);color:var(--surface-1)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(380px,100%),1fr));gap:12px}
.site-h{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.tag{font-size:12px;color:var(--text-secondary)}
.camp{border-top:1px solid var(--border);padding:8px 0}.camp:first-of-type{border-top:0}
.camp .t{font-weight:600}.camp .pt{color:var(--text-muted);font-size:12px}
.chip{display:inline-block;background:var(--chip);border-radius:4px;padding:0 6px;font-size:12px;margin:2px 4px 0 0;color:var(--text-secondary)}
.badge-new{display:inline-block;border:1px solid var(--critical);color:var(--critical);border-radius:4px;
 padding:0 5px;font-size:11px;font-weight:700;margin-right:6px}
.badge-hero{display:inline-block;border:1px solid var(--text-secondary);color:var(--text-secondary);border-radius:4px;padding:0 5px;font-size:11px;margin-right:6px}
.note{color:var(--text-secondary);font-size:13px;margin:4px 0 8px}
.err{color:var(--critical);font-size:13px}
details summary{cursor:pointer;color:var(--text-secondary);font-size:13px}
details img{width:100%;border:1px solid var(--border);border-radius:6px;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--text-secondary);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.down{color:var(--critical);font-weight:600}
.bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:12px}
.bar-row{display:grid;grid-template-columns:110px 1fr 52px;align-items:center;gap:8px;margin:5px 0;font-size:13px;position:relative}
.bar-track{height:14px}.bar{height:14px;border-radius:0 4px 4px 0;background:var(--muted-bar)}
.bar.lg{background:var(--series-1)}.bar-row .lab.lg{font-weight:700}
.bar-row:hover .bar{outline:2px solid var(--text-primary);outline-offset:1px}
.tip{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);padding:6px 8px;
 border-radius:4px;font-size:12px;display:none;z-index:9;max-width:340px;white-space:pre-line}
.hidden{display:none!important}
/* 언어 전환: 고정 문구는 두 언어를 모두 출력하고 하나만 보인다 */
.viz-root:not(.lang-en) .en{display:none!important}.viz-root.lang-en .ko{display:none!important}
/* 캘린더 */
.cal-bar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
.cal-bar .filters{margin:0}
.cal-year{display:inline-flex;align-items:center;gap:6px;font-weight:650}
.cal-year button{border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);
 width:30px;height:30px;border-radius:6px;cursor:pointer;font:inherit}
.cal-year button:disabled{opacity:.35;cursor:default}
.cal-scroll{overflow-x:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface-1)}
.cal{display:grid;position:relative;font-size:12px;width:max-content}
.cal .hd{position:sticky;top:0;background:var(--surface-1);color:var(--text-secondary);
 border-bottom:1px solid var(--border);padding:4px 0;text-align:center;white-space:nowrap;overflow:hidden;z-index:2}
.cal .hd.mon{text-align:left;padding-left:6px;font-weight:600;color:var(--text-primary);border-left:1px solid var(--border)}
.cal .hd.unit{font-variant-numeric:tabular-nums}.cal .hd.unit.today{color:var(--series-1);font-weight:700}
.cal .corner{position:sticky;left:0;z-index:4;background:var(--surface-1);border-bottom:1px solid var(--border);
 border-right:1px solid var(--border)}
.cal .site{position:sticky;left:0;z-index:3;background:var(--surface-1);border-right:1px solid var(--border);
 border-bottom:1px solid var(--border);padding:6px 10px;display:flex;flex-direction:column;justify-content:center}
.cal .site b{font-size:13px}.cal .site span{color:var(--text-muted);font-size:11px}
.cal .lane-bg{border-bottom:1px solid var(--border)}
.cal .today-line{background:var(--series-1);opacity:.12;z-index:0;pointer-events:none}
.cal .blk{margin:3px 1px;border-radius:5px;padding:3px 6px;overflow:hidden;white-space:nowrap;cursor:pointer;
 z-index:1;min-width:0;line-height:1.3;border:1px solid transparent}
.cal .blk .bt{font-weight:600;overflow:hidden;text-overflow:ellipsis}
.cal .blk .bd{color:var(--text-secondary);font-size:11px;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
.cal .blk.on{background:var(--series-1-soft);border-left:3px solid var(--series-1)}
.cal .blk.off{background:var(--ended-soft);border-left:3px solid var(--muted-bar)}
.cal .blk.hero .bt::before{content:"★ ";color:var(--warning)}
.cal .blk:hover,.cal .blk.sel{outline:2px solid var(--text-primary);outline-offset:-1px}
.cal .empty{color:var(--text-muted);padding:8px 10px;border-bottom:1px solid var(--border)}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--text-secondary);font-size:12px;margin:8px 0 0}
.legend i{display:inline-block;width:14px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}
.cal-detail{margin-top:12px}.cal-detail dl{display:grid;grid-template-columns:120px 1fr;gap:4px 12px;margin:8px 0 0}
.cal-detail dt{color:var(--text-secondary)}.cal-detail dd{margin:0}
@media (max-width:640px){.viz-root{padding:16px}.cal-detail dl{grid-template-columns:1fr}}
"""

JS = r"""
const root=document.querySelector('.viz-root');
const DATA=JSON.parse(document.getElementById('cal-data').textContent);
const I=DATA.i18n;
const store={get(k){try{return localStorage.getItem(k)}catch(e){return null}},
             set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
let lang=new URLSearchParams(location.search).get('lang')||store.get('promo-lang')||'ko';
function t(k){return (I[lang]||I.ko)[k]??k}
function setLang(l){
 lang=l==='en'?'en':'ko';store.set('promo-lang',lang);
 root.classList.toggle('lang-en',lang==='en');document.documentElement.lang=lang;
 document.querySelectorAll('[data-lang-btn]').forEach(b=>b.classList.toggle('on',b.dataset.langBtn===lang));
 document.title=t('doc_title')+' · '+DATA.run_date;
 if(calBuilt)renderCal();
}
/* 툴팁 */
const tip=document.getElementById('tip');
function showTip(e,text){tip.textContent=text;tip.style.display='block';
 const x=Math.min(e.clientX+12,innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px'}
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('mousemove',e=>showTip(e,lang==='en'?(el.dataset.tipEn||el.dataset.tip):el.dataset.tip));
 el.addEventListener('mouseleave',()=>tip.style.display='none')});
/* 일일 현황 필터 */
function filt(group,val,btn){
 document.querySelectorAll('[data-fgroup="'+group+'"] button').forEach(b=>b.classList.toggle('on',b===btn));
 document.querySelectorAll('[data-'+group+']').forEach(el=>{
  const v=el.getAttribute('data-'+group).split(' ');el.classList.toggle('hidden',val!=='all'&&!v.includes(val))});
}
/* 탭 */
function showTab(name){
 document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('on',b.dataset.tab===name));
 document.querySelectorAll('[data-panel]').forEach(p=>p.classList.toggle('hidden',p.dataset.panel!==name));
 if(history.replaceState)history.replaceState(null,'',name==='calendar'?'#calendar':location.pathname+location.search);
 if(name==='calendar'){if(!calBuilt){calBuilt=true;renderCal()}scrollToToday()}
}

/* ---------------- 52주 프로모션 캘린더 ---------------- */
const DAY=86400000;
const P=s=>{const [y,m,d]=s.split('-').map(Number);return Date.UTC(y,m-1,d)};
const ISO=ms=>new Date(ms).toISOString().slice(0,10);
function isoWeek1Monday(y){const j4=Date.UTC(y,0,4);const dow=(new Date(j4).getUTCDay()+6)%7;return j4-dow*DAY}
function isoWeeksIn(y){return Math.round((isoWeek1Monday(y+1)-isoWeek1Monday(y))/DAY/7)}
function fmtDate(s,withYear){const o={month:'short',day:'numeric',timeZone:'UTC'};if(withYear)o.year='numeric';
 return new Date(P(s)).toLocaleDateString(lang==='en'?'en-US':'ko-KR',o)}
const todayMs=P(DATA.run_date);
const years=(()=>{const ys=DATA.blocks.map(b=>+b.start.slice(0,4)).concat(DATA.blocks.map(b=>+b.end.slice(0,4)));
 const ty=+DATA.run_date.slice(0,4);const lo=Math.min(ty,...ys);const out=[];for(let y=lo;y<=ty;y++)out.push(y);return out})();
let calBuilt=false,view=store.get('promo-cal-view')||'week',year=+DATA.run_date.slice(0,4),cat='all',selected=null;
function setView(v){view=v;store.set('promo-cal-view',v);renderCal();scrollToToday()}
function setYear(d){const i=years.indexOf(year)+d;if(i>=0&&i<years.length){year=years[i];renderCal();scrollToToday()}}
function setCat(c){cat=c;renderCal()}

function units(){ // 열 정의: [{start,end,label,month}] (UTC ms, end 포함)
 const out=[];
 if(view==='day'){
  for(let ms=Date.UTC(year,0,1);ms<Date.UTC(year+1,0,1);ms+=DAY){const d=new Date(ms);
   out.push({start:ms,end:ms,label:String(d.getUTCDate()),month:d.getUTCMonth()})}
 }else{
  const m0=isoWeek1Monday(year),n=isoWeeksIn(year);
  for(let w=0;w<n;w++){const s=m0+w*7*DAY;out.push({start:s,end:s+6*DAY,label:'W'+(w+1),
   month:new Date(s+3*DAY).getUTCMonth()})} // 목요일이 속한 달
 }
 return out;
}
function blockLabel(b){return lang==='en'?b.title_en:b.title_ko}
function blockRange(b){return fmtDate(b.start)+' – '+(b.ongoing?t('ongoing'):fmtDate(b.end))}
function tipText(b){
 return blockLabel(b)+(b.discount?' · '+b.discount:'')+'\n'+
  fmtDate(b.start,true)+' – '+fmtDate(b.end,true)+(b.ongoing?' ('+t('ongoing')+')':'')+' · '+b.days+t('days_suffix');
}
function renderCal(){
 const host=document.getElementById('cal');const U=units();const N=U.length;
 const colW=view==='day'?28:64,labelW=innerWidth<640?120:170;
 const first=U[0].start,last=U[N-1].end;
 const idxOf=ms=>{if(view==='day')return Math.round((ms-first)/DAY);return Math.floor((ms-first)/DAY/7)};
 document.getElementById('cal-year').textContent=view==='day'?String(year):year+' (ISO '+t('weeks')+' 1–'+N+')';
 document.getElementById('cal-prev').disabled=years.indexOf(year)<=0;
 document.getElementById('cal-next').disabled=years.indexOf(year)>=years.length-1;
 document.querySelectorAll('[data-view-btn]').forEach(b=>b.classList.toggle('on',b.dataset.viewBtn===view));
 document.querySelectorAll('[data-calcat-btn]').forEach(b=>b.classList.toggle('on',b.dataset.calcatBtn===cat));

 const html=[];
 // 헤더 1행: 월, 2행: 일/주차
 html.push(`<div class="corner" style="grid-row:1/3;grid-column:1"></div>`);
 let i=0;while(i<N){let j=i;while(j+1<N&&U[j+1].month===U[i].month)j++;
  html.push(`<div class="hd mon" style="grid-row:1;grid-column:${i+2}/${j+3}">${I[lang].months[U[i].month]}</div>`);i=j+1}
 const todayIdx=(todayMs>=first&&todayMs<=last)?idxOf(todayMs):-1;
 U.forEach((u,k)=>html.push(`<div class="hd unit${k===todayIdx?' today':''}" style="grid-row:2;grid-column:${k+2}"`+
  (view==='week'?` title="${fmtDate(ISO(u.start))} – ${fmtDate(ISO(u.end))}"`:'')+`>${u.label}</div>`));

 let row=3,shown=0;
 const visible=DATA.blocks.filter(b=>(cat==='all'||b.categories.includes(cat))&&P(b.end)>=first&&P(b.start)<=last);
 for(const s of DATA.sites){
  const bs=visible.filter(b=>b.site===s.id).map(b=>({b,a:idxOf(Math.max(P(b.start),first)),z:idxOf(Math.min(P(b.end),last))}))
   .sort((x,y)=>x.a-y.a||y.z-x.z);
  const lanes=[];bs.forEach(o=>{let L=lanes.findIndex(end=>end<o.a);if(L<0){L=lanes.length;lanes.push(-1)}lanes[L]=o.z;o.lane=L});
  const n=Math.max(1,lanes.length);
  html.push(`<div class="site" style="grid-row:${row}/${row+n};grid-column:1"><b>${esc(s.name)}</b><span>${esc(I[lang].types[s.type]||s.type)}</span></div>`);
  for(let L=0;L<n;L++)html.push(`<div class="lane-bg" style="grid-row:${row+L};grid-column:2/${N+2}"></div>`);
  bs.forEach(o=>{const b=o.b;shown++;
   html.push(`<div class="blk ${b.ongoing?'on':'off'}${b.hero?' hero':''}${selected===b.idx?' sel':''}" data-i="${b.idx}"`+
    ` style="grid-row:${row+o.lane};grid-column:${o.a+2}/${o.z+3}">`+
    `<div class="bt">${esc(blockLabel(b))}</div><div class="bd">${esc(blockRange(b))}</div></div>`)});
  row+=n;
 }
 if(todayIdx>=0)html.push(`<div class="today-line" style="grid-row:3/${row};grid-column:${todayIdx+2}"></div>`);
 host.style.gridTemplateColumns=`${labelW}px repeat(${N},${colW}px)`;
 host.style.gridTemplateRows=`auto auto repeat(${row-3},minmax(44px,auto))`;
 host.innerHTML=html.join('');
 document.getElementById('cal-count').textContent=t('blocks_count').replace('{n}',shown);
 host.querySelectorAll('.blk').forEach(el=>{const b=DATA.blocks[+el.dataset.i];
  el.addEventListener('mousemove',e=>showTip(e,tipText(b)));
  el.addEventListener('mouseleave',()=>tip.style.display='none');
  el.addEventListener('click',()=>{selected=b.idx;renderCal();showDetail(b)})});
 if(selected!==null)showDetail(DATA.blocks[selected]);
}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function showDetail(b){
 const el=document.getElementById('cal-detail');const site=DATA.sites.find(s=>s.id===b.site);
 const row=(k,v)=>v?`<dt>${t(k)}</dt><dd>${v}</dd>`:'';
 el.classList.remove('hidden');
 el.innerHTML=`<h3>${b.hero?'★ ':''}${esc(blockLabel(b))}</h3><div class="tag">${esc(b.title_pt)}</div><dl>`+
  row('f_site',esc(site?site.name:b.site))+
  row('f_period',esc(fmtDate(b.start,true)+' – '+fmtDate(b.end,true))+' · '+b.days+t('days_suffix'))+
  row('f_status',b.ongoing?t('ongoing_long'):esc(t('ended_long').replace('{d}',fmtDate(b.end,true))))+
  row('f_discount',esc(b.discount))+
  row('f_conditions',esc(lang==='en'?b.conditions_en:b.conditions_ko))+
  row('f_valid',b.valid_until?esc(b.valid_until):'')+
  row('f_mech',b.mechanics.map(m=>`<span class="chip">${esc(I[lang].mech[m]||m)}</span>`).join(''))+
  row('f_cat',b.categories.map(c=>`<span class="chip">${esc(I[lang].cats[c]||c)}</span>`).join(''))+
  row('f_brands',b.brands.map(x=>`<span class="chip">${esc(x)}</span>`).join(''))+
  row('f_pages',b.pages.map(p=>esc(I[lang].pages[p]||p)).join(', '))+`</dl>`;
}
function scrollToToday(){const sc=document.querySelector('.cal-scroll');const td=document.querySelector('.cal .hd.unit.today');
 if(sc&&td)sc.scrollLeft=Math.max(0,td.offsetLeft-sc.clientWidth*0.6)}
DATA.blocks.forEach((b,i)=>b.idx=i);
setLang(lang);
showTab(location.hash==='#calendar'?'calendar':'daily');
"""

# 화면 고정 문구(한/영). JS 캘린더도 같은 사전을 쓴다.
I18N = {
    "ko": {"doc_title": "브라질 이커머스 & D2C 프로모션 대시보드", "ongoing": "진행 중",
           "ongoing_long": "진행 중 (가장 최근 수집에서도 확인됨)", "ended_long": "종료 (마지막 확인 {d})",
           "days_suffix": "일", "weeks": "주차", "blocks_count": "프로모션 블록 {n}개",
           "f_site": "사이트", "f_period": "기간", "f_status": "상태", "f_discount": "할인",
           "f_conditions": "조건", "f_valid": "표시 종료일", "f_mech": "프로모션 유형", "f_cat": "카테고리",
           "f_brands": "브랜드", "f_pages": "확인된 페이지",
           "months": ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"],
           "types": TYPE_KO, "mech": MECH_KO, "cats": CAT_KO, "pages": PAGE_KO},
    "en": {"doc_title": "Brazil E-commerce & D2C Promotion Dashboard", "ongoing": "Ongoing",
           "ongoing_long": "Ongoing (seen in the latest capture)", "ended_long": "Ended (last seen {d})",
           "days_suffix": " days", "weeks": "weeks", "blocks_count": "{n} promotion blocks",
           "f_site": "Site", "f_period": "Period", "f_status": "Status", "f_discount": "Discount",
           "f_conditions": "Conditions", "f_valid": "Stated end date", "f_mech": "Mechanics",
           "f_cat": "Categories", "f_brands": "Brands", "f_pages": "Seen on pages",
           "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
           "types": TYPE_EN, "mech": MECH_EN, "cats": CAT_EN, "pages": PAGE_EN},
}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _t(ko, en) -> str:
    """두 언어를 모두 출력. CSS가 현재 언어만 보여준다."""
    return f'<span class="ko">{_e(ko)}</span><span class="en">{_e(en)}</span>'


def _brl(v) -> str:
    if v is None:
        return "–"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _filter_bar(group: str, opts: list[tuple[str, str, str]]) -> str:
    btns = [f'<button class="on" onclick="filt(\'{group}\',\'all\',this)">{_t("전체", "All")}</button>']
    btns += [f'<button onclick="filt(\'{group}\',\'{v}\',this)">{_t(ko, en)}</button>' for v, ko, en in opts]
    return f'<div class="filters" data-fgroup="{group}">{"".join(btns)}</div>'


def _bullets(md: str) -> str:
    return "".join(f"<li>{_e(l[2:] if l.startswith('- ') else l)}</li>" for l in md.splitlines() if l.strip())


def render_html(d: dict, summary: dict | str, out_file: Path, shots_root: Path) -> None:
    sites = d["sites"]
    name = lambda sid: sites.get(sid, {}).get("name", sid)
    rel = lambda p: os.path.relpath(p, out_file.parent) if p else ""
    n_ok = sum(1 for p in d["pages"] if p["status"] == "ok")
    if isinstance(summary, str):
        summary = {"ko": summary, "en": summary}

    kpis = [
        (len(d["new_campaigns"]), "신규 프로모션", "New promotions"),
        (len(d["ended_campaigns"]), "종료된 프로모션", "Ended promotions"),
        (len(d["price_drops"]), f'가격 하락 ≥{d["drop_thr"]}% 품목', f'Price drops ≥{d["drop_thr"]}%'),
        (f'{n_ok}/{len(d["pages"])}', "정상 수집 페이지", "Pages captured OK"),
    ]
    kpi_html = "".join(f'<div class="card kpi"><div class="v">{_e(v)}</div><div class="l">{_t(ko, en)}</div></div>'
                       for v, ko, en in kpis)
    bullets = (f'<ul class="ko">{_bullets(summary.get("ko", ""))}</ul>'
               f'<ul class="en">{_bullets(summary.get("en") or summary.get("ko", ""))}</ul>')

    # 사이트별 캠페인 카드
    by_site = defaultdict(list)
    for c in d["campaigns"]:
        by_site[c["site_id"]].append(c)
    pages_by_site = defaultdict(list)
    for p in d["pages"]:
        pages_by_site[p["site_id"]].append(p)
    cards = []
    for sid, s in sites.items():
        cs = sorted(by_site.get(sid, []), key=lambda c: (c["status"] != "new", not c["is_hero"]))
        body = []
        for c in cs:
            badges = ('<span class="badge-new">NEW</span>' if c["status"] == "new" else "") + \
                     (f'<span class="badge-hero">{_t("메인배너", "Hero banner")}</span>' if c["is_hero"] else "")
            chips = "".join(f'<span class="chip">{_t(MECH_KO.get(m, m), MECH_EN.get(m, m))}</span>'
                            for m in c["mechanics"])
            chips += "".join(f'<span class="chip">{_t(CAT_KO.get(x, x), CAT_EN.get(x, x))}</span>'
                             for x in c["categories"])
            chips += "".join(f'<span class="chip">{_e(b)}</span>' for b in c["brands"])
            until = f'~{c["valid_until"]}' if c["valid_until"] else None
            extra_ko = " · ".join(x for x in [c["discount_text"], c["conditions_ko"], until,
                                              f'최초 확인 {c["first_seen"]}'] if x)
            extra_en = " · ".join(x for x in [c["discount_text"], c.get("conditions_en") or c["conditions_ko"],
                                              until, f'First seen {c["first_seen"]}'] if x)
            body.append(f'<div class="camp" data-cat="{" ".join(c["categories"])}" data-new="{c["status"]}">'
                        f'<div class="t">{badges}{_t(c["title_ko"], c.get("title_en") or c["title_pt"])}</div>'
                        f'<div class="pt">{_e(c["title_pt"])}</div>'
                        f'<div class="tag">{_t(extra_ko, extra_en)}</div>{chips}</div>')
        shots = []
        for p in sorted(pages_by_site.get(sid, []), key=lambda p: list(PAGE_KO).index(p["page_key"])
                        if p["page_key"] in PAGE_KO else 9):
            lko, len_ = PAGE_KO.get(p["page_key"], p["page_key"]), PAGE_EN.get(p["page_key"], p["page_key"])
            if p["status"] != "ok":
                shots.append(f'<div class="err">⚠ {_t(f"{lko}: 수집 실패", f"{len_}: capture failed")} '
                             f'({_e(p["status"])}) {_e(p["error"])}</div>')
            if p["page_note"]:
                shots.append(f'<div class="note"><b>{_t(lko, len_)}</b> — '
                             f'{_t(p["page_note"], p.get("page_note_en") or p["page_note"])}</div>')
            if p["screenshot"]:
                shots.append(f'<details><summary>{_t(f"{lko} 스크린샷", f"{len_} screenshot")} · '
                             f'<a href="{_e(p["url"])}" target="_blank" rel="noopener">'
                             f'{_t("원본 페이지", "Source page")}</a></summary>'
                             f'<img loading="lazy" src="{_e(rel(p["screenshot"]))}" alt="{_e(name(sid))} {_e(len_)}"></details>')
        if not pages_by_site.get(sid):
            body = [f'<div class="tag">{_t("오늘 수집 대상 아님(또는 미실행)", "Not captured today (or not run)")}</div>']
        elif not body:
            body = [f'<div class="tag">{_t("TV/냉장고/세탁기 관련 프로모션 없음", "No TV/refrigerator/washer promotions")}</div>']
        cards.append(f'<div class="card" data-stype="{s["type"]}"><div class="site-h"><h3>{_e(s["name"])}</h3>'
                     f'<span class="tag">{_t(TYPE_KO.get(s["type"], s["type"]), TYPE_EN.get(s["type"], s["type"]))}</span></div>'
                     f'{"".join(body)}<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px">'
                     f'{"".join(shots)}</div></div>')

    # 브랜드별 평균 할인율 막대
    bar_blocks = []
    for cat in ["tv", "refrigerator", "washer"]:
        rows = d["brand_stats"].get(cat, [])
        if not rows:
            continue
        mx = max(r["avg"] for r in rows) or 1
        rr = "".join(
            f'<div class="bar-row" data-tip="{_e(r["brand"])} · 평균 {r["avg"]}% · 상품 {r["n"]}개" '
            f'data-tip-en="{_e(r["brand"])} · avg {r["avg"]}% · {r["n"]} products">'
            f'<span class="lab {"lg" if r["brand"] == "LG" else ""}">{_e(r["brand"])}</span>'
            f'<div class="bar-track"><div class="bar {"lg" if r["brand"] == "LG" else ""}" '
            f'style="width:{max(r["avg"] / mx * 100, 1):.1f}%"></div></div>'
            f'<span class="num">{r["avg"]}%</span></div>' for r in rows)
        bar_blocks.append(f'<div class="card"><h3>{_t(CAT_KO[cat] + " — 브랜드별 평균 표시 할인율", CAT_EN[cat] + " — average displayed discount by brand")}</h3>{rr}'
                          f'<div class="tag">{_t("전 사이트 수집 상품 기준 · 상품 2개 이상 브랜드만 · LG 강조", "All captured products · brands with 2+ products · LG highlighted")}</div></div>')

    # 사이트 × 카테고리 평균 할인율 표
    st_rows = "".join(
        f'<tr><td>{_e(name(sid))}</td>' + "".join(
            f'<td class="num">{(str(v["avg"]) + "%") if (v := d["site_stats"].get(sid, {}).get(c)) else "–"}'
            f'{(" <span class=tag>(" + str(v["n"]) + ")</span>") if v else ""}</td>'
            for c in ["tv", "refrigerator", "washer"]) + "</tr>"
        for sid in sites)

    cat_cell = lambda c: _t(CAT_KO.get(c, c), CAT_EN.get(c, c))
    drop_rows = "".join(
        f'<tr data-cat="{p["category"]}"><td>{_e(name(p["site_id"]))}</td><td>{_e(p["brand"])}</td>'
        f'<td>{_e(p["model"])}</td><td>{cat_cell(p["category"])}</td>'
        f'<td class="num">{_brl(p["old_price"])}</td><td class="num">{_brl(p["sale_price"])}</td>'
        f'<td class="num down">{p["change_pct"]}%</td></tr>' for p in d["price_drops"])

    prod_rows = "".join(
        f'<tr data-pcat="{p["category"]}"><td>{_e(name(p["site_id"]))}</td><td>{_e(p["brand"])}</td>'
        f'<td>{_e(p["model"])}</td><td>{cat_cell(p["category"])}</td>'
        f'<td class="num">{_brl(p["list_price"])}</td><td class="num">{_brl(p["sale_price"])}</td>'
        f'<td class="num">{_brl(p["pix_price"])}</td>'
        f'<td class="num">{"" if p["discount_pct"] is None else str(p["discount_pct"]) + "%"}</td>'
        f'<td>{_e(p["installments"])}</td></tr>'
        for p in sorted(d["products"], key=lambda p: (p["category"], p["site_id"], _brand(p["brand"]) != "LG")))

    ended_rows = "".join(f'<li>{_e(name(c["site_id"]))} — {_t(c["title_ko"], c.get("title_en") or c["title_pt"])} '
                         f'<span class="tag">({_e(c["first_seen"])} ~ {_e(c["run_date"])})</span></li>'
                         for c in d["ended_campaigns"]) or f'<li>{_t("없음", "None")}</li>'

    cat_opts = [("tv", "TV", "TV"), ("refrigerator", "냉장고", "Refrigerators"), ("washer", "세탁기", "Washers"),
                ("other", "사이트 전체", "Site-wide")]
    th = lambda ko, en, num=False: f'<th{" class=num" if num else ""}>{_t(ko, en)}</th>'

    cal_data = json.dumps({
        "run_date": d["run_date"], "i18n": I18N,
        "sites": [{"id": sid, "name": s["name"], "type": s["type"]} for sid, s in sites.items()],
        "blocks": d.get("blocks", []),
    }, ensure_ascii=False).replace("</", "<\\/")
    cal_cats = "".join(f'<button data-calcat-btn="{v}" onclick="setCat(\'{v}\')">{_t(ko, en)}</button>'
                       for v, ko, en in [("all", "전체", "All")] + cat_opts)

    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(I18N["ko"]["doc_title"])} · {d["run_date"]}</title><style>{CSS}</style></head>
<body><div class="viz-root"><div class="wrap">
<div class="top"><div>
<h1>{_t(I18N["ko"]["doc_title"], I18N["en"]["doc_title"])}</h1>
<p class="sub">{_t(f'기준일 {d["run_date"]} (브라질 시간) · 비교 기준 {d["prev_date"] or "없음(첫 수집)"} · 중남미 D2C팀',
                   f'As of {d["run_date"]} (Brazil time) · compared with {d["prev_date"] or "none (first capture)"} · LATAM D2C team')}</p>
</div><div class="seg" role="group" aria-label="Language">
<button data-lang-btn="ko" onclick="setLang('ko')">한국어</button><button data-lang-btn="en" onclick="setLang('en')">English</button>
</div></div>
<div class="tabs" role="tablist">
<button data-tab="daily" onclick="showTab('daily')">{_t("일일 현황", "Daily overview")}</button>
<button data-tab="calendar" onclick="showTab('calendar')">{_t("52주 프로모션 캘린더", "52-week promotion calendar")}</button>
</div>

<section data-panel="daily">
<div class="kpis">{kpi_html}</div>
<h2>{_t("오늘의 요약", "Today's summary")}</h2><div class="card summary">{bullets}</div>

<h2>{_t("사이트별 프로모션", "Promotions by site")}</h2>
{_filter_bar("cat", cat_opts)}
<div class="filters" data-fgroup="stype"><button class="on" onclick="filt('stype','all',this)">{_t("전체 사이트", "All sites")}</button>
<button onclick="filt('stype','ecommerce',this)">{_t("이커머스", "E-commerce")}</button>
<button onclick="filt('stype','d2c',this)">{_t("제조사 D2C", "Manufacturer D2C")}</button>
<button onclick="filt('stype','self',this)">{_t("LG(자사)", "LG (own)")}</button></div>
<div class="grid">{"".join(cards)}</div>

<h2>{_t("전일 대비 가격 하락", "Price drops vs. previous day")}</h2>
<div class="card" style="overflow-x:auto"><table><thead><tr>{th("사이트", "Site")}{th("브랜드", "Brand")}{th("모델", "Model")}{th("카테고리", "Category")}
{th("전일", "Previous", True)}{th("오늘", "Today", True)}{th("변동", "Change", True)}</tr></thead>
<tbody>{drop_rows or f'<tr><td colspan="7">{_t("해당 없음", "None")}</td></tr>'}</tbody></table></div>

<h2>{_t("할인 강도 비교", "Discount depth comparison")}</h2>
<div class="bars">{"".join(bar_blocks) or f'<div class="card">{_t("데이터 없음", "No data")}</div>'}</div>
<div class="card" style="margin-top:12px;overflow-x:auto"><h3>{_t("사이트별 평균 표시 할인율 (괄호: 상품 수)", "Average displayed discount by site (in brackets: products)")}</h3>
<table><thead><tr>{th("사이트", "Site")}{th("TV", "TV", True)}{th("냉장고", "Refrigerators", True)}{th("세탁기", "Washers", True)}</tr></thead>
<tbody>{st_rows}</tbody></table></div>

<h2>{_t("종료된 프로모션", "Ended promotions")}</h2><div class="card"><ul>{ended_rows}</ul></div>

<h2>{_t("수집 상품 전체", "All captured products")}</h2>
{_filter_bar("pcat", cat_opts[:3])}
<div class="card" style="overflow-x:auto"><table><thead><tr>{th("사이트", "Site")}{th("브랜드", "Brand")}{th("모델", "Model")}{th("카테고리", "Category")}
{th("정가", "List price", True)}{th("판매가", "Sale price", True)}{th("PIX", "PIX", True)}{th("할인율", "Discount", True)}{th("할부", "Installments")}</tr></thead>
<tbody>{prod_rows}</tbody></table></div>
</section>

<section data-panel="calendar" class="hidden">
<p class="sub" style="margin-top:-4px">{_t("매일 수집한 기록에서 같은 프로모션을 이어 붙여 블록으로 만듭니다. 블록 기간은 처음 확인한 날부터 마지막으로 확인한 날까지입니다.",
       "Daily captures of the same promotion are chained into one block. A block runs from the first day it was seen to the last day it was seen.")}</p>
<div class="cal-bar">
<span class="cal-year"><button id="cal-prev" onclick="setYear(-1)" aria-label="Previous year">‹</button>
<span id="cal-year"></span><button id="cal-next" onclick="setYear(1)" aria-label="Next year">›</button></span>
<div class="seg" role="group"><button data-view-btn="week" onclick="setView('week')">{_t("주차별", "By week")}</button>
<button data-view-btn="day" onclick="setView('day')">{_t("일자별", "By day")}</button></div>
<div class="filters">{cal_cats}</div>
<span class="tag" id="cal-count"></span>
</div>
<div class="cal-scroll"><div class="cal" id="cal"></div></div>
<div class="legend"><span><i style="background:var(--series-1-soft);border-left:3px solid var(--series-1)"></i>{_t("진행 중", "Ongoing")}</span>
<span><i style="background:var(--ended-soft);border-left:3px solid var(--muted-bar)"></i>{_t("종료", "Ended")}</span>
<span>★ {_t("메인 배너급", "Hero banner")}</span>
<span>{_t("파란 세로줄 = 기준일 · 블록을 누르면 상세 정보", "Blue column = report date · click a block for details")}</span></div>
<div class="card cal-detail hidden" id="cal-detail"></div>
</section>

<p class="tag" style="margin-top:24px">{_t("자동 수집 데이터입니다. 스크린샷을 AI가 판독한 결과이므로 중요한 의사결정 전에는 원본 페이지를 확인하세요.",
       "Automatically collected data read from screenshots by AI. Check the source page before making important decisions.")}</p>
</div></div><div class="tip" id="tip"></div>
<script type="application/json" id="cal-data">{cal_data}</script>
<script>{JS}</script></body></html>"""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(doc, encoding="utf-8")
