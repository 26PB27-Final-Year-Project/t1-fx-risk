"""Read-only, run-scoped API for the first engine milestone."""

import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from t1fx.domain import CURRENCY_EXPONENT, Currency, Event, Outcome, RulePrediction, whole
from t1fx.storage import ArtifactError, RunStore
from t1fx.models import ModelStore, ModelUnavailable, RiskModel


class TradeView(BaseModel):
    trade_id: str
    counterparty_id: str
    cash_pool_id: str
    currency: Currency
    settlement_amount: str
    remaining_obligation: str
    observed_pool_cash: str
    instruction_state: str
    ssi_status: str
    deadline_at: datetime
    minutes_to_deadline: float
    prediction: RulePrediction


class TradePage(BaseModel):
    run_id: str
    as_of: datetime
    reference_policy_id: str
    items: list[TradeView]
    next_cursor: str | None


class TradeDetail(BaseModel):
    run_id: str
    as_of: datetime
    reference_policy_id: str
    trade: TradeView
    observed_events: list[Event]
    outcome_as_of: datetime
    outcome: Outcome


def create_app(artifact_root: Path | None = None) -> FastAPI:
    store = RunStore(artifact_root or Path(os.environ.get("T1FX_ARTIFACT_ROOT", "artifacts")))
    models = ModelStore(store.root)
    app = FastAPI(title="T+1 FX Risk Engine", version="0.1.0",
                  description="Synthetic research engine. Rule scores are not probabilities.")

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(request, status, code, message, details=None):
        return JSONResponse(status_code=status, content={
            "code": code, "message": message, "details": details or {},
            "request_id": request.state.request_id,
        })

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return error_response(request, 404, "RUN_NOT_FOUND", "The requested run does not exist.")

    @app.exception_handler(ArtifactError)
    async def invalid_artifact(request, exc):
        return error_response(request, 409, "ARTIFACT_INVALID", str(exc))

    @app.exception_handler(ModelUnavailable)
    async def unavailable_model(request, exc):
        return error_response(request, 409, "MODEL_UNAVAILABLE", str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, "REQUEST_ERROR", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(request, 422, "VALIDATION_ERROR", "Invalid request parameters.")

    @app.get("/health/live")
    def live():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/health/ready")
    def ready():
        runs = store.list_runs()
        if not runs:
            raise HTTPException(503, "No published runs; execute `make demo` first.")
        for run in runs:
            store.validate(run["run_id"])
        return {"status": "ready", "run_count": len(runs), "model": "UNCALIBRATED_RULE_SCORE"}

    @app.get("/api/v1/runs")
    def list_runs():
        return {"items": store.list_runs()}

    @app.get("/api/v1/models")
    def list_models():
        return {"items": models.list_models()}

    @app.get("/api/v1/models/{model_id}/health")
    def model_health(model_id: str):
        model = RiskModel(store.root, model_id)
        artifacts = model.artifacts
        return {"model_id": model_id, "status": "AVAILABLE", "reference_policy_id": "fixed-0",
                "training": artifacts["training_manifest.json"], "metrics": artifacts["metrics.json"],
                "thresholds": artifacts["thresholds.json"], "model_card": artifacts["model_card.md"]}

    @app.get("/api/v1/runs/{run_id}/model-predictions")
    def model_predictions(run_id: str, model_id: str, trade_id: str | None = None):
        manifest = store.validate(run_id)
        rows = store.read(run_id, "snapshots.json")
        rows = [row | {"reference_policy_id": manifest["reference_policy_id"]} for row in rows
                if trade_id is None or row["trade_id"] == trade_id]
        if trade_id is not None and not rows:
            raise HTTPException(404, "Trade not found at this decision time.")
        trades = {trade["trade_id"]: trade for trade in store.read(run_id, "scenario.json")["trades"]}
        return {"run_id": run_id, "model_id": model_id, "as_of": manifest["decision_at"],
                "reference_policy_id": manifest["reference_policy_id"],
                "items": RiskModel(store.root, model_id).predict(rows, trades)}

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        return store.manifest(run_id)

    def trade_rows(run_id):
        trades = {t["trade_id"]: t for t in store.read(run_id, "scenario.json")["trades"]}
        scores = {p["trade_id"]: p for p in store.read(run_id, "predictions.json")}
        result = []
        for snapshot in store.read(run_id, "snapshots.json"):
            trade = trades[snapshot["trade_id"]]
            currency = snapshot["bucket"]["currency"]
            result.append(TradeView(
                trade_id=trade["trade_id"], counterparty_id=trade["counterparty_id"],
                cash_pool_id=snapshot["bucket"]["cash_pool_id"], currency=currency,
                settlement_amount=str(whole(trade["settlement_amount_minor"], currency)),
                remaining_obligation=str(whole(snapshot["remaining_obligation_minor"], currency)),
                observed_pool_cash=str(whole(snapshot["pool_usable_minor"], currency)),
                instruction_state=snapshot["instruction_state"], ssi_status=snapshot["ssi_status"],
                deadline_at=snapshot["horizon_at"],
                minutes_to_deadline=snapshot["minutes_to_settlement_deadline"],
                prediction=scores[trade["trade_id"]],
            ))
        return result

    @app.get("/api/v1/runs/{run_id}/trades", response_model=TradePage)
    def list_trades(
        run_id: str, limit: int = Query(50, ge=1, le=100), cursor: str | None = None,
        currency: Currency | None = None, sort: Literal["risk", "amount"] = "risk",
    ):
        manifest = store.manifest(run_id)
        rows = [row for row in trade_rows(run_id) if currency is None or row.currency == currency]
        if sort == "amount" and len({row.currency for row in rows}) > 1:
            raise HTTPException(422, "Select one currency before sorting nominal amounts.")
        rows.sort(key=lambda row: (
            -row.prediction.score if sort == "risk" else -Decimal(row.settlement_amount), row.trade_id,
        ))
        if cursor:
            ids = [row.trade_id for row in rows]
            if cursor not in ids:
                raise HTTPException(400, "Cursor does not belong to this filtered result.")
            rows = rows[ids.index(cursor)+1:]
        return TradePage(
            run_id=run_id, as_of=manifest["decision_at"],
            reference_policy_id=manifest["reference_policy_id"], items=rows[:limit],
            next_cursor=rows[limit-1].trade_id if len(rows) > limit else None,
        )

    @app.get("/api/v1/runs/{run_id}/trades/{trade_id}", response_model=TradeDetail)
    def get_trade(run_id: str, trade_id: str):
        manifest = store.manifest(run_id)
        row = next((row for row in trade_rows(run_id) if row.trade_id == trade_id), None)
        if row is None:
            raise HTTPException(404, "Trade not found at this decision time.")
        as_of = datetime.fromisoformat(manifest["decision_at"])
        observed = [Event.model_validate(event) for event in store.read(run_id, "reference_events.json")
                    if event["trade_id"] == trade_id]
        observed = [event for event in observed
                    if event.event_time <= as_of and event.available_at <= as_of]
        outcome = next(outcome for outcome in store.read(run_id, "outcomes.json")
                       if outcome["trade_id"] == trade_id)
        return TradeDetail(
            run_id=run_id, as_of=as_of, reference_policy_id=manifest["reference_policy_id"],
            trade=row, observed_events=observed, outcome_as_of=manifest["result_as_of"], outcome=outcome,
        )

    @app.get("/api/v1/runs/{run_id}/funding")
    def get_funding(run_id: str):
        plan = store.read(run_id, "funding_plan.json")
        for allocation in plan["allocations"]:
            currency = allocation["bucket"]["currency"]
            allocation["incremental_reserve"] = str(whole(
                allocation.pop("incremental_reserve_minor"), currency,
            ))
            allocation["expected_shortfall"] = str(Decimal(
                allocation.pop("expected_shortfall_minor"),
            ).scaleb(-CURRENCY_EXPONENT[currency]))
            allocation["scenario_shortfalls"] = [str(whole(gap, currency))
                                                  for gap in allocation.pop("scenario_shortfalls_minor")]
        return {"run_id": run_id, "as_of": store.manifest(run_id)["decision_at"], "plan": plan}

    @app.get("/api/v1/timing-models/{model_id}/health")
    def timing_health(model_id: str):
        from t1fx.timing import load_model

        model, metrics = load_model(store.root, model_id)
        return {"timing_model_id": model_id, "method": model.method,
                "dataset_id": model.dataset_id, "trained_through": model.trained_through,
                "training_count": model.training_count, "metrics": metrics}

    @app.get("/api/v1/runs/{run_id}/timing-forecasts")
    def timing_forecasts(run_id: str):
        manifest = store.manifest(run_id)
        if "timing_forecasts.json" not in manifest["files"]:
            raise HTTPException(404, "This run has no FX timing forecasts.")
        return {"run_id": run_id, "forecasts": store.read(run_id, "timing_forecasts.json")}

    @app.get("/api/v1/runs/{run_id}/metrics")
    def get_metrics(run_id: str):
        return {"run_id": run_id, "metrics": store.read(run_id, "metrics.json")}

    return app
