# Local application

Gate G provides a React/TypeScript dashboard, run-scoped API, and durable SQLite funding jobs. The application is intended for one local research operator and synthetic data.

## Start

Install the Python environment as described in the README. The dashboard also requires Node.js 22.12+ (or a compatible newer release) and npm.

```sh
# Build the frontend and generate the reference comparison.
npm ci --prefix frontend
npm run build --prefix frontend
uv run python -m t1fx.cli demo

# Terminal 1: API and dashboard.
uv run python -m t1fx.cli serve

# Terminal 2: worker, using the same artifact directory.
uv run python -m t1fx.cli worker
```

Open <http://127.0.0.1:8000/>. `make dashboard`, `make demo`, `make serve`, and `make worker` are equivalent shortcuts. The API docs remain at `/docs`. A missing frontend build returns an actionable 503 at `/`; API routes remain available.

For a different artifact location, pass the global option before each subcommand, for example `t1fx --artifacts /path/to/artifacts serve` and `t1fx --artifacts /path/to/artifacts worker`. `worker --once` processes at most one available job and exits. `T1FX_DASHBOARD_DIR` can select an alternate built dashboard directory.

For frontend development, keep the API running and use `npm run dev --prefix frontend`. Vite proxies API requests to port 8000. Build output goes to `src/t1fx/dashboard/`, is ignored by Git, and is included in Python wheels when present at packaging time. Run the frontend build before packaging a wheel. Source installs require the separate frontend build.

## Dashboard

- **Trades:** select a stored run, filter by currency/instruction/minimum rule score, page through trades, and sort by rule score or exposure. Select a compatible trained risk model to show calibrated reference-policy probabilities and enable probability sorting. A model compatibility error remains visible. Trade detail shows independent instruction, SSI, cash and observable FX states, rule factors/actions, optional Tree SHAP factors, and separately dated replay outcomes.
- **Funding:** inspect observed cash, incremental reserves, expected shortfall, remaining budget, planning objective and fallback status. Submit a USD liquidity limit to recompute a matched comparison. An explicit submit button avoids sending jobs while editing. The queued/running/terminal state is polled; successful results open as a new immutable run. The job reference survives page reloads in local storage. The selected run and screen are encoded in the URL.
- **Scenarios:** compare failures, failed notional, bridge/late/route costs, total cost, and budget use for exactly one comparison identity. Inputs, code/runtime, funding plans and timing scenarios participate in that identity. Historical runs without an identity appear alone rather than being guessed into a comparison.
- **Model health:** inspect risk baselines or the selected run's timing model, with Brier/AUC results, reliability counts/frequencies, frozen evaluation details and provenance. The model-free demo has an explicit empty state.

All views show the simulated decision clock and reference policy. Outcomes carry the later replay result time. Dollar amounts remain decimal strings through the API and display. The dashboard makes no payments or FX bookings.

## Appearance and navigation

The dashboard uses shadcn/ui buttons, inputs, native selects, labels, cards, badges, tables, and alerts. Shared semantic color tokens define both themes. Body text, table cells, labels and controls use 14px text; supporting text uses 12px; headings and key figures use a separate consistent scale.

Use the sun, moon, and monitor buttons in the top bar for light, dark, or system appearance. The preference is remembered locally and applied before the page renders. System mode follows changes to your operating-system theme.

The watchlist includes run summary cards, status badges, rule-score meters, and a search field for the current page. Reset filters restores the default watchlist. The Scenarios view includes a cost comparison chart alongside the detailed table. Detailed model reports are collapsed by default. Currency displays use grouping and rounding to the currency's minor unit; stored values remain unchanged.

## Job API

```sh
curl -X POST http://127.0.0.1:8000/api/v1/runs/RUN_ID/funding/recompute \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: my-comparison-001' \
  -d '{"liquidity_limit":"15000.00","scenario_seed":42,"scenario_count":200,"common_shock_weight":0.5}'
```

The response is HTTP 202 with the job ID and durable state. Poll `GET /api/v1/jobs/JOB_ID`; cancel using `POST /api/v1/jobs/JOB_ID/cancel`. Reusing a key and the same normalized request returns the original job. A different request with that key returns 409. A new key submits a new attempt after a failed or canceled job.

The budget is a nonnegative decimal string with at most two decimal places and twelve integer digits. Scenario counts are 1–500, seeds are unsigned 32-bit integers, and common-shock weights are 0–1. Unrecognized request fields are rejected. The source run fixes the exogenous scenario, decision time, cost assumptions and optional timing model; requests cannot choose paths or execute code. Timing options default to 42/200/0.5 and apply when the source run has a timing model.

Other application endpoints are `GET /runs/RUN_ID/scenarios` and `GET /runs/RUN_ID/funding-plans/PLAN_ID`, under `/api/v1`. Trade queries support `currency`, `state`, `min_score`, and `sort=risk|amount|probability`; probability sort requires `model_id`.

## Durability and limits

`artifacts/jobs.sqlite3` uses WAL and transactional claims. A worker owns a renewable 60-second lease with a heartbeat every 20 seconds. On interruption, a subsequent worker can reclaim the job after lease expiry. Three expired attempts produce a visible failure. Result publication uses the existing immutable, checksummed run store, and each result is validated before marking the job successful. Owner and lease checks prevent stale workers from overwriting current job state.

Cancellation immediately fences the job's result and prevents queued work from being claimed. It does not forcibly interrupt a numerical solver already running; that worker may finish publishing valid immutable runs, but cannot change the canceled job to succeeded. A crash after publication but before job completion can likewise leave valid runs; deterministic retries reuse their identities. The worker must be launched separately; API restarts do not run heavy jobs.

The server binds to loopback, restricts Host headers, and rejects cross-origin mutation requests. There is no shared-host authentication or multi-user authorization. A distributed queue, retention/cleanup policies, live replay clock, and public deployment remain outside this local milestone.

## Checks

```sh
uv run pytest
npm run build --prefix frontend
# One-time browser installation:
cd frontend
npx playwright install chromium
npm run test:e2e
```

Playwright starts an isolated temporary artifact directory, a local API on port 8765, and its worker, then tears them down. It checks trade inspection, funding submission, reload recovery, opening results, matched comparisons, model empty states, API error recovery mobile navigation, theme persistence/system preference, text contrast and component typography across all four views in both themes, and trade search/reset. Backend tests additionally cover concurrent claims, idempotency conflicts, lease recovery, stale owners, cancellation, exhausted retries, persisted failure, validation and origin restrictions. Set `T1FX_PYTHON` to the desired Python executable if `.venv/bin/python` is unavailable (for example on Windows).
