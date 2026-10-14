"""Explicit notes with immutable version snapshots and a persistent chosen object.

The current notes table remains compatible with existing CRUD callers. Historical
versions are never overwritten; deleting a note also deletes its snapshots. A
single local workspace identity survives changing or clearing the chosen note.
"""
import sqlite3
import time
from uuid import uuid4
from contextlib import contextmanager


class SelectionConflict(ValueError):
    """A selection changed since the caller last reviewed it."""


class NoteStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
                source TEXT NOT NULL, version INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS note_revisions (
                id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                title TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(id, version))''')
            # An old database only knows its current version. Preserve exactly
            # that version; never invent unavailable earlier text or timestamps.
            db.execute('''INSERT OR IGNORE INTO note_revisions
                (id,title,body,source,version,created_at,updated_at)
                SELECT id,title,body,source,version,created_at,updated_at FROM notes''')
            db.execute('''CREATE TABLE IF NOT EXISTS note_selection (
                id INTEGER PRIMARY KEY CHECK(id=1),
                activity_id TEXT NOT NULL UNIQUE, note_id TEXT,
                selected_version INTEGER, revision INTEGER NOT NULL CHECK(revision > 0),
                selected_at REAL,
                CHECK((note_id IS NULL AND selected_version IS NULL) OR
                    (note_id IS NOT NULL AND selected_version > 0)))''')
            db.execute('''INSERT OR IGNORE INTO note_selection
                (id,activity_id,note_id,selected_version,revision,selected_at)
                VALUES (1,?,NULL,NULL,1,NULL)''', (uuid4().hex,))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def text(value, name, maximum, required=True):
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ValueError(f'invalid {name}')
        return value.strip()

    @staticmethod
    def _version(value, name='version'):
        if type(value) is not int or not 1 <= value <= 9223372036854775807:
            raise ValueError('invalid ' + name)

    @staticmethod
    def _record_revision(db, note_id):
        # A duplicate key is a consistency error, never a reason to overwrite
        # a snapshot. The enclosing transaction rolls back its current-row edit.
        db.execute('''INSERT INTO note_revisions
            (id,title,body,source,version,created_at,updated_at)
            SELECT id,title,body,source,version,created_at,updated_at FROM notes WHERE id=?''',
            (note_id,))

    @staticmethod
    def _selection(db):
        row = db.execute('''SELECT s.note_id,s.selected_version,s.revision,s.activity_id,
            s.selected_at,n.version AS current_version,r.version AS snapshot_version
            FROM note_selection s LEFT JOIN notes n ON n.id=s.note_id
            LEFT JOIN note_revisions r ON r.id=s.note_id AND r.version=s.selected_version
            WHERE s.id=1''').fetchone()
        result = dict(row)
        snapshot_version = result.pop('snapshot_version')
        chosen = result['note_id'] is not None
        result['missing'] = chosen and (result['current_version'] is None or snapshot_version is None)
        result['stale'] = chosen and result['current_version'] is not None and (
            result['selected_version'] != result['current_version'])
        result['status'] = ('none' if not chosen else 'missing' if result['missing']
                            else 'stale' if result['stale'] else 'selected')
        return result

    def selection(self):
        """One read-only snapshot; updates do not promote the chosen version."""
        with self.connection() as db:
            return self._selection(db)

    def workspace_id(self):
        """Stable workspace identity without consulting the personal selection."""
        with self.connection() as db:
            return db.execute('SELECT activity_id FROM note_selection WHERE id=1').fetchone()[0]

    def select(self, note_id, *, version, expected_revision):
        """Explicit CAS selection of an existing version, or explicit unlink."""
        self._version(expected_revision, 'expected_revision')
        if note_id is None:
            if version is not None:
                raise ValueError('version must be null when clearing selection')
        else:
            note_id = self.text(note_id, 'note_id', 256)
            self._version(version)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._selection(db)
            if current['revision'] != expected_revision:
                raise SelectionConflict('selection_conflict')
            if expected_revision == 9223372036854775807:
                raise ValueError('selection revision exhausted')
            if note_id is not None:
                found = db.execute('''SELECT r.version FROM note_revisions r
                    JOIN notes n ON n.id=r.id WHERE r.id=? AND r.version=?''',
                    (note_id, version)).fetchone()
                if found is None:
                    raise KeyError((note_id, version))
            cursor = db.execute('''UPDATE note_selection SET note_id=?,selected_version=?,
                revision=revision+1,selected_at=? WHERE id=1 AND revision=?''',
                (note_id, version, time.time() if note_id is not None else None, expected_revision))
            if cursor.rowcount != 1:
                raise SelectionConflict('selection_conflict')
            return self._selection(db)

    def create(self, title, body, source='用户手动保存'):
        title = self.text(title, 'title', 160)
        body = self.text(body, 'body', 20000)
        source = self.text(source, 'source', 500)
        now = time.time()
        with self.connection() as db:
            note_id = uuid4().hex
            db.execute('INSERT INTO notes VALUES (?,?,?,?,1,?,?)', (note_id, title, body, source, now, now))
            self._record_revision(db, note_id)
            return dict(db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone())

    def search(self, query=''):
        query = self.text(query, 'query', 200, False)
        with self.connection() as db:
            # Literal substring search handles Chinese without segmentation; no wildcard interpretation.
            rows = db.execute('''SELECT id,title,source,version,created_at,updated_at,
                substr(body,1,240) AS excerpt FROM notes
                WHERE ?='' OR instr(lower(title),lower(?))>0 OR instr(lower(body),lower(?))>0
                ORDER BY updated_at DESC,id LIMIT 50''', (query, query, query)).fetchall()
            return [dict(row) for row in rows]

    def get(self, note_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
            if row is None:
                raise KeyError(note_id)
            return dict(row)

    def get_version(self, note_id, version):
        """Read the exact chosen version; deleted notes never expose old bodies."""
        self._version(version)
        with self.connection() as db:
            row = db.execute('''SELECT r.id,r.title,r.body,r.source,r.version,r.created_at,r.updated_at
                FROM note_revisions r JOIN notes n ON n.id=r.id
                WHERE r.id=? AND r.version=?''', (note_id, version)).fetchone()
            if row is None:
                raise KeyError((note_id, version))
            return dict(row)

    def get_version_info(self, note_id, version):
        """Read exact version metadata without fetching its body."""
        self._version(version)
        with self.connection() as db:
            row = db.execute('''SELECT r.id,r.title,r.source,r.version,r.created_at,r.updated_at
                FROM note_revisions r JOIN notes n ON n.id=r.id
                WHERE r.id=? AND r.version=?''', (note_id, version)).fetchone()
            if row is None:
                raise KeyError((note_id, version))
            return dict(row)

    def history(self, note_id, limit=50):
        """Recent version metadata only; exact older versions remain readable."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid limit')
        with self.connection() as db:
            if db.execute('SELECT 1 FROM notes WHERE id=?', (note_id,)).fetchone() is None:
                raise KeyError(note_id)
            return [dict(row) for row in db.execute('''SELECT id,title,source,version,created_at,updated_at
                FROM note_revisions WHERE id=? ORDER BY version DESC LIMIT ?''', (note_id, limit))]

    def update(self, note_id, title, body, version):
        title = self.text(title, 'title', 160)
        body = self.text(body, 'body', 20000)
        self._version(version)
        if version == 9223372036854775807:
            raise ValueError('note version exhausted')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('UPDATE notes SET title=?,body=?,version=version+1,updated_at=? WHERE id=? AND version=?',
                (title, body, time.time(), note_id, version)).rowcount
            if not count:
                raise ValueError('note missing or version conflict')
            self._record_revision(db, note_id)
            return dict(db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone())

    def delete(self, note_id, version):
        self._version(version)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('DELETE FROM notes WHERE id=? AND version=?', (note_id, version)).rowcount:
                raise ValueError('note missing or version conflict')
            db.execute('DELETE FROM note_revisions WHERE id=?', (note_id,))
            # The predicate prevents deleting A from clearing a concurrently
            # reviewed selection of B. Both effects commit with note deletion.
            db.execute('''UPDATE note_selection SET note_id=NULL,selected_version=NULL,
                selected_at=NULL,revision=revision+1 WHERE id=1 AND note_id=?''', (note_id,))
        return {'deleted': True}
