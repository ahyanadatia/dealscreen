"""Client-side tools the agent can call. Each returns text for the model and registers sources."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

from dealscreen.http import FetchError, HttpClient
from dealscreen.ledger import SourceLedger
from dealscreen.registry import fetch_record
from dealscreen.sources import news, web, wikipedia
from dealscreen.sources.companies_house import (
    SITE_BASE,
    CompaniesHouseSource,
    normalise_company_number,
)

MAX_TOOL_RESULT_CHARS = 7000


class ToolError(Exception):
    """A recoverable tool failure, reported back to the model as an error result."""


@dataclass
class ToolContext:
    http: HttpClient
    ch: CompaniesHouseSource
    ledger: SourceLedger
    fetched_pages: dict[str, web.Page] = field(default_factory=dict)
    max_page_fetches: int = 8


def _untrusted(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, default=str, ensure_ascii=False)
    if len(body) > MAX_TOOL_RESULT_CHARS:
        body = body[:MAX_TOOL_RESULT_CHARS] + '..."(truncated)"'
    return f"<retrieved_data>\n{body}\n</retrieved_data>"


def search_companies_house(ctx: ToolContext, query: str) -> str:
    hits = ctx.ch.search(query, limit=6)
    src = ctx.ledger.add(
        "companies_house",
        f"Companies House search: {query}",
        f"{SITE_BASE}/search/companies?q={quote_plus(query)}",
    )
    return _untrusted({"source_id": src.id, "results": [h.model_dump(mode="json") for h in hits]})


def get_company_record(ctx: ToolContext, company_number: str) -> str:
    number = normalise_company_number(company_number)
    if number is None:
        raise ToolError(f"'{company_number}' is not a valid UK company number (8 characters)")
    rec = fetch_record(ctx.ch, ctx.ledger, number)
    assert rec.company is not None
    active = [o for o in rec.officers if o.resigned_on is None]
    return _untrusted(
        {
            "source_ids": rec.source_ids,
            "profile": rec.company.model_dump(mode="json"),
            "active_officers": [o.model_dump(mode="json") for o in active[:15]],
            "resigned_officer_count": len(rec.officers) - len(active),
            "recent_filings": [f.model_dump(mode="json") for f in rec.filings[:15]],
        }
    )


def fetch_webpage(ctx: ToolContext, url: str) -> str:
    if len(ctx.fetched_pages) >= ctx.max_page_fetches:
        raise ToolError("page fetch budget used up; work with the evidence you already have")
    page = web.fetch_page(ctx.http, url)
    ctx.fetched_pages[page.url] = page
    src = ctx.ledger.add("website", page.title or page.url, page.url, page.description or page.text)
    return _untrusted(
        {
            "source_id": src.id,
            "url": page.url,
            "title": page.title,
            "meta_description": page.description,
            "text": page.text,
            "same_site_links": page.links,
        }
    )


def search_news(ctx: ToolContext, query: str) -> str:
    items = news.search_news(ctx.http, query, limit=8)
    out = []
    for item in items:
        src = ctx.ledger.add("news", f"{item.title} ({item.publisher})", item.url)
        out.append(
            {
                "source_id": src.id,
                "headline": item.title,
                "publisher": item.publisher,
                "date": item.published,
            }
        )
    return _untrusted(
        {"articles": out, "note": "Headlines only; treat as leads, not verified facts."}
    )


def lookup_wikipedia(ctx: ToolContext, query: str) -> str:
    result = wikipedia.lookup(ctx.http, query)
    if result is None:
        return _untrusted({"result": None})
    src = ctx.ledger.add("wikipedia", f"Wikipedia: {result.title}", result.url, result.extract)
    return _untrusted({"source_id": src.id, "title": result.title, "summary": result.extract})


ToolFn = Callable[..., str]

TOOL_FUNCTIONS: dict[str, ToolFn] = {
    "search_companies_house": search_companies_house,
    "get_company_record": get_company_record,
    "fetch_webpage": fetch_webpage,
    "search_news": search_news,
    "lookup_wikipedia": lookup_wikipedia,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_companies_house",
        "description": "Search the UK Companies House register by company name. Use to confirm the legal entity, or to find competitors and related group entities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Company name to search for"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_company_record",
        "description": "Fetch a company's registry profile, active officers and recent filings by UK company number. Returns source ids to cite.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_number": {
                    "type": "string",
                    "description": "8-character company number, e.g. 09263424",
                }
            },
            "required": ["company_number"],
        },
    },
    {
        "name": "fetch_webpage",
        "description": "Fetch a public web page (company site, product, pricing, customers or about page) and return its readable text plus same-site links. Limited budget per screening.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute http(s) URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "search_news",
        "description": "Search recent news headlines (Google News, UK edition). Good for funding rounds, partnerships, leadership changes and controversies.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "lookup_wikipedia",
        "description": "Look up a company, market or technology on Wikipedia for background context.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Execute a tool. Returns (content, is_error). Never raises for expected failures."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return f"Unknown tool '{name}'.", True
    try:
        return fn(ctx, **args), False
    except TypeError as exc:
        return f"Invalid arguments for {name}: {exc}", True
    except (ToolError, FetchError, ValueError) as exc:
        return f"{name} failed: {exc}", True
