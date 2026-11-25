"""UK Companies House data source.

Two interchangeable backends return the same typed models:

* `CompaniesHouseAPI` uses the official REST API (free key from
  https://developer.company-information.service.gov.uk/). Preferred.
* `CompaniesHousePublicSite` reads the public "Find and update company information" pages when
  no key is configured, so the tool still works out of the box. It is rate limited to one
  request per second and parses only stable element ids.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Protocol
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from dealscreen.http import HttpClient
from dealscreen.models import CompanyProfile, CompanySearchHit, Filing, Officer

API_BASE = "https://api.company-information.service.gov.uk"
SITE_BASE = "https://find-and-update.company-information.service.gov.uk"
COMPANY_NUMBER_RE = re.compile(r"^(?:[A-Z]{2}\d{6}|\d{8})$")


def normalise_company_number(raw: str) -> str | None:
    value = raw.strip().upper().replace(" ", "")
    if value.isdigit() and len(value) < 8:
        value = value.zfill(8)
    return value if COMPANY_NUMBER_RE.match(value) else None


def company_url(number: str) -> str:
    return f"{SITE_BASE}/company/{number}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = " ".join(value.split())
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _humanise(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


class CompaniesHouseSource(Protocol):
    backend: str

    def search(self, query: str, limit: int = 5) -> list[CompanySearchHit]: ...
    def profile(self, number: str) -> CompanyProfile: ...
    def officers(self, number: str) -> list[Officer]: ...
    def filings(self, number: str, limit: int = 25) -> list[Filing]: ...


class CompaniesHouseAPI:
    backend = "api"

    def __init__(self, http: HttpClient, api_key: str):
        self.http = http
        self._auth = (api_key, "")

    def _get(self, path: str) -> dict[str, Any]:
        data = self.http.get(f"{API_BASE}{path}", auth=self._auth).json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected Companies House response for {path}")
        return data

    def search(self, query: str, limit: int = 5) -> list[CompanySearchHit]:
        data = self._get(f"/search/companies?q={quote_plus(query)}&items_per_page={limit}")
        return [
            CompanySearchHit(
                company_number=item["company_number"],
                name=item.get("title", ""),
                status=item.get("company_status"),
                incorporated_on=_parse_date(item.get("date_of_creation")),
                address=item.get("address_snippet"),
            )
            for item in data.get("items", [])[:limit]
        ]

    def profile(self, number: str) -> CompanyProfile:
        d = self._get(f"/company/{number}")
        addr = d.get("registered_office_address") or {}
        address = ", ".join(
            str(addr[k])
            for k in ("address_line_1", "address_line_2", "locality", "postal_code", "country")
            if addr.get(k)
        )
        accounts = d.get("accounts") or {}
        return CompanyProfile(
            company_number=d.get("company_number", number),
            name=d.get("company_name", ""),
            status=d.get("company_status", "unknown"),
            company_type=d.get("type"),
            incorporated_on=_parse_date(d.get("date_of_creation")),
            registered_address=address or None,
            sic_codes=list(d.get("sic_codes") or []),
            previous_names=[p.get("name", "") for p in d.get("previous_company_names") or []],
            accounts_next_due=_parse_date(accounts.get("next_due")),
            accounts_last_made_up_to=_parse_date(
                (accounts.get("last_accounts") or {}).get("made_up_to")
            ),
            accounts_overdue=bool(accounts.get("overdue", False)),
            confirmation_statement_overdue=bool(
                (d.get("confirmation_statement") or {}).get("overdue", False)
            ),
            has_insolvency_history=bool(d.get("has_insolvency_history", False)),
            has_charges=bool(d.get("has_charges", False)),
            url=company_url(number),
        )

    def officers(self, number: str) -> list[Officer]:
        d = self._get(f"/company/{number}/officers?items_per_page=50")
        return [
            Officer(
                name=o.get("name", ""),
                role=o.get("officer_role", ""),
                appointed_on=_parse_date(o.get("appointed_on")),
                resigned_on=_parse_date(o.get("resigned_on")),
                nationality=o.get("nationality"),
                country_of_residence=o.get("country_of_residence"),
            )
            for o in d.get("items", [])
        ]

    def filings(self, number: str, limit: int = 25) -> list[Filing]:
        d = self._get(f"/company/{number}/filing-history?items_per_page={limit}")
        return [
            Filing(
                date=_parse_date(f.get("date")),
                category=f.get("category", ""),
                description=_humanise(f.get("description", "")),
            )
            for f in d.get("items", [])[:limit]
        ]


def _text(node: Node | None) -> str:
    return " ".join(node.text(separator=" ").split()) if node is not None else ""


class CompaniesHousePublicSite:
    backend = "public-site"

    def __init__(self, http: HttpClient):
        self.http = http

    def _html(self, path: str) -> HTMLParser:
        return HTMLParser(self.http.get(f"{SITE_BASE}{path}").text)

    def search(self, query: str, limit: int = 5) -> list[CompanySearchHit]:
        tree = self._html(f"/search/companies?q={quote_plus(query)}")
        hits: list[CompanySearchHit] = []
        for li in tree.css("li.type-company"):
            link = li.css_first("h3 a")
            if link is None:
                continue
            href = link.attributes.get("href") or ""
            number = href.rsplit("/", 1)[-1]
            meta = ""
            for p in li.css("p.meta.crumbtrail"):
                if "inset" not in (p.attributes.get("class") or ""):
                    meta = _text(p)
            m = re.search(r"Incorporated on (\d{1,2} \w+ \d{4})", meta)
            dissolved = "Dissolved" in meta
            paras = [p for p in li.css("p") if "meta" not in (p.attributes.get("class") or "")]
            hits.append(
                CompanySearchHit(
                    company_number=number,
                    name=_text(link),
                    status="dissolved" if dissolved else None,
                    incorporated_on=_parse_date(m.group(1)) if m else None,
                    address=_text(paras[0]) if paras else None,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def profile(self, number: str) -> CompanyProfile:
        raw = self.http.get(f"{SITE_BASE}/company/{number}").text
        tree = HTMLParser(raw)
        name_node = tree.css_first("p.heading-xlarge") or tree.css_first("h1")
        sic = []
        for node in tree.css('span[id^="sic"]'):
            code = _text(node).split(" ", 1)[0]
            if code.isdigit():
                sic.append(code)
        flat = " ".join(HTMLParser(raw).text(separator=" ").split())
        next_due = re.search(r"Next accounts made up to [\w ]+? due by (\d{1,2} \w+ \d{4})", flat)
        last = re.search(r"Last accounts made up to (\d{1,2} \w+ \d{4})", flat)
        accounts_section = flat.split("Accounts", 1)[-1].split("Confirmation statement", 1)[0]
        conf_section = flat.split("Confirmation statement", 1)[-1][:400]
        prev_names = [_text(n) for n in tree.css('[id^="previous-name-"]')]
        return CompanyProfile(
            company_number=number,
            name=_text(name_node),
            status=_text(tree.css_first("#company-status")).lower() or "unknown",
            company_type=_text(tree.css_first("#company-type")) or None,
            incorporated_on=_parse_date(_text(tree.css_first("#company-creation-date"))),
            registered_address=_text(tree.css_first(".text.data")) or None,
            sic_codes=sic,
            previous_names=[n for n in prev_names if n],
            accounts_next_due=_parse_date(next_due.group(1)) if next_due else None,
            accounts_last_made_up_to=_parse_date(last.group(1)) if last else None,
            accounts_overdue="overdue" in accounts_section.lower(),
            confirmation_statement_overdue="overdue" in conf_section.lower(),
            has_insolvency_history=tree.css_first("#insolvency-tab") is not None,
            has_charges=tree.css_first("#charges-tab") is not None,
            url=company_url(number),
        )

    def officers(self, number: str) -> list[Officer]:
        tree = self._html(f"/company/{number}/officers")
        out: list[Officer] = []
        i = 1
        while True:
            name = tree.css_first(f"#officer-name-{i}")
            if name is None:
                break
            out.append(
                Officer(
                    name=_text(name),
                    role=_text(tree.css_first(f"#officer-role-{i}")),
                    appointed_on=_parse_date(_text(tree.css_first(f"#officer-appointed-on-{i}"))),
                    resigned_on=_parse_date(_text(tree.css_first(f"#officer-resigned-on-{i}"))),
                    nationality=_text(tree.css_first(f"#officer-nationality-{i}")) or None,
                    country_of_residence=_text(tree.css_first(f"#officer-country-of-residence-{i}"))
                    or None,
                )
            )
            i += 1
        return out

    def filings(self, number: str, limit: int = 25) -> list[Filing]:
        tree = self._html(f"/company/{number}/filing-history")
        out: list[Filing] = []
        for row in tree.css("#fhTable tr"):
            cells = row.css("td")
            if len(cells) < 3:
                continue
            desc_node = cells[2].css_first("strong") or cells[2]
            out.append(
                Filing(
                    date=_parse_date(_text(cells[0])),
                    category=_text(cells[1]).lower(),
                    description=_text(desc_node),
                )
            )
            if len(out) >= limit:
                break
        return out


def make_source(http: HttpClient, api_key: str | None) -> CompaniesHouseSource:
    if api_key:
        return CompaniesHouseAPI(http, api_key)
    return CompaniesHousePublicSite(http)
