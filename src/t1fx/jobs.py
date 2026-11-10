"""Durable local funding jobs with transactional claims and renewable leases."""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from t1fx.storage import RunStore, canonical_bytes, digest


class RecomputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    evaluation_local: datetime | None = None
    evaluation_timezone: str = "UTC"

    @field_validator("evaluation_timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Choose a valid IANA timezone") from exc
        return value

    @field_validator("evaluation_local")
    @classmethod
    def naive_local(cls, value):
        if value is not None and value.tzinfo is not None:
            raise ValueError("Use a local date and time with a separate timezone")
        return value

    liquidity_limit: str = Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$")
    timing_model_id: str | None = Field(default=None, pattern=r"^timing-[0-9a-f]{20}$")
    scenario_seed: Annotated[int, Field(strict=True, ge=0, le=2**32-1)] = 42
    scenario_count: Annotated[int, Field(strict=True, ge=1, le=500)] = 200
    common_shock_weight: float = Field(default=0.5, ge=0, le=1)

    @field_validator("liquidity_limit")
    @classmethod
    def normalize_budget(cls, value):
        return format(Decimal(value), ".2f")


class JobConflict(ValueError):
    pass


class JobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.sqlite3"
        with self.connection() as db:
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS workers (owner TEXT PRIMARY KEY, seen_at REAL NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                request_hash TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, owner TEXT,
                lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT, error TEXT)""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def view(row):
        if row is None:
            raise KeyError("Job not found")
        return {"job_id": row["job_id"], "status": row["status"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "attempts": row["attempts"], "request": json.loads(row["payload"]),
                "result": json.loads(row["result"]) if row["result"] else None,
                "error": row["error"]}

    def submit(self, run_id, request, key: str):
        payload = {**request.model_dump(mode="json")}
        if run_id is not None:
            payload["run_id"] = run_id
        return self.submit_payload(payload, key)

    def submit_payload(self, payload, key, *, retry_of=None):
        request_hash = digest(payload if retry_of is None else {"request": payload, "retry_of": retry_of})
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            checkpoint = None
            if retry_of is not None:
                source = db.execute("SELECT * FROM jobs WHERE job_id=?", (retry_of,)).fetchone()
                if source is None:
                    raise KeyError("Job not found")
                if source['status'] not in ('FAILED', 'CANCELED') or json.loads(source['payload']) != payload:
                    raise JobConflict("Only the unchanged request of a failed or canceled job can be retried")
                checkpoint = source['result']
            prior = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise JobConflict("Idempotency key was already used for a different request")
                return self.view(prior)
            job_id = f"job-{uuid.uuid4().hex}"
            db.execute("""INSERT INTO jobs
                (job_id,idempotency_key,request_hash,payload,status,created_at,updated_at,result)
                VALUES (?,?,?,?, 'QUEUED',?,?,?)""",
                       (job_id, key, request_hash, canonical_bytes(payload).decode(), now, now, checkpoint))
            return self.view(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def get(self, job_id):
        with self.connection() as db:
            return self.view(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def worker_ping(self, owner):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO workers VALUES (?,?)", (owner, time.time()))

    def worker_health(self):
        with self.connection() as db:
            last = db.execute("SELECT MAX(seen_at) FROM workers").fetchone()[0]
        return {"available": last is not None and time.time()-last < 15, "last_seen_at": last,
                "message": "Jobs wait in the queue until a worker is available."}

    def checkpoint(self, job_id, owner, stage, outputs):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE job_id=? AND owner=? AND lease_until>?",
                             (job_id, owner, now)).fetchone()
            if row is None or row['status'] not in ('RUNNING', 'CANCEL_REQUESTED'):
                raise InterruptedError('Worker ownership or lease lost; no further stages will run')
            db.execute("UPDATE jobs SET result=?,updated_at=? WHERE job_id=? AND owner=?",
                       (canonical_bytes({"stage": stage, "outputs": outputs}).decode(), now, job_id, owner))
        # Commit completed outputs before stopping, so retry can reuse an expensive stage.
        if row['status'] == 'CANCEL_REQUESTED':
            raise InterruptedError('Cancellation requested; stopped between stages with completed outputs saved')

    def cancel(self, job_id):
        with self.connection() as db:
            db.execute("""UPDATE jobs SET status=CASE WHEN status='QUEUED' THEN 'CANCELED'
                ELSE 'CANCEL_REQUESTED' END, updated_at=? WHERE job_id=?
                AND status IN ('QUEUED','RUNNING')""", (time.time(), job_id))
        return self.get(job_id)

    def claim(self, owner, lease_seconds=60):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET status='FAILED',error='Worker lease expired; cancellation could not be confirmed',owner=NULL,lease_until=NULL WHERE status='CANCEL_REQUESTED' AND lease_until<=?", (now,))
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('RUNNING','CANCEL_REQUESTED') AND lease_until>?", (now,)).fetchone():
                return None
            # Bounded recovery avoids an endless crash loop.
            db.execute("""UPDATE jobs SET status='FAILED', error='Worker lease expired after 3 attempts',
                updated_at=?, owner=NULL, lease_until=NULL
                WHERE status='RUNNING' AND lease_until<=? AND attempts>=3""", (now, now))
            row = db.execute("""SELECT * FROM jobs WHERE status='QUEUED'
                OR (status='RUNNING' AND lease_until<=? AND attempts<3)
                ORDER BY created_at,job_id LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            db.execute("""UPDATE jobs SET status='RUNNING', owner=?, lease_until=?,
                attempts=attempts+1, updated_at=? WHERE job_id=?""",
                       (owner, now+lease_seconds, now, row["job_id"]))
            return self.view(db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())

    def heartbeat(self, job_id, owner, lease_seconds=60):
        now = time.time()
        with self.connection() as db:
            return db.execute("""UPDATE jobs SET lease_until=?,updated_at=?
                WHERE job_id=? AND owner=? AND status IN ('RUNNING','CANCEL_REQUESTED') AND lease_until>?""",
                              (now+lease_seconds, now, job_id, owner, now)).rowcount == 1

    def finish(self, job_id, owner, result=None, error=None):
        now = time.time()
        with self.connection() as db:
            return db.execute("""UPDATE jobs SET status=CASE WHEN status='CANCEL_REQUESTED' THEN 'CANCELED' ELSE ? END,result=COALESCE(?,result),error=?,updated_at=?,
                owner=NULL,lease_until=NULL WHERE job_id=? AND owner=? AND status IN ('RUNNING','CANCEL_REQUESTED')
                AND lease_until>?""",
                              ("FAILED" if error else "SUCCEEDED",
                               canonical_bytes(result).decode() if result is not None else None,
                               error, now, job_id, owner, now)).rowcount == 1


def work_once(root: Path, lease_seconds=60):
    from t1fx.domain import Scenario
    from t1fx.pipeline import run_comparison

    jobs = JobStore(root)
    owner = uuid.uuid4().hex
    job = jobs.claim(owner, lease_seconds)
    if job is None:
        return False
    stopped = threading.Event()

    def renew():
        while not stopped.wait(lease_seconds/3):
            if not jobs.heartbeat(job["job_id"], owner, lease_seconds):
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        payload = job["request"]
        if payload.get('kind'):
            from t1fx.workflow import execute_job
            result = execute_job(root, payload, job.get('result'),
                lambda stage, outputs: jobs.checkpoint(job['job_id'], owner, stage, outputs))
            jobs.finish(job['job_id'], owner, result=result)
            return True
        store = RunStore(root)
        request = RecomputeRequest.model_validate({k: v for k, v in payload.items() if k != 'run_id'})
        payload = request.model_dump(mode='json') | {'run_id': payload['run_id']}
        manifest = store.validate(payload["run_id"])
        scenario = Scenario.model_validate(store.read(payload["run_id"], "scenario.json"))
        if payload.get("evaluation_local"):
            from t1fx.calendars import local_instant

            local = datetime.fromisoformat(payload["evaluation_local"])
            decision = local_instant(local.date(), local.time(), payload["evaluation_timezone"])
            scenario = Scenario.model_validate(scenario.model_dump() | {"decision_at": decision})
        if payload.get('timing_model_id'):
            from t1fx.workflow import ensure_timing_compatible
            ensure_timing_compatible(root, payload['timing_model_id'], scenario)
        fx_rate_snapshot = store.read(payload["run_id"], "fx_rates.json")
        if payload.get("evaluation_local") and scenario.decision_at != datetime.fromisoformat(manifest["decision_at"]):
            fx_rate_snapshot = None
        result = run_comparison(
            scenario, int(Decimal(payload["liquidity_limit"])*100), store,
            timing_model_id=payload.get("timing_model_id"),
            scenario_seed=payload["scenario_seed"], scenario_count=payload["scenario_count"],
            common_shock_weight=payload["common_shock_weight"],
            fx_rate_snapshot=fx_rate_snapshot,
        )
        for row in result["runs"]:
            store.validate(row["run_id"])
        jobs.finish(job["job_id"], owner, result=result)
    except Exception as exc:
        # Errors are persisted for the local operator; process interruption leaves a recoverable lease.
        jobs.finish(job["job_id"], owner, error=f"{type(exc).__name__}: {exc}")
    finally:
        stopped.set()
        thread.join()
    return True


def run_worker(root: Path, once=False):
    jobs = JobStore(root)
    owner = uuid.uuid4().hex
    stop = threading.Event()
    def pulse():
        while not stop.is_set():
            jobs.worker_ping(owner)
            stop.wait(3)
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        if once:
            return work_once(root)
        while True:
            if not work_once(root):
                time.sleep(1)
    finally:
        stop.set()
        thread.join()
        with jobs.connection() as db:
            db.execute("DELETE FROM workers WHERE owner=?", (owner,))
