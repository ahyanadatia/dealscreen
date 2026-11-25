"""Deterministic thesis scoring.

The LLM never produces the final number. It rates individual criteria with evidence; this module
combines those ratings with rule checks into a weighted score, a coverage figure and a
recommendation, so the result is reproducible and auditable.
"""

from __future__ import annotations

from datetime import date

from dealscreen.models import (
    AgentAssessment,
    CompanyProfile,
    CriterionResult,
    Recommendation,
    ThesisScore,
)
from dealscreen.thesis import Criterion, Rule, Thesis


def _years_between(start: date, end: date) -> float:
    return (end - start).days / 365.25


def evaluate_rule(
    rule: Rule, company: CompanyProfile | None, today: date
) -> tuple[float | None, str]:
    if company is None:
        return None, "No registry record matched, so this rule could not be checked."
    if rule.type == "company_active":
        ok = company.status.lower() == "active"
        return (5.0 if ok else 0.0), f"Companies House status: {company.status}."
    if rule.type == "no_insolvency":
        if company.has_insolvency_history:
            return 0.0, "Companies House records insolvency history."
        return 5.0, "No insolvency history on the register."
    if rule.type == "filings_current":
        problems = []
        if company.accounts_overdue:
            problems.append("accounts overdue")
        if company.confirmation_statement_overdue:
            problems.append("confirmation statement overdue")
        if problems:
            return 0.0, "Filing problems: " + ", ".join(problems) + "."
        return 5.0, "Accounts and confirmation statement are not overdue."
    if rule.type == "age_years":
        if company.incorporated_on is None:
            return None, "Incorporation date unknown."
        age = _years_between(company.incorporated_on, today)
        lo = rule.min if rule.min is not None else 0.0
        hi = rule.max if rule.max is not None else float("inf")
        if lo <= age <= hi:
            score = 5.0
        elif lo - 2 <= age <= hi + 2:
            score = 2.0
        else:
            score = 0.0
        band = f"{lo:g}-{hi:g}" if hi != float("inf") else f"{lo:g}+"
        return (
            score,
            f"Incorporated {company.incorporated_on.isoformat()} ({age:.1f} years; target {band}).",
        )
    if rule.type == "sic_prefix":
        if not company.sic_codes:
            return None, "No SIC codes on the register."
        matched = [s for s in company.sic_codes if any(s.startswith(p) for p in rule.prefixes)]
        if matched:
            return 5.0, f"SIC codes {', '.join(matched)} match the target list."
        return 1.0, f"SIC codes {', '.join(company.sic_codes)} are outside the target list."
    raise ValueError(f"unknown rule type {rule.type}")  # pragma: no cover


def _criterion_result(
    c: Criterion,
    company: CompanyProfile | None,
    assessment: AgentAssessment | None,
    today: date,
    registry_source_id: str | None,
) -> CriterionResult:
    if c.method == "rule":
        assert c.rule is not None
        score, why = evaluate_rule(c.rule, company, today)
        return CriterionResult(
            criterion_id=c.id,
            label=c.label,
            weight=c.weight,
            score=score,
            rationale=why,
            method="rule" if score is not None else "missing",
            source_ids=[registry_source_id] if company is not None and registry_source_id else [],
        )
    rating = None
    if assessment is not None:
        rating = next((r for r in assessment.ratings if r.criterion_id == c.id), None)
    if rating is None:
        return CriterionResult(
            criterion_id=c.id,
            label=c.label,
            weight=c.weight,
            score=None,
            rationale="Not assessed (requires the LLM agent).",
            method="missing",
        )
    return CriterionResult(
        criterion_id=c.id,
        label=c.label,
        weight=c.weight,
        score=rating.score,
        rationale=rating.rationale,
        method="agent",
        source_ids=rating.source_ids,
    )


def score(
    thesis: Thesis,
    company: CompanyProfile | None,
    assessment: AgentAssessment | None,
    today: date | None = None,
    registry_source_id: str | None = "S1",
) -> ThesisScore:
    today = today or date.today()
    results = [
        _criterion_result(c, company, assessment, today, registry_source_id)
        for c in thesis.criteria
    ]
    total_weight = sum(r.weight for r in results)
    assessed = [r for r in results if r.score is not None]
    assessed_weight = sum(r.weight for r in assessed)
    total = (
        sum(r.weight * (r.score or 0.0) for r in assessed) / assessed_weight / 5 * 100
        if assessed_weight
        else 0.0
    )
    coverage = assessed_weight / total_weight if total_weight else 0.0

    failed = []
    for c, r in zip(thesis.criteria, results, strict=True):
        if c.must_have and r.score is not None and r.score < c.must_have_min_score:
            failed.append(c.id)

    if failed:
        rec = Recommendation.PASS
    elif coverage < thesis.min_coverage:
        rec = Recommendation.REVIEW
    elif total >= thesis.thresholds.advance:
        rec = Recommendation.ADVANCE
    elif total >= thesis.thresholds.review:
        rec = Recommendation.REVIEW
    else:
        rec = Recommendation.PASS
    return ThesisScore(
        total=round(total, 1),
        coverage=round(coverage, 2),
        recommendation=rec,
        failed_must_haves=failed,
        criteria=results,
    )
