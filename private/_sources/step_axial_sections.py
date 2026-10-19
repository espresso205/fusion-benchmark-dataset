"""Certify coaxial cylindrical/conical sections with explicit end diameters."""
import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopAbs import TopAbs_IN
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt


def radial_solid(center, basis, low, high, outer, inner):
    n = basis[2]
    origin = list(center)
    origin[n] = low
    axis = gp_Ax2(gp_Pnt(*origin), gp_Dir(*[float(i == n) for i in range(3)]))
    def primitive(radii):
        if abs(radii[0] - radii[1]) < 1e-10:
            return BRepPrimAPI_MakeCylinder(axis, radii[0], high - low).Shape()
        return BRepPrimAPI_MakeCone(axis, radii[0], radii[1], high - low).Shape()
    shape = primitive(outer)
    if max(inner) > 1e-9:
        cut = BRepAlgoAPI_Cut(shape, primitive(inner))
        if not cut.IsDone():
            raise ValueError('Radial section subtraction failed')
        shape = cut.Shape()
    return shape


def certify_axial(shape, faces, basis, lo, hi, tolerance):
    from step_to_dxf import mass
    u, v, n = basis
    proof = {'passed': False, 'model_class': 'coaxial_linear_radial_sections'}
    round_faces = [f for f in faces if f['kind'] in {'cone', 'cylinder'}]
    if not round_faces or len(faces) > 150:
        return proof, []
    center = list(round_faces[0]['location'])
    center[n] = 0.
    profiles, levels = [], [lo[n], hi[n]]
    for face in faces:
        if face['kind'] == 'plane':
            if abs(face['normal'][n]) < 1 - 1e-9:
                return proof, []
            levels.append(face['location'][n])
            continue
        if face['kind'] not in {'cylinder', 'cone'} or abs(face['normal'][n]) < 1 - 1e-9:
            return proof, []
        if math.hypot(face['location'][u] - center[u], face['location'][v] - center[v]) > 1e-7:
            return proof, []
        if face['kind'] == 'cylinder':
            a, b = face['bounds'][0][n], face['bounds'][1][n]
            r0 = r1 = face['radius_mm']
        else:
            rings = sorted(face['end_rings'], key=lambda r: r['center'][n])
            a, b = [r['center'][n] for r in rings]
            r0, r1 = [r['radius_mm'] for r in rings]
        if b - a <= 1e-8:
            return proof, []
        profiles.append({'range': [a, b], 'radii': [r0, r1], 'face_id': face['id']})
        levels.extend((a, b))
    levels = sorted(levels)
    levels = [z for i, z in enumerate(levels) if i == 0 or z - levels[i - 1] > 1e-7]
    if len(levels) > 50:
        return proof, []
    sections, bodies = [], []
    def radius(profile, depth):
        a, b = profile['range']
        r0, r1 = profile['radii']
        return max(0., r0 + (depth - a) * (r1 - r0) / (b - a))
    for low, high in zip(levels, levels[1:]):
        middle = (low + high) / 2
        active = [p for p in profiles if p['range'][0] < middle < p['range'][1]]
        active.sort(key=lambda p: radius(p, middle))
        unique = [None]
        for profile in active:
            if unique[-1] is None or abs(radius(unique[-1], middle) - radius(profile, middle)) > 1e-7:
                unique.append(profile)
        for inner, outer in zip(unique, unique[1:]):
            ri, ro = 0. if inner is None else radius(inner, middle), radius(outer, middle)
            point = list(center)
            point[n] = middle
            point[u] += (ri + ro) / 2 * math.cos(.371)
            point[v] += (ri + ro) / 2 * math.sin(.371)
            if BRepClass3d_SolidClassifier(shape, gp_Pnt(*point), 1e-7).State() != TopAbs_IN:
                continue
            od = [radius(outer, z) for z in (low, high)]
            ident = [0., 0.] if inner is None else [radius(inner, z) for z in (low, high)]
            bodies.append(radial_solid(center, basis, low, high, od, ident))
            sections.append({'id': f'S{len(sections) + 1}', 'depth_range': [low, high],
                             'outer_diameters': [2 * r for r in od], 'inner_diameters': [2 * r for r in ident],
                             'source_face_ids': [outer['face_id']] + ([] if inner is None else [inner['face_id']]),
                             'semantic_type': 'coaxial_radial_section', 'value_type': 'exact'})
    if not bodies:
        return proof, []
    rebuilt = bodies[0]
    for body in bodies[1:]:
        fuse = BRepAlgoAPI_Fuse(rebuilt, body)
        if not fuse.IsDone():
            return proof, []
        rebuilt = fuse.Shape()
    if not BRepCheck_Analyzer(rebuilt).IsValid():
        return proof, []
    errors = []
    for a, b in ((shape, rebuilt), (rebuilt, shape)):
        cut = BRepAlgoAPI_Cut(a, b)
        if not cut.IsDone():
            return proof, []
        errors.append(mass(cut.Shape()))
    budget = max(1e-8, mass(shape) * 1e-8)
    proof.update(passed=all(e <= budget for e in errors), difference_volumes_mm3=errors,
                 volume_tolerance_mm3=budget, center=center, section_count=len(sections))
    return proof, sections if proof['passed'] else []
