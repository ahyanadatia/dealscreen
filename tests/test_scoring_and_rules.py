from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from dealscreen.models import (
    AgentAssessment,
    CompanyProfile,
    CriterionRating,
    Filing,
    Officer,
    Recommendation,
    Severity,
)
from dealscreen.redflags import registry_red_flags
from dealscreen.scoring import score
from dealscreen.thesis import Thesis, load_thesis

TODAY = date(2026, 11, 25)


def company(**kw: object) -> CompanyProfile:
    base: dict[str, object] = {
        "company_number": "01234567",
        "name": "EXAMPLE LTD",
        "status": "active",
        "incorporated_on": date(2018, 1, 1),
        "sic_codes": ["62012"],
        "url": "https://example.com",
    }
    base.update(kw)
    return CompanyProfile.model_validate(base)


def ratings(thesis: Thesis, value: float) -> AgentAssessment:
    return AgentAssessment(
        one_liner="x",
        business_model="y",
        ratings=[
            CriterionRating(criterion_id=c.id, score=value, rationale="r")
            for c in thesis.agent_criteria
        ],
    )


def test_all_example_theses_load() -> None:
    for path in Path("theses").glob("*.yaml"):
        t = load_thesis(path)
        assert t.agent_criteria and t.prompt_block()


def test_thesis_validation_errors(tmp_path: Path) -> None:
    bad = tmp_path / "t.yaml"
    bad.write_text(
        "name: x\ndescription: y\ncriteria:\n  - {id: a, label: A, weight: 1, method: rule}\n"
    )
    with pytest.raises(ValidationError, match="defines no rule"):
        load_thesis(bad)
    bad.write_text(
        "name: x\ndescription: y\ncriteria:\n  - {id: a, label: A, weight: 1, method: agent, guidance: g}\n  - {id: a, label: B, weight: 1, method: agent, guidance: g}\n"
    )
    with pytest.raises(ValidationError, match="duplicate"):
        load_thesis(bad)


def test_strong_company_advances(thesis: Thesis) -> None:
    s = score(thesis, company(), ratings(thesis, 5), today=TODAY)
    assert s.total == 100.0  # SIC 62012 is on the target list; every criterion maxed
    assert s.coverage == 1.0
    assert s.recommendation is Recommendation.ADVANCE


def test_weighted_average_is_correct(thesis: Thesis) -> None:
    s = score(thesis, company(sic_codes=["35140"]), ratings(thesis, 2.5), today=TODAY)
    agent_w = sum(c.weight for c in thesis.agent_criteria)
    rule_w = sum(c.weight for c in thesis.criteria if c.method == "rule")
    expected = (agent_w * 2.5 + rule_w * 5) / (agent_w + rule_w) / 5 * 100
    assert s.total == pytest.approx(expected, abs=0.05)


def test_failed_must_have_forces_pass(thesis: Thesis) -> None:
    a = ratings(thesis, 5)
    a.ratings[0] = CriterionRating(criterion_id="sector_fit", score=1, rationale="not climate")
    s = score(thesis, company(), a, today=TODAY)
    assert s.recommendation is Recommendation.PASS
    assert s.failed_must_haves == ["sector_fit"]


def test_dissolved_company_fails_must_have(thesis: Thesis) -> None:
    s = score(thesis, company(status="dissolved"), ratings(thesis, 5), today=TODAY)
    assert "registry_active" in s.failed_must_haves
    assert s.recommendation is Recommendation.PASS


def test_low_coverage_goes_to_review(thesis: Thesis) -> None:
    s = score(thesis, company(), None, today=TODAY)
    assert s.coverage < thesis.min_coverage
    assert s.recommendation is Recommendation.REVIEW
    assert all(c.method in {"rule", "missing"} for c in s.criteria)


def test_age_rule_bands(thesis: Thesis) -> None:
    young = score(thesis, company(incorporated_on=date(2025, 6, 1)), None, today=TODAY)
    old = score(thesis, company(incorporated_on=date(1990, 1, 1)), None, today=TODAY)
    get = lambda s: next(c for c in s.criteria if c.criterion_id == "maturity").score  # noqa: E731
    assert get(young) == 2.0 and get(old) == 0.0


def test_red_flags_for_distressed_company() -> None:
    c = company(
        status="liquidation",
        has_insolvency_history=True,
        accounts_overdue=True,
        confirmation_statement_overdue=True,
    )
    officers = [
        Officer(name=f"P{i}", role="director", resigned_on=date(2026, i, 1)) for i in range(1, 5)
    ]
    filings = [
        Filing(
            date=date(2026, 3, 1),
            category="gazette",
            description="First Gazette notice for compulsory strike-off",
        )
    ]
    flags = registry_red_flags(c, officers, filings, website_found=False, today=TODAY)
    titles = {f.title for f in flags}
    assert {
        "Company not active",
        "Insolvency history",
        "Accounts overdue",
        "Confirmation statement overdue",
        "Officer churn",
        "No active director listed",
        "Strike-off activity",
        "No website found",
    } <= titles
    assert all(f.origin == "rule" for f in flags)
    assert sum(f.severity is Severity.HIGH for f in flags) >= 4


def test_clean_company_has_no_serious_flags() -> None:
    officers = [Officer(name="A", role="director", appointed_on=date(2018, 1, 1))]
    flags = registry_red_flags(company(), officers, [], website_found=None, today=TODAY)
    assert flags == []


def test_missing_company_flag() -> None:
    flags = registry_red_flags(None, [], [], website_found=None, today=TODAY)
    assert flags[0].title == "No UK registry match"


def test_low_confidence_name_match_is_not_accepted() -> None:
    from dealscreen.ledger import SourceLedger
    from dealscreen.models import CompanySearchHit
    from dealscreen.registry import resolve

    class FakeCH:
        backend = "fake"

        def search(self, query: str, limit: int = 5) -> list[CompanySearchHit]:
            return [CompanySearchHit(company_number="15665033", name="OHMEGA CONTRACTORS LTD")]

        def profile(self, number: str) -> CompanyProfile:  # pragma: no cover - must not be called
            raise AssertionError("should not fetch a low-confidence match")

    rec = resolve(FakeCH(), SourceLedger(), "Ohme Technologies")  # type: ignore[arg-type]
    assert rec.company is None and rec.candidates
    assert "No confident" in rec.warnings[0]
