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


def test_receipt_mixture_has_early_on_time_late_and_unresolved_cases():
    from t1fx.domain import EventType
    config = load_config(Path('configs/generation/smoke.json'))
    scenario = generate_day(config, config.start_date, 200, 'test').scenario
    credits = {e.fx_leg_id: e.event_time for e in scenario.events if e.event_type == EventType.FX_CREDITED}
    differences = [(credits[leg.fx_leg_id]-leg.expected_credit_at).total_seconds()
                   for leg in scenario.fx_legs if leg.fx_leg_id in credits]
    assert any(d < 0 for d in differences)
    assert any(d == 0 for d in differences)
    assert any(d > 0 for d in differences)
    assert len(credits) < len(scenario.fx_legs)
    assert all(credits[leg.fx_leg_id] >= leg.booked_at for leg in scenario.fx_legs if leg.fx_leg_id in credits)
    assert generate_day(config, config.start_date, 200, 'test').scenario == scenario


def test_receipt_probabilities_reject_invalid_total():
    import pytest
    from t1fx.generation import GenerationConfig
    config = load_config(Path('configs/generation/smoke.json'))
    with pytest.raises(ValueError, match='probabilities'):
        GenerationConfig.model_validate(config.model_dump() | {'fx_early_rate': 1, 'fx_on_time_rate': 1})


def test_generation_rejects_close_before_execution_open():
    import pytest
    config = load_config(Path('configs/generation/smoke.json'))
    config = config.model_copy(update={'market': config.market.model_copy(update={
        'trading_early_closes': {config.start_date: time(9)},
    })})
    with pytest.raises(ValueError, match='Trading close'):
        generate_day(config, config.start_date, 10, 'test')


def test_fx_trade_references_reject_unknown_or_different_pool():
    import pytest
    from t1fx.domain import Scenario
    config = load_config(Path('configs/generation/smoke.json'))
    scenario = generate_day(config, config.start_date, 20, 'test').scenario
    data = scenario.model_dump(mode='json')
    data['fx_legs'][0]['trade_id'] = 'missing-trade'
    with pytest.raises(ValueError, match='FX trade reference'):
        Scenario.model_validate(data)
    other = next(trade for trade in scenario.trades if trade.bucket != scenario.fx_legs[0].bucket)
    data['fx_legs'][0]['trade_id'] = other.trade_id
    with pytest.raises(ValueError, match='FX trade reference'):
        Scenario.model_validate(data)
