import json
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from t1fx.api import create_app
from t1fx.cli import main, usd_minor
from t1fx.fixtures import three_trade_scenario
from t1fx.pipeline import run_comparison
from t1fx.storage import ArtifactError, RunStore


def test_eur_demo_uses_all_funding_currencies_and_eur_budget(tmp_path):
    from t1fx.fixtures import demo_scenario

    scenario = demo_scenario()
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from t1fx.fixtures import current_utc
    today = current_utc().astimezone(ZoneInfo("America/New_York")).date()
    assert all(today-timedelta(days=29) <= trade.executed_at.date() <= today
               for trade in scenario.trades)
    assert len({trade.executed_at.date() for trade in scenario.trades}) > 10
    assert {trade.bucket.currency for trade in scenario.trades} == {"USD"}
    assert {source.currency for source in scenario.funding_sources} == {
        "EUR", "CAD", "GBP", "JPY", "INR",
    }
    store = RunStore(tmp_path)
    report = run_comparison(scenario, 3_000_000, store)
    for run in report["runs"]:
        assert run["currency"] == "USD"
        assert Decimal(run["budget_used"]) <= Decimal("30000")
        rates = store.read(run["run_id"], "fx_rates.json")
        assert rates["base_currency"] == "USD"
        assert store.read(run["run_id"], "funding_plan.json")["base_currency"] == "USD"
        assert all(datetime.fromisoformat(row['as_of']) >= next(
            trade.executed_at for trade in scenario.trades if trade.trade_id == row['trade_id']
        ) for row in store.read(run['run_id'], 'snapshots.json'))


def test_evaluation_calendar_job_and_timezone_validation(demo):
    from t1fx.jobs import work_once

    store, report, client = demo
    run_id = report["runs"][0]["run_id"]
    body = {"liquidity_limit": "30000", "evaluation_local": "2026-10-14T18:00:00",
            "evaluation_timezone": "Asia/Kolkata"}
    response = client.post(f"/api/v1/runs/{run_id}/funding/recompute", json=body,
                           headers={"Idempotency-Key": "evaluation-test"})
    assert response.status_code == 202, response.text
    assert work_once(store.root)
    job = client.get('/api/v1/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'SUCCEEDED', job
    updated = store.manifest(job['result']['runs'][0]['run_id'])
    assert updated['decision_at'].startswith('2026-10-14T12:30:00')
    for change in ({"evaluation_timezone": "invalid/zone"},
                   {"evaluation_local": "2020-01-01T00:00:00"}):
        response = client.post(f"/api/v1/runs/{run_id}/funding/recompute", json=body | change,
                               headers={"Idempotency-Key": "invalid-evaluation"})
        assert response.status_code == 422
        if 'evaluation_local' in change:
            message = response.json()['message']
            assert 'Choose an evaluation time' in message
            assert '2026' in message
            assert 'pydantic' not in message.lower()


@pytest.fixture
def demo(tmp_path):
    store = RunStore(tmp_path)
    report = run_comparison(three_trade_scenario(), 3_000_000, store)
    return store, report, TestClient(create_app(tmp_path))


def test_pipeline_repeat_is_identical_and_policies_share_scenario(demo):
    store, report, _ = demo
    repeated = run_comparison(three_trade_scenario(), 3_000_000, store)
    assert report == repeated
    assert len(store.list_runs()) == 4
    hashes = {store.validate(row["run_id"])["scenario_hash"] for row in report["runs"]}
    assert len(hashes) == 1
    costs = {row["policy_id"]: Decimal(row["total_cost"]) for row in report["runs"]}
    assert costs["scenario-lp-v1"] < costs["fixed-0"]
    assert costs["scenario-lp-v1"] == costs["fixed-1"]
    assert costs["fixed-0.5"] > costs["fixed-0"]  # Whole-trade settlement makes partial reserves costly.


def test_run_tampering_detected_and_partial_directories_hidden(demo):
    store, report, _ = demo
    run_id = report["runs"][0]["run_id"]
    path = store.root/"runs"/run_id/"metrics.json"
    path.write_text('{"tampered":true}')
    with pytest.raises(ArtifactError, match="checksum"):
        store.validate(run_id)
    (store.root/"runs"/".staging-incomplete").mkdir()
    assert len(store.list_runs()) == 4
    with pytest.raises(ArtifactError):
        store.manifest("../../private")
    manifest_path = store.root/"runs"/run_id/"manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["policy_id"] = "modified-policy"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ArtifactError, match="identity"):
        store.manifest(run_id)


def test_api_trade_list_detail_funding_and_money_serialization(demo):
    _, report, client = demo
    run_id = report["runs"][-1]["run_id"]
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    page = client.get(f"/api/v1/runs/{run_id}/trades", params={"limit": 1}).json()
    first = page["items"][0]
    assert first["trade_id"] == "T-FX-GAP"
    assert first["settlement_amount"] == "30000.00"
    assert first["prediction"]["p_fail_any"] is None
    assert page["reference_policy_id"] == "fixed-0"
    next_page = client.get(f"/api/v1/runs/{run_id}/trades", params={"cursor": page["next_cursor"]}).json()
    assert len(next_page["items"]) == 2
    detail = client.get(f"/api/v1/runs/{run_id}/trades/T-FX-GAP").json()
    assert detail["outcome"]["missed_contractual_deadline"] is False
    assert all(e["event_type"] != "SETTLED" for e in detail["observed_events"])
    funding = client.get(f"/api/v1/runs/{run_id}/funding").json()
    fx = next(a for a in funding["plan"]["allocations"] if a["bucket"]["cash_pool_id"] == "fx")
    assert fx["incremental_reserve"] == "30000.00"
    assert "incremental_reserve_minor" not in fx
    assert client.get(f"/api/v1/runs/{run_id}/metrics").json()["metrics"]["failed_count"] == 0
    assert client.get(f"/api/v1/runs/{run_id}/fx-rates").json()["rates"]["base_currency"] == "USD"


def test_api_consistent_errors_and_validated_pagination(demo):
    _, report, client = demo
    run_id = report["runs"][0]["run_id"]
    for path, code in [
        (f"/api/v1/runs/{run_id}/trades?limit=1000", 422),
        (f"/api/v1/runs/{run_id}/trades?currency=XXX", 422),
        (f"/api/v1/runs/{run_id}/trades?cursor=invalid", 400),
        (f"/api/v1/runs/{run_id}/trades/missing", 404),
        ("/api/v1/runs/run-00000000000000000000", 404),
    ]:
        response = client.get(path)
        assert response.status_code == code, response.text
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert set(response.json()) == {"code", "message", "details", "request_id"}


def test_readiness_requires_artifacts_and_openapi_is_available(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/health/ready").status_code == 503
    schema = client.get("/openapi.json").json()
    assert "/api/v1/runs/{run_id}/trades" in schema["paths"]
    assert "TradePage" in schema["components"]["schemas"]


def test_cli_fixture_and_custom_scenario_pipeline(tmp_path, capsys):
    fixture = tmp_path/"fixture.json"
    main(["write-fixture", "--output", str(fixture)])
    assert json.loads(fixture.read_text())["scenario_id"] == "three-trades-v1"
    capsys.readouterr()
    main(["--artifacts", str(tmp_path/"artifacts"), "simulate", "--scenario", str(fixture), "--budget", "0"])
    report = json.loads(capsys.readouterr().out)
    assert all(row["failed_count"] == 1 for row in report["runs"])
    main(["--artifacts", str(tmp_path/"artifacts"), "validate", "--run", report["runs"][0]["run_id"]])
    assert "Validated" in capsys.readouterr().out
    assert usd_minor("12.34") == 1234


def test_watchlist_currency_matches_funding_and_preserves_original_amount(tmp_path):
    from t1fx.fixtures import demo_scenario
    scenario = demo_scenario()
    report = run_comparison(scenario, 3_000_000, RunStore(tmp_path))
    client = TestClient(create_app(tmp_path))
    run_id = report["runs"][0]["run_id"]
    page = client.get(f"/api/v1/runs/{run_id}/trades").json()
    assert set(page["available_currencies"]) == {"USD", "EUR", "CAD", "GBP", "JPY", "INR"}
    for currency in page["available_currencies"]:
        response = client.get(f"/api/v1/runs/{run_id}/trades", params={"currency": currency})
        assert response.status_code == 200
        rows = response.json()["items"]
        assert rows
        assert all(row["currency"] == currency or any(fx["sell_currency"] == currency for fx in row["fx_conversions"]) for row in rows)
        assert all(Decimal(row["settlement_amount"]) > 0 for row in rows)
    assert any(row["observed_settled"] and Decimal(row["remaining_obligation"]) == 0 for row in page["items"])


def test_small_demo_spans_window_and_decision_never_exceeds_online_time(monkeypatch):
    from datetime import datetime
    from t1fx.fixtures import demo_scenario
    now = datetime.fromisoformat('2026-11-06T01:00:00+00:00')
    monkeypatch.setattr('t1fx.fixtures.current_utc', lambda: now)
    scenario = demo_scenario(3)
    assert scenario.decision_at <= now
    assert (max(t.executed_at for t in scenario.trades) - min(t.executed_at for t in scenario.trades)).days >= 27
    assert all(leg.trade_id in {trade.trade_id for trade in scenario.trades} for leg in scenario.fx_legs)


def test_generated_watchlist_has_readable_labels_and_retains_research_ids(tmp_path):
    from pathlib import Path
    from t1fx.generation import generate_day, load_config
    config = load_config(Path('configs/generation/smoke.json'))
    scenario = generate_day(config, config.start_date, 10, 'test').scenario
    report = run_comparison(scenario, 0, RunStore(tmp_path))
    client = TestClient(create_app(tmp_path))
    items = client.get(f"/api/v1/runs/{report['runs'][0]['run_id']}/trades").json()['items']
    assert len(items) == 10
    assert all(t['trade_label'].startswith('Trade ') and t['security_label'].startswith('Sample equity ') for t in items)
    assert {t['trade_id'] for t in items} == {t.trade_id for t in scenario.trades}
    assert all(t['counterparty_label'].startswith('Sample counterparty ') for t in items)
