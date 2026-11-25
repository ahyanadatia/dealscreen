"""Deterministic red-flag rules over Companies House data.

These run with or without the LLM and cannot be talked out of a finding by persuasive text on a
company website, which is why they are kept separate from agent-raised flags.
"""

from __future__ import annotations

from datetime import date, timedelta

from dealscreen.models import CompanyProfile, Filing, Officer, RedFlag, Severity

STRIKE_OFF_TERMS = ("strike-off", "strike off", "striking off")
DISTRESS_TERMS = ("liquidat", "administrat", "winding up", "receiver")


def registry_red_flags(
    company: CompanyProfile | None,
    officers: list[Officer],
    filings: list[Filing],
    website_found: bool | None,
    today: date | None = None,
    source_id: str = "S1",
    officers_source_id: str = "S2",
    filings_source_id: str = "S3",
) -> list[RedFlag]:
    today = today or date.today()
    flags: list[RedFlag] = []

    def add(title: str, detail: str, severity: Severity, sid: str = source_id) -> None:
        flags.append(
            RedFlag(title=title, detail=detail, severity=severity, origin="rule", source_ids=[sid])
        )

    if company is None:
        flags.append(
            RedFlag(
                title="No UK registry match",
                detail="Could not match the company to a Companies House record. Confirm the legal entity before relying on any finding.",
                severity=Severity.MEDIUM,
                origin="rule",
            )
        )
        return flags

    if company.status.lower() != "active":
        add("Company not active", f"Registry status is '{company.status}'.", Severity.HIGH)
    if company.has_insolvency_history:
        add("Insolvency history", "Companies House records insolvency history.", Severity.HIGH)
    if company.accounts_overdue:
        add("Accounts overdue", "Statutory accounts are overdue at Companies House.", Severity.HIGH)
    if company.confirmation_statement_overdue:
        add(
            "Confirmation statement overdue",
            "The annual confirmation statement is overdue.",
            Severity.MEDIUM,
        )

    two_years_ago = today - timedelta(days=730)
    recent_resignations = [
        o for o in officers if o.resigned_on is not None and o.resigned_on >= two_years_ago
    ]
    active = [o for o in officers if o.resigned_on is None]
    if len(recent_resignations) >= 3:
        names = ", ".join(o.name for o in recent_resignations[:5])
        add(
            "Officer churn",
            f"{len(recent_resignations)} officer resignations in the last 24 months ({names}).",
            Severity.MEDIUM,
            officers_source_id,
        )
    if officers and not any("director" in o.role.lower() for o in active):
        add(
            "No active director listed",
            "No active director appears on the register.",
            Severity.MEDIUM,
            officers_source_id,
        )

    for f in filings:
        text = f.description.lower()
        if any(t in text for t in STRIKE_OFF_TERMS) and f.date and f.date >= two_years_ago:
            add(
                "Strike-off activity",
                f"Filing on {f.date.isoformat()}: {f.description}.",
                Severity.HIGH,
                filings_source_id,
            )
            break
    for f in filings:
        text = f.description.lower()
        if any(t in text for t in DISTRESS_TERMS):
            add(
                "Distress-related filing",
                f"Filing: {f.description}.",
                Severity.HIGH,
                filings_source_id,
            )
            break
    if any("dormant" in f.description.lower() for f in filings[:10]):
        add(
            "Dormant accounts filed",
            "Recent filings include dormant-company accounts; the trading entity may be elsewhere in the group.",
            Severity.MEDIUM,
            filings_source_id,
        )
    if company.incorporated_on and company.incorporated_on > today - timedelta(days=365):
        add("Very young entity", "Incorporated less than a year ago.", Severity.LOW)
    if company.previous_names:
        add(
            "Previous names",
            "Previously registered as: " + ", ".join(company.previous_names[:3]) + ".",
            Severity.LOW,
        )
    if company.has_charges:
        add(
            "Charges registered",
            "Security interests (charges) are registered against the company; check lenders and whether they are outstanding.",
            Severity.LOW,
        )
    if website_found is False:
        flags.append(
            RedFlag(
                title="No website found",
                detail="No company website could be retrieved, so product claims are unverified.",
                severity=Severity.LOW,
                origin="rule",
            )
        )
    return flags
