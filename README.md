# T+1 Settlement and FX Funding Risk Engine

A local research application that explains why securities trades miss payment deadlines and compares ways to allocate extra cash. Generate synthetic trades, train models, compare funding policies, and export results through a React dashboard.

**T+1** means settlement is due on the next eligible business day after a trade. For example, a firm may need USD to pay for securities while its incoming funds are in EUR. If the currency conversion receipt arrives late, the payment can miss its deadline. This project lets students and researchers explore whether reserving extra cash reduces those simulated failures and what that reserve costs.

You can complete an experiment in the dashboard after setup; no Python coding or copying artifact IDs is required.

**Research simulation only:** the app does not trade or move money. Trades, operational delays and costs are synthetic. FX reference prices come from Frankfurter; they are daily reference values, not live executable quotes.

## Setup and start

Use **Python 3.11** on Windows, macOS, and Linux. Install Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 22.12+ with npm before continuing. The repository pins Python 3.11 for local use and CI; uv can download that interpreter during setup if it is missing. On macOS, install the XGBoost runtime with `brew install libomp` (requires Homebrew).

Open Terminal on macOS/Linux or PowerShell on Windows. Run these commands one at a time. They download the source, install the locked Python and frontend dependencies, and build the dashboard:

```sh
git clone https://github.com/26PB27-Final-Year-Project/t1-fx-risk.git
cd t1-fx-risk
uv sync --frozen --python 3.11 --extra ml --extra parquet
npm ci --prefix frontend
npm run build --prefix frontend
```

Then start the API and background worker together:

```sh
uv run --no-sync python -m t1fx.cli start
```

Open **http://127.0.0.1:8000/**. Press **Ctrl+C** to stop both services. The launcher checks dependencies, dashboard build and port availability; it never installs packages or creates data automatically. After frontend source changes, run the build command again and refresh your browser.

On later visits, open a terminal in `t1-fx-risk` and run only the startup command. An empty workspace is expected on the first launch; create your first dataset below. Node.js is used to build the interface; Python serves the built interface and runs the engine.

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

Each results page offers a next-step action. Help text, reference rates and technical evidence expand on demand. Funding opens with the budget form; **New experiment** returns to preparation. Displayed experiment amounts are rounded to cents; exports retain full precision. Trades initially load in deadline order while model forecasts are calculated.

The evaluation calendar on Trades changes the observation time within a saved simulation. It supports 38 named timezones and requires online time synchronization. It does not run a live market simulation.

## What the results mean

An **experiment** links a dataset, trained models, an evaluation scenario, and funding-policy results. A **scenario** is one saved set of trades and events to replay. A **policy** is a rule for assigning extra cash. A **seed** makes randomized generation repeatable with the same configuration and inputs. A **held-out** scenario belongs to data reserved for evaluation rather than model fitting.

- **Risk prediction:** estimated settlement failure probability under zero extra reserve. It does not directly drive the funding optimizer or represent post-funding risk.
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

The dataset list verifies file checksums and reads display metadata without decoding every table. Full table and chronological validation runs before eligibility checks and execution; successful checks are reused within the process, with file checksums still verified on every read. Model choices share one dataset validation and decision-time replay per batch. The first operation after restarting can therefore take longer than browsing or later operations.

## Troubleshooting

- **Worker unavailable:** Start the application with the launcher above. Jobs remain queued until a worker is available.
- **Dates rejected:** Use **Use recent training dates**, or choose an earlier first trading date so all partitions and recovery gaps finish before today.
- **Training fails on a small sample:** Generate more trades or use a different seed; each training partition needs suitable outcome classes.
- **FX or time provider unavailable:** Check connectivity in Experiments and retry when the provider is reachable. Generation does not substitute fixed FX prices.
- **Changes do not appear:** Rebuild the dashboard, restart the launcher after backend changes, and refresh the browser.
- **Missing `sklearn` or XGBoost:** Run the complete `uv sync --frozen --python 3.11 --extra ml --extra parquet` setup command from the project root. The `ml` extra installs the training dependencies.
- **Port 8000 is busy:** Stop the other app using it, or start this app with `uv run --no-sync python -m t1fx.cli start --port 8001` and open http://127.0.0.1:8001/.

## Project structure

| Path | Responsibility |
| --- | --- |
| `src/t1fx/` | Python API, worker, generation, models, simulator and funding optimizer |
| `frontend/src/` | React dashboard and shared interface components |
| `tests/` | Backend and integration tests |
| `frontend/e2e/` | Browser acceptance tests |
| `docs/` | Architecture, methodology and workflow documentation |
| `artifacts/` | Generated local research outputs |

The dashboard sends requests to the FastAPI server. A background worker executes queued generation, training and comparison jobs. Both use the same artifact directory; SQLite stores job state, while saved files hold datasets, models and simulation results.

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

## Contributing and reporting problems

Open an issue in this repository with the action you attempted, expected and actual behavior, operating system, and relevant error output. For reproducibility, include the configuration and seed; remove private information before sharing logs or exports.

For changes, create a branch, keep the scope focused, add tests for changed behavior, and run the checks above before opening a pull request. Describe the change and verification results. Preserve chronological data isolation, accounting invariants, and immutable artifact checks; do not commit generated datasets, model bundles, or local environment files.

## License

This project is licensed under the [MIT License](LICENSE).
