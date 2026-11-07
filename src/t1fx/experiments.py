"""Held-out policy comparisons and portable, checksum-protected research reports."""
import csv
import hashlib
import html
import io
import json
import os
import shutil
import tempfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from t1fx.datasets import validate_dataset
from t1fx.domain import Scenario
from t1fx.pipeline import _code_hash, run_comparison
from t1fx.presentation import trade_labels
from t1fx.storage import RunStore, canonical_bytes, digest


def csv_bytes(rows):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else [])
    writer.writeheader()
    for row in rows:
        # Treat identifiers as text even if an imported record resembles a spreadsheet formula.
        writer.writerow({key: "'"+value if isinstance(value, str) and value.startswith(('=', '+', '-', '@'))
                         else value for key, value in row.items()})
    return output.getvalue().encode('utf-8-sig')


def publish_report(root: Path, identity: dict, files: dict[str, bytes]):
    checksums = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    experiment_id = 'experiment-' + digest({'identity': identity, 'files': checksums})[:20]
    target = root / 'research' / experiment_id
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'experiment_id': experiment_id, 'identity': identity, 'files': checksums}
    if target.exists():
        if json.loads((target/'manifest.json').read_text()) != manifest:
            raise ValueError('Existing experiment manifest differs')
        for name, checksum in checksums.items():
            if hashlib.sha256((target/name).read_bytes()).hexdigest() != checksum:
                raise ValueError(f'Existing experiment file has changed: {name}')
    else:
        staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=target.parent))
        try:
            for name, raw in files.items():
                (staging/name).write_bytes(raw)
            (staging/'manifest.json').write_bytes(canonical_bytes(manifest))
            os.rename(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {'experiment_id': experiment_id, 'directory': str(target), 'files': list(files)}


def run_experiment(root: Path, dataset_ids: list[str], budgets: list[int], *,
                   timing_model_id: str | None = None, seeds=(42,), scenario_count=200):
    if not dataset_ids or not budgets or any(b < 0 for b in budgets):
        raise ValueError('Supply datasets and nonnegative budgets')
    if not seeds or any(s < 0 for s in seeds) or not 1 <= scenario_count <= 10000:
        raise ValueError('Use nonnegative seeds and a scenario count from 1 to 10000')
    if timing_model_id is None and len(set(seeds)) > 1:
        raise ValueError('Multiple forecast seeds require a timing model; they do not regenerate trades')
    datasets = sorted(set(dataset_ids))
    budgets, seeds = sorted(set(budgets)), sorted(set(seeds))
    store = RunStore(root)
    rows, trades_export = [], []
    model = None
    if timing_model_id:
        from t1fx.timing import load_model
        model, _ = load_model(root, timing_model_id)
    # Validate every input and chronology before publishing comparisons.
    scenarios = []
    for dataset_id in datasets:
        manifest = validate_dataset(root, dataset_id)
        directory = root/'datasets'/dataset_id
        trades = json.loads((directory/manifest['tables']['trades']['json']).read_text())
        test_ids = sorted({trade['scenario_id'] for trade in trades if trade['split'] == 'test'})
        for scenario_id in test_ids:
            scenario = Scenario.model_validate_json((directory/'scenarios'/f'{scenario_id}.json').read_text())
            if model and model.trained_through >= scenario.starts_at:
                raise ValueError('Timing training labels must mature before each test scenario starts')
            scenarios.append((dataset_id, scenario))
    for dataset_id, scenario in scenarios:
        for trade in scenario.trades:
            record = trade.model_dump(mode='json')
            legs = [leg for leg in scenario.fx_legs if leg.trade_id == trade.trade_id]
            trades_export.append({'dataset_id': dataset_id, 'scenario_id': scenario.scenario_id,
                'trade_id': trade.trade_id, **trade_labels(record), 'side': 'Buy securities',
                'settlement_currency': trade.bucket.currency,
                'payment_due': str(Decimal(trade.settlement_amount_minor)/100),
                'execution_utc': trade.executed_at.isoformat(),
                'payment_deadline_utc': trade.contractual_deadline_at.isoformat(),
                'funding_currencies': ', '.join(sorted({leg.sell_currency for leg in legs})),
                'cash_pool_id': trade.bucket.cash_pool_id})
        rates = None
        for budget in budgets:
            for seed in seeds:
                comparison = run_comparison(scenario, budget, store, timing_model_id=timing_model_id,
                    scenario_seed=seed, scenario_count=scenario_count, fx_rate_snapshot=rates)
                rates = store.read(comparison['runs'][0]['run_id'], 'fx_rates.json')
                baseline = next(run for run in comparison['runs'] if run['policy_id'] == 'fixed-0')
                for run in comparison['runs']:
                    rows.append({'dataset_id': dataset_id, 'scenario_id': scenario.scenario_id,
                        'forecast_seed': seed, **run,
                        'failures_avoided_vs_no_reserve': baseline['failed_count']-run['failed_count'],
                        'cost_saved_vs_no_reserve': str(Decimal(baseline['total_cost'])-Decimal(run['total_cost']))})
    if not rows:
        raise ValueError('No test scenarios found')
    groups = defaultdict(list)
    for row in rows:
        groups[(row['currency'], row['budget_limit'], row['policy_id'])].append(row)
    summary = []
    for (currency, budget, policy), group in sorted(groups.items()):
        summary.append({'currency': currency, 'budget_limit': budget, 'policy_id': policy,
            'test_days': len({(r['dataset_id'], r['scenario_id']) for r in group}),
            'forecast_repeats': len(seeds),
            'mean_failed_count': sum(r['failed_count'] for r in group)/len(group),
            'mean_failures_avoided': sum(r['failures_avoided_vs_no_reserve'] for r in group)/len(group),
            'mean_total_cost': str(sum((Decimal(r['total_cost']) for r in group), Decimal(0))/len(group)),
            'mean_cost_saved': str(sum((Decimal(r['cost_saved_vs_no_reserve']) for r in group), Decimal(0))/len(group))})
    identity = {'dataset_ids': datasets, 'budgets_minor': budgets, 'timing_model_id': timing_model_id,
                'forecast_seeds': seeds, 'scenario_count': scenario_count, 'split': 'test',
                'code_hash': _code_hash(), 'common_shock_weight': 0.5}
    report = {'identity': identity, 'summary': summary, 'comparisons': rows,
              'limitations': 'Synthetic outcomes and assumed costs. Forecast seeds are repeated draws on the same trades, not independent market days. Descriptive means only; no confidence intervals or real-world accuracy claims.'}
    headers = list(summary[0])
    table = '<tr>'+''.join(f'<th scope="col">{html.escape(h.replace("_", " "))}</th>' for h in headers)+'</tr>'
    table += ''.join('<tr>'+''.join(f'<td>{html.escape(str(row[h]))}</td>' for h in headers)+'</tr>' for row in summary)
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Settlement research report</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;margin:32px;color:#172438;background:#fafbfc}}main{{max-width:1200px;margin:auto}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #ccd3dd;font-variant-numeric:tabular-nums}}th{{background:#eaf0f9}}p{{max-width:80ch}}a{{color:#164cc0}}</style><main><h1>Settlement policy comparison</h1><p>{html.escape(report['limitations'])}</p><p>Each policy sees the same trades and observed information within its comparison. Positive savings mean fewer failures or lower cost than no additional reserve. Budgets and costs are in the currency shown. Means are per test day and forecast repeat.</p><p><a href="comparisons.csv">Detailed results (CSV)</a> · <a href="summary.csv">Summary (CSV)</a> · <a href="trades.csv">Readable test trades (CSV)</a> · <a href="report.json">Full report (JSON)</a></p><div class="scroll"><table>{table}</table></div></main></html>'''
    return publish_report(root, identity, {'report.json': canonical_bytes(report),
        'comparisons.csv': csv_bytes(rows), 'summary.csv': csv_bytes(summary),
        'trades.csv': csv_bytes(trades_export), 'report.html': page.encode()})
