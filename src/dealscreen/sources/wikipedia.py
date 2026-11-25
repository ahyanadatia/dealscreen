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


def _relevant(query: str, title: str, extract: str) -> bool:
    """Wikipedia search is fuzzy; only accept a page that mentions the query's key terms."""
    terms = [t for t in query.lower().replace('"', " ").split() if len(t) > 3] or [query.lower()]
    haystack = f"{title} {extract}".lower()
    return all(t in haystack for t in terms[:3])


def lookup(http: HttpClient, query: str) -> WikiSummary | None:
    data: Any = http.get(SEARCH.format(q=quote_plus(query))).json()
    pages = data.get("pages") or []
    if not pages:
        return None
    key = pages[0].get("key") or pages[0].get("title", "")
    summary: Any = http.get(SUMMARY.format(title=quote(str(key), safe=""))).json()
    url = ((summary.get("content_urls") or {}).get("desktop") or {}).get("page", "")
    title = str(summary.get("title", key))
    extract = str(summary.get("extract", ""))
    if not _relevant(query, title, extract):
        return None
    return WikiSummary(title=title, url=url, extract=extract[:3000])
