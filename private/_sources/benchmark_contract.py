"""Versioned, hash-frozen evaluator-side tasks for the DXF-only benchmark.

Hashes detect accidental mutation, not malicious rewriting by the file owner.
Keep these task records private; publish only drawing.dxf and the uniform prompt.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

CONTRACT_VERSION = 'interx-dxf-benchmark-v2'
TRACKS = {'semantic_dxf_single_view', 'explicit_3d_constraints'}
SUBMISSION_NAME = 'submission.f3d'
TRUSTED_STEP_NAME = 'trusted_export.step'
PUBLIC_PROMPT = '''Reconstruct the geometry specified by drawing.dxf using Fusion MCP.
The only task input is drawing.dxf. Read its geometry, text, dimensions, layers,
groups and curve parameters. A read-only DXF parser is allowed. Use the units,
view and world coordinates declared in the DXF; do not fit, scale or reposition
the result. Do not read evaluator data, other tasks, reference models or services.
Save a self-contained, reopenable Fusion archive named submission.f3d in the
assigned output directory. Evaluation reopens that archive and exports its STEP.
An agent-exported STEP does not replace the required F3D submission.
'''


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _keys(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required.split()):
        raise ValueError(f'{label}: expected fields {required}')


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f'{label}: expected nonempty bounded string')


def _hash(value, label):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError(f'{label}: expected lowercase SHA256 hash')


def _positive(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{label}: expected finite positive number')


def _validate(data):
    _keys(data, 'taskId dataset datasetVersion split track drawingSha256 groundTruthSha256 '
          'modelClass unit viewAxis coordinateFrame allowedTransforms certification evaluation '
          'budget execution isolation', 'task')
    for key in ('taskId', 'dataset', 'datasetVersion', 'modelClass'):
        _text(data[key], key)
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', data['taskId']):
        raise ValueError('taskId: expected safe identifier')
    if data['split'] not in ('dev', 'test') or data['track'] not in TRACKS:
        raise ValueError('unsupported split or track')
    for key in ('drawingSha256', 'groundTruthSha256'):
        _hash(data[key], key)
    if (data['unit'] != 'mm' or data['viewAxis'] not in ('X', 'Y', 'Z')
            or data['coordinateFrame'] != 'world' or data['allowedTransforms'] != []):
        raise ValueError('geometry must use mm, an orthographic axis and fixed world coordinates')
    cert = data['certification']
    _keys(cert, 'version evidenceSha256 reconstructedStepSha256 dxfOnlyPassed '
          'stepReimportPassed fusionCompatible', 'certification')
    _text(cert['version'], 'certification.version')
    for key in ('evidenceSha256', 'reconstructedStepSha256'):
        _hash(cert[key], 'certification.' + key)
    if any(type(cert[key]) is not bool for key in ('dxfOnlyPassed', 'stepReimportPassed', 'fusionCompatible')):
        raise ValueError('certification results must be booleans')
    evaluation = data['evaluation']
    _keys(evaluation, 'evaluatorVersion rubricVersion config', 'evaluation')
    for key in ('evaluatorVersion', 'rubricVersion'):
        _text(evaluation[key], key)
    config = evaluation['config']
    _keys(config, 'unit alignment linearTolerance assemblyInterferencePolicy surfaceMethod '
          'surfaceBackend maxSurfaceSamples interferenceVolumeTolerance', 'evaluation.config')
    # V1 admits single-part geometry only. Assemblies need a separate protocol.
    fixed = {'unit': 'mm', 'alignment': 'fixed', 'assemblyInterferencePolicy': 'strict',
             'surfaceMethod': 'brep', 'surfaceBackend': 'cpu'}
    for key, value in fixed.items():
        if config[key] != value:
            raise ValueError(f'evaluation.config.{key} must be {value}')
    for key in ('linearTolerance', 'interferenceVolumeTolerance'):
        _positive(config[key], key)
    if type(config['maxSurfaceSamples']) is not int or not 100 <= config['maxSurfaceSamples'] <= 10000:
        raise ValueError('maxSurfaceSamples: expected integer in [100, 10000]')
    budget = data['budget']
    _keys(budget, 'wallSeconds toolCalls maxAttempts infrastructureRetries', 'budget')
    for key in ('wallSeconds', 'toolCalls', 'maxAttempts', 'infrastructureRetries'):
        minimum = 0 if key == 'infrastructureRetries' else 1
        if type(budget[key]) is not int or budget[key] < minimum:
            raise ValueError(f'budget.{key}: expected integer >= {minimum}')
    execution = data['execution']
    if isinstance(execution, dict) and execution.get('status') == 'unconfigured':
        _keys(execution, 'status', 'execution')
    else:
        _keys(execution, 'status adapter adapterVersion modelVersion promptSha256 '
              'toolManifestSha256 fusionVersion', 'execution')
        if execution['status'] != 'configured':
            raise ValueError('execution.status must be configured or unconfigured')
        for key in ('adapter', 'adapterVersion', 'modelVersion', 'fusionVersion'):
            _text(execution[key], key)
        for key in ('promptSha256', 'toolManifestSha256'):
            _hash(execution[key], key)
    isolation = data['isolation']
    if isinstance(isolation, dict) and isolation.get('mode') == 'development':
        _keys(isolation, 'mode', 'isolation')
    else:
        _keys(isolation, 'mode evidenceSha256 executionIdentity', 'isolation')
        if isolation['mode'] != 'verified':
            raise ValueError('isolation.mode must be development or verified')
        _hash(isolation['evidenceSha256'], 'isolation.evidenceSha256')
        _text(isolation['executionIdentity'], 'isolation.executionIdentity')
    _canonical(data)


@dataclass(frozen=True)
class FrozenTask:
    canonical_json: str

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_json.encode('utf-8')).hexdigest()

    def as_dict(self):
        return json.loads(self.canonical_json)

    def write(self, path):
        payload = {'contractVersion': CONTRACT_VERSION, 'taskSha256': self.sha256, 'task': self.as_dict()}
        with Path(path).open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')


def freeze_task(data):
    _validate(data)
    return FrozenTask(_canonical(data))


def load_task(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    _keys(payload, 'contractVersion taskSha256 task', 'frozen task')
    if payload['contractVersion'] != CONTRACT_VERSION:
        raise ValueError('unsupported contract version')
    frozen = freeze_task(payload['task'])
    if frozen.sha256 != payload['taskSha256']:
        raise ValueError('frozen task hash mismatch')
    return frozen


def require_runnable(task, *, formal=True):
    """Validate recorded prerequisites; this does not perform an ACL/Fusion probe."""
    data = task.as_dict()
    _validate(data)
    if data['execution']['status'] != 'configured':
        raise ValueError('execution adapter/model/tools have not been configured and frozen')
    cert = data['certification']
    if not cert['dxfOnlyPassed'] or not cert['stepReimportPassed']:
        raise ValueError('independent DXF certification and STEP reimport are required')
    if formal:
        if data['isolation']['mode'] != 'verified':
            raise ValueError('formal execution requires actual identity-based isolation evidence')
        if not cert['fusionCompatible']:
            raise ValueError('formal execution requires Fusion compatibility evidence')
