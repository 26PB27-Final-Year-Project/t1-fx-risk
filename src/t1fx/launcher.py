"""Cross-platform API/worker supervision; uses the active interpreter, never installs."""
import importlib
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def check_setup():
    for name in ('uvicorn','sklearn','xgboost'):
        try:
            importlib.import_module(name)
        except (ImportError,OSError) as exc:
            raise RuntimeError(f'{name} cannot load: {exc}. Run uv sync --extra ml --extra parquet. On macOS XGBoost may need brew install libomp.') from exc
    try:
        ZoneInfo('America/New_York')
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError('IANA timezone data is missing. Run uv sync to install tzdata.') from exc
    dashboard=Path(__file__).parent/'dashboard'/'index.html'
    if not dashboard.exists():
        raise RuntimeError('Dashboard is not built. Run npm ci --prefix frontend then npm run build --prefix frontend.')


def supervise(root:Path, port=8000):
    check_setup()
    root=root.resolve()
    from t1fx.jobs import JobStore
    try:
        JobStore(root)  # Initialize SQLite before starting concurrent services.
    except (OSError,sqlite3.Error) as exc:
        raise RuntimeError(f'Cannot open artifact workspace {root}: {exc}. Choose a writable directory with --artifacts PATH.') from exc
    if not 1 <= port <= 65535:
        raise RuntimeError('Use a port from 1 to 65535.')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1',port))
        except OSError as exc:
            raise RuntimeError(f'Port {port} is unavailable. Stop the existing server or use start --port 8001.') from exc
    children=[]
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    previous={s:signal.signal(s,stop) for s in (signal.SIGINT,signal.SIGTERM)}
    try:
        for command in (['worker'],['serve','--port',str(port)]):
            children.append(subprocess.Popen([sys.executable,'-m','t1fx.cli','--artifacts',str(root),*command]))
        print(f'Opening local workspace: http://127.0.0.1:{port}/\nArtifacts: {root}\nPress Ctrl+C to stop API and worker.',flush=True)
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError(f'A service exited with code {child.returncode}. See its error above; both services are stopping.')
            time.sleep(.2)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
        for sig,handler in previous.items():
            signal.signal(sig,handler)
