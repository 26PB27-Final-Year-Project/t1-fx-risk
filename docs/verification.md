# Local verification report — 4 November 2026

The implemented local research workflow is operational. This records local verification, not a remote CI or deployment result.

## Changes verified

- Removed the duplicate UTC evaluation clock from the run selector. Evaluation controls retain the date, time, timezone and saved-run boundaries.
- Currency filtering now opens with all currencies, so historical USD fixtures and current EUR runs both display their trades.
- Model discovery filters predictions by settlement currency and calibration maturity. Incompatible requests return a structured model-unavailable response.
- Trained FX timing models are listed in the API and Model health. Funding jobs can select a timing model, scenario count and random seed.
- Exposure evaluation reports the actual settlement currency instead of labelling EUR as USD.
- Added `python -m t1fx.cli bootstrap` (`make demo`) to generate chronologically earlier training data, train risk and timing models, and publish the current month's five-policy EUR comparison.

## Verification results

| Check | Result |
| --- | --- |
| Complete backend suite | 87 passed |
| Chromium acceptance suite | 13 passed |
| TypeScript check and production build | Passed |
| Clean bootstrap with real Frankfurter rates | Passed |
| Stored runs, datasets and both model types | All saved checksums validated |
| Live risk predictions and Tree SHAP detail | Passed |
| Live timing-model worker job with EUR 15,000 budget and 50 scenarios | Passed |
| Matched comparison after that job | All five policies displayed |
| Risk and timing Model health views | Passed |
| Browser JavaScript errors during live workflow | None |
| Light/dark contrast, typography, mobile navigation, sidebar widths | Browser checks passed |
| Calendar range checks and 38 timezone choices | Browser and API checks passed |
| Application-code static checks for undefined/unused symbols | Passed |

The browser tests also cover job recovery after reload, API error recovery, stale detail responses, search, filter reset, keyboard dropdowns, app icon loading and evaluation updates without duplicate controls. Backend tests cover chronological partitions, observable snapshots, money units, cash conservation, LP capacity/budget limits, solver fallback, model calibration, SHAP additivity, scenario sampling and artifact integrity.

## Prepared artifacts

- Dataset: `dataset-c5b7f4f040879a932969`
- Risk model: `model-46596d3ec83dfea7738d`
- Empirical FX timing model: `timing-7c066e8116b9c6931980`
- Monthly demo: `demo-50-s42-eur-2026-11` — 50 trades over 21 business-day execution dates.
- Full comparison: five policies, EUR 30,000 funding limit, saved Frankfurter quotes and 200 arrival scenarios.
- Live verified recomputation: `run-c7e223e5e34893be295c` (EUR 15,000, 50 arrival scenarios).

### Risk-model test results

These are held-out synthetic-data measurements:

| Metric | XGBoost |
| --- | --- |
| Brier score | 0.064240 |
| ROC AUC | 0.824284 |
| PR AUC / average precision | 0.655262 |

Logistic regression and heuristic rule comparisons are saved in the model's `metrics.json`; detailed calibration bins, bootstrap intervals and stress reports remain available through Model health.

## Start and reproduce

```sh
uv sync --extra parquet --extra ml
npm ci --prefix frontend
npm run build --prefix frontend
uv run python -m t1fx.cli bootstrap
```

Then run these in separate terminals:

```sh
uv run python -m t1fx.cli serve
uv run python -m t1fx.cli worker
```

Open http://127.0.0.1:8000/. Bootstrap requires internet access for Frankfurter. Funding recomputation at the same evaluation time reuses saved rates.

## Practical limits

This is a working synthetic research system. Empirical timing uses the documented fallback distribution with assumed dependence; it is not a separately calibrated timing classifier. Synthetic performance does not establish accuracy on real settlement data. The LP minimizes a planning surrogate, so a lower LP objective does not guarantee lower replay cost. Future monthly trades remain hidden from observations until execution.

Remaining research and production extensions are listed in [implementation-status.md](implementation-status.md), including real market/custodian integration, securities inventory, rolling treasury decisions, shared-user authentication and the complete research experiment suite.

The successful build still reports dependency bundling/chunk-size warnings, and API tests report a FastAPI/Starlette test-client deprecation warning. These do not fail the recorded checks.

## Online-date calendar update

The calendar now takes UTC from HTTPS response dates (Google, with Cloudflare as a fallback), advances the synchronized sample with a monotonic clock, and refreshes it every minute. A short cached synchronization can bridge transient outages; after five minutes without synchronization, evaluation is blocked until online time is available again. The API rejects future evaluation instants, and the calendar disables future dates in the selected timezone while preserving saved-run boundaries. Deadline countdowns display hours/days and explicitly label overdue intervals.

Verification for this update: 16 targeted clock/API/job checks passed, 14 Chromium checks passed, the production build passed, and the live HTTPS clock fetch succeeded.
