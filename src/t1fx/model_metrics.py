"""Trade-weighted evaluation and scenario-day uncertainty estimates."""

import math

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from t1fx.features import snapshot_weights


def evaluate(rows, y, scores, trades, *, probability=True, capacity=0.1):
    if not rows:
        return {'rows': 0, 'status': 'NO_OBSERVATIONS'}
    weights = snapshot_weights(rows)
    y, scores = np.asarray(y), np.asarray(scores)
    positives = float(np.sum(weights*y))
    both = len(np.unique(y)) == 2
    result = {'rows': len(rows), 'trades': len({r['trade_id'] for r in rows}),
              'positive_trade_weight': positives,
              'pr_auc_average_precision': float(average_precision_score(y, scores, sample_weight=weights)) if positives else None,
              'roc_auc': float(roc_auc_score(y, scores, sample_weight=weights)) if both else None,
              'score_type': 'CALIBRATED_PROBABILITY' if probability else 'UNCALIBRATED_RULE_SCORE'}
    if probability:
        scores = np.clip(scores, 1e-7, 1-1e-7)
        result.update(brier=float(brier_score_loss(y, scores, sample_weight=weights)),
                      log_loss=float(log_loss(y, scores, sample_weight=weights, labels=[0, 1])))
        bins = []
        for index in range(10):
            mask = (scores >= index/10) & (scores < (index+1)/10)
            bins.append({'lower': index/10, 'upper': (index+1)/10, 'count': int(mask.sum()),
                         'weight': float(weights[mask].sum()),
                         'mean_probability': float(np.average(scores[mask], weights=weights[mask])) if mask.any() else None,
                         'failure_rate': float(np.average(y[mask], weights=weights[mask])) if mask.any() else None})
        result['reliability_bins'] = bins
    # Capacity applies separately to each scenario and decision time, with stable tie-breaking.
    selected = np.zeros(len(rows), dtype=bool)
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault((row['scenario_id'], row['as_of']), []).append(i)
    for indices in groups.values():
        ordered = sorted(indices, key=lambda i: (-float(scores[i]), rows[i]['trade_id']))
        selected[ordered[:max(1, math.ceil(len(ordered)*capacity))]] = True
    tp = float(np.sum(weights*selected*y))
    amount = np.asarray([trades[r['trade_id']]['settlement_amount_minor'] for r in rows])
    failed_exposure = float(np.sum(weights*y*amount))
    missed_exposure = float(np.sum(weights*y*amount*~selected))
    result['alert_capacity'] = {'fraction': capacity, 'selected_rows': int(selected.sum()),
                              'precision': tp/float(np.sum(weights*selected)),
                              'recall': tp/positives if positives else None,
                              'missed_exposure_usd': missed_exposure/100,
                              'missed_exposure_fraction': missed_exposure/failed_exposure if failed_exposure else None}
    return result


def clustered_intervals(rows, y, probability, *, seed=42, repetitions=200):
    groups = sorted({r['scenario_id'] for r in rows})
    if len(groups) < 2:
        return {'scenario_days': len(groups), 'status': 'INSUFFICIENT_CLUSTERS'}
    indices = {g: np.asarray([i for i, row in enumerate(rows) if row['scenario_id'] == g]) for g in groups}
    weights = snapshot_weights(rows)
    rng = np.random.default_rng(seed)
    results = {'brier': [], 'pr_auc_average_precision': []}
    for _ in range(repetitions):
        chosen = np.concatenate([indices[g] for g in rng.choice(groups, len(groups), replace=True)])
        labels, p, w = y[chosen], probability[chosen], weights[chosen]
        results['brier'].append(float(np.average((labels-p)**2, weights=w)))
        if np.any(labels):
            results['pr_auc_average_precision'].append(float(average_precision_score(labels, p, sample_weight=w)))
    return {'scenario_days': len(groups), 'repetitions': repetitions,
            'status': 'FEW_CLUSTERS' if len(groups) < 10 else 'OK',
            'percentile_95': {key: {'lower': float(np.quantile(values, .025)),
                                    'upper': float(np.quantile(values, .975)),
                                    'valid_repetitions': len(values)} if values else None
                              for key, values in results.items()}}
