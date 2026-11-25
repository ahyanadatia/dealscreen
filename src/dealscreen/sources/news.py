"""Recent news via the public Google News RSS feed (no API key required)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from dealscreen.http import HttpClient

FEED = "https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"


@dataclass
class NewsItem:
    title: str
    url: str
    published: str
    publisher: str


def parse_feed(xml_text: str, limit: int = 8) -> list[NewsItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"could not parse news feed: {exc}") from exc
    items: list[NewsItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        source = item.find("source")
        publisher = (source.text or "").strip() if source is not None else ""
        try:
            published = parsedate_to_datetime(pub).date().isoformat() if pub else ""
        except (TypeError, ValueError):
            published = pub
        if title and link:
            items.append(NewsItem(title=title, url=link, published=published, publisher=publisher))
        if len(items) >= limit:
            break
    return items


def search_news(http: HttpClient, query: str, limit: int = 8) -> list[NewsItem]:
    resp = http.get(FEED.format(q=quote_plus(query)))
    return parse_feed(resp.text, limit)
