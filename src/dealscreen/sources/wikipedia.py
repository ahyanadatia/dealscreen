"""Wikipedia lookup for background context (no API key required)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, quote_plus

from dealscreen.http import HttpClient

SEARCH = "https://en.wikipedia.org/w/rest.php/v1/search/page?q={q}&limit=3"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"


@dataclass
class WikiSummary:
    title: str
    url: str
    extract: str


def lookup(http: HttpClient, query: str) -> WikiSummary | None:
    data: Any = http.get(SEARCH.format(q=quote_plus(query))).json()
    pages = data.get("pages") or []
    if not pages:
        return None
    key = pages[0].get("key") or pages[0].get("title", "")
    summary: Any = http.get(SUMMARY.format(title=quote(str(key), safe=""))).json()
    url = ((summary.get("content_urls") or {}).get("desktop") or {}).get("page", "")
    return WikiSummary(
        title=summary.get("title", key), url=url, extract=summary.get("extract", "")[:3000]
    )
