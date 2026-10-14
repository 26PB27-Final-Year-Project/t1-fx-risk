"""Validated engine contracts. Amounts are integer minor units, times are UTC."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Currency = Literal["USD", "CAD", "EUR", "GBP", "JPY", "INR"]
Minor = Annotated[int, Field(strict=True, ge=0)]
PositiveMinor = Annotated[int, Field(strict=True, gt=0)]
CURRENCY_EXPONENT = {"USD": 2, "CAD": 2, "EUR": 2, "GBP": 2, "JPY": 0, "INR": 2}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"

    @field_validator("*", mode="after")
    @classmethod
    def normalize_time(cls, value):
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return value


class Money(Contract):
    currency: Currency
    amount_minor: Annotated[int, Field(strict=True)]

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_minor).scaleb(-CURRENCY_EXPONENT[self.currency])


def whole(minor: int, currency: Currency) -> Decimal:
    return Money(currency=currency, amount_minor=minor).amount


class Bucket(Contract):
    cash_pool_id: str = Field(min_length=1)
    currency: Currency

    @property
    def key(self) -> str:
        return f"{self.cash_pool_id}:{self.currency}"


class Trade(Contract):
    trade_id: str = Field(min_length=1)
    scenario_id: str
    counterparty_id: str
    security_id: str
    market_profile_id: str
    bucket: Bucket
    settlement_amount_minor: PositiveMinor
    executed_at: AwareDatetime
    available_at: AwareDatetime
    affirmation_cutoff_at: AwareDatetime
    settlement_open_at: AwareDatetime
    contractual_deadline_at: AwareDatetime
    side: Literal["BUY"] = "BUY"
    instrument_type: Literal["CASH_EQUITY"] = "CASH_EQUITY"

    @model_validator(mode="after")
    def chronology(self):
        if not self.executed_at <= self.available_at <= self.contractual_deadline_at:
            raise ValueError("Trade observation must follow execution and precede deadline")
        if not self.executed_at <= self.settlement_open_at <= self.contractual_deadline_at:
            raise ValueError("Invalid settlement window")
        if not self.executed_at <= self.affirmation_cutoff_at <= self.contractual_deadline_at:
            raise ValueError("Invalid affirmation cutoff")
        return self


class FundingSource(Contract):
    source_id: str
    currency: Currency
    capacity_minor: Minor


class FXLeg(Contract):
    fx_leg_id: str
    bucket: Bucket
    buy_amount_minor: PositiveMinor
    sell_currency: Currency
    sell_amount_minor: PositiveMinor
    funding_source_id: str
    booked_at: AwareDatetime
    available_at: AwareDatetime
    expected_credit_at: AwareDatetime

    @model_validator(mode="after")
    def chronology(self):
        if self.available_at < self.booked_at or self.expected_credit_at < self.booked_at:
            raise ValueError("FX observation/expected receipt cannot precede booking")
        if self.sell_currency == self.bucket.currency:
            raise ValueError("FX leg must exchange different currencies")
        return self


class EventType(StrEnum):
    ALLOCATED = "ALLOCATED"
    CONFIRMED = "CONFIRMED"
    AFFIRMED = "AFFIRMED"
    INSTRUCTED = "INSTRUCTED"
    SSI_VALID = "SSI_VALID"
    SSI_MISMATCH = "SSI_MISMATCH"
    FX_CREDITED = "FX_CREDITED"
    SETTLED = "SETTLED"
    DEADLINE_FAILED = "DEADLINE_FAILED"


class Event(Contract):
    event_id: str
    event_type: EventType
    event_time: AwareDatetime
    available_at: AwareDatetime
    sequence: Annotated[int, Field(strict=True, ge=0)] = 0
    trade_id: str | None = None
    fx_leg_id: str | None = None

    @model_validator(mode="after")
    def valid_event(self):
        if self.available_at < self.event_time:
            raise ValueError("An event cannot be known before it occurs")
        if self.event_type == EventType.FX_CREDITED:
            if self.fx_leg_id is None or self.trade_id is not None:
                raise ValueError("FX credit requires only an FX leg ID")
        elif self.trade_id is None or self.fx_leg_id is not None:
            raise ValueError("Trade event requires only a trade ID")
        return self


class OpeningCash(Contract):
    bucket: Bucket
    amount_minor: Minor


class CostProfile(Contract):
    profile_id: str = "assumed-costs-v1"
    annual_bridge_rate: Decimal = Field(default=Decimal("0.05"), ge=0)
    daily_late_rate: Decimal = Field(default=Decimal("0.001"), ge=0)
    fallback_fee_minor: Minor = 500
    day_count: Literal[360, 365] = 365
    classification: Literal["assumption"] = "assumption"


class SettlementWindow(Contract):
    market_profile_id: str
    opens_at: AwareDatetime
    closes_at: AwareDatetime

    @model_validator(mode="after")
    def valid_window(self):
        if self.opens_at >= self.closes_at:
            raise ValueError("Settlement window must open before it closes")
        return self


class Scenario(Contract):
    scenario_id: str
    starts_at: AwareDatetime
    decision_at: AwareDatetime
    ends_at: AwareDatetime
    trades: tuple[Trade, ...]
    events: tuple[Event, ...]
    opening_cash: tuple[OpeningCash, ...]
    fx_legs: tuple[FXLeg, ...] = ()
    funding_sources: tuple[FundingSource, ...] = ()
    settlement_windows: tuple[SettlementWindow, ...] = ()
    fallback_delay_minutes: Annotated[int, Field(ge=0)] = 30
    costs: CostProfile = CostProfile()
    assumption: str = "Synthetic cash-equity scenario; securities assumed available."

    @model_validator(mode="after")
    def referential_integrity(self):
        if not self.starts_at <= self.decision_at < self.ends_at:
            raise ValueError("Invalid simulation/decision horizon")
        for label, ids in (
            ("trade", [t.trade_id for t in self.trades]),
            ("event", [e.event_id for e in self.events]),
            ("FX leg", [f.fx_leg_id for f in self.fx_legs]),
            ("source", [s.source_id for s in self.funding_sources]),
            ("opening balance", [b.bucket.key for b in self.opening_cash]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate {label} ID")
        trades = {t.trade_id: t for t in self.trades}
        legs = {f.fx_leg_id: f for f in self.fx_legs}
        sources = {s.source_id: s for s in self.funding_sources}
        for trade in self.trades:
            if trade.scenario_id != self.scenario_id:
                raise ValueError("Trade belongs to a different scenario")
            if not self.starts_at <= trade.executed_at <= trade.contractual_deadline_at <= self.ends_at:
                raise ValueError("Trade outside scenario horizon")
        last_close = {}
        for window in sorted(self.settlement_windows, key=lambda w: w.opens_at):
            if not self.starts_at <= window.opens_at < window.closes_at <= self.ends_at:
                raise ValueError("Settlement window outside scenario horizon")
            if window.market_profile_id in last_close and window.opens_at <= last_close[window.market_profile_id]:
                raise ValueError("Settlement windows overlap")
            last_close[window.market_profile_id] = window.closes_at
        if self.settlement_windows:
            for trade in self.trades:
                if not any(w.market_profile_id == trade.market_profile_id
                           and w.opens_at <= trade.contractual_deadline_at <= w.closes_at
                           for w in self.settlement_windows):
                    raise ValueError("Trade deadline is outside its settlement windows")
        committed: dict[str, int] = {}
        for leg in self.fx_legs:
            source = sources.get(leg.funding_source_id)
            if source is None or source.currency != leg.sell_currency:
                raise ValueError("FX receipt requires a matching source-currency commitment")
            committed[source.source_id] = committed.get(source.source_id, 0) + leg.sell_amount_minor
            if committed[source.source_id] > source.capacity_minor:
                raise ValueError("FX source commitment exceeds capacity")
        credited: set[str] = set()
        for event in self.events:
            if not self.starts_at <= event.event_time <= self.ends_at:
                raise ValueError("Event outside scenario horizon")
            if event.event_type in (EventType.SETTLED, EventType.DEADLINE_FAILED, EventType.INSTRUCTED):
                raise ValueError("Outcome/instruction events are generated by the engine")
            if event.trade_id:
                trade = trades.get(event.trade_id)
                if trade is None or event.event_time < trade.executed_at:
                    raise ValueError("Unknown or not-yet-executed trade")
            if event.fx_leg_id:
                leg = legs.get(event.fx_leg_id)
                if leg is None or event.event_time < leg.booked_at:
                    raise ValueError("Unknown or not-yet-booked FX leg")
                if event.fx_leg_id in credited:
                    raise ValueError("An FX leg can be credited only once")
                credited.add(event.fx_leg_id)
        return self


class Posting(Contract):
    posting_id: str
    bucket: Bucket
    amount_minor: Annotated[int, Field(strict=True)]
    effective_at: AwareDatetime
    available_at: AwareDatetime
    kind: Literal["FX_CREDIT", "SETTLEMENT", "BRIDGE_DRAW", "BRIDGE_REPAYMENT"]
    source_ref: str

    @model_validator(mode="after")
    def valid_posting(self):
        if self.available_at < self.effective_at:
            raise ValueError("Cash movement cannot be known before its effective time")
        credit = self.kind in ("FX_CREDIT", "BRIDGE_DRAW")
        if self.amount_minor == 0 or (self.amount_minor > 0) != credit:
            raise ValueError("Posting sign does not match cash movement kind")
        return self


class Outcome(Contract):
    trade_id: str
    missed_contractual_deadline: bool | None
    settled_at: AwareDatetime | None
    failure_reasons: tuple[str, ...]
    recovered_late: bool
    censored: bool
    late_cost: Decimal
    route_fee: Decimal
    currency: Currency


class Snapshot(Contract):
    trade_id: str
    as_of: AwareDatetime
    horizon_at: AwareDatetime
    bucket: Bucket
    remaining_obligation_minor: Minor
    instruction_state: str
    ssi_status: str
    observed_settled: bool
    minutes_to_affirmation_cutoff: float
    minutes_to_settlement_deadline: float
    pool_usable_minor: Annotated[int, Field(strict=True)]


class RulePrediction(Contract):
    trade_id: str
    model_id: Literal["rules-v1"] = "rules-v1"
    status: Literal["UNCALIBRATED_RULE_SCORE", "NOT_APPLICABLE"]
    score: Annotated[int, Field(ge=0, le=100)]
    p_fail_any: None = None
    factors: tuple[str, ...]
    recommended_action: str
