"""Chronological risk-model training; final evaluation happens after fitting is frozen."""

from collections import Counter
import importlib.metadata
import json
from pathlib import Path
import platform

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

from t1fx.datasets import build_tables, validate_dataset
from t1fx.features import (
    FEATURE_HASH, FEATURE_SCHEMA, FEATURES, fit_preprocessing, matrix,
    rule_scores, snapshot_weights, transform,
)
from t1fx.generation import GenerationConfig
from t1fx.model_metrics import clustered_intervals, evaluate
from t1fx.models import ModelStore, calibrated, xgboost_module
from t1fx.pipeline import _code_hash
from t1fx.storage import digest

SPLITS = ('train', 'tuning', 'calibration', 'test')
TRAINING_CONFIG = {
    'version': 'risk-training-v1', 'seed': 42, 'primary_model': 'xgboost',
    'logistic_C': [0.1, 1.0, 10.0], 'class_weighting': ['unweighted', 'balanced'],
    'xgboost_depth': [2, 3], 'xgboost_rounds': [40, 80], 'learning_rate': 0.08,
    'selection': 'Minimum uncalibrated tuning log loss, deterministic candidate-order ties.',
    'calibration': 'Monotonic sigmoid on raw margins; calibration partition only; L2 slope penalty 0.001.',
    'probability_threshold': 0.5, 'alert_capacity': 0.1,
    'counterparty_holdout': 'Sorted training-counterparty IDs at indices 0,5,10,...; excluded from all fitting and history.',
    'bootstrap_repetitions': 200,
    'stress': {'late_confirmation_rate': 0.20, 'missed_fx_cutoff_rate': 0.25,
               'ssi_mismatch_rate': 0.15, 'funding_stress_rate': 0.25},
}


def fit_sigmoid(margins, labels, weights):
    if len(np.unique(labels)) != 2:
        raise ValueError('Calibration needs both outcome classes; generate more trades or days')
    margins = np.asarray(margins, dtype=float)
    labels, weights = np.asarray(labels), np.asarray(weights)
    weights = weights/weights.sum()

    def loss(params):
        z = params[0]*margins+params[1]
        error = (expit(z)-labels)*weights
        value = np.sum(weights*(np.logaddexp(0, z)-labels*z)) + .001*params[0]**2
        gradient = np.asarray([np.sum(error*margins)+.002*params[0], np.sum(error)])
        return value, gradient

    fitted = minimize(loss, [1.0, 0.0], jac=True, method='L-BFGS-B',
                      bounds=[(0, 100), (-100, 100)], options={'maxiter': 1000, 'ftol': 1e-12})
    if not fitted.success or not np.all(np.isfinite(fitted.x)):
        raise ValueError('Sigmoid calibration failed to converge')
    return {'method': 'sigmoid_raw_margin', 'slope': float(fitted.x[0]),
            'intercept': float(fitted.x[1]), 'partition': 'calibration'}


def training_weights(labels, weights, mode):
    if mode == 'unweighted':
        return weights
    mass = np.bincount(labels, weights=weights, minlength=2)
    return weights*(mass.sum()/(2*mass))[labels]


def fit_candidates(x, y, weights, tuning_x, tuning_y, tuning_weights):
    xgb = xgboost_module()
    candidates = []
    best_lr, best_xgb = None, None
    tuning_data = xgb.DMatrix(tuning_x, feature_names=list(FEATURES), nthread=1)
    for mode in TRAINING_CONFIG['class_weighting']:
        fit_weights = training_weights(y, weights, mode)
        for c in TRAINING_CONFIG['logistic_C']:
            estimator = LogisticRegression(C=c, max_iter=2000, solver='lbfgs', random_state=42)
            estimator.fit(x, y, sample_weight=fit_weights)
            score = float(log_loss(tuning_y, estimator.predict_proba(tuning_x)[:, 1],
                                   sample_weight=tuning_weights, labels=[0, 1]))
            spec = {'model': 'logistic', 'C': c, 'weighting': mode, 'tuning_log_loss': score}
            candidates.append(spec)
            if best_lr is None or score < best_lr[0]:
                best_lr = (score, estimator, spec)
        data = xgb.DMatrix(x, label=y, weight=fit_weights, feature_names=list(FEATURES), nthread=1)
        for depth in TRAINING_CONFIG['xgboost_depth']:
            for rounds in TRAINING_CONFIG['xgboost_rounds']:
                params = {'objective': 'binary:logistic', 'eval_metric': 'logloss',
                          'max_depth': depth, 'eta': TRAINING_CONFIG['learning_rate'],
                          'seed': 42, 'nthread': 1, 'tree_method': 'hist', 'subsample': 1,
                          'colsample_bytree': 1, 'min_child_weight': 1}
                booster = xgb.train(params, data, num_boost_round=rounds)
                score = float(log_loss(tuning_y, booster.predict(tuning_data),
                                       sample_weight=tuning_weights, labels=[0, 1]))
                spec = {'model': 'xgboost', 'depth': depth, 'rounds': rounds,
                        'weighting': mode, 'tuning_log_loss': score}
                candidates.append(spec)
                if best_xgb is None or score < best_xgb[0]:
                    best_xgb = (score, booster, spec)
    return best_lr, best_xgb, candidates


def holdout_counterparties(trades):
    ids = sorted({t['counterparty_id'] for t in trades.values() if t['split'] == 'train'})
    if len(ids) < 2:
        raise ValueError('Training needs at least two counterparties for the reserved-group evaluation')
    return set(ids[::5])


def prepare(tables):
    trades = {t['trade_id']: t for t in tables['trades']}
    outcomes = {o['trade_id']: o for o in tables['outcomes']}
    held_out = holdout_counterparties(trades)
    partitions = {}
    for split in SPLITS:
        rows = [r for r in tables['snapshots'] if r['split'] == split
                and (split == 'test' or trades[r['trade_id']]['counterparty_id'] not in held_out)]
        if not rows:
            raise ValueError(f'No usable rows in {split} partition')
        partitions[split] = rows
    history = [{'trade_id': t['trade_id'], 'counterparty_id': t['counterparty_id'],
                'known_at': outcomes[t['trade_id']]['outcome_known_at'],
                'failed': int(outcomes[t['trade_id']]['missed_contractual_deadline'])}
               for t in trades.values() if t['split'] == 'train' and t['counterparty_id'] not in held_out]
    history.sort(key=lambda h: (h['known_at'], h['trade_id']))
    labels = {split: np.asarray([outcomes[r['trade_id']]['missed_contractual_deadline'] for r in rows], dtype=int)
              for split, rows in partitions.items()}
    for split in ('train', 'tuning', 'calibration'):
        if set(labels[split]) != {0, 1}:
            raise ValueError(f'{split} needs both classes after reserving counterparties; generate a larger dataset')
    return trades, partitions, labels, history, held_out


def report_predictions(rows, labels, x, trades, booster, logistic, calibration):
    xgb = xgboost_module()
    data = xgb.DMatrix(x, feature_names=list(FEATURES), nthread=1)
    margin = booster.predict(data, output_margin=True)
    contributions = booster.predict(data, pred_contribs=True)
    if not np.allclose(contributions.sum(axis=1), margin, atol=1e-5):
        raise ValueError('SHAP additivity failed on held-out predictions')
    probabilities = {
        'xgboost': calibrated(margin, calibration['xgboost']),
        'logistic': calibrated(logistic.decision_function(x), calibration['logistic']),
        'rules': rule_scores(rows),
    }
    report = {name: evaluate(rows, labels, p, trades, probability=name != 'rules')
              for name, p in probabilities.items()}
    report['by_horizon'] = {}
    for horizon, indices in (
        ('before_affirmation', [i for i, r in enumerate(rows) if r['minutes_to_affirmation_cutoff'] >= 0]),
        ('after_affirmation', [i for i, r in enumerate(rows) if r['minutes_to_affirmation_cutoff'] < 0]),
    ):
        report['by_horizon'][horizon] = {
            name: evaluate([rows[i] for i in indices], labels[indices], p[indices], trades,
                           probability=name != 'rules') for name, p in probabilities.items()
        }
    report['xgboost_uncertainty'] = clustered_intervals(rows, labels, probabilities['xgboost'])
    predictions = [{'trade_id': row['trade_id'], 'as_of': row['as_of'], 'scenario_id': row['scenario_id'],
                    'label': int(labels[i]), 'p_fail_any': float(probabilities['xgboost'][i]),
                    'logistic_p_fail_any': float(probabilities['logistic'][i]),
                    'rule_score': float(probabilities['rules'][i]*100),
                    'raw_margin': float(margin[i]), 'raw_probability': float(expit(margin[i])),
                    'expected_margin': float(contributions[i, -1]), 'shap_units': 'log_odds',
                    'contributions': dict(zip(FEATURES, map(float, contributions[i, :-1]), strict=True))}
                   for i, row in enumerate(rows)]
    report['mean_absolute_shap_log_odds'] = dict(zip(FEATURES, map(float, np.mean(np.abs(contributions[:, :-1]), axis=0)), strict=True))
    return report, predictions, probabilities


def train_model(root: Path, dataset_id: str):
    manifest = validate_dataset(root, dataset_id)
    directory = root/'datasets'/dataset_id
    # Features consume only these declared public tables; outcomes enter as labels or dated history.
    tables = {name: json.loads((directory/manifest['tables'][name]['json']).read_text())
              for name in ('trades', 'snapshots', 'outcomes')}
    trades, rows, labels, history, held_out = prepare(tables)
    weights = {split: snapshot_weights(partition) for split, partition in rows.items()}
    train_x = matrix(rows['train'], trades, history)
    preprocessing = fit_preprocessing(train_x, weights['train'])
    x = {split: transform(matrix(rows[split], trades, history), preprocessing)
         for split in ('train', 'tuning', 'calibration')}
    best_lr, best_xgb, candidates = fit_candidates(x['train'], labels['train'], weights['train'],
                                                  x['tuning'], labels['tuning'], weights['tuning'])
    logistic, booster = best_lr[1], best_xgb[1]
    xgb = xgboost_module()
    calibration_data = xgb.DMatrix(x['calibration'], feature_names=list(FEATURES), nthread=1)
    calibration = {
        'xgboost': fit_sigmoid(booster.predict(calibration_data, output_margin=True), labels['calibration'], weights['calibration']),
        'logistic': fit_sigmoid(logistic.decision_function(x['calibration']), labels['calibration'], weights['calibration']),
    }
    thresholds = {'probability': .5, 'alert_capacity': .1, 'selection': 'Predeclared; never fitted on test data.'}
    fitted = {'model.json': json.loads(booster.save_raw(raw_format='json').decode()),
              'logistic.json': {'coefficients': logistic.coef_[0].tolist(), 'intercept': float(logistic.intercept_[0])},
              'preprocessing.json': preprocessing, 'calibration.json': calibration,
              'feature_schema.json': FEATURE_SCHEMA, 'thresholds.json': thresholds, 'history.json': history}
    frozen_hash = digest(fitted)
    # Nothing below this boundary updates preprocessing, model, calibration, or thresholds.
    test_x = transform(matrix(rows['test'], trades, history), preprocessing)
    report, predictions, probabilities = report_predictions(rows['test'], labels['test'], test_x,
                                                            trades, booster, logistic, calibration)
    report['counterparty_groups'] = {}
    seen_counterparties = {trades[row['trade_id']]['counterparty_id'] for row in rows['train']}
    for group, unseen in [('unseen', True), ('seen', False)]:
        indices = [i for i, row in enumerate(rows['test'])
                   if (trades[row['trade_id']]['counterparty_id'] not in seen_counterparties) == unseen]
        report['counterparty_groups'][group] = {
            name: evaluate([rows['test'][i] for i in indices], labels['test'][indices], p[indices], trades,
                           probability=name != 'rules') for name, p in probabilities.items()
        }
    # A predeclared stress family is evaluated only after the same fitted bundle is frozen.
    config = json.loads((directory/'resolved_config.json').read_text())
    config.update(TRAINING_CONFIG['stress'])
    config['config_id'] = 'risk-holdout-stress'
    config['seed'] += 104729
    stress_tables, _, stress_summary = build_tables(GenerationConfig.model_validate(config))
    stress_trades = {t['trade_id']: t for t in stress_tables['trades']}
    stress_rows = [r for r in stress_tables['snapshots'] if r['split'] == 'test']
    stress_labels = {o['trade_id']: o['missed_contractual_deadline'] for o in stress_tables['outcomes']}
    stress_y = np.asarray([stress_labels[r['trade_id']] for r in stress_rows], dtype=int)
    stress_x = transform(matrix(stress_rows, stress_trades, history), preprocessing)
    stress_report, _, _ = report_predictions(stress_rows, stress_y, stress_x, stress_trades, booster, logistic, calibration)
    report['stress_family'] = {'config': config, 'tables_hash': digest(stress_tables),
                               'summary': stress_summary, 'test_metrics': stress_report}
    assert digest(fitted) == frozen_hash
    versions = {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'scikit-learn', 'xgboost')}
    versions['python'] = platform.python_version()
    training = {'dataset_id': dataset_id, 'dataset_identity': manifest['identity'],
                'feature_schema_hash': FEATURE_HASH, 'fitted_artifacts_hash': frozen_hash,
                'reference_policy_id': 'fixed-0', 'training_config': TRAINING_CONFIG,
                'versions': versions, 'code_hash': _code_hash(), 'selected_candidates': [best_lr[2], best_xgb[2]],
                'candidates': candidates, 'held_out_counterparties': sorted(held_out),
                'partitions': manifest['summary']['partitions'],
                'fitting_rows': {split: len(rows[split]) for split in SPLITS},
                'label_counts': {split: dict(Counter(map(str, labels[split]))) for split in SPLITS},
                'label_semantics': 'Missed contractual deadline under fixed-0; censored recovery does not censor this known binary label.'}
    card = ("# Settlement risk model\n\n"
            "Synthetic USD research model predicting deadline failure under the fixed-0 reference funding policy. "
            "XGBoost is the predeclared primary model; logistic regression and uncalibrated rules are baselines. "
            "No superiority is assumed.\n\n"
            "Training-only scaling and dated counterparty history; tuning-only candidate selection; separate "
            "monotonic sigmoid calibration; predeclared 0.5 alert threshold and 10% queue capacity. "
            "Every trade has total snapshot weight one. Held-out counterparty groups are excluded from fitting "
            "and history. Final chronological and stress-family results are evaluated after freezing the fitted bundle.\n\n"
            "Tree SHAP explains raw log-odds, not changes in calibrated probability or causal effects. "
            "Counterparty history contains only training-cohort outcomes known strictly before each observation. "
            "FX forecasts, historical delay quantiles, and production calendar feeds are not model features yet.\n\n"
            "Inspect metrics.json for reliability, decision horizons, unseen counterparties, stress performance, "
            "and scenario-day bootstrap intervals. Small numbers of held-out days cannot establish reliability. "
            "Synthetic calibration does not establish real-world calibration. Do not interpret predictions as "
            "risk after applying a different funding policy. Model files use JSON, never pickle.\n\n"
            "Tree SHAP implementation: XGBoost Booster.predict(pred_contribs=True), "
            "https://xgboost.readthedocs.io/en/stable/python/python_api.html .\n")
    artifacts = fitted | {'training_manifest.json': training, 'metrics.json': report,
                          'test_predictions.json': predictions, 'model_card.md': card}
    model_id = ModelStore(root).publish(artifacts)
    return {'model_id': model_id, 'directory': str(root/'models'/model_id),
            'dataset_id': dataset_id, 'test': {name: report[name] for name in ('rules', 'logistic', 'xgboost')}}
