"""Rebuild imported geometry from a native OCCT archive, without STEP or DXF.

This is a separate consumer, not a second CAD kernel. Native B-Rep retains
curves, support surfaces, pcurves, tolerances, orientation and shared topology.
STEP semantics live in an optional integrity-checked JSON sidecar.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re

import OCP
from OCP.BinTools import BinTools, BinTools_FormatVersion
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS_Shape
from OCP.TopTools import TopTools_FormatVersion

from step_topology import topology_inventory


def _native_bytes(shape):
    if shape.IsNull():
        raise ValueError('Cannot archive a null B-Rep')
    stream = io.BytesIO()
    # Fixed version and no tessellation: this archive represents native geometry.
    BinTools.Write_s(shape, stream, False, False,
                     BinTools_FormatVersion.BinTools_FormatVersion_VERSION_4)
    return stream.getvalue()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _native_text(shape):
    stream = io.BytesIO()
    BRepTools.Write_s(shape, stream, False, False, TopTools_FormatVersion.TopTools_FormatVersion_VERSION_3)
    return stream.getvalue()


def compare_native_geometry(source, rebuilt):
    """Compare every native geometry/topology token, allowing float roundoff only.

    BinTools normalizes gp_Dir on read. ASCII v3 exposes the entire native
    representation with 17-digit real values; both-integer tokens (including
    indices/counts) and every nonnumeric token must match exactly. An integer
    spelling can also be a normalized real (0.99999999999999989 -> 1).
    This is a scalar-parameter tolerance, not a spatial Hausdorff certificate.
    """
    left, right = source.split(), rebuilt.split()
    report = {'equal': len(left) == len(right), 'source_tokens': len(left),
              'rebuilt_tokens': len(right), 'changed_numeric_tokens': 0,
              'max_absolute_numeric_change': 0., 'relative_tolerance': 1e-12,
              'absolute_tolerance': 1e-12, 'mismatched_tokens': 0,
              'method': 'complete_OCCT_ASCII_v3_token_comparison'}
    for a, b in zip(left, right):
        if a == b:
            continue
        if re.fullmatch(rb'[+-]?\d+', a) and re.fullmatch(rb'[+-]?\d+', b):
            equal = False
        else:
            try:
                x, y = float(a), float(b)
                equal = math.isfinite(x) and math.isfinite(y) and math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12)
                if math.isfinite(x) and math.isfinite(y):
                    report['changed_numeric_tokens'] += 1
                    report['max_absolute_numeric_change'] = max(report['max_absolute_numeric_change'], abs(x-y))
            except ValueError:
                equal = False
        if not equal:
            report['equal'] = False
            report['mismatched_tokens'] += 1
    return report


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _measurements(shape):
    result = {}
    for name, calculate in [('volume_mm3', BRepGProp.VolumeProperties_s),
                            ('surface_area_mm2', BRepGProp.SurfaceProperties_s)]:
        try:
            props = GProp_GProps(); calculate(shape, props)
            value = float(props.Mass())
            result[name] = value if math.isfinite(value) else None
        except Exception as exc:
            result[name] = None
            result[name + '_error'] = type(exc).__name__
    try:
        box = Bnd_Box(); BRepBndLib.AddOptimal_s(shape, box, False, False)
        result['bounds_mm'] = None if box.IsVoid() else list(box.Get())
    except Exception as exc:
        result['bounds_mm'] = None
        result['bounds_error'] = type(exc).__name__
    return result


def _same_records(a, b):
    """Allow only arithmetic roundoff in extracted diagnostics, never ID drift."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same_records(v, b[k]) for k, v in a.items())
    if isinstance(a, list):
        return len(a) == len(b) and all(_same_records(x, y) for x, y in zip(a, b))
    return a == b


def _incidence(inventory):
    """Topology identity independent of diagnostic adaptor-derived parameters."""
    fields = {'vertices': ['vertex_id'],
              'edges': ['edge_id', 'degenerated', 'face_ids', 'wire_ids', 'vertex_ids', 'vertex_uses'],
              'wires': ['wire_id', 'closed', 'edge_uses', 'face_ids'],
              'faces': ['face_id', 'wire_ids', 'edge_ids', 'vertex_ids', 'shell_ids'],
              'shells': ['shell_id', 'closed', 'face_ids', 'face_uses'],
              'solids': ['solid_id', 'orientation', 'face_ids', 'shell_ids', 'shell_uses']}
    result = {kind: [{key: row[key] for key in keys} for row in inventory[kind]]
              for kind, keys in fields.items()}
    result['loops'] = [[{key: value for key, value in loop.items() if key != 'ordered_edge_uses'} |
                         {'ordered_edge_uses': [{k: v for k, v in use.items() if k not in ('pcurve', 'pcurve_error')}
                                                for use in loop['ordered_edge_uses']]}
                        for loop in face['loops']] for face in inventory['faces']]
    result['counts'] = inventory['counts']
    result['validation'] = inventory['validation']
    return result


def _report(shape, expected, native_equal, inventory, native_geometry):
    valid = bool(BRepCheck_Analyzer(shape).IsValid())
    topology_equal = _same_records(expected['topology_inventory'], inventory)
    incidence_equal = _incidence(expected['topology_inventory']) == _incidence(inventory)
    measurements = _measurements(shape)
    measurements_equal = _same_records(expected['measurements'], measurements)
    root_equal = expected['root'] == {'type': shape.ShapeType().name,
                                      'orientation': shape.Orientation().name}
    validity_equal = expected['input_brep_valid'] == valid
    preserved = native_geometry['equal'] and incidence_equal and measurements_equal and root_equal and validity_equal
    valid_roundtrip = preserved and valid and expected['input_brep_valid']
    status = ('verified_imported_brep' if valid_roundtrip else
              'invalid_brep_preserved' if preserved and not valid and not expected['input_brep_valid'] else
              'roundtrip_mismatch')
    return {'schema_version': 1, 'status': status,
            'valid_brep_roundtrip': bool(valid_roundtrip),
            'serialization_roundtrip_verified': bool(preserved),
            'native_serialization_equal': native_equal,
            'native_geometry_comparison': native_geometry,
            'oriented_topology_equal': incidence_equal,
            'topology_and_geometry_inventory_equal': topology_equal,
            'measurements_equal': measurements_equal, 'root_equal': root_equal,
            'brep_validity_equal': validity_equal,
            'input_brep_valid': expected['input_brep_valid'], 'rebuilt_brep_valid': valid,
            'topology_counts': inventory['counts'], 'validation': inventory['validation'],
            'measurements': measurements, 'kernel': 'OCCT via OCP ' + OCP.__version__,
            'independent_consumer': True, 'independent_kernel_validation': False,
            'source_step_required': False, 'dxf_reconstruction': False,
            'scope': 'Native imported B-Rep serialization; not proof of STEP import correctness or DXF losslessness'}


def compare_breps(source, rebuilt):
    """Compare all serialized native geometry, not merely mass or topology counts."""
    expected = {'topology_inventory': topology_inventory(source),
                'measurements': _measurements(source),
                'root': {'type': source.ShapeType().name, 'orientation': source.Orientation().name},
                'input_brep_valid': bool(BRepCheck_Analyzer(source).IsValid())}
    return _report(rebuilt, expected, _native_bytes(source) == _native_bytes(rebuilt),
                   topology_inventory(rebuilt), compare_native_geometry(_native_text(source), _native_text(rebuilt)))


def _checked_payload(folder, record):
    name = record['file']
    if not isinstance(name, str) or Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('Archive file must be a local filename')
    path = (folder / name).resolve()
    if path.parent != folder.resolve():
        raise ValueError('Archive path escapes package directory')
    payload = path.read_bytes()
    if _sha(payload) != record['sha256']:
        raise ValueError(f'SHA256 mismatch: {name}')
    return payload


def _validate_binding_targets(bindings, counts):
    checked = 0
    for name, rows in bindings.items():
        if not name.endswith('_bindings') or not isinstance(rows, list):
            continue
        for row in rows:
            for target in row.get('targets', []):
                kind, identifier = target.get('kind'), target.get('id')
                if (kind not in counts or type(identifier) is not int
                        or not 1 <= identifier <= counts[kind]):
                    raise ValueError(f'Invalid topology target in {name}: {target}')
                checked += 1
    return checked


def rebuild_brep(descriptor_path):
    """Read only the descriptor and its declared payloads; return shape and audit."""
    path = Path(descriptor_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 2 or data.get('format') != 'OCCT BinTools v4':
        raise ValueError('Unsupported B-Rep package schema or format')
    if type(data.get('input_brep_valid')) is not bool:
        raise ValueError('input_brep_valid must be a boolean')
    payload = _checked_payload(path.parent, data['native_brep'])
    reference_text = _checked_payload(path.parent, data['native_reference'])
    semantics = None
    if data.get('step_semantics'):
        semantics = json.loads(_checked_payload(path.parent, data['step_semantics']))
    shape = TopoDS_Shape()
    BinTools.Read_s(shape, io.BytesIO(payload))
    if shape.IsNull():
        raise ValueError('Native archive contains a null B-Rep')
    native_equal = _native_bytes(shape) == payload
    report = _report(shape, data, native_equal, topology_inventory(shape),
                     compare_native_geometry(reference_text, _native_text(shape)))
    report['native_archive_sha256'] = _sha(payload)
    report['semantic_sidecar_integrity'] = 'verified' if semantics is not None else 'not_present'
    report['semantic_binding_status'] = (semantics.get('bindings', {}).get('status', 'see_sidecar')
                                          if semantics is not None else 'not_present')
    report['semantic_topology_targets_checked'] = (_validate_binding_targets(
        semantics.get('bindings', {}), report['topology_counts']) if semantics is not None else 0)
    return shape, report


def export_brep_package(shape, out, topology=None, metadata=None, bindings=None):
    """Write a portable package, then exercise the independent reader immediately."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    names = ['geometry.binbrep', 'geometry_reference.brep', 'brep_serialization.json', 'brep_roundtrip_report.json']
    if metadata is not None or bindings is not None:
        names.append('step_semantics.json')
    if any((out / name).exists() for name in names):
        raise FileExistsError('B-Rep package files already exist')
    topology = topology if topology is not None else topology_inventory(shape)
    _validate_binding_targets(bindings or {}, topology['counts'])
    payload = _native_bytes(shape)
    (out / 'geometry.binbrep').write_bytes(payload)
    reference_text = _native_text(shape)
    (out / 'geometry_reference.brep').write_bytes(reference_text)
    descriptor = {'schema_version': 2, 'format': 'OCCT BinTools v4',
                  'complete': False, 'completeness_scope': 'Native imported geometry within declared scalar tolerances; STEP semantics separately audited',
                  'writer_version': OCP.__version__, 'unit': 'mm',
                  'native_brep': {'file': 'geometry.binbrep', 'sha256': _sha(payload), 'bytes': len(payload)},
                  'native_reference': {'file': 'geometry_reference.brep', 'sha256': _sha(reference_text)},
                  'input_brep_valid': bool(BRepCheck_Analyzer(shape).IsValid()),
                  'root': {'type': shape.ShapeType().name, 'orientation': shape.Orientation().name},
                  'measurements': _measurements(shape), 'topology_inventory': topology}
    if metadata is not None or bindings is not None:
        semantic_path = out / 'step_semantics.json'
        _write_json(semantic_path, {'schema_version': 1, 'metadata': metadata or {}, 'bindings': bindings or {}})
        descriptor['step_semantics'] = {'file': semantic_path.name, 'sha256': _sha(semantic_path.read_bytes())}
    _write_json(out / 'brep_serialization.json', descriptor)
    _, report = rebuild_brep(out / 'brep_serialization.json')
    # Do not advertise an unverified serialization as complete.
    descriptor['complete'] = report['serialization_roundtrip_verified']
    _write_json(out / 'brep_serialization.json', descriptor)
    _write_json(out / 'brep_roundtrip_report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('descriptor', type=Path)
    parser.add_argument('--out', type=Path, required=True, help='New or empty reconstruction directory')
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('Output directory must be empty')
    shape, report = rebuild_brep(args.descriptor)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'reconstructed.binbrep').write_bytes(_native_bytes(shape))
    (args.out / 'reconstructed.brep').write_bytes(_native_text(shape))
    descriptor = json.loads(args.descriptor.read_text(encoding='utf-8'))
    if descriptor.get('step_semantics'):
        (args.out / 'step_semantics.json').write_bytes(_checked_payload(args.descriptor.parent, descriptor['step_semantics']))
    _write_json(args.out / 'brep_roundtrip_report.json', report)
    files = sorted(p for p in args.out.iterdir() if p.is_file())
    (args.out / 'SHA256SUMS').write_text(''.join(_sha(p.read_bytes()) + '  ' + p.name + '\n' for p in files), encoding='ascii')
    print(json.dumps({k: report[k] for k in ('status', 'serialization_roundtrip_verified', 'valid_brep_roundtrip')}, indent=2))
    return 0 if report['serialization_roundtrip_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
