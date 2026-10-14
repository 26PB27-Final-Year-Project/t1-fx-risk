import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from t1fx.api import create_app
from t1fx.cli import main, usd_minor
from t1fx.fixtures import three_trade_scenario
from t1fx.pipeline import run_comparison
from t1fx.storage import ArtifactError, RunStore


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
