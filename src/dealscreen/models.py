"""Typed domain models shared by the pipeline, the agent, the CLI and the web app."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Source(BaseModel):
    """A piece of evidence the system actually retrieved. Facts cite these by id."""

    id: str  # e.g. "S3"
    kind: str  # companies_house | website | news | wikipedia | web_search
    title: str
    url: str
    retrieved_at: datetime
    excerpt: str = ""


class Officer(BaseModel):
    name: str
    role: str
    appointed_on: date | None = None
    resigned_on: date | None = None
    nationality: str | None = None
    country_of_residence: str | None = None


class Filing(BaseModel):
    date: date | None
    category: str
    description: str


class CompanyProfile(BaseModel):
    company_number: str
    name: str
    status: str
    company_type: str | None = None
    incorporated_on: date | None = None
    registered_address: str | None = None
    sic_codes: list[str] = Field(default_factory=list)
    previous_names: list[str] = Field(default_factory=list)
    accounts_next_due: date | None = None
    accounts_last_made_up_to: date | None = None
    accounts_overdue: bool = False
    confirmation_statement_overdue: bool = False
    has_insolvency_history: bool = False
    has_charges: bool = False
    url: str


class CompanySearchHit(BaseModel):
    company_number: str
    name: str
    status: str | None = None
    incorporated_on: date | None = None
    address: str | None = None


class Fact(BaseModel):
    claim: str
    source_ids: list[str]


class CriterionRating(BaseModel):
    criterion_id: str
    score: float = Field(ge=0, le=5)
    rationale: str
    source_ids: list[str] = Field(default_factory=list)


class Competitor(BaseModel):
    name: str
    note: str
    source_ids: list[str] = Field(default_factory=list)


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RedFlag(BaseModel):
    title: str
    detail: str
    severity: Severity
    origin: str  # "rule" (deterministic) or "agent"
    source_ids: list[str] = Field(default_factory=list)


class AgentAssessment(BaseModel):
    """What the agent submits through its final `submit_assessment` tool call."""

    one_liner: str
    business_model: str
    facts: list[Fact] = Field(default_factory=list)
    ratings: list[CriterionRating] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)
    key_questions: list[str] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)


class CriterionResult(BaseModel):
    criterion_id: str
    label: str
    weight: float
    score: float | None  # 0-5, None when unknown
    rationale: str
    method: str  # "rule" | "agent" | "missing"
    source_ids: list[str] = Field(default_factory=list)


class Recommendation(StrEnum):
    ADVANCE = "advance"
    REVIEW = "review"
    PASS = "pass"


class ThesisScore(BaseModel):
    total: float  # 0-100 over the criteria that could be assessed
    coverage: float  # share of total weight that had evidence
    recommendation: Recommendation
    failed_must_haves: list[str] = Field(default_factory=list)
    criteria: list[CriterionResult]


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TraceEvent(BaseModel):
    at: datetime
    kind: str  # step | tool_call | tool_result | llm | warning | done | error
    message: str
    data: dict[str, object] = Field(default_factory=dict)


class ScreeningResult(BaseModel):
    id: str
    query: str
    mode: str  # "agent" | "data-only"
    model: str | None
    thesis_name: str
    created_at: datetime
    company: CompanyProfile | None
    officers: list[Officer] = Field(default_factory=list)
    filings: list[Filing] = Field(default_factory=list)
    website: str | None = None
    assessment: AgentAssessment | None = None
    red_flags: list[RedFlag] = Field(default_factory=list)
    score: ThesisScore
    sources: list[Source] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    review_status: ReviewStatus = ReviewStatus.PENDING
    review_notes: str = ""
    reviewer: str | None = None
    memo_markdown: str = ""
