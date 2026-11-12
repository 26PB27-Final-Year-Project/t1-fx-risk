import csv
import json
from pathlib import Path

import pytest

from t1fx.datasets import generate_release
from t1fx.experiments import run_experiment
from t1fx.generation import load_config
from t1fx.timing import train_timing


@pytest.fixture
def dataset(tmp_path):
    config = load_config(Path('configs/generation/smoke.json')).model_copy(update={
        'trade_count': 80, 'trading_days': 4,
    })
    return generate_release(config, tmp_path, 'json')['dataset_id']


def test_experiment_exports_only_test_trades_and_matched_policy_results(tmp_path, dataset):
    result = run_experiment(tmp_path, [dataset], [0, 100000])
    directory = Path(result['directory'])
    report = json.loads((directory/'report.json').read_text())
    assert len(report['comparisons']) == 8
    assert len({r['scenario_id'] for r in report['comparisons']}) == 1
    assert all(r['failures_avoided_vs_no_reserve'] == 0 for r in report['comparisons'] if r['policy_id'] == 'fixed-0')
    with (directory/'trades.csv').open(encoding='utf-8-sig') as f:
        trades = list(csv.DictReader(f))
    assert len(trades) == 20
    assert all(t['trade_label'].startswith('Trade ') for t in trades)
    assert all(t['security_label'].startswith('Sample equity ') for t in trades)
    assert set(t['funding_currencies'] for t in trades) == {'EUR', 'CAD', 'GBP', 'JPY', 'INR'}
    assert run_experiment(tmp_path, [dataset], [0, 100000]) == result
    (directory/'trades.csv').write_text('corrupted')
    with pytest.raises(ValueError, match='changed'):
        run_experiment(tmp_path, [dataset], [0, 100000])


def test_experiment_distinguishes_forecast_seeds_from_data_seeds(tmp_path, dataset):
    with pytest.raises(ValueError, match='timing model'):
        run_experiment(tmp_path, [dataset], [1000], seeds=[7, 42])
    timing = train_timing(tmp_path, dataset)['timing_model_id']
    result = run_experiment(tmp_path, [dataset], [1000], timing_model_id=timing,
                            seeds=[7, 42], scenario_count=10)
    report = json.loads((Path(result['directory'])/'report.json').read_text())
    assert len(report['comparisons']) == 10
    assert len({row['policy_id'] for row in report['comparisons']}) == 5
    assert all(row['test_days'] == 1 and row['forecast_repeats'] == 2 for row in report['summary'])


def test_export_preserves_negative_cost_savings_without_allowing_formulas():
    import io
    from t1fx.experiments import csv_bytes
    raw = csv_bytes([{'cost_saved': '-12.50', 'trade_id': '=1+1', 'other_id': '-1+2'}])
    row = next(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    assert row['cost_saved'] == '-12.50'
    assert row['trade_id'] == "'=1+1"
    assert row['other_id'] == "'-1+2"


def test_jpy_research_export_preserves_whole_yen(tmp_path):
    from decimal import Decimal
    config = load_config(Path('configs/generation/smoke.json'))
    route = config.fx_routes[0].model_copy(update={'sell_currency':'EUR','buy_currency':'JPY'})
    config = config.model_copy(update={'trade_count':80,'trading_days':4,'fx_routes':(route,)})
    manifest = generate_release(config,tmp_path,'json')
    directory = tmp_path/'datasets'/manifest['dataset_id']
    originals = {r['trade_id']:r['settlement_amount_minor'] for r in json.loads((directory/'tables/trades.json').read_text())}
    result = run_experiment(tmp_path,[manifest['dataset_id']],[0])
    with (Path(result['directory'])/'trades.csv').open(encoding='utf-8-sig') as stream:
        rows=list(csv.DictReader(stream))
    assert rows
    assert all(Decimal(r['payment_due']) == originals[r['trade_id']] for r in rows)
