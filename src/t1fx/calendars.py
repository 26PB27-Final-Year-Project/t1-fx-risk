"""Explicit calendar coverage; no implicit market holiday assumptions."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from typing import Literal

from pydantic import Field, field_serializer, model_validator

from t1fx.domain import Contract


class CalendarProfile(Contract):
    profile_id: str
    timezone: str
    valid_from: date
    valid_to: date
    holidays: frozenset[date] = frozenset()
    affirmation_time: time = time(21)
    settlement_time: time = time(16)
    settlement_lag: int = Field(default=1, ge=0)
    early_closes: dict[date, time] = Field(default_factory=dict)
    classification: Literal["assumption", "scenario_override", "sourced"] = "assumption"
    source_url: str | None = None

    @model_validator(mode="after")
    def validate_profile(self):
        ZoneInfo(self.timezone)
        if self.valid_to < self.valid_from:
            raise ValueError("Invalid calendar coverage")
        if self.affirmation_time.tzinfo or self.settlement_time.tzinfo:
            raise ValueError("Cutoff clock times must be local, without an attached timezone")
        if self.classification == "sourced" and not self.source_url:
            raise ValueError("A sourced calendar needs its source URL")
        if any(not self.valid_from <= day <= self.valid_to for day in self.holidays | self.early_closes.keys()):
            raise ValueError("Calendar overrides must lie within profile coverage")
        if any(clock.tzinfo for clock in self.early_closes.values()):
            raise ValueError("Early-close clock times must be local and timezone-naive")
        return self

    @field_serializer("holidays", when_used="json")
    def stable_holidays(self, value):
        return [day.isoformat() for day in sorted(value)]

    def require_coverage(self, day: date) -> None:
        if not self.valid_from <= day <= self.valid_to:
            raise ValueError(f"Calendar {self.profile_id} has no coverage for {day}")

    def is_business_day(self, day: date) -> bool:
        self.require_coverage(day)
        return day.weekday() < 5 and day not in self.holidays

    def advance(self, day: date, business_days: int) -> date:
        self.require_coverage(day)
        if business_days < 0:
            raise ValueError("Business-day increment cannot be negative")
        while business_days:
            day += timedelta(days=1)
            if self.is_business_day(day):
                business_days -= 1
        return day

    def settlement_date(self, trade_date: date) -> date:
        if not self.is_business_day(trade_date):
            raise ValueError("Trade date is not a business day in this profile")
        return self.advance(trade_date, self.settlement_lag)

    def cutoff_at(self, day: date, kind: str) -> datetime:
        if not self.is_business_day(day):
            raise ValueError("Cutoff requested on a closed day")
        if kind == "affirmation":
            local_time = self.affirmation_time
        elif kind == "settlement":
            local_time = self.early_closes.get(day, self.settlement_time)
        else:
            raise ValueError(f"Unknown cutoff kind: {kind}")
        return local_instant(day, local_time, self.timezone)


def local_instant(day: date, clock: time, timezone: str) -> datetime:
    """Reject ambiguous/nonexistent local times instead of silently selecting a fold."""
    naive = datetime.combine(day, clock)
    zone = ZoneInfo(timezone)
    candidates = {
        candidate.astimezone(UTC)
        for fold in (0, 1)
        if (candidate := naive.replace(tzinfo=zone, fold=fold))
        .astimezone(UTC)
        .astimezone(zone)
        .replace(tzinfo=None)
        == naive
    }
    if len(candidates) != 1:
        raise ValueError("Ambiguous or nonexistent local cutoff; supply an explicit override")
    return candidates.pop()


def joint_value_date(day: date, lag: int, calendars: tuple[CalendarProfile, ...]) -> date:
    """Explicit simplified joint-business-day convention, not a universal FX rule."""
    if not calendars or lag < 0:
        raise ValueError("A nonnegative lag and currency calendars are required")
    for calendar in calendars:
        calendar.require_coverage(day)
    remaining = lag
    while remaining:
        day += timedelta(days=1)
        if all([calendar.is_business_day(day) for calendar in calendars]):
            remaining -= 1
    if not all([calendar.is_business_day(day) for calendar in calendars]):
        raise ValueError("Value date falls on a closed currency calendar")
    return day
