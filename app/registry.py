"""Content-based registry extraction. No organization-specific clause numbers."""
from __future__ import annotations

import re
from .db import stable_id
from .domain import FunctionAssertion
from .semantic import authority, best_action, has_action, object_and_scope

PREFIX = re.compile(r'^\s*(?:\d+(?:\.\d+)*\.?\s+|[а-яa-z][.)]\s*)')
OWNER = re.compile(
    r'^(Главный аудитор|Директор(?:а|ы)?\s+.{2,200}?|Руководител(?:ь|и)\s+.{2,200}?|'
    r'(?:Отдел|Департамент|Управление|Служба|Комитет|Блок)\s+.{2,200}?)'
    r'(?=\s*[:;.]?$|\s+(?:обязан\w*|организу\w*|осуществля\w*|обеспечива\w*|контролиру\w*|'
    r'проводит|проводят|готовит|готовят|информиру\w*|формиру\w*|утвержда\w*|проверя\w*|нес[её]т|ведет|ведёт)\b)', re.I)
UNIT = re.compile(r'^((?:Департамент|Отдел|Управление|Служба|Блок)\s+[^.;|]{2,180}?)(?:\s*\(([А-ЯЁA-Z]{2,12})\))?\s*[.;:]?$', re.I)


def extract_units_and_functions(store, comparison_id, side):
    units, roles, functions = [], [], []
    document = None
    contexts = {}
    action_contexts = {}
    current_owner = 'Не установлен'
    for span in store.get_spans(comparison_id, side):
        if span['document_id'] != document:
            document = span['document_id']
            contexts = {}
            action_contexts = {}
            current_owner = 'Не установлен'
        label = span.get('clause_label')
        text = span['original_text'].strip()
        clean = PREFIX.sub('', text).strip()
        if label:
            ancestors = [key for key in contexts if label == key or label.startswith(key + '.')]
            current_owner = contexts[max(ancestors, key=len)] if ancestors else 'Не установлен'
        # A table row keeps its explicit owner independently of surrounding paragraphs.
        cells = [x.strip() for x in clean.split('|')]
        table_owner = None
        if len(cells) >= 2 and any(has_action(x) for x in cells[1:]) and cells[0]:
            table_owner = cells[0]
            clean = ' ; '.join(cells[1:])
        owner_match = OWNER.match(clean) if not table_owner else None
        if owner_match and (re.match(r'^(Директор|Руководител|Главный аудитор)', owner_match.group(1), re.I) or not has_action(owner_match.group(1))):
            current_owner = owner_match.group(1).strip(' :;.')
            if label:
                contexts[label] = current_owner
            store.add_role(comparison_id, side, current_owner, span['id'])
            roles.append({'name': current_owner, 'span_id': span['id']})
        unit = UNIT.match(PREFIX.sub('', text).strip())
        if unit:
            name = unit.group(1).strip()
            store.add_org_unit(comparison_id, side, name, unit.group(2), span['id'])
            units.append({'name': name, 'abbreviation': unit.group(2), 'span_id': span['id']})
            # Organizational headings describe entities, not executable duties.
            if not has_action(clean):
                continue
        owner = table_owner or current_owner
        if owner_match and clean.strip(' :;.') == owner_match.group(1).strip(' :;.'):
            continue
        if label and clean.endswith(':') and has_action(clean):
            action_contexts[label] = (best_action(clean), span['id'])
        parents = [key for key in action_contexts if label and label.startswith(key+'.')]
        inherited = action_contexts[max(parents,key=len)] if parents else None
        # Keep unknown ownership visible instead of dropping a valid responsibility.
        pieces = re.split(r';\s*|(?<=\.)\s+(?=[А-ЯЁ])|,\s+(?=(?:взаимодействуют|взаимодействует|контролирует|контролируют|готовит|готовят|формирует|формируют)\b)', clean)
        for atom_index, piece in enumerate(pieces):
            if re.search(r'\b(?:понимается|означает|определяется как)\b', piece, re.I):
                continue
            if (not has_action(piece) and not inherited) or len(piece.strip()) < 12:
                continue
            uses_context = not has_action(piece) and bool(inherited)
            action = inherited[0] if uses_context else best_action(piece)
            obj, scope = object_and_scope(piece, action)
            fn = FunctionAssertion(
                id=stable_id('fn', comparison_id, side, span['id'], str(atom_index), piece),
                side=side, owner=owner, action=action, object=obj, scope=scope,
                authority=authority(piece), text=piece.strip(), span_id=span['id'],
                document_id=span['document_id'], clause_label=label,
                context_span_id=inherited[1] if uses_context else None,
            )
            store.add_function(comparison_id, fn)
            functions.append(fn)
    return (list({x['name'].lower(): x for x in units}.values()), functions,
            list({x['name'].lower(): x for x in roles}.values()))
