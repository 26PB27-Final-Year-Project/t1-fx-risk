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
    from datetime import UTC, datetime
    monkeypatch.setattr("t1fx.api.online_time", lambda: {
        'utc_now': datetime.now(UTC).isoformat(), 'source': 'test://online-clock',
        'synchronized_at': datetime.now(UTC).isoformat(), 'stale': False,
    })
