"""Lossless, deterministic factoring of an already-public source bundle.

This is a representation change, not a summary, redactor, retrieval system, or
authority check. Callers must supply only SourceCorpus.prepare() public episodes.
No file, database, domain service, provider, or environment is accessed here.

Body-bearing note dictionaries move into ``documents`` under the SHA-256 of
their complete original JSON value. Their body strings move into ``texts``.
This also shares identical bodies across notes with different versions/metadata;
all original fields remain recoverable. Process order and non-note fields stay
in place. Reference-looking input dictionaries are escaped rather than mistaken
for generated references. Short bundles can grow because references cost space.
Reported JSON character/byte sizes are not measured provider token counts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math


SCHEMA_VERSION = 1
REFERENCE_KEY = '$pm_ref'
LITERAL_KEY = '$pm_literal'


def _json(value, *, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys,
                      separators=(',', ':'), allow_nan=False)


def _address(value):
    return 'sha256:' + hashlib.sha256(_json(value, sort_keys=True).encode('utf-8')).hexdigest()


def _validate_json(value, active=None, *, public=False):
    """Reject non-JSON/coerced values and obvious host-only inputs; never redact."""
    active = set() if active is None else active
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('non_finite_json_value')
        return
    if type(value) not in (dict, list):
        raise ValueError('non_json_value')
    if id(value) in active:
        raise ValueError('cyclic_json_value')
    active.add(id(value))
    try:
        if isinstance(value, dict):
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError('non_string_json_key')
                if public and (key.endswith('_host_only') or key.startswith('host_only_')
                               or key in {'host_fixture', 'fault_schedule'}):
                    raise ValueError('host_only_material_in_public_bundle')
                _validate_json(item, active, public=public)
        else:
            for item in value:
                _validate_json(item, active, public=public)
    finally:
        active.remove(id(value))


def _is_note(value):
    if not isinstance(value, dict) or not isinstance(value.get('body'), str):
        return False
    ref = value.get('ref')
    referenced = (isinstance(ref, dict) and isinstance(ref.get('id'), str)
                  and type(ref.get('version')) is int)
    direct = isinstance(value.get('id'), str) and type(value.get('version')) is int
    return referenced or direct or isinstance(value.get('title'), str)


def _ref(kind, key):
    return {REFERENCE_KEY: {'kind': kind, 'key': key}}


def project_public_bundle(bundle):
    """Return an independently owned projection of one list of public episodes.

    The dictionaries in ``documents`` preserve note fields, with only their body
    replaced by a text reference. ``expand_public_bundle`` verifies both content
    addresses and restores the exact JSON values. Obvious host-only fields fail
    closed instead of being silently removed; this cannot detect misclassified
    private prose, so it does not replace the domain's public allowlist.
    """
    if type(bundle) is not list or any(type(item) is not dict for item in bundle):
        raise ValueError('public_bundle_must_be_episode_list')
    _validate_json(bundle, public=True)
    documents, texts = {}, {}

    def encode(value):
        if isinstance(value, list):
            return [encode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if _is_note(value):
            key = _address(value)
            if key not in documents:
                body_key = _address(value['body'])
                texts.setdefault(body_key, value['body'])
                documents[key] = {field: _ref('text', body_key) if field == 'body' else encode(item)
                                  for field, item in value.items()}
            return _ref('document', key)
        if set(value) in ({REFERENCE_KEY}, {LITERAL_KEY}):
            return {LITERAL_KEY: [[key, encode(item)] for key, item in value.items()]}
        return {key: encode(item) for key, item in value.items()}

    processes = encode(bundle)
    return {'schema_version': SCHEMA_VERSION, 'documents': documents,
            'texts': texts, 'processes': processes}


def expand_public_bundle(projection):
    """Validate a projection and restore fresh original public JSON values."""
    _validate_json(projection, public=True)
    if (type(projection) is not dict
            or set(projection) != {'schema_version', 'documents', 'texts', 'processes'}
            or type(projection['schema_version']) is not int
            or projection['schema_version'] != SCHEMA_VERSION
            or type(projection['documents']) is not dict
            or type(projection['texts']) is not dict
            or type(projection['processes']) is not list):
        raise ValueError('invalid_source_projection')
    documents, texts = projection['documents'], projection['texts']
    for key, text in texts.items():
        if type(text) is not str or key != _address(text):
            raise ValueError('text_content_address_mismatch')
    resolved, resolving = {}, set()

    def document(key):
        if key not in documents:
            raise ValueError('missing_document_reference')
        if key in resolving:
            raise ValueError('cyclic_document_reference')
        if key not in resolved:
            resolving.add(key)
            try:
                value = decode(documents[key])
                _validate_json(value, public=True)
                if not _is_note(value) or _address(value) != key:
                    raise ValueError('document_content_address_mismatch')
                resolved[key] = value
            finally:
                resolving.remove(key)
        # Repeated references must not create shared mutable note objects.
        return deepcopy(resolved[key])

    def decode(value):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {REFERENCE_KEY}:
            reference = value[REFERENCE_KEY]
            if (type(reference) is not dict or set(reference) != {'kind', 'key'}
                    or type(reference['key']) is not str):
                raise ValueError('invalid_projection_reference')
            kind, key = reference['kind'], reference['key']
            if kind == 'document':
                return document(key)
            if kind == 'text':
                if key not in texts:
                    raise ValueError('missing_text_reference')
                return texts[key]
            raise ValueError('unknown_projection_reference_kind')
        if set(value) == {LITERAL_KEY}:
            pairs = value[LITERAL_KEY]
            if (type(pairs) is not list or any(type(pair) is not list or len(pair) != 2
                    or type(pair[0]) is not str for pair in pairs)
                    or len({pair[0] for pair in pairs}) != len(pairs)):
                raise ValueError('invalid_projection_literal')
            return {key: decode(item) for key, item in pairs}
        return {key: decode(item) for key, item in value.items()}

    result = decode(projection['processes'])
    # Also verify unreferenced entries; extra corrupt content cannot hide in tables.
    for key in documents:
        document(key)
    if any(type(item) is not dict for item in result):
        raise ValueError('invalid_projected_episode_list')
    _validate_json(result, public=True)
    return result


def projection_stats(bundle, projection=None):
    """Return compact JSON sizes and table counts, never a token estimate."""
    projection = project_public_bundle(bundle) if projection is None else projection
    if expand_public_bundle(projection) != bundle:
        raise ValueError('projection_does_not_restore_bundle')
    original, projected = _json(bundle), _json(projection)
    original_bytes, projected_bytes = len(original.encode('utf-8')), len(projected.encode('utf-8'))
    return {'original_json_chars': len(original), 'projected_json_chars': len(projected),
            'original_json_utf8_bytes': original_bytes, 'projected_json_utf8_bytes': projected_bytes,
            'json_char_ratio': len(projected) / len(original) if original else None,
            'json_utf8_byte_ratio': projected_bytes / original_bytes if original_bytes else None,
            'documents': len(projection['documents']), 'texts': len(projection['texts']),
            'roundtrip_equal': True, 'provider_tokens_measured': False}
