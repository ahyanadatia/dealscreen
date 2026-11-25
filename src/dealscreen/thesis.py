"""Investment thesis definition, loaded from YAML.

A thesis is a weighted list of criteria. `rule` criteria are checked deterministically against
registry data; `agent` criteria are rated 0-5 by the LLM with cited evidence. Keeping both in one
file lets an investment team change what "good" means without touching code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

RuleType = Literal["company_active", "age_years", "sic_prefix", "filings_current", "no_insolvency"]


class Rule(BaseModel):
    type: RuleType
    min: float | None = None
    max: float | None = None
    prefixes: list[str] = Field(default_factory=list)


class Criterion(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    weight: float = Field(gt=0, le=10)
    method: Literal["agent", "rule"]
    guidance: str = ""
    must_have: bool = False
    must_have_min_score: float = Field(default=2.0, ge=0, le=5)
    rule: Rule | None = None

    @model_validator(mode="after")
    def _rule_matches_method(self) -> Criterion:
        if self.method == "rule" and self.rule is None:
            raise ValueError(f"criterion '{self.id}' uses method 'rule' but defines no rule")
        if self.method == "agent" and not self.guidance:
            raise ValueError(f"criterion '{self.id}' uses method 'agent' and needs guidance")
        return self


class Thresholds(BaseModel):
    advance: float = Field(default=70, ge=0, le=100)
    review: float = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if self.review > self.advance:
            raise ValueError("review threshold must not exceed advance threshold")
        return self


class Thesis(BaseModel):
    name: str
    description: str
    focus: str = ""
    thresholds: Thresholds = Field(default_factory=Thresholds)
    min_coverage: float = Field(default=0.5, ge=0, le=1)
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Thesis:
        ids = [c.id for c in self.criteria]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate criterion ids: {sorted(dupes)}")
        return self

    @property
    def agent_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.method == "agent"]

    def prompt_block(self) -> str:
        lines = [f"Thesis: {self.name}", self.description.strip()]
        if self.focus:
            lines.append(f"Focus: {self.focus.strip()}")
        lines.append("Criteria you must rate (0 = clearly fails, 5 = strong fit):")
        for c in self.agent_criteria:
            must = " [MUST-HAVE]" if c.must_have else ""
            lines.append(f"- {c.id} ({c.label}, weight {c.weight}){must}: {c.guidance.strip()}")
        return "\n".join(lines)


def load_thesis(path: str | Path) -> Thesis:
    data: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"thesis file {path} must contain a mapping")
    return Thesis.model_validate(data)
