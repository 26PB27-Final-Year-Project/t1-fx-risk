"""Run-scoped local API, durable funding submissions, and dashboard hosting."""

import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from t1fx.domain import CURRENCY_EXPONENT, Currency, Event, Outcome, RulePrediction, whole
from t1fx.storage import ArtifactError, RunStore
from t1fx.jobs import JobConflict, JobStore, RecomputeRequest
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
    fx_receipts: list[dict]
    observed_events: list[Event]
    outcome_as_of: datetime
    outcome: Outcome


def create_app(artifact_root: Path | None = None) -> FastAPI:
    store = RunStore(artifact_root or Path(os.environ.get("T1FX_ARTIFACT_ROOT", "artifacts")))
    models = ModelStore(store.root)
    jobs = JobStore(store.root)
    app = FastAPI(title="T+1 FX Risk Engine", version="0.1.0",
                  description="Synthetic research engine. Rule scores are not probabilities.")

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

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

    @app.exception_handler(JobConflict)
    async def job_conflict(request, exc):
        return error_response(request, 409, "IDEMPOTENCY_CONFLICT", str(exc))

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
        currency: Currency | None = None, sort: Literal["risk", "amount", "probability"] = "risk",
        min_score: int = Query(0, ge=0, le=100), state: str | None = None,
        model_id: str | None = None,
    ):
        manifest = store.manifest(run_id)
        rows = [row for row in trade_rows(run_id) if currency is None or row.currency == currency]
        rows = [row for row in rows if row.prediction.score >= min_score
                and (state is None or row.instruction_state == state)]
        if sort == "amount" and len({row.currency for row in rows}) > 1:
            raise HTTPException(422, "Select one currency before sorting nominal amounts.")
        probabilities = {}
        if sort == "probability":
            if model_id is None:
                raise HTTPException(422, "Probability sorting requires a calibrated model.")
            probabilities = {p["trade_id"]: p["p_fail_any"]
                             for p in model_predictions(run_id, model_id)["items"]}
        rows.sort(key=lambda row: (
            -probabilities[row.trade_id] if sort == "probability" else
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
        reference_events = store.read(run_id, "reference_events.json")
        credited = {event["fx_leg_id"] for event in reference_events
                    if event["event_type"] == "FX_CREDITED"
                    and datetime.fromisoformat(event["event_time"]) <= as_of
                    and datetime.fromisoformat(event["available_at"]) <= as_of}
        fx_receipts = [{"fx_leg_id": leg["fx_leg_id"],
                        "amount": str(whole(leg["buy_amount_minor"], row.currency)),
                        "currency": row.currency, "expected_credit_at": leg["expected_credit_at"],
                        "state": "CREDITED" if leg["fx_leg_id"] in credited else "PENDING"}
                       for leg in store.read(run_id, "scenario.json")["fx_legs"]
                       if leg["bucket"]["cash_pool_id"] == row.cash_pool_id
                       and leg["bucket"]["currency"] == row.currency
                       and datetime.fromisoformat(leg["booked_at"]) <= as_of
                       and datetime.fromisoformat(leg["available_at"]) <= as_of]
        return TradeDetail(
            run_id=run_id, as_of=as_of, reference_policy_id=manifest["reference_policy_id"],
            trade=row, fx_receipts=fx_receipts, observed_events=observed, outcome_as_of=manifest["result_as_of"], outcome=outcome,
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
        balances = {(s["bucket"]["cash_pool_id"], s["bucket"]["currency"]): s["pool_usable_minor"]
                    for s in store.read(run_id, "snapshots.json")}
        for allocation in plan["allocations"]:
            bucket = allocation["bucket"]
            allocation["observed_pool_cash"] = str(whole(
                balances[(bucket["cash_pool_id"], bucket["currency"])], bucket["currency"],
            ))
        metrics = store.read(run_id, "metrics.json")
        return {"run_id": run_id, "as_of": store.manifest(run_id)["decision_at"], "plan": plan,
                "remaining_budget": str(Decimal(metrics["budget_limit"])-Decimal(plan["budget_used_base"]))}

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

    def require_local_origin(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Funding mutations require the same origin.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site funding mutations are not allowed.")

    @app.post("/api/v1/runs/{run_id}/funding/recompute", status_code=202)
    def recompute(run_id: str, body: RecomputeRequest, request: Request,
                  idempotency_key: str = Header(min_length=1, max_length=128)):
        require_local_origin(request)
        store.validate(run_id)
        return jobs.submit(run_id, body, idempotency_key)

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            return jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, "Job not found.") from None

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request):
        require_local_origin(request)
        try:
            return jobs.cancel(job_id)
        except KeyError:
            raise HTTPException(404, "Job not found.") from None

    @app.get("/api/v1/runs/{run_id}/scenarios")
    def scenarios(run_id: str):
        selected = store.manifest(run_id)
        comparison_id = selected.get("comparison_id")
        matched = [run for run in store.list_runs()
                   if (run.get("comparison_id") == comparison_id if comparison_id
                       else run["run_id"] == run_id)]
        return {"run_id": run_id, "comparison_id": comparison_id,
                "items": [{"run_id": run["run_id"], "policy_id": run["policy_id"],
                           "metrics": store.read(run["run_id"], "metrics.json")}
                          for run in matched]}

    @app.get("/api/v1/runs/{run_id}/funding-plans/{plan_id}")
    def funding_plan(run_id: str, plan_id: str):
        result = get_funding(run_id)
        if result["plan"]["plan_id"] != plan_id:
            raise HTTPException(404, "Funding plan not found in this run.")
        return result

    dashboard = Path(os.environ.get("T1FX_DASHBOARD_DIR", Path(__file__).parent / "dashboard"))
    if (dashboard / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dashboard / "assets"), name="dashboard-assets")

        @app.get("/", include_in_schema=False)
        def dashboard_index():
            return FileResponse(dashboard / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def dashboard_missing():
            return JSONResponse(status_code=503, content={
                "message": "Build the dashboard with: cd frontend && npm ci && npm run build",
            })

    return app
