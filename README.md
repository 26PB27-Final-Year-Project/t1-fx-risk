# T+1 Settlement-Fail & FX Funding Risk Engine

The first working engine milestone from the architecture blueprint: a deterministic settlement simulator, segregated cash ledger, point-in-time snapshots, transparent rule scores, funding LP, matched policy replay, reproducible artifacts, and a read-only FastAPI service.

This is a synthetic research application. It does not book FX, send payments, or execute funding recommendations. Rule scores are explicitly **not calibrated probabilities**.

## Run the local build

The project-local `.venv` in this workspace is already prepared and tested. From this directory:

```sh
make test
make demo
make serve
```

The API starts at `http://127.0.0.1:8000`; interactive endpoint documentation is at `/docs`, and the schema is at `/openapi.json`. No simulation runs on API startup. Stop the server with Ctrl-C.

Run `make serve` in your regular terminal: the agent execution sandbox blocks local port binding, so no server is currently running. The API routes have been tested in-process.

For a new checkout with package-network access, install [uv](https://docs.astral.sh/uv/) and run `make setup`, then the commands above. `uv sync` uses Python 3.11 as the default target. The source also supports Python 3.14.

**Environment note:** package-index DNS is unavailable in the current execution environment. The working local environment was assembled from existing cached wheels and a local SciPy installation, copied into this project's own virtual environment without changing the source environment. It runs Python 3.14.7. `requirements-py314.lock` records the exact tested package versions; Python 3.11 syntax was checked, but its complete dependency stack has not yet been exercised locally. The CI matrix covers both versions when run with network access. A universal `uv.lock` remains to be generated with network access; the Python 3.14 freeze is not a cross-platform lock.

## What the demo does

The fixture contains three USD cash-equity purchases, all executed on 13 October 2026:

| Trade | Mechanism | No additional funding |
| --- | --- | --- |
| `T-TIMELY` | Instructions and existing cash are ready | Settles on time |
| `T-LATE-AFFIRM` | Affirmation misses the processing cutoff; fallback routing recovers it | Settles on time, incurs a $5 assumed route fee |
| `T-FX-GAP` | $30,000 is required on T+1; the FX receipt arrives later | Misses the deadline, recovers 18 hours later |

`make demo` compares four policies on the identical exogenous scenario and the same $30,000 additional-liquidity limit:

| Policy | Deadline failures | Simulated total cost |
| --- | --- | --- |
| No additional reserve | 1 | $27.50 |
| 50% reserve | 1 | $30.24 |
| Budget-constrained full reserve | 0 | $10.48 |
| LP reserve | 0 | $10.48 |

The 50% reserve cannot settle the whole $30,000 trade, so it adds carrying cost without avoiding the failure. This is intentional: whole-trade replay can expose limitations of a continuous shortfall-cost objective. The fixture verifies mechanics; these results do not establish performance on realistic portfolios.

All rates, final securities deadlines, market-open times, FX arrival assumptions, and fallback fees in this fixture are synthetic. See [assumptions and implementation status](docs/implementation-status.md).

## Commands

```sh
# Generate an editable, fully validated scenario file.
.venv/bin/python -m t1fx.cli write-fixture --output configs/scenarios/three-trades.json

# Replay that scenario with a different USD budget.
.venv/bin/python -m t1fx.cli simulate --scenario configs/scenarios/three-trades.json --budget 15000

# Create outputs elsewhere; global options precede the command.
.venv/bin/python -m t1fx.cli --artifacts /tmp/t1fx-example demo

# Verify all artifact checksums for one of the reported run IDs.
.venv/bin/python -m t1fx.cli validate --run run-<20-character-hash>

# Choose another local API port.
.venv/bin/python -m t1fx.cli serve --port 8010
```

`artifacts/demo-summary.json` lists current comparison run IDs. Each immutable `artifacts/runs/<run_id>/` directory contains inputs, observed snapshots, reference-policy events, rule scores, funding inputs/plan, actual replay events/postings, outcomes, metrics, and a manifest. The manifest records schema, source-code hash, dependency versions, scenario hash, reference and replay policies, and settlement ordering. Identical inputs, code, and runtime produce identical run IDs; altered files fail verification. Incomplete staging directories are invisible to the API.

## API

| Endpoint | Returns |
| --- | --- |
| `GET /health/live` | Process health |
| `GET /health/ready` | Published artifacts verified and available |
| `GET /api/v1/runs` | Completed run manifests |
| `GET /api/v1/runs/{run_id}` | Run metadata |
| `GET /api/v1/runs/{run_id}/trades` | Risk/amount sorting, currency filter, cursor pagination |
| `GET /api/v1/runs/{run_id}/trades/{trade_id}` | Decision-time observations plus separately labeled final outcome |
| `GET /api/v1/runs/{run_id}/funding` | Funding allocations, costs, shortfalls, solver/fallback status |
| `GET /api/v1/runs/{run_id}/metrics` | Realised policy metrics |

API monetary amounts are decimal strings. Reference-policy risk scores remain distinct from the replay policy's realised outcomes. No write/recompute endpoint is exposed before durable jobs are implemented.

## Source map

```text
src/t1fx/
  domain.py       Typed UTC/money/event/scenario contracts
  calendars.py    Explicit business-day coverage and local-time conversion
  ledger.py       Idempotent postings and atomic reservation consumption
  simulator.py    Event queue, lifecycle tracks, deadline labels, recovery, costs
  snapshots.py    Observation-time filtering and uncalibrated rule scores
  funding.py      Peak requirements, scenario LP, budget-aware baselines/fallback
  fixtures.py     Three hand-checkable trades
  storage.py      Canonical JSON, content hashes, atomic publication
  pipeline.py     Reference observation and matched policy replays
  api.py          Read-only FastAPI application
  cli.py          Demo, scenario replay, validation, and serving
```

## Next implementation milestone

Add seeded random scenario generation and validated calendar/FX route profiles, then explicit Arrow/Parquet schemas and the 1,000-trade smoke dataset. After the data contract and simulator are stable, add the baseline/ML training pipeline, durable recomputation jobs, and React dashboard. The full intended system is documented in [the architecture](docs/architecture.md).
