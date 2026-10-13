"""Peak cash requirements and the blueprint's scenario reserve LP."""

import hashlib
import json
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from pydantic import Field, model_validator

from t1fx.domain import (
    CURRENCY_EXPONENT,
    Bucket,
    Contract,
    Currency,
    FXLeg,
    Minor,
    Snapshot,
    whole,
)


class Requirement(Contract):
    bucket: Bucket
    scenario_amounts_minor: tuple[Minor, ...]
    cap_minor: Minor
    base_per_currency: Decimal = Field(gt=0)
    holding_coefficient: Decimal = Field(gt=0)
    shortfall_coefficient: Decimal = Field(gt=0)
    action_arrives_in_time: bool = True
    driver_trade_ids: tuple[str, ...] = ()


class FundingProblem(Contract):
    base_currency: Currency = "USD"
    budget_minor: Minor
    probabilities: tuple[Decimal, ...] = (Decimal(1),)
    requirements: tuple[Requirement, ...]

    @model_validator(mode="after")
    def coherent(self):
        if not self.probabilities or any(p < 0 for p in self.probabilities):
            raise ValueError("Scenario weights must be nonnegative and nonempty")
        if sum(self.probabilities) != Decimal(1):
            raise ValueError("Scenario weights must sum to one")
        keys = [r.bucket.key for r in self.requirements]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate funding bucket")
        rates: dict[str, Decimal] = {}
        for requirement in self.requirements:
            if len(requirement.scenario_amounts_minor) != len(self.probabilities):
                raise ValueError("Each bucket needs one requirement for each scenario")
            currency = requirement.bucket.currency
            if currency == self.base_currency and requirement.base_per_currency != 1:
                raise ValueError("Base-currency valuation rate must be one")
            if currency in rates and rates[currency] != requirement.base_per_currency:
                raise ValueError("Inconsistent valuation rates for the same currency")
            rates[currency] = requirement.base_per_currency
        return self


class Allocation(Contract):
    bucket: Bucket
    incremental_reserve_minor: Minor
    scenario_shortfalls_minor: tuple[Minor, ...]
    expected_shortfall_minor: Decimal
    driver_trade_ids: tuple[str, ...]


class FundingPlan(Contract):
    plan_id: str
    input_hash: str
    policy_id: str
    status: str
    fallback_used: bool = False
    failure_reason: str | None = None
    base_currency: Currency
    allocations: tuple[Allocation, ...]
    budget_used_base: Decimal
    expected_holding_cost_base: Decimal
    expected_shortfall_cost_base: Decimal
    expected_cost_base: Decimal
    liquidity_marginal_value: float | None = None


def fingerprint(problem: FundingProblem) -> str:
    payload = json.dumps(problem.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _capacity(requirement: Requirement) -> int:
    if not requirement.action_arrives_in_time:
        return 0
    return min(requirement.cap_minor, max(requirement.scenario_amounts_minor))


def _make_plan(problem, amounts, policy_id, status, reason=None, marginal=None):
    budget = whole(problem.budget_minor, problem.base_currency)
    # Exact Decimal validation protects the shared budget after floating-point conversion.
    used = sum((whole(a, r.bucket.currency) * r.base_per_currency
                for a, r in zip(amounts, problem.requirements, strict=True)), Decimal(0))
    if used > budget:
        for index in reversed(range(len(amounts))):
            requirement = problem.requirements[index]
            minor_value = whole(1, requirement.bucket.currency) * requirement.base_per_currency
            remove = min(amounts[index], int(((used-budget)/minor_value).to_integral_value(
                rounding=ROUND_CEILING,
            )))
            amounts[index] -= remove
            used -= remove * minor_value
            if used <= budget:
                break
    allocations, holding, shortfall_cost = [], Decimal(0), Decimal(0)
    for amount, requirement in zip(amounts, problem.requirements, strict=True):
        if not 0 <= amount <= _capacity(requirement):
            raise ValueError("Funding allocation violates its bucket capacity")
        shortfalls = tuple(max(0, demand-amount) for demand in requirement.scenario_amounts_minor)
        expected = sum((prob * gap for prob, gap in zip(
            problem.probabilities, shortfalls, strict=True,
        )), Decimal(0))
        allocations.append(Allocation(
            bucket=requirement.bucket, incremental_reserve_minor=amount,
            scenario_shortfalls_minor=shortfalls, expected_shortfall_minor=expected,
            driver_trade_ids=requirement.driver_trade_ids,
        ))
        holding += (whole(amount, requirement.bucket.currency) * requirement.base_per_currency
                    * requirement.holding_coefficient)
        shortfall_cost += (expected.scaleb(-CURRENCY_EXPONENT[requirement.bucket.currency])
                           * requirement.base_per_currency * requirement.shortfall_coefficient)
    input_hash = fingerprint(problem)
    plan_hash = hashlib.sha256(f"{input_hash}:{policy_id}:{amounts}".encode()).hexdigest()[:16]
    return FundingPlan(
        plan_id=f"plan-{plan_hash}", input_hash=input_hash, policy_id=policy_id, status=status,
        fallback_used=reason is not None, failure_reason=reason,
        base_currency=problem.base_currency, allocations=tuple(allocations),
        budget_used_base=used, expected_holding_cost_base=holding,
        expected_shortfall_cost_base=shortfall_cost, expected_cost_base=holding+shortfall_cost,
        liquidity_marginal_value=marginal,
    )


def fixed_percentage(problem: FundingProblem, fraction: Decimal) -> FundingPlan:
    if not fraction.is_finite() or not 0 <= fraction <= 1:
        raise ValueError("Funding fraction must lie between zero and one")
    amounts = [int((Decimal(_capacity(r))*fraction).to_integral_value(rounding=ROUND_FLOOR))
               for r in problem.requirements]
    total = sum((whole(a, r.bucket.currency)*r.base_per_currency
                 for a, r in zip(amounts, problem.requirements, strict=True)), Decimal(0))
    budget = whole(problem.budget_minor, problem.base_currency)
    if total > budget:
        amounts = [int((Decimal(a)*budget/total).to_integral_value(rounding=ROUND_FLOOR))
                   for a in amounts]
    return _make_plan(problem, amounts, f"fixed-{fraction.normalize()}", "BASELINE")


def optimize(problem: FundingProblem) -> FundingPlan:
    import numpy as np
    from scipy.optimize import linprog

    n, scenarios = len(problem.requirements), len(problem.probabilities)
    if not n:
        return _make_plan(problem, [], "scenario-lp-v1", "OPTIMAL", marginal=0.0)
    # x[0:n], then one shortfall per bucket/scenario. Amounts in whole currency units.
    objective = np.zeros(n + n*scenarios)
    constraints = np.zeros((n*scenarios+1, len(objective)))
    rhs = np.zeros(n*scenarios+1)
    bounds = []
    for b, requirement in enumerate(problem.requirements):
        value = float(requirement.base_per_currency)
        objective[b] = value * float(requirement.holding_coefficient)
        constraints[-1, b] = value
        bounds.append((0, float(whole(_capacity(requirement), requirement.bucket.currency))))
        for w, probability in enumerate(problem.probabilities):
            row = b*scenarios+w
            shortfall_index = n+row
            objective[shortfall_index] = value * float(requirement.shortfall_coefficient*probability)
            constraints[row, b] = -1
            constraints[row, shortfall_index] = -1
            rhs[row] = -float(whole(requirement.scenario_amounts_minor[w], requirement.bucket.currency))
    bounds.extend([(0, None)] * (n*scenarios))
    rhs[-1] = float(whole(problem.budget_minor, problem.base_currency))
    try:
        result = linprog(objective, A_ub=constraints, b_ub=rhs, bounds=bounds, method="highs")
        if not result.success or not np.isfinite(result.x).all():
            raise ValueError(str(result.message))
        if (constraints @ result.x-rhs).max() > 1e-6:
            raise ValueError("Solver output violates primal constraints")
        amounts = []
        for value, requirement in zip(result.x[:n], problem.requirements, strict=True):
            scaled = Decimal(str(max(0.0, value))).scaleb(CURRENCY_EXPONENT[requirement.bucket.currency])
            # Snap numerical dust around exact cents, then floor; final budget checked exactly.
            nearest = scaled.to_integral_value()
            if abs(scaled-nearest) < Decimal("0.000001"):
                scaled = nearest
            amounts.append(min(_capacity(requirement), int(scaled.to_integral_value(rounding=ROUND_FLOOR))))
        return _make_plan(problem, amounts, "scenario-lp-v1", "OPTIMAL",
                          marginal=max(0.0, -float(result.ineqlin.marginals[-1])))
    except (ValueError, RuntimeError) as exc:
        fallback = fixed_percentage(problem, Decimal("0.5"))
        return _make_plan(
            problem, [a.incremental_reserve_minor for a in fallback.allocations],
            "fallback-fixed-0.5", "FALLBACK", reason=str(exc),
        )


def deterministic_requirements(
    snapshots: tuple[Snapshot, ...],
    visible_fx: tuple[FXLeg, ...],
    credited_leg_ids: frozenset[str],
    as_of: datetime,
    budget_minor: int,
    base_rates: dict[Currency, Decimal],
) -> FundingProblem:
    """Use observed obligations/FX forecasts, never the simulation's future event queue."""
    if as_of.tzinfo is None or any(s.as_of != as_of for s in snapshots):
        raise ValueError("All inputs must share the same aware decision time")
    if len({leg.fx_leg_id for leg in visible_fx}) != len(visible_fx):
        raise ValueError("Duplicate FX forecast would double-count a receipt")
    if any(leg.available_at > as_of or leg.booked_at > as_of for leg in visible_fx):
        raise ValueError("Future FX contracts are not observable")
    requirements = []
    active = [s for s in snapshots if s.remaining_obligation_minor and s.horizon_at > as_of]
    for bucket in sorted({s.bucket for s in active}, key=lambda b: b.key):
        rows = [s for s in active if s.bucket == bucket]
        balances = {s.pool_usable_minor for s in rows}
        if len(balances) != 1:
            raise ValueError("Inconsistent pool balances at one decision time")
        running = -max(0, balances.pop())
        peak = max(0, running)
        movements = [(s.horizon_at, 1, s.trade_id, s.remaining_obligation_minor) for s in rows]
        latest = max(s.horizon_at for s in rows)
        for leg in visible_fx:
            if (leg.bucket == bucket and leg.fx_leg_id not in credited_leg_ids
                    and as_of < leg.expected_credit_at <= latest):
                movements.append((leg.expected_credit_at, 0, leg.fx_leg_id, -leg.buy_amount_minor))
        for _, _, _, movement in sorted(movements):
            running += movement
            peak = max(peak, running)
        requirements.append(Requirement(
            bucket=bucket, scenario_amounts_minor=(peak,), cap_minor=peak,
            base_per_currency=base_rates[bucket.currency], holding_coefficient=Decimal("0.001"),
            shortfall_coefficient=Decimal("0.01"), driver_trade_ids=tuple(s.trade_id for s in rows),
        ))
    return FundingProblem(budget_minor=budget_minor, requirements=tuple(requirements))
