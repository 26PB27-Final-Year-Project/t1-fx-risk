"""Hand-checkable starting scenario; all market times and cost rates are assumptions."""

from datetime import datetime
from random import Random

from t1fx.domain import (
    Bucket,
    Event,
    EventType,
    FundingSource,
    FXLeg,
    OpeningCash,
    Scenario,
    Trade,
)


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def three_trade_scenario() -> Scenario:
    scenario_id = "three-trades-v1"
    buckets = [Bucket(cash_pool_id=name, currency="USD") for name in ("timely", "late", "fx")]
    trades = tuple(Trade(
        trade_id=trade_id, scenario_id=scenario_id, counterparty_id=f"CP-{index+1}",
        security_id=f"EQUITY-{index+1}", market_profile_id="us-equity-demo-v1",
        bucket=bucket, settlement_amount_minor=amount,
        executed_at=instant("2026-10-13T14:00:00Z"),
        available_at=instant("2026-10-13T14:00:00Z"),
        affirmation_cutoff_at=instant("2026-10-14T01:00:00Z"),
        settlement_open_at=instant("2026-10-14T13:00:00Z"),
        contractual_deadline_at=instant("2026-10-14T20:00:00Z"),
    ) for index, (trade_id, bucket, amount) in enumerate(zip(
        ("T-TIMELY", "T-LATE-AFFIRM", "T-FX-GAP"), buckets,
        (1_000_000, 2_000_000, 3_000_000), strict=True,
    )))
    events = []
    for trade in trades:
        sequence = [
            (EventType.ALLOCATED, "2026-10-13T14:10:00Z"),
            (EventType.SSI_VALID, "2026-10-13T14:30:00Z"),
            (EventType.CONFIRMED, "2026-10-13T15:00:00Z"),
            (EventType.AFFIRMED, "2026-10-14T02:00:00Z" if trade.trade_id == "T-LATE-AFFIRM"
             else "2026-10-13T16:00:00Z"),
        ]
        for index, (kind, timestamp) in enumerate(sequence):
            events.append(Event(
                event_id=f"{trade.trade_id}:{kind}", trade_id=trade.trade_id, event_type=kind,
                event_time=instant(timestamp), available_at=instant(timestamp), sequence=index,
            ))
    fx = FXLeg(
        fx_leg_id="FX-EUR-USD-1", bucket=buckets[2], buy_amount_minor=3_000_000,
        sell_currency="EUR", sell_amount_minor=2_700_000, funding_source_id="EUR-COMMITTED",
        booked_at=instant("2026-10-13T15:00:00Z"),
        available_at=instant("2026-10-13T15:00:00Z"),
        expected_credit_at=instant("2026-10-15T14:00:00Z"),
    )
    events.append(Event(
        event_id="credit:FX-EUR-USD-1", event_type=EventType.FX_CREDITED,
        event_time=fx.expected_credit_at, available_at=fx.expected_credit_at, fx_leg_id=fx.fx_leg_id,
    ))
    return Scenario(
        scenario_id=scenario_id, starts_at=instant("2026-10-13T14:00:00Z"),
        decision_at=instant("2026-10-14T12:00:00Z"), ends_at=instant("2026-10-15T20:00:00Z"),
        trades=trades, events=tuple(events), fx_legs=(fx,),
        funding_sources=(FundingSource(
            source_id="EUR-COMMITTED", currency="EUR", capacity_minor=2_700_000,
        ),),
        opening_cash=tuple(OpeningCash(bucket=bucket, amount_minor=amount)
                           for bucket, amount in zip(buckets, (1_000_000, 2_000_000, 0), strict=True)),
    )


def demo_scenario(trade_count: int = 50, seed: int = 42) -> Scenario:
    """Build a reproducible dashboard sample while retaining the small reference fixture."""
    if trade_count < 3 or trade_count > 100:
        raise ValueError("Demo trade count must be between 3 and 100")
    reference = three_trade_scenario()
    scenario_id = f"demo-{trade_count}-s{seed}-v1"
    rng = Random(seed)
    trades = [trade.model_copy(update={"scenario_id": scenario_id}) for trade in reference.trades]
    events = list(reference.events)
    opening_cash = list(reference.opening_cash)
    extra_pools = [Bucket(cash_pool_id=f"DEMO-{i:02}", currency="USD") for i in range(1, 13)]
    obligations = {pool: 0 for pool in extra_pools}

    for index in range(trade_count - len(trades)):
        trade_id = f"T-DEMO-{index + 1:03}"
        pool = extra_pools[index % len(extra_pools)]
        amount = rng.randrange(550_000, 5_500_001, 25_000)
        obligations[pool] += amount
        trade = Trade(
            trade_id=trade_id, scenario_id=scenario_id,
            counterparty_id=f"CP-{rng.randrange(1, 21):02}",
            security_id=f"EQUITY-{rng.randrange(1, 101):03}",
            market_profile_id="us-equity-demo-v1", bucket=pool,
            settlement_amount_minor=amount,
            executed_at=instant("2026-10-13T14:00:00Z"),
            available_at=instant("2026-10-13T14:00:00Z"),
            affirmation_cutoff_at=instant("2026-10-14T01:00:00Z"),
            settlement_open_at=instant("2026-10-14T13:00:00Z"),
            contractual_deadline_at=instant("2026-10-14T20:00:00Z"),
        )
        trades.append(trade)
        late_affirmation = index % 11 == 3
        invalid_ssi = index % 13 == 5
        sequence = (
            (EventType.ALLOCATED, "2026-10-13T14:10:00Z"),
            (EventType.SSI_MISMATCH if invalid_ssi else EventType.SSI_VALID,
             "2026-10-13T14:30:00Z"),
            (EventType.CONFIRMED, "2026-10-13T15:00:00Z"),
            (EventType.AFFIRMED, "2026-10-14T02:00:00Z" if late_affirmation
             else "2026-10-13T16:00:00Z"),
        )
        for order, (kind, timestamp) in enumerate(sequence):
            events.append(Event(
                event_id=f"{trade_id}:{kind}", trade_id=trade_id, event_type=kind,
                event_time=instant(timestamp), available_at=instant(timestamp), sequence=order,
            ))
        if invalid_ssi:
            events.append(Event(
                event_id=f"{trade_id}:SSI_VALID:repair", trade_id=trade_id,
                event_type=EventType.SSI_VALID,
                event_time=instant("2026-10-14T14:00:00Z"),
                available_at=instant("2026-10-14T14:00:00Z"), sequence=4,
            ))

    for index, pool in enumerate(extra_pools):
        fraction = (55, 70, 85, 100)[index % 4]
        opening_cash.append(OpeningCash(
            bucket=pool, amount_minor=obligations[pool] * fraction // 100,
        ))
    return Scenario.model_validate({**reference.model_dump(), **{
        "scenario_id": scenario_id, "trades": tuple(trades), "events": tuple(events),
        "opening_cash": tuple(opening_cash),
        "assumption": "Seeded synthetic USD demo; cash, calendars, FX and costs are assumptions.",
    }})
