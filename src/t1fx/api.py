"""Run-scoped local API, durable funding submissions, and dashboard hosting."""

import os
import json
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

from t1fx.domain import Scenario, CURRENCY_EXPONENT, Currency, Event, Outcome, RulePrediction, whole
from t1fx.storage import ArtifactError, RunStore
from t1fx.jobs import JobConflict, JobStore, RecomputeRequest
from t1fx.models import ModelStore, ModelUnavailable, RiskModel
from t1fx.online_clock import online_time, OnlineClockUnavailable


from t1fx.presentation import trade_labels


class TradeView(BaseModel):
    trade_label: str
    security_label: str
    counterparty_label: str
    fx_conversions: list[dict] = []
    observed_settled: bool = False
    trade_id: str
    counterparty_id: str
    cash_pool_id: str
    currency: Currency
    settlement_amount: str
    remaining_obligation: str
    observed_pool_cash: str
    instruction_state: str
    ssi_status: str
    executed_at: datetime
    deadline_at: datetime
    minutes_to_deadline: float
    prediction: RulePrediction


class TradePage(BaseModel):
    run_id: str
    as_of: datetime
    reference_policy_id: str
    available_currencies: list[str] = []
    items: list[TradeView]
    model_predictions: list[dict] = []
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
        fields = [{"field": ".".join(str(part) for part in error['loc'] if part != 'body'),
                   "message": error['msg']} for error in exc.errors()]
        message = '; '.join(f"{item['field'] or 'request'}: {item['message']}" for item in fields)
        return error_response(request, 422, "VALIDATION_ERROR", message, {"fields": fields})

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

    @app.exception_handler(OnlineClockUnavailable)
    async def clock_unavailable(request, exc):
        return error_response(request, 503, "ONLINE_TIME_UNAVAILABLE", str(exc))

    @app.get("/api/v1/time")
    def get_online_time():
        return online_time()

    @app.get("/api/v1/runs")
    def list_runs():
        return {"items": store.list_runs()}

    @app.get("/api/v1/models")
    def list_models(run_id: str | None = None):
        items = models.list_models()
        if run_id:
            manifest = store.manifest(run_id)
            currencies = {trade['bucket']['currency'] for trade in store.read(run_id, 'scenario.json')['trades']}
            decision = datetime.fromisoformat(manifest['decision_at'])
            items = [item for item in items if currencies <= set(item['settlement_currencies'])
                     and decision > datetime.fromisoformat(item['calibrated_through'])]
        return {"items": items}

    @app.get("/api/v1/timing-models")
    def list_timing_models(run_id: str | None = None):
        from t1fx.timing import load_model

        items = []
        for path in sorted((store.root / "timing_models").glob("timing-*.json")):
            model, _ = load_model(store.root, path.stem)
            reasons=[]
            if run_id:
                from t1fx.workflow import ensure_timing_compatible
                try:
                    ensure_timing_compatible(store.root,path.stem,Scenario.model_validate(store.read(run_id,'scenario.json')))
                except ValueError as exc:
                    reasons.append(str(exc))
            items.append({"model_id": path.stem, "dataset_id": model.dataset_id,
                          "eligible":not reasons,"reasons":reasons,
                          "trained_through": model.trained_through.isoformat()})
        return {"items": items}

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
                if (trade_id is None or row["trade_id"] == trade_id)
                and not row["observed_settled"]
                and datetime.fromisoformat(row["as_of"]) < datetime.fromisoformat(row["horizon_at"])]
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
        scenario = store.read(run_id, "scenario.json")
        as_of = datetime.fromisoformat(store.manifest(run_id)["decision_at"])
        trades = {t["trade_id"]: t for t in scenario["trades"]}
        scores = {p["trade_id"]: p for p in store.read(run_id, "predictions.json")}
        # Index observable legs once instead of scanning all legs for every trade.
        from collections import defaultdict
        trade_legs, pool_legs = defaultdict(list), defaultdict(list)
        for leg in scenario['fx_legs']:
            if datetime.fromisoformat(leg['available_at']) > as_of or datetime.fromisoformat(leg['booked_at']) > as_of:
                continue
            if leg.get('trade_id'):
                trade_legs[leg['trade_id']].append(leg)
            else:
                pool_legs[(leg['bucket']['cash_pool_id'], leg['bucket']['currency'])].append(leg)
        result = []
        for snapshot in store.read(run_id, "snapshots.json"):
            trade = trades[snapshot["trade_id"]]
            currency = snapshot["bucket"]["currency"]
            result.append(TradeView(
                **trade_labels(trade),
                fx_conversions=[{"sell_currency": leg["sell_currency"],
                                 "sell_amount": str(whole(leg["sell_amount_minor"], leg["sell_currency"])),
                                 "buy_currency": currency, "buy_amount": str(whole(leg["buy_amount_minor"], currency)),
                                 "pricing": leg.get("pricing")}
                                for leg in trade_legs[trade['trade_id']] + pool_legs[(trade['bucket']['cash_pool_id'], currency)]],
                trade_id=trade["trade_id"], counterparty_id=trade["counterparty_id"],
                cash_pool_id=snapshot["bucket"]["cash_pool_id"], currency=currency,
                settlement_amount=str(whole(trade["settlement_amount_minor"], currency)),
                remaining_obligation=str(whole(snapshot["remaining_obligation_minor"], currency)),
                observed_pool_cash=str(whole(snapshot["pool_usable_minor"], currency)),
                instruction_state=snapshot["instruction_state"], ssi_status=snapshot["ssi_status"],
                observed_settled=snapshot["observed_settled"],
                executed_at=trade["executed_at"],
                deadline_at=snapshot["horizon_at"],
                minutes_to_deadline=snapshot["minutes_to_settlement_deadline"],
                prediction=scores[trade["trade_id"]],
            ))
        return result

    @app.get("/api/v1/runs/{run_id}/trades", response_model=TradePage)
    def list_trades(
        run_id: str, limit: int = Query(50, ge=1, le=100), cursor: str | None = None,
        currency: Currency | None = None, sort: Literal["risk", "amount", "probability", "deadline"] = "risk",
        min_score: int = Query(0, ge=0, le=100), state: str | None = None,
        model_id: str | None = None,
    ):
        manifest = store.manifest(run_id)
        all_rows = trade_rows(run_id)
        available_currencies = sorted({c for row in all_rows for c in
            [row.currency, *(leg["sell_currency"] for leg in row.fx_conversions)]})
        rows = [row for row in all_rows if currency is None or row.currency == currency
                or any(leg["sell_currency"] == currency for leg in row.fx_conversions)]
        rows = [row for row in rows if row.prediction.score >= min_score
                and (state is None or row.instruction_state == state)]
        if sort == "amount" and len({row.currency for row in rows}) > 1:
            raise HTTPException(422, "Select one currency before sorting nominal amounts.")
        probabilities = {}
        forecast = []
        if sort == "probability":
            if model_id is None:
                raise HTTPException(422, "Probability sorting requires a calibrated model.")
            forecast = model_predictions(run_id, model_id)["items"]
            probabilities = {p["trade_id"]: p["p_fail_any"] for p in forecast}
        rows.sort(key=lambda row: (
            -probabilities.get(row.trade_id, -1) if sort == "probability" else
            -row.prediction.score if sort == "risk" else
            row.minutes_to_deadline if sort == "deadline" else -Decimal(row.settlement_amount), row.trade_id,
        ))
        if cursor:
            ids = [row.trade_id for row in rows]
            if cursor not in ids:
                raise HTTPException(400, "Cursor does not belong to this filtered result.")
            rows = rows[ids.index(cursor)+1:]
        return TradePage(
            run_id=run_id, as_of=manifest["decision_at"],
            reference_policy_id=manifest["reference_policy_id"], available_currencies=available_currencies, items=rows[:limit],
            model_predictions=[p for p in forecast if p["trade_id"] in {row.trade_id for row in rows[:limit]}],
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

    @app.get("/api/v1/runs/{run_id}/evaluation-context")
    def evaluation_context(run_id: str):
        scenario = store.read(run_id, "scenario.json")
        manifest = store.manifest(run_id)
        return {"replay_settings": {key: manifest[key] for key in ("scenario_seed", "scenario_count", "common_shock_weight", "timing_model_id") if key in manifest},
                "starts_at": scenario["starts_at"], "ends_at": scenario["ends_at"],
                "trades": [{"trade_id": t["trade_id"], "executed_at": t["executed_at"],
                            "deadline_at": t["contractual_deadline_at"],
                            "currency": t["bucket"]["currency"]} for t in scenario["trades"]]}

    @app.get("/api/v1/runs/{run_id}/fx-rates")
    def get_fx_rates(run_id: str):
        manifest = store.manifest(run_id)
        if "fx_rates.json" not in manifest["files"]:
            raise HTTPException(404, "This run has no saved FX reference rates.")
        return {"run_id": run_id, "rates": store.read(run_id, "fx_rates.json")}

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
        if body.timing_model_id:
            from t1fx.timing import load_model
            load_model(store.root, body.timing_model_id)
            from t1fx.workflow import ensure_timing_compatible
            try:
                ensure_timing_compatible(store.root,body.timing_model_id,Scenario.model_validate(store.read(run_id,"scenario.json")))
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from exc
        if body.evaluation_local is not None:
            from t1fx.calendars import local_instant
            from zoneinfo import ZoneInfo

            try:
                decision = local_instant(body.evaluation_local.date(), body.evaluation_local.time(), body.evaluation_timezone)
                now = datetime.fromisoformat(online_time()['utc_now'])
                if decision > now:
                    raise HTTPException(422, "Evaluation must be at or before the current online date and time.")
                scenario = store.read(run_id, "scenario.json")
                start = datetime.fromisoformat(scenario["starts_at"])
                end = datetime.fromisoformat(scenario["ends_at"])
                if not start <= decision < end:
                    zone = ZoneInfo(body.evaluation_timezone)
                    first = start.astimezone(zone).strftime("%d %b %Y, %H:%M")
                    last = end.astimezone(zone).strftime("%d %b %Y, %H:%M")
                    raise HTTPException(422, f"Choose an evaluation time from {first} until before {last} ({body.evaluation_timezone}). To evaluate another month, select a run containing trades for that month.")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
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

    from t1fx.workflow import WorkflowRequest, ModelChoicesRequest, model_choices, compatibility, dataset_catalog_entry, export_record
    from fastapi.responses import Response

    @app.get('/api/v1/workflow/defaults')
    def workflow_defaults(trading_days: int = Query(default=10, ge=4, le=40)):
        from t1fx.workflow import recommended_start
        clock = online_time()
        today = datetime.fromisoformat(clock['utc_now']).date()
        try:
            start = recommended_start(today, trading_days)
        except ValueError as exc:
            raise HTTPException(422, f'Current-date defaults exceed saved calendar coverage: {exc}') from exc
        return {'today': today.isoformat(), 'start_date': start.isoformat(), 'source': clock['source']}

    def generation_today(body):
        # Resolve implicit generation dates against the same clock as the UI.
        if body.dataset_id:
            return None
        from t1fx.workflow import recommended_start
        today = datetime.fromisoformat(online_time()['utc_now']).date()
        if 'start_date' not in body.model_fields_set or body.start_date is None:
            try:
                body.start_date = recommended_start(today, body.trading_days)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return today

    @app.post('/api/v1/workflow/preview')
    def workflow_preview(body:WorkflowRequest,request:Request):
        require_local_origin(request)
        from t1fx.workflow import generation_preview
        try:
            return generation_preview(body, today=generation_today(body))
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get('/api/v1/workflow/health')
    def workflow_health():
        return {'api': 'available', 'worker': jobs.worker_health()}

    @app.get('/api/v1/workflow/catalog')
    def workflow_catalog(include_models: bool = True):
        datasets, errors = [], []
        for path in sorted((store.root/'datasets').glob('dataset-*/manifest.json'), key=lambda p:-p.stat().st_mtime_ns):
            try:
                datasets.append(dataset_catalog_entry(store.root, path.parent.name))
            except (ValueError,OSError,KeyError) as exc:
                errors.append({'artifact':path.parent.name,'message':str(exc)})
        risk=[]
        for path in (sorted((store.root/'models').glob('model-*/manifest.json')) if include_models else []):
            try:
                artifact=models.read(path.parent.name)
                risk.append({'model_id':path.parent.name,'dataset_id':artifact['training_manifest.json']['dataset_id'],
                    'metrics':artifact['metrics.json'],'training':artifact['training_manifest.json']})
            except (ValueError,OSError) as exc:
                errors.append({'artifact':path.parent.name,'message':str(exc)})
        timing=[]
        from t1fx.timing import load_model
        for path in (sorted((store.root/'timing_models').glob('timing-*.json')) if include_models else []):
            try:
                model,metrics=load_model(store.root,path.stem)
                timing.append({'model_id':path.stem,'dataset_id':model.dataset_id,'metrics':metrics})
            except (ValueError,OSError) as exc:
                errors.append({'artifact':path.stem,'message':str(exc)})
        records=RunStore(store.root/'experiments').list_runs()
        return {'datasets':datasets,'risk_models':risk,'timing_models':timing,'errors':errors,
                'experiments':[{'experiment_id':m['run_id']} for m in records]}

    @app.post('/api/v1/workflow/model-choices')
    def workflow_model_choices(body: ModelChoicesRequest, request: Request):
        require_local_origin(request)
        return model_choices(store.root, body)

    @app.post('/api/v1/workflow/eligibility')
    def workflow_eligibility(body:WorkflowRequest,request:Request):
        require_local_origin(request)
        return compatibility(store.root,body, today=generation_today(body))

    @app.post('/api/v1/workflow/jobs',status_code=202)
    def workflow_submit(body:WorkflowRequest,request:Request,
                        idempotency_key:str=Header(min_length=1,max_length=128)):
        require_local_origin(request)
        check=compatibility(store.root,body, today=generation_today(body))
        if not check['eligible']:
            return JSONResponse(status_code=422,content={'code':'INELIGIBLE','message':'; '.join(check['reasons']),'reasons':check['reasons']})
        return jobs.submit(None,body,idempotency_key)

    @app.post('/api/v1/workflow/jobs/{job_id}/retry',status_code=202)
    def workflow_retry(job_id:str,request:Request,idempotency_key:str=Header(min_length=1,max_length=128)):
        require_local_origin(request)
        try:
            prior=jobs.get(job_id)
        except KeyError:
            raise HTTPException(404,'Job not found.') from None
        if prior['status'] not in ('FAILED','CANCELED'):
            raise HTTPException(409,'Only failed or canceled preparations can be retried')
        if 'kind' not in prior['request']:
            raise HTTPException(422,'This is a funding job. Submit it again from Funding.')
        body=WorkflowRequest.model_validate(prior['request'])
        return jobs.submit_payload(body.model_dump(mode='json'),idempotency_key,retry_of=job_id)

    @app.get('/api/v1/workflow/experiments/{record_id}')
    def workflow_record(record_id:str):
        return RunStore(store.root/'experiments').read(record_id,'experiment.json')

    @app.get('/api/v1/workflow/experiments/{record_id}/export')
    def workflow_export(record_id:str,format:Literal['json','csv']='json'):
        raw=export_record(store.root,record_id,format)
        return Response(raw,media_type='application/json' if format=='json' else 'text/csv',
                        headers={'Content-Disposition':f'attachment; filename="experiment-{record_id}.{format}"'})

    @app.post('/api/v1/workflow/diagnostics')
    def workflow_diagnostics(request:Request):
        require_local_origin(request)
        from t1fx.fx_rates import fetch_rates
        from datetime import date,timedelta
        results={}
        for name,action in [('clock',online_time),('fx',lambda:fetch_rates({'EUR','USD'},date.today()-timedelta(days=1),'USD'))]:
            try:
                action(); results[name]={'available':True}
            except (ValueError,RuntimeError,OSError) as exc:
                results[name]={'available':False,'message':str(exc)+' Check HTTPS access, proxy settings and retry.'}
        return results


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
