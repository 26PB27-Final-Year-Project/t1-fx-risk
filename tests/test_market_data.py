from datetime import date, time
from pathlib import Path
from t1fx.generation import load_config, generate_day


def test_us_trading_payment_holidays_and_early_close_are_distinct():
    config = load_config(Path('configs/generation/smoke.json'))
    assert not config.market.is_business_day(date(2026, 7, 3))
    assert config.market.is_business_day(date(2026, 11, 11))
    assert config.market.settlement_date(date(2026, 11, 10)) == date(2026, 11, 12)
    assert config.market.trading_early_closes[date(2026, 11, 27)] == time(13)
    scenario = generate_day(config, date(2026, 11, 27), 20, 'test').scenario
    from zoneinfo import ZoneInfo
    assert all(t.executed_at.astimezone(ZoneInfo('America/New_York')).hour < 13 for t in scenario.trades)
    usd = config.fx_routes[0].buy_calendar
    assert usd.is_business_day(date(2026, 7, 3))
    assert not usd.is_business_day(date(2027, 7, 5))
