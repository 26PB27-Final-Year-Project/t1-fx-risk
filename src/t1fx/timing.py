"""Training-only empirical FX receipt forecasts and coherent arrival scenarios.

Unresolved training receipts retain mass at no arrival. This is a conservative
empirical fallback, not a calibrated classifier or an estimate of causal risk.
"""

import json
import os
import random
import re
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import AwareDatetime, Field, model_validator

from t1fx.domain import Contract, FXLeg
from t1fx.storage import ArtifactError, canonical_bytes, digest

METHOD = "empirical-route-survival-v1"


def timestamp(value):
    return datetime.fromisoformat(value)


class TimingModel(Contract):
    method: str = METHOD
    dataset_id: str
    trained_through: AwareDatetime
    # Route is the observable sell/buy pair; no hidden generator attributes.
    delays_seconds: dict[str, tuple[float | None, ...]]
    training_count: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_support(self):
        if (not self.delays_seconds or any(not values for values in self.delays_seconds.values())
                or sum(map(len, self.delays_seconds.values())) != self.training_count):
            raise ValueError("Timing training count must match nonempty route distributions")
        return self

    def support(self, leg: FXLeg, as_of: datetime):
        if self.method != METHOD:
            raise ValueError("Unsupported timing model method")
        if as_of.tzinfo is None or as_of <= self.trained_through:
            raise ValueError("Timing inference must follow training-label maturity")
        if leg.booked_at > as_of or leg.available_at > as_of:
            raise ValueError("Future FX contracts are not observable")
        route = f"{leg.sell_currency}/{leg.bucket.currency}"
        if route not in self.delays_seconds:
            raise ValueError(f"Timing model has no training observations for route {route}")
        arrivals = [None if delay is None else leg.expected_credit_at + timedelta(seconds=delay)
                    for delay in self.delays_seconds[route]]
        # An unposted receipt is conditioned on survival through decision time.
        arrivals = [arrival for arrival in arrivals if arrival is None or arrival > as_of]
        # Outside empirical support, never invent a certain near-term receipt.
        return sorted(arrival for arrival in arrivals if arrival is not None) + [
            None for arrival in arrivals if arrival is None
        ] or [None]


def publish_model(root: Path, model: TimingModel, metrics: dict) -> str:
    payload = {"model": model.model_dump(mode="json"), "metrics": metrics}
    model_id = f"timing-{digest(payload)[:20]}"
    directory = root / "timing_models"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{model_id}.json"
    fd, temporary = tempfile.mkstemp(prefix=".staging-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(payload))
        try:
            os.link(temporary, target)
        except FileExistsError:
            load_model(root, model_id)
    finally:
        os.unlink(temporary)
    return model_id


def load_model(root: Path, model_id: str):
    if re.fullmatch(r"timing-[0-9a-f]{20}", model_id) is None:
        raise ArtifactError("Invalid timing model ID")
    try:
        payload = json.loads((root / "timing_models" / f"{model_id}.json").read_text())
        if f"timing-{digest(payload)[:20]}" != model_id:
            raise ValueError("Timing model checksum mismatch")
        model = TimingModel.model_validate(payload["model"])
        if model.method != METHOD:
            raise ValueError("Unsupported timing model method")
        return model, payload["metrics"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ArtifactError(f"Timing model unavailable: {exc}") from exc


def train_timing(root: Path, dataset_id: str):
    from t1fx.datasets import validate_dataset

    manifest = validate_dataset(root, dataset_id)
    directory = root / "datasets" / dataset_id

    def rows(name):
        return json.loads((directory / manifest["tables"][name]["json"]).read_text())

    legs, events, outcomes = rows("fx_legs"), rows("events"), rows("outcomes")
    credits = {row["fx_leg_id"]: row for row in events if row["event_type"] == "FX_CREDITED"}
    training = [leg for leg in legs if leg["split"] == "train"]
    maturity = max(timestamp(row["outcome_known_at"]) for row in outcomes if row["split"] == "train")
    delays = {}
    for leg in training:
        credit = credits.get(leg["fx_leg_id"])
        if credit and timestamp(credit["available_at"]) > maturity:
            raise ValueError("Training FX label is not mature")
        delay = ((timestamp(credit["available_at"]) - timestamp(leg["expected_credit_at"])).total_seconds()
                 if credit else None)
        delays.setdefault(f"{leg['sell_currency']}/{leg['currency']}", []).append(delay)
    model = TimingModel(dataset_id=dataset_id, trained_through=maturity,
                        delays_seconds=delays, training_count=len(training))
    # Freeze before evaluating on the untouched chronological test partition.
    test_legs = {leg["fx_leg_id"]: leg for leg in legs if leg["split"] == "test"}
    bucket_legs = {}
    for leg_id, row in test_legs.items():
        bucket_legs.setdefault((row["scenario_id"], row["cash_pool_id"], row["currency"]), []).append(leg_id)
    predictions = []
    seen = set()
    for snapshot in rows("snapshots"):
        if snapshot["split"] != "test":
            continue
        bucket_key = (snapshot["scenario_id"], snapshot["cash_pool_id"], snapshot["currency"])
        for leg_id in bucket_legs.get(bucket_key, ()):
            key = (leg_id, snapshot["as_of"], snapshot["horizon_at"])
            if key in seen or leg_id not in test_legs:
                continue
            seen.add(key)
            row = test_legs[leg_id]
            leg = leg_from_row(row)
            as_of, target = timestamp(snapshot["as_of"]), timestamp(snapshot["horizon_at"])
            credit = credits.get(leg_id)
            if (max(leg.booked_at, leg.available_at) > as_of or target <= as_of
                    or (credit and timestamp(credit["available_at"]) <= as_of)):
                continue
            support = model.support(leg, as_of)
            probability = sum(t is None or t > target for t in support) / len(support)
            label = int(credit is None or timestamp(credit["available_at"]) > target)
            predictions.append({"fx_leg_id": leg_id, "as_of": as_of.isoformat(),
                                "target_at": target.isoformat(), "probability": probability,
                                "label": label})
    reliability = []
    for index in range(10):
        group = [row for row in predictions if min(int(row["probability"]*10), 9) == index]
        reliability.append({"lower": index/10, "upper": (index+1)/10, "count": len(group),
                            "mean_probability": (sum(r["probability"] for r in group)/len(group)
                                                 if group else None),
                            "late_frequency": (sum(r["label"] for r in group)/len(group)
                                               if group else None)})
    metrics = {"split": "test", "reliability_bins": reliability, "target": "receipt_observable_after_target",
               "count": len(predictions), "brier_score": (
                   sum((row["probability"]-row["label"])**2 for row in predictions)/len(predictions)
                   if predictions else None),
               "unresolved_training_receipts": sum(delay is None for ds in delays.values() for delay in ds),
               "classification": "synthetic_empirical_fallback", "predictions": predictions}
    model_id = publish_model(root, model, metrics)
    return {"timing_model_id": model_id, "dataset_id": dataset_id,
            "training_count": model.training_count, "metrics": metrics}


def leg_from_row(row):
    fields = {key: row[key] for key in (
        "fx_leg_id", "buy_amount_minor", "sell_currency", "sell_amount_minor",
        "funding_source_id", "booked_at", "available_at", "expected_credit_at",
    )}
    return FXLeg(**fields, bucket={"cash_pool_id": row["cash_pool_id"], "currency": row["currency"]})


def forecast_arrivals(model, legs, credited_ids, as_of, targets, seed=42, count=200,
                      common_shock_weight=0.5):
    """Mixture of shared and independent quantiles preserves each route marginal.

The common-shock weight is a declared stress assumption, not fitted correlation.
Each leg gets exactly one arrival per scenario, shared across all deadlines.
"""
    if not 1 <= count <= 10000 or not 0 <= common_shock_weight <= 1:
        raise ValueError("Use 1–10000 scenarios and a common-shock weight between zero and one")
    if as_of.tzinfo is None or as_of <= model.trained_through:
        raise ValueError("Timing inference must follow training-label maturity")
    if len({leg.fx_leg_id for leg in legs}) != len(legs):
        raise ValueError("Duplicate FX forecast would double-count a receipt")
    pending = sorted((leg for leg in legs if leg.fx_leg_id not in credited_ids),
                     key=lambda leg: leg.fx_leg_id)
    supports = {leg.fx_leg_id: model.support(leg, as_of) for leg in pending}
    rng = random.Random(seed)
    draws = []
    for _ in range(count):
        shared, common = rng.random(), rng.random() < common_shock_weight
        draws.append({leg_id: support[min(int((shared if common else rng.random())*len(support)),
                                           len(support)-1)] for leg_id, support in supports.items()})
    predictions = []
    for leg in pending:
        support = supports[leg.fx_leg_id]
        for target in sorted(set(targets.get(leg.bucket, ()))):
            p = sum(t is None or t > target for t in support)/len(support)
            observed = sum(draw[leg.fx_leg_id] is None or draw[leg.fx_leg_id] > target
                           for draw in draws)/count
            predictions.append({"fx_leg_id": leg.fx_leg_id, "as_of": as_of.isoformat(),
                                "credit_target_at": target.isoformat(), "p_credit_after_target": p,
                                "sampled_late_frequency": observed,
                                "absolute_sampling_error": abs(observed-p),
                                "support_count": len(support)})
    # Decimal residual makes weights sum to exactly one for arbitrary counts.
    unit = (Decimal(1)/count).quantize(Decimal("0.000000000000000001"))
    weights = (unit,)*(count-1) + (Decimal(1)-unit*(count-1),)
    return draws, weights, {"method": METHOD, "seed": seed, "scenario_count": count,
                            "common_shock_weight": common_shock_weight,
                            "correlation_classification": "assumed_quantile_mixture",
                            "predictions": predictions,
                            "arrivals": [{key: value.isoformat() if value else None
                                          for key, value in draw.items()} for draw in draws]}
