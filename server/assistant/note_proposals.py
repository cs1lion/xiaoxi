"""Bounded, process-local previews of explicit revisions to saved notes.

Preview, read, edit and reject never write a note. Confirmation performs one CAS
update against the immutable base version, then consumes the token. Neither note
selection nor a newer note version can retarget a candidate. Conflicts require a
new proposal based on the new original; there is no refresh or automatic retry.
"""

from copy import deepcopy
from contextlib import contextmanager, ExitStack
import threading
import time
import uuid

from .notes import NoteStore


class NoteProposalConflict(ValueError):
    """The proposal version or its immutable note base is no longer current."""


class NoteProposalService:
    TTL_SECONDS = 600
    MAX_PENDING = 128
    MAX_TITLE = 160
    MAX_BODY = 20000
    MAX_SOURCE_TEXT = 12000
    MAX_IDENTIFIER = 256

    def __init__(self, store, *, clock=None, on_confirm=None, source_validator=None, source_guard=None):
        if on_confirm is not None and not callable(on_confirm):
            raise TypeError('on_confirm must be callable')
        if source_validator is not None and not callable(source_validator):
            raise TypeError('source_validator must be callable')
        if source_guard is not None and not callable(source_guard):
            raise TypeError('source_guard must be callable')
        self._store = store
        self._clock = time.time if clock is None else clock
        self._on_confirm = on_confirm
        self._source_validator = source_validator
        self._source_guard = source_guard
        self._lock = threading.RLock()
        self._pending = {}

    @classmethod
    def _identifier(cls, value, name):
        if (not isinstance(value, str) or not value.strip()
                or len(value) > cls.MAX_IDENTIFIER or '\x00' in value):
            raise ValueError('invalid ' + name)

    @staticmethod
    def _usable(value, name, maximum):
        if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                or '\x00' in value
                or not any(char.isprintable() and not char.isspace() for char in value)):
            raise ValueError('invalid ' + name)

    @classmethod
    def _draft(cls, title, body):
        cls._usable(title, 'title', cls.MAX_TITLE)
        cls._usable(body, 'body', cls.MAX_BODY)
        # Match NoteStore normalization so the review shows exactly what saves.
        return title.strip(), body.strip()

    @classmethod
    def _source_context(cls, source, note_id, version):
        """Keep only the complete captured binding, detached from its caller."""
        keys = {'scope', 'thread_id', 'thread_revision', 'material_id', 'ref', 'purpose', 'status'}
        if not isinstance(source, dict) or set(source) != keys:
            raise ValueError('invalid source_context')
        if source['scope'] not in ('thread', 'personal') or source['status'] != 'selected':
            raise ValueError('invalid source_context')
        ref = source['ref']
        if not isinstance(ref, dict) or set(ref) != {'type', 'id', 'version'} or ref['type'] != 'note':
            raise ValueError('invalid source_context ref')
        cls._identifier(ref['id'], 'source note_id')
        NoteStore._version(ref['version'], 'source version')
        if ref['id'] != note_id or ref['version'] != version:
            raise ValueError('source_context note mismatch')
        if source['scope'] == 'thread':
            cls._identifier(source['thread_id'], 'thread_id')
            cls._identifier(source['material_id'], 'material_id')
            NoteStore._version(source['thread_revision'], 'thread_revision')
            if source['purpose'] != 'draft':
                raise ValueError('invalid source_context purpose')
        elif any(source[key] is not None for key in ('thread_id', 'thread_revision', 'material_id', 'purpose')):
            raise ValueError('invalid personal source_context')
        return deepcopy(source)

    @classmethod
    def _binding(cls, note_id, version, *, source_context=None, scope=None,
                 thread_id=None, thread_revision=None, material_id=None, purpose=None):
        supplied = {'scope': scope, 'thread_id': thread_id, 'thread_revision': thread_revision,
                    'material_id': material_id, 'purpose': purpose}
        if source_context is not None:
            source = cls._source_context(source_context, note_id, version)
            if any(value is not None and value != source[key] for key, value in supplied.items()):
                raise ValueError('source_context metadata mismatch')
            return source
        if scope is None:
            if any(value is not None for value in (thread_revision, material_id, purpose)):
                raise ValueError('scope required for source metadata')
            return None  # Existing direct callers have no working-source binding.
        return cls._source_context({**supplied, 'ref': {'type': 'note', 'id': note_id, 'version': version},
                                    'status': 'selected'}, note_id, version)

    def _validate_source(self, source):
        if source is None or self._source_validator is None:
            return
        try:
            valid = self._source_validator(deepcopy(source))
        except Exception:
            raise NoteProposalConflict('note source changed') from None
        if valid is not True:
            raise NoteProposalConflict('note source changed')

    @contextmanager
    def _confirmation_source(self, source):
        """Retain the captured draft association until its one note CAS ends."""
        if source is None or self._source_guard is None:
            self._validate_source(source)
            yield
            return
        with ExitStack() as stack:
            try:
                stack.enter_context(self._source_guard(deepcopy(source)))
            except Exception:
                raise NoteProposalConflict('note source changed') from None
            yield

    def _expire(self):
        now = self._clock()
        for token in list(self._pending):
            if self._pending[token]['expires_at'] <= now:
                del self._pending[token]
        return now

    def _candidate(self, token):
        self._expire()
        if not isinstance(token, str) or token not in self._pending:
            raise KeyError(token)
        return self._pending[token]

    @staticmethod
    def _check_version(candidate, expected_version):
        NoteStore._version(expected_version, 'expected_version')
        if candidate['version'] != expected_version:
            raise NoteProposalConflict('proposal version conflict')

    def _live(self, candidate, *, check_base=False, check_source=True):
        if check_source:
            self._validate_source(candidate['source_context'])
        try:
            note = self._store.get(candidate['note_id'])
        except KeyError:
            self.discard_note(candidate['note_id'])
            raise
        if check_base and note['version'] != candidate['base_note_version']:
            raise NoteProposalConflict('note base version conflict')
        return note

    def propose(self, note_id, version, title, body, source_text, *, thread_id=None,
                source_context=None, scope=None, thread_revision=None, material_id=None, purpose=None):
        """Review an existing current version with the actual original request.

        source_text is supplied by the server from user input, never synthesized
        by the model. thread_id is optional server-captured receipt context, not
        model output or permission to move the proposal to another object.
        """
        with self._lock:
            self._identifier(note_id, 'note_id')
            NoteStore._version(version)
            title, body = self._draft(title, body)
            self._usable(source_text, 'source_text', self.MAX_SOURCE_TEXT)
            if thread_id is not None:
                self._identifier(thread_id, 'thread_id')
            source = self._binding(note_id, version, source_context=source_context, scope=scope,
                thread_id=thread_id, thread_revision=thread_revision, material_id=material_id, purpose=purpose)
            self._validate_source(source)
            current = self._store.get(note_id)
            if current['version'] != version:
                raise NoteProposalConflict('note base version conflict')
            original = self._store.get_version(note_id, version)
            # A different connection may edit between the two reads. Preserve
            # the immutable original and reject that race rather than refresh.
            if self._store.get(note_id)['version'] != version:
                raise NoteProposalConflict('note base version conflict')
            now = self._expire()
            if len(self._pending) >= self.MAX_PENDING:
                raise ValueError('proposal capacity reached')
            token = str(uuid.uuid4())
            while token in self._pending:
                token = str(uuid.uuid4())
            candidate = {
                'id': token, 'version': 1, 'note_id': note_id,
                'base_note_version': version,
                'original_title': original['title'], 'original_body': original['body'],
                'title': title, 'body': body, 'source_text': source_text,
                'expires_at': now + self.TTL_SECONDS, 'status': 'pending',
                'thread_id': source['thread_id'] if source else thread_id,
                'source_context': source,
                'scope': source['scope'] if source else None,
                'thread_revision': source['thread_revision'] if source else None,
                'material_id': source['material_id'] if source else None,
                'purpose': source['purpose'] if source else None,
            }
            self._pending[token] = candidate
            return deepcopy(candidate)

    def get(self, id):
        with self._lock:
            candidate = self._candidate(id)
            self._live(candidate)
            return deepcopy(candidate)

    def list_pending(self, note_id):
        with self._lock:
            self._identifier(note_id, 'note_id')
            self._expire()
            matching = [candidate for candidate in self._pending.values() if candidate['note_id'] == note_id]
            for candidate in matching:
                self._validate_source(candidate['source_context'])
            try:
                self._store.get(note_id)
            except KeyError:
                self.discard_note(note_id)
                raise
            return deepcopy(matching)

    def revise(self, id, expected_version, title, body):
        """Edit the draft against the same original, source and expiry only."""
        with self._lock:
            candidate = self._candidate(id)
            self._check_version(candidate, expected_version)
            title, body = self._draft(title, body)
            self._live(candidate, check_base=True)
            if candidate['version'] == 9223372036854775807:
                raise ValueError('proposal version exhausted')
            candidate.update(title=title, body=body, version=candidate['version'] + 1)
            return deepcopy(candidate)

    def confirm(self, id, expected_version):
        """Save one new immutable note version; receipt failures do not undo it."""
        with self._lock:
            candidate = self._candidate(id)
            self._check_version(candidate, expected_version)
            try:
                with self._confirmation_source(candidate['source_context']):
                    # The guard has already validated the original identity;
                    # calling the validator here could open a nested registry
                    # transaction. Keep both the base check and write guarded.
                    self._live(candidate, check_base=True, check_source=False)
                    try:
                        saved = self._store.update(candidate['note_id'], candidate['title'],
                            candidate['body'], candidate['base_note_version'])
                    except ValueError as exc:
                        if str(exc) != 'note missing or version conflict':
                            raise
                        # Differentiate deletion from a concurrent note save;
                        # never retry the write with refreshed arguments.
                        self._live(candidate, check_source=False)
                        raise NoteProposalConflict('note base version conflict') from None
            except ValueError as exc:
                if str(exc) == 'draft_source_changed':
                    raise NoteProposalConflict('note source changed') from None
                raise
            del self._pending[id]
            result = dict(saved)
            if self._on_confirm is not None:
                try:
                    warnings = self._on_confirm(deepcopy(candidate), dict(saved))
                    if (isinstance(warnings, list)
                            and all(isinstance(warning, str) for warning in warnings) and warnings):
                        result['companion_warnings'] = list(warnings)
                except Exception:
                    result['companion_warnings'] = [
                        '笔记已保存，但未能关联到原共同事项。请检查事项记录，勿重复保存。']
            return result

    def reject(self, id, expected_version):
        with self._lock:
            candidate = self._candidate(id)
            self._check_version(candidate, expected_version)
            del self._pending[id]
            return {'id': id, 'version': candidate['version'], 'status': 'rejected',
                    'rejected': True}

    def discard_note(self, note_id):
        """Forget cached original bodies when an explicit deletion is observed."""
        with self._lock:
            tokens = [token for token, candidate in self._pending.items()
                      if candidate['note_id'] == note_id]
            for token in tokens:
                del self._pending[token]
            return len(tokens)
