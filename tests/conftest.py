"""Keep comparison tests independent of the public exchange-rate service."""

from decimal import Decimal

import pytest


@pytest.fixture(autouse=True)
def offline_comparison_rates(monkeypatch):
    def rates(currencies, as_of, base_currency="USD"):
        observations = [
            {"currency": currency, "rate_usd": str(Decimal("1.10")),
             "observed_date": as_of.isoformat(), "url": "test://frankfurter"}
            for currency in sorted(currencies - {base_currency})
        ]
        return {"source": "Frankfurter v2 blended reference rates",
                "requested_date": as_of.isoformat(), "base_currency": base_currency,
                "observations": observations}

    monkeypatch.setattr("t1fx.pipeline.fetch_rates", rates)
    monkeypatch.setattr("t1fx.fx_rates.fetch_rates", rates)
    from datetime import UTC, datetime
    monkeypatch.setattr("t1fx.fixtures.current_utc", lambda: datetime.now(UTC))
    monkeypatch.setattr("t1fx.fx_rates.online_time", lambda: {
        "utc_now": datetime.now(UTC).isoformat(), "source": "test://clock",
    })
    monkeypatch.setattr("t1fx.workflow.online_time", lambda: {
        'utc_now': datetime.now(UTC).isoformat(), 'source': 'test://online-clock',
    })
    monkeypatch.setattr("t1fx.api.online_time", lambda: {
        'utc_now': datetime.now(UTC).isoformat(), 'source': 'test://online-clock',
        'synchronized_at': datetime.now(UTC).isoformat(), 'stale': False,
    })

@pytest.fixture(scope="session", autouse=True)
def generation_reference_prices():
    from t1fx import generation
    original = generation.reference_snapshot
    def snapshot(currencies, day, base):
        return {"source": "Frankfurter v2 blended reference rates", "requested_date": day.isoformat(),
                "base_currency": base, "observations": [{"currency": c, "rate_base": "0.01" if c == "JPY" else "1.10",
                "observed_date": day.isoformat(), "url": "test://frankfurter"} for c in currencies if c != base]}
    generation.reference_snapshot = snapshot
    yield
    generation.reference_snapshot = original
