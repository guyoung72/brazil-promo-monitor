"""매일 1회 실행: 수집 → AI 판독 → 저장 → 대시보드 생성 → Teams 알림.

사용법:
  python run_daily.py                 # 전체 실행
  python run_daily.py --sites magalu,samsung   # 일부 사이트만
  python run_daily.py --report-only   # 수집 없이 대시보드/알림만 다시 생성
  python run_daily.py --no-teams      # Teams 알림 생략
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from monitor.store import Store
from monitor import report, teams

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--sites", help="쉼표로 구분한 site id")
    ap.add_argument("--date", help="기준일 YYYY-MM-DD (기본: 오늘, 브라질 시간)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--no-teams", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    run_date = a.date or datetime.now(ZoneInfo(cfg.get("timezone", "America/Sao_Paulo"))).date().isoformat()
    store = Store(ROOT / "data" / "monitor.db")
    targets = [s for s in cfg["sites"] if not a.sites or s["id"] in a.sites.split(",")]

    if not a.report_only:
        from monitor.capture import Browser
        from monitor.extract import extract_page
        br = Browser(cfg)
        try:
            for site in targets:
                for page_key, url in site["pages"].items():
                    tag = f"{site['id']}/{page_key}"
                    shot = OUT / "screens" / run_date / f"{site['id']}_{page_key}.jpg"
                    cap = br.capture(url, shot)
                    previous = store.last_ok_campaigns(run_date, site["id"], page_key)
                    data, status, err = None, cap.status, cap.error
                    if cap.ok:
                        try:
                            data = extract_page(cfg, site["name"], page_key, url, cap.tiles, cap.text, previous)
                        except Exception as e:
                            status, err = "ai_error", f"{type(e).__name__}: {e}"[:300]
                    store.save_page(run_date, site["id"], page_key, url, status, err,
                                    cap.screenshot_path, data, previous)
                    n = f"캠페인 {len(data['campaigns'])} / 상품 {len(data['products'])}" if data else err
                    print(f"[{status:8}] {tag:28} {n}", flush=True)
        finally:
            br.close()

    d = report.build_digest(store, cfg, run_date)
    if not d["pages"]:
        print("해당 날짜 수집 데이터가 없습니다.")
        return 1
    try:
        from monitor.extract import summarize_day
        summary = summarize_day(cfg, report.digest_for_llm(d))
    except Exception as e:
        summary = {"ko": f"- (AI 요약 생성 실패: {type(e).__name__})",
                   "en": f"- (AI summary failed: {type(e).__name__})"}

    archive = OUT / "archive" / f"{run_date}.html"
    report.render_html(d, summary, archive, OUT / "screens")
    report.render_html(d, summary, OUT / "index.html", OUT / "screens")
    print(f"대시보드: {OUT / 'index.html'}  (보관본: {archive})")

    if not a.no_teams:
        teams.post(d, summary["ko"], cfg["teams"]["dashboard_url"])

    # 오래된 스크린샷 정리(90일 초과)
    for p in (OUT / "screens").glob("*"):
        try:
            if (datetime.fromisoformat(run_date) - datetime.fromisoformat(p.name)).days > 90:
                shutil.rmtree(p)
        except ValueError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
