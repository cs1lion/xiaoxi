"""Bounded, process-local previews of explicit user record commands.

No import or preview operation writes to the store. Only confirm saves changes.
propose/get/revise return detached candidate dictionaries; list_pending returns a
list of those dictionaries. confirm returns the updated thread and consumes the
token on success. reject consumes it and returns id/version/status/rejected. Missing,
consumed and expired tokens raise KeyError. Stale versions and thread revisions
raise threads.RevisionConflict, with no automatic retry or revision refresh.
Timestamps are Unix seconds; an injected clock must return seconds.
"""

import copy
import re
import threading
import time
import uuid

from .threads import RevisionConflict, ThreadStore


class ProposalService:
    MAX_PENDING = 128
    TTL_SECONDS = 10 * 60
    MAX_TEXT = ThreadStore.MAX_TEXT
    MAX_SOURCE_TEXT = MAX_TEXT + 5
    _COMMAND = re.compile(
        r'(记个决定|记个问题|记个进展|记录决定)[:：]([^\r\n\v\f\x85\u2028\u2029]+)')
    _KINDS = {'记个决定': 'decision', '记录决定': 'decision',
              '记个问题': 'question', '记个进展': 'progress'}
    _OPERATIONS = frozenset(('add_event', 'revise_event', 'next_step'))

    def __init__(self, store, *, clock=None):
        self._store = store
        self._clock = time.time if clock is None else clock
        self._lock = threading.RLock()
        self._pending = {}

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
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError('expected_version must be a positive integer, not bool')
        if candidate['version'] != expected_version:
            raise RevisionConflict('proposal version conflict')

    @classmethod
    def _validate_text(cls, text):
        if (not isinstance(text, str) or not text.strip()
                or not any(char.isprintable() and not char.isspace() for char in text)
                or len(text) > cls.MAX_TEXT or '\x00' in text
                or any(char in text for char in '\r\n\v\f\x85\u2028\u2029')):
            raise ValueError('text must be nonempty, single-line and within the text limit')

    @classmethod
    def _validate_source_text(cls, source_text):
        if (not isinstance(source_text, str) or not source_text.strip()
                or not any(char.isprintable() and not char.isspace() for char in source_text)
                or len(source_text) > cls.MAX_SOURCE_TEXT or '\x00' in source_text
                or any(char in source_text for char in '\r\n\v\f\x85\u2028\u2029')):
            raise ValueError('source_text must be the nonempty, bounded original user input')

    @staticmethod
    def _target(thread, event_id, kind):
        ThreadStore._string(event_id, 'target_event_id', ThreadStore.MAX_IDENTIFIER)
        event = next((item for item in thread['events'] if item['id'] == event_id), None)
        if event is None:
            raise KeyError(event_id)
        if event['source'] != 'user_stated' or event['kind'] not in ThreadStore.USER_KINDS:
            raise ValueError('only user_stated events may be previewed for revision')
        if event['kind'] != kind:
            raise ValueError('kind does not match the target event')
        return event

    def propose_change(self, thread_id, *, operation, text, source_text, kind='decision',
                       target_event_id=None, expected_revision=None, source_session_id='',
                       source_captured_at=None, object_refs=None):
        """Preview a resolved user change without writing or choosing its target.

        The caller supplies the actual user input as source_text; synthesized or
        model-authored text must not be substituted for that provenance. Targets
        are exact identities within the supplied thread, never active selection.
        """
        with self._lock:
            if not isinstance(operation, str) or operation not in self._OPERATIONS:
                raise ValueError('invalid proposal operation')
            if not isinstance(kind, str) or kind not in ThreadStore.USER_KINDS:
                raise ValueError('invalid user event kind')
            self._validate_text(text)
            self._validate_source_text(source_text)
            evidence = {'state': 'user_confirmed', 'request_text': source_text,
                        'session_id': source_session_id,
                        'captured_at': self._clock() if source_captured_at is None else source_captured_at}
            evidence = ThreadStore._evidence(evidence)
            refs = ThreadStore._object_refs(object_refs)
            if expected_revision is not None:
                ThreadStore._revision(expected_revision)
            thread = self._store.get(thread_id)
            if expected_revision is not None and thread['revision'] != expected_revision:
                raise RevisionConflict('thread revision conflict')
            original_text = None
            if operation == 'revise_event':
                original_text = self._target(thread, target_event_id, kind)['text']
            elif target_event_id is not None:
                raise ValueError('target_event_id is only supported for revise_event')
            elif operation == 'next_step':
                original_text = thread['next_step']
            now = self._expire()
            if len(self._pending) >= self.MAX_PENDING:
                raise ValueError('proposal capacity reached')
            token = str(uuid.uuid4())
            while token in self._pending:
                token = str(uuid.uuid4())
            candidate = {
                'id': token, 'version': 1, 'thread_id': thread_id,
                'expected_revision': thread['revision'],
                'kind': kind, 'text': text.strip(), 'source_text': source_text,
                'expires_at': now + self.TTL_SECONDS, 'status': 'pending',
                'operation': operation, 'target_event_id': target_event_id,
                'evidence': evidence, 'object_refs': refs,
            }
            if original_text is not None:
                candidate['original_text'] = original_text
            self._pending[token] = candidate
            return copy.deepcopy(candidate)

    def propose(self, thread_id, utterance, *, source_session_id='', source_captured_at=None,
                object_refs=None):
        """Parse an entire explicit command, capturing its thread revision."""
        with self._lock:
            if (not isinstance(utterance, str) or len(utterance) > self.MAX_SOURCE_TEXT
                    or '\x00' in utterance):
                raise ValueError('explicit_record_command_required')
            match = self._COMMAND.fullmatch(utterance)
            if match is None:
                raise ValueError('explicit_record_command_required')
            raw_text = match.group(2)
            try:
                self._validate_text(raw_text)
            except ValueError:
                raise ValueError('explicit_record_command_required') from None
            return self.propose_change(
                thread_id, operation='add_event', kind=self._KINDS[match.group(1)],
                text=raw_text, source_text=utterance, source_session_id=source_session_id,
                source_captured_at=source_captured_at, object_refs=object_refs)

    def get(self, id):
        """Return a detached pending preview, never the mutable internal record."""
        with self._lock:
            return copy.deepcopy(self._candidate(id))

    def list_pending(self, thread_id):
        """Validate the live thread before returning its pending previews."""
        with self._lock:
            self._expire()
            self._store.get(thread_id)
            return [copy.deepcopy(candidate) for candidate in self._pending.values()
                    if candidate['thread_id'] == thread_id]

    def revise(self, id, expected_version, text):
        """Explicitly refresh a preview; preserve original source and deadline."""
        with self._lock:
            candidate = self._candidate(id)
            self._check_version(candidate, expected_version)
            self._validate_text(text)
            thread = self._store.get(candidate['thread_id'])
            original_text = None
            if candidate['operation'] == 'revise_event':
                original_text = self._target(
                    thread, candidate['target_event_id'], candidate['kind'])['text']
            elif candidate['operation'] == 'next_step':
                original_text = thread['next_step']
            candidate.update(text=text, version=candidate['version'] + 1,
                             expected_revision=thread['revision'])
            if original_text is not None:
                candidate['original_text'] = original_text
            return copy.deepcopy(candidate)

    def confirm(self, id, expected_version):
        """Write exactly the previewed user text once; never retry conflicts."""
        with self._lock:
            candidate = self._candidate(id)
            self._check_version(candidate, expected_version)
            if candidate['operation'] == 'add_event':
                source_id = 'proposal:' + candidate['id']
                thread = self._store.add_event(
                    candidate['thread_id'], expected_revision=candidate['expected_revision'],
                    kind=candidate['kind'], text=candidate['text'], source='user_stated',
                    source_id=source_id, dedupe_key=source_id,
                    evidence=candidate['evidence'], object_refs=candidate['object_refs'])
            elif candidate['operation'] == 'revise_event':
                thread = self._store.revise_event(
                    candidate['thread_id'], candidate['target_event_id'],
                    expected_revision=candidate['expected_revision'], text=candidate['text'],
                    evidence=candidate['evidence'], object_refs=candidate['object_refs'])
            else:
                thread = self._store.update(
                    candidate['thread_id'], expected_revision=candidate['expected_revision'],
                    next_step=candidate['text'])
            del self._pending[id]
            return thread

    def reject(self, id, expected_version):
        """Cancel and discard all retained sensitive fields for this token."""
        with self._lock:
            candidate = self._candidate(id)
            self._check_version(candidate, expected_version)
            del self._pending[id]
            return {'id': id, 'version': candidate['version'], 'status': 'rejected',
                    'rejected': True}

    def discard_thread(self, thread_id):
        """Forget previews after an explicit thread deletion; return their count."""
        with self._lock:
            tokens = [token for token, candidate in self._pending.items()
                      if candidate['thread_id'] == thread_id]
            for token in tokens:
                del self._pending[token]
            return len(tokens)
