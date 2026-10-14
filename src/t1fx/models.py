"""Verified JSON model bundles and reference-policy inference, without pickle."""

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import numpy as np
from scipy.special import expit

from t1fx.features import FEATURE_HASH, FEATURE_SCHEMA, FEATURES, matrix, rule_scores, timestamp, transform
from t1fx.storage import ArtifactError, canonical_bytes, digest

FILES = {'model.json', 'logistic.json', 'preprocessing.json', 'calibration.json',
         'feature_schema.json', 'thresholds.json', 'metrics.json', 'training_manifest.json',
         'history.json', 'test_predictions.json', 'model_card.md'}
FITTED_FILES = {'model.json', 'logistic.json', 'preprocessing.json', 'calibration.json',
                'feature_schema.json', 'thresholds.json', 'history.json'}


class ModelUnavailable(ArtifactError):
    pass


def xgboost_module():
    try:
        import xgboost
        return xgboost
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise ModelUnavailable('Install .[ml]; on macOS XGBoost also requires brew install libomp') from exc


class ModelStore:
    def __init__(self, root):
        self.root = Path(root)

    def directory(self, model_id):
        if re.fullmatch(r'model-[0-9a-f]{20}', model_id) is None:
            raise ModelUnavailable('Invalid model ID')
        return self.root/'models'/model_id

    def publish(self, artifacts):
        if set(artifacts) != FILES:
            raise ModelUnavailable('Incomplete model bundle')
        raw = {name: value.encode() if name.endswith('.md') else canonical_bytes(value)
               for name, value in artifacts.items()}
        hashes = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
        model_id = f'model-{digest(hashes)[:20]}'
        target = self.directory(model_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.read(model_id)
            return model_id
        stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=target.parent))
        try:
            for name, value in raw.items():
                (stage/name).write_bytes(value)
            (stage/'manifest.json').write_bytes(canonical_bytes({
                'schema_version': '1.0', 'model_id': model_id, 'status': 'SUCCEEDED', 'files': hashes,
            }))
            try:
                os.rename(stage, target)
            except OSError:
                if not target.exists():
                    raise
                self.read(model_id)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return model_id

    def read(self, model_id):
        directory = self.directory(model_id)
        try:
            manifest = json.loads((directory/'manifest.json').read_text())
            if (manifest.get('model_id') != model_id or manifest.get('status') != 'SUCCEEDED'
                    or manifest.get('schema_version') != '1.0'
                    or set(manifest['files']) != FILES
                    or f"model-{digest(manifest['files'])[:20]}" != model_id):
                raise ModelUnavailable('Model manifest identity mismatch')
            artifacts = {}
            for name, checksum in manifest['files'].items():
                raw = (directory/name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != checksum:
                    raise ModelUnavailable(f'Model checksum mismatch: {name}')
                artifacts[name] = raw.decode() if name.endswith('.md') else json.loads(raw)
            if artifacts['feature_schema.json'] != FEATURE_SCHEMA:
                raise ModelUnavailable('Model feature schema is incompatible')
            training = artifacts['training_manifest.json']
            if training['feature_schema_hash'] != FEATURE_HASH or training['reference_policy_id'] != 'fixed-0':
                raise ModelUnavailable('Model policy or feature compatibility mismatch')
            if digest({name: artifacts[name] for name in FITTED_FILES}) != training['fitted_artifacts_hash']:
                raise ModelUnavailable('Frozen model identity mismatch')
            return artifacts
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, ModelUnavailable):
                raise
            raise ModelUnavailable('Model bundle unavailable or invalid') from exc

    def list_models(self):
        result = []
        for path in sorted((self.root/'models').glob('model-*/manifest.json')):
            artifacts = self.read(path.parent.name)
            result.append({'model_id': path.parent.name,
                           'dataset_id': artifacts['training_manifest.json']['dataset_id'],
                           'reference_policy_id': 'fixed-0'})
        return result


def calibrated(margins, params):
    return expit(params['slope']*np.asarray(margins)+params['intercept'])


class RiskModel:
    def __init__(self, root, model_id):
        self.model_id = model_id
        self.artifacts = ModelStore(root).read(model_id)
        xgb = xgboost_module()
        expected = self.artifacts['training_manifest.json']['versions']['xgboost']
        actual = importlib.metadata.version('xgboost')
        if actual.split('.')[0] != expected.split('.')[0]:
            raise ModelUnavailable(f'XGBoost major version mismatch: trained {expected}, installed {actual}')
        try:
            self.booster = xgb.Booster(params={'nthread': 1})
            self.booster.load_model(bytearray(json.dumps(self.artifacts['model.json']).encode()))
            if self.booster.feature_names != list(FEATURES):
                raise ModelUnavailable('Booster feature order mismatch')
            transform(np.zeros((1, len(FEATURES))), self.artifacts['preprocessing.json'])
            logistic = self.artifacts['logistic.json']
            coefficients = np.asarray(logistic['coefficients'])
            if (coefficients.shape != (len(FEATURES),) or not np.all(np.isfinite(coefficients))
                    or not np.isfinite(logistic['intercept'])):
                raise ModelUnavailable('Invalid logistic coefficients')
            for name in ('xgboost', 'logistic'):
                params = self.artifacts['calibration.json'][name]
                if (params['method'] != 'sigmoid_raw_margin' or params['slope'] < 0
                        or not np.all(np.isfinite([params['slope'], params['intercept']]))):
                    raise ModelUnavailable('Invalid calibration parameters')
        except (ValueError, RuntimeError) as exc:
            raise ModelUnavailable('Model cannot be loaded with this feature schema') from exc

    def predict(self, rows, trades):
        if not rows:
            return []
        known_at = self.artifacts['training_manifest.json']['partitions']['calibration']['last_label_known_at']
        if any(timestamp(row['as_of']) <= timestamp(known_at) for row in rows):
            raise ModelUnavailable('Inference must occur after calibration labels matured; historical training-period scoring is unsupported')
        try:
            x = transform(matrix(rows, trades, self.artifacts['history.json']),
                          self.artifacts['preprocessing.json'])
        except ValueError as exc:
            raise ModelUnavailable(str(exc)) from exc
        xgb = xgboost_module()
        data = xgb.DMatrix(x, feature_names=list(FEATURES), nthread=1)
        margins = self.booster.predict(data, output_margin=True)
        contributions = self.booster.predict(data, pred_contribs=True)
        if not np.allclose(contributions.sum(axis=1), margins, atol=1e-5):
            raise ModelUnavailable('Tree SHAP contributions do not sum to the raw margin')
        probability = calibrated(margins, self.artifacts['calibration.json']['xgboost'])
        logistic = self.artifacts['logistic.json']
        lr_margin = x@np.asarray(logistic['coefficients'])+logistic['intercept']
        lr_probability = calibrated(lr_margin, self.artifacts['calibration.json']['logistic'])
        rules = rule_scores(rows)
        result = []
        for i, row in enumerate(rows):
            full = [{'feature': name, 'value': float(contributions[i, j])}
                    for j, name in enumerate(FEATURES)]
            result.append({'trade_id': row['trade_id'], 'as_of': row['as_of'],
                           'model_id': self.model_id, 'reference_policy_id': 'fixed-0',
                           'status': 'CALIBRATED_REFERENCE_POLICY',
                           'p_fail_any': float(probability[i]),
                           'raw_probability': float(expit(margins[i])),
                           'raw_margin': float(margins[i]),
                           'logistic_p_fail_any': float(lr_probability[i]),
                           'rule_score': float(rules[i]*100),
                           'alert': bool(probability[i] >= self.artifacts['thresholds.json']['probability']),
                           'explanation': {'method': 'TreeSHAP', 'units': 'log_odds',
                                           'expected_margin': float(contributions[i, -1]),
                                           'contributions': full,
                                           'top_three': sorted(full, key=lambda item: -abs(item['value']))[:3],
                                           'interpretation': 'Associations in the raw XGBoost margin; not causal effects or calibrated probability changes.'}})
        return result
