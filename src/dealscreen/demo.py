"""Offline demo: a full agent-mode screening of a FICTIONAL company with no network or API key.

Everything here is sample data: the company "Gridline Analytics Ltd" (number ZZ000001, a prefix
Companies House does not issue), its website on the reserved `.example` domain, the news feed and
the model's responses are all scripted. The pipeline, agent loop, citation validation, scoring,
red-flag rules, memo rendering and the real `anthropic` SDK client run unchanged, which makes
this a faithful walkthrough of the product without claiming to be a live run.
"""

from __future__ import annotations

from typing import Any

import httpx

from dealscreen.config import Settings
from dealscreen.http import HttpClient, RateLimiter
from dealscreen.memo import render_memo
from dealscreen.models import ScreeningResult
from dealscreen.pipeline import Emit, Screener, ScreenRequest
from dealscreen.sources.companies_house import CompaniesHouseAPI
from dealscreen.testing import ScriptedAnthropic, message, text_block, tool_use_block
from dealscreen.thesis import Thesis

NUMBER = "ZZ000001"
SITE = "https://gridline.example"

PROFILE = {
    "company_number": NUMBER,
    "company_name": "GRIDLINE ANALYTICS LTD",
    "company_status": "active",
    "type": "ltd",
    "date_of_creation": "2019-03-12",
    "registered_office_address": {
        "address_line_1": "1 Example Street",
        "locality": "London",
        "postal_code": "EC1A 1AA",
        "country": "England",
    },
    "sic_codes": ["62012", "71122"],
    "previous_company_names": [{"name": "GRIDLINE LABS LTD"}],
    "accounts": {
        "next_due": "2027-06-30",
        "last_accounts": {"made_up_to": "2025-09-30"},
        "overdue": False,
    },
    "confirmation_statement": {"overdue": False},
    "has_insolvency_history": False,
    "has_charges": True,
}
OFFICERS = {
    "items": [
        {
            "name": "EXAMPLE, Priya",
            "officer_role": "director",
            "appointed_on": "2019-03-12",
            "nationality": "British",
        },
        {
            "name": "SAMPLE, Tom",
            "officer_role": "director",
            "appointed_on": "2019-03-12",
            "nationality": "British",
        },
        {
            "name": "PLACEHOLDER, Ana",
            "officer_role": "director",
            "appointed_on": "2023-02-01",
            "resigned_on": "2025-08-15",
        },
        {
            "name": "DUMMY, Lee",
            "officer_role": "director",
            "appointed_on": "2022-05-10",
            "resigned_on": "2025-11-02",
        },
        {
            "name": "FICTION, Sam",
            "officer_role": "secretary",
            "appointed_on": "2021-01-04",
            "resigned_on": "2026-01-20",
        },
    ]
}
FILINGS = {
    "items": [
        {"date": "2026-04-02", "category": "capital", "description": "capital-allotment-shares"},
        {
            "date": "2026-03-14",
            "category": "confirmation-statement",
            "description": "confirmation-statement-with-updates",
        },
        {
            "date": "2026-01-20",
            "category": "officers",
            "description": "termination-secretary-company-with-name",
        },
        {
            "date": "2025-12-18",
            "category": "accounts",
            "description": "accounts-with-accounts-type-full",
        },
    ]
}
HOME_HTML = """<html><head><title>Gridline Analytics: grid-edge intelligence for distribution networks</title>
<meta name="description" content="Gridline forecasts congestion on low-voltage networks so utilities can connect more heat pumps, EV chargers and solar without new copper."></head>
<body><h1>Connect more low-carbon tech to the grid you already have</h1>
<p>Gridline turns smart-meter and substation data into 15-minute congestion forecasts for distribution network operators.</p>
<p>Sold as an annual SaaS licence per network area.</p><a href="/about">About</a><a href="/customers">Customers</a></body></html>"""
ABOUT_HTML = """<html><head><title>About Gridline Analytics</title></head><body>
<p>Founded in 2019 by power-systems engineers. 48 employees across London and Rotterdam.</p>
<p>Customers include three UK distribution network operators and one Dutch grid operator.</p>
<p>Our models have helped customers defer an estimated 40 GWh of network reinforcement. (Company claim.)</p></body></html>"""
NEWS_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Gridline Analytics raises Series A to expand grid forecasting (sample headline)</title><link>https://news.example/gridline-series-a</link><pubDate>Tue, 03 Mar 2026 09:00:00 GMT</pubDate><source url="https://news.example">Example Energy News</source></item>
<item><title>Network operator trials congestion forecasting from Gridline (sample headline)</title><link>https://news.example/gridline-trial</link><pubDate>Thu, 11 Sep 2025 09:00:00 GMT</pubDate><source url="https://news.example">Example Utility Week</source></item>
</channel></rss>"""
CH_SEARCH = {
    "items": [
        {
            "company_number": "ZZ000002",
            "title": "VOLTSENSE GRID LTD",
            "company_status": "active",
            "date_of_creation": "2017-06-01",
            "address_snippet": "Manchester",
        },
        {
            "company_number": "ZZ000003",
            "title": "FLEXNODE ENERGY SYSTEMS LTD",
            "company_status": "active",
            "date_of_creation": "2020-09-21",
            "address_snippet": "Bristol",
        },
    ]
}


def _http_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    path = request.url.path
    if "api.company-information" in url:
        if path == f"/company/{NUMBER}":
            return httpx.Response(200, json=PROFILE)
        if path.endswith("/officers"):
            return httpx.Response(200, json=OFFICERS)
        if path.endswith("/filing-history"):
            return httpx.Response(200, json=FILINGS)
        if path.startswith("/search/companies"):
            return httpx.Response(200, json=CH_SEARCH)
    if url.startswith(SITE):
        html = ABOUT_HTML if path.startswith("/about") else HOME_HTML
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})
    if "news.google.com" in url:
        return httpx.Response(200, text=NEWS_RSS, headers={"content-type": "application/rss+xml"})
    return httpx.Response(404, text="not found")


def scripted_responses(model: str) -> list[dict[str, Any]]:
    submission = {
        "one_liner": "Gridline sells SaaS that forecasts low-voltage grid congestion so network operators can connect more heat pumps, EV chargers and solar without new reinforcement.",
        "business_model": "Annual SaaS licence per network area, sold to distribution network operators (enterprise B2B, long sales cycles).",
        "facts": [
            {
                "claim": "Produces 15-minute congestion forecasts from smart-meter and substation data.",
                "source_ids": ["S4"],
            },
            {
                "claim": "Founded 2019; about 48 employees in London and Rotterdam (company claim).",
                "source_ids": ["S5", "S1"],
            },
            {
                "claim": "Customers include three UK DNOs and one Dutch grid operator (company claim).",
                "source_ids": ["S5"],
            },
            {"claim": "Reported a Series A round in March 2026.", "source_ids": ["S6"]},
            {"claim": "Has 2,000 paying customers.", "source_ids": ["S99"]},
        ],
        "ratings": [
            {
                "criterion_id": "sector_fit",
                "score": 5,
                "rationale": "Core product directly increases grid hosting capacity for electrification.",
                "source_ids": ["S4"],
            },
            {
                "criterion_id": "technology_moat",
                "score": 3,
                "rationale": "Proprietary forecasting models on operator data; defensibility depends on data access agreements, which are not evidenced.",
                "source_ids": ["S4", "S5"],
            },
            {
                "criterion_id": "commercial_traction",
                "score": 3,
                "rationale": "A handful of named-category utility customers and a Series A; revenue scale not disclosed.",
                "source_ids": ["S5", "S6"],
            },
            {
                "criterion_id": "business_model",
                "score": 4,
                "rationale": "Recurring SaaS licences with enterprise customers; concentration risk is high.",
                "source_ids": ["S4"],
            },
            {
                "criterion_id": "impact_measurability",
                "score": 3,
                "rationale": "Quantifies deferred reinforcement (40 GWh), but this is an unaudited company claim.",
                "source_ids": ["S5"],
            },
        ],
        "competitors": [
            {
                "name": "VoltSense Grid",
                "note": "Older UK grid-analytics vendor found on the register; overlapping LV monitoring.",
                "source_ids": ["S8"],
            },
            {
                "name": "FlexNode Energy Systems",
                "note": "Flexibility-market software; adjacent rather than direct.",
                "source_ids": ["S8"],
            },
            {
                "name": "In-house DNO data teams",
                "note": "Main substitute: operators building forecasting internally.",
                "source_ids": [],
            },
        ],
        "red_flags": [
            {
                "title": "Customer concentration",
                "detail": "Revenue appears to depend on four grid operators; losing one would be material.",
                "severity": "medium",
                "source_ids": ["S5"],
            },
        ],
        "key_questions": [
            "What share of ARR comes from the largest customer, and when do contracts renew?",
            "Who owns the smart-meter data and the models trained on it?",
            "How was the 40 GWh deferred-reinforcement figure measured?",
            "Why did two directors and the company secretary leave in the last 15 months?",
        ],
        "unverified_claims": ["40 GWh of reinforcement deferred (company claim)."],
    }
    return [
        message(
            [
                text_block(
                    "Registry record looks clean. I'll read the about page for customers and team size."
                ),
                tool_use_block("toolu_demo_1", "fetch_webpage", {"url": f"{SITE}/about"}),
            ],
            model=model,
            msg_id="msg_demo_1",
        ),
        message(
            [tool_use_block("toolu_demo_2", "search_news", {"query": '"Gridline Analytics"'})],
            model=model,
            msg_id="msg_demo_2",
        ),
        message(
            [
                text_block("Now a competitor scan on the register."),
                tool_use_block(
                    "toolu_demo_3", "search_companies_house", {"query": "grid analytics"}
                ),
            ],
            model=model,
            msg_id="msg_demo_3",
        ),
        message(
            [tool_use_block("toolu_demo_4", "submit_assessment", submission)],
            model=model,
            msg_id="msg_demo_4",
        ),
    ]


def run_demo(settings: Settings, thesis: Thesis, emit: Emit | None = None) -> ScreeningResult:
    http = HttpClient(
        user_agent="dealscreen-demo",
        transport=httpx.MockTransport(_http_handler),
        rate_limiter=RateLimiter(default_interval=0.0),
        check_ssrf=False,
    )
    scripted = ScriptedAnthropic(scripted_responses(settings.anthropic_model))
    screener = Screener(
        settings,
        thesis,
        http=http,
        ch=CompaniesHouseAPI(http, "demo-key"),
        llm=scripted.client().messages,
    )
    result = screener.screen(
        ScreenRequest("Gridline Analytics", website=SITE, company_number=NUMBER), emit
    )
    result.mode = "sample"
    result.model = f"{settings.anthropic_model} (scripted responses)"
    result.memo_markdown = render_memo(result)
    return result
