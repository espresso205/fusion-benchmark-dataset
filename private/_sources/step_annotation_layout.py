"""Place short boundary labels in free drawing space with collision checks."""
import math
import numpy as np
from scipy.spatial import cKDTree


def format_measure(value):
    nearest = round(value)
    if abs(value - nearest) < 1e-12:
        return str(nearest)
    return f'{value:.15g}'


def boundary_labels(curves, names, height, lo, hi):
    from step_depth_regions import anchor
    samples = []
    for curve in curves:
        if curve['entity'] == 'LINE':
            a, b = curve['start'], curve['end']
            samples.extend(np.linspace(a, b, min(1000, max(2, int(math.dist(a, b) / height * 3)))))
        elif curve['entity'] in {'CIRCLE', 'ARC'}:
            start = curve.get('start_angle', 0)
            sweep = (curve['end_angle'] - start) % 360 if curve['entity'] == 'ARC' else 360
            count = min(1000, max(32, int(curve['radius'] * math.radians(sweep) / height * 3)))
            for angle in np.linspace(start, start + sweep, count):
                samples.append([curve['center'][0] + curve['radius'] * math.cos(math.radians(angle)),
                                curve['center'][1] + curve['radius'] * math.sin(math.radians(angle))])
        elif curve['entity'] == 'ELLIPSE':
            center = np.array(curve['center']); major = np.array(curve['major_axis'])
            minor = np.array([-major[1], major[0]]) * curve['ratio']
            for t in np.linspace(curve.get('start_param', 0), curve.get('end_param', 2 * math.pi), 64):
                samples.append(center + major * math.cos(t) + minor * math.sin(t))
        elif curve['entity'] == 'SPLINE':
            from ezdxf.math import BSpline
            spline = BSpline(curve['control_points'], order=curve['degree'] + 1,
                             knots=curve['knots'], weights=curve.get('weights'))
            knot_start = curve['knots'][curve['degree']]
            knot_end = curve['knots'][-curve['degree'] - 1]
            samples.extend(list(spline.point(float(t)))[:2] for t in np.linspace(knot_start, knot_end, 128))
        else:
            samples.extend(curve['points'])
    data = np.array(samples)
    tree = cKDTree(data)
    boxes, result = [], []
    for name in names:
        related = [c for c in curves if c.get('boundary_id') == name]
        points = [anchor(c) for c in related[:24]]
        width = len(name) * height * .8
        best = None
        for distance in (2, 3.5, 5, 8, 12, 18, 26):
            for point in points:
                for dx, dy in ((1, 0), (-1, 0), (1, 1), (-1, 1), (0, 1), (0, -1), (1, -1), (-1, -1)):
                    x = point[0] + dx * height * distance - (width if dx < 0 else 0)
                    y = point[1] + dy * height * distance
                    box = (x - height * .2, y - height * .2, x + width + height * .2, y + height * 1.3)
                    if box[0] < lo[0] - height * 4 or box[2] > hi[0] + height * 4 or box[1] < lo[1] - height * 4 or box[3] > hi[1] + height * 4:
                        continue
                    if any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1] for b in boxes):
                        continue
                    center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
                    nearby = data[tree.query_ball_point(center, math.hypot(box[2] - box[0], box[3] - box[1]) / 2)]
                    if len(nearby) and np.any((nearby[:, 0] >= box[0]) & (nearby[:, 0] <= box[2]) & (nearby[:, 1] >= box[1]) & (nearby[:, 1] <= box[3])):
                        continue
                    best = (point, (x, y), box)
                    break
                if best:
                    break
            if best:
                break
        if best is None:
            point = points[0]
            x, y = lo[0] - height * 10, hi[1] - len(boxes) * height * 2
            best = (point, (x, y), (x, y, x + width, y + height))
        point, position, box = best
        boxes.append(box)
        end = (min(max(point[0], position[0]), position[0] + width), position[1] + height / 2)
        result.append({'name': name, 'point': point, 'position': position, 'end': end, 'box': box})
    return result
