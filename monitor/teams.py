"""Microsoft Teams 알림 (Teams Workflows '웹훅 요청 수신 시 채널에 게시' 템플릿용 Adaptive Card)."""
from __future__ import annotations

import os

import requests


def _card(d: dict, summary_md: str, dashboard_url: str) -> dict:
    name = lambda sid: d["sites"].get(sid, {}).get("name", sid)
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder",
         "text": f"🇧🇷 경쟁사 프로모션 데일리 · {d['run_date']}"},
        {"type": "ColumnSet", "columns": [
            {"type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock", "text": str(v), "size": "ExtraLarge", "weight": "Bolder"},
                {"type": "TextBlock", "text": l, "isSubtle": True, "spacing": "None", "wrap": True}]}
            for v, l in [(len(d["new_campaigns"]), "신규 프로모션"),
                         (len(d["ended_campaigns"]), "종료"),
                         (len(d["price_drops"]), f"가격 하락 ≥{d['drop_thr']}%")]]},
        {"type": "TextBlock", "text": "**오늘의 요약**", "spacing": "Medium"},
        {"type": "TextBlock", "text": summary_md, "wrap": True},
    ]
    if d["new_campaigns"]:
        lines = [f"- **{name(c['site_id'])}** · {c['title_ko']}"
                 + (f" ({c['discount_text']})" if c["discount_text"] else "")
                 for c in sorted(d["new_campaigns"], key=lambda c: not c["is_hero"])[:10]]
        if len(d["new_campaigns"]) > 10:
            lines.append(f"- 외 {len(d['new_campaigns']) - 10}건 (대시보드 참고)")
        body += [{"type": "TextBlock", "text": "**신규 프로모션**", "spacing": "Medium"},
                 {"type": "TextBlock", "text": "\n".join(lines), "wrap": True}]
    if d["failures"]:
        body.append({"type": "TextBlock", "isSubtle": True, "wrap": True, "spacing": "Medium",
                     "text": "⚠ 수집 실패: " + ", ".join(f"{name(p['site_id'])}/{p['page_key']}"
                                                       for p in d["failures"])})
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4", "msteams": {"width": "Full"},
                "body": body,
                "actions": [{"type": "Action.OpenUrl", "title": "대시보드 열기", "url": dashboard_url}],
            }}],
    }


def post(d: dict, summary_md: str, dashboard_url: str) -> bool:
    url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        print("[teams] TEAMS_WEBHOOK_URL 미설정 → 알림 생략")
        return False
    r = requests.post(url, json=_card(d, summary_md, dashboard_url), timeout=30)
    ok = r.status_code < 300
    print(f"[teams] HTTP {r.status_code}" + ("" if ok else f" {r.text[:300]}"))
    return ok
