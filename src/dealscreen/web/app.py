"""FastAPI web app: screening form, live agent trace (SSE), memo view and review workflow."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

import markdown as md
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dealscreen import __version__
from dealscreen.config import Settings
from dealscreen.models import ReviewStatus, ScreeningResult, TraceEvent
from dealscreen.pipeline import Screener, ScreenRequest, build_http, build_llm, read_pipeline_csv
from dealscreen.store import ResultStore
from dealscreen.thesis import Thesis, load_thesis
from dealscreen.web.jobs import Job, JobManager
from dealscreen.web.ratelimit import SlidingWindowLimiter

HERE = Path(__file__).parent
MAX_CSV_BYTES = 200_000
MAX_BATCH_ROWS = 50

ScreenerFactory = Callable[[Thesis, bool], Screener]


class JobTicket(TypedDict, total=False):
    kind: Literal["demo", "screen"]
    thesis: str
    query: str
    website: str
    company_number: str
    data_only: bool


def encode_job_ticket(ticket: JobTicket) -> str:
    raw = json.dumps(ticket, separators=(",", ":"), sort_keys=True).encode()
    return "t1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_job_ticket(job_id: str) -> JobTicket | None:
    if not job_id.startswith("t1."):
        return None
    data = job_id[3:]
    pad = "=" * (-len(data) % 4)
    try:
        parsed = json.loads(base64.urlsafe_b64decode(data + pad))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("kind") not in {"demo", "screen"}:
        return None
    if not isinstance(parsed.get("thesis"), str):
        return None
    return parsed  # type: ignore[return-value]


def load_theses(thesis_dir: Path) -> dict[str, Thesis]:
    out: dict[str, Thesis] = {}
    for path in sorted(thesis_dir.glob("*.y*ml")):
        out[path.stem] = load_thesis(path)
    if not out:
        raise RuntimeError(f"no thesis YAML files found in {thesis_dir}")
    return out


def create_app(
    *,
    settings: Settings | None = None,
    thesis_dir: Path = Path("theses"),
    screener_factory: ScreenerFactory | None = None,
    store: ResultStore | None = None,
    serverless: bool | None = None,
) -> FastAPI:
    settings = settings or Settings()
    theses = load_theses(thesis_dir)
    store = store or ResultStore(settings.db_path)
    jobs = JobManager(max_workers=max(1, settings.batch_concurrency))
    limiter = SlidingWindowLimiter(max_requests=20, window_seconds=60)
    llm_enabled = settings.llm_enabled or screener_factory is not None
    # On Vercel, instances don't share memory or background threads, so job
    # parameters travel in the job id and work runs inside the SSE request.
    serverless = os.environ.get("VERCEL") == "1" if serverless is None else serverless

    if screener_factory is None:
        http = build_http(settings)
        llm = build_llm(settings) if settings.llm_enabled else None

        def screener_factory(thesis: Thesis, data_only: bool) -> Screener:
            return Screener(
                settings, thesis, http=http, llm=llm, data_only=data_only or llm is None
            )

    factory: ScreenerFactory = screener_factory

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        jobs.shutdown()

    app = FastAPI(title="dealscreen", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["markdown"] = lambda text: md.markdown(text or "", extensions=["tables"])
    templates.env.globals.update(
        version=__version__, llm_enabled=llm_enabled, model=settings.anthropic_model
    )

    def check_rate(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise HTTPException(429, "Rate limit exceeded: max 20 screening requests per minute.")

    def get_thesis(key: str) -> Thesis:
        if key not in theses:
            raise HTTPException(400, f"Unknown thesis '{key}'")
        return theses[key]

    def screen_fn(
        req: ScreenRequest, thesis: Thesis, data_only: bool
    ) -> Callable[[Callable[[TraceEvent], None]], ScreeningResult]:
        screener = factory(thesis, data_only)

        def run(emit: Callable[[TraceEvent], None]) -> ScreeningResult:
            result = screener.screen(req, emit)
            store.save(result)
            return result

        return run

    def demo_fn(thesis: Thesis) -> Callable[[Callable[[TraceEvent], None]], ScreeningResult]:
        from dealscreen.demo import run_demo

        def run(emit: Callable[[TraceEvent], None]) -> ScreeningResult:
            result = run_demo(settings, thesis, emit)
            store.save(result)
            return result

        return run

    def start(req: ScreenRequest, thesis_key: str, thesis: Thesis, data_only: bool) -> str:
        if serverless:
            return encode_job_ticket(
                {
                    "kind": "screen",
                    "thesis": thesis_key,
                    "query": req.query,
                    "website": req.website or "",
                    "company_number": req.company_number or "",
                    "data_only": data_only,
                }
            )
        try:
            return jobs.submit(req.query, screen_fn(req, thesis, data_only)).id
        except OverflowError as exc:
            raise HTTPException(503, str(exc)) from exc

    def ticket_label(ticket: JobTicket) -> str:
        if ticket.get("kind") == "demo":
            return "Sample: Gridline Analytics"
        return str(ticket.get("query") or "screening")

    def materialise_ticket(job_id: str) -> Job | None:
        ticket = decode_job_ticket(job_id)
        if ticket is None:
            return None
        thesis = get_thesis(str(ticket["thesis"]))
        if ticket.get("kind") == "demo":
            fn = demo_fn(thesis)
            label = ticket_label(ticket)
        else:
            req = ScreenRequest(
                str(ticket.get("query") or "").strip(),
                (ticket.get("website") or "").strip() or None,
                (ticket.get("company_number") or "").strip() or None,
            )
            fn = screen_fn(req, thesis, bool(ticket.get("data_only")))
            label = ticket_label(ticket)
        try:
            return jobs.submit(label, fn, job_id=job_id, start=False)
        except OverflowError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "results": store.list(200),
                "theses": theses,
                "default_thesis": "energy-transition-growth"
                if "energy-transition-growth" in theses
                else next(iter(theses)),
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "llm_enabled": llm_enabled}

    @app.post("/api/screenings")
    def create_screening(
        request: Request,
        company: Annotated[str, Form(min_length=2, max_length=120)],
        thesis: Annotated[str, Form()],
        website: Annotated[str, Form(max_length=300)] = "",
        company_number: Annotated[str, Form(max_length=12)] = "",
        data_only: Annotated[bool, Form()] = False,
    ) -> dict[str, str]:
        check_rate(request)
        req = ScreenRequest(
            company.strip(), website.strip() or None, company_number.strip() or None
        )
        return {"job_id": start(req, thesis, get_thesis(thesis), data_only)}

    @app.post("/api/batch")
    async def create_batch(
        request: Request,
        file: Annotated[UploadFile, File()],
        thesis: Annotated[str, Form()],
        data_only: Annotated[bool, Form()] = False,
    ) -> dict[str, list[str]]:
        check_rate(request)
        raw = await file.read(MAX_CSV_BYTES + 1)
        if len(raw) > MAX_CSV_BYTES:
            raise HTTPException(413, "CSV too large (max 200 KB)")
        tmp = settings.data_dir / "uploads"
        tmp.mkdir(parents=True, exist_ok=True)
        path = tmp / "batch.csv"
        path.write_bytes(raw)
        try:
            reqs = read_pipeline_csv(path)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, f"Invalid CSV: {exc}") from exc
        if not reqs:
            raise HTTPException(400, "CSV has no company rows")
        if len(reqs) > MAX_BATCH_ROWS:
            raise HTTPException(400, f"Batch limited to {MAX_BATCH_ROWS} companies")
        th = get_thesis(thesis)
        return {"job_ids": [start(r, thesis, th, data_only) for r in reqs]}

    @app.post("/api/demo")
    def create_demo(request: Request, thesis: Annotated[str, Form()]) -> dict[str, str]:
        """Offline sample run on a fictional company with scripted model responses."""
        check_rate(request)
        th = get_thesis(thesis)
        if serverless:
            return {"job_id": encode_job_ticket({"kind": "demo", "thesis": thesis})}
        try:
            return {"job_id": jobs.submit("Sample: Gridline Analytics", demo_fn(th)).id}
        except OverflowError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            ticket = decode_job_ticket(job_id)
            if ticket is None:
                raise HTTPException(404, "Unknown job")
            return {
                "id": job_id,
                "label": ticket_label(ticket),
                "status": "queued",
                "result_id": None,
                "error": None,
            }
        return {
            "id": job.id,
            "label": job.label,
            "status": job.status,
            "result_id": job.result_id,
            "error": job.error,
        }

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        job = jobs.get(job_id)
        if job is None:
            job = materialise_ticket(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        jobs.start(job.id)

        async def stream() -> AsyncIterator[str]:
            seen = 0
            while True:
                await asyncio.to_thread(job.wait_for, seen, 10.0)
                while seen < len(job.events):
                    ev = job.events[seen]
                    seen += 1
                    yield f"data: {json.dumps({'kind': ev.kind, 'message': ev.message})}\n\n"
                if job.status in {"done", "error"} and seen >= len(job.events):
                    payload = {"status": job.status, "result_id": job.result_id, "error": job.error}
                    yield f"event: end\ndata: {json.dumps(payload)}\n\n"
                    return
                yield ": keep-alive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def get_result(result_id: str) -> ScreeningResult:
        r = store.get(result_id)
        if r is None:
            raise HTTPException(404, "Screening not found")
        return r

    @app.get("/screenings/{result_id}", response_class=HTMLResponse)
    def view(request: Request, result_id: str) -> HTMLResponse:
        r = get_result(result_id)
        sources = {s.id: s for s in r.sources}
        return templates.TemplateResponse(request, "result.html", {"r": r, "sources": sources})

    @app.get("/screenings/{result_id}/memo.md", response_class=PlainTextResponse)
    def memo(result_id: str) -> PlainTextResponse:
        r = get_result(result_id)
        name = (r.company.name if r.company else r.query).lower().replace(" ", "-")
        return PlainTextResponse(
            r.memo_markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{name}-memo.md"'},
        )

    @app.get("/api/screenings/{result_id}")
    def result_json(result_id: str) -> ScreeningResult:
        return get_result(result_id)

    @app.post("/screenings/{result_id}/review")
    def review(
        result_id: str,
        decision: Annotated[ReviewStatus, Form()],
        notes: Annotated[str, Form(max_length=4000)] = "",
        reviewer: Annotated[str, Form(max_length=80)] = "",
    ) -> RedirectResponse:
        try:
            store.review(result_id, decision, notes, reviewer or None)
        except KeyError as exc:
            raise HTTPException(404, "Screening not found") from exc
        return RedirectResponse(f"/screenings/{result_id}", status_code=303)

    return app
