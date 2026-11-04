from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from t1fx import online_clock
from t1fx.api import create_app
from t1fx.fixtures import three_trade_scenario
from t1fx.pipeline import run_comparison
from t1fx.storage import RunStore


def test_online_clock_advances_from_online_anchor_and_expires(monkeypatch):
    ticks = [100.0]
    monkeypatch.setattr(online_clock, '_sample', None)
    monkeypatch.setattr(online_clock.time, 'monotonic', lambda: ticks[0])

    @contextmanager
    def response(*_args, **_kwargs):
        class Response:
            headers = {'Date': 'Wed, 04 Nov 2026 14:00:00 GMT'}
        yield Response()

    monkeypatch.setattr(online_clock, 'urlopen', response)
    assert online_clock.online_time()['utc_now'] == '2026-11-04T14:00:00+00:00'
    ticks[0] += 20
    assert online_clock.online_time()['utc_now'] == '2026-11-04T14:00:20+00:00'

    def unavailable(*_args, **_kwargs):
        raise OSError('offline')

    monkeypatch.setattr(online_clock, 'urlopen', unavailable)
    ticks[0] = 170
    assert online_clock.online_time()['stale'] is True
    ticks[0] = 401
    with pytest.raises(online_clock.OnlineClockUnavailable):
        online_clock.online_time()


def test_api_rejects_future_evaluation_in_selected_timezone(tmp_path, monkeypatch):
    monkeypatch.setattr('t1fx.api.online_time', lambda: {
        'utc_now': '2026-10-14T12:00:00+00:00', 'source': 'test://clock', 'stale': False,
    })
    run = run_comparison(three_trade_scenario(), 3000000, RunStore(tmp_path))['runs'][0]
    client = TestClient(create_app(tmp_path))
    assert client.get('/api/v1/time').json()['utc_now'] == '2026-10-14T12:00:00+00:00'
    response = client.post(f"/api/v1/runs/{run['run_id']}/funding/recompute", json={
        'liquidity_limit': '30000', 'evaluation_local': '2026-10-14T18:00:00',
        'evaluation_timezone': 'Asia/Kolkata',
    }, headers={'Idempotency-Key': 'future-clock'})
    assert response.status_code == 422
    assert 'current online date and time' in response.json()['message']
