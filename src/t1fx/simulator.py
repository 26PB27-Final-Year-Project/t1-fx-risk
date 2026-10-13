"""Deterministic, whole-trade simulation with parallel instruction/SSI/cash tracks."""

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from t1fx.domain import Bucket, Event, EventType, Outcome, Posting, Scenario, whole
from t1fx.ledger import Ledger


@dataclass
class TradeState:
    instruction: str = "EXECUTED"
    ssi: str = "UNKNOWN"
    settled_at: datetime | None = None
    failed: bool | None = None
    reasons: tuple[str, ...] = ()
    used_fallback: bool = False


@dataclass
class SimulationResult:
    scenario: Scenario
    as_of: datetime
    ledger: Ledger
    events: list[Event]
    states: dict[str, TradeState]
    outcomes: list[Outcome]
    bridge_costs: dict[Bucket, Decimal] = field(default_factory=dict)
    bridge_outstanding_minor: dict[Bucket, int] = field(default_factory=dict)


def simulate(
    scenario: Scenario,
    *,
    until: datetime | None = None,
    funding: dict[Bucket, int] | None = None,
) -> SimulationResult:
    end = until or scenario.ends_at
    if end.tzinfo is None or not scenario.starts_at <= end <= scenario.ends_at:
        raise ValueError("Simulation end must be timezone-aware and within the scenario")
    funding = funding or {}
    if any(type(amount) is not int or amount < 0 for amount in funding.values()):
        raise ValueError("Funding must be nonnegative integer minor units")
    ledger = Ledger(scenario.opening_cash)
    states = {trade.trade_id: TradeState() for trade in scenario.trades}
    trades = {trade.trade_id: trade for trade in scenario.trades}
    legs = {leg.fx_leg_id: leg for leg in scenario.fx_legs}
    valid_buckets = {trade.bucket for trade in scenario.trades}
    if set(funding) - valid_buckets:
        raise ValueError("Funding refers to an unknown cash pool")
    recorded: list[Event] = []
    # phase 0: prerequisites/credits; 1: funding; 2: settlement; 3: deadline.
    queue: list[tuple] = []
    serial = 0

    def push(at, phase, kind, payload, sequence=0, identifier=""):
        nonlocal serial
        serial += 1
        heapq.heappush(queue, (at, phase, sequence, identifier, serial, kind, payload))

    for event in scenario.events:
        push(event.event_time, 0, "event", event, event.sequence, event.event_id)
    for trade in scenario.trades:
        push(trade.settlement_open_at, 2, "attempt", None)
        push(trade.contractual_deadline_at, 3, "deadline", trade.trade_id)
    if funding:
        push(scenario.decision_at, 1, "fund", None)
    push(end, 4, "end", None)
    outstanding: dict[Bucket, int] = {}
    bridge_costs: dict[Bucket, Decimal] = {}
    last_at = scenario.starts_at
    ordered_trades = sorted(
        scenario.trades,
        key=lambda t: (t.contractual_deadline_at, t.executed_at, t.trade_id),
    )

    def emit(kind: EventType, trade_id: str, at: datetime, known_at: datetime | None = None):
        recorded.append(Event(
            event_id=f"derived:{kind}:{trade_id}", event_type=kind, trade_id=trade_id,
            event_time=at, available_at=known_at or at,
        ))

    def blockers(trade, state):
        reasons = []
        if state.instruction != "INSTRUCTED":
            reasons.append("INSTRUCTIONS_INCOMPLETE")
        if state.ssi != "VALID":
            reasons.append("SSI_INVALID_OR_UNKNOWN")
        if ledger.free(trade.bucket) < trade.settlement_amount_minor:
            reasons.append("INSUFFICIENT_FUNDING")
        return tuple(reasons)

    while queue:
        at, phase, _, _, _, kind, payload = heapq.heappop(queue)
        if at > end:
            break
        elapsed = Decimal(str((at - last_at).total_seconds())) / Decimal(86400)
        for bucket, amount in outstanding.items():
            bridge_costs[bucket] = bridge_costs.get(bucket, Decimal(0)) + (
                whole(amount, bucket.currency) * scenario.costs.annual_bridge_rate
                * elapsed / scenario.costs.day_count
            )
        last_at = at
        if kind == "event":
            event = payload
            recorded.append(event)
            if event.event_type == EventType.FX_CREDITED:
                leg = legs[event.fx_leg_id]
                ledger.post(Posting(
                    posting_id=event.event_id, bucket=leg.bucket,
                    amount_minor=leg.buy_amount_minor, effective_at=at,
                    available_at=event.available_at, kind="FX_CREDIT", source_ref=leg.fx_leg_id,
                ))
            else:
                state = states[event.trade_id]
                if event.event_type in (EventType.SSI_VALID, EventType.SSI_MISMATCH):
                    state.ssi = "VALID" if event.event_type == EventType.SSI_VALID else "MISMATCH"
                else:
                    previous = {
                        EventType.ALLOCATED: "EXECUTED",
                        EventType.CONFIRMED: "ALLOCATED",
                        EventType.AFFIRMED: "CONFIRMED",
                    }[event.event_type]
                    if state.instruction != previous:
                        raise ValueError(f"Invalid lifecycle transition for {event.trade_id}")
                    state.instruction = event.event_type.value
                    if event.event_type == EventType.AFFIRMED:
                        trade = trades[event.trade_id]
                        state.used_fallback = at > trade.affirmation_cutoff_at
                        delay = scenario.fallback_delay_minutes if state.used_fallback else 0
                        push(at + timedelta(minutes=delay), 0, "instruct", event)
            push(at, 2, "attempt", None)
        elif kind == "instruct":
            state = states[payload.trade_id]
            state.instruction = "INSTRUCTED"
            emit(EventType.INSTRUCTED, payload.trade_id, at, max(at, payload.available_at))
            push(at, 2, "attempt", None)
        elif kind == "fund":
            for bucket, amount in sorted(funding.items(), key=lambda item: item[0].key):
                if amount:
                    ledger.post(Posting(
                        posting_id=f"bridge:{bucket.key}", bucket=bucket, amount_minor=amount,
                        effective_at=at, available_at=at, kind="BRIDGE_DRAW", source_ref="policy",
                    ))
                    outstanding[bucket] = amount
            push(at, 2, "attempt", None)
        elif kind == "attempt":
            for trade in ordered_trades:
                state = states[trade.trade_id]
                if state.settled_at or at < trade.settlement_open_at or blockers(trade, state):
                    continue
                ledger.reserve(trade.trade_id, trade.bucket, trade.settlement_amount_minor)
                ledger.settle(trade.trade_id, at)
                state.settled_at = at
                emit(EventType.SETTLED, trade.trade_id, at)
        elif kind == "deadline":
            state = states[payload]
            state.failed = state.settled_at is None
            if state.failed:
                state.reasons = blockers(trades[payload], state)
                emit(EventType.DEADLINE_FAILED, payload, at)
        elif kind == "end" and end == scenario.ends_at:
            for bucket, amount in outstanding.items():
                repayment = min(ledger.free(bucket), amount)
                if repayment:
                    ledger.post(Posting(
                        posting_id=f"repay:{bucket.key}", bucket=bucket, amount_minor=-repayment,
                        effective_at=at, available_at=at, kind="BRIDGE_REPAYMENT", source_ref="policy",
                    ))
                    outstanding[bucket] -= repayment
        ledger.assert_conserved()

    outcomes = []
    for trade in scenario.trades:
        state = states[trade.trade_id]
        late_days = max(Decimal(0), Decimal(str(
            ((state.settled_at or end) - trade.contractual_deadline_at).total_seconds()
        )) / Decimal(86400))
        outcomes.append(Outcome(
            trade_id=trade.trade_id, missed_contractual_deadline=state.failed,
            settled_at=state.settled_at, failure_reasons=state.reasons,
            recovered_late=bool(state.failed and state.settled_at),
            censored=state.settled_at is None,
            late_cost=whole(trade.settlement_amount_minor, trade.bucket.currency)
            * scenario.costs.daily_late_rate * late_days,
            route_fee=whole(scenario.costs.fallback_fee_minor, trade.bucket.currency)
            if state.used_fallback else Decimal(0),
            currency=trade.bucket.currency,
        ))
    return SimulationResult(
        scenario, end, ledger, recorded, states, outcomes, bridge_costs, outstanding,
    )
