"""Native support surfaces, recursively preserving basis geometry in world coordinates."""
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from step_curve_semantics import coordinates, curve_definition
from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone,
                         GeomAbs_Sphere, GeomAbs_Torus,
                         GeomAbs_BSplineSurface, GeomAbs_BezierSurface,
                         GeomAbs_SurfaceOfExtrusion, GeomAbs_SurfaceOfRevolution,
                         GeomAbs_OffsetSurface)


def surface_definition(a):
    typ = a.GetType()
    rec = {'surface_type': typ.name,
           'uv_bounds': [a.FirstUParameter(), a.LastUParameter(), a.FirstVParameter(), a.LastVParameter()]}
    if typ in (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus):
        support = getattr(a, typ.name.removeprefix('GeomAbs_'))()
        frame = support.Position()
        rec.update(origin=coordinates(frame.Location()), axis=coordinates(frame.Direction()),
                   x_direction=coordinates(frame.XDirection()),
                   y_direction=coordinates(frame.YDirection()),
                   direct=frame.Direct())
    if typ == GeomAbs_Plane:
        p = a.Plane(); rec.update(origin=[p.Location().X(),p.Location().Y(),p.Location().Z()],
            axis=[p.Axis().Direction().X(),p.Axis().Direction().Y(),p.Axis().Direction().Z()])
    elif typ == GeomAbs_Cylinder:
        c = a.Cylinder(); rec.update(origin=[c.Location().X(),c.Location().Y(),c.Location().Z()],
            axis=[c.Axis().Direction().X(),c.Axis().Direction().Y(),c.Axis().Direction().Z()], radius_mm=c.Radius())
    elif typ == GeomAbs_Cone:
        c = a.Cone(); rec.update(origin=[c.Location().X(),c.Location().Y(),c.Location().Z()],
            axis=[c.Axis().Direction().X(),c.Axis().Direction().Y(),c.Axis().Direction().Z()],
            reference_radius_mm=c.RefRadius(), semi_angle_radians=c.SemiAngle())
    elif typ == GeomAbs_Sphere:
        s = a.Sphere(); rec.update(origin=[s.Location().X(),s.Location().Y(),s.Location().Z()], radius_mm=s.Radius())
    elif typ == GeomAbs_Torus:
        t = a.Torus(); rec.update(origin=[t.Location().X(),t.Location().Y(),t.Location().Z()],
            axis=[t.Axis().Direction().X(),t.Axis().Direction().Y(),t.Axis().Direction().Z()],
            major_radius_mm=t.MajorRadius(), minor_radius_mm=t.MinorRadius())
    elif typ in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface):
        s = a.BSpline() if typ == GeomAbs_BSplineSurface else a.Bezier()
        rec.update(u_degree=s.UDegree(), v_degree=s.VDegree(),
                   nb_u_poles=s.NbUPoles(), nb_v_poles=s.NbVPoles(),
                   u_rational=s.IsURational(), v_rational=s.IsVRational(),
                   u_periodic=s.IsUPeriodic(), v_periodic=s.IsVPeriodic())
        poles=[]
        for u in range(1, s.NbUPoles()+1):
            row=[]
            for v in range(1, s.NbVPoles()+1):
                p=s.Pole(u,v); row.append([p.X(),p.Y(),p.Z()])
            poles.append(row)
        rec['poles_mm']=poles
        if s.IsURational() or s.IsVRational():
            weights = []
            for u in range(1, s.NbUPoles()+1):
                row = []
                for v in range(1, s.NbVPoles()+1):
                    row.append(s.Weight(u, v))
                weights.append(row)
            rec['weights'] = weights
        if typ == GeomAbs_BSplineSurface:
            rec['u_knots']=[s.UKnot(i) for i in range(1,s.NbUKnots()+1)]
            rec['v_knots']=[s.VKnot(i) for i in range(1,s.NbVKnots()+1)]
            rec['u_multiplicities']=[s.UMultiplicity(i) for i in range(1,s.NbUKnots()+1)]
            rec['v_multiplicities']=[s.VMultiplicity(i) for i in range(1,s.NbVKnots()+1)]
    elif typ in (GeomAbs_SurfaceOfExtrusion, GeomAbs_SurfaceOfRevolution):
        rec['basis_curve'] = curve_definition(a.BasisCurve())
        if typ == GeomAbs_SurfaceOfExtrusion:
            rec['extrusion_direction'] = coordinates(a.Direction())
        else:
            axis = a.AxeOfRevolution()
            rec['revolution_axis'] = {'origin': coordinates(axis.Location()),
                                      'direction': coordinates(axis.Direction())}
    elif typ == GeomAbs_OffsetSurface:
        rec['offset_value_mm'] = a.OffsetValue()
        rec['basis_surface'] = surface_definition(a.BasisSurface())
    else:
        raise NotImplementedError(f'Native surface unsupported: {typ.name}')
    rec['support_surface_status'] = 'native_definition_serialized'
    return rec


def surface_semantics(face):
    a = BRepAdaptor_Surface(face)
    rec = {'surface_type': a.GetType().name,
           'uv_bounds': [a.FirstUParameter(), a.LastUParameter(), a.FirstVParameter(), a.LastVParameter()],
           'orientation': face.Orientation().name, 'tolerance_mm': BRep_Tool.Tolerance_s(face),
           'coordinate_space': 'world_mm; location already applied'}
    try:
        rec.update(surface_definition(a))
    except Exception as exc:
        rec.update(support_surface_status='unavailable', parameter_error=str(exc))
    return rec
