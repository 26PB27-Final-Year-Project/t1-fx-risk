# T+1 Settlement and FX Funding Risk Engine

A local research application for exploring why securities trades miss settlement deadlines and how funding decisions affect those outcomes. It creates synthetic trades, simulates their settlement and FX cash flows, estimates observable risk, and compares funding policies. A dashboard makes the results easy to inspect.

**This is a research demo.** Its data, market rules, and costs are synthetic. It does not move money, book FX, or send settlement instructions.

## What you can do

- **Review trades:** see funding gaps, rule scores, deadlines, instruction status, and later outcomes.
- **Compare funding policies:** allocate a shared liquidity budget and inspect failures and realised costs across matched runs.
- **Study models:** generate larger datasets, train risk and FX timing models, and inspect their evaluation reports.
- **Reproduce results:** use seeded inputs, immutable artifacts, and a separate worker for funding jobs.

## Run the demo

You need Python 3.11–3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 22.12+, and npm. On macOS, install XGBoost's OpenMP runtime with `brew install libomp`.

```sh
git clone https://github.com/26PB27-Final-Year-Project/t1-fx-risk.git
cd t1-fx-risk
uv sync --extra parquet --extra ml
npm ci --prefix frontend
npm run build --prefix frontend
uv run python -m t1fx.cli demo
```

`demo` publishes four funding-policy runs for **50 reproducible synthetic trades**. To choose another sample, use `demo --trades 40 --seed 7`. The small three-trade fixture in `configs/scenarios/three-trades.json` remains available for hand-checking the engine.

Start the app and worker in separate terminals:

```sh
uv run python -m t1fx.cli serve
```

```sh
uv run python -m t1fx.cli worker
```

Open **<http://127.0.0.1:8000/>** for the dashboard or **<http://127.0.0.1:8000/docs>** for the API. If a saved URL still opens an older run, return to the root URL or select `demo-50-s42-v1` in **Simulation run**.

The dashboard has four views: **Trades** for individual cases, **Funding** for reserves and budget changes, **Scenarios** for policy comparisons, and **Model health** for trained-model reports. Recomputing funding requires the worker process.

## Generate research data

The demo is intended for exploration. Use the dataset generator for model training and experiments:

```sh
uv run python -m t1fx.cli generate --config configs/generation/smoke.json --format both
uv run python -m t1fx.cli validate-dataset --dataset DATASET_ID
uv run python -m t1fx.cli train --dataset DATASET_ID
uv run python -m t1fx.cli train-timing --dataset DATASET_ID
```

The generator prints `DATASET_ID`; training prints model IDs. The smoke configuration creates 1,000 trades across dated training and evaluation partitions. Data and runs are stored under `artifacts/`. For other commands and model compatibility rules, see the guides below.

## Project layout

| Path | Contents |
| --- | --- |
| `src/t1fx/` | Simulation, risk models, funding optimizer, API, jobs, and CLI |
| `frontend/` | React dashboard and browser checks |
| `configs/` | Scenario and dataset generation inputs |
| `artifacts/` | Generated datasets, models, and run results; ignored by Git |
| `docs/` | Detailed architecture, workflows, assumptions, and status |

## Documentation

- [Application and dashboard](docs/application.md)
- [Risk-model workflow](docs/models.md)
- [FX timing and scenario funding](docs/funding.md)
- [Implementation status and limitations](docs/implementation-status.md)
- [Architecture and design decisions](docs/architecture.md)

The current implementation is designed for one local operator. Generated settlement is USD-only, securities availability is assumed, and model results on synthetic data do not establish real-world accuracy.
