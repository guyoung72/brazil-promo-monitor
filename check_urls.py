"""설정된 모든 URL이 정상적으로 열리는지 점검(AI 호출 없음, 비용 0).
결과 스크린샷: output/url_check/  → 눈으로 보고 잘못된 페이지면 config.yaml 수정.
"""
from pathlib import Path

import yaml

from monitor.capture import Browser

ROOT = Path(__file__).resolve().parent
cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
cfg["capture"]["delay_between_pages_s"] = 2
br = Browser(cfg)
try:
    for s in cfg["sites"]:
        for k, url in s["pages"].items():
            c = br.capture(url, ROOT / "output" / "url_check" / f"{s['id']}_{k}.jpg")
            redirected = "" if c.final_url.rstrip("/") == url.rstrip("/") else f" → {c.final_url}"
            print(f"[{c.status:7}] {s['id']:15} {k:13} {url}{redirected} {c.error}"
                  f" (텍스트 {len(c.text)}자)", flush=True)
finally:
    br.close()
