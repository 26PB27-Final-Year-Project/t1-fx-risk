"""Hand-checkable starting scenario; all market times and cost rates are assumptions."""

from datetime import datetime

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
