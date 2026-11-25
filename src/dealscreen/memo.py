"""Render a screening result as a Markdown first-pass memo."""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined

from dealscreen.models import ScreeningResult

TEMPLATE = """# First-pass screening memo: {{ name }}

{% if r.mode == "sample" -%}
> **SAMPLE OUTPUT, NOT A LIVE RUN.** Fictional company, offline fixtures and scripted model
> responses, produced by `dealscreen demo` to show the output format.
{% elif r.mode == "data-only" -%}
> **Data-only mode.** No LLM was used: this memo contains registry data, deterministic checks and
> rule-based criteria only. Add `ANTHROPIC_API_KEY` to enable the research agent.
{% endif -%}
> Draft for human review. Status: **{{ r.review_status.value }}**{% if r.reviewer %} (by {{ r.reviewer }}){% endif %}.

| | |
|---|---|
| Query | {{ r.query }} |
| Thesis | {{ r.thesis_name }} |
| Recommendation | **{{ r.score.recommendation.value | upper }}** |
| Thesis score | {{ "%.1f" | format(r.score.total) }} / 100 over assessed criteria (evidence coverage {{ "%.0f" | format(r.score.coverage * 100) }}% of criteria weight) |
| Generated | {{ r.created_at.strftime("%Y-%m-%d %H:%M UTC") }}{% if r.model %} with `{{ r.model }}`{% endif %} |
{% if c %}| Company | {{ c.name }} ({{ c.company_number }}), {{ c.status }} |
| Incorporated | {{ c.incorporated_on or "unknown" }} |
| SIC | {{ c.sic_codes | join(", ") or "n/a" }} |
{% endif %}{% if r.website %}| Website | {{ r.website }} |
{% endif %}
{% if a %}
## Summary

{{ a.one_liner }}

**Business model.** {{ a.business_model }}

## Key facts

{% for f in a.facts %}- {{ f.claim }} {{ cite(f.source_ids) }}
{% else %}- No cited facts.
{% endfor %}
{% endif %}
## Thesis fit

| Criterion | Weight | Score | Method | Rationale |
|---|---|---|---|---|
{% for cr in r.score.criteria %}| {{ cr.label }} | {{ cr.weight }} | {{ "%.1f" | format(cr.score) if cr.score is not none else "n/a" }} | {{ cr.method }} | {{ cr.rationale | replace("|", "/") }} {{ cite(cr.source_ids) }} |
{% endfor %}
{% if r.score.failed_must_haves %}Failed must-have criteria: {{ r.score.failed_must_haves | join(", ") }}.
{% endif %}
## Red flags

{% for fl in r.red_flags %}- **[{{ fl.severity.value | upper }}] {{ fl.title }}** ({{ fl.origin }}): {{ fl.detail }} {{ cite(fl.source_ids) }}
{% else %}- None identified.
{% endfor %}
{% if a %}
## Competitive landscape

{% for comp in a.competitors %}- **{{ comp.name }}**: {{ comp.note }} {{ cite(comp.source_ids) }}
{% else %}- Not assessed.
{% endfor %}

## Questions for management

{% for q in a.key_questions %}- {{ q }}
{% else %}- None.
{% endfor %}
{% if a.unverified_claims %}
## Unverified claims

{% for u in a.unverified_claims %}- {{ u }}
{% endfor %}{% endif %}{% endif %}
{% if r.officers %}
## Officers (active)

{% for o in active_officers %}- {{ o.name }}, {{ o.role }}{% if o.appointed_on %}, appointed {{ o.appointed_on }}{% endif %}
{% endfor %}{% endif %}
{% if r.review_notes %}
## Reviewer notes

{{ r.review_notes }}
{% endif %}
## Sources

{% for s in r.sources %}{{ loop.index }}. [{{ s.id }}] [{{ s.title }}]({{ s.url }}), {{ s.kind }}, retrieved {{ s.retrieved_at.strftime("%Y-%m-%d") }}
{% endfor %}
{% if r.warnings %}
## Run notes

{% for w in r.warnings %}- {{ w }}
{% endfor %}{% endif %}"""

_env = Environment(autoescape=False, undefined=StrictUndefined, trim_blocks=False)
_template = _env.from_string(TEMPLATE)


def _cite(ids: list[str]) -> str:
    return "[" + ", ".join(ids) + "]" if ids else ""


def render_memo(r: ScreeningResult) -> str:
    c = r.company
    name = c.name if c else r.query
    active = [o for o in r.officers if o.resigned_on is None][:12]
    text = _template.render(r=r, c=c, a=r.assessment, name=name, cite=_cite, active_officers=active)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n"
