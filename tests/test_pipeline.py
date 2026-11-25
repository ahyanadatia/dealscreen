from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dealscreen.config import Settings
from dealscreen.demo import run_demo
from dealscreen.http import HttpClient, RateLimiter
from dealscreen.models import Recommendation
from dealscreen.pipeline import MissingAPIKeyError, Screener, ScreenRequest, read_pipeline_csv
from dealscreen.sources.companies_house import CompaniesHousePublicSite
from dealscreen.thesis import Thesis
from tests.test_companies_house import site_handler


def offline_http() -> HttpClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if "company-information" in str(req.url):
            return site_handler(req)
        if req.url.host == "down.example":
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(
            200,
            text="<html><title>Octopus</title><body>Energy</body></html>",
            headers={"content-type": "text/html"},
        )

    return HttpClient(
        user_agent="t",
        transport=httpx.MockTransport(handler),
        rate_limiter=RateLimiter(0),
        check_ssrf=False,
        max_retries=0,
    )


def test_requires_key_unless_data_only(settings: Settings, thesis: Thesis) -> None:
    with pytest.raises(MissingAPIKeyError):
        Screener(settings, thesis, http=offline_http())
    placeholder = Settings(ANTHROPIC_API_KEY="sk-ant-placeholder-replace-me")
    assert not placeholder.llm_enabled


def test_data_only_screen_on_real_registry_pages(settings: Settings, thesis: Thesis) -> None:
    http = offline_http()
    s = Screener(settings, thesis, http=http, ch=CompaniesHousePublicSite(http), data_only=True)
    r = s.screen(ScreenRequest("Octopus Energy", website="octopus.energy"))
    assert r.mode == "data-only" and r.assessment is None
    assert r.company and r.company.company_number == "09263424"
    assert [src.id for src in r.sources] == ["S1", "S2", "S3", "S4"]
    assert r.website == "https://octopus.energy"
    assert r.score.recommendation is Recommendation.REVIEW  # agent criteria missing -> low coverage
    assert "Data-only mode" in r.memo_markdown and "[S1]" in r.memo_markdown


def test_website_failure_is_reported_not_fatal(settings: Settings, thesis: Thesis) -> None:
    http = offline_http()
    s = Screener(settings, thesis, http=http, ch=CompaniesHousePublicSite(http), data_only=True)
    r = s.screen(ScreenRequest("Octopus Energy", website="https://down.example"))
    assert any("Website fetch failed" in w for w in r.warnings)
    assert any(f.title == "No website found" for f in r.red_flags)


def test_batch_isolates_failures(settings: Settings, thesis: Thesis) -> None:
    http = offline_http()
    s = Screener(settings, thesis, http=http, ch=CompaniesHousePublicSite(http), data_only=True)
    original = s.screen

    def flaky(req: ScreenRequest, emit=None):  # type: ignore[no-untyped-def]
        if req.query == "boom":
            raise RuntimeError("upstream exploded")
        return original(req, emit)

    s.screen = flaky  # type: ignore[method-assign]
    out = s.batch([ScreenRequest("Octopus Energy"), ScreenRequest("boom")])
    assert not isinstance(out[0], Exception)
    assert isinstance(out[1], RuntimeError)


def test_read_pipeline_csv(tmp_path: Path) -> None:
    p = tmp_path / "p.csv"
    p.write_text(
        "Company,Website,company_number\nOctopus Energy,octopus.energy,09263424\n,,\nAcme,,\n",
        encoding="utf-8",
    )
    rows = read_pipeline_csv(p)
    assert [(r.query, r.website, r.company_number) for r in rows] == [
        ("Octopus Energy", "octopus.energy", "09263424"),
        ("Acme", None, None),
    ]
    p.write_text("foo\nbar\n")
    with pytest.raises(ValueError, match="company"):
        read_pipeline_csv(p)


def test_offline_demo_runs_full_agent_pipeline(settings: Settings, thesis: Thesis) -> None:
    r = run_demo(settings, thesis)
    assert r.mode == "sample" and r.assessment is not None
    assert "SAMPLE OUTPUT, NOT A LIVE RUN" in r.memo_markdown
    assert r.score.recommendation is Recommendation.ADVANCE
    assert any(f.title == "Officer churn" for f in r.red_flags)  # rule flag from fixture officers
    assert "Has 2,000 paying customers." in r.assessment.unverified_claims  # bad citation demoted
