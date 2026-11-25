# Examples

| File | What it is |
|---|---|
| `pipeline.csv` | Example pipeline for `dealscreen batch`. Real UK companies, public data only. |
| `live-data-only/` | Output of a **live data-only run** on 25 Nov 2026: `dealscreen batch examples/pipeline.csv --data-only`. Real Companies House and website data, deterministic rules, **no LLM**. Agent criteria therefore show as "not assessed". Registry data changes; re-run for current results. |
| `SAMPLE-agent-memo-fictional-company.md` | Output of `dealscreen demo`: a **fictional company** with offline fixtures and **scripted model responses**. It shows the agent-mode memo format. It is not a live model run. |

Notes from the live run worth looking at:

- `ripple-energy.md`: the registry rules flag a company in administration (four high-severity flags) and fail two must-have criteria, so it is passed without any LLM call.
- `ohme-technologies.md`: the public register search did not return a confident name match, so the tool refuses to guess an entity and asks for a company number instead of screening the wrong company.
