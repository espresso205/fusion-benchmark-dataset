"""Generate ten complex, independently certified DXF-only development tasks.

This generator and all source definitions are grader-side material. No agent or
Fusion calls are made. A complete.json marker is written only after ten passes.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import shutil
import sys
import traceback

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeWire
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.Geom import Geom_BezierCurve
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TColStd import TColStd_Array1OfReal
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.gp import gp_Ax2, gp_Dir, gp_Elips, gp_Pnt, gp_Vec

from benchmark_certify import certify
from benchmark_contract import freeze_task
from benchmark_package import build_package, sha256
from step_to_dxf import analyze_faces, bounds, generate, items, mass


def box(x, y, z, dx, dy, dz):
    return {'kind': 'box', 'origin': [x, y, z], 'size': [dx, dy, dz]}


def cylinder(x, y, z, radius, height):
    return {'kind': 'cylinder', 'origin': [x, y, z], 'radius': radius, 'height': height}


def slot(a, b, radius, z, height):
    return {'kind': 'capsule', 'a': list(a), 'b': list(b), 'radius': radius, 'z': z, 'height': height}


def polygon(points, z, height):
    return {'kind': 'polygon', 'points': [list(p) for p in points], 'z': z, 'height': height}


def cut(shape, feature):
    return {'action': 'cut', 'feature': feature, 'shape': shape}


def add(shape, feature):
    return {'action': 'fuse', 'feature': feature, 'shape': shape}


def definitions():
    """Explicit reproducible parameters in mm; no dependence on external assets."""
    rows = []
    def register(qid, english, chinese, family, base, operations):
        rows.append({'taskId': qid, 'titleEn': english, 'titleZh': chinese, 'family': family,
                     'sourceRole': 'locally_constructed_analytic_challenge', 'unit': 'mm',
                     'viewAxis': 'Z', 'base': base, 'operations': operations})

    holes = [(x, y) for x in (-42, 0, 42) for y in (-28, 28)]
    ops = [cut(cylinder(x, y, 5, 3.5, 20), 'through mounting hole') for x, y in holes]
    ops += [cut(cylinder(x, y, 18, 6, 7), 'counterbore') for x in (-42, 42) for y in (-28, 28)]
    ops += [cut(cylinder(x, 0, 12, 4, 13), 'blind locating bore') for x in (-34, 34)]
    ops += [cut(slot((-12, 0), (12, 0), 4, 14, 11), 'blind central capsule pocket')]
    register('Q01', 'Multidepth fixture plate with central capsule pocket', '多深度夹具板', 'fixture_plate',
             box(-60, -45, 6, 120, 90, 18), ops)

    outline = [(0, 0), (110, 0), (110, 34), (44, 34), (44, 96), (0, 96)]
    holes = [(12, 12), (32, 12), (70, 12), (96, 12), (12, 48), (32, 48), (12, 80), (32, 80)]
    ops = [cut(cylinder(x, y, -6, 3, 18), 'through mounting hole') for x, y in holes]
    ops += [cut(cylinder(x, y, 6, 5, 6), 'counterbore') for x, y in (holes[0], holes[3], *holes[-2:])]
    ops += [cut(box(54, 21, 3, 42, 8, 9), 'rectangular blind relief'),
            cut(slot((22, 57), (22, 67), 4, 1, 11), 'long arm blind slot')]
    register('Q02', 'Pocketed L mounting bracket', '凹槽与沉孔组合 L 形支架', 'l_bracket',
             polygon(outline, -5, 16), ops)

    center = (15, -12)
    holes = [(15 + 46 * math.cos(i * math.pi / 4), -12 + 46 * math.sin(i * math.pi / 4)) for i in range(8)]
    ops = [cut(cylinder(*center, 2, 16, 20), 'central through bore'),
           cut(cylinder(*center, 15, 25, 7), 'stepped central recess')]
    ops += [cut(cylinder(x, y, 2, 3.5, 20), 'bolt circle through hole') for x, y in holes]
    ops += [cut(cylinder(x, y, 14, 6, 8), 'alternating bolt counterbore') for x, y in holes[::2]]
    ops += [cut(cylinder(15 + x, -12, 2, 2.5, 20), 'index locating hole') for x in (-32, 32)]
    register('Q03', 'Stepped circular flange and indexed bolt pattern', '阶梯中心孔与交错沉孔法兰', 'circular_flange',
             cylinder(*center, 3, 62, 18), ops)

    def radial(low, high, r0, r1):
        return {'kind': 'cone' if r0 != r1 else 'cylinder', 'origin': [23, -17, low],
                'radius': r0, 'topRadius': r1, 'height': high - low}
    outer = [(-8, -2, 28, 28), (-2, 4, 28, 23), (4, 12, 23, 23),
             (12, 16, 30, 30), (16, 21, 30, 24), (21, 29, 24, 24),
             (29, 35, 24, 18), (35, 41, 18, 18), (41, 46, 22, 22), (46, 53, 22, 19)]
    inner = [(-8, 0, 10, 10), (0, 7, 10, 13), (7, 16, 13, 13), (16, 23, 11, 11),
             (23, 31, 11, 8), (31, 38, 8, 8), (38, 45, 10, 10), (45, 53, 10, 12)]
    ops = [add(radial(*section), 'outer cylindrical/conical section') for section in outer[1:]]
    ops += [cut(radial(*section), 'inner cylindrical/conical section') for section in inner]
    register('Q04', 'Ten-stage external and eight-stage internal sleeve', '内外阶梯锥段组合套筒', 'coaxial_sleeve',
             radial(*outer[0]), ops)

    ops = [cut(cylinder(x, y, 10, 3, 10), 'through mounting hole') for x in (-48, -22, 22, 48) for y in (-20, 20)]
    ops += [cut(slot((-22, 0), (22, 0), 4, 10, 10), 'long horizontal through slot'),
            cut(slot((0, -28), (0, -18), 3, 10, 10), 'vertical through slot'),
            cut(box(-7, 20, 10, 14, 7, 10), 'rectangular through key')]
    register('Q05', 'Elliptical tooling plate with three distinct slots', '椭圆外形多孔异向槽板', 'elliptical_plate',
             {'kind': 'ellipse', 'origin': [0, 0, 11], 'majorRadius': 68, 'minorRadius': 42, 'height': 8}, ops)

    spline = {'kind': 'spline_profile', 'z': -7, 'height': 9,
              'poles': [[-60, 0], [-40, 60], [30, 54], [60, 0]], 'weights': [1, .8, 1.25, 1],
              'returnPoints': [[52, -24], [10, -32], [-38, -24]]}
    holes = [(-40, 3), (-22, 8), (0, 12), (22, 10), (42, 3), (-27, -12), (0, -15), (28, -10)]
    ops = [cut(cylinder(x, y, -8, 3, 11), 'asymmetric through bore') for x, y in holes]
    ops += [cut(slot((-18, -1), (0, -1), 3, -8, 11), 'first through capsule'),
            cut(slot((13, -1), (30, -1), 2.5, -8, 11), 'second through capsule')]
    register('Q06', 'Rational-spline cam plate with asymmetric apertures', '有理样条轮廓与非对称孔槽板', 'rational_spline_plate', spline, ops)

    outline = [(0, 0), (100, 0), (100, 92), (72, 92), (72, 30), (28, 30), (28, 92), (0, 92)]
    holes = [(x, y) for x in (14, 86) for y in (16, 52, 78)]
    ops = [cut(cylinder(x, y, -1, 3, 18), 'arm through hole') for x, y in holes]
    ops += [cut(cylinder(x, y, 10, 5, 7), 'upper arm counterbore') for x in (14, 86) for y in (52, 78)]
    ops += [cut(box(x, 30, 9, 14, 12, 8), 'arm rectangular blind pocket') for x in (7, 79)]
    ops += [cut(slot((40, 14), (60, 14), 4, -1, 18), 'bridge through capsule')]
    register('Q07', 'Fork frame with pockets and counterbored arms', '双叉臂沉孔凹槽框架', 'fork_frame', polygon(outline, 0, 16), ops)

    outline = [(-22, -60), (22, -60), (22, -22), (66, -22), (66, 22), (22, 22),
               (22, 60), (-22, 60), (-22, 22), (-66, 22), (-66, -22), (-22, -22)]
    holes = [(x, y) for x in (-12, 12) for y in (-44, 44)] + [(x, y) for x in (-48, 48) for y in (-12, 12)]
    ops = [cut(cylinder(x, y, -12, 3, 24), 'cross-arm mounting hole') for x, y in holes]
    ops += [cut(cylinder(x, y, 5, 5, 7), 'vertical arm counterbore') for x, y in holes[:4]]
    ops += [cut(cylinder(0, 0, -2, 13, 14), 'central blind chamber')]
    ops += [cut(cylinder(x, 0, -5, 4, 17), 'side locating blind bore') for x in (-35, 35)]
    ops += [cut(box(-9, -36, -12, 18, 12, 7), 'underside rectangular relief')]
    register('Q08', 'Cross manifold with opposite-side relief', '十字多深度腔体与底面凹槽', 'cross_manifold', polygon(outline, -11, 22), ops)

    outline = [(-42, -52), (42, -52), (62, -32), (62, 32), (42, 52), (-42, 52), (-62, 32), (-62, -32)]
    ops = [cut(box(-16, -12, 1, 32, 24, 17), 'rectangular central opening')]
    ops += [cut(slot((x, -18), (x, 18), 4, 1, 17), 'long through mounting slot') for x in (-42, 42)]
    holes = [(x, y) for x in (-25, 25) for y in (-36, 36)]
    ops += [cut(cylinder(x, y, 1, 3.5, 17), 'adapter through hole') for x, y in holes]
    ops += [cut(cylinder(x, y, 10, 6, 8), 'adapter counterbore') for x, y in holes]
    ops += [cut(box(-8, y, 8, 16, 8, 10), 'opposed rectangular blind pocket') for y in (-34, 26)]
    register('Q09', 'Octagonal window adapter with slotted mounts', '八角开窗转接板与长槽安装孔', 'octagonal_adapter', polygon(outline, 2, 15), ops)

    ops = [add(box(-48, -28, 13, 96, 56, 9), 'raised rectangular deck'),
           add(cylinder(0, 0, 22, 18, 8), 'raised central boss')]
    ops += [cut(cylinder(x, y, 4, 3.5, 10), 'base mounting hole') for x in (-58, 58) for y in (-28, 28)]
    holes = [(x, y) for x in (-35, 35) for y in (-16, 16)]
    ops += [cut(cylinder(x, y, 4, 3, 20), 'deck through bore') for x, y in holes]
    ops += [cut(cylinder(x, y, 16, 5.5, 8), 'deck counterbore') for x, y in holes]
    ops += [cut(cylinder(0, 0, 4, 6, 27), 'boss through bore'),
            cut(slot((-37, 0), (-25, 0), 3, 17, 7), 'left blind slot'),
            cut(slot((25, 0), (37, 0), 3, 19, 5), 'right shallower blind slot')]
    register('Q10', 'Three-level carrier with central boss and unequal pockets', '三级凸台载板与异深度凹槽', 'stepped_carrier', box(-70, -40, 5, 140, 80, 8), ops)
    return rows


def _boolean(left, right, action):
    operation = (BRepAlgoAPI_Cut if action == 'cut' else BRepAlgoAPI_Fuse)(left, right)
    if not operation.IsDone():
        raise ValueError(f'source {action} failed')
    operation.SimplifyResult()
    return operation.Shape()


def _primitive(spec):
    kind = spec['kind']
    if kind == 'box':
        return BRepPrimAPI_MakeBox(gp_Pnt(*spec['origin']), *spec['size']).Shape()
    if kind in ('cylinder', 'cone'):
        axis = gp_Ax2(gp_Pnt(*spec['origin']), gp_Dir(0, 0, 1))
        if kind == 'cone':
            return BRepPrimAPI_MakeCone(axis, spec['radius'], spec['topRadius'], spec['height']).Shape()
        return BRepPrimAPI_MakeCylinder(axis, spec['radius'], spec['height']).Shape()
    if kind == 'capsule':
        a, b, r, z, h = spec['a'], spec['b'], spec['radius'], spec['z'], spec['height']
        distance = math.dist(a, b)
        nx, ny = -(b[1] - a[1]) / distance * r, (b[0] - a[0]) / distance * r
        points = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                  (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
        shape = _primitive(polygon(points, z, h))
        for x, y in (a, b):
            shape = _boolean(shape, _primitive(cylinder(x, y, z, r, h)), 'fuse')
        return shape
    if kind == 'polygon':
        builder = BRepBuilderAPI_MakePolygon()
        for x, y in spec['points']:
            builder.Add(gp_Pnt(x, y, spec['z']))
        builder.Close()
        wire = builder.Wire()
    elif kind == 'ellipse':
        ellipse = gp_Elips(gp_Ax2(gp_Pnt(*spec['origin']), gp_Dir(0, 0, 1)), spec['majorRadius'], spec['minorRadius'])
        wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(ellipse).Edge()).Wire()
    elif kind == 'spline_profile':
        poles = TColgp_Array1OfPnt(1, len(spec['poles']))
        weights = TColStd_Array1OfReal(1, len(spec['weights']))
        for index, ((x, y), weight) in enumerate(zip(spec['poles'], spec['weights']), 1):
            poles.SetValue(index, gp_Pnt(x, y, spec['z']))
            weights.SetValue(index, weight)
        curve = Geom_BezierCurve(poles, weights)
        builder = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(curve).Edge())
        vertices = [spec['poles'][-1], *spec['returnPoints'], spec['poles'][0]]
        for a, b in zip(vertices, vertices[1:]):
            builder.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(*a, spec['z']), gp_Pnt(*b, spec['z'])).Edge())
        wire = builder.Wire()
    else:
        raise ValueError('unsupported source primitive: ' + kind)
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire).Face(), gp_Vec(0, 0, spec['height'])).Shape()


def build_model(definition):
    shape = _primitive(definition['base'])
    for operation in definition['operations']:
        if operation['action'] not in ('cut', 'fuse'):
            raise ValueError('unsupported source operation')
        shape = _boolean(shape, _primitive(operation['shape']), operation['action'])
    return shape


def geometry_stats(shape):
    return {'valid': BRepCheck_Analyzer(shape).IsValid(),
            'solidCount': len(items(shape, TopAbs_SOLID)), 'faceCount': len(items(shape, TopAbs_FACE)),
            'edgeCount': len(items(shape, TopAbs_EDGE)), 'vertexCount': len(items(shape, TopAbs_VERTEX)),
            'surfaceCounts': dict(Counter(f['kind'] for f in analyze_faces(shape))),
            'volumeMm3': mass(shape), 'boundsMm': bounds(shape)}


def _write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def _task(definition, view, certificate, certificate_path):
    from cadpy.step_evaluator import EVALUATOR_VERSION
    from cadpy.step_assessment import RUBRIC_VERSION
    return freeze_task({
        'taskId': definition['taskId'], 'dataset': 'complex-single-view-challenge', 'datasetVersion': '2026-09-16-v1',
        'split': 'dev', 'track': 'semantic_dxf_single_view',
        'drawingSha256': certificate['drawingSha256'], 'groundTruthSha256': certificate['groundTruthSha256'],
        'modelClass': view['model_class'], 'unit': 'mm', 'viewAxis': definition['viewAxis'],
        'coordinateFrame': 'world', 'allowedTransforms': [],
        'certification': {'version': certificate['certificationVersion'], 'evidenceSha256': sha256(certificate_path),
                          'reconstructedStepSha256': certificate['reconstructedStepSha256'],
                          'dxfOnlyPassed': certificate['passed'], 'stepReimportPassed': certificate['stepReimportPassed'],
                          'fusionCompatible': False},
        'evaluation': {'evaluatorVersion': EVALUATOR_VERSION, 'rubricVersion': RUBRIC_VERSION,
                       'config': {'unit': 'mm', 'alignment': 'fixed', 'linearTolerance': .01,
                                  'assemblyInterferencePolicy': 'strict', 'surfaceMethod': 'brep', 'surfaceBackend': 'cpu',
                                  'maxSurfaceSamples': 5000, 'interferenceVolumeTolerance': 1e-8}},
        'budget': {'wallSeconds': 1800, 'toolCalls': 200, 'maxAttempts': 1, 'infrastructureRetries': 1},
        'execution': {'status': 'unconfigured'}, 'isolation': {'mode': 'development'},
    })


def generate_set(output, *, roster=None, timeout=300):
    roster = definitions() if roster is None else list(roster)
    if len(roster) != 10 or {r['taskId'] for r in roster} != {f'Q{i:02d}' for i in range(1, 11)}:
        raise ValueError('exactly ten unique task IDs Q01 through Q10 are required')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for definition in roster:
        case = output / definition['taskId']
        case.mkdir()
        _write(case / 'source_definition.json', definition)
        row = {key: definition[key] for key in ('taskId', 'titleEn', 'titleZh', 'family')}
        row['constructionFeatureCount'] = len(definition['operations'])
        row['status'] = 'failed'
        stage = 'source_construction'
        try:
            shape = build_model(definition)
            stats = geometry_stats(shape)
            row['complexity'] = stats
            _write(case / 'complexity.json', {**row, 'status': 'source_inspected', 'complexity': stats})
            if not stats['valid'] or stats['solidCount'] != 1 or stats['faceCount'] < 20:
                raise ValueError('challenge source must be valid, one solid, and have at least 20 faces')
            source = case / 'reference.step'
            writer = STEPControl_Writer()
            if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone or writer.Write(str(source)) != IFSelect_RetDone:
                raise ValueError('source STEP export failed')
            stage = 'conversion'
            conversion = case / 'conversion'
            result = generate(source, conversion, view=definition['viewAxis'], mode='standard')
            row['generation'] = result
            if result['status'] != 'solvable':
                raise ValueError('source is not independently single-view solvable')
            view = json.loads((conversion / 'view_contract.json').read_text(encoding='utf-8'))
            if view['model_class'] == 'explicit_planar_brep':
                raise ValueError('challenge requires engineering semantic track, not explicit face tables')
            row['modelClass'] = view['model_class']
            stage = 'certification'
            certificate = certify(conversion / 'drawing.dxf', source, case / 'certification', timeout=timeout)
            row['certification'] = certificate
            if not certificate['passed']:
                raise ValueError('independent DXF-only reconstruction/STEP reimport certification failed')
            stage = 'packaging'
            task = _task(definition, view, certificate, case / 'certification/certificate.json')
            shutil.copyfile(case / 'source_definition.json', conversion / 'challenge_source.json')
            shutil.copyfile(Path(__file__), conversion / 'challenge_generator.py')
            _write(conversion / 'challenge_metadata.json', {**row, 'status': 'certified',
                   'suggestedBudgetOnly': True, 'budgetEnforced': False,
                   'difficultyBasis': 'compound construction operations and exact topology; not calibrated agent difficulty'})
            row['package'] = build_package(conversion, case / 'package', task,
                                            certificate=case / 'certification/certificate.json')
            row['status'] = 'certified'
        except Exception as exc:
            row['failure'] = {'stage': stage, 'error': f'{type(exc).__name__}: {exc}'}
            _write(case / 'failure.json', {**row['failure'], 'traceback': traceback.format_exc()})
        rows.append(row)
        _write(output / 'results.json', {'cases': rows})
        print(definition['taskId'], row['status'], row.get('failure', ''), flush=True)
    summary = {'status': 'completed' if all(r['status'] == 'certified' for r in rows) else 'incomplete',
               'candidateCount': len(rows), 'certifiedCount': sum(r.get('certification', {}).get('passed', False) for r in rows),
               'packageCount': sum('package' in r for r in rows), 'formalEligibleCount': 0,
               'fusionTestedCount': 0, 'agentAttemptCount': 0, 'suggestedBudgetEnforced': False,
               'failures': [{'taskId': r['taskId'], **r['failure']} for r in rows if 'failure' in r],
               'cases': [{k: r[k] for k in ('taskId', 'titleEn', 'titleZh', 'family', 'constructionFeatureCount', 'status', 'complexity', 'modelClass') if k in r} for r in rows]}
    _write(output / 'summary.json', summary)
    if summary['status'] != 'completed' or summary['packageCount'] != 10:
        raise RuntimeError(f'challenge set incomplete: {summary["packageCount"]}/10 packages; evidence retained at {output}')
    _write(output / 'complete.json', {'taskIds': [r['taskId'] for r in rows], 'summarySha256': sha256(output / 'summary.json'),
                                    'status': 'development_unverified', 'formalEligible': False})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--timeout', type=float, default=300, help='timeout in seconds for each certification child')
    args = parser.parse_args()
    try:
        result = generate_set(args.output, timeout=args.timeout)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
