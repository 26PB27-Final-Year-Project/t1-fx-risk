# Guided local experiment workflow

Install once with `uv sync --frozen --extra ml --extra parquet`, then `npm ci --prefix frontend` and `npm run build --prefix frontend`. On macOS, XGBoost needs the OpenMP runtime (`brew install libomp`). The locked `tzdata` package supplies IANA zones on Windows. Node is needed to build the dashboard, not to serve it.

Start from the repository root on Windows, macOS or Linux:

```sh
uv run --no-sync python -m t1fx.cli start
```

Open http://127.0.0.1:8000/. This command checks ML dependencies, timezone data, the built dashboard and port availability, then supervises API and worker using the active Python interpreter. It installs nothing and generates nothing. Ctrl+C shuts down both services; if one exits, the supervisor stops its sibling. `--artifacts PATH` before `start` selects a shared workspace; `start --port 8001` changes the API port. Standalone `serve` and `worker` remain available. Avoid running extra workers unnecessarily: the queue permits one active expensive job per workspace.

## Walkthrough

Use the persistent stage links in Experiments to jump to Dataset, Models, Compare funding, or Review and export. Desktop sections place context beside controls; mobile sections stack and offer direct bottom navigation to all five workspace pages. Preparation status appears above the forms. Optional dataset and timing assumptions expand separately; collapsing them preserves their configured values.

1. Open **Experiments**. On an empty workspace it opens automatically. Quick preparation calculates recent historical defaults from the current online UTC date; Research exposes explicit saved settings. Choose an open first trading date, counts, seed, cash fraction and operational assumptions. These are synthetic frequencies, not measured bank statistics. The default 1,000 trades gives more stable training cohorts than very small samples.
2. Choose **Create dataset** to generate from the displayed settings, even if a saved dataset is currently selected. Unchanged settings may resolve to the same immutable release; changed settings produce a different release. **Prepare complete experiment** uses the selected dataset (or generates one if none is selected), trains both models, evaluates the selected held-out day (or the first if none is selected), compares policies and saves an exportable record. Dates must leave enough time for chronological partitions and recovery gaps before today. Generation uses dated FX reference prices; it never falls back to invented fixed prices.
3. Select an existing dataset by its readable date/count/seed name. Inspect partition dates, outcome balance and held-out day count. Train the settlement-risk and timing models separately or together. All preprocessing, history and candidate fitting remain training-only; tuning chooses hyperparameters, calibration adjusts probabilities and test results are frozen evidence.
4. Choose a **Held-out scenario**, then compatible saved model versions. Disabled choices have explanations below the controls. Eligibility and execution share integrity, runtime, currency, maturity and FX-route checks. The risk-model loader checks feature schema and supported XGBoost major version. Saved model metrics, tuning candidates, selected hyperparameters, stress/holdout reports and provenance are expandable; test results never select hyperparameters automatically.
5. Set the **USD budget**, timing scenario count, seed and common-shock weight. This weight is an assumed mixture of shared and independent receipt quantiles. **Compare funding policies** replays matched policies. **Compare budgets** accepts up to six USD amounts and holds scenario, decision time, reference rates, timing model and arrival draws fixed across budgets.
6. Review the realized-cost chart and exact results table. Open Trades, Funding, Scenarios or Model health directly. The Funding view contains pool reserves, expected shortfall, budget use, planning objective, solver status and fallback reason; Scenarios contains realized failures and replay costs. Identical result values alone do not establish a cause—inspect recorded trade outcomes.
7. Download the experiment JSON for resolved configuration, dataset manifest, model evaluations, immutable artifact references, code/dependency provenance, assumptions and limitations. CSV contains each policy's funding budget and realized metrics, including sensitivity runs. The JSON links saved FX rates through its reference run. Readable artifact aliases are displayed; canonical IDs remain in details.

## Finding your way around

The default views focus on the next action. Expand **How to read this page** for terminology and interpretation. Trades owns the evaluation date and timezone controls; Funding, Scenarios and Model health use the selected saved run without repeating those controls.

Funding places the budget form before the saved plan. Saved reference rates are expandable. Each results page ends with a next-step action, and **New experiment** returns to preparation. Opening Funding from an experiment selects its scenario-optimizer run. Experiment result tables use readable policy names and amounts rounded to cents; JSON/CSV retain original precision. Full model reports, assumptions and identifiers remain available in expandable details.

## Jobs, recovery and cancellation

The API exposes worker availability at `/api/v1/workflow/health`; worker heartbeats renew every three seconds and expire after fifteen seconds. Queued jobs remain durable if a worker is unavailable. A separate lease heartbeat protects active jobs during long training operations.

Typed requests enter the existing SQLite queue. Successful stage references are checkpointed. A crash leaves a renewable lease; an expired lease can be recovered up to three attempts. Expired owners cannot write new checkpoints. **Retry preparation** atomically queues a new job with the successful prior checkpoints already attached, validates immutable outputs and resumes missing work. Retry idempotency keys are scoped to their source job. Draft selections, job references, selected result and consumed-result markers persist in browser storage across refreshes. If an artifact workspace was removed, an unavailable saved job is cleared from the browser so new preparation remains possible. Another browser can inspect immutable experiment records but does not inherit the first browser's draft.

Cancellation of a queued job is immediate. An active job becomes **CANCEL_REQUESTED** and stops between stages. A solver, API request or training call already in progress is allowed to return; its successfully completed output is checkpointed before stopping. Both Experiments and Funding keep tracking cancellation through refreshes until a final status is recorded. The UI does not claim it stopped immediately. If a worker disappears before confirming cancellation, lease recovery marks it failed with an unconfirmed-cancellation explanation.

The five sidebar destinations have distinct icons: flask (Experiments), opposing arrows (Trades), wallet (Funding), chart (Scenarios), and pulse (Model health). Model eligibility is checked for the selected scenario; Funding disables incompatible timing choices and explains why. Comparison actions remain disabled while eligibility for changed settings is pending or unavailable. Switching saved experiments clears the previous results until the selected record loads, keeping the displayed evidence and export links consistent.

Stage names describe actual calls. Budget comparisons expose completed/total budgets. Training has no invented percentage or estimated completion time. Small datasets can legitimately lack enough outcome classes to train; inspect the error and generate a larger or differently seeded cohort.

## Constraints

This is a single-operator local research application. The guided workflow currently supports USD settlement and the configured EUR/CAD/GBP/JPY/INR funding routes. US/EUR sourced calendar snapshots cover 2026–2028; remaining currency profiles still have narrower assumed coverage. Requests outside any required calendar coverage fail. Quick mode resolves and saves current-date-based configuration explicitly; retain immutable outputs for reproduction.

FX/time connectivity checks are explicit and can take several seconds per provider. Provider availability is required for uncached generation prices and the first valuation snapshot. Tests freeze provider responses but exercise real generation, training, accounting and optimization. Synthetic performance is not evidence of real-market accuracy. Independent test days, not forecast draws, determine the size of the independent evaluation cohort.

Experiment exports include descriptive policy metrics; they do not add statistical confidence intervals or an automatic hypothesis test. Worker progress is stage-level, not per boosting round. User authentication, public deployment and distributed compute are outside this local workflow.

## Verification record — 9 November 2026

- Full backend regression: 114 passed. The workflow integration tests use real settlement-risk and empirical timing training with frozen provider responses. They cover typed requests, heartbeat expiry, exclusive claims, cancellation checkpoints, expired-owner fencing, atomic retry publication and idempotency, execution-time model validation, chronology, sensitivity input consistency and JSON/CSV exports.
- Browser acceptance: the complete 23-test suite passed, including a page-by-page walkthrough in both themes. Coverage includes guided dataset creation and real training, creating a different dataset while one is selected, cancellation through refresh, missing-job recovery, stale result responses, eligibility outages, incompatible timing choices, navigation and exports. Distinct navigation icons were checked in expanded, collapsed and mobile sidebars; light/dark screenshots were inspected.
- TypeScript checking and Vite production build pass. The existing large-bundle warning remains.
- The mechanical frontend design detector reported no findings; no ignores were added. New experiment controls use the existing design tokens and components.
- A real local supervisor smoke check started API and worker, observed worker availability, terminated the supervisor and confirmed the API port closed. Launcher unit checks cover sibling cleanup and port conflicts.
- Linux, Windows and macOS backend CI now use the committed uv lock. This session ran on macOS; remote CI and execution on Windows/Linux were not run here. Ordinary tests avoid live FX/time services; explicit connectivity diagnostics are available in the UI.

New experiment dates come from `/api/v1/workflow/defaults`, using the online UTC day and the configured market calendar. The suggested start leaves time for all partitions and recovery gaps to mature. It is intentionally earlier than today. Explicit research dates, immutable outputs, versioned calendar snapshots and deterministic test fixtures are preserved. Calendar coverage limits still apply. Header actions share a responsive alignment group; form controls use a minimum 44-pixel height and action groups stack on mobile.

## Schedule and assumptions preview

Before generating, expand **Preview dates, deadlines and assumptions**. The preview calls the same calendar schedule used by generation, without requesting FX prices or creating artifacts. Every row shows its partition, execution date, trade count, evaluation time, contractual settlement deadline and label-maturity time in UTC. The market timezone is displayed separately. T+1 is the next eligible settlement business day, not 24 elapsed hours. Recovery gaps mean ten trading days can span several calendar weeks. Historical windows must finish before the current UTC date.

Generation frequencies are configurable synthetic probabilities, not guaranteed realized failure percentages. The resolved configuration exposes all additional assumptions, including receipt delays, route cutoffs, calendar profiles and costs. Saved-dataset preparation reuses its original configuration; editing the generation fields only affects **Create dataset** or preparation with no saved dataset selected.

The simulator replays stored events. The risk model estimates failure under zero extra reserve. The empirical timing model estimates whether receipts are observable after a target time; its test observation count and Brier score are shown separately. The optimizer uses cash requirements and timing scenarios to recommend reserves. Selected evaluation, deadline and replay-end timestamps are displayed before comparison. Planning objective and realized replay costs remain separate. Training can legitimately fail when partitions lack both outcome classes; use a larger cohort or a different seed and inspect the recorded error.

## Loading and validation

The Trades page initially shows deadline order so stored trade rows can load while forecasts are calculated. Explicit probability sorting returns the same forecast used to order its page, avoiding a second inference request. Evaluation controls and the schedule share an in-flight context request. Observable FX legs are indexed once per request by trade and cash pool.

Catalog and compatibility reads can reuse successful dataset semantic checks in a bounded process-local memo. Every call still verifies manifest identity and hashes every listed file; the memo key also includes the complete manifest and scenario filename set. Changed files invalidate reuse or fail checksums. First reads and normal standalone validation perform full table/Parquet/chronology checks. No timestamp-only validation cache is used. Process restart clears the memo, so the first catalog read can take longer than later reads.
