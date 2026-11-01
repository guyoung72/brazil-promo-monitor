"""Playwright로 페이지를 열어 스크린샷과 화면 텍스트를 수집한다."""
from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright, TimeoutError as PWTimeout

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
COOKIE_BUTTONS = ["Aceitar", "Aceito", "Aceitar todos", "Aceitar cookies", "Concordo",
                  "Entendi", "OK", "Continuar", "Fechar"]
BLOCK_MARKERS = ["captcha", "não sou um robô", "nao sou um robo", "access denied",
                 "acesso negado", "unusual traffic", "robot check", "pardon our interruption",
                 "para continuar, acesse sua conta"]  # Mercado Livre 로그인 벽(account-verification)


@dataclass
class Capture:
    url: str
    ok: bool
    status: str                      # ok | blocked | error
    error: str = ""
    final_url: str = ""
    screenshot_path: str = ""        # 전체 스크린샷(대시보드용)
    tiles: list[bytes] = field(default_factory=list)  # AI 판독용 JPEG 조각
    text: str = ""


def _dismiss_popups(page: Page) -> None:
    for label in COOKIE_BUTTONS:
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(500)
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def _lazy_scroll(page: Page, max_h: int) -> None:
    """지연 로딩 이미지/상품이 뜨도록 천천히 스크롤."""
    y = 0
    while y < max_h:
        y += 700
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(350)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(800)


def _tiles(png: bytes, tile_h: int, max_tiles: int) -> list[bytes]:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    out = []
    for i in range(max_tiles):
        top = i * tile_h
        if top >= img.height:
            break
        piece = img.crop((0, top, img.width, min(img.height, top + tile_h)))
        buf = io.BytesIO()
        piece.save(buf, "JPEG", quality=72)
        out.append(buf.getvalue())
    return out


class Browser:
    def __init__(self, cfg: dict):
        self.cfg = cfg["capture"]
        self._pw = sync_playwright().start()
        exe = os.environ.get("CHROMIUM_PATH")  # 사내 PC에 이미 설치된 Chrome을 쓰고 싶을 때
        self._browser = self._pw.chromium.launch(headless=True, executable_path=exe or None)
        self._ctx = self._browser.new_context(
            user_agent=UA,
            locale="pt-BR",
            timezone_id=cfg.get("timezone", "America/Sao_Paulo"),
            viewport=self.cfg["viewport"],
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9"},
        )

    def close(self) -> None:
        self._ctx.close()
        self._browser.close()
        self._pw.stop()

    def capture(self, url: str, shot_path: Path) -> Capture:
        page = self._ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.cfg["page_timeout_ms"])
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass  # 광고/트래커 때문에 networkidle이 안 오는 사이트가 많음
            _dismiss_popups(page)
            max_h = self.cfg["max_page_height"]
            _lazy_scroll(page, max_h)
            _dismiss_popups(page)

            text = page.evaluate("document.body ? document.body.innerText : ''") or ""
            low = text[:5000].lower()
            code = resp.status if resp else 0
            blocked = (code in (403, 429) or any(m in low for m in BLOCK_MARKERS)
                       or "account-verification" in page.url)

            height = min(page.evaluate("document.documentElement.scrollHeight"), max_h)
            png = page.screenshot(full_page=True, clip={
                "x": 0, "y": 0, "width": self.cfg["viewport"]["width"], "height": height})
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            Image.open(io.BytesIO(png)).convert("RGB").save(shot_path, "JPEG", quality=70)

            status = "blocked" if blocked else ("error" if code >= 400 else "ok")
            return Capture(
                url=url, ok=status == "ok", status=status,
                error="" if status == "ok" else f"HTTP {code}" + (" / 봇 차단 화면 감지" if blocked else ""),
                final_url=page.url, screenshot_path=str(shot_path),
                tiles=_tiles(png, self.cfg["tile_height"], self.cfg["max_tiles"]),
                text=text[:15000],
            )
        except Exception as e:  # 타임아웃, DNS, 네트워크 등
            return Capture(url=url, ok=False, status="error", error=f"{type(e).__name__}: {e}"[:300])
        finally:
            page.close()
            time.sleep(self.cfg.get("delay_between_pages_s", 5))
