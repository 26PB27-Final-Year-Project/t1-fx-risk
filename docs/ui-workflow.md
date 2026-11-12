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

After dataset generation, the interface refreshes datasets without reloading every model bundle, retains the loaded model catalog, and selects the generated dataset for training. A loading status remains visible until the catalog refresh finishes. Initial visits and completed training still load the complete catalog. Dataset integrity validation remains mandatory.

Page guides explain the controls and the interpretation of each page's results. Model health remains available for frozen test metrics, reliability and provenance. The experiment intro link was removed; the four stage links provide navigation.

The Trades page initially shows deadline order so stored trade rows can load while forecasts are calculated. Explicit probability sorting returns the same forecast used to order its page, avoiding a second inference request. Evaluation controls and the schedule share an in-flight context request. Observable FX legs are indexed once per request by trade and cash pool.

Catalog and compatibility reads can reuse successful dataset semantic checks in a bounded process-local memo. Every call still verifies manifest identity and hashes every listed file; the memo key also includes the complete manifest and scenario filename set. Changed files invalidate reuse or fail checksums. First reads and normal standalone validation perform full table/Parquet/chronology checks. No timestamp-only validation cache is used. Process restart clears the memo, so the first catalog read can take longer than later reads.

Dataset catalog summaries and parsed held-out scenarios now use a bounded 32-entry process-local cache after mandatory byte verification. Returned values are copied, so downstream operations cannot change shared cached objects. Concurrent reads serialize cold validation to avoid duplicate work. Catalog refresh no longer reloads models after comparison-only jobs, and restoring an already-applied completed job does not trigger a second catalog refresh. Eligibility requests wait 300 ms after edits; execution still revalidates independently.

Cold validation computes each canonical table hash once. The Parquet adapter converts schema-verified UTC microseconds directly instead of repeating timezone resolution for every scalar, preserving nulls and exact microseconds. A local measurement over four existing datasets (approximately 193 MB) reduced cold catalog validation from 42.2 to 10.7 seconds; repeat reads took 0.11 seconds. These are local measurements, not latency guarantees. Restarting the API still requires full validation; training and provider requests have separate costs.

Repeated workspace labels and section introductions were removed. Assumptions, cancellation semantics, provenance and interpretation remain in expandable details. Single-budget experiment comparisons use policy bars; multiple-budget comparisons use lines. Scenarios shows separate cost and missed-deadline charts, with the exact table retained to avoid conflating money and counts.

Model health shows the relevant held-out score columns and a reliability line chart (predicted probability versus observed frequency, with a perfect-agreement diagonal). Empty bins are omitted from the expandable calibration table; a single populated bin is shown as one point, not an invented curve. Single-outcome evaluation is explicitly flagged. Timing reports omit ROC/PR-AUC columns because the timing evaluator does not publish those metrics. Full frozen reports and provenance remain available under one disclosure; no saved metrics are rewritten.

Generation distributes the requested total evenly across the selected trading days: each day receives the integer quotient, with one extra trade on each of the first remainder days. Thus 10,000 trades over 30 trading days means 334 on the first 10 days and 333 on the remaining 20. Partition recovery gaps do not create extra trades.

## Interface audit — 12 November 2026

The five pages were reviewed for navigation, control states, validation, loading/error recovery, keyboard access, themes and responsive layouts. Fixed issues:

- Missing saved Funding jobs now clear their persisted references and unlock recomputation instead of polling forever with a disabled submit button.
- Evaluation jobs retain their references across page refreshes. Temporary polling errors retry; missing jobs clear safely; completed jobs without result references produce an actionable error.
- Run-list errors remain visible on Experiments with a retry action. Run refreshes reject stale responses and disable repeat clicks while loading.
- Calendar popovers reopen on the selected date rather than the previously browsed month.
- Trade schedule failures are reported instead of silently displaying an empty schedule.
- Reset filters returns to the default deadline ordering. Late responses from obsolete model-eligibility requests cannot replace current errors.

Acceptance coverage includes generation, real training, eligibility, funding and evaluation jobs, cancellation, refresh recovery, exports, dropdown keyboard operation, light/dark contrast and layouts from 320px to desktop. Chromium automation and rendered screenshots were used; physical devices and Safari/Firefox were not exercised. The production build retains Vite's existing large JavaScript chunk warning.

Experiment model pickers list only models that pass backend compatibility checks for the selected held-out day. They remain disabled while checks are pending. If a saved selection fails those checks, it is cleared with a notice; no replacement model is silently selected. Rejected-model explanations are grouped under **Why some models are hidden**. Users can select a compatible earlier-trained model, choose an eligible later test day, or train models on the selected dataset. Model-free funding comparison remains available with expected receipt times. Backend execution still independently enforces calibration/training-label maturity, integrity and route coverage.

## Generation and time corrections — 12 November 2026

Generation preflight now follows the actual whole-day partition schedule, including settlement holidays and recovery windows, instead of rejecting dates against an overestimated horizon. It validates execution/settlement windows, route calendar coverage and normal/missed-cutoff booking boundaries before requesting FX data. Preview and generation use matching deadlines and maturity instants. API preview/submission and worker generation checks use the online UTC date; omitted start dates are resolved against that same clock provider. Explicit dates remain subject to validation.

Settlement cutoffs are unavailable on payment holidays even when securities trading is open. Trading execution is not constrained by that day's payment-system cutoff. Local-clock conversion rejects already timezone-aware clock values; ambiguous/nonexistent DST times continue to be rejected. Missed-cutoff generation uses the FX route's local booking date and cannot move a booking earlier than its normal booking time. Historical reference selection uses UTC rather than the computer's local date. FX sell amounts respect the settlement currency's minor-unit exponent, including JPY.

Route assignment continues across generation days so small daily cohorts do not repeatedly use only the first currencies. A day with fewer trades than routes still cannot contain every route; coverage is across the dataset. Existing immutable datasets are not rewritten. Generate a new dataset to use the corrected generation behavior.

The evaluation picker rounds its lower bound up to the first valid whole minute and respects the exclusive end instant, including subsecond ends. The backend remains authoritative for timezone/DST validity and evaluation-time bounds.

FX route booking rollovers and joint value dates also respect separately configured payment holidays on both currency calendars. A day can be open for trading while closed for FX payment scheduling. Existing immutable datasets retain their original schedules; generate a new release to apply this correction.

## Dataset loading and reuse

Catalog browsing verifies the manifest identity, every listed file checksum, configuration/schema identities and report identity, then reads display metadata and held-out scenarios. It does not grant training eligibility or populate the full-validation cache. Eligibility, submission and execution retain full semantic validation; training and timing reuse successful semantic checks only after all file bytes are verified again. No timestamp-only trust or persistent validation bypass is used.

The Experiments model pickers use a bounded typed `POST /api/v1/workflow/model-choices` request instead of one request per model. Each batch verifies the dataset once and replays the decision-time snapshot once for its risk models, while checking each model independently with the same compatibility logic used during execution. The selected comparison is separately validated before submission and again by the worker.

Frontend recovery: failed catalog reads offer **Retry loading datasets** without discarding the draft. Failed or incomplete model-choice responses offer **Retry model checks** and keep comparison disabled. Large model lists are checked sequentially in batches of at most 100 risk and 100 timing models, with actual completed counts. Switching the dataset or test day aborts obsolete browser requests and ignores stale responses; this does not claim that server computation has stopped. A failed post-job catalog refresh is retried before the job outputs are marked applied.

## Workflow date and validation consistency

Request parsing does not derive dates from the host clock. Generation resolves an omitted start date only when needed, using the online UTC clock in both API validation and worker execution. The worker resolves its generation configuration once per attempt. Online clock failure is reported instead of silently falling back to the system date. Historical FX requests use the booking day’s preceding UTC date without clamping it to the host date.

Training/comparison requests for existing datasets do not calculate generation defaults. Retry checkpoints with completed datasets validate and reuse those artifacts without needing a new generation window. Full semantic dataset checks are reused throughout a worker attempt, including final experiment publication, while every validation still verifies current file checksums.

The dependency lock targets Python 3.11 exclusively. CI uses `uv sync --locked` to reject future configuration/lock mismatches.
