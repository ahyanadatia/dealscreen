# First-pass screening memo: GRIDLINE ANALYTICS LTD

> **SAMPLE OUTPUT, NOT A LIVE RUN.** Fictional company, offline fixtures and scripted model
> responses, produced by `dealscreen demo` to show the output format.
> Draft for human review. Status: **pending**.

| | |
|---|---|
| Query | Gridline Analytics |
| Thesis | Energy transition & industrial tech (growth) |
| Recommendation | **ADVANCE** |
| Thesis score | 82.8 / 100 over assessed criteria (evidence coverage 100% of criteria weight) |
| Generated | 2026-11-25 02:16 UTC with `claude-sonnet-5-5 (scripted responses)` |
| Company | GRIDLINE ANALYTICS LTD (ZZ000001), active |
| Incorporated | 2019-03-12 |
| SIC | 62012, 71122 |
| Website | https://gridline.example |

## Summary

Gridline sells SaaS that forecasts low-voltage grid congestion so network operators can connect more heat pumps, EV chargers and solar without new reinforcement.

**Business model.** Annual SaaS licence per network area, sold to distribution network operators (enterprise B2B, long sales cycles).

## Key facts

- Produces 15-minute congestion forecasts from smart-meter and substation data. [S4]
- Founded 2019; about 48 employees in London and Rotterdam (company claim). [S5, S1]
- Customers include three UK DNOs and one Dutch grid operator (company claim). [S5]
- Reported a Series A round in March 2026. [S6]

## Thesis fit

| Criterion | Weight | Score | Method | Rationale |
|---|---|---|---|---|
| Sector fit | 3.0 | 5.0 | agent | Core product directly increases grid hosting capacity for electrification. [S4] |
| Technology & data moat | 2.0 | 3.0 | agent | Proprietary forecasting models on operator data; defensibility depends on data access agreements, which are not evidenced. [S4, S5] |
| Commercial traction | 2.5 | 3.0 | agent | A handful of named-category utility customers and a Series A; revenue scale not disclosed. [S5, S6] |
| Business model quality | 1.5 | 4.0 | agent | Recurring SaaS licences with enterprise customers; concentration risk is high. [S4] |
| Measurable impact | 1.0 | 3.0 | agent | Quantifies deferred reinforcement (40 GWh), but this is an unaudited company claim. [S5] |
| Active company | 1.0 | 5.0 | rule | Companies House status: active. [S1] |
| Filings up to date | 1.0 | 5.0 | rule | Accounts and confirmation statement are not overdue. [S1] |
| No insolvency history | 1.0 | 5.0 | rule | No insolvency history on the register. [S1] |
| Growth-stage maturity | 1.0 | 5.0 | rule | Incorporated 2019-03-12 (7.7 years; target 3-15). [S1] |
| Registered activity (SIC) | 0.5 | 5.0 | rule | SIC codes 62012, 71122 match the target list. [S1] |

## Red flags

- **[MEDIUM] Officer churn** (rule): 3 officer resignations in the last 24 months (PLACEHOLDER, Ana, DUMMY, Lee, FICTION, Sam). [S2]
- **[MEDIUM] Customer concentration** (agent): Revenue appears to depend on four grid operators; losing one would be material. [S5]
- **[LOW] Previous names** (rule): Previously registered as: GRIDLINE LABS LTD. [S1]
- **[LOW] Charges registered** (rule): Security interests (charges) are registered against the company; check lenders and whether they are outstanding. [S1]

## Competitive landscape

- **VoltSense Grid**: Older UK grid-analytics vendor found on the register; overlapping LV monitoring. [S8]
- **FlexNode Energy Systems**: Flexibility-market software; adjacent rather than direct. [S8]
- **In-house DNO data teams**: Main substitute: operators building forecasting internally. 

## Questions for management

- What share of ARR comes from the largest customer, and when do contracts renew?
- Who owns the smart-meter data and the models trained on it?
- How was the 40 GWh deferred-reinforcement figure measured?
- Why did two directors and the company secretary leave in the last 15 months?

## Unverified claims

- 40 GWh of reinforcement deferred (company claim).
- Has 2,000 paying customers.

## Officers (active)

- EXAMPLE, Priya, director, appointed 2019-03-12
- SAMPLE, Tom, director, appointed 2019-03-12

## Sources

1. [S1] [Companies House: GRIDLINE ANALYTICS LTD (ZZ000001)](https://find-and-update.company-information.service.gov.uk/company/ZZ000001), companies_house, retrieved 2026-11-25
2. [S2] [Officers of GRIDLINE ANALYTICS LTD](https://find-and-update.company-information.service.gov.uk/company/ZZ000001/officers), companies_house, retrieved 2026-11-25
3. [S3] [Filing history of GRIDLINE ANALYTICS LTD](https://find-and-update.company-information.service.gov.uk/company/ZZ000001/filing-history), companies_house, retrieved 2026-11-25
4. [S4] [Gridline Analytics: grid-edge intelligence for distribution networks](https://gridline.example), website, retrieved 2026-11-25
5. [S5] [About Gridline Analytics](https://gridline.example/about), website, retrieved 2026-11-25
6. [S6] [Gridline Analytics raises Series A to expand grid forecasting (sample headline) (Example Energy News)](https://news.example/gridline-series-a), news, retrieved 2026-11-25
7. [S7] [Network operator trials congestion forecasting from Gridline (sample headline) (Example Utility Week)](https://news.example/gridline-trial), news, retrieved 2026-11-25
8. [S8] [Companies House search: grid analytics](https://find-and-update.company-information.service.gov.uk/search/companies?q=grid+analytics), companies_house, retrieved 2026-11-25

## Run notes

- Removed unknown source ids ['S99'] from a fact.
- Fact without valid citation moved to unverified: Has 2,000 paying customers.

