"""Isolated application and worker for browser acceptance checks."""
import subprocess
import sys
import tempfile
from pathlib import Path

import uvicorn
from datetime import UTC, datetime
import t1fx.api

# A deterministic test boundary; real deployments synchronize the clock online.
t1fx.api.online_time = lambda: {'utc_now': datetime.now(UTC).isoformat(),
                              'source': 'test://online-clock', 'stale': False}

from t1fx.api import create_app
from t1fx.fixtures import three_trade_scenario
from t1fx.pipeline import run_comparison
from t1fx.storage import RunStore

with tempfile.TemporaryDirectory(prefix="t1fx-browser-") as directory:
    root = Path(directory)
    # Frozen reference quote for the hand-checkable fixture; browser checks need no internet.
    rates = {"source": "Frankfurter v2 blended reference rates", "base_currency": "USD",
             "requested_date": "2026-10-14", "observations": [
                 {"currency": "EUR", "rate_base": "1.1", "observed_date": "2026-10-14",
                  "url": "test://frozen-quote"}]}
    run_comparison(three_trade_scenario(), 3_000_000, RunStore(root), fx_rate_snapshot=rates)
    worker = subprocess.Popen([sys.executable, "-m", "t1fx.cli", "--artifacts", directory, "worker"])
    try:
        uvicorn.run(create_app(root), host="127.0.0.1", port=8765, log_level="warning")
    finally:
        worker.terminate()
        worker.wait(timeout=10)
