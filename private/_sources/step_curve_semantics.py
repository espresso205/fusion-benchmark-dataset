"""Native curve definitions in world millimetres or face parameter coordinates."""
from OCP.GeomAbs import (GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse,
                         GeomAbs_Hyperbola, GeomAbs_Parabola,
                         GeomAbs_BSplineCurve, GeomAbs_BezierCurve)


def coordinates(value, dimension=3):
    return [value.Coord(i) for i in range(1, dimension + 1)]


def curve_definition(adaptor, dimension=3):
    """Keep the native parameterization; never convert or sample geometry."""
    kind = adaptor.GetType()
    uv = dimension == 2
    result = {'type': kind.name if uv else kind.name.removeprefix('GeomAbs_').lower(),
              'periodic': bool(adaptor.IsPeriodic()), 'closed': bool(adaptor.IsClosed()),
              'first_parameter': adaptor.FirstParameter(),
              'last_parameter': adaptor.LastParameter()}
    if kind == GeomAbs_Line:
        line = adaptor.Line()
        result.update({('origin_uv' if uv else 'origin'): coordinates(line.Location(), dimension),
                       ('direction_uv' if uv else 'direction'): coordinates(line.Direction(), dimension)})
    elif kind in (GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_Hyperbola, GeomAbs_Parabola):
        conic = getattr(adaptor, kind.name.removeprefix('GeomAbs_'))()
        frame = conic.Position()
        result.update({('center_uv' if uv else 'center'): coordinates(conic.Location(), dimension),
                       'x_direction': coordinates(frame.XDirection(), dimension),
                       'y_direction': coordinates(frame.YDirection(), dimension)})
        if not uv:
            result['axis'] = coordinates(frame.Direction())
        if kind == GeomAbs_Circle:
            result['radius' if uv else 'radius_mm'] = conic.Radius()
        elif kind == GeomAbs_Parabola:
            result['focal' if uv else 'focal_mm'] = conic.Focal()
        else:
            result['major_radius' if uv else 'major_radius_mm'] = conic.MajorRadius()
            result['minor_radius' if uv else 'minor_radius_mm'] = conic.MinorRadius()
    elif kind in (GeomAbs_BSplineCurve, GeomAbs_BezierCurve):
        spline = adaptor.BSpline() if kind == GeomAbs_BSplineCurve else adaptor.Bezier()
        if not uv:
            result['type'] = 'bspline' if kind == GeomAbs_BSplineCurve else 'bezier'
        result.update(degree=spline.Degree(), rational=spline.IsRational(),
                      weights=[spline.Weight(i) for i in range(1, spline.NbPoles()+1)]
                      if spline.IsRational() else None)
        result['poles_uv' if uv else 'poles_mm'] = [
            coordinates(spline.Pole(i), dimension) for i in range(1, spline.NbPoles()+1)]
        if kind == GeomAbs_BSplineCurve:
            result.update(knots=[spline.Knot(i) for i in range(1, spline.NbKnots()+1)],
                          multiplicities=[spline.Multiplicity(i) for i in range(1, spline.NbKnots()+1)])
    else:
        raise NotImplementedError(f'Native curve definition unsupported: {kind.name}')
    return result
