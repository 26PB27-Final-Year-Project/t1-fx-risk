from unittest.mock import Mock
import pytest
from t1fx.launcher import supervise


def test_launcher_supervises_both_children_and_stops_sibling(tmp_path,monkeypatch):
    monkeypatch.setattr('t1fx.launcher.check_setup',lambda:None)
    probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
    monkeypatch.setattr('t1fx.launcher.socket.socket',lambda:probe)
    worker=Mock();worker.poll.side_effect=[None,None];worker.wait.return_value=0
    api=Mock();api.poll.return_value=2;api.returncode=2
    spawn=Mock(side_effect=[worker,api]);monkeypatch.setattr('t1fx.launcher.subprocess.Popen',spawn)
    monkeypatch.setattr('t1fx.launcher.signal.signal',lambda *args:None)
    with pytest.raises(RuntimeError,match='service exited'):
        supervise(tmp_path,8768)
    assert spawn.call_count==2
    first,second=[call.args[0] for call in spawn.call_args_list]
    assert first[0]==second[0] and first[4]==second[4]==str(tmp_path.resolve())
    assert first[-1]=='worker' and second[-3]=='serve'
    worker.terminate.assert_called_once()
    worker.wait.assert_called_once()


def test_launcher_reports_port_conflict_before_spawning(tmp_path,monkeypatch):
    monkeypatch.setattr('t1fx.launcher.check_setup',lambda:None)
    probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
    probe.bind.side_effect=OSError('occupied')
    monkeypatch.setattr('t1fx.launcher.socket.socket',lambda:probe)
    with pytest.raises(RuntimeError,match='Port 8000'):
        supervise(tmp_path)


def test_timezone_data_available():
    from zoneinfo import ZoneInfo
    assert ZoneInfo('America/New_York').key=='America/New_York'
    assert ZoneInfo('Asia/Kolkata').key=='Asia/Kolkata'
