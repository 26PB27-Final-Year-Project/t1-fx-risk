import copy
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip('sklearn')
pytest.importorskip('xgboost')

from fastapi.testclient import TestClient

from t1fx.api import create_app
from t1fx.cli import main
from t1fx.datasets import generate_release
from t1fx.domain import Scenario
from t1fx.features import FEATURE_SCHEMA, fit_preprocessing, matrix, snapshot_weights
from t1fx.generation import load_config
from t1fx.model_metrics import clustered_intervals, evaluate
from t1fx.models import ModelStore, ModelUnavailable, RiskModel, calibrated
from t1fx.pipeline import run_comparison
from t1fx.storage import RunStore
from t1fx.training import fit_sigmoid, prepare, train_model


@pytest.fixture(scope='module')
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp('models')
    dataset = generate_release(load_config(Path('configs/generation/smoke.json')), root, 'json')
    result = train_model(root, dataset['dataset_id'])
    directory = root/'datasets'/dataset['dataset_id']
    tables = {name: json.loads((directory/entry['json']).read_text()) for name, entry in dataset['tables'].items()}
    return root, dataset, result, tables


def test_bundle_load_predict_matches_frozen_test_and_shap_additivity(trained):
    root, _, result, tables = trained
    model = RiskModel(root, result['model_id'])
    rows = [r for r in tables['snapshots'] if r['split'] == 'test']
    trades = {t['trade_id']: t for t in tables['trades']}
    predictions = model.predict(rows, trades)
    saved = model.artifacts['test_predictions.json']
    for prediction, frozen in zip(predictions, saved, strict=True):
        assert prediction['p_fail_any'] == pytest.approx(frozen['p_fail_any'])
        assert prediction['logistic_p_fail_any'] == pytest.approx(frozen['logistic_p_fail_any'])
        explanation = prediction['explanation']
        assert explanation['expected_margin']+sum(c['value'] for c in explanation['contributions']) == pytest.approx(prediction['raw_margin'], abs=1e-5)
        assert 0 <= prediction['p_fail_any'] <= 1
        assert explanation['units'] == 'log_odds'
    assert model.artifacts['metrics.json']['counterparty_groups']['unseen']['xgboost']['rows'] > 0
    assert model.artifacts['metrics.json']['stress_family']['test_metrics']['xgboost']['rows'] > 0
    assert model.artifacts['metrics.json']['xgboost_uncertainty']['status'] == 'FEW_CLUSTERS'
    assert len(FEATURE_SCHEMA['units']) == len(FEATURE_SCHEMA['columns'])


def test_repeated_training_is_identical(trained):
    root, dataset, result, _ = trained
    assert train_model(root, dataset['dataset_id']) == result


def test_test_labels_and_hidden_truth_cannot_change_fitting_features(trained):
    _, _, _, tables = trained
    trades, rows, labels, history, held_out = prepare(tables)
    original = matrix(rows['train'], trades, history)
    changed = copy.deepcopy(tables)
    for label in changed['outcomes']:
        if label['split'] == 'test':
            label['missed_contractual_deadline'] = not label['missed_contractual_deadline']
    changed['injections'] = []
    changed['counterparty_truth'] = []
    trades2, rows2, labels2, history2, _ = prepare(changed)
    assert history == history2
    assert np.array_equal(original, matrix(rows2['train'], trades2, history2))
    assert not np.array_equal(labels['test'], labels2['test'])
    assert all(trades[r['trade_id']]['counterparty_id'] not in held_out for r in rows['calibration'])
    assert all(h['counterparty_id'] not in held_out for h in history)
    pp = fit_preprocessing(original, snapshot_weights(rows['train']))
    assert pp['mean'] == pytest.approx(np.average(original, weights=snapshot_weights(rows['train']), axis=0))


def test_incompatible_missing_and_corrupt_bundles_fail_closed(trained, tmp_path):
    root, _, result, _ = trained
    with pytest.raises(ModelUnavailable):
        RiskModel(root, 'model-00000000000000000000')
    artifacts = ModelStore(root).read(result['model_id'])
    artifacts['feature_schema.json'] = {'version': 'incompatible'}
    bad_id = ModelStore(tmp_path).publish(artifacts)
    with pytest.raises(ModelUnavailable, match='incompatible'):
        RiskModel(tmp_path, bad_id)
    artifacts = ModelStore(root).read(result['model_id'])
    good_id = ModelStore(tmp_path).publish(artifacts)
    path = tmp_path/'models'/good_id/'calibration.json'
    path.write_text('{}')
    with pytest.raises(ModelUnavailable, match='checksum'):
        RiskModel(tmp_path, good_id)


def test_old_observations_and_wrong_policy_are_not_calibrated_predictions(trained):
    root, _, result, tables = trained
    model = RiskModel(root, result['model_id'])
    trades = {t['trade_id']: t for t in tables['trades']}
    with pytest.raises(ModelUnavailable, match='after calibration'):
        model.predict([r for r in tables['snapshots'] if r['split'] == 'train'][:1], trades)
    row = next(r for r in tables['snapshots'] if r['split'] == 'test')
    with pytest.raises(ModelUnavailable, match='reference-policy'):
        model.predict([row | {'reference_policy_id': 'lp'}], trades)


def test_calibration_single_class_and_extreme_margins():
    with pytest.raises(ValueError, match='both outcome classes'):
        fit_sigmoid(np.array([0, 1]), np.array([0, 0]), np.ones(2))
    params = fit_sigmoid(np.array([-1000, -1, 1, 1000]), np.array([0, 0, 1, 1]), np.ones(4))
    p = calibrated(np.array([-1000, 0, 1000]), params)
    assert np.all(np.isfinite(p)) and np.all(np.diff(p) >= 0)


def test_single_class_metrics_are_explicit_and_capacity_has_stable_ties():
    rows = [{'trade_id': str(i), 'scenario_id': 'day', 'as_of': 'time'} for i in range(10)]
    trades = {str(i): {'settlement_amount_minor': 100} for i in range(10)}
    report = evaluate(rows, np.zeros(10), np.ones(10)*.2, trades)
    assert report['roc_auc'] is None
    assert report['pr_auc_average_precision'] is None
    assert report['alert_capacity']['selected_rows'] == 1
    assert report['alert_capacity']['recall'] is None
    assert clustered_intervals(rows, np.zeros(10), np.ones(10)*.2)['status'] == 'INSUFFICIENT_CLUSTERS'


def test_model_api_and_cli(trained, capsys):
    root, dataset, result, tables = trained
    client = TestClient(create_app(root))
    assert client.get('/api/v1/models').json()['items'][0]['model_id'] == result['model_id']
    health = client.get(f"/api/v1/models/{result['model_id']}/health")
    assert health.status_code == 200
    assert health.json()['status'] == 'AVAILABLE'
    assert client.get('/api/v1/models/model-00000000000000000000/health').json()['code'] == 'MODEL_UNAVAILABLE'
    main(['--artifacts', str(root), 'validate-model', '--model', result['model_id']])
    assert json.loads(capsys.readouterr().out)['status'] == 'VALID'
    row = next(r for r in tables['snapshots'] if r['split'] == 'test')
    scenario_path = root/'datasets'/dataset['dataset_id']/'scenarios'/f"{row['scenario_id']}.json"
    scenario = Scenario.model_validate_json(scenario_path.read_text())
    replay = run_comparison(scenario, 100000, RunStore(root))
    response = client.get(f"/api/v1/runs/{replay['runs'][0]['run_id']}/model-predictions",
                          params={'model_id': result['model_id'], 'trade_id': row['trade_id']})
    assert response.status_code == 200, response.text
    prediction = response.json()['items'][0]
    frozen = next(p for p in ModelStore(root).read(result['model_id'])['test_predictions.json']
                  if p['trade_id'] == row['trade_id'] and p['as_of'] == scenario.decision_at.isoformat(timespec='microseconds'))
    assert prediction['p_fail_any'] == pytest.approx(frozen['p_fail_any'])
    other_policy = client.get(f"/api/v1/runs/{replay['runs'][-1]['run_id']}/model-predictions",
                              params={'model_id': result['model_id'], 'trade_id': row['trade_id']})
    assert other_policy.json()['items'] == response.json()['items']
