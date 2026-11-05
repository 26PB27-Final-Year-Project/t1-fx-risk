import json
from datetime import datetime

import pytest

pytest.importorskip('sklearn')
pytest.importorskip('xgboost')

from t1fx.cli import main
from t1fx.datasets import validate_dataset
from t1fx.models import RiskModel
from t1fx.storage import RunStore
from t1fx.timing import load_model


def test_clean_bootstrap_prepares_models_and_five_valid_policies(tmp_path, capsys):
    main(['--artifacts', str(tmp_path), 'bootstrap'])
    result = json.loads(capsys.readouterr().out)
    validate_dataset(tmp_path, result['dataset_id'])
    risk = RiskModel(tmp_path, result['model_id'])
    timing, _ = load_model(tmp_path, result['timing_model_id'])
    store = RunStore(tmp_path)
    assert len(result['runs']) == 5
    for run in result['runs']:
        manifest = store.validate(run['run_id'])
        assert timing.trained_through < datetime.fromisoformat(manifest['decision_at'])
        assert manifest['timing_model_id'] == result['timing_model_id']
        assert run['currency'] == 'USD'
        assert store.read(run['run_id'], 'timing_forecasts.json')['scenario_count'] == 200
    assert risk.artifacts['training_manifest.json']['settlement_currencies'] == ['USD']
