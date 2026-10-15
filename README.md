# T+1 Settlement-Fail & FX Funding Risk Engine

A synthetic research backend for simulating settlement failures, measuring FX funding gaps, and comparing funding policies.

- Deterministic settlement simulation with segregated cash, deadline outcomes, and late recovery.
- Point-in-time snapshots, transparent rule scores, and budget-constrained funding optimization.
- Empirical FX timing forecasts, correlated arrival scenarios, and five-policy matched replay.
- Seeded datasets with chronological train/tuning/calibration/test splits, JSON/Parquet schemas, and integrity checks.
- Calibrated XGBoost and logistic regression, Tree SHAP explanations, and model health reports.
- React dashboard for trades, funding, policy comparisons, and model health.
- Run-scoped FastAPI endpoints and durable SQLite jobs for funding recomputation.

## Setup

Requires Git and Python 3.11–3.14. Install [uv](https://docs.astral.sh/uv/getting-started/installation/); it selects the project's Python 3.11 version and downloads it if needed.

macOS or Linux (Terminal):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Open a new terminal, clone the project, and install its runtime, Parquet, and machine-learning dependencies:

```sh
git clone https://github.com/26PB27-Final-Year-Project/t1-fx-risk.git
cd t1-fx-risk
uv sync --extra parquet --extra ml
```

`uv sync` creates the project virtual environment and installs the development tools used by the test suite. `.python-version` selects Python 3.11 for consistent environments. On macOS, install XGBoost's OpenMP runtime before running the app:

```sh
brew install libomp
```

If you prefer pip, create and activate a virtual environment, then install the project and extras:

```sh
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[parquet,ml]'
python -m pip install pytest httpx
```

Use `uv run` below on all three operating systems. `make` examples are also provided for systems with Make installed.

## Run

```sh
uv run pytest
uv run python -m t1fx.cli demo
uv run python -m t1fx.cli generate --config configs/generation/smoke.json --format both
uv run python -m t1fx.cli serve
```

The commands respectively run the checks, compare four funding policies on the reference scenario, generate 1,000 trades and 2,000 snapshots, and start the API. The generator prints the dataset ID; save it for the dataset and model commands below. API documentation is at <http://127.0.0.1:8000/docs>.

Equivalent Make shortcuts: `make setup`, `make test`, `make demo`, `make smoke DATASET_FORMAT=both`, and `make serve`. Train a model with `make train DATASET=DATASET_ID`.

## Dashboard and worker

With Node.js 22.12+ and npm installed:

```sh
npm ci --prefix frontend
npm run build --prefix frontend
uv run python -m t1fx.cli demo
uv run python -m t1fx.cli serve
# In another terminal:
uv run python -m t1fx.cli worker
```

Open <http://127.0.0.1:8000/>. The dashboard explores stored trades, funding, matched policy comparisons, and model health. Funding recomputation runs in the separate worker and survives API restarts. See [application usage](docs/application.md) for job behavior, development, and browser checks.

## Datasets and scenarios

```sh
# Require both JSON and Parquet output.
uv run python -m t1fx.cli generate --config configs/generation/smoke.json --format both

# Customize generation.
uv run python -m t1fx.cli generate --config configs/generation/smoke.json --seed 42 --trades 1000

# Replace DATASET_ID with the ID printed by generation.
uv run python -m t1fx.cli validate-dataset --dataset DATASET_ID
uv run python -m t1fx.cli export-parquet --dataset DATASET_ID

# Replay a scenario with a USD funding budget.
uv run python -m t1fx.cli simulate --scenario configs/scenarios/three-trades.json --budget 15000
```

Outputs are stored in `artifacts/runs/` and `artifacts/datasets/`. Each dataset includes a manifest, resolved configuration, schemas, validation report, data card, and replayable scenarios. Releases are immutable; Parquet export creates a new release. Identical inputs, source, and runtime produce the same release ID.

The default format is `auto`: JSON plus Parquet when PyArrow is installed, otherwise JSON with an explicit `not_exported` status. Use `--format json` for JSON only. Global `--artifacts PATH` must precede the subcommand.

Use snapshots as point-in-time observations and outcomes as labels. Event/posting tables contain future records and require time filtering. `simulation_truth/` contains hidden generation parameters and must never be used as model features. Split boundaries allow prior labels to mature before the next partition.

## Risk models

```sh
# Use the dataset ID printed by make smoke.
make train DATASET=DATASET_ID

# Use the model ID printed by training.
uv run python -m t1fx.cli validate-model --model MODEL_ID
uv run python -m t1fx.cli score-model --model MODEL_ID --dataset DATASET_ID --split test
```

Models are saved under `artifacts/models/`, with frozen metrics, test predictions, training provenance, and a model card. Training reserves counterparties, tunes on the tuning partition, and calibrates on the calibration partition. Final reports include chronological and stress evaluations, reliability bins, and uncertainty across scenario days.

Browse `/api/v1/models/MODEL_ID/health` for results. `/api/v1/runs/RUN_ID/model-predictions?model_id=MODEL_ID` scores reference-policy snapshots after the model's calibration period. The original three-trade demo predates that period and cannot be scored by a model trained on the smoke dataset. See [model usage](docs/models.md) for a compatible scenario replay.

## Scenario funding

Train an empirical timing model with `uv run python -m t1fx.cli train-timing --dataset DATASET_ID`.
Pass its ID to `simulate --timing-model TIMING_MODEL_ID` to compare deterministic and scenario LP funding alongside three fixed funding policies. Use a dataset test scenario after training-label maturity.
See [funding usage](docs/funding.md) for commands, forecast assumptions, and API endpoints.

## Scope

Calendars, FX conventions, and costs are synthetic assumptions. Generated settlement is USD-only; securities are assumed available. Rule scores are uncalibrated. The engine does not execute payments or FX trades. ML probabilities are calibrated on synthetic data under the fixed-0 funding policy; they do not estimate risk after a new funding intervention. The dashboard and worker are for local use; shared-host authentication and a live replay clock are not implemented.

See [architecture](docs/architecture.md) and [implementation status](docs/implementation-status.md) for design details and limitations.
