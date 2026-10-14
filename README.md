# T+1 Settlement-Fail & FX Funding Risk Engine

A synthetic research backend for simulating settlement failures, measuring FX funding gaps, and comparing funding policies.

- Deterministic settlement simulation with segregated cash, deadline outcomes, and late recovery.
- Point-in-time snapshots, transparent rule scores, and budget-constrained funding optimization.
- Seeded datasets with chronological train/tuning/calibration/test splits, JSON/Parquet schemas, and integrity checks.
- Read-only FastAPI endpoints for simulation runs, trades, funding, and metrics.

## Setup and run

Requires Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/). Python 3.11 is the default.

```sh
make setup       # Install dependencies, including PyArrow
make test
make demo        # Compare four funding policies on the reference scenario
make smoke       # Generate 1,000 trades and 2,000 snapshots
make serve
```

API documentation: <http://127.0.0.1:8000/docs>. Generate a demo before browsing run endpoints.

## Datasets and scenarios

```sh
# Require both JSON and Parquet output.
make smoke DATASET_FORMAT=both

# Customize generation.
.venv/bin/python -m t1fx.cli generate --config configs/generation/smoke.json --seed 42 --trades 1000

# Replace DATASET_ID with the ID printed by generation.
.venv/bin/python -m t1fx.cli validate-dataset --dataset DATASET_ID
.venv/bin/python -m t1fx.cli export-parquet --dataset DATASET_ID

# Replay a scenario with a USD funding budget.
.venv/bin/python -m t1fx.cli simulate --scenario configs/scenarios/three-trades.json --budget 15000
```

Outputs are stored in `artifacts/runs/` and `artifacts/datasets/`. Each dataset includes a manifest, resolved configuration, schemas, validation report, data card, and replayable scenarios. Releases are immutable; Parquet export creates a new release. Identical inputs, source, and runtime produce the same release ID.

The default format is `auto`: JSON plus Parquet when PyArrow is installed, otherwise JSON with an explicit `not_exported` status. Use `--format json` for JSON only. Global `--artifacts PATH` must precede the subcommand.

Use snapshots as point-in-time observations and outcomes as labels. Event/posting tables contain future records and require time filtering. `simulation_truth/` contains hidden generation parameters and must never be used as model features. Split boundaries allow prior labels to mature before the next partition.

## Scope

Calendars, FX conventions, and costs are synthetic assumptions. Generated settlement is USD-only; securities are assumed available. Rule scores are uncalibrated. The engine does not execute payments or FX trades. ML training, durable jobs, authentication, and the dashboard are not implemented.

See [architecture](docs/architecture.md) and [implementation status](docs/implementation-status.md) for design details and limitations.
