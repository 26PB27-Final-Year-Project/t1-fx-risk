import io
import json
from datetime import date
from decimal import Decimal

import pytest

from t1fx.fx_rates import fetch_rates, valuation_rates


@pytest.mark.parametrize("base,foreign", [("USD", "EUR"), ("EUR", "USD")])
def test_frankfurter_rate_snapshot_and_usd_valuation(monkeypatch, base, foreign):
    requested = date(2026, 10, 13)

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def fake_open(request, timeout):
        assert request.full_url == (
            f"https://api.frankfurter.dev/v2/rate/{foreign.lower()}/{base.lower()}?date=2026-10-13"
        )
        assert timeout == 10
        return Response(json.dumps({"date": "2026-10-13", "base": foreign, "quote": base,
                                    "rate": 1.1196}).encode())

    monkeypatch.setattr("t1fx.fx_rates.urlopen", fake_open)
    snapshot = fetch_rates({"USD", "EUR"}, requested, base_currency=base)
    assert valuation_rates(snapshot, {"USD", "EUR"}) == {
        base: Decimal(1), foreign: Decimal("1.1196"),
    }
    with pytest.raises(ValueError, match="Missing FX valuation rates"):
        valuation_rates(snapshot, {"USD", "GBP"})


def test_frankfurter_rejects_mismatched_response(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    monkeypatch.setattr("t1fx.fx_rates.urlopen", lambda *_args, **_kwargs: Response(
        b'{"date":"2026-10-14","base":"EUR","quote":"USD","rate":1.1}',
    ))
    with pytest.raises(ValueError, match="Inconsistent"):
        fetch_rates({"EUR"}, date(2026, 10, 13))
