# Risk-model workflow

Generate a dataset with `make smoke`, then run `make train DATASET=DATASET_ID`. Install dependencies with `make setup`; macOS also needs `brew install libomp`. Small datasets without both label classes in train, tuning, or calibration are rejected. Increase trade/day counts instead of moving test observations into fitting partitions.

## What is fitted

The primary model is XGBoost. Logistic regression and the existing transparent rule score provide comparisons. Training evaluates a fixed grid of tree depth/round count, logistic regularization, and balanced versus unweighted classes. Selection uses uncalibrated tuning log loss. Both learned models then receive a monotonic sigmoid calibrator fitted exclusively on the calibration partition. The primary model choice, 0.5 alert threshold, and 10% alert capacity are declared before test evaluation.

Each trade has total snapshot weight one. Preprocessing uses training rows only. Counterparty history includes only outcomes from eligible training trades, known strictly before each snapshot. A population prior smooths sparse history; no history uses a 0.5 prior with explicit cold-start indicators. Sorted training counterparties at indices 0, 5, 10, ... are reserved from every fitting stage and history, then reported separately on the final test cohort.

The final chronological test and a predefined stress family are evaluated after model artifacts are frozen. PR-AUC is reported as average precision. Alerts at 10% capacity are selected separately per scenario/observation time, rounding up and breaking ties by trade ID. Missed exposure is weighted by notional and snapshot weight. Bootstrap intervals resample complete scenario days; fewer than ten days are explicitly marked as limited evidence. The smoke dataset has only two final test days.

## Bundle and inference

`artifacts/models/MODEL_ID/` contains XGBoost JSON, logistic coefficients, preprocessing, calibrators, feature schema, thresholds, dated history, training provenance, frozen test predictions/metrics, and a model card. No pickle is loaded. The manifest verifies every file and the fitted-artifact identity. Inference requires a compatible feature schema and XGBoost major version.

```sh
.venv/bin/python -m t1fx.cli validate-model --model MODEL_ID
.venv/bin/python -m t1fx.cli score-model --model MODEL_ID --dataset DATASET_ID --split test
```

To inspect risk predictions through the API, replay a scenario from the dataset's **test** cohort, after calibration labels mature:

```sh
.venv/bin/python -m t1fx.cli simulate --scenario artifacts/datasets/DATASET_ID/scenarios/TEST_SCENARIO_ID.json
make serve
```

Find test scenario IDs in `tables/trades.json` by filtering `split == "test"`. Use the run ID printed by replay:

- `GET /api/v1/models`: published models.
- `GET /api/v1/models/MODEL_ID/health`: model availability, provenance, and frozen metrics.
- `GET /api/v1/runs/RUN_ID/model-predictions?model_id=MODEL_ID`: reference-policy probabilities and explanations; optional `trade_id` filters a trade.

Predictions apply to the fixed-0 reference snapshots stored in each run. The run may replay another funding policy, but these probabilities still describe the reference policy. They do not estimate post-intervention risk. Observations before calibration maturity, unsupported currencies/policies, missing bundles, and incompatible models are rejected with `MODEL_UNAVAILABLE`. Existing rule-score endpoints remain explicitly uncalibrated.

Tree SHAP contributions and the expected margin sum to the raw XGBoost log-odds. The calibrated probability is separate; contributions are associations, not causal effects or additive percentage-point changes. Inference returns all contributions and the three largest by absolute magnitude. The implementation uses [XGBoost's native contribution predictions](https://xgboost.readthedocs.io/en/stable/python/python_api.html#xgboost.Booster.predict).

Synthetic calibration and strong test scores do not establish performance on actual settlement data. FX arrival forecasting, production history feeds, and the full funding-scenario engine are subsequent work.
