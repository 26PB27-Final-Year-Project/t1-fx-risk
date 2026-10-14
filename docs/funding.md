# FX timing and scenario funding

Gate F adds an empirical FX timing model, joint arrival scenarios, peak cash requirements, and five matched policy replays. This is the empirical fallback described in the architecture. It requires no additional ML dependencies.

## Train and replay

```sh
uv run python -m t1fx.cli generate --config configs/generation/smoke.json --format json
uv run python -m t1fx.cli train-timing --dataset DATASET_ID
uv run python -m t1fx.cli validate-timing --model TIMING_MODEL_ID
```

Use the IDs printed by the commands. Select a scenario from the dataset's test partition: `tables/trades.json` records each trade's `split` and `scenario_id`, and `scenarios/SCENARIO_ID.json` is its replay input.

```sh
uv run python -m t1fx.cli simulate \
  --scenario artifacts/datasets/DATASET_ID/scenarios/SCENARIO_ID.json \
  --timing-model TIMING_MODEL_ID --budget 15000 \
  --scenario-seed 42 --scenario-count 200 --common-shock-weight 0.5
```

The decision must follow the training period's label maturity. The original three-trade demo predates the smoke training period and will be rejected. Omitting `--timing-model` retains the original four-policy deterministic comparison.

With a timing model, the comparison includes fixed zero/half/full reserves, deterministic LP, and scenario LP. Fixed fractions apply to the maximum sampled requirement and are scaled to the shared budget. All five policies replay identical exogenous events, with the existing whole-trade settlement priority and bridge/late/route cost accounting. Realised costs are distinct from the LP's planning objective. No policy is guaranteed to win.

## Forecast contract

- Training reads only observable FX contracts and training-period credit observations. It never reads hidden generation parameters as features. Each sell/buy currency pair defines a route because richer route identifiers are absent from the current domain contract.
- The model stores empirical seconds from expected credit to observable credit. Unresolved training receipts retain conservative mass at no arrival, rather than being dropped. The training period's recovery end defines label maturity.
- Unposted receipts are conditioned on arrival after the decision time. If no historical arrivals survive that condition, the forecast assumes no arrival within the planning horizon. Unknown currency routes fail explicitly.
- Each scenario samples one arrival per pending FX leg. Shared receipts are counted once and evaluated consistently against all bucket deadlines. Already posted receipts stay in observed cash.
- The common-shock weight mixes a shared quantile with independent quantiles. This preserves marginal distributions and introduces positive dependence. Weight zero means independent draws; weight one means common quantiles. The weight is an explicit stress assumption, not an estimated correlation.
- Test metrics use untouched chronological test snapshots, deduplicated by leg/decision/target. Reports include Brier score, reliability bins, and the individual predictions. These are empirical probabilities, not separately calibrated classifier outputs. Repeated observations and pooled receipts mean evaluation rows are not independent.

Model bundles under `artifacts/timing_models/` are immutable, content addressed JSON, verified on load. Runs persist the timing model ID, seed, common-shock weight, sampled arrivals, exact scenario weights in `funding_problem.json`, and generated requirements. Forecast rows include target probabilities, sampled late frequencies, and absolute sampling error. Sampling error is reported rather than silently claiming exact marginal matching.

## Read-only API

- `GET /api/v1/timing-models/TIMING_MODEL_ID/health`: provenance and frozen test metrics.
- `GET /api/v1/runs/RUN_ID/timing-forecasts`: forecast probabilities, diagnostics, and sampled arrivals; returns 404 for deterministic-only runs.
- Existing funding and metrics endpoints show the selected run's allocations and realised replay results.

## Limits

Forecast quality is limited by synthetic training coverage. Missing receipts are treated conservatively; this is not a survival estimator for censoring. Route/state classifiers, separate probability calibration, fitted correlation, rolling liquidity, and full E4/E5 experiments remain future work. The comparison pipeline remains USD-only. Standalone funding optimization retains explicit currency conversion support. The original smoke configuration often expects FX after the settlement deadline, so its policy results may be identical even with timing scenarios; the acceptance tests include a receipt expected exactly at the deadline where timing uncertainty changes the allocation and realised outcome.
