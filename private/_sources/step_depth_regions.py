"""Certify piecewise constant depth sections without inferring feature history."""
import json
import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakePrism
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopAbs import TopAbs_FORWARD, TopAbs_SOLID
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt, gp_Vec


def certify_regions(shape, faces, basis, lo, hi, tolerance, max_intervals=24):
    from step_to_dxf import analyze_faces, bounds, certify_prism, items, mass, vector
    n = basis[2]
    result = {'passed': False, 'model_class': 'piecewise_uniform_sections'}
    # Only parallel walls and perpendicular caps can bound constant sections.
    for face in faces:
        if face['kind'] == 'plane':
            component = abs(face['normal'][n])
            if 1e-9 < component < 1 - 1e-9:
                result['reason'] = 'sloped_planar_surface'
                return result, []
        elif face['kind'] != 'cylinder' or abs(face['normal'][n]) < 1 - 1e-9:
            result['reason'] = 'non_axial_or_non_prismatic_surface'
            return result, []
    levels = sorted([lo[n], hi[n]] + [f['location'][n] for f in faces
                    if f['kind'] == 'plane' and abs(f['normal'][n]) > 1 - 1e-9])
    levels = [value for i, value in enumerate(levels) if i == 0 or value - levels[i - 1] > 1e-7]
    if len(levels) > max_intervals + 1:
        result['reason'] = 'depth_region_limit'
        return result, []
    regions, prisms = [], []
    margin = max(hi[i] - lo[i] for i in range(3)) + 1
    for lower, upper in zip(levels, levels[1:]):
        start, end = [x - margin for x in lo], [x + margin for x in hi]
        start[n], end[n] = lower, upper
        slab = BRepPrimAPI_MakeBox(gp_Pnt(*start), *[b - a for a, b in zip(start, end)]).Shape()
        common = BRepAlgoAPI_Common(shape, slab)
        if not common.IsDone():
            result['reason'] = 'section_boolean_failed'
            return result, []
        common.SimplifyResult()
        for solid in items(common.Shape(), TopAbs_SOLID):
            if mass(solid) <= 1e-10:
                continue
            sl, sh = bounds(solid)
            sf = analyze_faces(solid)
            proof, cap = certify_prism(solid, sf, basis, sl, sh, 1, tolerance)
            if not proof['passed']:
                result['reason'] = 'nonuniform_section'
                return result, []
            flat = TopoDS.Face_s(cap['face'].Oriented(TopAbs_FORWARD))
            if cap['normal'][n] < 0:
                flat.Reverse()
            prism = BRepPrimAPI_MakePrism(flat, gp_Vec(*[x * (sh[n] - sl[n]) for x in vector(n)])).Shape()
            prisms.append(prism)
            regions.append({'id': f'R{len(regions) + 1}', 'depth_range': [sl[n], sh[n]], 'cap': cap,
                            'source_face_ids': [f['id'] for f in faces if f['kind'] == 'plane'
                                                and abs(f['normal'][n]) > 1 - 1e-9
                                                and min(abs(f['location'][n] - sl[n]), abs(f['location'][n] - sh[n])) < tolerance]})
    if not prisms:
        return result, []
    rebuilt = prisms[0]
    for prism in prisms[1:]:
        fused = BRepAlgoAPI_Fuse(rebuilt, prism)
        if not fused.IsDone():
            return result, []
        rebuilt = fused.Shape()
    if not BRepCheck_Analyzer(rebuilt).IsValid():
        return result, []
    errors = []
    for a, b in ((shape, rebuilt), (rebuilt, shape)):
        difference = BRepAlgoAPI_Cut(a, b)
        if not difference.IsDone():
            return result, []
        errors.append(mass(difference.Shape()))
    budget = max(1e-8, mass(shape) * 1e-8)
    result.update(passed=all(e <= budget for e in errors), difference_volumes_mm3=errors,
                  volume_tolerance_mm3=budget, depth_levels=levels, region_count=len(regions))
    return result, regions if result['passed'] else []


def region_geometry(regions, basis, tolerance, global_depth):
    from step_to_dxf import profile_geometry, CURVE_FIELDS
    boundaries, curves, features, rows, keys = [], [], [], [], {}
    for region in regions:
        projected, wire_features = profile_geometry(region['cap'], basis, tolerance)
        outer, holes = None, []
        for wire in wire_features:
            group = [c for c in projected if c['feature_id'] == wire['id']]
            if len(group) == 1 and group[0]['entity'] == 'CIRCLE':
                wire.update(center=group[0]['center'], diameter=2 * group[0]['radius'])
            geometry = [{k: c[k] for k in CURVE_FIELDS if k in c} for c in group]
            def rounded(value):
                if isinstance(value, float):
                    return round(value, 8)
                if isinstance(value, list):
                    return [rounded(x) for x in value]
                if isinstance(value, dict):
                    return {k: rounded(v) for k, v in value.items()}
                return value
            for g in geometry:
                if g['entity'] == 'LINE':
                    g['start'], g['end'] = sorted([g['start'], g['end']])
            key = json.dumps(sorted([json.dumps(g, sort_keys=True) for g in geometry]))
            is_outer = wire['semantic_type'] == 'outer_profile'
            if key not in keys:
                bid = f'B{len(boundaries) + 1}'
                keys[key] = bid
                boundaries.append({'id': bid, 'source_face_ids': region['source_face_ids'], 'depth_ranges': [],
                                   'roles': [], 'wire_semantics': wire, 'source': 'boolean_section_boundary'})
                for c in group:
                    c.update(feature_id=bid, boundary_id=bid, source_face_id=None,
                             source_face_ids=region['source_face_ids'], source='boolean_section_boundary')
                    curves.append(c)
            bid = keys[key]
            boundary = next(b for b in boundaries if b['id'] == bid)
            boundary['depth_ranges'].append(region['depth_range'])
            boundary['roles'].append('outer' if is_outer else 'void')
            if is_outer:
                outer = bid
            else:
                holes.append(bid)
        rows.append({'id': region['id'], 'outer': outer, 'voids': holes, 'depth_range': region['depth_range'],
                     'source_face_ids': region['source_face_ids'], 'semantic_type': 'material_depth_region', 'value_type': 'exact'})
    for boundary in boundaries:
        wire = boundary.pop('wire_semantics')
        kind = 'circular_section_boundary' if 'diameter' in wire else 'section_boundary'
        if set(boundary['roles']) == {'void'}:
            merged = []
            for a, b in sorted(boundary['depth_ranges']):
                if merged and a <= merged[-1][1] + tolerance:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            boundary['depth_ranges'] = merged
            low = min(r[0] for r in boundary['depth_ranges'])
            high = max(r[1] for r in boundary['depth_ranges'])
            opening = ('segmented' if len(merged) > 1 else 'through' if abs(low - global_depth[0]) < tolerance and abs(high - global_depth[1]) < tolerance
                       else 'blind' if abs(low - global_depth[0]) < tolerance or abs(high - global_depth[1]) < tolerance else 'internal')
            kind = opening + ('_hole' if 'diameter' in wire else '_slot' if 'width' in wire else '_pocket')
        record = {**boundary, 'semantic_type': kind, 'value_type': 'derived', 'confidence': 1.0}
        for key in ('center', 'diameter', 'end_centers', 'width', 'center_distance'):
            if key in wire:
                record[key] = wire[key]
        features.append(record)
    for curve in curves:
        feature = next(f for f in features if f['id'] == curve['boundary_id'])
        if feature['semantic_type'].startswith('internal_'):
            curve['layer'] = 'HIDDEN'
    return curves, features, rows


def anchor(curve):
    if curve['entity'] == 'LINE':
        return [(a + b) / 2 for a, b in zip(curve['start'], curve['end'])]
    if curve['entity'] in {'CIRCLE', 'ARC'}:
        angle = math.radians(curve.get('start_angle', 0))
        return [curve['center'][0] + curve['radius'] * math.cos(angle), curve['center'][1] + curve['radius'] * math.sin(angle)]
    if curve['entity'] == 'ELLIPSE':
        from ezdxf.math import ConstructionEllipse
        ellipse = ConstructionEllipse(center=curve['center'], major_axis=curve['major_axis'], ratio=curve['ratio'])
        return list(next(iter(ellipse.vertices([curve['start_param']]))))[:2]
    if curve['entity'] == 'SPLINE':
        from ezdxf.math import BSpline
        spline = BSpline(curve['control_points'], order=curve['degree']+1, knots=curve['knots'], weights=curve.get('weights'))
        return list(spline.point(curve['knots'][curve['degree']]))[:2]
    raise ValueError('Unsupported boundary anchor: ' + curve['entity'])


def qualify_void_features(shape, features, basis):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_OUT
    from step_to_dxf import mass, vector, bounds
    u, v, n = basis
    lo, hi = bounds(shape)
    for feature in features:
        if 'diameter' not in feature or not feature['semantic_type'].endswith('_hole'):
            continue
        occupied = 0.
        for lower, upper in feature['depth_ranges']:
            center = [0., 0., 0.]
            center[u], center[v], center[n] = *feature['center'], lower
            tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*center), gp_Dir(*vector(n))), feature['diameter'] / 2, upper - lower).Shape()
            common = BRepAlgoAPI_Common(shape, tool)
            if not common.IsDone():
                occupied = float('inf')
                break
            occupied += mass(common.Shape())
        if occupied > max(1e-8, mass(shape) * 1e-8):
            feature['semantic_type'] = 'circular_region_boundary'
            feature['qualification'] = 'other material regions occupy part of this circle; not a standalone empty hole'
        elif len(feature['depth_ranges']) == 1:
            lower, upper = feature['depth_ranges'][0]
            states = []
            epsilon = min((upper - lower) / 100, max(1e-6, (hi[n] - lo[n]) * 1e-7))
            for depth, sign, outside in ((lower, -1, lo[n]), (upper, 1, hi[n])):
                if abs(depth - outside) < 1e-7:
                    states.append('outer_boundary')
                    continue
                point = [0., 0., 0.]
                point[u], point[v], point[n] = *feature['center'], depth + sign * epsilon
                state = BRepClass3d_SolidClassifier(shape, gp_Pnt(*point), 1e-8).State()
                states.append('solid_floor' if state == TopAbs_IN else 'connected_void' if state == TopAbs_OUT else 'unknown')
            feature['axial_end_states'] = states
            if 'connected_void' in states:
                feature['semantic_type'] = 'stepped_bore_segment'
            elif 'unknown' in states:
                feature['semantic_type'] = 'circular_void_boundary'
