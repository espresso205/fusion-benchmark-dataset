"""Measured geometric annotations; no tooth standard or manufacturing inference."""
import math
import numpy as np


def measured_relations(curves, features, tolerance):
    relations = []
    holes = [f for f in features if f['semantic_type'] == 'through_hole' and 'center' in f]
    groups = []
    for hole in holes:
        found = next((g for g in groups if abs(g[0]['diameter'] - hole['diameter']) < tolerance), None)
        if found is None:
            groups.append([hole])
        else:
            found.append(hole)
    for group in groups:
        if len(group) < 3:
            continue
        points = np.array([h['center'] for h in group])
        origin = points.mean(axis=0)
        local = points - origin
        matrix = np.column_stack([2 * local, np.ones(len(local))])
        solution, _, rank, _ = np.linalg.lstsq(matrix, np.sum(local ** 2, axis=1), rcond=None)
        if rank < 3:
            continue
        center = solution[:2] + origin
        radii = np.linalg.norm(points - center, axis=1)
        angles = np.sort(np.arctan2((points - center)[:, 1], (points - center)[:, 0]))
        gaps = np.diff(np.r_[angles, angles[0] + 2 * math.pi])
        if max(radii) - min(radii) < tolerance and max(gaps) - min(gaps) < tolerance / max(radii[0], tolerance):
            relations.append({'semantic_type': 'circular_hole_pattern', 'count': len(group), 'center': center.tolist(),
                              'pitch_circle_diameter': 2 * float(radii.mean()), 'angle_spacing_degrees': 360 / len(group),
                              'feature_ids': [h['id'] for h in group], 'value_type': 'derived'})
    outer = [c for c in curves if c['layer'] == 'OUTLINE']
    if len(outer) < 20 or not all(c['entity'] == 'LINE' for c in outer):
        return relations
    # A complete edge-set correspondence avoids inferring symmetry from a bounding box.
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    coords = np.array([p for c in outer for p in (c['start'], c['end'])])
    close_pairs = np.array(list(cKDTree(coords).query_pairs(min(tolerance / 10, 1e-7))), dtype=int).reshape(-1, 2)
    adjacency = coo_matrix((np.ones(len(close_pairs)), (close_pairs[:, 0], close_pairs[:, 1])), shape=(len(coords), len(coords)))
    count, inverse = connected_components(adjacency, directed=False)
    points = np.zeros((count, 2))
    np.add.at(points, inverse, coords)
    points /= np.bincount(inverse)[:, None]
    center = points.mean(axis=0)
    tree = cKDTree(points)
    edge_set = {tuple(sorted(pair)) for pair in inverse.reshape(-1, 2)}
    for count in range(min(120, len(points)), 2, -1):
        if len(points) % count:
            continue
        angle = 2 * math.pi / count
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        distances, indices = tree.query((points - center) @ rotation.T + center)
        if distances.max() > tolerance or len(set(indices)) != len(points):
            continue
        mapped = {tuple(sorted((int(indices[a]), int(indices[b])))) for a, b in edge_set}
        if mapped == edge_set:
            relations.append({'semantic_type': 'rotational_profile_repeat', 'count': count,
                              'center': center.tolist(), 'value_type': 'derived',
                              'max_corresponding_vertex_error_mm': float(distances.max()),
                              'scope': 'outer profile only; not an assertion of gear design standard'})
            break
    return relations


def surface_notes(faces, basis):
    u, v, n = basis
    notes = ['MEASURED SURFACES; LOCAL PATCH EXTENTS, NOT FEATURE TERMINATIONS']
    def position(point):
        return ','.join(f'{x:.7g}' for x in point)
    for face in faces:
        prefix = f"F{face['id']}"
        if face['kind'] == 'cylinder':
            notes.append(f"{prefix} {'INWARD' if face['inward'] else 'OUTWARD'} CYL %%c{2 * face['radius_mm']:.9g}; SWEEP {face['angular_span_degrees']:.7g} DEG")
            notes.append(f"  AXIS ({position(face['normal'])}); POINT ({position(face['location'])})")
            notes.append(f"  {'XYZ'[n]} EXTENT {face['bounds'][0][n]:.9g} TO {face['bounds'][1][n]:.9g}")
        elif face['kind'] == 'cone':
            notes.append(f"{prefix} CONICAL PATCH; HALF ANGLE {face['semi_angle_degrees']:.9g} DEG")
            for ring in face['end_rings']:
                notes.append(f"  R {ring['radius_mm']:.9g} AT ({position(ring['center'])})")
            notes.append('  CHAMFER / COUNTERSINK INTENT UNKNOWN')
        elif face['kind'] == 'torus':
            notes.append(f"{prefix} TORUS R {face['major_radius_mm']:.9g}, r {face['minor_radius_mm']:.9g}")
            notes.append(f"  CENTER ({position(face['location'])}); AXIS ({position(face['normal'])})")
        elif face['kind'] == 'sphere':
            notes.append(f"{prefix} SPHERICAL PATCH R {face['radius_mm']:.9g}; CENTER ({position(face['location'])})")
        elif face['kind'] == 'plane':
            if abs(face['normal'][n]) > 1 - 1e-9:
                notes.append(f"{prefix} PLANAR LEVEL {'XYZ'[n]} {face['location'][n]:.9g}; AREA {face['area_mm2']:.9g}")
            elif abs(face['normal'][n]) > 1e-9:
                notes.append(f"{prefix} SLOPE NORMAL ({position(face['normal'])}); POINT ({position(face['location'])})")
        else:
            notes.append(f"{prefix} {face['surface_type']}; FREEFORM GEOMETRY NOT FULLY DIMENSIONED")
    return notes
