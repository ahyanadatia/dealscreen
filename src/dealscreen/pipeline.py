"""End-to-end screening pipeline: resolve -> collect -> (agent) -> rules -> score -> memo."""

from __future__ import annotations

import csv
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dealscreen.agent import MessagesClient, ScreeningAgent
from dealscreen.config import Settings
from dealscreen.http import DiskCache, FetchError, HttpClient, RateLimiter, parse_networks
from dealscreen.ledger import SourceLedger
from dealscreen.memo import render_memo
from dealscreen.models import ScreeningResult, Severity, TraceEvent
from dealscreen.redflags import registry_red_flags
from dealscreen.registry import RegistryRecord, fetch_record, resolve
from dealscreen.scoring import score
from dealscreen.sources.companies_house import (
    CompaniesHouseSource,
    make_source,
    normalise_company_number,
)
from dealscreen.sources.web import fetch_page
from dealscreen.thesis import Thesis
from dealscreen.tools import ToolContext

Emit = Callable[[TraceEvent], None]
SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


@dataclass
class ScreenRequest:
    query: str
    website: str | None = None
    company_number: str | None = None


class MissingAPIKeyError(RuntimeError):
    pass


def build_http(settings: Settings) -> HttpClient:
    limiter = RateLimiter(
        default_interval=0.5,
        per_host={
            "find-and-update.company-information.service.gov.uk": 1.0,
            "api.company-information.service.gov.uk": 0.6,  # API limit is 600 req / 5 min
            "news.google.com": 1.0,
        },
    )
    cache = DiskCache(settings.data_dir / "http_cache.sqlite3", settings.cache_ttl_seconds)
    return HttpClient(
        user_agent=settings.user_agent,
        timeout=settings.http_timeout_seconds,
        cache=cache,
        rate_limiter=limiter,
        allowed_networks=parse_networks(settings.trusted_egress_cidrs),
    )


def build_llm(settings: Settings) -> MessagesClient:
    if not settings.llm_enabled or settings.anthropic_api_key is None:
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set. Add it to .env, or use --data-only for a registry-only run."
        )
    import anthropic

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=120.0,
        max_retries=3,
    )
    return client.messages


class Screener:
    def __init__(
        self,
        settings: Settings,
        thesis: Thesis,
        *,
        http: HttpClient | None = None,
        ch: CompaniesHouseSource | None = None,
        llm: MessagesClient | None = None,
        data_only: bool = False,
    ):
        self.settings = settings
        self.thesis = thesis
        self.http = http or build_http(settings)
        key = settings.companies_house_api_key
        self.ch = ch or make_source(self.http, key.get_secret_value() if key else None)
        self.data_only = data_only
        self.llm = None if data_only else (llm or build_llm(settings))

    @property
    def mode(self) -> str:
        return "data-only" if self.data_only else "agent"

    def screen(self, req: ScreenRequest, emit: Emit | None = None) -> ScreeningResult:
        trace: list[TraceEvent] = []

        def log(kind: str, message: str, **data: object) -> None:
            ev = TraceEvent(at=datetime.now(UTC), kind=kind, message=message, data=dict(data))
            trace.append(ev)
            if emit:
                emit(ev)

        ledger = SourceLedger()
        warnings: list[str] = []
        log("step", f"Resolving '{req.query}' on Companies House ({self.ch.backend})")
        rec = resolve(self.ch, ledger, req.query, req.company_number)
        warnings.extend(rec.warnings)
        if rec.company:
            log(
                "step",
                f"Matched {rec.company.name} ({rec.company.company_number}), {rec.company.status}",
            )
        else:
            log("warning", "No registry match; continuing with web evidence only")

        website = req.website
        website_found: bool | None = None
        ctx = ToolContext(http=self.http, ch=self.ch, ledger=ledger)
        if website:
            try:
                page = fetch_page(self.http, website)
                ctx.fetched_pages[page.url] = page
                ledger.add(
                    "website", page.title or page.url, page.url, page.description or page.text
                )
                website = page.url
                website_found = True
                log("step", f"Fetched website {page.url}")
            except FetchError as exc:
                website_found = False
                warnings.append(f"Website fetch failed: {exc}")
                log("warning", f"Website fetch failed: {exc}")

        assessment = None
        usage: dict[str, int] = {}
        if self.llm is not None:
            log("step", "Starting research agent")
            agent = ScreeningAgent(
                self.llm,
                ctx,
                model=self.settings.anthropic_model,
                max_turns=self.settings.max_agent_turns,
                max_tokens=self.settings.max_output_tokens,
                web_search=self.settings.anthropic_web_search,
                emit=emit,
            )
            result = agent.run(self.thesis, req.query, self._dossier(rec, ctx), website)
            trace.extend(result.trace)
            warnings.extend(result.warnings)
            assessment = result.assessment
            usage = result.usage
            override = normalise_company_number(result.company_number_override or "")
            if override and (rec.company is None or override != rec.company.company_number):
                log("step", f"Agent corrected the entity to {override}; refetching registry data")
                try:
                    rec = fetch_record(self.ch, ledger, override)
                except (FetchError, ValueError) as exc:
                    warnings.append(f"Could not fetch agent-selected entity {override}: {exc}")
            if website is None:
                site = next((s for s in ledger.all() if s.kind == "website"), None)
                website = site.url if site else None
                website_found = site is not None

        flags = registry_red_flags(
            rec.company,
            rec.officers,
            rec.filings,
            website_found,
            source_id=rec.source_ids.get("profile", ""),
            officers_source_id=rec.source_ids.get("officers", ""),
            filings_source_id=rec.source_ids.get("filings", ""),
        )
        for fl in flags:
            fl.source_ids = [s for s in fl.source_ids if s]
        if assessment is not None:
            flags.extend(assessment.red_flags)
        flags.sort(key=lambda f: SEVERITY_ORDER[f.severity])

        thesis_score = score(
            self.thesis, rec.company, assessment, registry_source_id=rec.source_ids.get("profile")
        )
        log(
            "done",
            f"Recommendation: {thesis_score.recommendation.value} ({thesis_score.total:.1f}/100)",
        )
        result_obj = ScreeningResult(
            id=uuid.uuid4().hex[:12],
            query=req.query,
            mode=self.mode,
            model=None if self.data_only else self.settings.anthropic_model,
            thesis_name=self.thesis.name,
            created_at=datetime.now(UTC),
            company=rec.company,
            officers=rec.officers,
            filings=rec.filings,
            website=website,
            assessment=assessment,
            red_flags=flags,
            score=thesis_score,
            sources=ledger.all(),
            trace=trace,
            warnings=warnings,
            usage=usage,
        )
        result_obj.memo_markdown = render_memo(result_obj)
        return result_obj

    @staticmethod
    def _dossier(rec: RegistryRecord, ctx: ToolContext) -> dict[str, Any]:
        active = [o for o in rec.officers if o.resigned_on is None]
        return {
            "registry_source_ids": rec.source_ids,
            "profile": rec.company.model_dump(mode="json") if rec.company else None,
            "registry_candidates": [c.model_dump(mode="json") for c in rec.candidates[:5]],
            "active_officers": [o.model_dump(mode="json") for o in active[:12]],
            "resigned_officer_count": len(rec.officers) - len(active),
            "recent_filings": [f.model_dump(mode="json") for f in rec.filings[:12]],
            "website_pages_already_fetched": [
                {"source_id": s.id, "url": s.url, "title": s.title}
                for s in ctx.ledger.all()
                if s.kind == "website"
            ],
        }

    def batch(
        self, requests: Iterable[ScreenRequest], emit: Emit | None = None
    ) -> list[ScreeningResult | Exception]:
        reqs = list(requests)

        def one(r: ScreenRequest) -> ScreeningResult | Exception:
            try:
                return self.screen(r, emit)
            except Exception as exc:  # keep the batch going; report per row
                return exc

        with ThreadPoolExecutor(max_workers=max(1, self.settings.batch_concurrency)) as pool:
            return list(pool.map(one, reqs))


def read_pipeline_csv(path: Path) -> list[ScreenRequest]:
    """Read a pipeline CSV with a `company` (or `name`) column and optional `website`,
    `company_number` columns."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("CSV is empty")
        cols = {c.strip().lower(): c for c in reader.fieldnames}
        name_col = cols.get("company") or cols.get("name")
        if name_col is None:
            raise ValueError("CSV needs a 'company' or 'name' column")
        out = []
        for row in reader:
            name = (row.get(name_col) or "").strip()
            if not name:
                continue
            web_col = cols.get("website")
            num_col = cols.get("company_number")
            out.append(
                ScreenRequest(
                    query=name,
                    website=((row.get(web_col) or "").strip() or None) if web_col else None,
                    company_number=((row.get(num_col) or "").strip() or None) if num_col else None,
                )
            )
        return out
