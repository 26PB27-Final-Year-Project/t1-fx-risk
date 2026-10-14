"""Seeded exogenous scenarios. Hidden draws never enter observable trade records."""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import Field, model_validator

from t1fx.calendars import CalendarProfile, joint_value_date, local_instant
from t1fx.domain import (
    Bucket, Contract, CostProfile, Currency, Event, EventType, FundingSource,
    FXLeg, OpeningCash, Scenario, SettlementWindow, Trade,
)


class FXRouteProfile(Contract):
    profile_id: str
    sell_currency: Currency = "EUR"
    buy_currency: Literal["USD"] = "USD"
    timezone: str = "America/New_York"
    booking_cutoff: time = time(17)
    credit_time: time = time(9)
    value_lag: int = Field(default=2, ge=0, le=5)
    sell_per_buy: Decimal = Field(default=Decimal("0.9"), gt=0)
    sell_calendar: CalendarProfile
    buy_calendar: CalendarProfile
    classification: Literal["assumption", "scenario_override"] = "assumption"

    @model_validator(mode="after")
    def valid_route(self):
        ZoneInfo(self.timezone)
        if self.sell_currency == self.buy_currency:
            raise ValueError("An FX route must exchange different currencies")
        if self.booking_cutoff.tzinfo or self.credit_time.tzinfo:
            raise ValueError("Route clock times must be local and timezone-naive")
        if self.sell_currency == "JPY":
            raise ValueError("Generator source amounts currently require a two-decimal currency")
        return self

    def expected_credit(self, booked_at: datetime) -> datetime:
        if booked_at.tzinfo is None:
            raise ValueError("FX booking time must be timezone-aware")
        day = booked_at.astimezone(ZoneInfo(self.timezone)).date()
        calendars = (self.sell_calendar, self.buy_calendar)

        def open_day(candidate):
            return all([calendar.is_business_day(candidate) for calendar in calendars])

        # A closed day or a booking strictly after cutoff rolls to the next joint day.
        if not open_day(day) or booked_at > local_instant(day, self.booking_cutoff, self.timezone):
            day += timedelta(days=1)
            while not open_day(day):
                day += timedelta(days=1)
        value_date = joint_value_date(day, self.value_lag, calendars)
        credit = local_instant(value_date, self.credit_time, self.timezone)
        if credit < booked_at:
            raise ValueError("Configured credit time precedes booking; select a later route")
        return credit


class GenerationConfig(Contract):
    config_id: str = Field(default="smoke-v1", pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    seed: int = Field(default=42, ge=0, strict=True)
    trade_count: int = Field(default=1000, ge=4, le=100_000, strict=True)
    trading_days: int = Field(default=10, ge=4, le=100, strict=True)
    start_date: date = date(2026, 10, 13)
    counterparty_count: int = Field(default=20, ge=1, le=1000)
    pool_count: int = Field(default=8, ge=1, le=100)
    market: CalendarProfile
    fx_route: FXRouteProfile
    arrival_open: time = time(9, 30)
    arrival_close: time = time(15, 30)
    settlement_open: time = time(9)
    decision_time: time = time(8)
    recovery_business_days: int = Field(default=4, ge=3, le=10)
    opening_cash_fraction: Decimal = Field(default=Decimal("0.95"), ge=0, le=1)
    late_confirmation_rate: float = Field(default=0.08, ge=0, le=1)
    missed_fx_cutoff_rate: float = Field(default=0.10, ge=0, le=1)
    ssi_mismatch_rate: float = Field(default=0.06, ge=0, le=1)
    funding_stress_rate: float = Field(default=0.10, ge=0, le=1)
    funding_stress_fraction: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    notional_median_minor: int = Field(default=1_000_000, ge=100, le=1_000_000_000)
    notional_log_sigma: float = Field(default=0.8, gt=0, le=3)
    observation_delay_minutes: int = Field(default=15, ge=0, le=180)
    costs: CostProfile = CostProfile()

    @model_validator(mode="after")
    def coherent(self):
        if self.trade_count < self.trading_days:
            raise ValueError("Each trading day must contain at least one trade")
        if self.market.settlement_lag != 1:
            raise ValueError("This generator implements the configured T+1 equity profile")
        clocks = (self.arrival_open, self.arrival_close, self.settlement_open, self.decision_time)
        if any(clock.tzinfo for clock in clocks):
            raise ValueError("Generator clock times must be local and timezone-naive")
        if not self.arrival_open < self.arrival_close < self.market.affirmation_time:
            raise ValueError("Execution window must precede the affirmation cutoff")
        if not self.decision_time < self.settlement_open < self.market.settlement_time:
            raise ValueError("Decision must precede the settlement window")
        if not self.market.is_business_day(self.start_date):
            raise ValueError("Generation must start on an open market day")
        return self


def load_config(path: Path) -> GenerationConfig:
    return GenerationConfig.model_validate_json(path.read_text())


def random_stream(seed: int, *identity: object) -> np.random.Generator:
    """Mechanism-specific streams are stable when unrelated draws are added."""
    key = "\0".join((str(seed), *(str(item) for item in identity))).encode()
    words = np.frombuffer(hashlib.sha256(key).digest(), dtype="<u4").tolist()
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(words)))


@dataclass(frozen=True)
class GeneratedDay:
    scenario: Scenario
    split: str
    injections: tuple[dict, ...]
    counterparty_truth: tuple[dict, ...]


def generate_day(config: GenerationConfig, day: date, count: int, split: str) -> GeneratedDay:
    market = config.market
    settlement_day = market.settlement_date(day)
    deadline = market.cutoff_at(settlement_day, "settlement")
    settlement_open = local_instant(settlement_day, config.settlement_open, market.timezone)
    decision = local_instant(settlement_day, config.decision_time, market.timezone)
    start = local_instant(day, config.arrival_open, market.timezone)
    close = local_instant(day, config.arrival_close, market.timezone)
    if not decision < settlement_open < deadline:
        raise ValueError("Early-close override is incompatible with the configured decision window")
    if close >= market.cutoff_at(day, "settlement"):
        raise ValueError("Execution window crosses the market's configured close")
    end_day = market.advance(settlement_day, config.recovery_business_days)
    end = market.cutoff_at(end_day, "settlement")
    scenario_id = f"{config.config_id}-s{config.seed}-{day.isoformat()}"
    pools = [Bucket(cash_pool_id=f"POOL-{i:03}", currency="USD") for i in range(config.pool_count)]
    cash = {pool: 0 for pool in pools}
    trades, events, legs, injections = [], [], [], []
    counterparties = []
    for index in range(config.counterparty_count):
        cp_id = f"CP-{index:04}"
        rng = random_stream(config.seed, cp_id, "counterparty")
        counterparties.append({
            "counterparty_id": cp_id,
            "delay_multiplier": float(np.clip(rng.lognormal(0, 0.65), 0.3, 6)),
        })
    source_total = 0
    for index in range(count):
        trade_id = f"{scenario_id}-T{index:06}"
        rng = random_stream(config.seed, day, index, "trade")
        cp = counterparties[int(rng.integers(len(counterparties)))]
        bucket = pools[int(rng.integers(len(pools)))]
        amount = int(np.clip(rng.lognormal(np.log(config.notional_median_minor),
                                           config.notional_log_sigma), 100, 1_000_000_000))
        arrival_seconds = int(rng.beta(2, 2) * (close-start).total_seconds())
        executed = start + timedelta(seconds=arrival_seconds)
        trade = Trade(
            trade_id=trade_id, scenario_id=scenario_id, counterparty_id=cp["counterparty_id"],
            security_id=f"EQ-{int(rng.integers(200)):04}", market_profile_id=market.profile_id,
            bucket=bucket, settlement_amount_minor=amount, executed_at=executed,
            available_at=executed, affirmation_cutoff_at=market.cutoff_at(day, "affirmation"),
            settlement_open_at=settlement_open, contractual_deadline_at=deadline,
        )
        trades.append(trade)
        flags = {
            mechanism: bool(random_stream(config.seed, day, index, mechanism).random() < rate)
            for mechanism, rate in (
                ("late_confirmation", config.late_confirmation_rate),
                ("missed_fx_cutoff", config.missed_fx_cutoff_rate),
                ("ssi_mismatch", config.ssi_mismatch_rate),
                ("funding_stress", config.funding_stress_rate),
            )
        }
        injections.append({"scenario_id": scenario_id, "trade_id": trade_id, **flags})
        delay_rng = random_stream(config.seed, day, index, "lifecycle_delays")
        allocated = executed + timedelta(minutes=float(delay_rng.lognormal(1, 0.5)))
        confirmed = allocated + timedelta(minutes=float(delay_rng.lognormal(2, 0.8)*cp["delay_multiplier"]))
        if flags["late_confirmation"]:
            confirmed = deadline + timedelta(minutes=float(delay_rng.uniform(5, 180)))
        affirmed = confirmed + timedelta(minutes=float(delay_rng.lognormal(2.5, 0.7)*cp["delay_multiplier"]))

        def add_event(kind, at, suffix="", sequence=0):
            if at > end:
                return
            obs_rng = random_stream(config.seed, day, index, "observation", kind, suffix)
            known_at = at + timedelta(seconds=int(obs_rng.integers(
                config.observation_delay_minutes*60+1,
            )))
            events.append(Event(
                event_id=f"{trade_id}:{kind}{suffix}", trade_id=trade_id, event_type=kind,
                event_time=at, available_at=known_at, sequence=sequence,
            ))

        add_event(EventType.ALLOCATED, allocated, sequence=0)
        add_event(EventType.CONFIRMED, confirmed, sequence=1)
        add_event(EventType.AFFIRMED, affirmed, sequence=2)
        ssi_at = executed + timedelta(minutes=5)
        if flags["ssi_mismatch"]:
            add_event(EventType.SSI_MISMATCH, ssi_at)
            repair_rng = random_stream(config.seed, day, index, "ssi_repair")
            add_event(EventType.SSI_VALID, ssi_at+timedelta(hours=float(repair_rng.uniform(4, 60))), ":repair")
        else:
            add_event(EventType.SSI_VALID, ssi_at)
        prefund = Decimal(amount)*config.opening_cash_fraction
        if flags["funding_stress"]:
            prefund *= 1-config.funding_stress_fraction
        cash[bucket] += int(prefund)

        # FX replenishes the contractual obligation; opening cash is a separate initial resource.
        booked = executed + timedelta(minutes=10)
        if flags["missed_fx_cutoff"]:
            booked = local_instant(day, config.fx_route.booking_cutoff, config.fx_route.timezone)
            booked += timedelta(minutes=30)
        booked = max(booked, executed)
        expected = config.fx_route.expected_credit(booked)
        sell_amount = int((Decimal(amount)*config.fx_route.sell_per_buy).to_integral_value(rounding=ROUND_CEILING))
        source_total += sell_amount
        fx_id = f"{trade_id}:FX"
        legs.append(FXLeg(
            fx_leg_id=fx_id, bucket=bucket, buy_amount_minor=amount,
            sell_currency=config.fx_route.sell_currency, sell_amount_minor=sell_amount,
            funding_source_id="SOURCE-COMMITTED", booked_at=booked, available_at=booked,
            expected_credit_at=expected,
        ))
        # Actual credit is an exogenous draw. The expected credit field never contains this draw.
        credit_rng = random_stream(config.seed, day, index, "fx_credit_delay")
        actual_credit = expected+timedelta(minutes=float(credit_rng.lognormal(2, 0.6)))
        if actual_credit <= end:
            events.append(Event(
                event_id=f"credit:{fx_id}", event_type=EventType.FX_CREDITED, fx_leg_id=fx_id,
                event_time=actual_credit, available_at=actual_credit,
            ))
    windows = []
    window_day = settlement_day
    while window_day <= end_day:
        windows.append(SettlementWindow(
            market_profile_id=market.profile_id,
            opens_at=local_instant(window_day, config.settlement_open, market.timezone),
            closes_at=market.cutoff_at(window_day, "settlement"),
        ))
        if window_day == end_day:
            break
        window_day = market.advance(window_day, 1)
    scenario = Scenario(
        scenario_id=scenario_id, starts_at=start, decision_at=decision, ends_at=end,
        trades=tuple(trades), events=tuple(events), fx_legs=tuple(legs),
        opening_cash=tuple(OpeningCash(bucket=bucket, amount_minor=amount) for bucket, amount in cash.items()),
        funding_sources=(FundingSource(source_id="SOURCE-COMMITTED",
                                       currency=config.fx_route.sell_currency, capacity_minor=source_total),),
        costs=config.costs,
        settlement_windows=tuple(windows),
        assumption="Synthetic USD equities, assumed calendar/FX conventions, securities available, fixed-0 reference policy.",
    )
    return GeneratedDay(scenario, split, tuple(injections), tuple(counterparties))


def generate_dataset(config: GenerationConfig):
    """Whole-day partitions with recovery-horizon embargo between partitions."""
    train_days = min(config.trading_days-3, max(1, int(config.trading_days*0.6)))
    tuning_days = min(config.trading_days-train_days-2, max(1, int(config.trading_days*0.15)))
    calibration_days = min(config.trading_days-train_days-tuning_days-1,
                           max(1, int(config.trading_days*0.1)))
    counts = [train_days, tuning_days, calibration_days]
    counts.append(config.trading_days-sum(counts))
    day = config.start_date
    day_index = 0
    for split, day_count in zip(("train", "tuning", "calibration", "test"), counts, strict=True):
        last_end = None
        for split_day in range(day_count):
            count = config.trade_count//config.trading_days + (day_index < config.trade_count % config.trading_days)
            generated = generate_day(config, day, count, split)
            yield generated
            last_end = generated.scenario.ends_at
            if split_day + 1 < day_count:
                day = config.market.advance(day, 1)
            day_index += 1
        # Training labels mature before any trade in the following fitted/evaluated partition.
        if split != "test":
            local_end_day = last_end.astimezone(ZoneInfo(config.market.timezone)).date()
            day = config.market.advance(local_end_day, 1)
