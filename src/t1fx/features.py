"""Versioned, observable features shared by training and saved-model inference."""

from collections import Counter
from datetime import datetime
import math

import numpy as np

from t1fx.domain import Snapshot
from t1fx.snapshots import score_rules
from t1fx.storage import digest

STAGES = ('EXECUTED', 'ALLOCATED', 'CONFIRMED', 'AFFIRMED', 'INSTRUCTED')
SSI = ('UNKNOWN', 'MISMATCH', 'VALID')
FEATURES = (
    'log_notional_usd', 'log_remaining_usd', 'cash_coverage',
    'minutes_to_affirmation', 'minutes_to_deadline', 'observed_settled',
    *(f'instruction_{stage.lower()}' for stage in STAGES),
    *(f'ssi_{state.lower()}' for state in SSI),
    'counterparty_fail_rate', 'log_counterparty_count', 'counterparty_cold_start',
)
FEATURE_SCHEMA = {
    'version': 'risk-features-v1', 'columns': list(FEATURES), 'currency': 'USD',
    'reference_policy_id': 'fixed-0',
    'units': ['log1p_USD', 'log1p_USD', 'ratio_clipped_0_10', 'minutes', 'minutes',
              *(['indicator'] * 9), 'probability', 'log1p_count', 'indicator'],
    'history': 'Training-cohort outcomes known strictly before as_of; 10 prior observations; cold prior 0.5.',
    'categorical_mappings': {'instruction_state': list(STAGES), 'ssi_status': list(SSI)},
}
FEATURE_HASH = digest(FEATURE_SCHEMA)


def timestamp(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError('Feature timestamps must be timezone-aware')
    return result


def as_snapshot(row):
    keys = set(Snapshot.model_fields) - {'bucket'}
    data = {key: value for key, value in row.items() if key in keys}
    data['bucket'] = row.get('bucket', {'cash_pool_id': row.get('cash_pool_id'),
                                      'currency': row.get('currency')})
    return Snapshot.model_validate(data)


def feature_row(row, trade, history):
    """History is never derived from future outcomes or simulation-truth columns."""
    snapshot = as_snapshot(row)
    currency = trade.get('currency', trade.get('bucket', {}).get('currency'))
    if currency != 'USD' or snapshot.bucket.currency != 'USD':
        raise ValueError('Risk model supports USD settlement only')
    if row.get('reference_policy_id') != 'fixed-0':
        raise ValueError('Risk model requires fixed-0 reference-policy snapshots')
    if snapshot.trade_id != trade['trade_id']:
        raise ValueError('Snapshot/trade identity mismatch')
    pool = trade.get('cash_pool_id', trade.get('bucket', {}).get('cash_pool_id'))
    if (snapshot.bucket.cash_pool_id != pool
            or snapshot.horizon_at != timestamp(trade['contractual_deadline_at'])
            or timestamp(trade['executed_at']) > snapshot.as_of):
        raise ValueError('Snapshot disagrees with the trade contract')
    if not timestamp(trade['available_at']) <= snapshot.as_of < snapshot.horizon_at:
        raise ValueError('Trade is unavailable or snapshot is beyond the supported horizon')
    if snapshot.instruction_state not in STAGES or snapshot.ssi_status not in SSI:
        raise ValueError('Unsupported observable lifecycle state')
    visible = [h for h in history if timestamp(h['known_at']) < snapshot.as_of
               and h['trade_id'] != snapshot.trade_id]
    prior = sum(h['failed'] for h in visible)/len(visible) if visible else 0.5
    cp = [h for h in visible if h['counterparty_id'] == trade['counterparty_id']]
    rate = (sum(h['failed'] for h in cp) + 10*prior)/(len(cp)+10)
    amount = trade['settlement_amount_minor']
    if type(amount) is not int or amount <= 0:
        raise ValueError('Trade notional must be positive integer minor units')
    values = [math.log1p(amount/100), math.log1p(snapshot.remaining_obligation_minor/100),
              min(10, max(0, snapshot.pool_usable_minor/max(1, snapshot.remaining_obligation_minor))),
              snapshot.minutes_to_affirmation_cutoff, snapshot.minutes_to_settlement_deadline,
              float(snapshot.observed_settled),
              *[float(snapshot.instruction_state == stage) for stage in STAGES],
              *[float(snapshot.ssi_status == state) for state in SSI],
              rate, math.log1p(len(cp)), float(not cp)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Nonfinite model feature')
    return values


def matrix(rows, trades, history):
    return np.asarray([feature_row(row, trades[row['trade_id']], history) for row in rows],
                      dtype=np.float64).reshape((-1, len(FEATURES)))


def snapshot_weights(rows):
    counts = Counter(row['trade_id'] for row in rows)
    return np.asarray([1/counts[row['trade_id']] for row in rows])


def rule_scores(rows):
    return np.asarray([score_rules(as_snapshot(row)).score/100 for row in rows])


def fit_preprocessing(x, weights):
    mean = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x-mean)**2, axis=0, weights=weights))
    scale[scale < 1e-12] = 1
    return {'feature_schema_hash': FEATURE_HASH, 'mean': mean.tolist(), 'scale': scale.tolist()}


def transform(x, preprocessing):
    if preprocessing['feature_schema_hash'] != FEATURE_HASH:
        raise ValueError('Incompatible feature schema')
    mean, scale = np.asarray(preprocessing['mean']), np.asarray(preprocessing['scale'])
    if mean.shape != (len(FEATURES),) or scale.shape != mean.shape or np.any(scale <= 0):
        raise ValueError('Invalid preprocessing dimensions or scale')
    if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(scale)):
        raise ValueError('Nonfinite preprocessing')
    return (x-mean)/scale
