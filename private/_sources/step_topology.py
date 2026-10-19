"""Imported B-Rep incidence, including oriented occurrences and periodic seams.

This is an audit graph, not a serialized B-Rep: pcurves and surface definitions
are still required for reconstruction. IDs are local to this STEP transfer.
"""
from collections import Counter

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAdaptor import BRepAdaptor_Curve2d
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.TopAbs import (TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_WIRE,
                        TopAbs_EDGE, TopAbs_VERTEX, TopAbs_FORWARD)
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Iterator
from OCP.BRepCheck import BRepCheck_Analyzer
from step_curve_semantics import curve_definition
from step_surface_semantics import surface_semantics


def topology_inventory(shape):
    kinds = {'solids': TopAbs_SOLID, 'shells': TopAbs_SHELL, 'faces': TopAbs_FACE,
             'wires': TopAbs_WIRE, 'edges': TopAbs_EDGE, 'vertices': TopAbs_VERTEX}
    maps = {name: TopTools_IndexedMapOfShape() for name in kinds}
    for name, mapping in maps.items():
        TopExp.MapShapes_s(shape, kinds[name], mapping)

    def identifier(part, name):
        if part.IsNull():
            return None
        found = maps[name].FindIndex(part)
        if not found:
            raise ValueError(f'Unindexed {name} in topology')
        return found

    def location_record(part):
        """Serialize the OCC placement carried by an occurrence."""
        tr = part.Location().Transformation()
        return {'is_identity': bool(part.Location().IsIdentity()),
                'matrix': [[float(tr.Value(r, c)) for c in range(1, 5)] for r in range(1, 4)],
                'form': tr.Form().name}

    def children(part, name):
        it = TopoDS_Iterator(part)
        result = []
        while it.More():
            child = it.Value()
            if child.ShapeType() == kinds[name]:
                result.append({'id': identifier(child, name), 'orientation': child.Orientation().name})
            it.Next()
        return result

    def descendants(part, name):
        local = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(part, kinds[name], local)
        return [identifier(local.FindKey(i), name) for i in range(1, local.Extent()+1)]

    def edge_use(edge, face=None):
        edge = TopoDS.Edge_s(edge)
        record = {'edge_id': identifier(edge, 'edges'), 'orientation': edge.Orientation().name,
                  'start_vertex_id': identifier(TopExp.FirstVertex_s(edge, True), 'vertices'),
                  'end_vertex_id': identifier(TopExp.LastVertex_s(edge, True), 'vertices')}
        if face is not None:
            record['seam_on_face'] = BRep_Tool.IsClosed_s(edge, face)
            # Preserve the exact pcurve attached to this face occurrence when OCC exposes it.
            try:
                record['pcurve'] = curve_definition(BRepAdaptor_Curve2d(edge, face), 2)
            except Exception as exc:
                record['pcurve'] = None
                record['pcurve_error'] = str(exc)
        return record

    validation = {}
    for name, mapping in maps.items():
        invalid = []
        for i in range(1, mapping.Extent()+1):
            part = mapping.FindKey(i)
            try:
                if not BRepCheck_Analyzer(part).IsValid():
                    invalid.append(i)
            except Exception:
                invalid.append(i)
        validation[name] = {'count': mapping.Extent(), 'invalid_ids': invalid,
                            'valid': not invalid}
    result = {'schema_version': 3, 'counts': {k: m.Extent() for k,m in maps.items()},
              'validation': validation,
              'scope': 'Imported located topology; not STEP assembly/PMI or complete B-Rep serialization',
              'complete_brep_serialization': False,
              'orientation_convention': 'Child uses relative to forward-oriented parent, with locations accumulated',
              'missing_for_brep_reconstruction': ['surface definitions for unsupported OCC surface classes',
                                                   '3D curve definitions',
                                                   'pcurve geometry for unavailable edge/face occurrences'],
              **{name: [] for name in kinds}}

    for i in range(1, maps['vertices'].Extent()+1):
        vertex = TopoDS.Vertex_s(maps['vertices'].FindKey(i))
        p = BRep_Tool.Pnt_s(vertex)
        result['vertices'].append({'vertex_id': i, 'point_mm': [p.X(),p.Y(),p.Z()],
                                   'location': location_record(vertex),
                                   'tolerance_mm': BRep_Tool.Tolerance_s(vertex)})
    for i in range(1, maps['edges'].Extent()+1):
        edge = TopoDS.Edge_s(maps['edges'].FindKey(i).Oriented(TopAbs_FORWARD))
        degenerate = BRep_Tool.Degenerated_s(edge)
        record = {'edge_id': i, 'degenerated': degenerate, 'face_ids': [], 'wire_ids': [],
                  'location': location_record(edge),
                  'vertex_ids': descendants(edge,'vertices'), 'vertex_uses': children(edge,'vertices'),
                  'tolerance_mm': BRep_Tool.Tolerance_s(edge),
                  'parameter_range': list(BRep_Tool.Range_s(edge)), 'curve_type': None}
        if degenerate:
            record['curve_status'] = 'degenerate_edge_no_3d_curve_required'
        else:
            try:
                adaptor = BRepAdaptor_Curve(edge)
                record['curve_type'] = adaptor.GetType().name
                record['curve_status'] = 'available'
                record['curve_periodic'] = bool(adaptor.IsPeriodic())
                record['curve_closed'] = bool(adaptor.IsClosed())
                try:
                    record['curve_native'] = curve_definition(adaptor, 3)
                except NotImplementedError as exc:
                    record['curve_native_error'] = str(exc)
                try:
                    if 'Line' in record['curve_type']:
                        l = adaptor.Line(); record['curve'] = {'type':'line', 'origin':[l.Location().X(),l.Location().Y(),l.Location().Z()], 'direction':[l.Direction().X(),l.Direction().Y(),l.Direction().Z()]}
                    elif 'Circle' in record['curve_type']:
                        c = adaptor.Circle(); record['curve'] = {'type':'circle', 'center':[c.Location().X(),c.Location().Y(),c.Location().Z()], 'radius_mm':c.Radius()}
                    elif 'Ellipse' in record['curve_type']:
                        e = adaptor.Ellipse(); record['curve'] = {'type':'ellipse', 'center':[e.Location().X(),e.Location().Y(),e.Location().Z()], 'major_radius_mm':e.MajorRadius(), 'minor_radius_mm':e.MinorRadius()}
                    elif 'BSpline' in record['curve_type']:
                        s = adaptor.BSpline(); record['curve'] = {'type':'bspline', 'degree':s.Degree(), 'rational':s.IsRational(), 'poles_mm':[[s.Pole(k).X(),s.Pole(k).Y(),s.Pole(k).Z()] for k in range(1,s.NbPoles()+1)], 'knots': [s.Knot(k) for k in range(1,s.NbKnots()+1)], 'multiplicities':[s.Multiplicity(k) for k in range(1,s.NbKnots()+1)], 'weights':[s.Weight(k) for k in range(1,s.NbPoles()+1)] if s.IsRational() else None}
                    elif 'Bezier' in record['curve_type']:
                        s = adaptor.Bezier(); record['curve'] = {'type':'bezier', 'degree':s.Degree(), 'rational':s.IsRational(), 'poles_mm':[[s.Pole(k).X(),s.Pole(k).Y(),s.Pole(k).Z()] for k in range(1,s.NbPoles()+1)]}
                except Exception as exc:
                    record['curve_parameter_error'] = str(exc)
            except Exception as exc:
                record['curve_status'] = 'unavailable'
                record['curve_error'] = str(exc)
        result['edges'].append(record)
    for i in range(1, maps['wires'].Extent()+1):
        wire = TopoDS.Wire_s(maps['wires'].FindKey(i).Oriented(TopAbs_FORWARD))
        uses = children(wire,'edges')
        result['wires'].append({'wire_id': i, 'closed': BRep_Tool.IsClosed_s(wire),
                                'location': location_record(wire),
                                'edge_uses': uses, 'face_ids': []})
        for eid in {use['id'] for use in uses}:
            result['edges'][eid-1]['wire_ids'].append(i)
    for i in range(1, maps['faces'].Extent()+1):
        face = TopoDS.Face_s(maps['faces'].FindKey(i).Oriented(TopAbs_FORWARD))
        outer = BRepTools.OuterWire_s(face)
        loops = []
        for use in children(face,'wires'):
            wid = use['id']
            # Retrieve the oriented occurrence, not just the deduplicated wire.
            it = TopoDS_Iterator(face)
            while it.More():
                part = it.Value()
                if part.ShapeType() == TopAbs_WIRE and identifier(part,'wires') == wid:
                    wire = TopoDS.Wire_s(part)
                    break
                it.Next()
            explorer = BRepTools_WireExplorer(wire, face)
            ordered = []
            while explorer.More():
                ordered.append(edge_use(explorer.Current(),face))
                explorer.Next()
            actual = Counter((u['edge_id'],u['orientation']) for u in ordered)
            expected = Counter((u['id'],u['orientation']) for u in children(wire,'edges'))
            loops.append({'wire_id': wid, 'orientation': use['orientation'],
                          'outer': wire.IsSame(outer), 'ordered_edge_uses': ordered,
                          'traversal_complete': actual == expected})
            result['wires'][wid-1]['face_ids'].append(i)
        eids = descendants(face,'edges')
        for eid in eids:
            result['edges'][eid-1]['face_ids'].append(i)
        result['faces'].append({'face_id': i, 'wire_ids': descendants(face,'wires'),
                               'location': location_record(face),
                               'edge_ids': eids, 'vertex_ids': descendants(face,'vertices'),
                               'shell_ids': [], 'loops': loops,
                               'tolerance_mm': BRep_Tool.Tolerance_s(face),
                               'surface': surface_semantics(TopoDS.Face_s(maps['faces'].FindKey(i)))})
    for i in range(1, maps['shells'].Extent()+1):
        shell = maps['shells'].FindKey(i).Oriented(TopAbs_FORWARD)
        fids = descendants(shell,'faces')
        result['shells'].append({'shell_id': i, 'closed': BRep_Tool.IsClosed_s(shell),
                                 'location': location_record(shell),
                                 'face_ids': fids, 'face_uses': children(shell,'faces')})
        for fid in fids:
            result['faces'][fid-1]['shell_ids'].append(i)
    for i in range(1, maps['solids'].Extent()+1):
        solid = maps['solids'].FindKey(i)
        result['solids'].append({'solid_id': i, 'orientation': solid.Orientation().name,
                                'location': location_record(solid),
                                'face_ids': descendants(solid,'faces'),
                                'shell_ids': descendants(solid,'shells'),
                                'shell_uses': children(solid.Oriented(TopAbs_FORWARD),'shells')})
    result['all_face_loops_traversed'] = all(loop['traversal_complete'] for face in result['faces'] for loop in face['loops'])
    return result
