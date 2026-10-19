"""Auditable STEP to single-view DXF; exact prisms, HLR for general solids."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil

import ezdxf
from step_metadata import step_header_metadata
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepLib import BRepLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools
from OCP.GeomConvert import GeomConvert
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_BSplineCurve, GeomAbs_BezierCurve, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Cone, GeomAbs_Torus, GeomAbs_Sphere
from OCP.GProp import GProp_GProps
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TColStd import TColStd_SequenceOfAsciiString
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE, TopAbs_SHELL
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec

LAYERS = ("OUTLINE", "HOLE", "HIDDEN", "CENTER", "DIM", "DATUM", "TEXT", "CONSTRUCTION")
# Right-handed basis: drawing U cross V points toward the observer (+N).
BASES = {"X": (1, 2, 0), "Y": (2, 0, 1), "Z": (0, 1, 2)}
CURVE_FIELDS = ('entity', 'start', 'end', 'center', 'radius', 'start_angle', 'end_angle',
                'major_axis', 'ratio', 'start_param', 'end_param', 'control_points',
                'degree', 'knots', 'weights', 'periodic')


def vector(axis):
    return [float(i == axis) for i in range(3)]


def xyz(point):
    return [float(point.X()), float(point.Y()), float(point.Z())]


def items(shape, kind):
    if shape.IsNull():
        return []
    mapped = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, mapped)
    return [mapped.FindKey(i) for i in range(1, mapped.Extent() + 1)]


def bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    values = list(box.Get())
    return values[:3], values[3:]


def mass(shape, surface=False):
    props = GProp_GProps()
    if surface:
        BRepGProp.SurfaceProperties_s(shape, props)
    else:
        BRepGProp.VolumeProperties_s(shape, props)
    return abs(float(props.Mass()))


def read_step(path, strict=True, return_reader=False):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError(f"Cannot read STEP: {path}")
    units = [TColStd_SequenceOfAsciiString() for _ in range(3)]
    reader.FileUnits(*units)
    source_units = [units[0].Value(i).ToCString() for i in range(1, units[0].Length() + 1)]
    reader.SetSystemLengthUnit(1.0)  # OCCT transfer target: millimetres.
    if reader.TransferRoots() == 0:
        raise ValueError("STEP has no transferable roots")
    shape = reader.OneShape()
    if shape.IsNull() or (strict and not BRepCheck_Analyzer(shape).IsValid()):
        raise ValueError("STEP does not contain a valid B-Rep")
    solids = items(shape, TopAbs_SOLID)
    if not solids or mass(shape) <= 1e-12:
        raise ValueError("STEP must contain a nonempty solid")
    result = (shape, source_units, len(solids))
    return (*result, reader) if return_reader else result



def analyze_faces(shape):
    result = []
    for face_id, raw in enumerate(items(shape, TopAbs_FACE), 1):
        face = TopoDS.Face_s(raw)
        adaptor = BRepAdaptor_Surface(face)
        record = {"id": face_id, "face": face, "surface_type": str(adaptor.GetType()), "area_mm2": mass(face, True)}
        record['uv_bounds'] = [adaptor.FirstUParameter(), adaptor.LastUParameter(), adaptor.FirstVParameter(), adaptor.LastVParameter()]
        if adaptor.GetType() == GeomAbs_Plane:
            plane = adaptor.Plane()
            record.update(kind="plane", normal=xyz(plane.Axis().Direction()), location=xyz(plane.Location()))
        elif adaptor.GetType() == GeomAbs_Cylinder:
            cylinder = adaptor.Cylinder()
            lower, upper = bounds(face)
            # Orientation distinguishes inward cylinder walls from external bosses.
            record.update(kind="cylinder", normal=xyz(cylinder.Axis().Direction()),
                          location=xyz(cylinder.Location()), radius_mm=cylinder.Radius(),
                          inward=face.Orientation() == TopAbs_REVERSED, bounds=[lower, upper])
            record['angular_span_degrees'] = math.degrees(adaptor.LastUParameter() - adaptor.FirstUParameter())
        elif adaptor.GetType() == GeomAbs_Cone:
            cone = adaptor.Cone()
            record.update(kind='cone', normal=xyz(cone.Axis().Direction()), location=xyz(cone.Location()),
                          reference_radius_mm=cone.RefRadius(), semi_angle_degrees=math.degrees(cone.SemiAngle()),
                          inward=face.Orientation() == TopAbs_REVERSED)
            origin, axis = record['location'], record['normal']
            rings = []
            for param in (adaptor.FirstVParameter(), adaptor.LastVParameter()):
                point = xyz(adaptor.Value(adaptor.FirstUParameter(), param))
                axial = sum((p - o) * d for p, o, d in zip(point, origin, axis))
                center = [o + axial * d for o, d in zip(origin, axis)]
                rings.append({'center': center, 'radius_mm': math.dist(point, center)})
            record['end_rings'] = rings
        elif adaptor.GetType() == GeomAbs_Torus:
            torus = adaptor.Torus()
            record.update(kind='torus', normal=xyz(torus.Axis().Direction()), location=xyz(torus.Location()),
                          major_radius_mm=torus.MajorRadius(), minor_radius_mm=torus.MinorRadius())
        elif adaptor.GetType() == GeomAbs_Sphere:
            sphere = adaptor.Sphere()
            record.update(kind='sphere', location=xyz(sphere.Location()), radius_mm=sphere.Radius())
        else:
            record["kind"] = "unsupported_surface"
        result.append(record)
    return result


def topology_inventory(shape):
    """Incidence audit including oriented occurrences; not a full B-Rep export."""
    from step_topology import topology_inventory as inventory
    return inventory(shape)


def planar_face_table(topology):
    """Return an explicit, readable vertex table for a planar polyhedral solid.

    This is a geometric constraint table, not a STEP/BREP payload: each record is
    a plane face expressed as its ordered world-space vertices.  It is used only
    when every source face and edge is planar/linear, so no curved geometry is
    silently approximated.
    """
    vertices = {v["vertex_id"]: v["point_mm"] for v in topology["vertices"]}
    edge_types = {e["edge_id"]: (e.get("curve_native") or {}).get("type")
                  for e in topology["edges"]}
    if any(t not in {"line", "GeomAbs_Line", None} for t in edge_types.values()):
        return []
    records = []
    for face in topology["faces"]:
        if face.get("surface", {}).get("surface_type") != "GeomAbs_Plane":
            return []
        loops = []
        for loop in face["loops"]:
            points = [vertices[u["start_vertex_id"]]
                      for u in loop["ordered_edge_uses"]]
            if len(points) < 3:
                return []
            loops.append(points)
        if not loops:
            return []
        records.append({"face_id": face["face_id"], "loops": loops,
                        "orientation": face.get("surface", {}).get("orientation")})
    return records


def has_loose_geometry(shape):
    solids = items(shape, TopAbs_SOLID)
    for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX):
        owned = TopTools_IndexedMapOfShape()
        for solid in solids:
            TopExp.MapShapes_s(solid, kind, owned)
        if any(not owned.Contains(part) for part in items(shape, kind)):
            return True
    return False


def certify_prism(shape, faces, basis, lo, hi, solid_count, tolerance):
    """Prove equivalence within tolerance by two boolean volume differences.

    This certificate applies only to the declared uniform-section model class;
    one projection alone cannot exclude arbitrary unannotated internal cavities.
    """
    u, v, n = basis
    result = {"passed": False, "model_class": "uniform_section_prism", "tolerance_mm": tolerance}
    if solid_count != 1:
        return result, None
    # A uniform extrusion cannot have a sloped planar wall or a transverse cylinder.
    if any((f['kind'] == 'plane' and 1e-9 < abs(f['normal'][n]) < 1 - 1e-9)
           or (f['kind'] == 'cylinder' and abs(f['normal'][n]) < 1 - 1e-9) for f in faces):
        return result, None
    caps = [f for f in faces if f["kind"] == "plane" and abs(f["normal"][n]) > 1 - 1e-9
            and abs(f["location"][n] - lo[n]) < tolerance]
    if len(caps) != 1:
        return result, None
    cap = caps[0]
    if not all(BRep_Tool.IsClosed_s(TopoDS.Wire_s(w)) for w in items(cap["face"], TopAbs_WIRE)):
        return result, None
    try:
        face = TopoDS.Face_s(cap["face"].Oriented(TopAbs_FORWARD))
        normal = cap["normal"][n]
        if normal < 0:
            face.Reverse()
        prism = BRepPrimAPI_MakePrism(face, gp_Vec(*[d * (hi[n] - lo[n]) for d in vector(n)]), True).Shape()
        if not BRepCheck_Analyzer(prism).IsValid():
            return result, None
        errors = []
        for a, b in ((shape, prism), (prism, shape)):
            cut = BRepAlgoAPI_Cut(a, b)
            if not cut.IsDone():
                return result, None
            errors.append(mass(cut.Shape()))
        budget = max(1e-8, mass(shape) * 1e-8)
        result.update(difference_volumes_mm3=errors, volume_tolerance_mm3=budget,
                      passed=all(error <= budget for error in errors), source_face_id=cap["id"])
        return result, cap if result["passed"] else None
    except Exception as exc:
        result["error"] = str(exc)
        return result, None


def select_view(shape, faces, lo, hi, count, tolerance, forced=None):
    from step_depth_regions import certify_regions
    from step_axial_sections import certify_axial
    candidates = []
    for name, basis in BASES.items():
        u, v, n = basis
        projected_area = max((hi[u] - lo[u]) * (hi[v] - lo[v]), tolerance ** 2)
        plane_area = sum(f["area_mm2"] for f in faces if f["kind"] == "plane" and abs(f["normal"][n]) > .999)
        aligned = sum(f["kind"] == "cylinder" and f["inward"] and abs(f["normal"][n]) > .999 for f in faces)
        unaligned = sum(f["kind"] == "cylinder" and abs(f["normal"][n]) <= .999 for f in faces)
        certificate, cap = certify_prism(shape, faces, basis, lo, hi, count, tolerance)
        regions = []
        axial_sections = []
        if not cap and count == 1 and (forced is None or forced == name):
            region_proof, regions = certify_regions(shape, faces, basis, lo, hi, tolerance)
            if regions:
                certificate = region_proof
            else:
                certificate['depth_region_attempt'] = region_proof
        if not cap and not regions and count == 1 and (forced is None or forced == name):
            radial_proof, axial_sections = certify_axial(shape, faces, basis, lo, hi, tolerance)
            if axial_sections:
                certificate = radial_proof
        all_projected_area = sum((hi[a] - lo[a]) * (hi[b] - lo[b]) for a, b in ((0, 1), (1, 2), (2, 0)))
        cylinder_count = max(1, sum(f['kind'] == 'cylinder' for f in faces))
        components = {"major_plane_area": plane_area / projected_area,
                      "projected_extent": projected_area / all_projected_area * 20, "aligned_holes": aligned * 4 / cylinder_count,
                      "unaligned_cylinders": -unaligned * 2 / cylinder_count, "certified_prism": 100 if cap else 80 if regions or axial_sections else 0}
        candidates.append({"axis": name, "score": sum(components.values()), "score_components": components,
                           "certificate": certificate, "cap": cap, "regions": regions, 'axial_sections': axial_sections})
    chosen = next(c for c in candidates if c["axis"] == forced) if forced else max(candidates, key=lambda c: c["score"])
    return chosen, [{k: v for k, v in c.items() if k not in {"cap", "regions", 'axial_sections'}} for c in candidates]


def curve_geometry(edge, basis, tolerance):
    u, v, n = basis
    edge = TopoDS.Edge_s(edge)
    if BRep_Tool.Degenerated_s(edge):
        return None
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    def project(p):
        coord = xyz(p)
        return [coord[u], coord[v]]
    a, b = project(curve.Value(first)), project(curve.Value(last))
    if curve.GetType() == GeomAbs_Line:
        return {"entity": "LINE", "start": a, "end": b, "value_type": "exact"} if a != b else None
    if curve.GetType() == GeomAbs_Circle and abs(xyz(curve.Circle().Axis().Direction())[n]) > 1 - 1e-9:
        circle = curve.Circle()
        center = project(circle.Location())
        data = {"center": center, "radius": circle.Radius(), "value_type": "exact"}
        if abs(last - first - 2 * math.pi) < 1e-8:
            return {**data, "entity": "CIRCLE"}
        if xyz(circle.Axis().Direction())[n] < 0:
            a, b = b, a
        angles = [math.degrees(math.atan2(p[1] - center[1], p[0] - center[0])) % 360 for p in (a, b)]
        return {**data, "entity": "ARC", "start_angle": angles[0], "end_angle": angles[1]}
    if curve.GetType() == GeomAbs_Ellipse and abs(xyz(curve.Ellipse().Axis().Direction())[n]) > 1 - 1e-9:
        ellipse = curve.Ellipse()
        center = project(ellipse.Location())
        major = ellipse.MajorRadius()
        minor = ellipse.MinorRadius()
        xdir = xyz(ellipse.XAxis().Direction())
        major_vec = [xdir[u] * major, xdir[v] * major]
        first, last = curve.FirstParameter(), curve.LastParameter()
        if xyz(ellipse.Axis().Direction())[n] < 0:
            first, last = -last, -first
        # DXF ELLIPSE uses parametric angles in the ellipse's local axes.
        return {"entity": "ELLIPSE", "center": center, "major_axis": major_vec,
                "ratio": minor / major, "start_param": first, "end_param": last,
                "value_type": "exact"}
    # Preserve general analytic/NURBS edges as native DXF SPLINE.  Sampling
    # into a polyline silently changes geometry, so it is forbidden here.
    try:
        if curve.GetType() == GeomAbs_BSplineCurve:
            bs = curve.BSpline()
        elif curve.GetType() == GeomAbs_BezierCurve:
            bs = GeomConvert.CurveToBSplineCurve_s(curve.Bezier())
        else:
            bs = None
        if bs is not None:
            # These adaptor accessors include the edge's placement. Segment a
            # private copy so trimming never mutates geometry shared by edges.
            bs = bs.Copy()
            bs.Segment(first, last)
            if bs.IsPeriodic():
                bs.SetNotPeriodic()
            poles = [project(bs.Pole(i)) for i in range(1, bs.NbPoles() + 1)]
            knots = []
            for i in range(1, bs.NbKnots() + 1):
                knots.extend([bs.Knot(i)] * bs.Multiplicity(i))
            weights = [bs.Weight(i) for i in range(1, bs.NbPoles() + 1)] if bs.IsRational() else None
            if len(poles) >= 2:
                return {"entity": "SPLINE", "control_points": poles, "degree": bs.Degree(),
                        "knots": knots, "weights": weights, "value_type": "exact",
                        "periodic": bool(bs.IsPeriodic())}
    except Exception as exc:
        raise RuntimeError(f"Exact spline extraction failed: {exc}") from exc
    raise RuntimeError(f"Unsupported projected curve type {curve.GetType()}; refusing polyline approximation")


def is_capsule(group, tolerance):
    if Counter(g["entity"] for g in group) != {"LINE": 2, "ARC": 2}:
        return False
    arcs = [g for g in group if g["entity"] == "ARC"]
    a, b = [arc["center"] for arc in arcs]
    length = math.dist(a, b)
    radius = arcs[0]["radius"]
    if length <= tolerance or abs(radius - arcs[1]["radius"]) > tolerance:
        return False
    tangent = [(b[i] - a[i]) / length for i in range(2)]
    normal = [-tangent[1], tangent[0]]
    expected = [[[c[i] + sign * radius * normal[i] for i in range(2)] for c in (a, b)] for sign in (-1, 1)]
    for line in (g for g in group if g["entity"] == "LINE"):
        endpoints = [line["start"], line["end"]]
        if not any(min(max(math.dist(p, q) for p, q in zip(endpoints, pair)),
                       max(math.dist(p, q) for p, q in zip(endpoints, reversed(pair)))) <= tolerance for pair in expected):
            return False
    for index, arc in enumerate(arcs):
        sweep = (arc["end_angle"] - arc["start_angle"]) % 360
        if abs(sweep - 180) > 1e-6:
            return False
        middle = math.radians(arc["start_angle"] + sweep / 2)
        outward = math.cos(middle) * tangent[0] + math.sin(middle) * tangent[1]
        if outward * (-1 if index == 0 else 1) < 1 - 1e-6:
            return False
    return True


def profile_geometry(cap, basis, tolerance, source_edge_map=None):
    curves, features = [], []
    outer = BRepTools.OuterWire_s(cap["face"])
    for index, raw in enumerate(items(cap["face"], TopAbs_WIRE), 1):
        wire = TopoDS.Wire_s(raw)
        is_outer = wire.IsSame(outer)
        group = []
        for edge in items(wire, TopAbs_EDGE):
            geom = curve_geometry(edge, basis, tolerance)
            if geom:
                geom.update(layer="OUTLINE" if is_outer else "HOLE", source_face_id=cap["id"],
                            source="BRep_cap_wire", feature_id=f"profile_{index}")
                if source_edge_map is not None:
                    source_edge_id = source_edge_map.FindIndex(edge)
                    if not source_edge_id:
                        raise RuntimeError('Certified cap edge missing from source topology')
                    geom['source_edge_ids'] = [source_edge_id]
                group.append(geom)
        kind = "outer_profile" if is_outer else "through_cutout"
        feature = {"id": f"profile_{index}", "semantic_type": kind, "source_face_id": cap["id"],
                   "value_type": "exact", "confidence": 1.0, "closed": BRep_Tool.IsClosed_s(wire)}
        if not is_outer and len(group) == 1 and group[0]["entity"] == "CIRCLE":
            feature.update(semantic_type="through_hole", center=group[0]["center"], diameter=2 * group[0]["radius"])
        elif not is_outer and is_capsule(group, tolerance):
            arcs = [g for g in group if g["entity"] == "ARC"]
            feature.update(semantic_type="through_slot", end_centers=[a["center"] for a in arcs],
                           width=2 * arcs[0]["radius"], center_distance=math.dist(arcs[0]["center"], arcs[1]["center"]))
        features.append(feature)
        curves.extend(group)
    return curves, features


def hlr_geometry(shape, basis, tolerance):
    u, v, n = basis
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*vector(n)), gp_Dir(*vector(u)))))
    algo.Update()
    algo.Hide()
    data = algo.DataStructure()
    input_edge_count = int(data.NbEdges())
    input_face_count = int(data.NbFaces())
    output = HLRBRep_HLRToShape(algo)
    curves = []
    for method, layer in (("VCompound", "OUTLINE"), ("OutLineVCompound", "OUTLINE"),
                          ("HCompound", "HIDDEN"), ("OutLineHCompound", "HIDDEN")):
        compound = getattr(output, method)()
        if compound.IsNull():
            continue
        BRepLib.BuildCurves3d_s(compound)
        for edge in items(compound, TopAbs_EDGE):
            # HLR emits a new XY drawing-space shape regardless of source axis.
            geom = curve_geometry(edge, BASES["Z"], tolerance)
            if geom:
                geom.update(layer=layer, source="HLRBRep_Algo", source_face_id=None, source_face_ids=[],
                            source_edge_ids=[], hlr_input_edge_count=input_edge_count,
                            hlr_input_face_count=input_face_count,
                            provenance_note="HLR projection edge; OCC output has no stable source occurrence mapping")
                curves.append(geom)
    return curves


def unique_curves(curves):
    result, seen = [], set()
    for curve in curves:
        fields = {k: curve[k] for k in (*CURVE_FIELDS, 'layer') if k in curve}
        if curve["entity"] == "LINE":
            fields["ends"] = sorted([curve["start"], curve["end"]])
            fields.pop('start'); fields.pop('end')
        key = json.dumps(fields, sort_keys=True)
        if key not in seen:
            result.append(curve)
            seen.add(key)
    return result


def draw_geometry(msp, curve):
    attrs = {"layer": curve["layer"]}
    kind = curve["entity"]
    if kind == "LINE":
        return msp.add_line(curve["start"], curve["end"], dxfattribs=attrs)
    if kind == "CIRCLE":
        return msp.add_circle(curve["center"], curve["radius"], dxfattribs=attrs)
    if kind == "ARC":
        return msp.add_arc(curve["center"], curve["radius"], curve["start_angle"], curve["end_angle"], dxfattribs=attrs)
    if kind == "ELLIPSE":
        return msp.add_ellipse(curve["center"], curve["major_axis"], curve["ratio"],
                               curve["start_param"], curve["end_param"], dxfattribs=attrs)
    if kind == "SPLINE":
        spline = msp.add_spline(dxfattribs=attrs)
        spline.dxf.degree = curve["degree"]
        spline.control_points = [(x, y, 0.0) for x, y in curve["control_points"]]
        spline.knots = curve["knots"]
        if curve.get("weights") is not None:
            spline.weights = curve["weights"]
            spline.dxf.flags |= 4
        if curve.get("periodic"):
            spline.dxf.flags = spline.dxf.get("flags", 0) | 2
        return spline
    raise ValueError(f"Unsupported DXF geometry entity {kind}")


def patterns(features, tolerance):
    holes = [f for f in features if f["semantic_type"] == "through_hole"]
    result = []
    for direction in (0, 1):
        remaining = list(holes)
        while remaining:
            seed = remaining.pop(0)
            group = [seed] + [h for h in remaining if abs(h["diameter"] - seed["diameter"]) < tolerance
                             and abs(h["center"][1 - direction] - seed["center"][1 - direction]) < tolerance]
            remaining = [h for h in remaining if h not in group]
            group.sort(key=lambda h: h["center"][direction])
            gaps = [b["center"][direction] - a["center"][direction] for a, b in zip(group, group[1:])]
            if len(group) >= 3 and max(gaps) - min(gaps) < tolerance:
                result.append({"semantic_type": "linear_hole_pattern", "feature_ids": [h["id"] for h in group],
                               "direction": "UV"[direction], "count": len(group), "spacing": gaps[0], "value_type": "derived"})
    return result


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def generate(input_step: Path, out: Path, mode="standard", view=None, license_file=None, tolerance=0.001, allow_invalid=False):
    input_step, out = Path(input_step).resolve(), Path(out).resolve()
    if mode not in {"minimal", "standard", "rich"} or (view is not None and view not in BASES):
        raise ValueError("Invalid mode or view")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Tolerance must be positive and finite")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Output directory must be empty: {out}")
    license_text = Path(license_file).read_text(encoding="utf-8") if license_file else None
    shape, source_units, solid_count, step_reader = read_step(input_step, strict=not allow_invalid, return_reader=True)
    input_brep_valid = BRepCheck_Analyzer(shape).IsValid()
    step_metadata = step_header_metadata(input_step)
    lo, hi = bounds(shape)
    faces = analyze_faces(shape)
    topology = topology_inventory(shape)
    from step_bindings import bind_step_metadata
    step_bindings = bind_step_metadata(input_step, step_reader, shape, step_metadata)
    # Boolean view analysis can attach helper pcurves to shared source edges.
    # Freeze and verify the imported baseline before any projection operations.
    out.mkdir(parents=True, exist_ok=True)
    from brep_roundtrip import export_brep_package
    brep_report = export_brep_package(shape, out, topology=topology, metadata=step_metadata, bindings=step_bindings)
    polyhedral_faces = planar_face_table(topology) if solid_count == 1 else []
    surface_counts = Counter(f["kind"] for f in faces)
    unsupported_faces = [f["id"] for f in faces if f["kind"] == "unsupported_surface"]
    loose_geometry = has_loose_geometry(shape)
    chosen, candidates = select_view(shape, faces, lo, hi, 0 if loose_geometry else solid_count, tolerance, view)
    basis = BASES[chosen["axis"]]
    u, v, n = basis
    cap = chosen["cap"]
    depth_regions = []
    axial_sections = chosen['axial_sections']
    if cap:
        source_edge_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, source_edge_map)
        curves, features = profile_geometry(cap, basis, tolerance, source_edge_map)
        geometry_method = "certified_cap_wires"
    elif chosen['regions']:
        from step_depth_regions import region_geometry, qualify_void_features
        curves, features, depth_regions = region_geometry(chosen['regions'], basis, tolerance, [lo[n], hi[n]])
        qualify_void_features(shape, features, basis)
        geometry_method = 'certified_depth_regions'
    else:
        curves, features = hlr_geometry(shape, basis, tolerance), []
        geometry_method = "HLRBRep_Algo_exact_conics"
        if axial_sections:
            features = axial_sections
            geometry_method = 'certified_axial_sections_with_HLR'
        elif polyhedral_faces and all(f["kind"] == "plane" for f in faces):
            geometry_method = "explicit_planar_brep_with_HLR"
    if not depth_regions:
        curves = unique_curves(curves)
    if not curves or not any(c["layer"] == "OUTLINE" for c in curves):
        raise RuntimeError("No projected outline generated")
    flags = []
    complete_geometry = bool(cap or depth_regions or axial_sections or polyhedral_faces)
    if not complete_geometry:
        flags.append("unsupported_nonuniform_or_internal_geometry")
    if solid_count != 1 and not polyhedral_faces:
        flags.append("multiple_solids")
    if loose_geometry:
        flags.append("non_solid_geometry")
    if any(c["entity"] == "LWPOLYLINE" for c in curves):
        flags.append("approximated_curve")
    if unsupported_faces and not complete_geometry:
        flags.append("unsupported_surface_types")
    if mode == "minimal":
        flags.append("depth_annotations_omitted")
    status = "single_view_ambiguous" if flags else "solvable"
    contract = {"schema_version": 1, "view_direction": vector(n), "view_direction_convention": "toward_observer",
                "camera_look_direction": [-x for x in vector(n)], "up_direction": vector(v), "right_direction": vector(u),
                "sketch_plane": "XYZ"[u] + "XYZ"[v], "extrusion_axis": "XYZ"[n], "unit": "mm", "origin": [0, 0, 0],
                "sketch_depth": lo[n], "depth_range": [lo[n], hi[n]], "allow_translation": False,
                "allow_rotation": False, "allow_mirror": False, "allow_scale": False,
                "world_mapping": "world = U * drawing_x + V * drawing_y + N * depth",
                "model_class": ("explicit_planar_brep" if polyhedral_faces and geometry_method == "explicit_planar_brep_with_HLR"
                                else chosen['certificate']['model_class'] if complete_geometry else "unrestricted_unknown"), "mode": mode}
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$MEASUREMENT"] = 1
    doc.header["$INSBASE"] = (0, 0, 0)
    if 'HIDDEN' not in doc.linetypes:
        dash = max(hi[u] - lo[u], hi[v] - lo[v]) / 120
        doc.linetypes.new('HIDDEN', dxfattribs={'description': 'Hidden edges', 'pattern': [3 * dash, 2 * dash, -dash]})
    for layer in LAYERS:
        attrs = {"color": {"HIDDEN": 8, "CENTER": 4, "DIM": 3, "DATUM": 2}.get(layer, 7)}
        if layer in {"HIDDEN", "CENTER"}:
            attrs["linetype"] = "HIDDEN" if layer == "HIDDEN" else "CENTER"
        doc.layers.new(layer, dxfattribs=attrs)
    msp = doc.modelspace()
    doc.appids.new('STEP_SOURCE')
    entities = []
    boundary_entities = {}
    for curve in curves:
        entity = draw_geometry(msp, curve)
        if curve.get('source_edge_ids'):
            tags = [(1000, 'transfer_local_topology_v1'), (1000, 'source_face_id'),
                    (1071, curve['source_face_id'])]
            for edge_id in curve['source_edge_ids']:
                tags.extend([(1000, 'source_edge_id'), (1071, edge_id)])
            entity.set_xdata('STEP_SOURCE', tags)
        entities.append({**curve, "handle": entity.dxf.handle, "semantic_type": "profile_edge" if cap else "projection_edge"})
        if 'boundary_id' in curve:
            boundary_entities.setdefault(curve['boundary_id'], []).append(entity)
    for name, members in boundary_entities.items():
        doc.groups.new(name=name, description='Depth region boundary; visible label and table in TEXT').set_data(members)
    span = max(hi[u] - lo[u], hi[v] - lo[v])
    text_height = max(span * .018, .15)
    margin = text_height * 5
    notes = ["UNITS mm", f"VIEW +{'XYZ'[n]}; U={'XYZ'[u]}, V={'XYZ'[v]}; ORIGIN (0,0,0)",
             "WORLD COORDINATES; NO SHIFT / ROTATION / MIRROR / SCALE"]
    if mode != "minimal":
        from step_annotation_layout import format_measure as fmt
        notes.append(f"{'THK' if cap else 'AXIAL EXTENT'} {fmt(hi[n] - lo[n])}; {'XYZ'[n]} {fmt(lo[n])} TO {fmt(hi[n])}")
        notes.append("UNIFORM SECTION; ALL INNER CONTOURS THRU; NO OTHER FEATURES" if cap else
                     "PIECEWISE UNIFORM SECTIONS; NO OTHER FEATURES" if depth_regions else
                     'COAXIAL LINEAR RADIAL SECTIONS; NO OTHER FEATURES' if axial_sections else "INCOMPLETE DEPTH SEMANTICS; SINGLE VIEW AMBIGUOUS")
        if polyhedral_faces and geometry_method == "explicit_planar_brep_with_HLR":
            notes.append("EXPLICIT PLANAR BREP; FACE VERTICES IN WORLD MM; NO CURVE APPROXIMATION")
            for record in polyhedral_faces:
                encoded = []
                for loop in record["loops"]:
                    encoded.append("|".join(
                        ",".join(f"{float(x):.15g}" for x in point) for point in loop))
                notes.append(f"FACE {record['face_id']}: " + ";".join(encoded))
    if flags and complete_geometry:
        notes.append("SINGLE VIEW AMBIGUOUS: " + ", ".join(flags))
    from step_rich_annotations import measured_relations, surface_notes
    relation_list = patterns(features, tolerance) + measured_relations(curves, features, tolerance)
    if axial_sections and mode != 'minimal':
        center = chosen['certificate']['center']
        notes.append(f"AXIS CENTER U {center[u]:.15g}, V {center[v]:.15g}; DIRECTION +{'XYZ'[n]}")
        notes.append('OD / ID ARE END DIAMETERS; LINEAR TAPER BETWEEN ENDS')
        for section in axial_sections:
            a, b = section['depth_range']
            od, ident = section['outer_diameters'], section['inner_diameters']
            notes.append(f"RADIAL {section['id']}: {'XYZ'[n]} {fmt(a)} TO {fmt(b)}; OD {fmt(od[0])} TO {fmt(od[1])}; ID {fmt(ident[0])} TO {fmt(ident[1])}")
        for a, b in (((lo[u] - text_height, center[v]), (hi[u] + text_height, center[v])),
                     ((center[u], lo[v] - text_height), (center[u], hi[v] + text_height))):
            e = msp.add_line(a, b, dxfattribs={'layer': 'CENTER'})
            entities.append({'handle': e.dxf.handle, 'entity': 'LINE', 'layer': 'CENTER', 'semantic_type': 'rotation_axis', 'value_type': 'derived'})
    if depth_regions and mode != 'minimal':
        from step_annotation_layout import boundary_labels
        notes.append('DEPTH REGIONS: OUTER BOUNDARY / VOID BOUNDARIES')
        for region in depth_regions:
            a, b = region['depth_range']
            notes.append(f"REGION {region['id']}: {'XYZ'[n]} {fmt(a)} TO {fmt(b)}; OUTER {region['outer']}; VOIDS {','.join(region['voids']) or 'NONE'}")
        for placement in boundary_labels(curves, boundary_entities, text_height * .7, [lo[u], lo[v]], [hi[u], hi[v]]):
            name = placement['name']
            line = msp.add_line(placement['point'], placement['end'], dxfattribs={'layer': 'DATUM'})
            label = msp.add_text(name, dxfattribs={'layer': 'DATUM', 'height': text_height * .7})
            label.set_placement(placement['position'])
            for e in (line, label):
                entities.append({'handle': e.dxf.handle, 'entity': e.dxftype(), 'layer': 'DATUM',
                                 'boundary_id': name, 'semantic_type': 'boundary_identifier', 'value_type': 'synthetic'})
        for feature in features:
            if 'diameter' in feature:
                x, y = feature['center']
                notes.append(f"{feature['id']}: {feature['semantic_type'].upper().replace('_', ' ')}; U {x:.9g}, V {y:.9g}; %%c{feature['diameter']:.9g}")
    for feature in features:
        if depth_regions or axial_sections:
            continue
        if feature["semantic_type"].startswith("through_"):
            feature.update(axis=vector(n), depth=hi[n] - lo[n], depth_type="exact", termination="through")
        if mode == "minimal":
            continue
        if feature["semantic_type"] == "through_hole":
            x, y = feature["center"]
            radius = feature["diameter"] / 2
            notes.append(f"HOLE {feature['id']}: U {x:.15g}, V {y:.15g}; THRU %%c{2 * radius:.15g}")
            for start, end in (((x - radius - text_height, y), (x + radius + text_height, y)),
                               ((x, y - radius - text_height), (x, y + radius + text_height))):
                e = msp.add_line(start, end, dxfattribs={"layer": "CENTER"})
                entities.append({"handle": e.dxf.handle, "entity": "LINE", "layer": "CENTER", "semantic_type": "hole_centerline",
                                 "feature_id": feature["id"], "source_face_id": cap["id"], "value_type": "derived"})
        elif feature["semantic_type"] == "through_slot":
            a, b = feature["end_centers"]
            notes.append(f"SLOT {feature['id']}: WIDTH {feature['width']:.15g}, CENTERS {feature['center_distance']:.15g}; THRU")
            e = msp.add_line(a, b, dxfattribs={"layer": "CENTER"})
            entities.append({"handle": e.dxf.handle, "entity": "LINE", "layer": "CENTER", "semantic_type": "slot_centerline",
                             "source_face_id": cap["id"], "value_type": "derived"})
    if mode == "rich":
        for relation in relation_list:
            if relation['semantic_type'] == 'linear_hole_pattern':
                notes.append(f"{relation['count']}X HOLES EQ SP {relation['spacing']:.15g} ALONG {relation['direction']}")
            elif relation['semantic_type'] == 'circular_hole_pattern':
                notes.append(f"{relation['count']}X HOLES ON PCD {relation['pitch_circle_diameter']:.9g}; EQ ANGLE {relation['angle_spacing_degrees']:.9g} DEG")
                notes.append(f"PCD CENTER U {relation['center'][0]:.9g}, V {relation['center'][1]:.9g}")
            elif relation['semantic_type'] == 'rotational_profile_repeat':
                notes.append(f"PROFILE REPEAT {relation['count']}X; ANGLE {360 / relation['count']:.9g} DEG; CENTER U {relation['center'][0]:.9g}, V {relation['center'][1]:.9g}")
        notes.append("MATERIAL / TOLERANCES / THREAD / PROCESS: UNKNOWN")
        if not complete_geometry:
            notes.extend(surface_notes(faces, basis))
        for item in step_metadata.get('materials', []):
            notes.append(f"MATERIAL: {item.get('name') or '$'} [STEP #{item['entity_id']}]")
        for item in step_metadata.get('colours', []):
            rgb = ','.join(f"{float(v):.6g}" for v in item['rgb'])
            notes.append(f"COLOUR RGB: {rgb} [STEP #{item['entity_id']}]")
        for item in step_metadata.get('uncertainty_measures', []):
            notes.append(f"MODEL UNCERTAINTY: {item['value']:.15g} [STEP #{item['entity_id']}; UNIT #{item['unit_entity_id']}]")
        for item in step_metadata.get('assembly_occurrences', []):
            notes.append(f"ASSEMBLY OCCURRENCE {item['id']}: {item['name']} [STEP #{item['entity_id']}; PARENT #{item['relating_product_definition_id']} CHILD #{item['related_product_definition_id']}]")
        for item in step_metadata.get('presentation_layers', []):
            notes.append(f"PRESENTATION LAYER: {item.get('name') or '$'}; ENTITIES {','.join('#'+str(x) for x in item['assigned_entity_ids'])} [STEP #{item['entity_id']}]")
        # Preserve PMI that DXF R12/AC1027 cannot represent faithfully.  Emit
        # a compact, auditable note keyed by the original STEP entity; the
        # complete arguments remain in feature_manifest.step_metadata.
        for item in step_metadata.get('pmi_entities', []):
            refs = ','.join('#' + str(x) for x in item.get('references', []))
            suffix = f"; REFS {refs}" if refs else ''
            notes.append(f"PMI {item['entity_type']} [STEP #{item['entity_id']}{suffix}]: PRESERVED RAW; SCHEMA RESOLUTION REQUIRED")
    rows_per_column = max(24, min(44, int((hi[v] - lo[v]) / (text_height * 2))))
    column_x, previous_column = hi[u] + margin, -1
    for index, note in enumerate(notes):
        column, row = divmod(index, rows_per_column)
        if column > previous_column:
            if previous_column >= 0:
                previous_notes = notes[(column - 1) * rows_per_column: column * rows_per_column]
                column_x += max(len(text) for text in previous_notes) * text_height * .85 + margin
            previous_column = column
        e = msp.add_text(note, dxfattribs={"layer": "TEXT", "height": text_height})
        e.set_placement((column_x, hi[v] - row * text_height * 2))
        entities.append({"handle": e.dxf.handle, "entity": "TEXT", "layer": "TEXT", "text": note,
                         "semantic_type": "engineering_note", "source_face_id": None, "value_type": "derived"})
    override = {"dimtxt": text_height, "dimasz": text_height, "dimexo": text_height * .4,
                "dimgap": text_height * .4, "dimdec": 6, "dimtad": 1,
                "dimlfac": 1.0, "dimscale": 1.0, "dimblk": "", "dimlunit": 2}
    for p1, p2, base, angle in (((lo[u], lo[v]), (hi[u], lo[v]), (lo[u], lo[v] - margin), 0),
                               ((lo[u], lo[v]), (lo[u], hi[v]), (lo[u] - margin, lo[v]), 90)):
        dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, override=override, dxfattribs={"layer": "DIM"})
        dim.render()
        entities.append({"handle": dim.dimension.dxf.handle, "entity": "DIMENSION", "layer": "DIM", "value_type": "exact",
                         "source_face_id": None, "semantic_type": "overall_extent", "measurement_mm": math.dist(p1, p2)})
    covered = {"outer_profile": complete_geometry, "holes": complete_geometry, "slots": complete_geometry,
               "thickness": complete_geometry and mode != "minimal", "hidden_features": complete_geometry,
               "depth_regions": bool(depth_regions) and mode != 'minimal'}
    if allow_invalid and not input_brep_valid:
        status = "invalid_brep_projection_only"
        flags = list(flags) + ["input_brep_failed_occt_validation"]
    manifest = {"schema_version": 3, "status": status, "ambiguity_flags": flags,
                "solvability_scope": "Within declared model class and numeric tolerances; not a universal uniqueness proof",
                "feature_coverage": covered, "feature_counts": dict(Counter(f["semantic_type"] for f in features)),
                "features": features, "relations": relation_list, "entities": entities,
                "depth_regions": depth_regions,
                "polyhedral_faces": polyhedral_faces,
                'axial_sections': axial_sections,
                'step_metadata': step_metadata,
                'step_bindings': step_bindings,
                "source_face_id_scheme": "1-based traversal of unique faces in imported shape; local to this STEP transfer",
                "surface_counts": dict(surface_counts),
                "topology_inventory": topology,
                "unsupported_source_face_ids": unsupported_faces,
                "semantic_completeness": {"all_source_faces_classified": not unsupported_faces,
                                           "all_face_loops_traversed": topology['all_face_loops_traversed'],
                                           "complete_brep_serialization": False,
                                           "input_brep_valid": bool(input_brep_valid),
                                           "input_brep_validation": topology.get('validation', {}),
                                           "topology_geometry_coverage": {
                                               "edges_with_native_curve": sum(bool(e.get('curve_native')) for e in topology['edges']),
                                               "edges_total": len(topology['edges']),
                                               "face_uses_with_pcurve": sum(bool(u.get('pcurve')) for f in topology['faces'] for l in f['loops'] for u in l['ordered_edge_uses']),
                                               "face_uses_total": sum(len(l['ordered_edge_uses']) for f in topology['faces'] for l in f['loops'])},
                                           "geometry_exact": not any(c.get("value_type") not in (None, "exact") for c in curves),
                                           "projection_exact": (None if geometry_method in {"HLRBRep_Algo_exact_conics", "certified_axial_sections_with_HLR"}
                                                                else not any(c.get("approximation") for c in curves)),
                                           "source_mapping_exact": all(bool(c.get('source_edge_ids')) for c in curves) if geometry_method != "HLRBRep_Algo_exact_conics" else False,
                                           "all_projected_edges_have_source_edge_ids": all(bool(c.get('source_edge_ids')) for c in curves),
                                           "all_projected_edges_exact": not any(c["value_type"] != "exact" for c in curves),
                                           "hidden_edges_explicit": any(c["layer"] == "HIDDEN" for c in curves),
                                           "manufacturing_metadata_extraction_status": "explicit_STEP_references_audited_see_step_bindings",
                                           "assembly_metadata_extraction_status": step_metadata.get("assembly_resolution_status", "no_occurrences"),
                                           "assembly_product_count": len(step_metadata.get("products", [])),
                                           "assembly_occurrence_count": len(step_metadata.get("assembly_occurrences", [])),
                                           "pmi_metadata_extraction_status": "raw_entities_preserved_with_explicit_topology_binding_audit",
                                           "dxf_metadata_annotation_status": "generated_for_parsed_step_entities"},
                "source_faces": [{k: value for k, value in f.items() if k != "face"} for f in faces],
                "unknown_semantics": ["material", "tolerances", "thread", "manufacturing_process", "original_design_intent",
                                      "colors_layers", "pmi", "surface_roughness"],
                "unparsed_step_entity_types": step_metadata.get("unparsed_entity_types", []),
                "synthetic_semantics": ["view_selection", contract['model_class']] if complete_geometry else ["view_selection"]}
    out.mkdir(parents=True, exist_ok=True)
    manifest['brep_roundtrip'] = brep_report
    manifest['semantic_completeness'].update(
        complete_brep_serialization=brep_report['serialization_roundtrip_verified'],
        brep_serialization_scope='OCCT native imported geometry in geometry.binbrep; not DXF',
        valid_brep_roundtrip=brep_report['valid_brep_roundtrip'],
        complete_step_semantics=False)
    doc.saveas(out / "drawing.dxf")
    checked = ezdxf.readfile(out / "drawing.dxf")
    audit = checked.audit()
    handles = {e.dxf.handle for e in checked.modelspace()}
    if audit.errors or audit.fixes or handles != {e["handle"] for e in entities}:
        raise RuntimeError("DXF round-trip or provenance audit failed")
    license_status = "provided_unverified" if license_text else "unknown"
    log = {"schema_version": 2, "created_utc": datetime.now(timezone.utc).isoformat(), "input": str(input_step),
           "mode": mode, "source_length_units": source_units, "output_unit": "mm", "solid_count": solid_count,
           "bbox_min": lo, "bbox_max": hi, "volume_mm3": mass(shape), "view_candidates": candidates,
           "selected_axis": chosen["axis"], "geometry_method": geometry_method, "certificate": chosen["certificate"],
           "status": status, "license_status": license_status, "mainboard_eligible": False,
           "mainboard_note": "Requires source license review and independent Fusion reconstruction evaluation",
           "dxf_audit_errors": len(audit.errors), "dxf_audit_fixes": len(audit.fixes),
           "modelspace_entities": len(handles), "all_modelspace_entities_have_provenance": True,
           "provenance_scope": "Record coverage only; reliable original-edge IDs currently limited to certified cap wires",
           "surface_counts": dict(surface_counts), "unsupported_source_face_ids": unsupported_faces,
           "topology_counts": topology["counts"],
           "step_metadata": step_metadata,
           "brep_roundtrip": brep_report,
           "step_binding_status": step_bindings.get('status', 'see_feature_manifest'),
           "projection_exact": (None if geometry_method in {"HLRBRep_Algo_exact_conics", "certified_axial_sections_with_HLR"}
                                else not any(c.get("approximation") for c in curves)),
           "projection_exactness_note": ("HLR kernel approximation status is not exposed by current OCC binding"
                                         if "HLR" in geometry_method else "Certified source geometry path"),
           "exactness_policy": "No curve polyline approximation; unsupported geometry is flagged explicitly"}
    shutil.copy2(input_step, out / "source.step")
    write_json(out / "view_contract.json", contract)
    write_json(out / "feature_manifest.json", manifest)
    write_json(out / "generation_log.json", log)
    (out / "LICENSE.txt").write_text(license_text if license_text else
        "SOURCE LICENSE UNKNOWN. No redistribution permission is granted by this generated file.\n"
        "Obtain and review the original STEP license before publishing this sample.\n", encoding="utf-8")
    files = sorted(p for p in out.iterdir() if p.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in files), encoding="ascii")
    return {"output": str(out), "status": status, "ambiguity_flags": flags, "view": chosen["axis"],
            "feature_counts": manifest["feature_counts"], "dxf_audit_errors": len(audit.errors)}


def generate_orthographic_views(input_step: Path, out: Path, mode="standard", license_file=None, tolerance=0.001, allow_invalid=False):
    """Generate one independently audited DXF per principal axis.

    A single-view drawing cannot contain information occluded in another axis;
    this explicit bundle preserves all three orthographic observations without
    inventing hidden geometry. Each child has its own contract and manifest.
    """
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Output directory must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for axis in BASES:
        records.append(generate(input_step, out / axis, mode=mode, view=axis,
                                license_file=license_file, tolerance=tolerance, allow_invalid=allow_invalid))
    bundle = {"schema_version": 1, "input_step": str(Path(input_step).resolve()),
              "views": records, "axes": list(BASES),
              "semantic_policy": "Each view is exact within its contract; no view is claimed to expose occluded features.",
              "all_views_have_geometry_certificate": all(not r["ambiguity_flags"] for r in records),
              "complete_3d_semantics": False,
              "completeness_note": "Projection/model-class certificates do not prove retention of all STEP metadata or complete B-Rep semantics"}
    write_json(out / "orthographic_bundle.json", bundle)
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_step", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="New or empty sample directory")
    parser.add_argument("--mode", choices=("minimal", "standard", "rich"), default="standard")
    parser.add_argument("--view", choices=tuple(BASES), help="Force orthogonal view; default: auto score")
    parser.add_argument("--all-views", action="store_true", help="Write audited X/Y/Z DXF subdirectories")
    parser.add_argument("--license", dest="license_file", type=Path, help="Source license text (no automatic license inference)")
    parser.add_argument("--tolerance", type=float, default=.001, help="Geometry/curve tolerance in mm")
    parser.add_argument("--allow-invalid", action="store_true", help="Project an OCC-invalid STEP for diagnosis; output is never certified as a valid B-Rep")
    args = parser.parse_args()
    try:
        values = vars(args)
        if values.pop("all_views"):
            values.pop("view", None)
            result = generate_orthographic_views(**values)
        else:
            result = generate(**values)
    except (ValueError, RuntimeError, FileExistsError, OSError) as exc:
        parser.exit(2, f"step_to_dxf: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
