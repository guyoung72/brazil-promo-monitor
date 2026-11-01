"""일일 변화 계산(digest) + 정적 HTML 대시보드 생성."""
from __future__ import annotations

import html
import json
import os
import re
from collections import defaultdict
from pathlib import Path

CAT_KO = {"tv": "TV", "refrigerator": "냉장고", "washer": "세탁기", "other": "사이트 전체"}
PAGE_KO = {"home": "메인/오퍼", "tv": "TV", "refrigerator": "냉장고", "washer": "세탁기"}
TYPE_KO = {"self": "자사", "ecommerce": "이커머스", "d2c": "제조사 D2C"}
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
    }


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
 --muted-bar:#c9c8c2;--good:#008300;--critical:#d03b3a;--warning:#b87900;--chip:#ecebe7}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
 --surface-0:#121211;--surface-1:#1a1a19;--border:#2f2f2d;--text-primary:#f0efec;--text-secondary:#c3c2b7;
 --text-muted:#8f8e86;--series-1:#3987e5;--muted-bar:#4a4a46;--good:#3fa23f;--critical:#e66767;
 --warning:#d9a12c;--chip:#2a2a28}}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--surface-0);color:var(--text-primary);font:14px/1.5 -apple-system,"Segoe UI",
 "Malgun Gothic","Apple SD Gothic Neo",sans-serif;min-height:100vh;padding:24px}
.wrap{max-width:1280px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}h3{font-size:15px;margin:0 0 8px}
.sub{color:var(--text-secondary);margin:0 0 20px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi .v{font-size:28px;font-weight:650}.kpi .l{color:var(--text-secondary);font-size:13px}
.summary ul{margin:0;padding-left:20px}.summary li{margin:4px 0}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}
.filters button{border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);
 padding:6px 12px;border-radius:999px;cursor:pointer;font:inherit}
.filters button.on{background:var(--text-primary);color:var(--surface-1)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:12px}
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
.bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.bar-row{display:grid;grid-template-columns:110px 1fr 52px;align-items:center;gap:8px;margin:5px 0;font-size:13px;position:relative}
.bar-track{height:14px}.bar{height:14px;border-radius:0 4px 4px 0;background:var(--muted-bar)}
.bar.lg{background:var(--series-1)}.bar-row .lab.lg{font-weight:700}
.bar-row:hover .bar{outline:2px solid var(--text-primary);outline-offset:1px}
.tip{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);padding:4px 8px;border-radius:4px;font-size:12px;display:none;z-index:9}
.hidden{display:none!important}
"""

JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('mousemove',e=>{tip.textContent=el.dataset.tip;tip.style.display='block';
  tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px'});
 el.addEventListener('mouseleave',()=>tip.style.display='none')});
function filt(group,val,btn){
 document.querySelectorAll('[data-fgroup="'+group+'"] button').forEach(b=>b.classList.toggle('on',b===btn));
 document.querySelectorAll('[data-'+group+']').forEach(el=>{
  const v=el.getAttribute('data-'+group).split(' ');el.classList.toggle('hidden',val!=='all'&&!v.includes(val))});
}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _brl(v) -> str:
    if v is None:
        return "–"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _filter_bar(group: str, opts: list[tuple[str, str]]) -> str:
    btns = [f'<button class="on" onclick="filt(\'{group}\',\'all\',this)">전체</button>']
    btns += [f'<button onclick="filt(\'{group}\',\'{v}\',this)">{_e(l)}</button>' for v, l in opts]
    return f'<div class="filters" data-fgroup="{group}">{"".join(btns)}</div>'


def render_html(d: dict, summary_md: str, out_file: Path, shots_root: Path) -> None:
    sites = d["sites"]
    name = lambda sid: sites.get(sid, {}).get("name", sid)
    rel = lambda p: os.path.relpath(p, out_file.parent) if p else ""
    n_ok = sum(1 for p in d["pages"] if p["status"] == "ok")

    kpis = [
        (len(d["new_campaigns"]), "신규 프로모션"),
        (len(d["ended_campaigns"]), "종료된 프로모션"),
        (len(d["price_drops"]), f'가격 하락 ≥{d["drop_thr"]}% 품목'),
        (f'{n_ok}/{len(d["pages"])}', "정상 수집 페이지"),
    ]
    kpi_html = "".join(f'<div class="card kpi"><div class="v">{_e(v)}</div><div class="l">{_e(l)}</div></div>'
                       for v, l in kpis)
    bullets = "".join(f"<li>{_e(l[2:] if l.startswith('- ') else l)}</li>"
                      for l in summary_md.splitlines() if l.strip())

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
                     ('<span class="badge-hero">메인배너</span>' if c["is_hero"] else "")
            chips = "".join(f'<span class="chip">{_e(MECH_KO.get(m, m))}</span>' for m in c["mechanics"])
            chips += "".join(f'<span class="chip">{_e(CAT_KO.get(x, x))}</span>' for x in c["categories"])
            chips += "".join(f'<span class="chip">{_e(b)}</span>' for b in c["brands"])
            extra = " · ".join(x for x in [c["discount_text"], c["conditions_ko"],
                                           f'~{c["valid_until"]}' if c["valid_until"] else None,
                                           f'최초 확인 {c["first_seen"]}'] if x)
            body.append(f'<div class="camp" data-cat="{" ".join(c["categories"])}" data-new="{c["status"]}">'
                        f'<div class="t">{badges}{_e(c["title_ko"])}</div>'
                        f'<div class="pt">{_e(c["title_pt"])}</div><div class="tag">{_e(extra)}</div>{chips}</div>')
        shots = []
        for p in sorted(pages_by_site.get(sid, []), key=lambda p: list(PAGE_KO).index(p["page_key"])
                        if p["page_key"] in PAGE_KO else 9):
            label = PAGE_KO.get(p["page_key"], p["page_key"])
            if p["status"] != "ok":
                shots.append(f'<div class="err">⚠ {_e(label)}: 수집 실패 ({_e(p["status"])}) {_e(p["error"])}</div>')
            if p["page_note"]:
                shots.append(f'<div class="note"><b>{_e(label)}</b> — {_e(p["page_note"])}</div>')
            if p["screenshot"]:
                shots.append(f'<details><summary>{_e(label)} 스크린샷 · <a href="{_e(p["url"])}" '
                             f'target="_blank" rel="noopener">원본 페이지</a></summary>'
                             f'<img loading="lazy" src="{_e(rel(p["screenshot"]))}" alt="{_e(name(sid))} {_e(label)}"></details>')
        if not pages_by_site.get(sid):
            body = ['<div class="tag">오늘 수집 대상 아님(또는 미실행)</div>']
        elif not body:
            body = ['<div class="tag">TV/냉장고/세탁기 관련 프로모션 없음</div>']
        cards.append(f'<div class="card" data-stype="{s["type"]}"><div class="site-h"><h3>{_e(s["name"])}</h3>'
                     f'<span class="tag">{_e(TYPE_KO.get(s["type"], s["type"]))}</span></div>'
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
            f'<div class="bar-row" data-tip="{_e(r["brand"])} · 평균 {r["avg"]}% · 상품 {r["n"]}개">'
            f'<span class="lab {"lg" if r["brand"] == "LG" else ""}">{_e(r["brand"])}</span>'
            f'<div class="bar-track"><div class="bar {"lg" if r["brand"] == "LG" else ""}" '
            f'style="width:{max(r["avg"] / mx * 100, 1):.1f}%"></div></div>'
            f'<span class="num">{r["avg"]}%</span></div>' for r in rows)
        bar_blocks.append(f'<div class="card"><h3>{CAT_KO[cat]} — 브랜드별 평균 표시 할인율</h3>{rr}'
                          f'<div class="tag">전 사이트 수집 상품 기준 · 상품 2개 이상 브랜드만 · LG 강조</div></div>')

    # 사이트 × 카테고리 평균 할인율 표
    st_rows = "".join(
        f'<tr><td>{_e(name(sid))}</td>' + "".join(
            f'<td class="num">{(str(v["avg"]) + "%") if (v := d["site_stats"].get(sid, {}).get(c)) else "–"}'
            f'{(" <span class=tag>(" + str(v["n"]) + ")</span>") if v else ""}</td>'
            for c in ["tv", "refrigerator", "washer"]) + "</tr>"
        for sid in sites)

    # 가격 하락 표
    drop_rows = "".join(
        f'<tr data-cat="{p["category"]}"><td>{_e(name(p["site_id"]))}</td><td>{_e(p["brand"])}</td>'
        f'<td>{_e(p["model"])}</td><td>{_e(CAT_KO.get(p["category"]))}</td>'
        f'<td class="num">{_brl(p["old_price"])}</td><td class="num">{_brl(p["sale_price"])}</td>'
        f'<td class="num down">{p["change_pct"]}%</td></tr>' for p in d["price_drops"])

    # 전체 상품 표
    prod_rows = "".join(
        f'<tr data-pcat="{p["category"]}"><td>{_e(name(p["site_id"]))}</td><td>{_e(p["brand"])}</td>'
        f'<td>{_e(p["model"])}</td><td>{_e(CAT_KO.get(p["category"]))}</td>'
        f'<td class="num">{_brl(p["list_price"])}</td><td class="num">{_brl(p["sale_price"])}</td>'
        f'<td class="num">{_brl(p["pix_price"])}</td>'
        f'<td class="num">{"" if p["discount_pct"] is None else str(p["discount_pct"]) + "%"}</td>'
        f'<td>{_e(p["installments"])}</td></tr>'
        for p in sorted(d["products"], key=lambda p: (p["category"], p["site_id"], _brand(p["brand"]) != "LG")))

    ended_rows = "".join(f'<li>{_e(name(c["site_id"]))} — {_e(c["title_ko"])} '
                         f'<span class="tag">({_e(c["first_seen"])} ~ {_e(c["run_date"])})</span></li>'
                         for c in d["ended_campaigns"]) or "<li>없음</li>"

    cat_opts = [("tv", "TV"), ("refrigerator", "냉장고"), ("washer", "세탁기"), ("other", "사이트 전체")]
    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>브라질 이커머스 & D2C 프로모션 대시보드 · {d["run_date"]}</title><style>{CSS}</style></head>
<body><div class="viz-root"><div class="wrap">
<h1>브라질 이커머스 & D2C 프로모션 대시보드</h1>
<p class="sub">기준일 {d["run_date"]} (브라질 시간) · 비교 기준 {d["prev_date"] or "없음(첫 수집)"} · 중남미 D2C팀</p>
<div class="kpis">{kpi_html}</div>
<h2>오늘의 요약</h2><div class="card summary"><ul>{bullets}</ul></div>

<h2>사이트별 프로모션</h2>
{_filter_bar("cat", cat_opts)}
<div class="filters" data-fgroup="stype"><button class="on" onclick="filt('stype','all',this)">전체 사이트</button>
<button onclick="filt('stype','ecommerce',this)">이커머스</button><button onclick="filt('stype','d2c',this)">제조사 D2C</button>
<button onclick="filt('stype','self',this)">LG(자사)</button></div>
<div class="grid">{"".join(cards)}</div>

<h2>전일 대비 가격 하락</h2>
<div class="card"><table><thead><tr><th>사이트</th><th>브랜드</th><th>모델</th><th>카테고리</th>
<th class="num">전일</th><th class="num">오늘</th><th class="num">변동</th></tr></thead>
<tbody>{drop_rows or '<tr><td colspan="7">해당 없음</td></tr>'}</tbody></table></div>

<h2>할인 강도 비교</h2>
<div class="bars">{"".join(bar_blocks) or '<div class="card">데이터 없음</div>'}</div>
<div class="card" style="margin-top:12px"><h3>사이트별 평균 표시 할인율 (괄호: 상품 수)</h3>
<table><thead><tr><th>사이트</th><th class="num">TV</th><th class="num">냉장고</th><th class="num">세탁기</th></tr></thead>
<tbody>{st_rows}</tbody></table></div>

<h2>종료된 프로모션</h2><div class="card"><ul>{ended_rows}</ul></div>

<h2>수집 상품 전체</h2>
{_filter_bar("pcat", cat_opts[:3])}
<div class="card" style="overflow-x:auto"><table><thead><tr><th>사이트</th><th>브랜드</th><th>모델</th><th>카테고리</th>
<th class="num">정가</th><th class="num">판매가</th><th class="num">PIX</th><th class="num">할인율</th><th>할부</th></tr></thead>
<tbody>{prod_rows}</tbody></table></div>
<p class="tag" style="margin-top:24px">자동 수집 데이터입니다. 스크린샷을 AI가 판독한 결과이므로 중요한 의사결정 전에는 원본 페이지를 확인하세요.</p>
</div></div><div class="tip" id="tip"></div><script>{JS}</script></body></html>"""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(doc, encoding="utf-8")
