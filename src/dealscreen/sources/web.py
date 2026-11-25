"""Company website fetching and readable-text extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from dealscreen.http import HttpClient

MAX_TEXT_CHARS = 6000
INTERESTING_PATHS = ("about", "product", "solution", "platform", "customer", "pricing", "team")


@dataclass
class Page:
    url: str
    title: str
    description: str
    text: str
    links: list[str] = field(default_factory=list)


def normalise_url(raw: str) -> str:
    raw = raw.strip()
    if not urlparse(raw).scheme:
        raw = "https://" + raw
    return raw


def extract_page(url: str, html: str, max_chars: int = MAX_TEXT_CHARS) -> Page:
    tree = LexborHTMLParser(html)
    for sel in ("script", "style", "noscript", "svg", "nav", "footer", "form", "iframe"):
        for node in tree.css(sel):
            node.decompose()
    title_node = tree.css_first("title")
    title = " ".join(title_node.text().split()) if title_node else ""
    desc = ""
    for sel in ('meta[name="description"]', 'meta[property="og:description"]'):
        meta = tree.css_first(sel)
        if meta is not None and meta.attributes.get("content"):
            desc = str(meta.attributes["content"]).strip()
            break
    body = tree.body
    text = " ".join(body.text(separator=" ").split()) if body is not None else ""
    host = urlparse(url).hostname
    links: list[str] = []
    for a in tree.css("a[href]"):
        href = urljoin(url, str(a.attributes.get("href") or ""))
        parsed = urlparse(href)
        if parsed.hostname != host or parsed.scheme not in {"http", "https"}:
            continue
        clean = href.split("#", 1)[0]
        if clean not in links and any(p in parsed.path.lower() for p in INTERESTING_PATHS):
            links.append(clean)
    return Page(url=url, title=title, description=desc, text=text[:max_chars], links=links[:15])


def fetch_page(http: HttpClient, url: str) -> Page:
    resp = http.get(normalise_url(url))
    if "html" not in resp.content_type and "text" not in resp.content_type:
        return Page(url=resp.url, title="", description="", text="(non-HTML content skipped)")
    return extract_page(resp.url, resp.text)
