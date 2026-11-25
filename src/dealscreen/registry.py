"""Deterministic entity resolution and registry collection (runs in every mode)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from dealscreen.http import FetchError
from dealscreen.ledger import SourceLedger
from dealscreen.models import CompanyProfile, CompanySearchHit, Filing, Officer
from dealscreen.sources.companies_house import (
    SITE_BASE,
    CompaniesHouseSource,
    normalise_company_number,
)

LEGAL_SUFFIXES = re.compile(r"\b(limited|ltd|plc|llp|group|holdings|uk|the)\b\.?", re.I)


def _clean(name: str) -> str:
    return " ".join(LEGAL_SUFFIXES.sub(" ", name.lower()).replace(".", " ").split())


def rank_candidates(query: str, hits: list[CompanySearchHit]) -> list[CompanySearchHit]:
    """Order search hits by name similarity, preferring live companies over dissolved ones."""
    q = _clean(query)

    def key(hit: CompanySearchHit) -> float:
        sim = SequenceMatcher(None, q, _clean(hit.name)).ratio()
        if hit.status and hit.status.lower() == "dissolved":
            sim -= 0.3
        return sim

    return sorted(hits, key=key, reverse=True)


@dataclass
class RegistryRecord:
    company: CompanyProfile | None = None
    officers: list[Officer] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    candidates: list[CompanySearchHit] = field(default_factory=list)
    source_ids: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def fetch_record(ch: CompaniesHouseSource, ledger: SourceLedger, number: str) -> RegistryRecord:
    rec = RegistryRecord()
    rec.company = ch.profile(number)
    s1 = ledger.add(
        "companies_house",
        f"Companies House: {rec.company.name} ({number})",
        rec.company.url,
        f"Status {rec.company.status}; SIC {', '.join(rec.company.sic_codes)}",
    )
    rec.source_ids["profile"] = s1.id
    try:
        rec.officers = ch.officers(number)
        s2 = ledger.add(
            "companies_house",
            f"Officers of {rec.company.name}",
            f"{SITE_BASE}/company/{number}/officers",
        )
        rec.source_ids["officers"] = s2.id
    except (FetchError, ValueError) as exc:
        rec.warnings.append(f"officers unavailable: {exc}")
    try:
        rec.filings = ch.filings(number)
        s3 = ledger.add(
            "companies_house",
            f"Filing history of {rec.company.name}",
            f"{SITE_BASE}/company/{number}/filing-history",
        )
        rec.source_ids["filings"] = s3.id
    except (FetchError, ValueError) as exc:
        rec.warnings.append(f"filing history unavailable: {exc}")
    return rec


def resolve(
    ch: CompaniesHouseSource,
    ledger: SourceLedger,
    query: str,
    company_number: str | None = None,
) -> RegistryRecord:
    number = normalise_company_number(company_number or "") or normalise_company_number(query)
    candidates: list[CompanySearchHit] = []
    if number is None:
        try:
            candidates = rank_candidates(query, ch.search(query, limit=8))
        except (FetchError, ValueError) as exc:
            return RegistryRecord(warnings=[f"Companies House search failed: {exc}"])
        if not candidates:
            return RegistryRecord(warnings=[f"No Companies House match for '{query}'."])
        number = candidates[0].company_number
    try:
        rec = fetch_record(ch, ledger, number)
    except (FetchError, ValueError) as exc:
        return RegistryRecord(
            candidates=candidates, warnings=[f"Companies House lookup failed: {exc}"]
        )
    rec.candidates = candidates
    return rec
