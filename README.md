# T+1 Settlement and FX Funding Risk Engine

A local research application that explains why securities trades miss payment deadlines and compares ways to allocate extra cash. Generate synthetic trades, train models, compare funding policies, and export results through a React dashboard.

**Research simulation only:** the app does not trade or move money. Trades, operational delays and costs are synthetic. FX reference prices come from Frankfurter; they are daily reference values, not live executable quotes.

## Setup and start

Requirements: Python 3.11–3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 22.12+ and npm. On macOS, XGBoost also needs `brew install libomp`.

```sh
git clone https://github.com/26PB27-Final-Year-Project/t1-fx-risk.git
cd t1-fx-risk
uv sync --frozen --extra ml --extra parquet
npm ci --prefix frontend
npm run build --prefix frontend
```

Then start the API and background worker together:

```sh
uv run --no-sync python -m t1fx.cli start
```

Open **http://127.0.0.1:8000/**. Press **Ctrl+C** to stop both services. The launcher checks dependencies, dashboard build and port availability; it never installs packages or creates data automatically. After frontend source changes, run the build command again and refresh your browser.

For a different port, append `--port 8001`. For a separate workspace:

```sh
uv run --no-sync python -m t1fx.cli --artifacts ./research-artifacts start
```

## Your first experiment

1. Open **Experiments**. New experiments calculate a recent historical training window from the current online UTC date. Choose Quick preparation for these defaults, or set explicit research dates, trade count and seed. Use **Use recent training dates** to refresh a saved draft. Optional dataset assumptions expand separately.
2. Click **Prepare complete experiment** to generate data, train both models, compare funding policies and save results. If a dataset is selected, preparation uses it. **Create dataset** always uses the displayed generation settings.
3. Watch **Preparation status** near the top. Jobs survive refreshes; failed stages can be retried using completed outputs. Worker availability appears in the top bar.
4. For manual experiments, train models separately, choose a held-out scenario and compatible models, then set a **USD funding budget**. Expand timing assumptions when needed. Compare one budget or up to six budgets with identical inputs and timing draws.
5. Open the resulting views, then download **experiment JSON** and **results CSV** from Review and export. Stage links at the top let you jump between sections.

| Page | Purpose |
| --- | --- |
| Experiments | Create datasets, train models, compare budgets and export |
| Trades | Inspect observable trade risk, FX conversions and lifecycle events |
| Funding | Inspect cash reserves, solver status and replay costs |
| Scenarios | Compare policies within the same experiment |
| Model health | Inspect frozen model metrics, calibration and provenance |

Each results page offers a next-step action. Help text, reference rates and technical evidence expand on demand. Funding opens with the budget form; **New experiment** returns to preparation. Displayed experiment amounts are rounded to cents; exports retain full precision.

The evaluation calendar on Trades changes the observation time within a saved simulation. It supports 38 named timezones and requires online time synchronization. It does not run a live market simulation.

## What the results mean

- **Risk prediction:** estimated settlement failure probability under zero extra reserve. It does not directly drive the funding LP or represent post-funding risk.
- **Timing forecast:** estimated chance that an FX receipt arrives after a deadline. Shared receipt delays are an explicit assumption.
- **Funding plan:** recommended reserves under the selected budget. Its planning objective is distinct from actual replay cost.
- **Replay outcome:** simulated settlement failures and costs after applying each policy. Identical outcomes are possible; inspect recorded evidence before attributing a cause.

Training uses chronological training, tuning, calibration and test partitions, with gaps for outcome maturity. Preprocessing uses training data only. Test metrics never select hyperparameters. SHAP explains raw model log-odds, not causal effects or calibrated probability changes.

## Data and reproducibility

The guided workflow settles in USD, with EUR, CAD, GBP, JPY and INR funding routes. Generation uses dated FX references without a fixed-price fallback. US/EUR calendars have sourced 2026–2028 snapshots; other payment calendars and route cutoffs include documented assumptions and narrower coverage. Unsupported dates fail validation.

Everything is stored in the chosen artifact directory, defaulting to `artifacts/`:

| Directory/file | Contents |
| --- | --- |
| `datasets/` | JSON tables, optional Parquet, configurations and scenarios |
| `models/` | Risk models, preprocessing, calibration and evaluation |
| `timing_models/` | Timing distributions and frozen metrics |
| `runs/` | Funding plans, saved rates, events, outcomes and metrics |
| `experiments/` | Immutable experiment records linking datasets, models and runs |
| `jobs.sqlite3` | Durable job states, ownership and checkpoints |

Timestamps are timezone-aware UTC. The engine uses integer minor currency units; API monetary fields use decimal strings where defined. Checksums protect immutable artifacts—do not edit generated bundles. JSON exports include resolved settings, seeds, model evaluation, provenance and artifact references; CSV exports contain policy metrics, including budget sensitivity when present.

Internet access is needed for uncached FX prices and online time. Use **Check provider connectivity** in Experiments to diagnose provider failures. Synthetic accuracy is not evidence of real-world performance; repeated timing draws are not independent test days.

## Development and checks

```sh
uv run --no-sync python -m pytest
npm run build --prefix frontend
npm exec --prefix frontend -- playwright install chromium
npm run test:e2e --prefix frontend
```

Tests use frozen provider responses while exercising real generation, training and replay. Browser tests start an isolated local server. Build the dashboard before running them.

Standalone services remain available in separate terminals:

```sh
uv run --no-sync python -m t1fx.cli serve
uv run --no-sync python -m t1fx.cli worker
```

API schemas: **http://127.0.0.1:8000/docs**. Worker health: `/api/v1/workflow/health`. CLI reference: `uv run --no-sync python -m t1fx.cli --help`.

## Detailed guides

- [Frontend design system and component map](frontend/DESIGN.md): colors, typography, responsive layout and component conventions.

- [UI workflow, recovery and limitations](docs/ui-workflow.md)
- [Architecture](docs/architecture.md)
- [Application and API](docs/application.md)
- [Models and evaluation](docs/models.md)
- [Funding optimization](docs/funding.md)
- [Verification](docs/verification.md)

This is a single-operator local research tool. Cancellation waits for an active training or solver stage to return. Public authentication, distributed execution and real-bank validation are outside its current scope.
