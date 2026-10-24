"""Explicit-user thread storage, independent of application bootstrap and models.

Call create/update/revise_event/delete/select_thread and material mutations only
from an explicit-user API action.
The caller must authenticate tool_verified receipts; source labels are provenance,
not authentication. Missing/deleted IDs raise KeyError; invalid input and dedupe
collisions raise ValueError; stale revisions raise RevisionConflict. Exact dedupe
retries return the current thread even with a stale (but valid) revision. Event
revision preserves the original dedupe payload so retries never undo user edits.
All timestamps are Unix seconds. No database is opened during import.
"""

from contextlib import contextmanager
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from typing import Callable


class RevisionConflict(ValueError):
    """The expected revision does not match the live thread."""


class ThreadStore:
    STATUSES = frozenset(('discussing', 'waiting', 'active', 'done', 'paused'))
    USER_KINDS = frozenset(('decision', 'question', 'progress'))
    MATERIAL_PURPOSES = frozenset(('reference', 'draft', 'artifact'))
    MAX_TITLE = 200
    MAX_TEXT = 4000
    MAX_IDENTIFIER = 256
    MAX_EVENTS = 200
    MAX_EVENT_VERSIONS = 200
    MAX_OBJECT_REFS = 6
    MAX_REQUEST_TEXT = MAX_TEXT + 5
    MAX_MATERIALS = 12
    MAX_LIST = 100

    def __init__(self, path):
        if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
            raise ValueError('path must be a nonempty filesystem path or :memory:')
        # 生产库哨兵（#39）：台账库正是当初被测试写脏的那一个（379 条），
        # 拦在打开点。本模块不依赖 server 包，判据就地写，与
        # server.config.guard_sqlite_path 一致（TEST_SENTINEL=1 且在仓库 data/ 下）。
        if os.environ.get('TEST_SENTINEL') == '1' and str(path) != ':memory:':
            target = os.path.abspath(os.fspath(path))
            data_dir = os.path.abspath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data'))
            if target == data_dir or target.startswith(data_dir + os.sep):
                raise RuntimeError(
                    f'测试进程的台账路径指向真实数据目录：{target}。'
                    '测试必须用临时库，见 issue #39。')
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute('PRAGMA foreign_keys = ON')
            self._db.execute('PRAGMA secure_delete = ON')
            with self._transaction():
                self._db.execute('''CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, session_id TEXT NOT NULL,
                    status TEXT NOT NULL, next_step TEXT NOT NULL,
                    allow_callbacks INTEGER NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
                    created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
                self._db.execute('''CREATE TABLE IF NOT EXISTS thread_events (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL,
                    source_id TEXT NOT NULL, dedupe_key TEXT NOT NULL, dedupe_payload TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
                self._db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS thread_event_dedupe
                    ON thread_events(thread_id, dedupe_key) WHERE dedupe_key != '' ''')
                # Current rows retain stable event IDs for old callers. Every
                # edit appends a detached snapshot; no API rewrites a version.
                self._db.execute('''CREATE TABLE IF NOT EXISTS thread_event_versions (
                    event_id TEXT NOT NULL REFERENCES thread_events(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL CHECK(version > 0),
                    previous_version INTEGER,
                    kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL,
                    source_id TEXT NOT NULL, evidence TEXT NOT NULL, object_refs TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(event_id, version),
                    CHECK((version=1 AND previous_version IS NULL)
                          OR (version>1 AND previous_version=version-1)))''')
                # An older database only contains the last saved text. Its v1
                # is that text, not a reconstructed history or guessed quote.
                self._db.execute('''INSERT INTO thread_event_versions
                    (event_id, version, previous_version, kind, text, source, source_id,
                     evidence, object_refs, created_at, updated_at)
                    SELECT id, 1, NULL, kind, text, source, source_id, ?, ?, updated_at, updated_at
                    FROM thread_events e WHERE NOT EXISTS (
                        SELECT 1 FROM thread_event_versions v WHERE v.event_id=e.id)''',
                    ('{"state":"legacy"}', '[]'))
                self._db.execute('''CREATE TABLE IF NOT EXISTS thread_tombstones (
                    id TEXT PRIMARY KEY, deleted_at REAL NOT NULL)''')
                # Single local user: a selection belongs to the database, not a
                # chat session or a Participation instance. Additive migration
                # gives existing databases an empty selection without rewriting
                # any threads or events. The FK clears it in the delete commit.
                self._db.execute('''CREATE TABLE IF NOT EXISTS thread_selection (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL)''')
                self._db.execute('''INSERT OR IGNORE INTO thread_selection
                    (singleton, thread_id) VALUES (1, NULL)''')
                # This is an explicit current association, separate from event
                # provenance. Old selections and event refs never seed it.
                self._db.execute('''CREATE TABLE IF NOT EXISTS thread_materials (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    note_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0),
                    purpose TEXT NOT NULL CHECK(purpose IN ('reference', 'draft', 'artifact')),
                    created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
                self._db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS thread_material_ref
                    ON thread_materials(thread_id, note_id, version)''')
                self._db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS thread_material_draft
                    ON thread_materials(thread_id) WHERE purpose='draft' ''')
        except BaseException:
            self._db.close()
            self._closed = True
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('ThreadStore is closed')
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    @staticmethod
    def _string(value, name, maximum, empty=False):
        if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
            raise ValueError(f'{name} must be a string of at most {maximum} characters without NUL')
        if not empty and not value.strip():
            raise ValueError(f'{name} must not be blank')
        return value

    @staticmethod
    def _revision(value):
        if type(value) is not int or not 1 <= value <= 9223372036854775807:
            raise ValueError('expected_revision must be a positive SQLite integer, not bool')

    @classmethod
    def _material_purpose(cls, value):
        if not isinstance(value, str) or value not in cls.MATERIAL_PURPOSES:
            raise ValueError('invalid_material_purpose')

    @classmethod
    def _evidence(cls, evidence, source='user_stated'):
        """Check shape only; a confirmed request is not semantic proof of text.

        Callers own source capture and user confirmation. This store cannot
        authenticate a session, quote, model conclusion, or external receipt.
        """
        default = 'tool_verified' if source == 'tool_verified' else 'manual'
        if evidence is None:
            return {'state': default}
        if not isinstance(evidence, dict):
            raise ValueError('evidence must be an object')
        state = evidence.get('state')
        if not isinstance(state, str) or state not in ('manual', 'user_confirmed', 'legacy', 'tool_verified'):
            raise ValueError('invalid evidence state')
        if state == 'tool_verified' and source != 'tool_verified':
            raise ValueError('tool evidence requires tool_verified provenance')
        if source == 'tool_verified' and state not in ('tool_verified', 'legacy'):
            raise ValueError('tool receipts cannot be user confirmation evidence')
        if state != 'user_confirmed':
            if set(evidence) != {'state'}:
                raise ValueError('unexpected evidence fields')
            return {'state': state}
        if set(evidence) != {'state', 'request_text', 'session_id', 'captured_at'}:
            raise ValueError('user_confirmed evidence requires captured request fields')
        cls._string(evidence['request_text'], 'request_text', cls.MAX_REQUEST_TEXT)
        cls._string(evidence['session_id'], 'session_id', cls.MAX_IDENTIFIER, empty=True)
        captured_at = evidence['captured_at']
        if (type(captured_at) not in (int, float)
                or not 0 <= captured_at <= 253402300799 or not math.isfinite(captured_at)):
            raise ValueError('captured_at must be finite supported Unix seconds')
        return {'state': state, 'request_text': evidence['request_text'],
                'session_id': evidence['session_id'], 'captured_at': captured_at}

    @classmethod
    def _object_refs(cls, refs):
        """Validate fixed note-reference shape; callers verify object existence."""
        if refs is None:
            return []
        if not isinstance(refs, list) or len(refs) > cls.MAX_OBJECT_REFS:
            raise ValueError('object_refs must be a bounded list')
        result = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {'type', 'id', 'version'} or ref['type'] != 'note':
                raise ValueError('only exact note ObjectRefs are supported')
            cls._string(ref['id'], 'object_ref.id', cls.MAX_IDENTIFIER)
            cls._revision(ref['version'])
            result.append({'type': 'note', 'id': ref['id'], 'version': ref['version']})
        return result

    @staticmethod
    def _version_record(row):
        result = dict(row)
        result['evidence'] = json.loads(result['evidence'])
        result['object_refs'] = json.loads(result['object_refs'])
        return result

    def _append_version(self, event, version, evidence, object_refs, now):
        self._db.execute('''INSERT INTO thread_event_versions
            (event_id, version, previous_version, kind, text, source, source_id,
             evidence, object_refs, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (event['id'], version, version - 1 if version > 1 else None,
             event['kind'], event['text'], event['source'], event['source_id'],
             json.dumps(evidence, ensure_ascii=True, separators=(',', ':')),
             json.dumps(object_refs, ensure_ascii=True, separators=(',', ':')),
             now, now))

    def _thread(self, thread_id):
        self._string(thread_id, 'thread_id', self.MAX_IDENTIFIER)
        row = self._db.execute('SELECT * FROM threads WHERE id=?', (thread_id,)).fetchone()
        if row is None:
            raise KeyError(thread_id)
        result = dict(row)
        result['allow_callbacks'] = bool(result['allow_callbacks'])
        result['events'] = [self._version_record(event) for event in self._db.execute('''SELECT
            e.id, e.kind, e.text, e.source, e.source_id, e.dedupe_key, e.created_at, e.updated_at,
            v.version, v.evidence, v.object_refs
            FROM thread_events e JOIN thread_event_versions v ON v.event_id=e.id
            AND v.version=(SELECT MAX(version) FROM thread_event_versions WHERE event_id=e.id)
            WHERE e.thread_id=? ORDER BY e.rowid''', (thread_id,))]
        result['materials'] = [dict(material) for material in self._db.execute('''SELECT
            id, thread_id, note_id, version, purpose, created_at, updated_at
            FROM thread_materials WHERE thread_id=? ORDER BY rowid''', (thread_id,))]
        return result

    @staticmethod
    def _check(thread, expected_revision, *, require_increment=True):
        if thread['revision'] != expected_revision:
            raise RevisionConflict('thread revision conflict')
        if require_increment and expected_revision == 9223372036854775807:
            raise ValueError('thread revision exhausted')

    def _bump(self, thread_id, expected_revision, now):
        cursor = self._db.execute('''UPDATE threads SET revision=revision+1, updated_at=?
            WHERE id=? AND revision=?''', (now, thread_id, expected_revision))
        if cursor.rowcount != 1:
            raise RevisionConflict('thread revision conflict')

    def create(self, title, session_id='') -> dict:
        self._string(title, 'title', self.MAX_TITLE)
        self._string(session_id, 'session_id', self.MAX_IDENTIFIER, empty=True)
        with self._transaction():
            thread_id = str(uuid.uuid4())
            # Even a UUID collision must not revive a deleted identity.
            if self._db.execute('SELECT 1 FROM thread_tombstones WHERE id=?', (thread_id,)).fetchone():
                raise ValueError('generated thread ID was previously deleted')
            now = time.time()
            self._db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)',
                             (thread_id, title, session_id, 'discussing', '', 0, 1, now, now))
            return self._thread(thread_id)

    def list_threads(self) -> list:
        with self._transaction():
            ids = self._db.execute('SELECT id FROM threads ORDER BY updated_at DESC, id LIMIT ?',
                                   (self.MAX_LIST,)).fetchall()
            return [self._thread(row['id']) for row in ids]

    def get(self, thread_id) -> dict:
        with self._transaction():
            return self._thread(thread_id)

    @contextmanager
    def material_guard(self, thread_id, material_id, *, note_id, version):
        """Serialize a draft save against detach, role/version change and deletion.

        This holds the SQLite write reservation across the caller's note CAS.
        Callers must not nest another ThreadStore transaction in this interval.
        The guard changes no material or revision and is never held over awaits.
        """
        self._string(thread_id, 'thread_id', self.MAX_IDENTIFIER)
        self._string(material_id, 'material_id', self.MAX_IDENTIFIER)
        self._string(note_id, 'note_id', self.MAX_IDENTIFIER)
        self._revision(version)
        with self._transaction():
            material = self._db.execute('''SELECT note_id, version, purpose FROM thread_materials
                WHERE thread_id=? AND id=?''', (thread_id, material_id)).fetchone()
            if (material is None or material['note_id'] != note_id
                    or material['version'] != version or material['purpose'] != 'draft'):
                raise ValueError('draft_source_changed')
            yield

    def event_history(self, thread_id, event_id) -> list:
        """Return immutable snapshots in order, only within the named thread."""
        self._string(event_id, 'event_id', self.MAX_IDENTIFIER)
        with self._transaction():
            self._string(thread_id, 'thread_id', self.MAX_IDENTIFIER)
            if not self._db.execute('SELECT 1 FROM threads WHERE id=?', (thread_id,)).fetchone():
                raise KeyError(thread_id)
            if not self._db.execute('SELECT 1 FROM thread_events WHERE id=? AND thread_id=?',
                                    (event_id, thread_id)).fetchone():
                raise KeyError(event_id)
            return [self._version_record(row) for row in self._db.execute('''SELECT
                event_id AS id, version, previous_version, kind, text, source, source_id,
                evidence, object_refs, created_at, updated_at FROM thread_event_versions
                WHERE event_id=? ORDER BY version''', (event_id,))]

    def get_selection(self) -> str | None:
        """Read the explicitly selected thread, shared across connections."""
        with self._transaction():
            return self._db.execute('''SELECT thread_id FROM thread_selection
                WHERE singleton=1''').fetchone()['thread_id']

    def select_thread(self, thread_id) -> dict:
        """Select an existing thread, or persist an explicit unlink with None."""
        if thread_id is not None:
            self._string(thread_id, 'thread_id', self.MAX_IDENTIFIER)
        with self._transaction():
            if thread_id is not None:
                # Validation and write share the transaction: a concurrent
                # deletion either clears this choice or precedes this failure.
                self._thread(thread_id)
            self._db.execute('''UPDATE thread_selection SET thread_id=?
                WHERE singleton=1''', (thread_id,))
            return {'thread_id': thread_id}

    def update(self, thread_id, *, expected_revision: int, title=None, status=None,
               next_step=None, allow_callbacks=None) -> dict:
        self._revision(expected_revision)
        if title is not None:
            self._string(title, 'title', self.MAX_TITLE)
        if status is not None and (not isinstance(status, str) or status not in self.STATUSES):
            raise ValueError('invalid thread status')
        if next_step is not None:
            self._string(next_step, 'next_step', self.MAX_TEXT, empty=True)
        if allow_callbacks is not None and type(allow_callbacks) is not bool:
            raise ValueError('allow_callbacks must be bool')
        with self._transaction():
            thread = self._thread(thread_id)
            self._check(thread, expected_revision)
            for name, value in (('title', title), ('status', status), ('next_step', next_step),
                                ('allow_callbacks', allow_callbacks)):
                if value is not None:
                    thread[name] = value
            self._db.execute('''UPDATE threads SET title=?, status=?, next_step=?, allow_callbacks=?
                WHERE id=? AND revision=?''', (thread['title'], thread['status'], thread['next_step'],
                                               int(thread['allow_callbacks']), thread_id, expected_revision))
            self._bump(thread_id, expected_revision, time.time())
            return self._thread(thread_id)

    def delete(self, thread_id, *, expected_revision: int) -> dict:
        self._revision(expected_revision)
        with self._transaction():
            self._check(self._thread(thread_id), expected_revision)
            tombstone = {'id': thread_id, 'deleted_at': time.time()}
            self._db.execute('INSERT INTO thread_tombstones VALUES (:id,:deleted_at)', tombstone)
            self._db.execute('DELETE FROM threads WHERE id=? AND revision=?', (thread_id, expected_revision))
            return tombstone

    def link_material(self, thread_id, *, expected_revision: int, note_id, version, purpose) -> dict:
        """Link an exact saved version after caller-owned note validation.

        The store checks shape and local associations only. It cannot prove an
        external note exists, owns the matter, supports a claim, or grants tools.
        """
        self._revision(expected_revision)
        self._string(note_id, 'note_id', self.MAX_IDENTIFIER)
        self._revision(version)
        self._material_purpose(purpose)
        with self._transaction():
            thread = self._thread(thread_id)
            self._check(thread, expected_revision, require_increment=False)
            same = next((material for material in thread['materials']
                         if material['note_id'] == note_id and material['version'] == version), None)
            if same is not None:
                if same['purpose'] != purpose:
                    raise ValueError('material_already_linked')
                return thread  # A fresh-CAS exact retry does not create a new revision.
            self._check(thread, expected_revision)
            if purpose == 'draft' and any(material['purpose'] == 'draft' for material in thread['materials']):
                raise ValueError('draft_already_linked')
            if len(thread['materials']) >= self.MAX_MATERIALS:
                raise ValueError('thread_material_limit_reached')
            now = time.time()
            self._db.execute('''INSERT INTO thread_materials
                (id, thread_id, note_id, version, purpose, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?)''',
                (str(uuid.uuid4()), thread_id, note_id, version, purpose, now, now))
            self._bump(thread_id, expected_revision, now)
            return self._thread(thread_id)

    def update_material(self, thread_id, material_id, *, expected_revision: int,
                        version=None, purpose=None) -> dict:
        """Explicitly change version/use while retaining the same note identity."""
        self._revision(expected_revision)
        self._string(material_id, 'material_id', self.MAX_IDENTIFIER)
        if version is None and purpose is None:
            raise ValueError('material_update_required')
        if version is not None:
            self._revision(version)
        if purpose is not None:
            self._material_purpose(purpose)
        with self._transaction():
            thread = self._thread(thread_id)
            self._check(thread, expected_revision, require_increment=False)
            material = next((item for item in thread['materials'] if item['id'] == material_id), None)
            if material is None:
                raise KeyError(material_id)
            new_version = material['version'] if version is None else version
            new_purpose = material['purpose'] if purpose is None else purpose
            if new_version == material['version'] and new_purpose == material['purpose']:
                return thread
            self._check(thread, expected_revision)
            others = [item for item in thread['materials'] if item['id'] != material_id]
            if any(item['note_id'] == material['note_id'] and item['version'] == new_version for item in others):
                raise ValueError('material_already_linked')
            if new_purpose == 'draft' and any(item['purpose'] == 'draft' for item in others):
                raise ValueError('draft_already_linked')
            now = time.time()
            self._db.execute('''UPDATE thread_materials SET version=?, purpose=?, updated_at=?
                WHERE id=? AND thread_id=?''', (new_version, new_purpose, now, material_id, thread_id))
            self._bump(thread_id, expected_revision, now)
            return self._thread(thread_id)

    def remove_material(self, thread_id, material_id, *, expected_revision: int) -> dict:
        """Unlink only; original notes and historical event refs remain separate."""
        self._revision(expected_revision)
        self._string(material_id, 'material_id', self.MAX_IDENTIFIER)
        with self._transaction():
            thread = self._thread(thread_id)
            self._check(thread, expected_revision)
            cursor = self._db.execute('DELETE FROM thread_materials WHERE id=? AND thread_id=?',
                                      (material_id, thread_id))
            if cursor.rowcount != 1:
                raise KeyError(material_id)
            self._bump(thread_id, expected_revision, time.time())
            return self._thread(thread_id)

    def add_event(self, thread_id, *, expected_revision: int, kind: str, text: str,
                  source: str = 'user_stated', source_id: str = '', dedupe_key: str = '',
                  evidence=None, object_refs=None) -> dict:
        self._revision(expected_revision)
        self._string(kind, 'kind', 32)
        self._string(source, 'source', 32)
        self._string(text, 'text', self.MAX_TEXT)
        self._string(source_id, 'source_id', self.MAX_IDENTIFIER, empty=True)
        self._string(dedupe_key, 'dedupe_key', self.MAX_IDENTIFIER, empty=True)
        if kind == 'tool_receipt':
            if source != 'tool_verified' or not source_id.strip():
                raise ValueError('tool_receipt requires tool_verified and a source_id')
        elif kind not in self.USER_KINDS or source != 'user_stated':
            raise ValueError('user event kinds require user_stated provenance')
        evidence = self._evidence(evidence, source)
        object_refs = self._object_refs(object_refs)
        # Default writes preserve the pre-history dedupe payload exactly. A
        # metadata-bearing retry must match the originally captured metadata.
        values = [kind, text, source, source_id]
        if evidence != self._evidence(None, source) or object_refs:
            values.extend((evidence, object_refs))
        payload = json.dumps(values, ensure_ascii=True, separators=(',', ':'))
        with self._transaction():
            thread = self._thread(thread_id)
            if dedupe_key:
                previous = self._db.execute('''SELECT dedupe_payload FROM thread_events
                    WHERE thread_id=? AND dedupe_key=?''', (thread_id, dedupe_key)).fetchone()
                if previous is not None:
                    if previous['dedupe_payload'] != payload:
                        raise ValueError('dedupe_key already used for a different payload')
                    return thread
            self._check(thread, expected_revision)
            if len(thread['events']) >= self.MAX_EVENTS:
                raise ValueError('thread event limit reached')
            now = time.time()
            event_id = str(uuid.uuid4())
            self._db.execute('INSERT INTO thread_events VALUES (?,?,?,?,?,?,?,?,?,?)',
                             (event_id, thread_id, kind, text, source, source_id,
                              dedupe_key, payload, now, now))
            self._append_version({'id': event_id, 'kind': kind, 'text': text, 'source': source,
                                  'source_id': source_id, 'created_at': now},
                                 1, evidence, object_refs, now)
            self._bump(thread_id, expected_revision, now)
            return self._thread(thread_id)

    def revise_event(self, thread_id, event_id, *, expected_revision: int, text,
                     evidence=None, object_refs=None) -> dict:
        self._revision(expected_revision)
        self._string(event_id, 'event_id', self.MAX_IDENTIFIER)
        self._string(text, 'text', self.MAX_TEXT)
        evidence = self._evidence(evidence, 'user_stated')
        object_refs = self._object_refs(object_refs)
        with self._transaction():
            thread = self._thread(thread_id)
            self._check(thread, expected_revision)
            event = next((item for item in thread['events'] if item['id'] == event_id), None)
            if event is None:
                raise KeyError(event_id)
            if event['source'] != 'user_stated':
                raise ValueError('only user_stated events may be revised')
            if event['version'] >= self.MAX_EVENT_VERSIONS:
                raise ValueError('event version limit reached')
            now = time.time()
            self._db.execute('UPDATE thread_events SET text=?, updated_at=? WHERE id=? AND thread_id=?',
                             (text, now, event_id, thread_id))
            self._append_version({**event, 'text': text}, event['version'] + 1,
                                 evidence, object_refs, now)
            self._bump(thread_id, expected_revision, now)
            return self._thread(thread_id)


class CommissionStore:
    """V3 督办台账：6 阶段状态机（docs/PROTOCOL.md §19 / EXECUTIVE_SECRETARY_V3.md §3）。

    与 ThreadStore 同库同规约：缺失 ID 抛 KeyError，非法迁移抛 ValueError，
    时间戳一律 Unix 秒，只存公开决定与结果，不存模型思考过程、不存凭据。
    事件表只追加不改写，方便事后回答"这一单到底卡在谁手里"。
    """

    STATUSES = frozenset(('drafting', 'briefing', 'running', 'blocked',
                          'auditing', 'ready_to_report', 'done'))
    TERMINAL = frozenset(('done',))
    TRANSITIONS = {
        'drafting': frozenset(('briefing', 'done')),
        'briefing': frozenset(('running', 'blocked', 'done')),
        'running': frozenset(('blocked', 'auditing', 'done')),
        'blocked': frozenset(('running', 'done')),
        'auditing': frozenset(('running', 'ready_to_report', 'blocked')),
        'ready_to_report': frozenset(('done', 'running')),
    }
    MAX_TITLE = 200
    MAX_BRIEF = 4000
    MAX_QUESTION = 400
    MAX_LIST = 100

    def __init__(self, path):
        if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
            raise ValueError('path must be a nonempty filesystem path or :memory:')
        # 生产库哨兵（#39）：台账库正是当初被测试写脏的那一个（379 条），
        # 拦在打开点。本模块不依赖 server 包，判据就地写，与
        # server.config.guard_sqlite_path 一致（TEST_SENTINEL=1 且在仓库 data/ 下）。
        if os.environ.get('TEST_SENTINEL') == '1' and str(path) != ':memory:':
            target = os.path.abspath(os.fspath(path))
            data_dir = os.path.abspath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data'))
            if target == data_dir or target.startswith(data_dir + os.sep):
                raise RuntimeError(
                    f'测试进程的台账路径指向真实数据目录：{target}。'
                    '测试必须用临时库，见 issue #39。')
        self._lock = threading.RLock()
        self._closed = False
        # 变更通知：台账是唯一真相，谁改的都得从这里过，所以钩子只挂一处。
        # 订阅者拿到的是一条已投影的督办（与 GET /api/executive/commissions 同形）。
        # 回调异常一律吞掉——推送坏了不能把写台账本身带崩。
        self._listeners: list[Callable[[dict], None]] = []
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute('PRAGMA foreign_keys = ON')
            with self._transaction():
                self._db.execute('''CREATE TABLE IF NOT EXISTS commissions (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL, brief TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    step_idx INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
                    blocked_question TEXT NOT NULL DEFAULT '',
                    blocked_kind TEXT NOT NULL DEFAULT '',
                    delivery TEXT NOT NULL DEFAULT '{}', audit TEXT NOT NULL DEFAULT '{}',
                    packet TEXT NOT NULL DEFAULT '{}',
                    rubric_ids TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, closed_at REAL)''')
                self._db.execute('''CREATE TABLE IF NOT EXISTS commission_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, commission_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL)''')
                self._db.execute('''CREATE TABLE IF NOT EXISTS commission_requests (
                    request_id TEXT PRIMARY KEY, commission_id TEXT NOT NULL,
                    arguments TEXT NOT NULL, created_at REAL NOT NULL)''')
                columns = {row['name'] for row in self._db.execute('PRAGMA table_info(commissions)')}
                if 'blocked_kind' not in columns:
                    self._db.execute("ALTER TABLE commissions ADD COLUMN blocked_kind TEXT NOT NULL DEFAULT ''")
                self._db.execute('CREATE INDEX IF NOT EXISTS commission_status ON commissions(status, updated_at)')
                self._db.execute('CREATE INDEX IF NOT EXISTS commission_events_id ON commission_events(commission_id, id)')
        except BaseException:
            self._db.close()
            self._closed = True
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('CommissionStore is closed')
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    # ---------- 内部 ----------
    def _row(self, commission_id):
        if not isinstance(commission_id, str) or not commission_id:
            raise ValueError('id must be nonempty text')
        row = self._db.execute('SELECT * FROM commissions WHERE id=?', (commission_id,)).fetchone()
        if row is None:
            raise KeyError(commission_id)
        return self._project(dict(row))

    @staticmethod
    def _project(row):
        try:
            row['packet'] = json.loads(row.get('packet') or '{}')
        except (TypeError, ValueError):
            row['packet'] = {}
        for key in ('delivery', 'audit', 'rubric_ids'):
            try:
                row[key] = json.loads(row.get(key) or ('[]' if key == 'rubric_ids' else '{}'))
            except (TypeError, ValueError):
                row[key] = [] if key == 'rubric_ids' else {}
        return row

    def _allowed(self, status, target):
        return target in self.TRANSITIONS.get(status, frozenset())

    @staticmethod
    def _string(value, name, maximum):
        if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
            raise ValueError(f'{name} must be a string of at most {maximum} characters without NUL')
        return value

    # ---------- 写入 ----------
    def create(self, title, brief='', thread_id='', agent_id='', *, request_id=None):
        title = self._string(title, 'title', self.MAX_TITLE)
        brief = self._string(brief, 'brief', self.MAX_BRIEF)
        thread_id = self._string(thread_id, 'thread_id', 100)
        agent_id = self._string(agent_id, 'agent_id', 100)
        if request_id is not None:
            request_id = self._string(request_id, 'request_id', 200)
            if not request_id:
                raise ValueError('request_id must be nonempty')
        arguments = json.dumps({'title': title, 'brief': brief, 'thread_id': thread_id,
                                'agent_id': agent_id}, ensure_ascii=False, sort_keys=True)
        commission_id, now = 'c_' + uuid.uuid4().hex[:12], time.time()
        with self._transaction():
            if request_id is not None:
                previous = self._db.execute(
                    'SELECT commission_id, arguments FROM commission_requests WHERE request_id=?',
                    (request_id,)).fetchone()
                if previous is not None:
                    if previous['arguments'] != arguments:
                        raise ValueError('request_id already used with different arguments')
                    return self._row(previous['commission_id'])
            self._db.execute('''INSERT INTO commissions
                (id, thread_id, title, brief, agent_id, status, step_idx, retry_count,
                 blocked_question, delivery, audit, packet, rubric_ids, created_at, updated_at, closed_at)
                VALUES (?,?,?,?,?, 'drafting', 0, 0, '', '{}', '{}', '{}', '[]', ?, ?, NULL)''',
                (commission_id, thread_id, title, brief, agent_id, now, now))
            if request_id is not None:
                self._db.execute('INSERT INTO commission_requests VALUES (?,?,?,?)',
                                 (request_id, commission_id, arguments, now))
            self._event(commission_id, 'opened', {'title': title, 'brief': brief[:200]}, now)
            created = self._row(commission_id)
        self._publish(commission_id)
        return created

    def transition(self, commission_id, status, **changes):
        """Move one commission along the 6-phase machine; illegal moves raise ValueError."""
        if not isinstance(status, str) or status not in self.STATUSES:
            raise ValueError('invalid commission status')
        allowed_changes = {'agent_id', 'step_idx', 'retry_delta', 'blocked_question',
                           'blocked_kind', 'delivery', 'audit', 'rubric_ids', 'packet',
                           'title', 'brief'}
        if set(changes) - allowed_changes:
            raise ValueError('unknown commission changes')
        with self._transaction():
            row = self._row(commission_id)
            if not self._allowed(row['status'], status):
                raise ValueError(f"illegal commission transition {row['status']}->{status}")
            now = time.time()
            updates = {'status': status, 'updated_at': now}
            payload = {'from': row['status'], 'to': status}
            if 'agent_id' in changes:
                updates['agent_id'] = self._string(changes['agent_id'], 'agent_id', 100)
            if 'step_idx' in changes:
                updates['step_idx'] = max(0, int(changes['step_idx']))
                payload['step_idx'] = updates['step_idx']
            if changes.get('retry_delta'):
                updates['retry_count'] = max(0, int(row['retry_count']) + int(changes['retry_delta']))
                payload['retry_count'] = updates['retry_count']
            if 'blocked_question' in changes or status == 'blocked':
                # 协议 §19：blocked 状态的前置条件是一句非空请示；清空只在离开 blocked 时发生。
                question = self._string(changes.get('blocked_question', row['blocked_question']),
                                        'blocked_question', self.MAX_QUESTION)
                if status == 'blocked' and not question:
                    raise ValueError('blocked requires a question')
                updates['blocked_question'] = question
                payload['question'] = question
                if status == 'blocked':
                    kind = changes.get('blocked_kind', row['blocked_kind'] or 'clarification')
                    if kind not in ('clarification', 'approval'):
                        raise ValueError('blocked_kind must be clarification or approval')
                    updates['blocked_kind'] = kind
                    payload['blocked_kind'] = kind
                else:
                    updates['blocked_kind'] = ''
            elif 'blocked_kind' in changes:
                raise ValueError('blocked_kind only applies to blocked')
            for key in ('delivery', 'audit', 'rubric_ids', 'packet'):
                if key in changes:
                    value = changes[key]
                    encoded = json.dumps(value, ensure_ascii=False)
                    if len(encoded) > 8000:
                        raise ValueError(f'{key} too large')
                    updates[key] = encoded
            if status in self.TERMINAL:
                updates['closed_at'] = now
            columns = ', '.join(f'{key}=?' for key in updates)
            self._db.execute(f'UPDATE commissions SET {columns} WHERE id=?',
                             (*updates.values(), commission_id))
            self._event(commission_id, 'transition', payload, now)
            updated = self._row(commission_id)
        self._publish(commission_id)
        return updated

    def record_step(self, commission_id, step_idx):
        with self._transaction():
            row = self._row(commission_id)
            if row['status'] not in ('running', 'auditing'):
                raise ValueError('only a running commission records steps')
            now = time.time()
            step = max(0, int(step_idx))
            self._db.execute('UPDATE commissions SET step_idx=?, updated_at=? WHERE id=?',
                             (step, now, commission_id))
            self._event(commission_id, 'step', {'step_idx': step}, now)
            stepped = self._row(commission_id)
        self._publish(commission_id)
        return stepped

    def _event(self, commission_id, kind, payload, now):
        self._db.execute('INSERT INTO commission_events (commission_id, kind, payload, created_at)'
                         ' VALUES (?,?,?,?)',
                         (commission_id, kind, json.dumps(payload, ensure_ascii=False), now))

    # ---------- 变更通知 ----------
    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        """订阅每一次落库之后的督办投影。返回取消订阅函数。

        只推"这一条督办现在长什么样"，不推差异：订阅者拿到的形状和 REST 完全
        一致，就不必维护第二套字段映射。回调在写事务之外调用。
        """
        self._listeners.append(listener)

        def unsubscribe():
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    def _publish(self, commission_id):
        """在事务外读一次投影再分发。读失败不推，也不影响已经写成功的台账。"""
        listeners = list(self._listeners)
        if not listeners:
            return
        try:
            commission = self.get(commission_id)
        except (KeyError, sqlite3.Error):
            return
        for listener in listeners:
            try:
                listener(commission)
            except Exception:
                # 推送是旁路：一个订阅者坏了不能连累其它订阅者，更不能回滚台账。
                continue

    # ---------- 读取 ----------
    def get(self, commission_id):
        with self._transaction():
            return self._row(commission_id)

    def request_commission(self, request_id):
        """Return the commission bound to an idempotency key, or None."""
        if request_id is None:
            return None
        request_id = self._string(request_id, 'request_id', 200)
        if not request_id:
            return None
        with self._transaction():
            row = self._db.execute(
                'SELECT commission_id FROM commission_requests WHERE request_id=?',
                (request_id,)).fetchone()
            return self._row(row['commission_id']) if row is not None else None

    def list_commissions(self, status=None, limit=50):
        clause, params = '', []
        if status is not None:
            if not isinstance(status, str) or status not in self.STATUSES:
                raise ValueError('invalid commission status filter')
            clause, params = 'WHERE status=?', [status]
        params.append(max(1, min(int(limit), self.MAX_LIST)))
        with self._transaction():
            rows = self._db.execute(
                f'SELECT * FROM commissions {clause} ORDER BY updated_at DESC, id LIMIT ?',
                params).fetchall()
        return [self._project(dict(row)) for row in rows]

    def open_commissions(self, agent_id=None):
        with self._transaction():
            if agent_id:
                rows = self._db.execute(
                    "SELECT * FROM commissions WHERE agent_id=? AND status NOT IN ('done')"
                    " ORDER BY updated_at DESC", (agent_id,)).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM commissions WHERE status NOT IN ('done') ORDER BY updated_at DESC").fetchall()
        return [self._project(dict(row)) for row in rows]

    def events(self, commission_id, limit=50):
        with self._transaction():
            self._row(commission_id)
            rows = self._db.execute(
                'SELECT kind, payload, created_at FROM commission_events WHERE commission_id=?'
                ' ORDER BY id DESC LIMIT ?', (commission_id, max(1, min(int(limit), 200)))).fetchall()
        return [{'kind': row['kind'], 'payload': json.loads(row['payload']),
                 'created_at': row['created_at']} for row in rows]
