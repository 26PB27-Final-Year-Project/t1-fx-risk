from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from t1fx.api import create_app
from t1fx.fixtures import three_trade_scenario
from t1fx.funding import scenario_requirements
from t1fx.pipeline import run_comparison
from t1fx.simulator import simulate
from t1fx.snapshots import build_snapshots
from t1fx.storage import ArtifactError, RunStore
from t1fx.timing import TimingModel, forecast_arrivals, load_model, publish_model


def setup_forecast():
    scenario = three_trade_scenario()
    leg = scenario.fx_legs[0].model_copy(update={
        "expected_credit_at": scenario.decision_at + timedelta(hours=1),
    })
    model = TimingModel(dataset_id="synthetic-test", trained_through=scenario.starts_at-timedelta(days=1),
                        delays_seconds={"EUR/USD": (0, 7200, None)}, training_count=3)
    return scenario, leg, model


def test_survival_conditioning_maturity_and_missing_support():
    scenario, leg, model = setup_forecast()
    assert len(model.support(leg, scenario.decision_at)) == 3
    assert len(model.support(leg, leg.expected_credit_at)) == 2
    assert model.support(leg, leg.expected_credit_at+timedelta(days=1)) == [None]
    with pytest.raises(ValueError, match="maturity"):
        model.support(leg, model.trained_through)
    with pytest.raises(ValueError, match="route"):
        model.support(leg.model_copy(update={"sell_currency": "GBP"}), scenario.decision_at)


def test_common_shocks_coherent_deadlines_and_reproducible_marginals():
    scenario, leg, model = setup_forecast()
    second = leg.model_copy(update={"fx_leg_id": "SECOND"})
    targets = {leg.bucket: (leg.expected_credit_at, leg.expected_credit_at+timedelta(hours=3))}
    args = (model, (leg, second), frozenset(), scenario.decision_at, targets)
    draws, weights, report = forecast_arrivals(*args, count=3000, common_shock_weight=1)
    assert (draws, weights, report) == forecast_arrivals(*args, count=3000, common_shock_weight=1)
    assert sum(weights) == 1
    assert all(draw[leg.fx_leg_id] == draw[second.fx_leg_id] for draw in draws)
    probabilities = [row["p_credit_after_target"] for row in report["predictions"][:2]]
    assert probabilities == [2/3, 1/3]
    assert max(row["absolute_sampling_error"] for row in report["predictions"]) < 0.04


def test_peak_paths_receipts_once_and_deadline_equality():
    scenario, leg, _ = setup_forecast()
    snapshots = build_snapshots(simulate(scenario, until=scenario.decision_at), scenario.decision_at)
    row = next(s for s in snapshots if s.bucket == leg.bucket)
    first = row.model_copy(update={"remaining_obligation_minor": 10_000})
    second = first.model_copy(update={"trade_id": "SECOND"})
    leg = leg.model_copy(update={"buy_amount_minor": 15_000})
    problem = scenario_requirements(
        (first, second), (leg,), frozenset(), scenario.decision_at, 20_000, {"USD": Decimal(1)},
        [{leg.fx_leg_id: row.horizon_at}, {leg.fx_leg_id: row.horizon_at+timedelta(seconds=1)},
         {leg.fx_leg_id: None}], (Decimal("0.2"), Decimal("0.3"), Decimal("0.5")),
    )
    assert problem.requirements[0].scenario_amounts_minor == (5_000, 20_000, 20_000)
    with pytest.raises(ValueError, match="every uncredited"):
        scenario_requirements((first,), (leg,), frozenset(), scenario.decision_at, 20_000,
                              {"USD": Decimal(1)}, [{}], (Decimal(1),))


def test_timing_replay_integrity_and_api(tmp_path):
    scenario, _, model = setup_forecast()
    model_id = publish_model(tmp_path, model, {"count": 0})
    assert load_model(tmp_path, model_id)[0] == model
    store = RunStore(tmp_path)
    report = run_comparison(scenario, 3_000_000, store, timing_model_id=model_id, scenario_count=17)
    assert len(report["runs"]) == 5
    assert {r["policy_id"] for r in report["runs"]} == {
        "fixed-0", "fixed-0.5", "fixed-1", "deterministic-lp-v1", "scenario-lp-v1",
    }
    assert report == run_comparison(scenario, 3_000_000, store,
                                   timing_model_id=model_id, scenario_count=17)
    for row in report["runs"]:
        store.validate(row["run_id"])
        assert Decimal(row["budget_used"]) <= Decimal(row["budget_limit"])
    client = TestClient(create_app(tmp_path))
    run_id = report["runs"][-1]["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}/timing-forecasts").status_code == 200
    assert client.get(f"/api/v1/timing-models/{model_id}/health").status_code == 200
    (tmp_path/"timing_models"/f"{model_id}.json").write_text('{}')
    with pytest.raises(ArtifactError):
        load_model(tmp_path, model_id)


def test_dataset_training_cli_and_future_replay(tmp_path, capsys):
    import json
    from pathlib import Path

    from t1fx.cli import main
    from t1fx.datasets import generate_release
    from t1fx.domain import Scenario
    from t1fx.generation import GenerationConfig, load_config
    from t1fx.timing import train_timing

    config = GenerationConfig.model_validate(load_config(Path("configs/generation/smoke.json")).model_dump()
                                             | {"trade_count": 80, "trading_days": 4})
    manifest = generate_release(config, tmp_path, "json")
    dataset_id = manifest["dataset_id"]
    main(["--artifacts", str(tmp_path), "train-timing", "--dataset", dataset_id])
    trained = json.loads(capsys.readouterr().out)
    model_id = trained["timing_model_id"]
    assert trained["training_count"] == 20
    assert trained["metrics"]["count"] > 0
    assert 0 <= trained["metrics"]["brier_score"] <= 1
    assert train_timing(tmp_path, dataset_id)["timing_model_id"] == model_id
    directory = tmp_path/"datasets"/dataset_id
    legs = json.loads((directory/manifest["tables"]["fx_legs"]["json"]).read_text())
    test_scenario_id = next(row["scenario_id"] for row in legs if row["split"] == "test")
    scenario_path = directory/"scenarios"/f"{test_scenario_id}.json"
    main(["--artifacts", str(tmp_path), "simulate", "--scenario", str(scenario_path),
          "--timing-model", model_id, "--scenario-count", "3"])
    report = json.loads(capsys.readouterr().out)
    assert len(report["runs"]) == 5
    model, _ = load_model(tmp_path, model_id)
    assert model.trained_through < Scenario.model_validate_json(scenario_path.read_text()).decision_at
    assert sum(len(values) for values in model.delays_seconds.values()) == 20


def test_timing_uncertainty_changes_reserve_and_realised_replay(tmp_path):
    scenario, leg, model = setup_forecast()
    deadline = next(t.contractual_deadline_at for t in scenario.trades if t.bucket == leg.bucket)
    leg = leg.model_copy(update={"expected_credit_at": deadline})
    scenario = scenario.model_copy(update={"fx_legs": (leg,)})
    model = model.model_copy(update={"delays_seconds": {"EUR/USD": (0, 7200)}, "training_count": 2})
    model_id = publish_model(tmp_path, model, {})
    store = RunStore(tmp_path)
    report = run_comparison(scenario, 3_000_000, store, timing_model_id=model_id)
    runs = {row["policy_id"]: row for row in report["runs"]}
    assert runs["deterministic-lp-v1"]["budget_used"] == "0.00"
    assert Decimal(runs["scenario-lp-v1"]["budget_used"]) == 30000
    assert runs["scenario-lp-v1"]["failed_count"] < runs["deterministic-lp-v1"]["failed_count"]
    # Both recommendations replay the exact same actual future, independent of sampled paths.
    assert store.read(runs["deterministic-lp-v1"]["run_id"], "scenario.json") == store.read(
        runs["scenario-lp-v1"]["run_id"], "scenario.json",
    )
