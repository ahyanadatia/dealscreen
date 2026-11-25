"""Command-line interface: `dealscreen screen|batch|list|show|review|serve|doctor`."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from dealscreen.config import Settings
from dealscreen.models import ReviewStatus, ScreeningResult, TraceEvent
from dealscreen.pipeline import MissingAPIKeyError, Screener, ScreenRequest, read_pipeline_csv
from dealscreen.store import ResultStore
from dealscreen.thesis import load_thesis

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

DEFAULT_THESIS = Path("theses/energy-transition-growth.yaml")
ThesisOpt = Annotated[Path, typer.Option("--thesis", "-t", help="Thesis YAML file.")]
DataOnlyOpt = Annotated[
    bool, typer.Option("--data-only", help="Skip the LLM agent: registry data and rules only.")
]


def _settings() -> Settings:
    return Settings()


def _screener(thesis: Path, data_only: bool) -> tuple[Screener, ResultStore]:
    settings = _settings()
    try:
        th = load_thesis(thesis)
    except (OSError, ValueError) as exc:
        typer.secho(f"Could not load thesis {thesis}: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc
    try:
        screener = Screener(settings, th, data_only=data_only)
    except MissingAPIKeyError as exc:
        typer.secho(str(exc), fg="yellow", err=True)
        raise typer.Exit(2) from exc
    return screener, ResultStore(settings.db_path)


def _print_event(ev: TraceEvent) -> None:
    colours = {"warning": "yellow", "error": "red", "done": "green", "tool_call": "cyan"}
    typer.secho(f"  {ev.kind:<11} {ev.message}", fg=colours.get(ev.kind), err=True)


def _summary(r: ScreeningResult) -> str:
    name = r.company.name if r.company else r.query
    high = sum(1 for f in r.red_flags if f.severity.value == "high")
    return (
        f"{name}: {r.score.recommendation.value.upper()} {r.score.total:.1f}/100 "
        f"(coverage {r.score.coverage:.0%}, {high} high flags) id={r.id}"
    )


@app.command()
def screen(
    company: Annotated[str, typer.Argument(help="Company name or UK company number.")],
    website: Annotated[str | None, typer.Option("--website", "-w")] = None,
    company_number: Annotated[str | None, typer.Option("--number", "-n")] = None,
    thesis: ThesisOpt = DEFAULT_THESIS,
    data_only: DataOnlyOpt = False,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Write the memo here.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full result as JSON.")] = False,
) -> None:
    """Screen one company and print a first-pass memo."""
    screener, store = _screener(thesis, data_only)
    result = screener.screen(ScreenRequest(company, website, company_number), emit=_print_event)
    store.save(result)
    if out:
        out.write_text(result.memo_markdown, encoding="utf-8")
        typer.secho(f"Memo written to {out}", fg="green", err=True)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    elif not out:
        typer.echo(result.memo_markdown)
    typer.secho(_summary(result), bold=True, err=True)


@app.command()
def batch(
    csv_path: Annotated[Path, typer.Argument(help="CSV with company[,website,company_number].")],
    thesis: ThesisOpt = DEFAULT_THESIS,
    data_only: DataOnlyOpt = False,
    out_dir: Annotated[Path, typer.Option("--out-dir")] = Path("memos"),
) -> None:
    """Screen a pipeline CSV; writes one memo per company plus a ranked summary CSV."""
    try:
        requests = read_pipeline_csv(csv_path)
    except (OSError, ValueError) as exc:
        typer.secho(f"Could not read {csv_path}: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc
    screener, store = _screener(thesis, data_only)
    out_dir.mkdir(parents=True, exist_ok=True)
    typer.secho(f"Screening {len(requests)} companies...", err=True)
    results = screener.batch(requests)
    rows: list[dict[str, object]] = []
    for req, res in zip(requests, results, strict=True):
        if isinstance(res, Exception):
            typer.secho(f"  {req.query}: FAILED ({res})", fg="red", err=True)
            rows.append({"company": req.query, "status": "error", "error": str(res)})
            continue
        store.save(res)
        slug = "".join(ch if ch.isalnum() else "-" for ch in req.query.lower()).strip("-")
        (out_dir / f"{slug}.md").write_text(res.memo_markdown, encoding="utf-8")
        typer.echo("  " + _summary(res))
        rows.append(
            {
                "company": req.query,
                "status": "ok",
                "registered_name": res.company.name if res.company else "",
                "company_number": res.company.company_number if res.company else "",
                "recommendation": res.score.recommendation.value,
                "score": res.score.total,
                "coverage": res.score.coverage,
                "high_flags": sum(1 for f in res.red_flags if f.severity.value == "high"),
                "result_id": res.id,
            }
        )
    ok = sorted((r for r in rows if r["status"] == "ok"), key=lambda r: -float(str(r["score"])))
    failed = [r for r in rows if r["status"] != "ok"]
    fields = [
        "company",
        "status",
        "registered_name",
        "company_number",
        "recommendation",
        "score",
        "coverage",
        "high_flags",
        "result_id",
        "error",
    ]
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ok + failed)
    typer.secho(f"Wrote {len(ok)} memos and summary.csv to {out_dir}/", fg="green", err=True)


@app.command("list")
def list_results(limit: int = 20) -> None:
    """List recent screenings."""
    store = ResultStore(_settings().db_path)
    for r in store.list(limit):
        typer.echo(f"{r.created_at:%Y-%m-%d %H:%M}  [{r.review_status.value:<8}]  {_summary(r)}")


@app.command()
def show(result_id: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Print a saved memo."""
    r = ResultStore(_settings().db_path).get(result_id)
    if r is None:
        typer.secho(f"No screening with id {result_id}", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(r.model_dump_json(indent=2) if as_json else r.memo_markdown)


@app.command()
def review(
    result_id: str,
    decision: Annotated[ReviewStatus, typer.Argument(help="approved | rejected | pending")],
    notes: Annotated[str, typer.Option("--notes")] = "",
    reviewer: Annotated[str | None, typer.Option("--reviewer")] = None,
) -> None:
    """Record a human review decision on a screening."""
    try:
        r = ResultStore(_settings().db_path).review(result_id, decision, notes, reviewer)
    except KeyError:
        typer.secho(f"No screening with id {result_id}", fg="red", err=True)
        raise typer.Exit(1) from None
    typer.secho(f"{_summary(r)} -> {r.review_status.value}", fg="green")


@app.command()
def demo(thesis: ThesisOpt = DEFAULT_THESIS) -> None:
    """Run an offline sample screening of a fictional company (no network, no API key)."""
    from dealscreen.demo import run_demo

    settings = _settings()
    result = run_demo(settings, load_thesis(thesis), emit=_print_event)
    ResultStore(settings.db_path).save(result)
    typer.echo(result.memo_markdown)
    typer.secho(
        "SAMPLE OUTPUT: fictional company and scripted model responses.", fg="yellow", err=True
    )
    typer.secho(_summary(result), bold=True, err=True)


@app.command()
def doctor() -> None:
    """Check configuration and which data sources are available."""
    s = _settings()
    report = {
        "llm_agent": "enabled" if s.llm_enabled else "disabled (set ANTHROPIC_API_KEY)",
        "model": s.anthropic_model,
        "anthropic_web_search": s.anthropic_web_search,
        "companies_house": "official API"
        if s.companies_house_api_key
        else "public website (no key)",
        "news": "Google News RSS (no key needed)",
        "data_dir": str(s.data_dir.resolve()),
    }
    typer.echo(json.dumps(report, indent=2))


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    thesis_dir: Annotated[Path, typer.Option("--thesis-dir")] = Path("theses"),
) -> None:
    """Run the web app."""
    import uvicorn

    from dealscreen.web.app import create_app

    uvicorn.run(create_app(thesis_dir=thesis_dir), host=host, port=port)


def main() -> None:  # pragma: no cover
    app(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    main()
