"""Reference-policy observation, funding recommendation, and matched policy replay."""

import hashlib
import importlib.metadata
import platform
from decimal import Decimal
from pathlib import Path

from t1fx.domain import EventType, Scenario, whole
from t1fx.funding import deterministic_requirements, fixed_percentage, optimize, scenario_requirements
from t1fx.simulator import simulate
from t1fx.snapshots import build_snapshots, score_rules
from t1fx.storage import RunStore, digest


def _code_hash() -> str:
    root = Path(__file__).parent
    hasher = hashlib.sha256()
    for source in sorted(root.glob("*.py")):
        hasher.update(source.name.encode())
        hasher.update(source.read_bytes())
    return hasher.hexdigest()


def run_comparison(scenario: Scenario, budget_minor: int, store: RunStore, *,
                   timing_model_id: str | None = None, scenario_seed: int = 42,
                   scenario_count: int = 200, common_shock_weight: float = 0.5) -> dict:
    # Deliberately stop the reference replay at decision time before constructing plans.
    reference = simulate(scenario, until=scenario.decision_at)
    snapshots = build_snapshots(reference, scenario.decision_at)
    visible_fx = tuple(leg for leg in scenario.fx_legs
                       if leg.booked_at <= scenario.decision_at and leg.available_at <= scenario.decision_at)
    credited_ids = frozenset(event.fx_leg_id for event in reference.events
                            if event.event_type == EventType.FX_CREDITED
                            and event.available_at <= scenario.decision_at)
    currencies = {s.bucket.currency for s in snapshots}
    if currencies - {"USD"}:
        raise ValueError("Demo replay supports USD only; supply explicit rates before adding currencies")
    problem = deterministic_requirements(
        snapshots, visible_fx, credited_ids, scenario.decision_at, budget_minor, {"USD": Decimal(1)},
    )
    deterministic = problem
    timing_forecasts = None
    if timing_model_id is not None:
        from t1fx.timing import forecast_arrivals, load_model

        timing_model, _ = load_model(store.root, timing_model_id)
        targets = {}
        for snapshot in snapshots:
            if snapshot.remaining_obligation_minor and snapshot.horizon_at > scenario.decision_at:
                targets.setdefault(snapshot.bucket, []).append(snapshot.horizon_at)
        draws, weights, timing_forecasts = forecast_arrivals(
            timing_model, visible_fx, credited_ids, scenario.decision_at, targets,
            scenario_seed, scenario_count, common_shock_weight,
        )
        timing_forecasts["timing_model_id"] = timing_model_id
        problem = scenario_requirements(
            snapshots, visible_fx, credited_ids, scenario.decision_at, budget_minor,
            {"USD": Decimal(1)}, draws, weights,
        )
    plans = [fixed_percentage(problem, fraction) for fraction in (Decimal(0), Decimal("0.5"), Decimal(1))]
    problems = [problem] * len(plans)
    if timing_model_id is not None:
        plans.append(optimize(deterministic, policy_id="deterministic-lp-v1"))
        problems.append(deterministic)
    plans.append(optimize(problem))
    problems.append(problem)
    predictions = [score_rules(snapshot).model_dump(mode="json") for snapshot in snapshots]
    environment = {
        "python": platform.python_version(),
        "dependencies": {name: importlib.metadata.version(name)
                         for name in ("pydantic", "numpy", "scipy", "fastapi")},
    }
    code_hash = _code_hash()
    comparison_id = "comparison-" + digest({
        "scenario": scenario.model_dump(mode="json"), "budget_minor": budget_minor,
        "code_hash": code_hash, "environment": environment,
        "plans": [plan.model_dump(mode="json") for plan in plans],
        "timing_forecasts": timing_forecasts,
    })[:20]
    reports = []
    for plan, plan_problem in zip(plans, problems, strict=True):
        funding = {allocation.bucket: allocation.incremental_reserve_minor for allocation in plan.allocations}
        result = simulate(scenario, funding=funding)
        late_cost = sum((outcome.late_cost for outcome in result.outcomes), Decimal(0))
        route_cost = sum((outcome.route_fee for outcome in result.outcomes), Decimal(0))
        bridge_cost = sum(result.bridge_costs.values(), Decimal(0))
        failed_ids = {outcome.trade_id for outcome in result.outcomes
                      if outcome.missed_contractual_deadline is True}
        metrics = {
            "currency": "USD", "trade_count": len(scenario.trades),
            "failed_notional": str(whole(sum(
                trade.settlement_amount_minor for trade in scenario.trades
                if trade.trade_id in failed_ids
            ), "USD")),
            "failed_count": sum(outcome.missed_contractual_deadline is True for outcome in result.outcomes),
            "recovered_late_count": sum(outcome.recovered_late for outcome in result.outcomes),
            "censored_count": sum(outcome.censored for outcome in result.outcomes),
            "late_cost": str(late_cost), "route_cost": str(route_cost),
            "bridge_cost": str(bridge_cost), "total_cost": str(late_cost+route_cost+bridge_cost),
            "budget_limit": str(whole(budget_minor, "USD")),
            "budget_used": str(plan.budget_used_base),
            "bridge_outstanding": str(sum((whole(amount, bucket.currency)
                                           for bucket, amount in result.bridge_outstanding_minor.items()), Decimal(0))),
            "cost_classification": "synthetic_assumptions",
        }
        artifacts = {
            "scenario.json": scenario.model_dump(mode="json"),
            "events.json": [event.model_dump(mode="json") for event in result.events],
            "reference_events.json": [event.model_dump(mode="json") for event in reference.events],
            "postings.json": [p.model_dump(mode="json") for p in result.ledger.postings.values()],
            "snapshots.json": [s.model_dump(mode="json") for s in snapshots],
            "predictions.json": predictions,
            "funding_problem.json": plan_problem.model_dump(mode="json"),
            "funding_plan.json": plan.model_dump(mode="json"),
            "outcomes.json": [outcome.model_dump(mode="json") for outcome in result.outcomes],
            "metrics.json": metrics,
        }
        if timing_forecasts is not None:
            artifacts["timing_forecasts.json"] = timing_forecasts
        metadata = {
            "scenario_id": scenario.scenario_id,
            "scenario_hash": digest(scenario.model_dump(mode="json")),
            "code_hash": code_hash, "environment": environment, "comparison_id": comparison_id,
            "policy_id": plan.policy_id, "reference_policy_id": "fixed-0",
            "decision_at": scenario.decision_at.isoformat(),
            "result_as_of": result.as_of.isoformat(), "settlement_priority": "deadline_execution_id_skip_unfunded",
            "artifact_format": "canonical_json_v1", "calendar_profile_ids": sorted({t.market_profile_id for t in scenario.trades}),
            "model_id": "rules-v1", "assumption": scenario.assumption,
        }
        if timing_model_id is not None:
            metadata["timing_model_id"] = timing_model_id
            metadata["scenario_seed"] = scenario_seed
        run_id = store.publish(artifacts, metadata)
        reports.append({"run_id": run_id, "policy_id": plan.policy_id, **metrics})
    return {"scenario_id": scenario.scenario_id, "runs": reports}
