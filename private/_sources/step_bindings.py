"""Bind explicit STEP semantics to topology from the *same* OCCT transfer.

No geometric proximity, labels, separate XCAF transfer, or HLR correspondence is
used. A definition may legitimately cover several located topology instances.
Unsupported schema links remain unresolved and retain their source references.
"""
from collections import Counter, defaultdict

from OCP.TopAbs import (TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_WIRE,
                       TopAbs_EDGE, TopAbs_VERTEX, TopAbs_COMPOUND, TopAbs_COMPSOLID)
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Iterator

from step_metadata import arguments, label, references, step_header_metadata


KINDS = {'solids': TopAbs_SOLID, 'shells': TopAbs_SHELL, 'faces': TopAbs_FACE,
         'wires': TopAbs_WIRE, 'edges': TopAbs_EDGE, 'vertices': TopAbs_VERTEX}


def _maps(shape):
    result = {}
    for kind, shape_type in KINDS.items():
        mapping = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, shape_type, mapping)
        result[kind] = mapping
    return result


def _placement(shape):
    tr = shape.Location().Transformation()
    return {'matrix': [[float(tr.Value(row, col)) for col in range(1, 5)]
                       for row in range(1, 4)], 'is_identity': shape.Location().IsIdentity()}


def _summarize(report):
    categories = {}
    for category, key in [('topology', 'entity_topology_bindings'), ('assembly', 'assembly_bindings'),
                          ('pmi', 'pmi_bindings'), ('semantic_support', 'semantic_support_bindings'),
                          ('colour', 'colour_bindings'), ('material', 'material_bindings')]:
        counts = Counter(row['status'] for row in report[key])
        total = len(report[key])
        resolved = counts['resolved']
        categories[category] = {'total': total, 'resolved': resolved,
            'partially_resolved': counts['partially_resolved'],
            'unresolved': sum(count for status, count in counts.items() if status.startswith('unresolved')),
            'ambiguous': counts['ambiguous'], 'status_counts': dict(counts),
            'status': 'no_entities' if not total else 'resolved' if total == resolved else
                      'partial' if resolved or counts['partially_resolved'] else 'unresolved'}
    report['category_status'] = categories
    report['status'] = 'unavailable' if not report['same_transfer_verified'] else (
        'partial' if any(row['status'] in ('partial', 'unresolved') for row in categories.values())
        else 'resolved_supported_paths')
    return report


def bind_step_metadata(path, reader, shape, metadata=None):
    """Return JSON bindings; ``reader`` must be the importer of ``shape``.

    Targets use the 1-based located IDs in ``step_topology.topology_inventory``.
    This function does not mutate metadata. Evidence paths list actual STEP
    reference edges (some traversed in reverse, described by the rule).
    """
    metadata = metadata if metadata is not None else step_header_metadata(path)
    records = {row['entity_id']: row for row in metadata['unparsed_entities']}
    components = {eid: {c['entity_type']: arguments(c['raw_arguments'])
                        for c in row.get('components', [row])} for eid, row in records.items()}
    by_type = defaultdict(list)
    reverse = defaultdict(list)
    for eid, row in records.items():
        for kind in components[eid]:
            by_type[kind].append(eid)
        for ref in row['references']:
            reverse[ref].append(eid)
    geometry_users = defaultdict(list)
    geometry_fields = {'EDGE_CURVE': 3, 'ADVANCED_FACE': 2, 'FACE_SURFACE': 2,
                       'VERTEX_POINT': 1, 'SURFACE_CURVE': 1, 'SEAM_CURVE': 1,
                       'TRIMMED_CURVE': 1}
    for eid, parts in components.items():
        for kind, values in parts.items():
            index = geometry_fields.get(kind)
            if index is not None and len(values) > index:
                for geometry in references(values[index]):
                    geometry_users[geometry].append(eid)
    def is_shape_representation(eid):
        return any(kind == 'SHAPE_REPRESENTATION' or
                   (kind.endswith('_SHAPE_REPRESENTATION') and kind != 'CONTEXT_DEPENDENT_SHAPE_REPRESENTATION')
                   for kind in components.get(eid, {}))

    aspect_representations = defaultdict(list)
    for sdr in by_type['SHAPE_DEFINITION_REPRESENTATION']:
        values = components[sdr]['SHAPE_DEFINITION_REPRESENTATION']
        if len(values) < 2:
            continue
        for definition in references(values[0]):
            candidates = []
            if any('SHAPE_ASPECT' in kind for kind in components.get(definition, {})):
                candidates.append((definition, (sdr,)))
            property_args = components.get(definition, {}).get('PROPERTY_DEFINITION', [])
            if len(property_args) > 2:
                candidates.extend((aspect, (definition, sdr)) for aspect in references(property_args[2])
                                  if any('SHAPE_ASPECT' in kind for kind in components.get(aspect, {})))
            for aspect, via in candidates:
                aspect_representations[aspect].extend((rep, via) for rep in references(values[1]))
    maps = _maps(shape)
    report = {'schema_version': 1, 'same_transfer_verified': False,
              'topology_counts': {kind: mapping.Extent() for kind, mapping in maps.items()},
              'entity_topology_bindings': [], 'assembly_bindings': [], 'pmi_bindings': [],
              'semantic_support_bindings': [],
              'colour_bindings': [], 'material_bindings': [], 'diagnostics': [],
              'scope': 'Explicit STEP semantics to imported located topology; no HLR or DXF entity correspondence',
              'limitations': ['Unsupported semantic schema paths remain unresolved',
                              'Topology IDs are local to this transfer and ignore orientation',
                              'Coincident occurrences may share a located topology ID']}
    source_maps = _maps(reader.OneShape())
    if any(mapping.Extent() != source_maps[kind].Extent() or any(
            not source_maps[kind].Contains(mapping.FindKey(i))
            for i in range(1, mapping.Extent() + 1)) for kind, mapping in maps.items()):
        report['diagnostics'].append('shape_does_not_belong_to_supplied_reader_transfer')
        return _summarize(report)
    report['same_transfer_verified'] = True
    model = reader.StepModel()
    transfer = reader.WS().TransferReader()
    # NextNumberForLabel resolves source numeric labels inside OCCT. Do not use
    # file order as an ID, or assume STEP labels are dense. Starting immediately
    # before the expected record makes the usual case linear. This also avoids
    # OCP 7.9's broken Standard_Transient argument conversion in Model.Number.
    entity_ids = {}
    previous = 0
    for eid in records:
        number = model.NextNumberForLabel(f'#{eid}', previous, True)
        if not number:
            number = model.NextNumberForLabel(f'#{eid}', 0, True)
        if number:
            entity_ids[model.Value(number)] = eid
            previous = number
        else:
            report['diagnostics'].append({'entity_id': eid, 'reason': 'source_label_not_in_reader_model'})
    direct = defaultdict(dict)
    placements = defaultdict(list)
    target_locations = {}

    def targets_for(part):
        output = []
        for kind, mapping in maps.items():
            found = mapping.FindIndex(part)
            if found:
                return [{'kind': kind, 'id': found}]
        # Assembly compounds do not have inventory IDs: bind their explicit
        # descendant solids (or faces if the transferred result is a surface).
        for kind in ('solids', 'faces', 'edges', 'vertices'):
            local = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(part, KINDS[kind], local)
            output = [{'kind': kind, 'id': maps[kind].FindIndex(local.FindKey(i))}
                      for i in range(1, local.Extent() + 1)
                      if maps[kind].FindIndex(local.FindKey(i))]
            if output:
                break
        return output

    def add_transfer(part, targets, mode=-1, definition=False):
        query = part.Located(TopLoc_Location()) if definition else part
        entity = transfer.EntityFromShapeResult(query, mode)
        eid = entity_ids.get(entity)
        if eid is None:
            return
        for target in targets:
            direct[eid][(target['kind'], target['id'])] = target
        placement = _placement(part)
        if placement not in placements[eid]:
            placements[eid].append(placement)

    required_kinds = {'solids', 'faces'}
    topology_types = {'EDGE_CURVE': 'edges', 'ORIENTED_EDGE': 'edges', 'VERTEX_POINT': 'vertices',
                      'EDGE_LOOP': 'wires', 'POLY_LOOP': 'wires', 'CLOSED_SHELL': 'shells', 'OPEN_SHELL': 'shells'}
    semantic_ids = {row['entity_id'] for row in metadata.get('pmi_entities', [])}
    for kind in by_type:
        if 'STYLED_ITEM' in kind or kind in ('GEOMETRIC_ITEM_SPECIFIC_USAGE', 'ITEM_IDENTIFIED_REPRESENTATION_USAGE'):
            semantic_ids.update(by_type[kind])
    pending = [ref for eid in semantic_ids for ref in records[eid]['references']]
    pending.extend(rep for representations in aspect_representations.values()
                   for rep, _ in representations if is_shape_representation(rep))
    seen = set()
    while pending:
        eid = pending.pop()
        if eid in seen:
            continue
        seen.add(eid)
        pending.extend(geometry_users[eid])
        for kind in components.get(eid, {}):
            if kind in topology_types:
                required_kinds.add(topology_types[kind])
            elif kind.endswith('SHAPE_REPRESENTATION'):
                values = components[eid][kind]
                if len(values) > 1:
                    pending.extend(references(values[1]))
    report['mapped_topology_kinds'] = sorted(required_kinds)
    report['entity_mapping_scope'] = 'solids, faces, assemblies and topology kinds explicitly referenced by semantic entities'
    for kind, mapping in maps.items():
        if kind not in required_kinds:
            continue
        for index in range(1, mapping.Extent() + 1):
            part = mapping.FindKey(index)
            targets = [{'kind': kind, 'id': index}]
            target_locations[(kind, index)] = _placement(part)
            add_transfer(part, targets)
            if kind == 'solids':
                add_transfer(part, targets, mode=2)
                if not part.Location().IsIdentity():
                    add_transfer(part, targets, definition=True)
    for kind in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
        local = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, kind, local)
        for index in range(1, local.Extent() + 1):
            part = local.FindKey(index)
            targets = targets_for(part)
            add_transfer(part, targets)
            add_transfer(part, targets, mode=2)
            if not part.Location().IsIdentity():
                add_transfer(part, targets, definition=True)

    occurrence_records = {row['entity_id']: row for row in metadata.get('assembly_occurrences', [])}
    occurrence_instances = defaultdict(list)
    # The inverse transfer lookup may fall back to a partner shape when the
    # location differs. For NAUO that is insufficient: recover each local child
    # under its actual assembly parent, then accumulate the parent placement.
    for eid in occurrence_records:
        direct.pop(eid, None)
        placements.pop(eid, None)

    def walk_assembly(global_part, parent_definition=None, occurrence_path=()):
        if global_part.ShapeType() not in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
            return
        iterator = TopoDS_Iterator(global_part, True, False)
        while iterator.More():
            local_part = iterator.Value()
            located_part = local_part.Located(global_part.Location().Multiplied(local_part.Location()))
            eid = entity_ids.get(transfer.EntityFromShapeResult(local_part, -1))
            occurrence = occurrence_records.get(eid)
            child_definition = parent_definition
            child_path = occurrence_path
            if occurrence and (parent_definition is None or
                               occurrence['relating_product_definition_id'] == parent_definition):
                targets = targets_for(located_part)
                placement = _placement(located_part)
                child_definition = occurrence['related_product_definition_id']
                child_path = (*occurrence_path, eid)
                instance = {'occurrence_path': list(child_path), 'targets': targets,
                            'placement': placement, 'local_placement': _placement(local_part)}
                occurrence_instances[eid].append(instance)
                for target in targets:
                    direct[eid][(target['kind'], target['id'])] = target
                if placement not in placements[eid]:
                    placements[eid].append(placement)
            walk_assembly(located_part, child_definition, child_path)
            iterator.Next()

    root_entity = entity_ids.get(transfer.EntityFromShapeResult(shape, 2))
    walk_assembly(shape, root_entity if root_entity in by_type['PRODUCT_DEFINITION'] else None)
    ambiguous_occurrences = {}
    for eid, instances in occurrence_instances.items():
        paths = [tuple(instance['occurrence_path']) for instance in instances]
        if len(set(paths)) != len(paths):
            ambiguous_occurrences[eid] = {'candidate_targets': list(direct.pop(eid, {}).values()),
                                          'candidate_occurrence_instances': instances}
    sibling_groups = defaultdict(list)
    for eid, occurrence in occurrence_records.items():
        sibling_groups[(occurrence['relating_product_definition_id'],
                        occurrence['related_product_definition_id'])].append(eid)
    for group in sibling_groups.values():
        if len(group) > 1 and any(not occurrence_instances[eid] for eid in group):
            # OCCT can collapse coincident duplicate children. The inverse
            # result then identifies only one source NAUO; do not assign that
            # shared identity to one sibling as though it were unique.
            for eid in group:
                ambiguous_occurrences[eid] = {'candidate_targets': list(direct.pop(eid, {}).values()),
                    'candidate_occurrence_instances': occurrence_instances[eid],
                    'candidate_occurrence_entity_ids': group}

    def types(eid):
        return set(components.get(eid, {}))

    def args(eid, kind=None):
        row = components.get(eid, {})
        return row.get(kind, []) if kind else next(iter(row.values()), [])

    def field_refs(eid, index, kind=None):
        values = args(eid, kind)
        return references(values[index]) if len(values) > index else []

    def safe_label(eid, index=0):
        try:
            return label(args(eid)[index], True)
        except (ValueError, IndexError) as exc:
            report['diagnostics'].append({'entity_id': eid, 'reason': 'invalid_semantic_label', 'error': str(exc)})
            return None

    def record(eid, targets, paths, **extra):
        unique = {(t['kind'], t['id']): t for t in targets}
        return {'entity_id': eid, 'entity_type': records[eid]['entity_type'],
                'status': 'resolved' if unique else 'unresolved',
                'targets': [unique[key] for key in sorted(unique)],
                'references': records[eid]['references'],
                'evidence_paths': paths, **extra}

    for eid in sorted(direct):
        report['entity_topology_bindings'].append(record(eid, direct[eid].values(), [[eid]],
            rule='OCCT same-transfer EntityFromShapeResult; topology identity with located occurrence',
            target_scope='all_transferred_occurrences_of_definition', placements=placements[eid]))

    # Restrict representation resolution to representation items and explicit
    # product -> product-definition-shape -> shape-definition-representation.
    def endpoint(eid, trail=(), unresolved=None):
        if eid in trail or len(trail) > 32:
            if unresolved is not None:
                unresolved.append({'entity_id': eid, 'reason': 'cyclic_or_overlong_endpoint_path',
                                   'evidence_path': [*trail, eid]})
            return [], []
        trail = (*trail, eid)
        if eid in direct:
            return list(direct[eid].values()), [list(trail)]
        children = []
        if any(kind.endswith('SHAPE_REPRESENTATION') or kind == 'SHAPE_REPRESENTATION'
               for kind in types(eid)):
            children = [(ref, ()) for ref in field_refs(eid, 1)
                        if not types(ref) & {'AXIS2_PLACEMENT_3D', 'AXIS2_PLACEMENT_2D'}]
        elif 'PRODUCT_DEFINITION' in types(eid):
            for pds in reverse[eid]:
                if 'PRODUCT_DEFINITION_SHAPE' not in types(pds) or eid not in field_refs(pds, 2):
                    continue
                for sdr in reverse[pds]:
                    if 'SHAPE_DEFINITION_REPRESENTATION' in types(sdr) and pds in field_refs(sdr, 0):
                        children += [(rep, (pds, sdr)) for rep in field_refs(sdr, 1)]
        elif 'ORIENTED_EDGE' in types(eid):
            children = [(ref, ()) for ref in field_refs(eid, 3)]
        elif eid in geometry_users:
            children = [(ref, ()) for ref in geometry_users[eid]]
        found, paths = [], []
        if not children and unresolved is not None:
            unresolved.append({'entity_id': eid, 'reason': 'no_supported_transferred_endpoint',
                               'evidence_path': list(trail)})
        for child, via in children:
            results, evidence = endpoint(child, (*trail, *via), unresolved)
            found.extend(results)
            paths.extend(evidence)
        return found, paths

    for item in metadata.get('assembly_occurrences', []):
        eid = item['entity_id']
        targets = list(direct.get(eid, {}).values())
        row = record(eid, targets, [[eid]] if targets else [],
            rule='OCCT occurrence transfer result',
            relating_product_definition_id=item['relating_product_definition_id'],
            related_product_definition_id=item['related_product_definition_id'],
            placement=placements[eid][0] if len(placements[eid]) == 1 else None,
            placements=placements[eid],
            occurrence_instances=[] if eid in ambiguous_occurrences else occurrence_instances[eid],
            unresolved_reason=None if targets else 'no_same_transfer_occurrence_result')
        if eid in ambiguous_occurrences:
            row.update(ambiguous_occurrences[eid], status='ambiguous',
                       unresolved_reason='coincident_children_share_a_transfer_result; occurrence_identity_not_unique')
        report['assembly_bindings'].append(row)

    usages = set(by_type['GEOMETRIC_ITEM_SPECIFIC_USAGE'] +
                 by_type['ITEM_IDENTIFIED_REPRESENTATION_USAGE'])
    usages_by_definition = defaultdict(list)
    for usage in usages:
        for definition in field_refs(usage, 2):
            usages_by_definition[definition].append(usage)
    reachability = {}

    def representation_contains(rep, item):
        if rep not in reachability:
            seen, pending = set(), [rep]
            while pending:
                node = pending.pop()
                if node in seen:
                    continue
                seen.add(node)
                pending.extend(records.get(node, {}).get('references', []))
            reachability[rep] = seen
        return item in reachability[rep]

    def pmi_endpoint(eid, trail=()):
        path = [*trail, eid]

        def missing(reason, node=eid, evidence=path):
            return [], [], [{'entity_id': node, 'reason': reason, 'evidence_path': evidence}]

        if eid not in records:
            return missing('missing_step_entity')
        if eid in trail or len(trail) > 32:
            return missing('cyclic_or_overlong_semantic_reference_path')
        if eid in direct:
            return list(direct[eid].values()), [path], []
        children = []
        if eid in usages:
            identified = field_refs(eid, 4)
            reps = field_refs(eid, 3)
            if len(reps) != 1 or reps[0] not in records:
                return missing('missing_or_ambiguous_used_representation')
            if not any(kind in ('REPRESENTATION', 'SHAPE_REPRESENTATION') or
                       (kind.endswith('_SHAPE_REPRESENTATION') and kind != 'CONTEXT_DEPENDENT_SHAPE_REPRESENTATION')
                       for kind in types(reps[0])):
                return missing('unsupported_used_representation_type', reps[0], [*path, reps[0]])
            if not identified:
                return missing('no_explicit_identified_item')
            found, paths, failures = [], [], []
            for child in identified:
                if child not in records:
                    failures.append({'entity_id': child, 'reason': 'missing_step_entity',
                                     'evidence_path': [*path, child]})
                elif not representation_contains(reps[0], child):
                    failures.append({'entity_id': child, 'reason': 'identified_item_not_in_used_representation',
                                     'evidence_path': [*path, child], 'used_representation_id': reps[0]})
                else:
                    targets, evidence = endpoint(child, tuple(path))
                    found.extend(targets)
                    paths.extend(evidence)
                    if not targets:
                        failures.append({'entity_id': child, 'reason': 'no_transferred_topology_for_identified_item',
                                         'evidence_path': [*path, child]})
            return found, paths, failures
        entity_types = types(eid)
        shape_targets, shape_paths, shape_failures = [], [], []
        if any('SHAPE_ASPECT' in kind or kind in ('DATUM', 'DATUM_FEATURE', 'DATUM_TARGET')
               for kind in entity_types) and not any('RELATIONSHIP' in kind for kind in entity_types):
            children = sorted(usages_by_definition[eid])
            # The shape_aspect.of_shape field describes ownership; it is not
            # an identified geometry endpoint and is deliberately not followed.
            # A direct SDR, or PROPERTY_DEFINITION.definition -> aspect followed
            # by an SDR, instead explicitly supplies the aspect's own shape.
            # Generic PROPERTY_DEFINITION_REPRESENTATION measurements are excluded.
            for rep, via in aspect_representations[eid]:
                evidence = [*path, *via, rep]
                if not is_shape_representation(rep):
                    shape_failures.append({'entity_id': rep, 'reason': 'unsupported_aspect_shape_representation_type',
                                           'evidence_path': evidence})
                    continue
                targets, paths = endpoint(rep, (*path, *via), shape_failures)
                shape_targets.extend(targets)
                shape_paths.extend(paths)
        elif 'DIMENSIONAL_CHARACTERISTIC_REPRESENTATION' in entity_types:
            children = field_refs(eid, 0)
        elif any(kind.startswith('DIMENSIONAL_SIZE') for kind in entity_types):
            children = field_refs(eid, 0)
        elif any(kind.startswith('DIMENSIONAL_LOCATION') for kind in entity_types):
            children = field_refs(eid, 2) + field_refs(eid, 3)
        elif 'GEOMETRIC_TOLERANCE' in entity_types:
            children = field_refs(eid, 3, 'GEOMETRIC_TOLERANCE')
        elif any(kind.endswith('_TOLERANCE') for kind in entity_types):
            children = field_refs(eid, 3)
        if not children and not shape_targets and not shape_failures:
            return missing('no_supported_explicit_geometry_path')
        found, paths, failures = shape_targets, shape_paths, shape_failures
        for child in children:
            targets, evidence, unresolved = pmi_endpoint(child, tuple(path))
            found.extend(targets)
            paths.extend(evidence)
            failures.extend(unresolved)
        return found, paths, failures

    pmi_ids = {row['entity_id'] for row in metadata.get('pmi_entities', [])}
    support_ids = {row['entity_id'] for row in metadata.get('semantic_support_entities', [])} | usages
    for eid in sorted(pmi_ids | support_ids):
        targets, paths, unresolved = pmi_endpoint(eid)
        row = record(eid, targets, paths, unresolved_links=unresolved,
            rule='dimension/tolerance -> shape_aspect -> explicit identified item or shape-definition representation',
            target_scope='all_transferred_occurrences_of_definition',
            semantic_role='engineering_annotation_or_tolerance' if eid in pmi_ids else 'support_only_not_engineering_annotation',
            semantic_value_status='raw_STEP_value_preserved_not_interpreted')
        if targets and unresolved:
            row['status'] = 'partially_resolved'
        report['pmi_bindings' if eid in pmi_ids else 'semantic_support_bindings'].append(row)

    # Presentation styles: only style references feed the colour; only the
    # styled_item.item field supplies the target. A colour in a context does
    # not colour the entire product.
    predefined = {'black': [0., 0., 0.], 'white': [1., 1., 1.], 'red': [1., 0., 0.],
                  'green': [0., 1., 0.], 'blue': [0., 0., 1.], 'yellow': [1., 1., 0.],
                  'magenta': [1., 0., 1.], 'cyan': [0., 1., 1.]}
    colours = {row['entity_id']: row['rgb'] for row in metadata.get('colours', [])}
    for eid in by_type['DRAUGHTING_PRE_DEFINED_COLOUR']:
        try:
            rgb = predefined.get(label(args(eid)[0]).lower())
            if rgb is not None:
                colours[eid] = rgb
        except (ValueError, IndexError):
            pass
    style_fields = {'PRESENTATION_STYLE_ASSIGNMENT': 0, 'PRESENTATION_STYLE_BY_CONTEXT': 0,
                    'SURFACE_STYLE_USAGE': 1, 'SURFACE_SIDE_STYLE': 1, 'SURFACE_STYLE_FILL_AREA': 0,
                    'FILL_AREA_STYLE': 1, 'FILL_AREA_STYLE_COLOUR': 1, 'CURVE_STYLE': 3,
                    'POINT_STYLE': 3, 'SURFACE_STYLE_RENDERING': 1,
                    'SURFACE_STYLE_RENDERING_WITH_PROPERTIES': 1, 'SURFACE_STYLE_BOUNDARY': 0}

    def style_colours(eid, trail=(), failures=None):
        failures = failures if failures is not None else []
        if eid in trail or len(trail) > 20:
            failures.append({'entity_id': eid, 'reason': 'cyclic_or_overlong_colour_style',
                             'evidence_path': [*trail, eid]})
            return []
        trail = (*trail, eid)
        if eid in colours:
            return [(eid, list(trail))]
        children = [ref for kind in types(eid) if kind in style_fields
                    for ref in field_refs(eid, style_fields[kind], kind)]
        if not children:
            failures.append({'entity_id': eid,
                'reason': 'missing_step_entity' if eid not in records else 'no_supported_colour_style_reference',
                'evidence_path': list(trail)})
            return []
        return [pair for child in children for pair in style_colours(child, trail, failures)]

    def context_occurrences(context, trail):
        """Resolve the context's SDR -> product-definition-shape -> NAUO."""
        if context in occurrence_records:
            return [(context, [*trail, context])]
        found = []
        for sdr in reverse[context]:
            if 'SHAPE_DEFINITION_REPRESENTATION' not in types(sdr) or context not in field_refs(sdr, 1):
                continue
            for pds in field_refs(sdr, 0):
                if 'PRODUCT_DEFINITION_SHAPE' in types(pds):
                    for occurrence in field_refs(pds, 2):
                        if occurrence in occurrence_records:
                            found.append((occurrence, [*trail, context, sdr, pds, occurrence]))
        return found

    containment_cache = {}

    def within_occurrences(target, occurrence_ids):
        for occurrence_id in occurrence_ids:
            for outer in direct.get(occurrence_id, {}).values():
                key = (outer['kind'], outer['id'], target['kind'])
                if key not in containment_cache:
                    local = TopTools_IndexedMapOfShape()
                    TopExp.MapShapes_s(maps[outer['kind']].FindKey(outer['id']), KINDS[target['kind']], local)
                    containment_cache[key] = local
                if containment_cache[key].Contains(maps[target['kind']].FindKey(target['id'])):
                    return True
        return False

    for kind in ('STYLED_ITEM', 'OVER_RIDING_STYLED_ITEM', 'CONTEXT_DEPENDENT_OVER_RIDING_STYLED_ITEM'):
        for eid in by_type[kind]:
            targets, target_paths, failures = [], [], []
            for target_id in field_refs(eid, 2):
                found, paths = endpoint(target_id, (eid,), failures)
                targets.extend(found)
                target_paths.extend(paths)
            colour_matches, style_failures = [], []
            for style_id in field_refs(eid, 1):
                colour_matches.extend(style_colours(style_id, (eid,), style_failures))
            failures.extend(style_failures)
            if not colour_matches:
                report['colour_bindings'].append(record(eid, [], target_paths,
                    colour_entity_id=None, rgb=None, candidate_targets=targets,
                    unresolved_links=failures or [{'entity_id': eid,
                        'reason': 'no_explicit_colour_style', 'evidence_path': [eid]}],
                    unresolved_reason='no_supported_colour_style', target_scope='unresolved'))
            for colour_id, colour_path in colour_matches:
                    selected_targets = targets
                    scope = 'all_transferred_occurrences_of_definition'
                    context_paths, context_failures, context_ids, occurrence_ids = [], [], [], []
                    for node in colour_path:
                        if 'PRESENTATION_STYLE_BY_CONTEXT' in types(node):
                            contexts = field_refs(node, 1)
                            if not contexts:
                                context_failures.append({'entity_id': node,
                                    'reason': 'missing_presentation_context', 'evidence_path': [eid, node]})
                            for context in contexts:
                                context_ids.append(context)
                                resolved = context_occurrences(context, (eid, node))
                                for occurrence, evidence in resolved:
                                    if occurrence in direct:
                                        occurrence_ids.append(occurrence)
                                        context_paths.append(evidence)
                                if not resolved or not all(occurrence in direct for occurrence, _ in resolved):
                                    context_failures.append({'entity_id': context,
                                        'reason': 'presentation_context_has_no_resolved_occurrence',
                                        'evidence_path': [eid, node, context]})
                    if kind == 'CONTEXT_DEPENDENT_OVER_RIDING_STYLED_ITEM':
                        context_failures.append({'entity_id': eid,
                            'reason': 'unsupported_context_dependent_override_schema', 'evidence_path': [eid]})
                    if context_ids:
                        scope = 'explicit_assembly_occurrence_context'
                        selected_targets = [target for target in targets if within_occurrences(target, occurrence_ids)]
                        if not selected_targets and not context_failures:
                            context_failures.append({'entity_id': eid,
                                'reason': 'styled_item_not_in_explicit_occurrence_context', 'evidence_path': [eid]})
                    if context_failures:
                        selected_targets = []
                    row = record(eid, selected_targets, [colour_path, *target_paths, *context_paths],
                        colour_entity_id=colour_id, rgb=colours[colour_id],
                        rule='styled_item.item and explicit presentation style chain',
                        target_scope=scope, context_entity_ids=context_ids,
                        context_occurrence_ids=sorted(set(occurrence_ids)),
                        unresolved_links=[*failures, *context_failures])
                    if context_failures:
                        row['status'] = 'unresolved_context'
                    elif selected_targets and failures:
                        row['status'] = 'partially_resolved'
                    report['colour_bindings'].append(row)

    # STEPCAF/AP214 writes material name/density as property definitions and
    # representations. The semantic property name is schema convention, never
    # used to identify a shape. Preserve density representation separately.
    for pdr in by_type['PROPERTY_DEFINITION_REPRESENTATION']:
        for prop in field_refs(pdr, 0):
            values = args(prop, 'PROPERTY_DEFINITION')
            if len(values) < 3:
                continue
            try:
                property_name = label(values[0]).lower()
                property_description = label(values[1], True)
            except ValueError:
                continue
            if property_name != 'material property':
                continue
            for representation in field_refs(pdr, 1):
                for value in field_refs(representation, 1):
                    if 'DESCRIPTIVE_REPRESENTATION_ITEM' not in types(value):
                        continue
                    name = safe_label(value)
                    targets, paths, failures = [], [], []
                    for definition in references(values[2]):
                        found, evidence = endpoint(definition, (value, representation, pdr, prop), failures)
                        targets.extend(found)
                        paths.extend(evidence)
                    row = record(value, targets, paths, name=name, unresolved_links=failures,
                        property_definition_id=prop, property_description=property_description,
                        rule='material property definition -> product definition -> shape representation',
                        target_scope='all_transferred_occurrences_of_definition')
                    if targets and failures:
                        row['status'] = 'partially_resolved'
                    report['material_bindings'].append(row)
    for eid in by_type['MATERIAL_DESIGNATION']:
        targets, paths, failures = [], [], []
        for definition in field_refs(eid, 1):
            found, evidence = endpoint(definition, (eid,), failures)
            targets.extend(found)
            paths.extend(evidence)
        row = record(eid, targets, paths, unresolved_links=failures,
            name=safe_label(eid), rule='material_designation.definitions explicit product endpoints',
            target_scope='all_transferred_occurrences_of_definition')
        if targets and failures:
            row['status'] = 'partially_resolved'
        report['material_bindings'].append(row)
    for eid in by_type['MATERIAL']:
        report['material_bindings'].append(record(eid, [], [], name=safe_label(eid),
            unresolved_reason='material_entity_has_no_supported_explicit_assignment'))
    return _summarize(report)
