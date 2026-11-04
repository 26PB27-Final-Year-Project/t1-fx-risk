"""Prepare the complete local research workflow with chronologically separate training."""

from datetime import date, timedelta
from pathlib import Path

from t1fx.datasets import generate_release
from t1fx.fixtures import demo_scenario
from t1fx.generation import load_config
from t1fx.pipeline import run_comparison
from t1fx.storage import RunStore, canonical_bytes
from t1fx.timing import train_timing
from t1fx.training import train_model


def bootstrap_demo(root: Path, trade_count=50, seed=42, budget_minor=3_000_000):
    scenario = demo_scenario(trade_count, seed)
    today = date.today()
    first = today.replace(day=1)
    training_start = first - timedelta(days=180)
    config = load_config(Path('configs/generation/smoke.json'))
    coverage = {'valid_from': date(training_start.year, 1, 1),
                'valid_to': date(today.year+1, 12, 31)}
    market = config.market.model_copy(update=coverage)
    while not market.is_business_day(training_start):
        training_start += timedelta(days=1)
    routes = tuple(route.model_copy(update={
        'sell_calendar': route.sell_calendar.model_copy(update=coverage),
        'buy_calendar': route.buy_calendar.model_copy(update=coverage),
    }) for route in config.fx_routes)
    config = config.model_copy(update={
        'config_id': f'eur-training-{first:%Y-%m}', 'start_date': training_start,
        'seed': seed, 'market': market, 'fx_routes': routes,
    })
    dataset = generate_release(config, root, 'auto')
    risk = train_model(root, dataset['dataset_id'])
    timing = train_timing(root, dataset['dataset_id'])
    comparison = run_comparison(scenario, budget_minor, RunStore(root),
                                timing_model_id=timing['timing_model_id'])
    output = {'dataset_id': dataset['dataset_id'], 'model_id': risk['model_id'],
              'timing_model_id': timing['timing_model_id'], **comparison}
    (root/'demo-summary.json').write_bytes(canonical_bytes(output))
    return output
