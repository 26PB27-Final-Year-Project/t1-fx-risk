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

## Risk-model milestone

- One versioned feature function serves training and inference: USD notional, observable cash coverage, lifecycle state, time to cutoffs, and dated counterparty failure history. Feature names, units, and categorical mappings are persisted.
- Logistic regression and XGBoost compare unweighted and class-balanced fitting using trade-normalized snapshot weights. A fixed candidate grid is selected by tuning log loss.
- Monotonic sigmoid calibration uses only the separate calibration cohort. Models, preprocessing, calibration, and thresholds are frozen before final evaluation.
- Counterparty holdouts are excluded from all fitting and historical features. A predeclared stress family is evaluated after freezing.
- Reports include PR-AUC (average precision), ROC-AUC, Brier score, log loss, reliability bins, capacity-based precision/recall, missed exposure, decision horizons, and scenario-day bootstrap intervals.
- Native XGBoost Tree SHAP contributions explain raw log-odds; calibrated probabilities remain separate. Additivity is checked in evaluation and inference.
- Atomic JSON model bundles include checksums and a frozen fitted-artifact hash. Inference checks feature compatibility, XGBoost major version, funding policy, and calibration maturity. Missing/incompatible models return `MODEL_UNAVAILABLE`.
- CLI training, validation, and scoring; API model listing, health reports, and run-scoped reference-policy predictions. See [model usage](models.md).

## Scenario-funding milestone (Gate F, empirical timing fallback)

- Immutable empirical FX timing bundles fit only training-partition observable contracts and receipts, with explicit label maturity and conservative unresolved-receipt mass.
- Conditional pending-receipt forecasts use sell/buy routes; coherent joint draws share one arrival per leg across deadlines. Common quantile shocks have a declared stress weight.
- Scenario requirements measure peak cumulative deficits, count shared receipts once, and preserve receipt-before-deadline ordering at equal timestamps.
- Five matched policies compare fixed zero/half/full reserves, deterministic LP, and scenario LP using the existing realised cost accounting.
- Persisted seeds, arrivals, model identity, exact weights, requirements, sampling diagnostics, chronological test Brier/reliability reports, CLI training/replay, and read-only API inspection.
- This implements the architecture's empirical fallback. A separately calibrated timing classifier and fitted dependence remain extensions. See [funding usage](funding.md).

## Application milestone (Gate G)

- Local React/TypeScript dashboard with trade inspection, funding, matched scenarios, and model health; explicit loading, empty, model-unavailable and request-error states.
- Currency/state/score filters, model probability sorting, observed FX tracks, SHAP factors, separate decision/outcome clocks, and decimal-string amounts.
- SQLite job registry with transactional claims, normalized idempotency keys, renewable leases, bounded restart recovery, owner fencing, cancellation and durable result/error references.
- Separate CLI worker runs funding comparisons and validates immutable artifacts before completing a job. Same-origin mutation checks and loopback host restrictions protect the local API.
- Comparison identities isolate budgets and forecast settings; browser job references survive reloads and stale responses are ignored.
- Frontend build and Playwright acceptance workflow. See [application usage](application.md).

## Current simplifications

1. The demo uses three purchases, USD settlement, three segregated cash pools, and an externally funded EUR/USD receipt. The standalone LP supports multiple currencies; the comparison pipeline deliberately rejects non-USD portfolios until valuation rates are supplied explicitly.
2. The fixture's market profile is synthetic. Its calendar utility is tested independently; the fixture timestamps are explicit inputs, not generated from an official calendar feed. The joint-calendar FX utility is a simplified configurable convention, not a universal spot-date rule.
3. All securities are assumed available. There is no securities inventory, contractual netting, partial settlement, or real custodian integration.
4. FX source-currency commitments are capacity-checked. Full source-currency ledger movements and treasury-wide FX optimization are not implemented.
5. Reserves in replay are drawn in full at the decision time and held until the recovery-window end. Interest accrues on outstanding drawn principal; repayment uses only actual remaining cash. Unrepaid amounts are reported, but there is not yet a rolling next-window engine. Idle undrawn facility accounting is deferred.
6. The requirement builder uses contractual deadline times and observable expected FX credits. It is conservative about overdue/unobserved receipts. The default demo supplies one scenario. Timing-enabled replays use training-only empirical receipt distributions with assumed common-quantile dependence; a separately calibrated timing classifier is not implemented.
7. The LP objective is an assumed planning surrogate. Realised bridge/late/route costs are independently computed in replay and are not interchangeable with that objective.
8. Costs use the configured annual/daily rates and elapsed fractional days. The fallback fee is in each trade currency's minor units. These are assumptions, not jurisdictional penalty schedules.
9. Snapshot reconstruction currently covers immediate reservation consumption in this simulator. Persistent external reservations require additional observable records. Model history uses only dated training-cohort deadline outcomes; historical delay quantiles are not implemented.
10. Simulation runs use JSON. Dataset releases support JSON and optional Parquet; auto mode reports JSON-only output explicitly when PyArrow is unavailable. PyArrow 23.0.1 passed local round-trip, export, and 1,000-trade release validation. CI installs the extra and exercises both formats.
11. The API, React dashboard and durable worker run locally. Shared-host authentication, distributed jobs and a live replay clock remain unimplemented. Cancellation fences job state but does not preempt a running numerical solver.
12. Rule scores remain hand-written heuristics. Risk models are calibrated on synthetic data; the 1,000-trade smoke release has only two final test scenario-days. Its results do not establish real-world calibration. Tuning uses a fixed grid, not Optuna. A richer FX timing classifier and the full E1–E6 study remain future work.

## Validation recorded during development

The test suite also checks seeded generation, temporal partition boundaries, truth separation, settlement-window recovery, table contracts, dataset integrity, and Parquet round trips. The dataset milestone passed 56 tests locally with PyArrow 23.0.1. Risk-model checks additionally cover dated history, held-out fitting boundaries, calibration, SHAP additivity, reproducibility, incompatible bundles, API inference, and CLI loading.

Before Gate F, the complete suite passed 67 tests locally, including model training, repeated-bundle identity, and API/replay prediction parity. Gate F passed 73 tests locally. Gate F adds six tests covering survival conditioning, maturity, shared shocks, empirical marginals, exact scenario weights, shared receipts, deadline equality, artifact integrity, CLI training/replay, API inspection, reproducibility, and a matched case where uncertainty changes reserves and realised failures.

The original engine tests check the fixture, cash conservation/idempotency, invalid financial inputs, deadline boundary ordering, late recovery, SSI/funding separation, observation leakage, currency conversion, scenario probabilities, LP hand solutions, capacity/budget constraints, solver fallback, immutable artifact integrity, repeatability, CLI flow, API pagination/errors, and decimal-string response amounts.

Gate F used Python 3.14.7 with dependency versions recorded in `requirements-py314.lock`. Gate G passed all 79 backend tests on Python 3.11.16, the TypeScript/Vite production build, and four Chromium Playwright checks. These cover the complete recompute flow, browser reload recovery, API error recovery, mobile navigation and stale trade-detail responses. A FastAPI/Starlette dependency warning currently recommends `httpx2` for test clients; the installed `httpx` compatibility path works and all API tests pass. No production reliance is placed on the test client.

CI configuration includes Python engine checks and the dashboard build/browser flow, but has not been run remotely. Frontend dependencies are pinned by `frontend/package-lock.json`; universal Python dependency locking remains future work.

The API is covered by FastAPI test-client checks. The application milestone also uses a temporary live loopback server and Chromium for browser acceptance; restricted sandboxes require permission to bind and launch the browser. Start `make serve` and `make worker` to use the dashboard locally.
