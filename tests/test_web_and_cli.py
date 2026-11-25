from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dealscreen.cli import app as cli_app
from dealscreen.config import Settings
from dealscreen.pipeline import Screener
from dealscreen.sources.companies_house import CompaniesHousePublicSite
from dealscreen.store import ResultStore
from dealscreen.thesis import Thesis
from dealscreen.web.app import create_app
from dealscreen.web.ratelimit import SlidingWindowLimiter
from tests.test_pipeline import offline_http


@pytest.fixture
def client(settings: Settings) -> TestClient:
    def factory(thesis: Thesis, data_only: bool) -> Screener:
        http = offline_http()
        return Screener(
            settings, thesis, http=http, ch=CompaniesHousePublicSite(http), data_only=True
        )

    app = create_app(settings=settings, thesis_dir=Path("theses"), screener_factory=factory)
    return TestClient(app)


def wait_done(client: TestClient, job_id: str) -> dict[str, str]:
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"done", "error"}:
            return job  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_screen_view_review_download(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    res = client.post(
        "/api/screenings", data={"company": "Octopus Energy", "thesis": "energy-transition-growth"}
    )
    job = wait_done(client, res.json()["job_id"])
    assert job["status"] == "done", job
    rid = job["result_id"]

    events = client.get(f"/api/jobs/{res.json()['job_id']}/events").text
    assert "Matched OCTOPUS ENERGY LIMITED" in events and "event: end" in events

    page = client.get(f"/screenings/{rid}")
    assert page.status_code == 200 and "OCTOPUS ENERGY LIMITED" in page.text
    assert "OCTOPUS ENERGY LIMITED" in client.get("/").text

    r = client.post(
        f"/screenings/{rid}/review",
        data={"decision": "approved", "notes": "Strong team", "reviewer": "AA"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    data = client.get(f"/api/screenings/{rid}").json()
    assert data["review_status"] == "approved" and data["reviewer"] == "AA"
    memo = client.get(f"/screenings/{rid}/memo.md")
    assert "attachment" in memo.headers["content-disposition"] and "Strong team" in memo.text


def test_validation_and_not_found(client: TestClient) -> None:
    assert (
        client.post(
            "/api/screenings", data={"company": "x", "thesis": "energy-transition-growth"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/screenings", data={"company": "Acme", "thesis": "nope"}).status_code
        == 400
    )
    assert client.get("/screenings/doesnotexist").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


def test_batch_upload(client: TestClient) -> None:
    csv_bytes = b"company,website\nOctopus Energy,\nOctopus Energy Group,\n"
    res = client.post(
        "/api/batch",
        data={"thesis": "b2b-software-growth"},
        files={"file": ("p.csv", csv_bytes, "text/csv")},
    )
    assert res.status_code == 200
    ids = res.json()["job_ids"]
    assert len(ids) == 2 and all(wait_done(client, i)["status"] == "done" for i in ids)
    bad = client.post(
        "/api/batch",
        data={"thesis": "b2b-software-growth"},
        files={"file": ("p.csv", b"x\n1\n", "text/csv")},
    )
    assert bad.status_code == 400


def test_demo_endpoint_labels_sample(client: TestClient) -> None:
    job = wait_done(
        client,
        client.post("/api/demo", data={"thesis": "energy-transition-growth"}).json()["job_id"],
    )
    page = client.get(f"/screenings/{job['result_id']}").text
    assert "Sample output." in page and "Not a live run" in page


def test_rate_limiter_window() -> None:
    lim = SlidingWindowLimiter(max_requests=2, window_seconds=10)
    assert lim.allow("ip", 0) and lim.allow("ip", 1)
    assert not lim.allow("ip", 2)
    assert lim.allow("other", 2)
    assert lim.allow("ip", 11.5)


def test_api_rate_limit_returns_429(client: TestClient) -> None:
    codes = [
        client.post("/api/screenings", data={"company": "Acme", "thesis": "nope"}).status_code
        for _ in range(22)
    ]
    assert 429 in codes


def test_cli_demo_list_review_and_doctor(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEALSCREEN_DATA_DIR", str(settings.data_dir))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()
    out = runner.invoke(cli_app, ["demo"])
    assert out.exit_code == 0, out.output
    assert "SAMPLE OUTPUT" in out.output
    rid = ResultStore(settings.db_path).list()[0].id
    assert rid in runner.invoke(cli_app, ["list"]).output
    assert (
        runner.invoke(cli_app, ["review", rid, "rejected", "--notes", "too early"]).exit_code == 0
    )
    assert ResultStore(settings.db_path).get(rid).review_status.value == "rejected"  # type: ignore[union-attr]
    assert "disabled" in runner.invoke(cli_app, ["doctor"]).output
    no_key = runner.invoke(cli_app, ["screen", "Acme"])
    assert no_key.exit_code == 2 and "ANTHROPIC_API_KEY" in no_key.output
