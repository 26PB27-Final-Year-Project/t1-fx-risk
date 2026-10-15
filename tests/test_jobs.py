from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from t1fx.api import create_app
from t1fx.fixtures import three_trade_scenario
from t1fx.jobs import JobConflict, JobStore, RecomputeRequest, work_once
from t1fx.pipeline import run_comparison
from t1fx.storage import RunStore


def test_idempotency_and_transactional_claims(tmp_path):
    store = JobStore(tmp_path)
    request = RecomputeRequest(liquidity_limit="50")
    job = store.submit("run-test", request, "key")
    assert JobStore(tmp_path).submit("run-test", RecomputeRequest(liquidity_limit="50.00"), "key") == job
    with pytest.raises(JobConflict):
        store.submit("run-test", RecomputeRequest(liquidity_limit="60"), "key")
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(store.claim, ["one", "two"]))
    assert sum(claim is not None for claim in claims) == 1
    assert store.get(job["job_id"])["attempts"] == 1


def test_restart_recovery_owner_fencing_and_cancellation(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("t1fx.jobs.time.time", lambda: clock[0])
    jobs = JobStore(tmp_path)
    job = jobs.submit("run-test", RecomputeRequest(liquidity_limit="50"), "key")
    jobs.claim("old", lease_seconds=10)
    clock[0] += 11
    assert not jobs.heartbeat(job["job_id"], "old")
    recovered = JobStore(tmp_path).claim("new", lease_seconds=10)
    assert recovered["attempts"] == 2
    assert not jobs.finish(job["job_id"], "old", result={"bad": True})
    assert jobs.heartbeat(job["job_id"], "new")
    assert jobs.cancel(job["job_id"])["status"] == "CANCELED"
    assert not jobs.finish(job["job_id"], "new", result={"late": True})
    assert jobs.claim("next") is None


def test_expired_crash_loop_fails_after_three_attempts(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("t1fx.jobs.time.time", lambda: clock[0])
    jobs = JobStore(tmp_path)
    job = jobs.submit("run-test", RecomputeRequest(liquidity_limit="50"), "key")
    for attempt in range(3):
        assert jobs.claim(str(attempt), lease_seconds=1)["attempts"] == attempt+1
        clock[0] += 2
    assert jobs.claim("fourth") is None
    assert jobs.get(job["job_id"])["status"] == "FAILED"


def test_api_worker_roundtrip_and_comparison_isolation(tmp_path):
    report = run_comparison(three_trade_scenario(), 3_000_000, RunStore(tmp_path))
    run_id = report["runs"][-1]["run_id"]
    client = TestClient(create_app(tmp_path))
    url = f"/api/v1/runs/{run_id}/funding/recompute"
    response = client.post(url, json={"liquidity_limit": "15000"}, headers={"Idempotency-Key": "one"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert client.post(url, json={"liquidity_limit": "15000.00"}, headers={"Idempotency-Key": "one"}).json()["job_id"] == job_id
    assert client.post(url, json={"liquidity_limit": "1"}, headers={"Idempotency-Key": "one"}).status_code == 409
    assert work_once(tmp_path)
    # Recreate the API to prove status/results are durable.
    client = TestClient(create_app(tmp_path))
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED"
    result_id = job["result"]["runs"][-1]["run_id"]
    assert result_id != run_id
    comparisons = client.get(f"/api/v1/runs/{result_id}/scenarios").json()["items"]
    assert len(comparisons) == 4
    assert {r["metrics"]["budget_limit"] for r in comparisons} == {"15000.00"}
    assert client.get(f"/api/v1/runs/{run_id}/scenarios").json()["items"][0]["metrics"]["budget_limit"] == "30000.00"
    plan = client.get(f"/api/v1/runs/{result_id}/funding").json()["plan"]
    assert client.get(f"/api/v1/runs/{result_id}/funding-plans/{plan['plan_id']}").status_code == 200
    assert client.get(f"/api/v1/runs/{result_id}/funding-plans/wrong").status_code == 404
    assert not work_once(tmp_path)


def test_job_failure_is_persisted(tmp_path):
    jobs = JobStore(tmp_path)
    job = jobs.submit("run-" + "0"*20, RecomputeRequest(liquidity_limit="10"), "bad")
    assert work_once(tmp_path)
    failed = jobs.get(job["job_id"])
    assert failed["status"] == "FAILED"
    assert "FileNotFoundError" in failed["error"]
    assert failed["result"] is None


def test_submission_validation_origins_and_missing_jobs(tmp_path):
    client = TestClient(create_app(tmp_path))
    path = "/api/v1/runs/run-" + "0"*20 + "/funding/recompute"
    for body in ({"liquidity_limit": "-1"}, {"liquidity_limit": "NaN"},
                 {"liquidity_limit": 100}, {"liquidity_limit": "1.001"},
                 {"liquidity_limit": "10", "scenario_count": 501},
                 {"liquidity_limit": "10", "path": "/tmp/scenario"}):
        assert client.post(path, json=body, headers={"Idempotency-Key": "key"}).status_code == 422
    assert client.post(path, json={"liquidity_limit": "10"}).status_code == 422
    assert client.post(path, json={"liquidity_limit": "10"}, headers={
        "Idempotency-Key": "key", "Origin": "https://untrusted.example",
    }).status_code == 403
    assert client.get("/api/v1/jobs/missing").status_code == 404
    assert client.post("/api/v1/jobs/missing/cancel").status_code == 404
