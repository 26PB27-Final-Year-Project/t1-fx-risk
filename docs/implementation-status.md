# Implementation status and assumptions

## Completed in the first milestone

- Typed contracts reject naive timestamps, fractional minor-unit amounts, duplicate identities, unfunded FX sources, and invalid event references.
- Explicit calendar coverage, weekends/holidays, early closes, joint-calendar value dates, and DST ambiguity checks.
- Deterministic event queue; instruction, SSI, FX, and cash progress independently.
- Missing an affirmation cutoff invokes fallback routing; actual deadline failure remains a separate outcome.
- Segregated pools, reservations, idempotent postings, cash conservation, whole-trade settlement, and deterministic skip-unfunded priority.
- Cash receipts exactly at a deadline are available before deadline evaluation.
- Late recovery preserves the missed-deadline label; unresolved trades remain censored.
- Point-in-time snapshots filter both event time and availability time. Rule scores never masquerade as model probabilities.
- Deterministic peak cash requirements and a scenario-capable HiGHS LP with currency conversions, capacities, rounding checks, local budget marginal, and a labeled fixed-percentage fallback.
- Matched replay of zero/half/full-budget funding and LP funding, with bridge and overdue costs.
- Immutable, checksummed JSON runs and a run-scoped read-only API.

## Dataset milestone

- Seeded, independent random streams generate trade sizes, arrivals, counterparty delays, and four failure injections.
- Configurable calendar and FX route profiles determine T+1 deadlines, booking cutoffs, and joint-calendar value dates.
- Explicit settlement windows defer weekend/closed-hour recovery until the next opening.
- Whole-day train/tuning/calibration/test partitions are separated by recovery-horizon embargoes. Each generated day is an independent cash episode.
- Ten typed tables enforce UTC timestamps, integer minor units, unique identities, references, cash conservation, and separation of hidden simulation truth.
- Immutable dataset releases contain canonical JSON, optional explicit-schema Parquet, replayable scenarios, configuration, provenance, checksums, and a data card.
- CLI commands generate, validate, and export datasets. `make smoke` generates 1,000 trades with two snapshots each.

## Current simplifications

1. The demo uses three purchases, USD settlement, three segregated cash pools, and an externally funded EUR/USD receipt. The standalone LP supports multiple currencies; the comparison pipeline deliberately rejects non-USD portfolios until valuation rates are supplied explicitly.
2. The fixture's market profile is synthetic. Its calendar utility is tested independently; the fixture timestamps are explicit inputs, not generated from an official calendar feed. The joint-calendar FX utility is a simplified configurable convention, not a universal spot-date rule.
3. All securities are assumed available. There is no securities inventory, contractual netting, partial settlement, or real custodian integration.
4. FX source-currency commitments are capacity-checked. Full source-currency ledger movements and treasury-wide FX optimization are not implemented.
5. Reserves in replay are drawn in full at the decision time and held until the recovery-window end. Interest accrues on outstanding drawn principal; repayment uses only actual remaining cash. Unrepaid amounts are reported, but there is not yet a rolling next-window engine. Idle undrawn facility accounting is deferred.
6. The requirement builder uses contractual deadline times and observable expected FX credits. It is conservative about overdue/unobserved receipts. The generic LP accepts weighted scenarios, but the demo supplies one scenario; a learned FX timing distribution is not yet implemented.
7. The LP objective is an assumed planning surrogate. Realised bridge/late/route costs are independently computed in replay and are not interchangeable with that objective.
8. Costs use the configured annual/daily rates and elapsed fractional days. The fallback fee is in each trade currency's minor units. These are assumptions, not jurisdictional penalty schedules.
9. Snapshot reconstruction currently covers immediate reservation consumption in this simulator. Persistent, externally created reservations and historical counterparty aggregates require additional observable records before they can be included.
10. Simulation runs use JSON. Dataset releases support JSON and optional Parquet; auto mode reports JSON-only output explicitly when PyArrow is unavailable. PyArrow 23.0.1 passed local round-trip, export, and 1,000-trade release validation. CI installs the extra and exercises both formats.
11. The API is read-only and binds to loopback. Authentication, durable jobs, funding recomputation, and the React dashboard are not implemented. Interactive `/docs` describes the backend, not the planned dashboard.
12. Rule scores are hand-written heuristics. XGBoost, logistic regression, calibration, SHAP, Optuna, and E1–E6 research results have not yet been implemented or claimed.

## Validation recorded during development

The test suite also checks seeded generation, temporal partition boundaries, truth separation, settlement-window recovery, table contracts, dataset integrity, and Parquet round trips. All 56 tests passed locally with PyArrow 23.0.1.

The original engine tests check the fixture, cash conservation/idempotency, invalid financial inputs, deadline boundary ordering, late recovery, SSI/funding separation, observation leakage, currency conversion, scenario probabilities, LP hand solutions, capacity/budget constraints, solver fallback, immutable artifact integrity, repeatability, CLI flow, API pagination/errors, and decimal-string response amounts.

The local runtime is Python 3.14.7 with dependency versions pinned in `requirements-py314.lock`. Source compilation also succeeds under Python 3.11. A FastAPI/Starlette dependency warning currently recommends `httpx2` for test clients; the installed `httpx` compatibility path works and all API tests pass. No production reliance is placed on the test client.

CI configuration is supplied but has not been run remotely. Python 3.11 dependency integration and universal dependency locking remain unverified because package-index DNS is unavailable here.

The API was exercised through FastAPI's in-process test client. A live launch reached application startup successfully, but this execution sandbox denied binding to `127.0.0.1` (`Operation not permitted`). No server is left running. Start `make serve` from a regular local terminal to use the endpoints in a browser.
