"""SQLite persistence for screening results and the human-review workflow."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from dealscreen.memo import render_memo
from dealscreen.models import ReviewStatus, ScreeningResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS screenings (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    query TEXT NOT NULL,
    company_name TEXT,
    company_number TEXT,
    recommendation TEXT NOT NULL,
    score REAL NOT NULL,
    review_status TEXT NOT NULL,
    mode TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screenings_created ON screenings (created_at DESC);
"""


class ResultStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def save(self, r: ScreeningResult) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO screenings VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r.id,
                    r.created_at.isoformat(),
                    r.query,
                    r.company.name if r.company else None,
                    r.company.company_number if r.company else None,
                    r.score.recommendation.value,
                    r.score.total,
                    r.review_status.value,
                    r.mode,
                    r.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get(self, result_id: str) -> ScreeningResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM screenings WHERE id = ?", (result_id,)
            ).fetchone()
        return ScreeningResult.model_validate_json(row[0]) if row else None

    def list(self, limit: int = 200) -> list[ScreeningResult]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM screenings ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [ScreeningResult.model_validate_json(r[0]) for r in rows]

    def review(
        self, result_id: str, status: ReviewStatus, notes: str = "", reviewer: str | None = None
    ) -> ScreeningResult:
        r = self.get(result_id)
        if r is None:
            raise KeyError(result_id)
        r.review_status = status
        r.review_notes = notes.strip()
        r.reviewer = (reviewer or "").strip() or None
        r.memo_markdown = render_memo(r)
        self.save(r)
        return r
