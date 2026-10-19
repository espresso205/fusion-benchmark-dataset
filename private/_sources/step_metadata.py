"""Selected Part 21 labels and explicit product usage references, without inference."""
import re
from collections import Counter
from pathlib import Path


def statements(text):
    # Consume strings and comments atomically: embedded semicolons are not separators.
    tokens = re.finditer(r"'(?:''|[^'])*'|/\*.*?\*/|;|[^'/;]+|.", text, re.S)
    current = []
    for match in tokens:
        token = match.group()
        if token.startswith('/*'):
            current.append(' ')
        elif token == ';':
            yield ''.join(current).strip()
            current = []
        else:
            current.append(token)


def decode_label(raw):
    # Preserve unsupported legacy page directives literally rather than guessing.
    def decode_token(match):
        token = match.group()
        if token == "''":
            return "'"
        if token == '\\\\':
            return '\\'
        codec = 'utf-16-be' if match[1] == '2' else 'utf-32-be'
        return bytes.fromhex(match[2]).decode(codec)
    # Decode once. Escaped backslashes must not start a Unicode directive, and
    # two apostrophes produced by Unicode decoding must not be collapsed.
    return re.sub(r"''|\\\\|\\X([24])\\([0-9A-Fa-f]+)\\X0\\", decode_token, raw)


def arguments(body):
    """Split only top-level commas; labels and aggregates remain atomic."""
    fields, current, depth = [], [], 0
    for m in re.finditer(r"'(?:''|[^'])*'|[(),]|[^'(),]+", body, re.S):
        t = m.group()
        if t == ',' and depth == 0:
            fields.append(''.join(current).strip()); current = []
            continue
        if t == '(':
            depth += 1
        elif t == ')':
            depth -= 1
        current.append(t)
    if depth != 0:
        raise ValueError('Unbalanced entity arguments')
    fields.append(''.join(current).strip())
    return fields


def label(token, optional=False):
    if optional and token == '$':
        return None
    if not re.fullmatch(r"'(?:''|[^'])*'", token, re.S):
        raise ValueError('Expected STEP string')
    return decode_label(token[1:-1])


def reference(token):
    if not re.fullmatch(r'#\d+', token):
        raise ValueError('Expected STEP entity reference')
    return int(token[1:])


def references(body):
    """Entity references outside Part 21 strings/comments, in source order."""
    return [int(match.group()[1:]) for match in re.finditer(
        r"'(?:''|[^'])*'|/\*.*?\*/|#\d+", body, re.S)
        if match.group().startswith('#')]


def entity_record(statement):
    """Read simple and complex instances without treating typed values as entities."""
    match = re.fullmatch(r'#(\d+)\s*=\s*(.*)', statement, re.S)
    if not match:
        return None
    rhs = match[2].strip()
    complex_entity = rhs.startswith('(')
    source = rhs[1:-1] if complex_entity else rhs
    components = []
    offset = 0
    while offset < len(source):
        start = re.match(r'\s*([A-Z0-9_]+)\s*\(', source[offset:], re.I)
        if not start:
            if source[offset:].strip():
                raise ValueError('Malformed STEP entity component')
            break
        body_start = offset + start.end()
        depth = 1
        end = None
        for token in re.finditer(r"'(?:''|[^'])*'|[()]|[^'()]+", source[body_start:], re.S):
            if token.group() == '(':
                depth += 1
            elif token.group() == ')':
                depth -= 1
                if depth == 0:
                    end = body_start + token.start()
                    break
        if end is None:
            raise ValueError('Unbalanced STEP entity component')
        body = source[body_start:end]
        components.append({'entity_type': start[1].upper(), 'raw_arguments': body,
                           'references': references(body)})
        offset = end + 1
    if not components:
        raise ValueError('Empty STEP entity instance')
    kinds = [c['entity_type'] for c in components]
    body = source if complex_entity else components[0]['raw_arguments']
    return {'entity_id': int(match[1]), 'entity_type': kinds[0] if len(kinds) == 1 else 'COMPLEX',
            'entity_types': kinds, 'components': components, 'raw_arguments': body,
            'references': references(body)}


def semantic_entity_role(entity_types):
    """Classify annotation families, not incidental geometry/style supertypes.

    Shape aspects and identified-item usages can support PMI, feature structure,
    or representation organization. Their presence alone is not engineering PMI.
    In particular DIMENSIONAL_EXPONENTS is a unit definition, and the generic
    GEOMETRIC_REPRESENTATION_* bases also occur in ordinary rational surfaces.
    """
    for kind in entity_types:
        words = set(kind.split('_'))
        if kind == 'DIMENSIONAL_EXPONENTS':
            continue
        if (words & {'DIMENSION', 'DIMENSIONAL', 'TOLERANCE', 'DATUM', 'ANNOTATION', 'LEADER', 'CALLOUT'}
                or kind.startswith(('DRAUGHTING_MODEL', 'DRAUGHTING_TEXT', 'SURFACE_FINISH',
                                    'SURFACE_TEXTURE', 'SURFACE_ROUGHNESS'))):
            return 'engineering_annotation_or_tolerance'
    if any('SHAPE_ASPECT' in kind or kind in ('GEOMETRIC_ITEM_SPECIFIC_USAGE',
                                             'ITEM_IDENTIFIED_REPRESENTATION_USAGE')
           for kind in entity_types):
        return 'support_only_not_engineering_annotation'
    return None


def step_header_metadata(path):
    text = Path(path).read_bytes().decode('latin-1')
    result = {'file_name': None, 'timestamp': None, 'file_description': [],
              'products': [], 'product_formations': [], 'product_definitions': [],
              'assembly_occurrences': [], 'colours': [], 'materials': [], 'presentation_layers': [],
              'uncertainty_measures': [],
              # PMI entities are retained verbatim with references.  Their
              # schemas differ across AP203/AP214/AP242, so this audit layer
              # deliberately avoids inventing a dimension or tolerance value.
              'pmi_entities': [], 'semantic_support_entities': [],
              'unparsed_entities': [],
              'parse_errors': [], 'status': 'parsed_selected_textual_entities',
              'scope': 'Selected headers, PRODUCT, PRODUCT_DEFINITION, formations and NEXT_ASSEMBLY_USAGE_OCCURRENCE; no placement-to-topology binding',
              'string_encoding_policy': 'X2/X4 decoded; other STEP directives retained literally'}
    section = None
    entity_types = Counter()
    entity_ids = {}
    string = r"'((?:''|[^'])*)'"
    for statement in statements(text):
        upper = statement.upper()
        if upper in ('HEADER', 'DATA', 'ENDSEC'):
            section = upper
            continue
        if re.fullmatch(r'DATA\s*\(.*\)', statement, re.S | re.I):
            section = 'DATA'
            continue
        if section == 'HEADER':
            match = re.fullmatch(r'(FILE_NAME|FILE_DESCRIPTION|FILE_SCHEMA)\s*\((.*)\)', statement, re.S | re.I)
            if not match:
                continue
            kind, body = match[1].upper(), match[2]
            values = [decode_label(v) for v in re.findall(string, body)]
            if kind == 'FILE_NAME':
                result['file_name'] = values[0] if values else None
                result['timestamp'] = values[1] if len(values) > 1 else None
            elif kind == 'FILE_DESCRIPTION':
                # Description aggregate precedes the implementation-level string.
                result['file_description'] = values[:-1]
                result['implementation_level'] = values[-1] if values else None
            else:
                result['schemas'] = values
        elif section == 'DATA':
            try:
                raw_record = entity_record(statement)
            except ValueError as exc:
                result['parse_errors'].append({'statement': statement, 'error': str(exc)})
                continue
            if raw_record:
                result['unparsed_entities'].append(raw_record)
                for kind in raw_record['entity_types']:
                    entity_types[kind] += 1
                entity_ids[raw_record['entity_id']] = raw_record['entity_type']
                semantic_role = semantic_entity_role(raw_record['entity_types'])
                if semantic_role == 'engineering_annotation_or_tolerance':
                    result['pmi_entities'].append(dict(raw_record,
                        semantic_role=semantic_role,
                        semantic_status='raw_entity_preserved_requires_schema_specific_resolution'))
                elif semantic_role == 'support_only_not_engineering_annotation':
                    result['semantic_support_entities'].append(dict(raw_record,
                        semantic_role=semantic_role,
                        semantic_status='support_reference_preserved_not_evidence_of_engineering_PMI'))
            entity_match = re.match(r'#(\d+)\s*=\s*([A-Z0-9_]+)\s*\(', statement, re.I | re.S)
            if entity_match:
                eid, etype = int(entity_match[1]), entity_match[2].upper()
                # Capture common AP dimensional/geometric annotation entities
                # as structured raw records.  This makes PMI loss explicit and
                # gives downstream DXF writers stable entity IDs to reference.
            match = re.match(r'#(\d+)\s*=\s*PRODUCT\s*\(\s*' + string + r'\s*,\s*' + string + r'\s*,\s*' + string + r'\s*,', statement, re.I | re.S)
            if match:
                result['products'].append(dict(entity_id=int(match[1]), **{
                    key: decode_label(match[i]) for i, key in enumerate(('id', 'name', 'description'), 2)}))
            if entity_match and entity_match[2].upper() == 'COLOUR_RGB':
                try:
                    args = arguments(statement[statement.index('(') + 1:-1])
                    result['colours'].append({'entity_id': eid, 'name': label(args[0], True),
                                              'rgb': [float(args[1]), float(args[2]), float(args[3])]})
                except (ValueError, IndexError) as exc:
                    result['parse_errors'].append({'entity_id': eid, 'entity_type': 'COLOUR_RGB', 'error': str(exc)})
            if entity_match and entity_match[2].upper() == 'MATERIAL':
                try:
                    args = arguments(statement[statement.index('(') + 1:-1])
                    result['materials'].append({'entity_id': eid, 'name': label(args[0], True)})
                except (ValueError, IndexError) as exc:
                    result['parse_errors'].append({'entity_id': eid, 'entity_type': 'MATERIAL', 'error': str(exc)})
            if entity_match and entity_match[2].upper() == 'PRESENTATION_LAYER_ASSIGNMENT':
                try:
                    args = arguments(statement[statement.index('(') + 1:-1])
                    refs = [reference(x.strip()) for x in args[2].strip('()').split(',') if x.strip()]
                    result['presentation_layers'].append({'entity_id': eid, 'name': label(args[0], True),
                                                          'description': label(args[1], True), 'assigned_entity_ids': refs})
                except (ValueError, IndexError) as exc:
                    result['parse_errors'].append({'entity_id': eid, 'entity_type': 'PRESENTATION_LAYER_ASSIGNMENT', 'error': str(exc)})
            if entity_match and entity_match[2].upper() == 'UNCERTAINTY_MEASURE_WITH_UNIT':
                try:
                    body = statement[statement.index('(') + 1:-1]
                    args = arguments(body)
                    number = re.search(r'[-+]?\d+(?:\.\d*)?(?:[Ee][-+]?\d+)?', args[0])
                    units = references(body)
                    if not number or not units:
                        raise ValueError('missing numeric measure or unit reference')
                    result['uncertainty_measures'].append({'entity_id': eid, 'value': float(number.group()),
                                                          'unit_entity_id': units[-1], 'raw_arguments': body})
                except (ValueError, IndexError) as exc:
                    result['parse_errors'].append({'entity_id': eid, 'entity_type': 'UNCERTAINTY_MEASURE_WITH_UNIT', 'error': str(exc)})
            m = re.fullmatch(r'#(\d+)\s*=\s*(NEXT_ASSEMBLY_USAGE_OCCURRENCE|PRODUCT_DEFINITION|PRODUCT_DEFINITION_FORMATION(?:_WITH_SPECIFIED_SOURCE)?)\s*\((.*)\)', statement, re.I | re.S)
            if not m:
                continue
            eid, kind = int(m[1]), m[2].upper()
            try:
                args = arguments(m[3])
                count = 6 if kind == 'NEXT_ASSEMBLY_USAGE_OCCURRENCE' else (3 if kind == 'PRODUCT_DEFINITION_FORMATION' else 4)
                if len(args) != count:
                    raise ValueError(f'{kind} requires {count} arguments, got {len(args)}')
                record = {'entity_id': eid, 'id': label(args[0])}
                if kind == 'NEXT_ASSEMBLY_USAGE_OCCURRENCE':
                    record.update(name=label(args[1]), description=label(args[2],True),
                                  relating_product_definition_id=reference(args[3]),
                                  related_product_definition_id=reference(args[4]),
                                  reference_designator=label(args[5],True))
                    result['assembly_occurrences'].append(record)
                elif kind == 'PRODUCT_DEFINITION':
                    record.update(description=label(args[1],True), formation_id=reference(args[2]),
                                  frame_of_reference_id=reference(args[3]))
                    result['product_definitions'].append(record)
                else:
                    record.update(description=label(args[1],True), product_id=reference(args[2]),
                                  source=args[3] if count == 4 else None)
                    result['product_formations'].append(record)
            except ValueError as exc:
                result['parse_errors'].append({'entity_id':eid, 'entity_type':kind, 'error':str(exc)})
    parsed_ids = {p['entity_id'] for p in result['products']} | {x['entity_id'] for key in ('colours','materials','presentation_layers','assembly_occurrences','product_definitions','product_formations','pmi_entities') for x in result[key]}
    for item in result['unparsed_entities']:
        item['parsed_semantics'] = item['entity_id'] in parsed_ids
    # Resolve after collecting everything: Part 21 entity order is arbitrary.
    products = {p['entity_id'] for p in result['products']}
    formations = {f['entity_id']:f['product_id'] for f in result['product_formations']}
    definitions = {}
    for d in result['product_definitions']:
        pid = formations.get(d['formation_id'])
        d['product_id'] = pid if pid in products else None
        definitions[d['entity_id']] = d['product_id']
    for occurrence in result['assembly_occurrences']:
        occurrence['relating_product_id'] = definitions.get(occurrence['relating_product_definition_id'])
        occurrence['related_product_id'] = definitions.get(occurrence['related_product_definition_id'])
    result['assembly_resolution_status'] = 'resolved_product_links' if result['assembly_occurrences'] and all(
        o['relating_product_id'] is not None and o['related_product_id'] is not None for o in result['assembly_occurrences']) else (
        'unresolved_product_definition_links' if result['assembly_occurrences'] else 'no_occurrences')
    if result['parse_errors']:
        result['assembly_resolution_status'] = 'incomplete_parse'
    result['entity_type_counts'] = dict(sorted(entity_types.items()))
    result['entity_count'] = len(entity_ids)
    result['pmi_count'] = len(result['pmi_entities'])
    result['semantic_support_count'] = len(result['semantic_support_entities'])
    result['pmi_classification_policy'] = ('Engineering dimensions/tolerances/datums/annotations and explicit drawing annotation families; '
        'generic geometry, unit exponents, predefined colours/fonts excluded; shape-aspect/identified-item support counted separately')
    result['unparsed_entity_types'] = sorted(set(entity_types) - {
        'PRODUCT', 'PRODUCT_DEFINITION_FORMATION', 'PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE',
        'PRODUCT_DEFINITION', 'NEXT_ASSEMBLY_USAGE_OCCURRENCE'})
    return result
