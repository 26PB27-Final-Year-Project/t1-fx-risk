"""Isolated application and worker for browser acceptance checks."""
import subprocess
import sys
import tempfile
from pathlib import Path

import uvicorn

from t1fx.api import create_app
from t1fx.fixtures import three_trade_scenario
from t1fx.pipeline import run_comparison
from t1fx.storage import RunStore

with tempfile.TemporaryDirectory(prefix="t1fx-browser-") as directory:
    root = Path(directory)
    run_comparison(three_trade_scenario(), 3_000_000, RunStore(root))
    worker = subprocess.Popen([sys.executable, "-m", "t1fx.cli", "--artifacts", directory, "worker"])
    try:
        uvicorn.run(create_app(root), host="127.0.0.1", port=8765, log_level="warning")
    finally:
        worker.terminate()
        worker.wait(timeout=10)
