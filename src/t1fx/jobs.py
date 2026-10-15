"""Durable local funding jobs with transactional claims and renewable leases."""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from t1fx.storage import RunStore, canonical_bytes, digest


class RecomputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    liquidity_limit: str = Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$")
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
            db.execute("PRAGMA journal_mode=WAL")
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

    def submit(self, run_id, request: RecomputeRequest, key: str):
        payload = {"run_id": run_id, **request.model_dump()}
        request_hash = digest(payload)
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise JobConflict("Idempotency key was already used for a different request")
                return self.view(prior)
            job_id = f"job-{uuid.uuid4().hex}"
            db.execute("""INSERT INTO jobs
                (job_id,idempotency_key,request_hash,payload,status,created_at,updated_at)
                VALUES (?,?,?,?, 'QUEUED',?,?)""",
                       (job_id, key, request_hash, canonical_bytes(payload).decode(), now, now))
            return self.view(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def get(self, job_id):
        with self.connection() as db:
            return self.view(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def cancel(self, job_id):
        with self.connection() as db:
            db.execute("""UPDATE jobs SET status='CANCELED', updated_at=?, owner=NULL,
                lease_until=NULL WHERE job_id=? AND status IN ('QUEUED','RUNNING')""",
                       (time.time(), job_id))
        return self.get(job_id)

    def claim(self, owner, lease_seconds=60):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
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
                WHERE job_id=? AND owner=? AND status='RUNNING' AND lease_until>?""",
                              (now+lease_seconds, now, job_id, owner, now)).rowcount == 1

    def finish(self, job_id, owner, result=None, error=None):
        now = time.time()
        with self.connection() as db:
            return db.execute("""UPDATE jobs SET status=?,result=?,error=?,updated_at=?,
                owner=NULL,lease_until=NULL WHERE job_id=? AND owner=? AND status='RUNNING'
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
        store = RunStore(root)
        manifest = store.validate(payload["run_id"])
        scenario = Scenario.model_validate(store.read(payload["run_id"], "scenario.json"))
        result = run_comparison(
            scenario, int(Decimal(payload["liquidity_limit"])*100), store,
            timing_model_id=manifest.get("timing_model_id"),
            scenario_seed=payload["scenario_seed"], scenario_count=payload["scenario_count"],
            common_shock_weight=payload["common_shock_weight"],
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
    if once:
        return work_once(root)
    while True:
        if not work_once(root):
            time.sleep(1)
