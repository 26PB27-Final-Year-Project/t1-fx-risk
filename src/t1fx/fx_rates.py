"""Dated settlement-currency valuation rates from Frankfurter's public reference-rate API."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from t1fx.domain import Currency

API_ROOT = "https://api.frankfurter.dev/v2/rate"


def fetch_rates(currencies: set[Currency], as_of: date, base_currency: Currency = "USD") -> dict:
    """Return a serializable rate snapshot; one unit of currency is worth rate in the base currency."""
    if as_of > date.today():
        raise ValueError("FX valuation date cannot be in the future")
    def fetch_one(currency: Currency) -> dict:
        url = f"{API_ROOT}/{currency.lower()}/{base_currency.lower()}?{urlencode({'date': as_of.isoformat()})}"
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=10) as response:
                payload = json.load(response)
            rate = Decimal(str(payload["rate"]))
            observed = date.fromisoformat(payload["date"])
            if (payload["base"].upper() != currency or payload["quote"].upper() != base_currency
                    or not rate.is_finite() or rate <= 0 or observed > as_of):
                raise ValueError("Inconsistent Frankfurter rate response")
        except (URLError, TimeoutError) as exc:
            raise ValueError(
                f"Frankfurter is unavailable for {currency}/{base_currency}: {exc}",
            ) from exc
        except (KeyError, TypeError, InvalidOperation, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid Frankfurter response for {currency}/{base_currency}") from exc
        return {"currency": currency, "rate_base": str(rate),
                "observed_date": observed.isoformat(), "url": url}

    targets = sorted(currencies - {base_currency})
    with ThreadPoolExecutor(max_workers=min(5, max(1, len(targets)))) as executor:
        observations = list(executor.map(fetch_one, targets))
    return {"source": "Frankfurter v2 blended reference rates", "requested_date": as_of.isoformat(),
            "base_currency": base_currency, "observations": observations}


def valuation_rates(snapshot: dict, currencies: set[Currency]) -> dict[Currency, Decimal]:
    base = snapshot.get("base_currency")
    rates: dict[Currency, Decimal] = {base: Decimal(1)}
    if (base not in ("USD", "EUR", "CAD", "GBP", "JPY", "INR")
            or snapshot.get("source") != "Frankfurter v2 blended reference rates"):
        raise ValueError("Invalid FX rate snapshot")
    requested = date.fromisoformat(snapshot["requested_date"])
    for row in snapshot.get("observations", []):
        currency = row["currency"]
        rate = Decimal(row.get("rate_base", row.get("rate_usd")))
        if (currency in rates or not rate.is_finite() or rate <= 0
                or date.fromisoformat(row["observed_date"]) > requested):
            raise ValueError("Duplicate or invalid FX valuation rate")
        rates[currency] = rate
    if currencies - rates.keys():
        raise ValueError(f"Missing FX valuation rates: {sorted(currencies - rates.keys())}")
    return rates
