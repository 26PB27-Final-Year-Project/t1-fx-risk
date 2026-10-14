from datetime import date, datetime, time

import pytest
from pydantic import ValidationError

from t1fx.calendars import CalendarProfile, joint_value_date, local_instant
from t1fx.domain import Money, Scenario
from t1fx.fixtures import instant, three_trade_scenario


def profile(**overrides):
    return CalendarProfile(
        profile_id="test-v1", timezone="America/New_York",
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31), **overrides,
    )


def test_minor_unit_exponents_and_strict_integers():
    assert str(Money(currency="USD", amount_minor=1234).amount) == "12.34"
    assert str(Money(currency="JPY", amount_minor=1234).amount) == "1234"
    for invalid in (True, 1.2, "123"):
        with pytest.raises(ValidationError):
            Money(currency="USD", amount_minor=invalid)


def test_naive_trade_time_rejected_and_offsets_normalized():
    data = three_trade_scenario().trades[0].model_dump()
    data["executed_at"] = datetime(2026, 10, 13, 14)
    with pytest.raises(ValidationError):
        type(three_trade_scenario().trades[0]).model_validate(data)
    data["executed_at"] = datetime.fromisoformat("2026-10-13T19:30:00+05:30")
    trade = type(three_trade_scenario().trades[0]).model_validate(data)
    assert trade.executed_at == instant("2026-10-13T14:00:00Z")


def test_calendar_weekend_holiday_dst_and_early_close():
    calendar = profile(holidays=frozenset({date(2026, 10, 12)}),
                       early_closes={date(2026, 11, 27): time(13)})
    assert calendar.settlement_date(date(2026, 10, 9)) == date(2026, 10, 13)
    assert calendar.cutoff_at(date(2026, 10, 30), "affirmation") == instant("2026-10-31T01:00:00Z")
    assert calendar.cutoff_at(date(2026, 11, 2), "affirmation") == instant("2026-11-03T02:00:00Z")
    assert calendar.cutoff_at(date(2026, 11, 27), "settlement") == instant("2026-11-27T18:00:00Z")


def test_calendar_rejects_missing_coverage_closed_days_and_ambiguous_instants():
    calendar = profile()
    with pytest.raises(ValueError, match="coverage"):
        calendar.advance(date(2026, 12, 31), 1)
    with pytest.raises(ValueError, match="closed"):
        calendar.cutoff_at(date(2026, 10, 11), "settlement")
    for day, clock in [(date(2026, 11, 1), time(1, 30)), (date(2026, 3, 8), time(2, 30))]:
        with pytest.raises(ValueError, match="Ambiguous or nonexistent"):
            local_instant(day, clock, "America/New_York")


def test_joint_currency_holiday():
    usd = profile()
    eur = profile(holidays=frozenset({date(2026, 10, 14)}))
    assert joint_value_date(date(2026, 10, 13), 2, (usd, eur)) == date(2026, 10, 16)


@pytest.mark.parametrize("mutation", ["duplicate_event", "duplicate_credit", "unfunded_fx"])
def test_scenario_referential_integrity(mutation):
    data = three_trade_scenario().model_dump(mode="json")
    if mutation == "duplicate_event":
        data["events"].append(data["events"][0])
    elif mutation == "duplicate_credit":
        duplicate = dict(data["events"][-1], event_id="duplicate-credit")
        data["events"].append(duplicate)
    else:
        data["funding_sources"][0]["capacity_minor"] = 0
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)
