"""SQLite 저장소: 일별 스냅샷을 누적한다."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS pages (
  run_date TEXT, site_id TEXT, page_key TEXT, url TEXT, status TEXT, error TEXT,
  screenshot TEXT, page_note TEXT,
  PRIMARY KEY (run_date, site_id, page_key));
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT, site_id TEXT, page_key TEXT,
  title_pt TEXT, title_ko TEXT, mechanics TEXT, discount_text TEXT, categories TEXT,
  brands TEXT, conditions_ko TEXT, valid_until TEXT, is_hero INTEGER,
  status TEXT, previous_id INTEGER, first_seen TEXT);
CREATE TABLE IF NOT EXISTS ended_campaigns (
  run_date TEXT, campaign_id INTEGER);
CREATE TABLE IF NOT EXISTS products (
  run_date TEXT, site_id TEXT, page_key TEXT, brand TEXT, model TEXT, category TEXT,
  list_price REAL, sale_price REAL, pix_price REAL, discount_pct REAL,
  installments TEXT, seller TEXT);
CREATE INDEX IF NOT EXISTS ix_c ON campaigns(run_date, site_id, page_key);
CREATE INDEX IF NOT EXISTS ix_p ON products(run_date, site_id);
"""

# 기존 DB에 나중에 추가된 컬럼(영문 대시보드용). 없으면 ALTER TABLE로 추가한다.
MIGRATIONS = [("pages", "page_note_en", "TEXT"),
              ("campaigns", "title_en", "TEXT"),
              ("campaigns", "conditions_en", "TEXT")]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DDL)
        for table, col, typ in MIGRATIONS:
            cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.commit()

    def previous_date(self, run_date: str) -> str | None:
        r = self.db.execute("SELECT MAX(run_date) d FROM pages WHERE run_date < ? AND status='ok'",
                            (run_date,)).fetchone()
        return r["d"]

    def campaigns_on(self, run_date: str | None, site_id: str | None = None,
                     page_key: str | None = None) -> list[dict]:
        if not run_date:
            return []
        q, a = "SELECT * FROM campaigns WHERE run_date=?", [run_date]
        if site_id:
            q, a = q + " AND site_id=?", a + [site_id]
        if page_key:
            q, a = q + " AND page_key=?", a + [page_key]
        rows = [dict(r) for r in self.db.execute(q, a)]
        for r in rows:
            for k in ("mechanics", "categories", "brands"):
                r[k] = json.loads(r[k] or "[]")
        return rows

    def products_on(self, run_date: str | None) -> list[dict]:
        if not run_date:
            return []
        return [dict(r) for r in self.db.execute("SELECT * FROM products WHERE run_date=?", (run_date,))]

    def pages_on(self, run_date: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM pages WHERE run_date=?", (run_date,))]

    def ended_on(self, run_date: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT c.* FROM ended_campaigns e JOIN campaigns c ON c.id=e.campaign_id WHERE e.run_date=?",
            (run_date,))]

    def clear_page(self, run_date: str, site_id: str, page_key: str) -> None:
        """같은 날 재실행 시 중복 방지."""
        self.db.execute("DELETE FROM campaigns WHERE run_date=? AND site_id=? AND page_key=?",
                        (run_date, site_id, page_key))
        self.db.execute("DELETE FROM products WHERE run_date=? AND site_id=? AND page_key=?",
                        (run_date, site_id, page_key))
        self.db.execute("DELETE FROM pages WHERE run_date=? AND site_id=? AND page_key=?",
                        (run_date, site_id, page_key))
        self.db.execute("DELETE FROM ended_campaigns WHERE run_date=? AND campaign_id IN "
                        "(SELECT id FROM campaigns WHERE run_date<? AND site_id=? AND page_key=?)",
                        (run_date, run_date, site_id, page_key))

    def last_ok_campaigns(self, run_date: str, site_id: str, page_key: str) -> list[dict]:
        """이 페이지를 마지막으로 정상 수집한 날의 캠페인(어제 실패했으면 그 전날 기준)."""
        r = self.db.execute("SELECT MAX(run_date) d FROM pages WHERE run_date<? AND site_id=? "
                            "AND page_key=? AND status='ok'", (run_date, site_id, page_key)).fetchone()
        return self.campaigns_on(r["d"], site_id, page_key)

    def save_page(self, run_date, site_id, page_key, url, status, error, screenshot,
                  data: dict | None, previous: list[dict]) -> None:
        self.clear_page(run_date, site_id, page_key)
        note = (data or {}).get("page_note", "")
        note_en = (data or {}).get("page_note_en", "")
        self.db.execute("INSERT INTO pages (run_date, site_id, page_key, url, status, error, screenshot,"
                        " page_note, page_note_en) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_date, site_id, page_key, url, status, error, screenshot, note, note_en))
        if data:
            prev_first = {p["id"]: p["first_seen"] for p in previous}
            for c in data["campaigns"]:
                pid = c["previous_id"] if c["previous_id"] in prev_first else None
                status_c = "continuing" if pid else "new"
                self.db.execute(
                    "INSERT INTO campaigns (run_date, site_id, page_key, title_pt, title_ko, mechanics,"
                    " discount_text, categories, brands, conditions_ko, valid_until, is_hero, status,"
                    " previous_id, first_seen, title_en, conditions_en)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_date, site_id, page_key, c["title_pt"], c["title_ko"],
                     json.dumps(c["mechanics"]), c["discount_text"], json.dumps(c["categories"]),
                     json.dumps(c["brands"], ensure_ascii=False), c["conditions_ko"], c["valid_until"],
                     int(c["is_hero"]), status_c, pid, prev_first.get(pid, run_date),
                     c.get("title_en"), c.get("conditions_en")))
            for cid in data["ended_campaign_ids"]:
                if cid in prev_first:
                    self.db.execute("INSERT INTO ended_campaigns VALUES (?,?)", (run_date, cid))
            for p in data["products"]:
                self.db.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (run_date, site_id, page_key, p["brand"], p["model"], p["category"],
                                 p["list_price"], p["sale_price"], p["pix_price"], p["discount_pct"],
                                 p["installments"], p["seller"]))
        self.db.commit()

    def campaigns_until(self, run_date: str) -> list[dict]:
        """캘린더용: 기준일까지의 모든 캠페인 기록(날짜순)."""
        rows = [dict(r) for r in self.db.execute(
            "SELECT * FROM campaigns WHERE run_date<=? ORDER BY run_date, id", (run_date,))]
        for r in rows:
            for k in ("mechanics", "categories", "brands"):
                r[k] = json.loads(r[k] or "[]")
        return rows

    def ended_until(self, run_date: str) -> dict[int, str]:
        """campaign_id → 종료가 확인된 날짜(그날 화면에서 사라짐)."""
        return {r["campaign_id"]: r["run_date"] for r in self.db.execute(
            "SELECT campaign_id, MIN(run_date) run_date FROM ended_campaigns WHERE run_date<=? "
            "GROUP BY campaign_id", (run_date,))}

    def last_ok_dates(self, run_date: str) -> dict[tuple[str, str], str]:
        """(site_id, page_key) → 기준일까지 마지막으로 정상 수집한 날."""
        return {(r["site_id"], r["page_key"]): r["d"] for r in self.db.execute(
            "SELECT site_id, page_key, MAX(run_date) d FROM pages WHERE run_date<=? AND status='ok' "
            "GROUP BY site_id, page_key", (run_date,))}
