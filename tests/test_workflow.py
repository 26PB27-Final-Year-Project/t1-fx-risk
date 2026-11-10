import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from t1fx.api import create_app
from t1fx.jobs import JobStore,work_once
from t1fx.workflow import WorkflowRequest,compatibility,export_record


def test_typed_requests_reject_paths_and_invalid_budgets():
    for fields in ({'path':'/tmp/a'},{'kind':'shell'},{'trade_count':True},{'budgets':['NaN']},{'budgets':['1.001']}, {'scenario_id':'../../etc/passwd'}):
        with pytest.raises(ValidationError):
            WorkflowRequest.model_validate({'kind':'generate',**fields})


def test_heartbeat_queue_and_exclusive_claims(tmp_path,monkeypatch):
    client=TestClient(create_app(tmp_path)); jobs=JobStore(tmp_path)
    assert not client.get('/api/v1/workflow/health').json()['worker']['available']
    jobs.worker_ping('worker')
    assert client.get('/api/v1/workflow/health').json()['worker']['available']
    a=jobs.submit(None,WorkflowRequest(kind='generate'),'one')
    assert jobs.submit(None,WorkflowRequest(kind='generate'),'one')['job_id']==a['job_id']
    jobs.submit(None,WorkflowRequest(kind='generate',seed=3),'two')
    jobs.claim('a'); assert jobs.claim('b') is None
    assert jobs.cancel(a['job_id'])['status']=='CANCEL_REQUESTED'
    assert jobs.claim('b') is None
    jobs.finish(a['job_id'],'a')
    assert jobs.get(a['job_id'])['status']=='CANCELED'
    assert jobs.claim('b')


@pytest.fixture
def prepared(tmp_path_factory):
    root=tmp_path_factory.mktemp('guided')
    jobs=JobStore(root)
    job=jobs.submit(None,WorkflowRequest(kind='prepare',trade_count=1000,trading_days=10),'prepare')
    assert work_once(root)
    job=jobs.get(job['job_id'])
    assert job['status']=='SUCCEEDED',job
    return root,job['result']['outputs']


def test_real_training_preparation_export_and_compatibility(prepared):
    root,out=prepared;client=TestClient(create_app(root))
    catalog=client.get('/api/v1/workflow/catalog').json()
    assert len(catalog['datasets'])==1 and catalog['risk_models'] and catalog['timing_models']
    scenarios=catalog['datasets'][0]['scenarios']
    request=WorkflowRequest(kind='compare',dataset_id=out['dataset_id'],scenario_id=scenarios[0]['scenario_id'],risk_model_id=out['risk_model_id'],timing_model_id=out['timing_model_id'])
    assert compatibility(root,request)['eligible']
    assert not compatibility(root,request.model_copy(update={'scenario_id':'not-a-test-day'}))['eligible']
    assert not compatibility(root,request.model_copy(update={'kind':'prepare','scenario_id':'not-a-test-day'}))['eligible']
    record=json.loads(export_record(root,out['experiment_id'],'json'))
    assert record['configuration']['risk_model_id']==out['risk_model_id']
    assert len(record['outputs']['comparison_0']['runs'])==5
    assert 'budget_limit' in export_record(root,out['experiment_id'],'csv').decode()
    assert client.get(f"/api/v1/workflow/experiments/{out['experiment_id']}/export?format=json").headers['content-disposition']


def test_sensitivity_keeps_rates_and_draws_fixed(prepared):
    root,out=prepared;client=TestClient(create_app(root));jobs=JobStore(root)
    record=json.loads(export_record(root,out['experiment_id'],'json'))
    body=record['configuration']|{'kind':'sensitivity','budgets':['0','10000','20000']}
    # Record preparation configuration is normalized to its selected held-out scenario.
    response=client.post('/api/v1/workflow/jobs',json=body,headers={'Idempotency-Key':'sensitivity'})
    assert response.status_code==202,response.text
    assert work_once(root)
    result=jobs.get(response.json()['job_id']);assert result['status']=='SUCCEEDED',result
    from t1fx.storage import RunStore
    store=RunStore(root);rates=[];draws=[]
    for key,c in result['result']['outputs'].items():
        if key.startswith('comparison_'):
            run=c['runs'][0]['run_id'];rates.append(store.read(run,'fx_rates.json'));draws.append(store.read(run,'timing_forecasts.json')['arrivals'])
    assert len(rates)==3 and all(r==rates[0] for r in rates)
    assert all(d==draws[0] for d in draws)


def test_failed_stage_retry_preserves_dataset(tmp_path,monkeypatch):
    import t1fx.training
    real=t1fx.training.train_model
    monkeypatch.setattr(t1fx.training,'train_model',Mock(side_effect=RuntimeError('training failed deliberately')))
    client=TestClient(create_app(tmp_path));jobs=JobStore(tmp_path)
    response=client.post('/api/v1/workflow/jobs',json={'kind':'prepare'},headers={'Idempotency-Key':'initial'})
    assert response.status_code==202
    work_once(tmp_path);job=jobs.get(response.json()['job_id']);assert job['status']=='FAILED'
    dataset=job['result']['outputs']['dataset_id']
    monkeypatch.setattr(t1fx.training,'train_model',real)
    retry=client.post(f"/api/v1/workflow/jobs/{job['job_id']}/retry",json={},headers={'Idempotency-Key':'retry'})
    assert retry.status_code==202
    work_once(tmp_path);next_job=jobs.get(retry.json()['job_id'])
    assert next_job['status']=='SUCCEEDED',next_job
    assert next_job['result']['outputs']['dataset_id']==dataset
    assert len(list((tmp_path/'datasets').glob('dataset-*')))==1


def test_worker_heartbeat_expires_and_typed_checkpoint_survives_reclaim(tmp_path,monkeypatch):
    clock=[1000.0]
    monkeypatch.setattr('t1fx.jobs.time.time',lambda:clock[0])
    jobs=JobStore(tmp_path);jobs.worker_ping('a')
    assert jobs.worker_health()['available']
    job=jobs.submit(None,WorkflowRequest(kind='generate'),'typed')
    jobs.claim('old',lease_seconds=10)
    jobs.checkpoint(job['job_id'],'old','Dataset published',{'dataset_id':'dataset-'+'1'*20})
    clock[0]+=16
    assert not jobs.worker_health()['available']
    claimed=jobs.claim('new')
    assert claimed['result']['outputs']['dataset_id']=='dataset-'+'1'*20
    assert not jobs.finish(job['job_id'],'old')


def test_generation_cannot_silently_reuse_selected_dataset(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post('/api/v1/workflow/jobs', json={
        'kind': 'generate', 'dataset_id': 'dataset-'+'a'*20,
    }, headers={'Idempotency-Key': 'generate'})
    assert response.status_code == 422
    assert 'omit dataset_id' in response.json()['message']
    assert response.json()['details']['fields']


def test_retry_publishes_checkpoint_atomically_and_scopes_idempotency(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path)
    request = WorkflowRequest(kind='generate')
    first = jobs.submit(None, request, 'first')
    jobs.claim('owner')
    jobs.checkpoint(first['job_id'], 'owner', 'Saved', {'dataset_id': 'dataset-'+'a'*20})
    jobs.finish(first['job_id'], 'owner', error='Failed after saving')
    client = TestClient(create_app(tmp_path))
    original = JobStore.submit_payload
    claims = []
    def claim_immediately(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        claims.append(jobs.claim('worker'))
        return result
    monkeypatch.setattr(JobStore, 'submit_payload', claim_immediately)
    url = f"/api/v1/workflow/jobs/{first['job_id']}/retry"
    response = client.post(url, json={}, headers={'Idempotency-Key': 'retry'})
    assert response.status_code == 202
    assert claims[0]['result']['outputs']['dataset_id'] == 'dataset-'+'a'*20
    assert client.post(url, json={}, headers={'Idempotency-Key': 'retry'}).json()['job_id'] == response.json()['job_id']
    assert client.post(url, json={}, headers={'Idempotency-Key': 'first'}).status_code == 409
    jobs.finish(response.json()['job_id'], 'worker', error='Retry also failed')
    other_url = f"/api/v1/workflow/jobs/{response.json()['job_id']}/retry"
    assert client.post(other_url, json={}, headers={'Idempotency-Key': 'retry'}).status_code == 409
    assert client.post('/api/v1/workflow/jobs/missing/retry', json={}, headers={'Idempotency-Key': 'missing'}).status_code == 404


def test_cancel_after_generation_preserves_release_for_retry(tmp_path, monkeypatch):
    import t1fx.datasets
    real = t1fx.datasets.generate_release
    jobs = JobStore(tmp_path)
    job = jobs.submit(None, WorkflowRequest(kind='generate', trade_count=200, trading_days=4), 'generate')
    def generate_then_cancel(*args, **kwargs):
        result = real(*args, **kwargs)
        jobs.cancel(job['job_id'])
        return result
    monkeypatch.setattr(t1fx.datasets, 'generate_release', generate_then_cancel)
    work_once(tmp_path)
    stopped = jobs.get(job['job_id'])
    assert stopped['status'] == 'CANCELED'
    dataset_id = stopped['result']['outputs']['dataset_id']
    monkeypatch.setattr(t1fx.datasets, 'generate_release', Mock(side_effect=AssertionError('Must reuse saved release')))
    client = TestClient(create_app(tmp_path))
    response = client.post(f"/api/v1/workflow/jobs/{job['job_id']}/retry", json={}, headers={'Idempotency-Key': 'retry'})
    work_once(tmp_path)
    completed = jobs.get(response.json()['job_id'])
    assert completed['status'] == 'SUCCEEDED', completed
    assert completed['result']['outputs']['dataset_id'] == dataset_id


def test_current_date_defaults_use_online_day_and_mature_window(tmp_path, monkeypatch):
    from datetime import date
    from t1fx.workflow import recommended_start, generation_config
    monkeypatch.setattr('t1fx.api.online_time',lambda:{'utc_now':'2026-11-09T12:00:00+00:00','source':'test://clock'})
    client = TestClient(create_app(tmp_path))
    defaults = client.get('/api/v1/workflow/defaults?trading_days=10')
    assert defaults.status_code == 200
    assert defaults.json()['today'] == '2026-11-09'
    start = date.fromisoformat(defaults.json()['start_date'])
    assert start == recommended_start(date(2026,11,9),10)
    assert start != recommended_start(date(2026,11,16),10)
    config = generation_config(WorkflowRequest(kind='generate',start_date=start))
    assert config.market.advance(start,34) < date(2026,11,9)
    assert client.get('/api/v1/workflow/defaults?trading_days=100').status_code == 422


def test_schedule_preview_matches_generated_deadlines(tmp_path):
    from t1fx.generation import generate_dataset
    from t1fx.workflow import generation_config, generation_preview
    request = WorkflowRequest(kind='generate', trade_count=80, trading_days=4)
    preview = generation_preview(request)
    generated = list(generate_dataset(generation_config(request)))
    assert len(preview['schedule']) == len(generated) == 4
    assert sum(row['trade_count'] for row in preview['schedule']) == 80
    for row, day in zip(preview['schedule'], generated, strict=True):
        assert row['partition'] == day.split
        assert row['decision_at'] == day.scenario.decision_at.isoformat()
        assert row['deadline_at'] == day.scenario.trades[0].contractual_deadline_at.isoformat()
        assert row['labels_mature_at'] == day.scenario.ends_at.isoformat()
    client = TestClient(create_app(tmp_path))
    response = client.post('/api/v1/workflow/preview', json=request.model_dump(mode='json'))
    assert response.status_code == 200
    assert response.json() == preview
    assert not list((tmp_path/'datasets').glob('dataset-*'))


def test_preview_rejects_invalid_dates_and_counts(tmp_path):
    client = TestClient(create_app(tmp_path))
    for fields in ({'start_date':'2026-10-11'}, {'trade_count':0}, {'opening_cash_fraction':2}):
        response = client.post('/api/v1/workflow/preview', json={'kind':'generate', **fields})
        assert response.status_code == 422


def test_prepare_replaces_old_model_selections(prepared):
    root, out = prepared
    request = WorkflowRequest(kind='prepare', dataset_id=out['dataset_id'],
        risk_model_id='model-'+'0'*20, timing_model_id='timing-'+'0'*20)
    assert compatibility(root, request)['eligible']
    compare = request.model_copy(update={'kind':'compare', 'scenario_id':'invalid'})
    assert not compatibility(root, compare)['eligible']
