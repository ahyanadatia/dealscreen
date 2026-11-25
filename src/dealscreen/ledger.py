"""Source ledger: every retrieved document gets a stable id the agent must cite."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from dealscreen.models import Source


class SourceLedger:
    def __init__(self) -> None:
        self._sources: list[Source] = []
        self._by_url: dict[str, Source] = {}
        self._lock = threading.Lock()

    def add(self, kind: str, title: str, url: str, excerpt: str = "") -> Source:
        if not url.startswith(("http://", "https://")):
            url = ""  # never render non-web URLs (e.g. javascript:) as links
        with self._lock:
            key = f"{kind}|{url}"
            if key in self._by_url:
                return self._by_url[key]
            src = Source(
                id=f"S{len(self._sources) + 1}",
                kind=kind,
                title=title[:200] or url,
                url=url,
                retrieved_at=datetime.now(UTC),
                excerpt=excerpt[:500],
            )
            self._sources.append(src)
            self._by_url[key] = src
            return src

    def ids(self) -> set[str]:
        return {s.id for s in self._sources}

    def all(self) -> list[Source]:
        return list(self._sources)

    def get(self, source_id: str) -> Source | None:
        return next((s for s in self._sources if s.id == source_id), None)
