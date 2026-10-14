from datetime import timedelta
from decimal import Decimal

import pytest

from t1fx.domain import Bucket, EventType, OpeningCash, Posting, Scenario
from t1fx.fixtures import instant, three_trade_scenario
from t1fx.ledger import Ledger
from t1fx.simulator import simulate
from t1fx.snapshots import build_snapshots, score_rules


def replace_events(scenario, events):
    return Scenario.model_validate({**scenario.model_dump(), "events": tuple(events)})


def test_three_trade_fixture_outcomes_and_costs():
    result = simulate(three_trade_scenario())
    outcomes = {outcome.trade_id: outcome for outcome in result.outcomes}
    assert outcomes["T-TIMELY"].missed_contractual_deadline is False
    assert outcomes["T-LATE-AFFIRM"].missed_contractual_deadline is False
    assert outcomes["T-LATE-AFFIRM"].route_fee == Decimal("5.00")
    gap = outcomes["T-FX-GAP"]
    assert gap.missed_contractual_deadline and gap.recovered_late
    assert gap.failure_reasons == ("INSUFFICIENT_FUNDING",)
    assert gap.late_cost == Decimal("22.50")
    assert all(balance == 0 for balance in result.ledger.balances.values())


def test_funding_prevents_cash_failure_and_repayment_is_conserved():
    scenario = three_trade_scenario()
    bucket = scenario.trades[2].bucket
    result = simulate(scenario, funding={bucket: 3_000_000})
    assert not any(outcome.missed_contractual_deadline for outcome in result.outcomes)
    assert result.bridge_outstanding_minor[bucket] == 0
    assert result.ledger.balances[bucket] == 0
    assert abs(result.bridge_costs[bucket]-Decimal("5.4794520547945205")) < Decimal("1e-12")
    result.ledger.assert_conserved()


def test_partial_funding_does_not_partially_settle_a_trade():
    scenario = three_trade_scenario()
    result = simulate(scenario, funding={scenario.trades[2].bucket: 1_500_000})
    assert result.outcomes[2].missed_contractual_deadline is True
    assert result.outcomes[2].settled_at == scenario.fx_legs[0].expected_credit_at


def test_cash_cannot_fix_ssi_and_unrepaid_bridge_persists():
    scenario = three_trade_scenario()
    events = [event.model_copy(update={"event_type": EventType.SSI_MISMATCH})
              if event.trade_id == "T-FX-GAP" and event.event_type == EventType.SSI_VALID else event
              for event in scenario.events]
    result = simulate(replace_events(scenario, events), funding={scenario.trades[2].bucket: 3_000_000})
    outcome = result.outcomes[2]
    assert outcome.censored
    assert outcome.failure_reasons == ("SSI_INVALID_OR_UNKNOWN",)
    # An unresolved source receipt cannot be repaid using invented end-of-window cash.
    no_receipt = replace_events(scenario, [e for e in scenario.events if e.event_type != EventType.FX_CREDITED])
    result = simulate(no_receipt, funding={scenario.trades[2].bucket: 3_000_000})
    assert result.bridge_outstanding_minor[scenario.trades[2].bucket] == 3_000_000


def test_credit_exactly_at_deadline_precedes_deadline_evaluation():
    scenario = three_trade_scenario()
    deadline = scenario.trades[2].contractual_deadline_at
    events = [event.model_copy(update={"event_time": deadline, "available_at": deadline})
              if event.event_type == EventType.FX_CREDITED else event for event in scenario.events]
    result = simulate(replace_events(scenario, events))
    assert result.outcomes[2].settled_at == deadline
    assert result.outcomes[2].missed_contractual_deadline is False


def test_event_input_order_does_not_change_outcomes():
    scenario = three_trade_scenario()
    assert simulate(scenario).outcomes == simulate(replace_events(scenario, reversed(scenario.events))).outcomes


def test_malformed_lifecycle_is_rejected():
    scenario = three_trade_scenario()
    events = [event for event in scenario.events
              if not (event.trade_id == "T-TIMELY" and event.event_type == EventType.ALLOCATED)]
    with pytest.raises(ValueError, match="transition"):
        simulate(replace_events(scenario, events))


def test_observation_boundary_excludes_unavailable_events_and_future_mutations():
    scenario = three_trade_scenario()
    as_of = scenario.decision_at
    base = build_snapshots(simulate(scenario), as_of)
    future_events = [event.model_copy(update={
        "event_time": event.event_time+timedelta(hours=1),
        "available_at": event.available_at+timedelta(hours=1),
    }) if event.event_type == EventType.FX_CREDITED else event for event in scenario.events]
    assert base == build_snapshots(simulate(replace_events(scenario, future_events)), as_of)
    delayed = [event.model_copy(update={"available_at": as_of+timedelta(hours=1)})
               if event.trade_id == "T-TIMELY" and event.event_type == EventType.SSI_VALID else event
               for event in scenario.events]
    observed = build_snapshots(simulate(replace_events(scenario, delayed)), as_of)
    assert observed[0].ssi_status == "UNKNOWN"
    assert base[0].ssi_status == "VALID"
    assert observed[2].pool_usable_minor == 0  # Tomorrow's FX is not cash today.


def test_rule_score_is_never_a_probability():
    scenario = three_trade_scenario()
    snapshot = build_snapshots(simulate(scenario, until=scenario.decision_at), scenario.decision_at)[2]
    prediction = score_rules(snapshot)
    assert prediction.score > 0
    assert prediction.p_fail_any is None
    assert prediction.status == "UNCALIBRATED_RULE_SCORE"


def test_ledger_reservation_idempotency_and_no_double_spend():
    bucket = Bucket(cash_pool_id="A", currency="USD")
    ledger = Ledger((OpeningCash(bucket=bucket, amount_minor=10_000),))
    at = instant("2026-10-13T12:00:00Z")
    assert ledger.reserve("T1", bucket, 8_000)
    assert ledger.reserve("T1", bucket, 8_000) is False
    assert ledger.free(bucket) == 2_000
    with pytest.raises(ValueError):
        ledger.reserve("T2", bucket, 3_000)
    with pytest.raises(ValueError):
        ledger.reserve("T1", bucket, 7_000)
    assert ledger.settle("T1", at)
    assert ledger.settle("T1", at) is False
    assert ledger.free(bucket) == 2_000
    ledger.assert_conserved()


def test_posting_idempotency_and_segregated_pools():
    a, b = (Bucket(cash_pool_id=name, currency="USD") for name in ("A", "B"))
    ledger = Ledger((OpeningCash(bucket=a, amount_minor=10_000),))
    posting = Posting(posting_id="P1", bucket=a, amount_minor=100,
                      effective_at=instant("2026-10-13T12:00:00Z"),
                      available_at=instant("2026-10-13T12:00:00Z"),
                      kind="FX_CREDIT", source_ref="FX1")
    assert ledger.post(posting)
    assert ledger.post(posting) is False
    with pytest.raises(ValueError):
        ledger.post(posting.model_copy(update={"amount_minor": 200}))
    with pytest.raises(ValueError):
        ledger.reserve("T2", b, 1)
    with pytest.raises(ValueError, match="sign"):
        Posting.model_validate(posting.model_dump() | {"amount_minor": -100})
    with pytest.raises(ValueError, match="effective time"):
        Posting.model_validate(posting.model_dump() | {
            "available_at": instant("2026-10-13T11:00:00Z"),
        })


def test_shared_pool_fixed_order_skips_unfunded_trade():
    scenario = three_trade_scenario()
    bucket = scenario.trades[0].bucket
    data = scenario.model_dump()
    data["trades"] = tuple(trade.model_copy(update={"bucket": bucket}) for trade in scenario.trades)
    data["opening_cash"] = (OpeningCash(bucket=bucket, amount_minor=1_000_000),)
    # FX pool remains segregated; only the small timely trade fits at the first settlement attempt.
    result = simulate(Scenario.model_validate(data))
    assert result.outcomes[0].missed_contractual_deadline is False
    assert result.outcomes[1].missed_contractual_deadline is True
    assert result.outcomes[2].missed_contractual_deadline is True
