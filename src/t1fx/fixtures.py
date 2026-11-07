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


def current_utc():
    from t1fx.online_clock import online_time
    return datetime.fromisoformat(online_time()["utc_now"])


def demo_scenario(trade_count: int = 50, seed: int = 42, month=None) -> Scenario:
    """A trailing 30-day schedule; explicit month remains available for research replay."""
    import calendar
    from datetime import date, timedelta, time
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from t1fx.generation import generate_day, load_config
    from t1fx.calendars import local_instant

    if trade_count < 3 or trade_count > 100:
        raise ValueError("Demo trade count must be between 3 and 100")
    rolling = month is None
    now = current_utc()
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    first = today - timedelta(days=29)
    month = month or first
    config = load_config(Path("configs/generation/smoke.json")).model_copy(update={"seed": seed})
    year = month.year
    market = config.market.model_copy(update={"valid_from": date(year, 1, 1), "valid_to": date(year+1, 12, 31)})
    routes = tuple(route.model_copy(update={
        "sell_calendar": route.sell_calendar.model_copy(update={"valid_from": date(year, 1, 1), "valid_to": date(year+1, 12, 31)}),
        "buy_calendar": route.buy_calendar.model_copy(update={"valid_from": date(year, 1, 1), "valid_to": date(year+1, 12, 31)}),
    }) for route in config.fx_routes)
    config = config.model_copy(update={"market": market, "fx_routes": routes})
    days = [date(year, month.month, day) for day in range(1, calendar.monthrange(year, month.month)[1]+1)
            if market.is_business_day(date(year, month.month, day))]
    if rolling:
        days = [first + timedelta(days=i) for i in range(30)
                if market.is_business_day(first + timedelta(days=i))]
    if len(days) > trade_count:
        days = [days[round(i * (len(days)-1) / (trade_count-1))] for i in range(trade_count)]
    combined = None
    windows = {}
    scenario_id = f"demo-{trade_count}-s{seed}-usd-{month:%Y-%m}" + (f"-30d-{today}" if rolling else "")
    for index, day in enumerate(days):
        count = trade_count // len(days) + (index < trade_count % len(days))
        # Rotate the daily route order so short daily samples cover every funding currency.
        offset = index % len(config.fx_routes)
        daily = config.model_copy(update={"fx_routes": config.fx_routes[offset:] + config.fx_routes[:offset]})
        data = generate_day(daily, day, count, "test").scenario.model_dump(mode="json")
        prefix = day.isoformat() + ":"
        for table in ("trades", "fx_legs", "opening_cash"):
            for row in data[table]:
                row["bucket"]["cash_pool_id"] = prefix + row["bucket"]["cash_pool_id"]
        for row in data["funding_sources"]:
            row["source_id"] = prefix + row["source_id"]
        for row in data["fx_legs"]:
            row["funding_source_id"] = prefix + row["funding_source_id"]
        for trade in data["trades"]:
            trade["scenario_id"] = scenario_id
        for window in data["settlement_windows"]:
            windows[window["opens_at"]] = window
        if combined is None:
            combined = data
        else:
            for table in ("trades", "events", "fx_legs", "opening_cash", "funding_sources"):
                combined[table].extend(data[table])
            combined["ends_at"] = data["ends_at"]
    if rolling:
        combined["starts_at"] = local_instant(first, time(0), market.timezone).isoformat()
    combined["scenario_id"] = scenario_id
    combined["settlement_windows"] = list(windows.values())
    combined["assumption"] = "Synthetic USD business-day schedule; observations respect execution time. Daily cash pools are isolated."
    start = datetime.fromisoformat(combined["starts_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(combined["ends_at"].replace("Z", "+00:00"))
    combined["decision_at"] = max(start, min(now, end-timedelta(seconds=1))).isoformat()
    return Scenario.model_validate(combined)
