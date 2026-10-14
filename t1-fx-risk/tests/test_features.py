from copy import deepcopy

import numpy as np
import pytest

from t1fx.features import FEATURES, feature_row, fit_preprocessing, snapshot_weights, transform
from t1fx.fixtures import three_trade_scenario
from t1fx.simulator import simulate
from t1fx.snapshots import build_snapshots


def observation():
    scenario = three_trade_scenario()
    snapshot = build_snapshots(simulate(scenario), scenario.decision_at)[0]
    trade = next(t for t in scenario.trades if t.trade_id == snapshot.trade_id)
    return snapshot.model_dump(mode='json') | {'reference_policy_id': 'fixed-0'}, trade.model_dump(mode='json')


def test_history_is_point_in_time_and_smoothed_by_hand():
    row, trade = observation()
    history = [
        {'trade_id': 'old-1', 'counterparty_id': trade['counterparty_id'], 'known_at': '2026-10-12T00:00:00Z', 'failed': 1},
        {'trade_id': 'old-2', 'counterparty_id': 'someone-else', 'known_at': '2026-10-12T00:00:00Z', 'failed': 0},
        {'trade_id': 'future', 'counterparty_id': trade['counterparty_id'], 'known_at': '2026-10-20T00:00:00Z', 'failed': 1},
    ]
    values = feature_row(row, trade, history)
    assert values[FEATURES.index('counterparty_fail_rate')] == pytest.approx(6/11)
    assert values[FEATURES.index('log_counterparty_count')] == pytest.approx(np.log(2))
    mutated = deepcopy(history)
    mutated[-1]['failed'] = 0
    mutated.append(history[0] | {'trade_id': 'boundary', 'known_at': row['as_of']})
    assert feature_row(row, trade, mutated) == values
    # Extra hidden columns are never read by the feature function.
    assert feature_row(row | {'late_confirmation': True, 'settled_at': '2099-01-01'}, trade, history) == values


def test_wrong_policy_and_unavailable_trade_are_rejected():
    row, trade = observation()
    with pytest.raises(ValueError, match='reference-policy'):
        feature_row(row | {'reference_policy_id': 'lp'}, trade, [])
    with pytest.raises(ValueError, match='unavailable'):
        feature_row(row, trade | {'available_at': '2099-01-01T00:00:00Z'}, [])
    with pytest.raises(ValueError, match='trade contract'):
        feature_row(row | {'horizon_at': '2099-01-01T00:00:00Z'}, trade, [])


def test_preprocessing_uses_trade_weights_and_constant_features():
    x = np.zeros((3, len(FEATURES)))
    x[:, 0] = [1, 3, 8]
    weights = snapshot_weights([{'trade_id': 'a'}, {'trade_id': 'a'}, {'trade_id': 'b'}])
    preprocessing = fit_preprocessing(x, weights)
    assert preprocessing['mean'][0] == 5
    assert np.all(np.isfinite(transform(x, preprocessing)))
