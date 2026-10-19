"""Independent consumer of ordinary DXF geometry, GROUP boundaries and TEXT depths.

No source STEP, feature manifest, or view contract is read by reconstruct().
"""
import argparse
import json
import math
from pathlib import Path
import re

import ezdxf
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse, BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopTools import TopTools_HSequenceOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Vec
from OCP.gp import gp_Elips
from OCP.Geom import Geom_BSplineCurve
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
from OCP.BRepLib import BRepLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid, BRepBuilderAPI_MakePolygon


def reconstruct(path):
    doc = ezdxf.readfile(path)
    if doc.units != ezdxf.units.MM:
        raise ValueError('Expected explicit millimetres')
    notes = '\n'.join(e.dxf.text for e in doc.modelspace().query('TEXT'))
    if 'AMBIGUOUS' in notes:
        raise ValueError('Drawing declares incomplete semantics')
    match = re.search(r'VIEW \+([XYZ]); U=([XYZ]), V=([XYZ])', notes)
    if not match:
        raise ValueError('Missing view basis')
    axis, horizontal, vertical = match.groups()
    n, u, v = ['XYZ'.index(a) for a in (axis, horizontal, vertical)]
    normal = [float(i == n) for i in range(3)]
    def body(entities, start, end):
        def world(point):
            pos = [0., 0., 0.]
            pos[u], pos[v], pos[n] = point[0], point[1], start
            return gp_Pnt(*pos)
        seq, wires = TopTools_HSequenceOfShape(), TopTools_HSequenceOfShape()
        for ent in entities:
            if ent.dxftype() == 'LINE':
                edge = BRepBuilderAPI_MakeEdge(world(ent.dxf.start), world(ent.dxf.end)).Edge()
            elif ent.dxftype() in {'CIRCLE', 'ARC'}:
                circle = gp_Circ(gp_Ax2(world(ent.dxf.center), gp_Dir(*normal), gp_Dir(*[float(i == u) for i in range(3)])), ent.dxf.radius)
                if ent.dxftype() == 'CIRCLE':
                    edge = BRepBuilderAPI_MakeEdge(circle).Edge()
                else:
                    a, b = map(math.radians, (ent.dxf.start_angle, ent.dxf.end_angle))
                    if b <= a:
                        b += 2 * math.pi
                    edge = BRepBuilderAPI_MakeEdge(circle, a, b).Edge()
            elif ent.dxftype() == 'ELLIPSE':
                if tuple(ent.dxf.extrusion) != (0.,0.,1.):
                    raise ValueError('Expected drawing-plane ellipse normal +Z')
                major = ent.dxf.major_axis
                direction = [0.,0.,0.]
                direction[u], direction[v] = major.x, major.y
                r = math.hypot(major.x, major.y)
                ellipse = gp_Elips(gp_Ax2(world(ent.dxf.center),gp_Dir(*normal),gp_Dir(*direction)),r,r*ent.dxf.ratio)
                a,b = ent.dxf.start_param,ent.dxf.end_param
                if b <= a:
                    b += 2*math.pi
                edge = BRepBuilderAPI_MakeEdge(ellipse,a,b).Edge()
            elif ent.dxftype() == 'SPLINE':
                if ent.dxf.flags & 2:
                    raise ValueError('Expected explicitly trimmed nonperiodic spline')
                poles = TColgp_Array1OfPnt(1,len(ent.control_points))
                for i,p in enumerate(ent.control_points,1):
                    poles.SetValue(i,world(p))
                unique,mults = [],[]
                for knot in ent.knots:
                    if unique and knot == unique[-1]:
                        mults[-1] += 1
                    else:
                        unique.append(float(knot)); mults.append(1)
                knots = TColStd_Array1OfReal(1,len(unique))
                multiplicities = TColStd_Array1OfInteger(1,len(unique))
                for i,(k,m) in enumerate(zip(unique,mults),1):
                    knots.SetValue(i,k); multiplicities.SetValue(i,m)
                if len(ent.weights):
                    if not ent.dxf.flags & 4 or len(ent.weights) != len(ent.control_points):
                        raise ValueError('Inconsistent rational spline')
                    weights = TColStd_Array1OfReal(1,len(ent.weights))
                    for i,w in enumerate(ent.weights,1):
                        weights.SetValue(i,w)
                    spline = Geom_BSplineCurve(poles,weights,knots,multiplicities,ent.dxf.degree,False)
                else:
                    spline = Geom_BSplineCurve(poles,knots,multiplicities,ent.dxf.degree,False)
                edge = BRepBuilderAPI_MakeEdge(spline).Edge()
            else:
                raise ValueError('Unsupported reconstruction entity: ' + ent.dxftype())
            seq.Append(edge)
        if not seq.Length():
            raise ValueError('Missing boundary geometry')
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(seq, 1e-7, False, wires)
        if wires.Length() == 0:
            raise ValueError('No closed wire')
        solids = []
        for index in range(1, wires.Length() + 1):
            wire = TopoDS.Wire_s(wires.Value(index))
            from OCP.BRep import BRep_Tool
            if not BRep_Tool.IsClosed_s(wire):
                raise ValueError('Open boundary')
            face = BRepBuilderAPI_MakeFace(gp_Pln(world((0, 0)), gp_Dir(*normal)), wire, True).Face()
            solids.append(BRepPrimAPI_MakePrism(face, gp_Vec(*[x * (end - start) for x in normal])).Shape())
        return solids
    if 'EXPLICIT PLANAR BREP; FACE VERTICES IN WORLD MM' in notes:
        # The table is intentionally ordinary TEXT so a DXF-only consumer can
        # inspect and reproduce the exact planar faces without source metadata.
        face_rows = re.findall(r'^FACE (\d+): (.+)$', notes, flags=re.MULTILINE)
        if not face_rows:
            raise ValueError('Missing explicit planar face table')
        sewing = BRepBuilderAPI_Sewing(1e-6)
        for _, encoded in face_rows:
            wires = []
            for loop_text in encoded.split(';'):
                points = []
                for point_text in loop_text.split('|'):
                    values = [float(v) for v in point_text.split(',')]
                    if len(values) != 3 or not all(math.isfinite(v) for v in values):
                        raise ValueError('Invalid planar face vertex')
                    points.append(gp_Pnt(*values))
                if len(points) < 3:
                    raise ValueError('Planar face has fewer than three vertices')
                polygon = BRepBuilderAPI_MakePolygon()
                for point in points:
                    polygon.Add(point)
                polygon.Close()
                wires.append(polygon.Wire())
            maker = BRepBuilderAPI_MakeFace(wires[0])
            if not maker.IsDone():
                raise ValueError('Planar face construction failed')
            for wire in wires[1:]:
                maker.Add(wire)
            sewing.Add(maker.Face())
        sewing.Perform()
        sewed = sewing.SewedShape()
        shells = [TopoDS.Shell_s(shell) for shell in _items(sewed, 'shell')]
        if not shells:
            raise ValueError('Planar face sewing produced no shell')
        solid_maker = BRepBuilderAPI_MakeSolid()
        for shell in shells:
            solid_maker.Add(shell)
        model = solid_maker.Solid()
        BRepLib.OrientClosedSolid_s(model)
    elif 'COAXIAL LINEAR RADIAL SECTIONS; NO OTHER FEATURES' in notes:
        match = re.search(r'AXIS CENTER U (\S+), V (\S+); DIRECTION \+([XYZ])', notes)
        if not match or match[3] != axis:
            raise ValueError('Missing or inconsistent rotation axis')
        center = [0., 0., 0.]
        center[u], center[v] = float(match[1]), float(match[2])
        rows = re.findall(r'RADIAL (S\d+): ([XYZ]) (\S+) TO (\S+); OD (\S+) TO (\S+); ID (\S+) TO (\S+)', notes)
        parts = []
        for _, section_axis, a, b, od0, od1, id0, id1 in rows:
            low, high, *diameters = map(float, (a, b, od0, od1, id0, id1))
            if section_axis != axis or high <= low or not all(math.isfinite(x) for x in [low, high, *diameters]):
                raise ValueError('Invalid radial section')
            center[n] = low
            placement = gp_Ax2(gp_Pnt(*center), gp_Dir(*normal))
            def radial(pair):
                r1, r2 = [d / 2 for d in pair]
                if min(r1, r2) < 0 or max(r1, r2) == 0:
                    raise ValueError('Invalid diameter')
                if abs(r1 - r2) < 1e-10:
                    return BRepPrimAPI_MakeCylinder(placement, r1, high - low).Shape()
                return BRepPrimAPI_MakeCone(placement, r1, r2, high - low).Shape()
            part = radial(diameters[:2])
            if max(diameters[2:]) > 1e-9:
                cut = BRepAlgoAPI_Cut(part, radial(diameters[2:]))
                if not cut.IsDone():
                    raise ValueError('Radial subtraction failed')
                part = cut.Shape()
            parts.append(part)
        if not parts:
            raise ValueError('Missing radial sections')
        model = parts[0]
        for part in parts[1:]:
            fuse = BRepAlgoAPI_Fuse(model, part)
            if not fuse.IsDone():
                raise ValueError('Radial union failed')
            model = fuse.Shape()
    elif 'PIECEWISE UNIFORM SECTIONS; NO OTHER FEATURES' in notes:
        rows = re.findall(r'REGION (R\d+): ([XYZ]) (\S+) TO (\S+); OUTER (B\d+); VOIDS ([B0-9,]+|NONE)', notes)
        if not rows:
            raise ValueError('Missing depth region table')
        parts = []
        for rid, row_axis, lower, upper, outer, holes in rows:
            if row_axis != axis:
                raise ValueError('Inconsistent depth axis')
            low, high = float(lower), float(upper)
            if high <= low or not math.isfinite(low + high):
                raise ValueError('Invalid depth range')
            outer_bodies = body(list(doc.groups.get(outer)), low, high)
            if len(outer_bodies) != 1:
                raise ValueError('Outer boundary must be one closed wire')
            result = outer_bodies[0]
            if holes != 'NONE':
                for hole in holes.split(','):
                    cuts = body(list(doc.groups.get(hole)), low, high)
                    if len(cuts) != 1:
                        raise ValueError('Void boundary must be one closed wire')
                    cut = BRepAlgoAPI_Cut(result, cuts[0])
                    if not cut.IsDone():
                        raise ValueError('Void subtraction failed')
                    result = cut.Shape()
            parts.append(result)
        model = parts[0]
        for part in parts[1:]:
            fuse = BRepAlgoAPI_Fuse(model, part)
            if not fuse.IsDone():
                raise ValueError('Region union failed')
            model = fuse.Shape()
    elif 'UNIFORM SECTION; ALL INNER CONTOURS THRU; NO OTHER FEATURES' in notes:
        match = re.search(r'THK [^;]+; [XYZ] (\S+) TO (\S+)', notes)
        if not match:
            raise ValueError('Missing thickness range')
        low, high = map(float, match.groups())
        outer = body(doc.modelspace().query('*[layer=="OUTLINE"]'), low, high)
        if len(outer) != 1:
            raise ValueError('Expected one outer boundary')
        model = outer[0]
        holes = doc.modelspace().query('*[layer=="HOLE"]')
        if holes:
            for hole in body(holes, low, high):
                cut = BRepAlgoAPI_Cut(model, hole)
                if not cut.IsDone():
                    raise ValueError('Hole subtraction failed')
                model = cut.Shape()
    else:
        raise ValueError('Unsupported or missing model-class constraint')
    if not BRepCheck_Analyzer(model).IsValid():
        raise ValueError('Reconstruction is not a valid B-Rep')
    return model


def _items(shape, kind):
    """Small local topology iterator used by the explicit planar branch."""
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopAbs import TopAbs_SHELL
    mapped = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_SHELL if kind == 'shell' else TopAbs_SHELL, mapped)
    return [mapped.FindKey(i) for i in range(1, mapped.Extent() + 1)]


def evaluate(dxf, source_step, output_step=None):
    from step_to_dxf import read_step, mass, bounds, items
    from OCP.TopAbs import TopAbs_SOLID
    model = reconstruct(dxf)
    source, _, _ = read_step(source_step)
    errors = []
    for a, b in ((source, model), (model, source)):
        cut = BRepAlgoAPI_Cut(a, b)
        if not cut.IsDone():
            raise ValueError('Comparison boolean failed')
        errors.append(mass(cut.Shape()))
    v1, v2 = mass(source), mass(model)
    budget = max(1e-8, v1 * 1e-8)
    intersection = max(0, v1 - errors[0])
    result = {'passed': all(e <= budget for e in errors), 'difference_volumes_mm3': errors,
              'volume_tolerance_mm3': budget, 'relative_volume_error': abs(v2 - v1) / v1,
              'solid_iou': min(1., max(0., intersection / (v1 + v2 - intersection))), 'bbox': bounds(model),
              'solid_count': len(items(model, TopAbs_SOLID)), 'alignment': 'fixed world coordinates; no fitted transform',
              'reconstruction_inputs': ['drawing.dxf only'], 'not_fusion': True}
    if output_step:
        writer = STEPControl_Writer()
        writer.Transfer(model, STEPControl_AsIs)
        if writer.Write(str(output_step)) != IFSelect_RetDone:
            raise ValueError('STEP export failed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dxf', type=Path)
    parser.add_argument('--source-step', type=Path, required=True)
    parser.add_argument('--out-step', type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.dxf, args.source_step, args.out_step), indent=2))
