from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from dealscreen.http import HttpClient, RateLimiter
from dealscreen.sources.companies_house import (
    CompaniesHouseAPI,
    CompaniesHousePublicSite,
    normalise_company_number,
)
from tests.conftest import fixture_text


def client(handler: Any) -> HttpClient:
    return HttpClient(
        user_agent="t",
        transport=httpx.MockTransport(handler),
        rate_limiter=RateLimiter(0),
        check_ssrf=False,
    )


def site_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    name = "ch_overview.html"
    if path.startswith("/search"):
        name = "ch_search.html"
    elif path.endswith("/officers"):
        name = "ch_officers.html"
    elif path.endswith("/filing-history"):
        name = "ch_filing_history.html"
    return httpx.Response(200, text=fixture_text(name), headers={"content-type": "text/html"})


def test_normalise_company_number() -> None:
    assert normalise_company_number("9263424") == "09263424"
    assert normalise_company_number(" sc123456 ") == "SC123456"
    assert normalise_company_number("Octopus Energy") is None
    assert normalise_company_number("123456789") is None


def test_public_site_parsers_on_real_pages() -> None:
    """Fixtures are real Companies House pages for Octopus Energy Limited (public record)."""
    src = CompaniesHousePublicSite(client(site_handler))
    hits = src.search("octopus energy")
    assert hits[0].company_number == "09263424"
    assert hits[0].name == "OCTOPUS ENERGY LIMITED"
    assert hits[0].incorporated_on == date(2014, 10, 14)

    p = src.profile("09263424")
    assert p.status == "active"
    assert p.sic_codes == ["35140", "35230"]
    assert p.incorporated_on == date(2014, 10, 14)
    assert p.accounts_next_due == date(2027, 1, 31)
    assert p.accounts_last_made_up_to == date(2025, 4, 30)
    assert not p.accounts_overdue and not p.has_insolvency_history
    assert p.has_charges
    assert "MERCURY ENERGY SUPPLY LIMITED" in p.previous_names

    officers = src.officers("09263424")
    assert len(officers) == 15
    assert sum(o.resigned_on is not None for o in officers) == 9
    assert all(o.name and o.role for o in officers)

    filings = src.filings("09263424")
    assert len(filings) == 25
    assert filings[0].date == date(2026, 5, 21)
    assert filings[0].description.startswith("Resolution")


def test_api_backend_maps_json_and_sends_basic_auth() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization", "")
        if req.url.path == "/company/01234567":
            return httpx.Response(
                200,
                json={
                    "company_number": "01234567",
                    "company_name": "EXAMPLE LTD",
                    "company_status": "liquidation",
                    "date_of_creation": "2010-01-02",
                    "sic_codes": ["62012"],
                    "registered_office_address": {"address_line_1": "1 Road", "locality": "Leeds"},
                    "accounts": {
                        "next_due": "2025-01-01",
                        "overdue": True,
                        "last_accounts": {"made_up_to": "2023-03-31"},
                    },
                    "confirmation_statement": {"overdue": True},
                    "has_insolvency_history": True,
                },
            )
        if req.url.path.endswith("/filing-history"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "date": "2024-05-01",
                            "category": "insolvency",
                            "description": "liquidation-voluntary-statement-of-affairs",
                        }
                    ]
                },
            )
        if req.url.path.endswith("/officers"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "name": "DOE, Jane",
                            "officer_role": "director",
                            "appointed_on": "2010-01-02",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "company_number": "01234567",
                        "title": "EXAMPLE LTD",
                        "company_status": "active",
                    }
                ]
            },
        )

    api = CompaniesHouseAPI(client(handler), "key123")
    p = api.profile("01234567")
    assert seen["auth"].startswith("Basic ")
    assert p.status == "liquidation" and p.accounts_overdue and p.confirmation_statement_overdue
    assert p.registered_address == "1 Road, Leeds"
    assert api.filings("01234567")[0].description == "Liquidation voluntary statement of affairs"
    assert api.officers("01234567")[0].role == "director"
    assert api.search("example")[0].name == "EXAMPLE LTD"
