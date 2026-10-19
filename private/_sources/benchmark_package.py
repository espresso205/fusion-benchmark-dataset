"""Build auditable development packages without changing conversion archives.

This module never claims to provision process/ACL isolation. The output root is
evaluator-owned; an actual runner must expose ONLY public/ to its agent identity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector

from benchmark_contract import CONTRACT_VERSION, PUBLIC_PROMPT, freeze_task, load_task
from benchmark_certify import CERTIFICATION_VERSION, EVIDENCE_FILES


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def audit_public_dxf(path, task, private_names=()):
    """Reject known private payloads/references; retain exact certified DXF bytes."""
    doc = ezdxf.readfile(path)
    audit = doc.audit()
    if audit.errors or audit.fixes:
        raise ValueError('DXF requires audit repairs')
    if doc.units != ezdxf.units.MM:
        raise ValueError('DXF must declare millimeters')
    notes = '\n'.join(e.dxf.text for e in doc.modelspace().query('TEXT'))
    notes += '\n' + '\n'.join(e.plain_text() for e in doc.modelspace().query('MTEXT'))
    axis = task['viewAxis']
    basis = {'X': ('Y', 'Z'), 'Y': ('Z', 'X'), 'Z': ('X', 'Y')}
    u, v = basis[axis]
    if 'UNITS mm' not in notes or f'VIEW +{axis}; U={u}, V={v}; ORIGIN (0,0,0)' not in notes:
        raise ValueError('DXF units/view/world basis must agree with frozen task')
    if not any(marker in notes for marker in ('THK ', 'REGION ', 'RADIAL ', 'FACE ')):
        raise ValueError('DXF is missing depth or explicit geometry semantics')
    forbidden_types = {'3DSOLID', 'BODY', 'REGION', 'SURFACE', 'MESH', '3DFACE',
                       'IMAGE', 'IMAGEDEF', 'OLE2FRAME', 'PDFUNDERLAY', 'DGNUNDERLAY', 'DWFUNDERLAY'}
    private_tokens = {'source.step', 'geometry.binbrep', 'geometry_reference.brep',
                      'feature_manifest.json', 'step_semantics.json', *private_names}
    private_tokens = {str(value).lower() for value in private_tokens if value}
    def check_string(value):
        if not isinstance(value, str):
            return
        text = value.lower()
        if any(token in text for token in private_tokens):
            raise ValueError('private file reference found in DXF')
        if re.search(r'(?:[a-z]:[\\/]|\\\\[^\s\\]+\\|(?<!\w)\.{1,2}[\\/]|'
                     r'(?<![\w\\])[\w.-]+\\[^\s\\]+\.[a-z0-9]+|'
                     r'file://|https?://|(?<!\w)/(?:[^\s/]+/)+)', text):
            raise ValueError('external path or URL found in DXF')
    # Inspect the bytes that will actually be published. ezdxf discards 999
    # comments and may omit unrecognized tags on re-export. Latin-1 preserves
    # every byte while retaining the ASCII path syntax checked here.
    raw = Path(path).read_bytes()
    if raw.startswith(b'AutoCAD Binary DXF'):
        raise ValueError('public audit requires an ASCII DXF tag stream')
    lines = raw.splitlines()
    if len(lines) % 2:
        raise ValueError('incomplete original DXF tag stream')
    for index in range(0, len(lines), 2):
        try:
            code = int(lines[index].strip())
        except ValueError as exc:
            raise ValueError('invalid original DXF group code') from exc
        value = lines[index + 1].decode('latin-1')
        check_string(value)
        kind = value.strip().upper()
        if code == 0 and (kind in forbidden_types or kind.endswith('SURFACE')):
            raise ValueError(f'private or unsupported payload entity: {kind}')
    header_tags = TagCollector(dxfversion=doc.dxfversion)
    doc.header.export_dxf(header_tags)
    for tag in header_tags.tags:
        check_string(tag.value)
    for block in doc.blocks:
        if block.block.dxf.flags & 12 or block.block.dxf.get('xref_path', ''):
            raise ValueError('external DXF block reference is not allowed')
    counts = {}
    for entity in doc.entitydb.values():
        if not entity.is_alive:
            continue
        kind = entity.dxftype()
        counts[kind] = counts.get(kind, 0) + 1
        if kind in forbidden_types or kind.endswith('SURFACE'):
            raise ValueError(f'private or unsupported payload entity: {kind}')
        for tag in TagCollector.dxftags(entity):
            check_string(tag.value)
    return {'passed': True, 'unit': 'mm', 'viewAxis': axis, 'entityCounts': counts,
            'scope': 'DXF validity, declared view/units/depth, external paths and known private payloads'}


def validate_certificate(certificate, data):
    """Bind retained independent-process evidence to a frozen task; not a signature."""
    certificate = Path(certificate)
    root = certificate.parent
    cert = data['certification']
    try:
        paths = [certificate, root, root / 'rebuild', root / 'rebuild/input',
                 *(root / name for name in EVIDENCE_FILES)]
        if any(path.is_symlink() or path.is_junction() for path in paths):
            raise ValueError('certification evidence must not contain links or junctions')
        if sha256(certificate) != cert['evidenceSha256']:
            raise ValueError('certification evidence hash differs from frozen task')
        report = json.loads(certificate.read_text(encoding='utf-8'))
        if (report.get('certificationVersion') != CERTIFICATION_VERSION
                or cert['version'] != CERTIFICATION_VERSION):
            raise ValueError('unsupported or mismatched certification version')
        if (report.get('passed') is not True or report.get('stepReimportPassed') is not True
                or report.get('stage') != 'completed'
                or report.get('reconstructionInputs') != ['drawing.dxf']
                or report.get('dxf_reconstructionExitCode') != 0
                or report.get('step_reimport_comparisonExitCode') != 0
                or not report.get('rebuildCommand') or not report.get('compareCommand')
                or report.get('fusionCompatible') is not cert['fusionCompatible']):
            raise ValueError('successful independent certification and STEP reimport are required')
        expected = {'drawingSha256': data['drawingSha256'],
                    'groundTruthSha256': data['groundTruthSha256'],
                    'reconstructedStepSha256': cert['reconstructedStepSha256']}
        if any(report.get(key) != value for key, value in expected.items()):
            raise ValueError('certification geometry hash differs from frozen task')
        inventory = report.get('evidenceFiles')
        if not isinstance(inventory, dict) or set(inventory) != set(EVIDENCE_FILES):
            raise ValueError('certification evidence inventory is incomplete')
        if any(sha256(root / name) != inventory[name] for name in EVIDENCE_FILES):
            raise ValueError('certification artifact hash mismatch')
        if (inventory['rebuild/input/drawing.dxf'] != expected['drawingSha256']
                or inventory['rebuild/reconstructed.step'] != expected['reconstructedStepSha256']):
            raise ValueError('certification input or reconstruction hash mismatch')
        comparison = json.loads((root / 'comparison.json').read_text(encoding='utf-8'))
        if (comparison != report.get('comparison') or comparison.get('passed') is not True
                or comparison.get('validStepReimports') is not True
                or any(comparison.get(key) != expected[key]
                       for key in ('groundTruthSha256', 'reconstructedStepSha256'))):
            raise ValueError('certification comparison evidence mismatch')
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise ValueError('certification evidence is missing or unreadable') from exc
    return report


def build_package(conversion, output, task, *, certificate):
    source, output = Path(conversion).resolve(), Path(output).resolve()
    data = freeze_task(task.as_dict()).as_dict()
    if output.exists():
        raise FileExistsError(output)
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('package and conversion roots must be disjoint')
    files = list(source.rglob('*'))
    if any(p.is_symlink() or p.is_junction() for p in files):
        raise ValueError('conversion must not contain links or junctions')
    for name, key in (('drawing.dxf', 'drawingSha256'), ('source.step', 'groundTruthSha256')):
        if sha256(source / name) != data[key]:
            raise ValueError(f'{name} hash does not match frozen task')
    generation = json.loads((source / 'generation_log.json').read_text(encoding='utf-8'))
    view = json.loads((source / 'view_contract.json').read_text(encoding='utf-8'))
    if generation.get('status') != 'solvable' or generation.get('solid_count') != 1:
        raise ValueError('v1 requires a solvable single-part conversion')
    if (view.get('unit') != data['unit'] or view.get('extrusion_axis') != data['viewAxis']
            or view.get('model_class') != data['modelClass']):
        raise ValueError('conversion view/model class differs from frozen task')
    explicit = data['modelClass'] == 'explicit_planar_brep'
    if explicit != (data['track'] == 'explicit_3d_constraints'):
        raise ValueError('explicit three-dimensional face tables require their own track')
    if not data['certification']['dxfOnlyPassed'] or not data['certification']['stepReimportPassed']:
        raise ValueError('independent certification including STEP reimport is required')
    certificate = Path(certificate)
    validate_certificate(certificate, data)
    original = generation.get('input', '')
    audit = audit_public_dxf(source / 'drawing.dxf', data, [original, Path(original).name])
    output.mkdir(parents=True, exist_ok=False)
    public, private = output / 'public', output / 'private'
    public.mkdir()
    private.mkdir()
    # Private archives remain useful for evaluator diagnostics and licensing.
    shutil.copytree(source, private / 'conversion')
    archive = private / 'certification'
    for name in EVIDENCE_FILES:
        target = archive / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(certificate.parent / name, target)
    shutil.copyfile(certificate, archive / 'certificate.json')
    # Revalidate the actual copied evidence, including the frozen certificate
    # digest, so mutation during copying cannot produce a successful package.
    validate_certificate(archive / 'certificate.json', data)
    shutil.copyfile(source / 'drawing.dxf', public / 'drawing.dxf')
    (public / 'TASK.md').write_text(PUBLIC_PROMPT, encoding='utf-8')
    task.write(private / 'task.json')
    if sha256(public / 'drawing.dxf') != data['drawingSha256']:
        raise ValueError('drawing hash changed during packaging')
    if sha256(private / 'conversion/source.step') != data['groundTruthSha256']:
        raise ValueError('ground truth hash changed during packaging')
    report = {'taskSha256': task.sha256, 'contractVersion': CONTRACT_VERSION,
              'isolation': 'development_unverified', 'formalEligible': False, 'dxfAudit': audit,
              'files': {p.relative_to(output).as_posix(): sha256(p)
                        for p in sorted(output.rglob('*')) if p.is_file()}}
    (private / 'package_report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('conversion', type=Path)
    parser.add_argument('task', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--certificate', type=Path, required=True,
                        help='independent certificate.json; its sibling evidence is archived privately')
    args = parser.parse_args()
    print(json.dumps(build_package(args.conversion, args.output, load_task(args.task),
                                   certificate=args.certificate), indent=2))
