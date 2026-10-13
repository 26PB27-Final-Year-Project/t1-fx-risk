from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from t1fx.domain import Bucket
from t1fx.fixtures import three_trade_scenario
from t1fx.funding import FundingProblem, Requirement, deterministic_requirements, fixed_percentage, optimize
from t1fx.simulator import simulate
from t1fx.snapshots import build_snapshots


def requirement(pool="A", penalty="0.10", currency="USD", rate="1", **kwargs):
    defaults = dict(
        bucket=Bucket(cash_pool_id=pool, currency=currency), scenario_amounts_minor=(10_000,),
        cap_minor=10_000, base_per_currency=Decimal(rate), holding_coefficient=Decimal("0.01"),
        shortfall_coefficient=Decimal(penalty),
    )
    return Requirement(**(defaults | kwargs))


@pytest.mark.parametrize("budget,amounts,cost", [
    (20_000, [10_000, 10_000], "2"), (10_000, [10_000, 0], "6"),
    (15_000, [10_000, 5_000], "4"), (0, [0, 0], "15"),
])
def test_blueprint_hand_solutions(budget, amounts, cost):
    problem = FundingProblem(budget_minor=budget, requirements=(requirement(), requirement("B", "0.05")))
    plan = optimize(problem)
    assert plan.status == "OPTIMAL"
    assert [a.incremental_reserve_minor for a in plan.allocations] == amounts
    assert plan.expected_cost_base == Decimal(cost)


def test_probability_weighted_scenario_does_not_fund_expensive_reserve():
    problem = FundingProblem(
        budget_minor=10_000, probabilities=(Decimal("0.8"), Decimal("0.2")),
        requirements=(requirement(scenario_amounts_minor=(0, 10_000), holding_coefficient=Decimal("0.03")),),
    )
    plan = optimize(problem)
    assert plan.allocations[0].incremental_reserve_minor == 0
    assert plan.expected_cost_base == Decimal("2")


def test_currency_conversion_and_route_cap():
    problem = FundingProblem(budget_minor=7_500, requirements=(
        requirement(), requirement("B", "0.20", "CAD", "0.75"),
    ))
    plan = optimize(problem)
    assert [a.incremental_reserve_minor for a in plan.allocations] == [0, 10_000]
    assert plan.budget_used_base == Decimal("75")
    unavailable = FundingProblem(budget_minor=10_000, requirements=(requirement(action_arrives_in_time=False),))
    assert optimize(unavailable).allocations[0].incremental_reserve_minor == 0


def test_budget_monotonicity_and_fractional_conversion_rounding():
    previous = Decimal("Infinity")
    for budget in range(0, 20_001, 1_000):
        problem = FundingProblem(budget_minor=budget, requirements=(requirement(), requirement("B", "0.05")))
        plan = optimize(problem)
        assert plan.expected_cost_base <= previous
        assert plan.budget_used_base <= Decimal(budget)/100
        previous = plan.expected_cost_base
    problem = FundingProblem(budget_minor=1, requirements=(requirement("B", currency="CAD", rate="0.7317"),))
    plan = optimize(problem)
    assert plan.budget_used_base <= Decimal("0.01")
    assert plan.allocations[0].incremental_reserve_minor == 1


def test_budget_constrained_baseline_and_solver_fallback(monkeypatch):
    problem = FundingProblem(budget_minor=5_000, requirements=(requirement(), requirement("B")))
    plan = fixed_percentage(problem, Decimal(1))
    assert [a.incremental_reserve_minor for a in plan.allocations] == [2_500, 2_500]
    monkeypatch.setattr("scipy.optimize.linprog", lambda *a, **k: SimpleNamespace(
        success=False, message="injected solver failure",
    ))
    fallback = optimize(problem)
    assert fallback.fallback_used and fallback.status == "FALLBACK"
    assert "injected solver failure" in fallback.failure_reason
    assert fallback.budget_used_base <= Decimal("50")


@pytest.mark.parametrize("bad_rate", ["NaN", "Infinity", "-1", "0"])
def test_invalid_financial_parameters_rejected(bad_rate):
    with pytest.raises(ValidationError):
        requirement(rate=bad_rate)


def test_peak_requirement_ignores_receipts_after_deadline():
    scenario = three_trade_scenario()
    snapshots = build_snapshots(simulate(scenario, until=scenario.decision_at), scenario.decision_at)
    problem = deterministic_requirements(snapshots, scenario.fx_legs, frozenset(), scenario.decision_at,
                                         3_000_000, {"USD": Decimal(1)})
    demands = {r.bucket.cash_pool_id: r.scenario_amounts_minor for r in problem.requirements}
    assert demands == {"fx": (3_000_000,), "late": (0,), "timely": (0,)}


def test_shared_receipt_counted_once_and_unknown_forecasts_rejected():
    scenario = three_trade_scenario()
    snapshot = build_snapshots(simulate(scenario), scenario.decision_at)[2]
    first = snapshot.model_copy(update={"remaining_obligation_minor": 10_000})
    second = first.model_copy(update={"trade_id": "SECOND"})
    leg = scenario.fx_legs[0].model_copy(update={
        "buy_amount_minor": 15_000, "expected_credit_at": first.horizon_at,
    })
    problem = deterministic_requirements((first, second), (leg,), frozenset(), scenario.decision_at,
                                         20_000, {"USD": Decimal(1)})
    assert problem.requirements[0].scenario_amounts_minor == (5_000,)
    with pytest.raises(ValueError, match="Duplicate"):
        deterministic_requirements((first, second), (leg, leg), frozenset(), scenario.decision_at,
                                   20_000, {"USD": Decimal(1)})
    with pytest.raises(ValueError, match="Future"):
        deterministic_requirements((first,), (leg.model_copy(update={"available_at": first.horizon_at}),),
                                   frozenset(), scenario.decision_at, 20_000, {"USD": Decimal(1)})


def test_incorrect_scenario_weights_rejected():
    with pytest.raises(ValidationError, match="sum to one"):
        FundingProblem(budget_minor=1, probabilities=(Decimal("0.9"),), requirements=(requirement(),))
