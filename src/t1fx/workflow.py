"""Typed, recoverable local experiment preparation using immutable existing stores."""
import csv
import io
import json
import re
from functools import lru_cache
from copy import deepcopy
from threading import RLock
from datetime import date, datetime, UTC, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from t1fx.datasets import dataset_manifest, validate_dataset
from t1fx.domain import Scenario
from t1fx.generation import GenerationConfig, load_config
from t1fx.storage import RunStore
from t1fx.online_clock import online_time

ID = r'^dataset-[0-9a-f]{20}$'
SCENARIO = r'^[a-zA-Z0-9_.-]{1,160}$'

def recommended_start(today: date, trading_days: int = 10) -> date:
    """Resolve a recent historical window with enough room for mature labels."""
    config = load_config(Path(__file__).resolve().parents[2]/'configs/generation/smoke.json')
    remaining = trading_days + 4*(config.recovery_business_days+2) + 1
    candidate = today
    while remaining:
        candidate -= timedelta(days=1)
        if config.market.is_business_day(candidate):
            remaining -= 1
    return candidate


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['generate', 'train-risk', 'train-timing', 'train-both', 'prepare', 'compare', 'sensitivity']
    dataset_id: str | None = Field(default=None, pattern=ID)
    scenario_id: str | None = Field(default=None, pattern=SCENARIO)
    risk_model_id: str | None = Field(default=None, pattern=r'^model-[0-9a-f]{20}$')
    timing_model_id: str | None = Field(default=None, pattern=r'^timing-[0-9a-f]{20}$')
    mode: Literal['quick', 'research'] = 'research'
    start_date: date | None = None
    trade_count: int = Field(default=1000, ge=80, le=20000, strict=True)
    trading_days: int = Field(default=10, ge=4, le=40, strict=True)
    seed: int = Field(default=42, ge=0, le=2**32-1, strict=True)
    opening_cash_fraction: Decimal = Field(default=Decimal('.95'), ge=0, le=1)
    funding_stress_rate: float = Field(default=.1, ge=0, le=1)
    late_confirmation_rate: float = Field(default=.08, ge=0, le=1)
    ssi_mismatch_rate: float = Field(default=.06, ge=0, le=1)
    budget: Decimal = Field(default=Decimal('30000'), ge=0, le=100000000, decimal_places=2)
    currency: Literal['USD'] = 'USD'
    scenario_count: int = Field(default=200, ge=1, le=500, strict=True)
    scenario_seed: int = Field(default=42, ge=0, le=2**32-1, strict=True)
    common_shock_weight: float = Field(default=.5, ge=0, le=1)
    budgets: list[Decimal] = Field(default_factory=lambda: [Decimal(0), Decimal(15000), Decimal(30000)], min_length=1, max_length=6)

    @model_validator(mode='before')
    @classmethod
    def ignore_unused_generation_fields(cls, data):
        if isinstance(data, dict) and data.get('dataset_id') and data.get('kind') != 'generate':
            data = dict(data)
            for key in ('start_date', 'trade_count', 'trading_days', 'seed', 'opening_cash_fraction',
                        'funding_stress_rate', 'late_confirmation_rate', 'ssi_mismatch_rate'):
                data.pop(key, None)
        return data

    @model_validator(mode='after')
    def coherent(self):
        if self.kind == 'generate' and self.dataset_id is not None:
            raise ValueError('Dataset generation creates a new release; omit dataset_id')
        if self.trade_count < self.trading_days:
            raise ValueError('Use at least one trade per trading day')
        if self.kind in ('train-risk', 'train-timing', 'train-both', 'compare', 'sensitivity') and not self.dataset_id:
            raise ValueError('Select a dataset')
        if self.kind in ('compare', 'sensitivity') and not self.scenario_id:
            raise ValueError('Select a held-out evaluation scenario')
        if any(not b.is_finite() or b < 0 or b > 100000000 or b*100 != (b*100).to_integral_value() for b in self.budgets):
            raise ValueError('Use up to six nonnegative USD budgets with at most two decimals')
        return self


def generation_config(request, *, today=None):
    config = load_config(Path(__file__).resolve().parents[2]/'configs/generation/smoke.json')
    data = config.model_dump() | {key: getattr(request, key) for key in (
        'start_date','trade_count','trading_days','seed','opening_cash_fraction',
        'funding_stress_rate','late_confirmation_rate','ssi_mismatch_rate')}
    today = today or datetime.fromisoformat(online_time()['utc_now']).astimezone(UTC).date()
    data['start_date'] = request.start_date or recommended_start(today, request.trading_days)
    config = GenerationConfig.model_validate(data)
    # Validate the same schedule used by preview and generation, not a padded estimate.
    from t1fx.generation import generation_schedule
    from t1fx.calendars import local_instant
    for day, _, _ in generation_schedule(config):
        settlement = config.market.settlement_date(day)
        end_day = config.market.advance_settlement(settlement, config.recovery_business_days)
        end = config.market.cutoff_at(end_day, 'settlement')
        if end.date() >= today:
            raise ValueError('Choose an earlier start date: all partitions and recovery gaps must finish before today')
        start = local_instant(day, config.arrival_open, config.market.timezone)
        close = local_instant(day, min(config.arrival_close, config.market.trading_early_closes.get(day, config.arrival_close)), config.market.timezone)
        if close <= start:
            raise ValueError('Trading close must be after the execution window opens')
        decision = local_instant(settlement, config.decision_time, config.market.timezone)
        window_day = settlement
        while True:
            opening = local_instant(window_day, config.settlement_open, config.market.timezone)
            cutoff = config.market.cutoff_at(window_day, 'settlement')
            if opening >= cutoff or (window_day == settlement and decision >= opening):
                raise ValueError('Early-close override is incompatible with the configured decision window')
            if window_day == end_day:
                break
            window_day = config.market.advance_settlement(window_day, 1)
        for route in config.fx_routes:
            for calendar in (route.sell_calendar, route.buy_calendar):
                calendar.require_coverage(start.astimezone(ZoneInfo(route.timezone)).date())
                calendar.require_coverage(end.astimezone(ZoneInfo(route.timezone)).date())
            # Validate normal and missed-cutoff bookings at both execution boundaries.
            for executed in (start, close):
                booked = executed + timedelta(minutes=10)
                route_day = booked.astimezone(ZoneInfo(route.timezone)).date()
                missed = max(booked, local_instant(route_day, route.booking_cutoff, route.timezone)+timedelta(minutes=30))
                route.expected_credit(booked)
                route.expected_credit(missed)

    return config


def generation_preview(request, *, today=None):
    """Resolve deadlines without generating trades or contacting FX providers."""
    from t1fx.generation import generation_schedule
    from t1fx.calendars import local_instant
    config = generation_config(request, today=today)
    rows = []
    for day, count, split in generation_schedule(config):
        settlement = config.market.settlement_date(day)
        end = config.market.advance_settlement(settlement, config.recovery_business_days)
        rows.append({'partition': split, 'trade_count': count, 'execution_date': day.isoformat(),
            'decision_at': local_instant(settlement, config.decision_time, config.market.timezone).isoformat(),
            'deadline_at': config.market.cutoff_at(settlement, 'settlement').isoformat(),
            'labels_mature_at': config.market.cutoff_at(end, 'settlement').isoformat()})
    return {'timezone': config.market.timezone, 'currency': config.fx_routes[0].buy_currency,
            'schedule': rows, 'configuration': config.model_dump(mode='json')}


@lru_cache(maxsize=32)
def _parsed_dataset(directory: Path, manifest_json: str):
    """Reuse parsing only AFTER the caller has verified all artifact bytes."""
    manifest = json.loads(manifest_json)
    trades = json.loads((directory/manifest['tables']['trades']['json']).read_text())
    ids = sorted({t['scenario_id'] for t in trades if t['split']=='test'})
    if any(not re.fullmatch(SCENARIO, i) or f'scenarios/{i}.json' not in manifest['files'] for i in ids):
        raise ValueError('Invalid dataset scenario reference')
    scenarios = [Scenario.model_validate_json((directory/'scenarios'/f'{i}.json').read_text()) for i in ids]
    config = json.loads((directory/'resolved_config.json').read_text())
    outcomes = json.loads((directory/manifest['tables']['outcomes']['json']).read_text())
    balance = {split: {'failed': 0, 'total': 0} for split in ('train','tuning','calibration','test')}
    for row in outcomes:
        balance[row['split']]['total'] += 1
        balance[row['split']]['failed'] += bool(row['missed_contractual_deadline'])
    entry = {'outcome_balance': balance, 'dataset_id': manifest['dataset_id'],
        'name': f"{manifest['summary']['trade_count']} trades · {config['start_date']} · seed {config['seed']}",
        'summary': manifest['summary'], 'configuration': config,
        'scenarios': [{'scenario_id': s.scenario_id, 'decision_at': s.decision_at.isoformat(),
            'deadline_at': max(t.contractual_deadline_at for t in s.trades).isoformat(),
            'ends_at': s.ends_at.isoformat(),
            'name': f"{s.trades[0].executed_at.date()} · {len(s.trades)} trades · USD", 'currency': 'USD'} for s in scenarios]}
    return scenarios, entry


# Prevent simultaneous catalog/eligibility requests from duplicating cold validation.
_DATASET_READ_LOCK = RLock()


def _verified_dataset(root, dataset_id):
    with _DATASET_READ_LOCK:
        manifest = validate_dataset(root, dataset_id, reuse_verified=True)
        parsed = _parsed_dataset((Path(root)/'datasets'/dataset_id).resolve(), json.dumps(manifest, sort_keys=True))
        return manifest, parsed


def dataset_scenarios(root, dataset_id):
    manifest, (scenarios, _) = _verified_dataset(root, dataset_id)
    # Solvers and callers must never mutate shared cached scenario objects.
    return manifest, [scenario.model_copy(deep=True) for scenario in scenarios]


def dataset_catalog_entry(root, dataset_id):
    manifest = dataset_manifest(root, dataset_id)
    _, entry = _parsed_dataset((Path(root)/'datasets'/dataset_id).resolve(), json.dumps(manifest, sort_keys=True))
    return deepcopy(entry)


def ensure_timing_compatible(root, model_id, scenario):
    from t1fx.timing import load_model
    model, _ = load_model(root, model_id)
    if scenario.starts_at <= model.trained_through:
        raise ValueError('Timing training labels must mature before the evaluation scenario starts')
    missing = {f'{leg.sell_currency}/{leg.bucket.currency}' for leg in scenario.fx_legs} - model.delays_seconds.keys()
    if missing:
        raise ValueError('Timing model lacks routes: '+', '.join(sorted(missing)))
    return model


def compatibility(root, request, *, today=None):
    """The same integrity/runtime/maturity checks gate eligibility and execution."""
    reasons = []
    try:
        if not request.dataset_id:
            generation_config(request, today=today)
            return {'eligible': True, 'reasons': []}
        _, scenarios = dataset_scenarios(root, request.dataset_id)
        return _scenario_compatibility(root, request, scenarios, {})
    except (ValueError, RuntimeError, OSError, ImportError, KeyError) as exc:
        reasons.append(str(exc))
    return {'eligible': not reasons, 'reasons': reasons}


def _scenario_compatibility(root, request, scenarios, replay):
    reasons = []
    try:
        if request.kind in ('compare','sensitivity','prepare'):
            scenario = next((s for s in scenarios if s.scenario_id == request.scenario_id),
                            scenarios[0] if request.kind=='prepare' and request.scenario_id is None and scenarios else None)
            if scenario is None:
                raise ValueError('Choose a held-out test scenario from this dataset')
            if scenario.decision_at > datetime.now(UTC):
                raise ValueError('Evaluation time cannot be in the future')
            if {t.bucket.currency for t in scenario.trades} != {request.currency}:
                raise ValueError('Evaluation settlement currency must match the USD budget')
            if request.risk_model_id and request.kind != 'prepare':
                from t1fx.models import RiskModel
                from t1fx.simulator import simulate
                from t1fx.snapshots import build_snapshots
                model = RiskModel(root, request.risk_model_id)
                if 'rows' not in replay:
                    replay['rows'] = [s.model_dump(mode='json') | {'reference_policy_id':'fixed-0'} for s in build_snapshots(simulate(scenario, until=scenario.decision_at),scenario.decision_at)]
                model.predict(replay['rows'], {t.trade_id:t.model_dump(mode='json') for t in scenario.trades})
            if request.timing_model_id and request.kind != 'prepare':
                ensure_timing_compatible(root,request.timing_model_id,scenario)
    except (ValueError, RuntimeError, OSError, ImportError, KeyError) as exc:
        reasons.append(str(exc))
    return {'eligible': not reasons, 'reasons': reasons}



class ModelChoicesRequest(WorkflowRequest):
    kind: Literal['compare'] = 'compare'
    risk_model_ids: list[Annotated[str, Field(pattern=r'^model-[0-9a-f]{20}$')]] = Field(default_factory=list, max_length=100)
    timing_model_ids: list[Annotated[str, Field(pattern=r'^timing-[0-9a-f]{20}$')]] = Field(default_factory=list, max_length=100)


def model_choices(root, request):
    ids = [(id, 'risk_model_id') for id in request.risk_model_ids] + [(id, 'timing_model_id') for id in request.timing_model_ids]
    try:
        _, scenarios = dataset_scenarios(root, request.dataset_id)
    except (ValueError, RuntimeError, OSError, ImportError, KeyError) as exc:
        return {id: [str(exc)] for id, _ in ids}
    replay = {}
    result = {}
    for id, field in ids:
        choice = request.model_copy(update={'risk_model_id': None, 'timing_model_id': None, field: id})
        check = _scenario_compatibility(root, choice, scenarios, replay)
        result[id] = check['reasons']
    return result


def execute_job(root, payload, checkpoint, progress):
    from t1fx.generation import FX_CACHE
    token=FX_CACHE.set(root/'fx_cache')
    try:
        return _execute_job(root,payload,checkpoint,progress)
    finally:
        FX_CACHE.reset(token)


def _execute_job(root, payload, checkpoint, progress):
    from t1fx.datasets import generate_release
    from t1fx.models import ModelStore
    from t1fx.pipeline import run_comparison, _code_hash
    from t1fx.timing import train_timing, load_model
    from t1fx.training import train_model
    request = WorkflowRequest.model_validate(payload)
    outputs = dict((checkpoint or {}).get('outputs', {}))
    dataset_id = request.dataset_id or outputs.get('dataset_id')
    resolved_generation = None
    if not dataset_id:
        # Resolve once against the same online clock provider used by submission.
        # Retrying completed generation uses its immutable output, not new dates.
        resolved_generation = generation_config(request)
        request = request.model_copy(update={'start_date': resolved_generation.start_date})
    else:
        check = compatibility(root, request.model_copy(update={'dataset_id': dataset_id}))
        if not check['eligible']:
            raise ValueError('; '.join(check['reasons']))
    def save(stage, key, action):
        progress(stage, outputs)
        if key not in outputs:
            outputs[key] = action()
        if key=='dataset_id':
            validate_dataset(root,outputs[key], reuse_verified=True)
        elif key=='risk_model_id':
            from t1fx.models import RiskModel
            RiskModel(root,outputs[key])
        elif key=='timing_model_id':
            load_model(root,outputs[key])
        elif key.startswith('comparison_'):
            for run in outputs[key]['runs']:
                RunStore(root).validate(run['run_id'])
        progress(stage+' completed', outputs)
        return outputs[key]
    if request.kind in ('generate','prepare') and not dataset_id:
        dataset_id = save('Generating dataset', 'dataset_id', lambda: generate_release(resolved_generation,root,'auto')['dataset_id'])
    validate_dataset(root,dataset_id, reuse_verified=True)
    outputs['dataset_id'] = dataset_id
    # Record the selected dataset's actual generation settings, rather than UI drafts/defaults.
    saved_config = json.loads((root/'datasets'/dataset_id/'resolved_config.json').read_text())
    generation_fields = {key: saved_config[key] for key in (
        'trade_count', 'trading_days', 'seed', 'opening_cash_fraction', 'funding_stress_rate',
        'late_confirmation_rate', 'ssi_mismatch_rate')}
    generation_fields['start_date'] = date.fromisoformat(saved_config['start_date'])
    generation_fields['opening_cash_fraction'] = Decimal(generation_fields['opening_cash_fraction'])
    request = request.model_copy(update=generation_fields)
    if request.kind in ('train-risk','train-both','prepare'):
        save('Training settlement risk model', 'risk_model_id', lambda: train_model(root,dataset_id)['model_id'])
    if request.kind in ('train-timing','train-both','prepare'):
        save('Training FX timing model', 'timing_model_id', lambda: train_timing(root,dataset_id)['timing_model_id'])
    if request.kind in ('generate','train-risk','train-timing','train-both'):
        return {'stage':'Completed','outputs':outputs}
    request = request.model_copy(update={'dataset_id':dataset_id,
        'risk_model_id':outputs.get('risk_model_id',request.risk_model_id),
        'timing_model_id':outputs.get('timing_model_id',request.timing_model_id)})
    _, scenarios = dataset_scenarios(root,dataset_id)
    scenario = next((s for s in scenarios if s.scenario_id==request.scenario_id),
                    scenarios[0] if request.scenario_id is None and scenarios else None)
    if scenario is None:
        raise ValueError('Choose a held-out test scenario from this dataset')
    request = request.model_copy(update={'scenario_id':scenario.scenario_id})
    check = compatibility(root, request.model_copy(update={'kind':'compare'}))
    if not check['eligible']:
        raise ValueError('; '.join(check['reasons']))
    store = RunStore(root)
    budgets = request.budgets if request.kind=='sensitivity' else [request.budget]
    for index,budget in enumerate(budgets):
        def compare():
            rates = store.read(outputs['reference_run'],'fx_rates.json') if 'reference_run' in outputs else None
            return run_comparison(scenario,int(budget*100),store,timing_model_id=request.timing_model_id,
                scenario_count=request.scenario_count, scenario_seed=request.scenario_seed,
                common_shock_weight=request.common_shock_weight, fx_rate_snapshot=rates)
        outputs['budget_progress'] = {'completed':sum(k.startswith('comparison_') for k in outputs),'total':len(budgets)}
        comparison = save(f'Comparing budget {index+1} of {len(budgets)}',f'comparison_{index}',compare)
        outputs.setdefault('reference_run',comparison['runs'][0]['run_id'])
        outputs['budget_progress']['completed'] = sum(k.startswith('comparison_') for k in outputs)
        progress('Budget comparisons',outputs)
    evaluation = {}
    if request.risk_model_id:
        artifacts = ModelStore(root).read(request.risk_model_id)
        evaluation['risk'] = {k:artifacts[k] for k in ('metrics.json','training_manifest.json','thresholds.json')}
    if request.timing_model_id:
        model,metrics = load_model(root,request.timing_model_id)
        evaluation['timing'] = {'model_id':request.timing_model_id,'trained_through':model.trained_through.isoformat(),'metrics':metrics}
    reference_manifest = store.manifest(outputs['reference_run'])
    from collections import Counter
    evidence={}
    for key,comparison in outputs.items():
        if key.startswith('comparison_'):
            for run in comparison['runs']:
                outcomes=store.read(run['run_id'],'outcomes.json')
                evidence[run['run_id']]={'policy_id':run['policy_id'],
                    'failure_reasons':dict(Counter(reason for outcome in outcomes for reason in outcome['failure_reasons'])),
                    'missed_deadlines':run['failed_count'],'replay_cost':run['total_cost']}
    record = {'replay_evidence':evidence,'configuration':request.model_dump(mode='json'),'outputs':outputs,
              'decision_at':scenario.decision_at.isoformat(), 'dataset_manifest':validate_dataset(root,dataset_id, reuse_verified=True),
              'code_hash':_code_hash(),'dependencies':reference_manifest['environment'],
              'model_evaluation':evaluation,
              'limitations':['Synthetic data and assumed costs.','Risk probabilities describe zero extra reserve; they do not drive the LP.',
                 'Timing dependence is an assumed common-quantile mixture.','Repeated timing draws are not independent test days.'],
              'reference_rates_run_id':outputs['reference_run']}
    record_id = RunStore(root/'experiments').publish({'experiment.json':record}, {'kind':'experiment'})
    outputs['experiment_id'] = record_id
    return {'stage':'Completed','outputs':outputs}


def export_record(root, record_id, format):
    record = RunStore(root/'experiments').read(record_id,'experiment.json')
    if format=='json':
        from t1fx.storage import canonical_bytes
        return canonical_bytes(record)
    rows = [r for key,c in record['outputs'].items() if key.startswith('comparison_') for r in c['runs']]
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output,fieldnames=list(rows[0]) if rows else [])
    writer.writeheader(); writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')
