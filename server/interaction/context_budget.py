"""Pure shared budget for already projected dynamic context.

The budget is characters, not tokens or money. Callers may provide a renderer
cost so wrappers and JSON escaping count too. Whole optional records are omitted;
requests, source bindings and boundary metadata are never clipped. The returned
usage describes data provided to a model, not proof the model used it correctly.
No stores, configuration, models, or authorization are accessed here.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Callable


MAX_USAGE_ITEMS = 32
MAX_USAGE_ITEM_CHARS = 4000
_SENSITIVE = re.compile(
    r'(?i)(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
    r'\bBearer\s+\S+|\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+|'
    r'AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)|'
    r'(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|passwd|'
    r'secret|credential|authorization|cookie|密码|口令|密钥|令牌|凭据)'
    r'[\"\'\s]*(?:[:=：]|是|为)\s*\S+|https?://[^\s/@:]+:[^\s/@]+@)'
)
_SOURCE = frozenset(('stated', 'inferred', 'uncertain', 'user_stated', 'tool_verified'))
_EVIDENCE = frozenset(('linked', 'manual', 'legacy', 'user_confirmed', 'tool_verified'))
_PURPOSE = frozenset(('draft', 'reference', 'artifact'))


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _identifier(value: Any) -> str | int | None:
    if type(value) is int and value > 0:
        return value
    if (isinstance(value, str) and 0 < len(value) <= 256 and value.strip()
            and all(character.isprintable() for character in value)
            and not _SENSITIVE.search(value)):
        return value
    return None


def _version(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _increment(record: dict, key: str) -> None:
    record[key] = _count(record.get(key)) + 1


def _qualifications(data: dict) -> dict:
    """Existing upstream omissions survive the shared budget, without raw text."""
    result = {}
    thread = _mapping(data.get('companion_thread'))
    projection = _mapping(thread.get('projection_budget'))
    for name in ('omitted_event_count', 'omitted_history_event_count'):
        if type(projection.get(name)) is int and projection[name] >= 0:
            result['thread_' + name] = projection[name]
    histories = thread.get('event_histories')
    if isinstance(histories, list):
        # Count omitted versions only in history bundles actually still supplied.
        # Dropped whole bundles have their separate omitted-history-event count.
        result['thread_omitted_history_version_count'] = sum(
            _count(_mapping(history).get('omitted_version_count')) for history in histories)
    activity = _mapping(data.get('activity_data'))
    if type(activity.get('omitted_materials')) is int and activity['omitted_materials'] >= 0:
        result['omitted_materials'] = activity['omitted_materials']
    note = _mapping(activity.get('note_data'))
    if type(note.get('body_truncated')) is bool:
        result['upstream_note_body_truncated'] = note['body_truncated']
    if type(note.get('body_redacted')) is bool:
        result['upstream_note_body_redacted'] = note['body_redacted']
    materials = activity.get('material_data')
    if isinstance(materials, list):
        result['upstream_material_bodies_truncated'] = sum(
            _mapping(item).get('body_truncated') is True for item in materials)
        result['upstream_material_bodies_redacted'] = sum(
            _mapping(item).get('body_redacted') is True for item in materials)
    return result


def _usage_items(data: dict) -> tuple[list[dict], int]:
    """Whitelist identities after pruning; do not export titles or evidence text."""
    candidates: list[dict] = []

    def add(kind, identifier, reason, *, version=None, thread_id=None, purpose=None,
            source=None, evidence_state=None, body_included=None):
        identifier = _identifier(identifier)
        if identifier is None:
            return
        item = {'kind': kind, 'id': identifier, 'reason': reason}
        if _version(version) is not None:
            item['version'] = version
        if _identifier(thread_id) is not None:
            item['thread_id'] = thread_id
        if purpose in _PURPOSE:
            item['purpose'] = purpose
        if source in _SOURCE:
            item['source'] = source
        if evidence_state in _EVIDENCE:
            item['evidence_state'] = evidence_state
        if type(body_included) is bool:
            item['body_included'] = body_included
        candidates.append(item)

    thread = _mapping(data.get('companion_thread'))
    thread_id = thread.get('id')
    if thread:
        add('thread', thread_id, 'selected_matter', version=thread.get('revision'))

    activity = _mapping(data.get('activity_data'))
    # Outer bindings belong to run bookkeeping and can be hidden by a model
    # projection. Only identities retained inside the supplied activity count.
    source_context = _mapping(activity.get('material_source'))
    activity_thread = _mapping(activity.get('thread'))
    activity_thread_id = source_context.get('thread_id') or activity_thread.get('id') or thread_id
    note_data = _mapping(activity.get('note_data'))
    note_ref = _mapping(note_data.get('ref'))
    if note_data:
        add('note', note_ref.get('id') or note_data.get('id'), 'explicit_note_request',
            version=note_ref.get('version') or note_data.get('selected_version'),
            thread_id=activity_thread_id, purpose=source_context.get('purpose'),
            body_included=isinstance(note_data.get('body'), str))
    for item in activity.get('material_data', []) if isinstance(activity.get('material_data'), list) else []:
        item = _mapping(item)
        ref = _mapping(item.get('ref'))
        add('note', ref.get('id'), 'explicit_material_request', version=ref.get('version'),
            thread_id=activity_thread_id, purpose=item.get('purpose'),
            body_included=isinstance(item.get('body'), str))
    # A removed body still has its retained source identity/metadata.
    retained_ref = _mapping(source_context.get('ref'))
    if retained_ref:
        add('note', retained_ref.get('id'), 'metadata_only', version=retained_ref.get('version'),
            thread_id=source_context.get('thread_id'), purpose=source_context.get('purpose'),
            body_included=False)
    note = _mapping(activity.get('note'))
    if note:
        add('note', note.get('id'), 'metadata_only', version=note.get('selected_version'),
            thread_id=activity_thread_id, purpose=source_context.get('purpose'), body_included=False)
    for ref in activity.get('object_refs', []) if isinstance(activity.get('object_refs'), list) else []:
        ref = _mapping(ref)
        if ref.get('type') == 'note':
            add('note', ref.get('id'), 'metadata_only', version=ref.get('version'),
                thread_id=activity_thread_id, purpose=source_context.get('purpose'), body_included=False)
    for item in activity.get('materials', []) if isinstance(activity.get('materials'), list) else []:
        item = _mapping(item)
        add('note', item.get('note_id'), 'metadata_only', version=item.get('version'),
            thread_id=item.get('thread_id') or activity_thread_id, purpose=item.get('purpose'),
            body_included=False)
    for event in thread.get('events', []) if isinstance(thread.get('events'), list) else []:
        event = _mapping(event)
        add('thread_event', event.get('id'), 'matter_recall', version=event.get('version'),
            thread_id=thread_id, source=event.get('source'),
            evidence_state=_mapping(event.get('evidence')).get('state'))
    for history in thread.get('event_histories', []) if isinstance(thread.get('event_histories'), list) else []:
        history = _mapping(history)
        for item in history.get('versions', []) if isinstance(history.get('versions'), list) else []:
            item = _mapping(item)
            add('thread_event', history.get('event_id'), 'matter_recall', version=item.get('version'),
                thread_id=thread_id, source=item.get('source'),
                evidence_state=_mapping(item.get('evidence')).get('state'))
    memories = _mapping(data.get('long_term_memory'))
    for group in ('stated', 'inferred', 'uncertain'):
        for item in memories.get(group, []) if isinstance(memories.get(group), list) else []:
            item = _mapping(item)
            add('memory', item.get('id'), 'query_retrieval', source=item.get('source', group),
                evidence_state=_mapping(item.get('evidence')).get('state'))
    policy = _mapping(data.get('response_policy'))
    for identifier in policy.get('preference_claim_ids', []) if isinstance(policy.get('preference_claim_ids'), list) else []:
        add('preference', identifier, 'scene_scope')
    for group in ('active_tasks', 'tasks'):
        for item in data.get(group, []) if isinstance(data.get(group), list) else []:
            item = _mapping(item)
            add('task', item.get('id'), 'active_task', version=item.get('version'))

    # First occurrence has the richest description: bodies precede metadata.
    unique, seen = [], set()
    for item in candidates:
        identity = (item['kind'], item['id'], item.get('version'), item.get('thread_id'), item.get('purpose'))
        if identity not in seen:
            seen.add(identity)
            unique.append(item)
    items = []
    for item in unique:
        if len(items) >= MAX_USAGE_ITEMS or len(_compact([*items, item])) > MAX_USAGE_ITEM_CHARS:
            continue
        items.append(item)
    return items, len(unique) - len(items)


def limit_context(context: dict, *, enabled: bool = True, max_chars: int = 12000,
                  measure: Callable[[dict], int] | None = None) -> dict:
    """Return a copied, bounded context and identifier-only injection metadata.

    ``measure`` synchronously counts the caller's actual dynamic rendering. It
    receives a copy and must return a nonnegative integer. A budget too small for
    the remaining request/bindings/boundaries raises ValueError, without clipping
    them. Disabled gathering belongs to callers; disabled usage never lists even
    mistakenly supplied records. This function performs no reads or writes.
    """
    if not isinstance(context, dict):
        raise TypeError('context must be a dictionary')
    if type(enabled) is not bool:
        raise TypeError('enabled must be a boolean')
    if type(max_chars) is not int or max_chars < 0:
        raise ValueError('max_chars must be a nonnegative integer')
    if measure is not None and not callable(measure):
        raise TypeError('measure must be callable')
    data = deepcopy(context)
    _compact(data)  # Reject NaN/non-JSON objects even when a custom renderer is used.
    upstream = _qualifications(data) if enabled else {}
    policy = _mapping(data.get('response_policy'))
    duplicate_thread = policy.pop('companion_thread', None)
    if not data.get('companion_thread') and isinstance(duplicate_thread, dict) and duplicate_thread:
        data['companion_thread'] = duplicate_thread
    omitted: dict[str, int] = {}

    def cost():
        value = len(_compact(data)) if measure is None else measure(deepcopy(data))
        if type(value) is not int or value < 0:
            raise ValueError('measure must return a nonnegative integer character count')
        return value

    def pop_item(record, name, category, *, oldest=False):
        items = record.get(name)
        if not isinstance(items, list) or not items:
            return False
        items.pop(0 if oldest else -1)
        omitted[category] = omitted.get(category, 0) + 1
        return True

    def prune():
        if pop_item(policy, 'examples', 'response_examples'):
            return True
        if pop_item(data, 'history', 'history_messages', oldest=True):
            return True
        memories = _mapping(data.get('long_term_memory'))
        for group in ('uncertain', 'inferred', 'stated'):
            if pop_item(memories, group, 'memory_' + group):
                if not any(memories.get(name) for name in ('stated', 'inferred', 'uncertain')):
                    memories['status'] = 'omitted_by_budget'
                return True
        for group in ('active_tasks', 'tasks'):
            if pop_item(data, group, 'tasks'):
                if not data.get('active_tasks') and not data.get('tasks'):
                    data['task_status'] = 'omitted_by_budget'
                return True
        thread = _mapping(data.get('companion_thread'))
        if pop_item(thread, 'event_histories', 'thread_histories'):
            projection = thread.setdefault('projection_budget', {})
            if isinstance(projection, dict):
                _increment(projection, 'omitted_history_event_count')
            if not thread['event_histories']:
                thread['history_status'] = 'omitted_by_budget'
            return True
        if pop_item(thread, 'events', 'thread_events'):
            projection = thread.setdefault('projection_budget', {})
            if isinstance(projection, dict):
                _increment(projection, 'omitted_event_count')
            return True
        activity = _mapping(data.get('activity_data'))
        if pop_item(activity, 'materials', 'material_metadata'):
            _increment(activity, 'omitted_material_metadata')
            return True
        refs = activity.get('object_refs')
        if isinstance(refs, list):
            known = []
            for value in (_mapping(activity.get('material_source')).get('ref'),
                          _mapping(activity.get('note_data')).get('ref')):
                if isinstance(value, dict):
                    known.append(value)
            if thread.get('id') and thread.get('revision'):
                known.append({'type': 'thread', 'id': thread['id'], 'version': thread['revision']})
            for index, ref in enumerate(refs):
                if ref in known or ref in refs[:index]:
                    refs.pop(index)
                    omitted['duplicate_object_refs'] = omitted.get('duplicate_object_refs', 0) + 1
                    return True
        if pop_item(activity, 'material_data', 'material_bodies'):
            _increment(activity, 'omitted_materials')
            activity['included_material_body'] = bool(activity['material_data'])
            return True
        if isinstance(activity.get('note_data'), dict) and activity['note_data']:
            note_data = activity['note_data']
            # Keep the fixed source when callers supplied only the body's ref.
            if isinstance(note_data.get('ref'), dict) and not data.get('activity_note_ref'):
                data['activity_note_ref'] = deepcopy(note_data['ref'])
            activity['note_data'] = None
            activity['included_note_body'] = False
            activity['note_status'] = 'omitted_by_budget'
            omitted['note_bodies'] = omitted.get('note_bodies', 0) + 1
            return True
        return False

    used_chars = cost()
    while used_chars > max_chars:
        if not prune():
            raise ValueError('context budget too small for request, source identity and boundaries')
        used_chars = cost()
    items, omitted_usage_items = _usage_items(data) if enabled else ([], 0)
    qualifications = _qualifications(data) if enabled else {}
    qualifications.update({key: value for key, value in upstream.items() if key.startswith('upstream_')})
    return {'data': data, 'usage': {
        'schema_version': 1, 'enabled': enabled,
        'budget': {'scope': 'dynamic_context', 'unit': 'characters', 'max_chars': max_chars,
                   'used_chars': used_chars, 'omitted_items': sum(omitted.values())},
        'omitted_counts': omitted, 'items': items,
        'items_truncated': bool(omitted_usage_items), 'omitted_usage_items': omitted_usage_items,
        'qualifications': qualifications, 'model_use_verified': False,
        'context_is_untrusted_data': True, 'grants_authorization': False,
    }}
