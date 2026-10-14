"""SQLite preferences with explicit provenance and fail-closed conflict handling.

No database is opened until PreferenceStore is constructed; its default is RAM.
IDs are opaque strings. Validation errors (including unknown evidence/claim IDs
for create/revise) raise ValueError. delete_evidence returns the number of deleted
 dependent claims; delete_claim returns whether a claim existed. Revision retains
source, scope and expiry, so revising an inferred claim never promotes it.
"""

from contextlib import contextmanager
import json
import math
import os
import sqlite3
import threading
import time
import uuid


class PreferenceStore:
    """Thread-safe independent store; clock returns Unix seconds.

    Claim dictionaries contain id, dimension, value, evidence_ids, scope, source,
    valid_until, created_at, supersedes, status and active. Status is one of active,
    candidate, conflicted, expired or superseded. list_claims defaults to active
    records only; pass include_inactive=True for the full remaining history.
    """

    DIMENSIONS = {
        "humor": frozenset(("off", "light")),
        "detail": frozenset(("short", "detailed")),
        "support_style": frozenset(("listen", "help")),
        "question_style": frozenset(("minimal", "normal")),
    }
    SOURCES = frozenset(("explicit_user", "manual", "inferred"))
    ACTIVITIES = frozenset(("work", "writing", "technical", "chat"))

    def __init__(self, path=":memory:", clock=time.time):
        if not callable(clock):
            raise ValueError("clock must be callable")
        if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
            raise ValueError("path must be a nonempty filesystem path or :memory:")
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA foreign_keys = ON")
            # Scrub deleted payloads in database pages, not merely logical rows.
            self._db.execute("PRAGMA secure_delete = ON")
            with self._transaction():
                self._db.execute("""CREATE TABLE IF NOT EXISTS preference_evidence (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT NOT NULL,
                    subject TEXT NOT NULL, created_at REAL NOT NULL)""")
                self._db.execute("""CREATE TABLE IF NOT EXISTS preference_claims (
                    id TEXT PRIMARY KEY, dimension TEXT NOT NULL, value TEXT NOT NULL,
                    scope TEXT NOT NULL, source TEXT NOT NULL, valid_until REAL,
                    created_at REAL NOT NULL, supersedes TEXT,
                    superseded INTEGER NOT NULL DEFAULT 0)""")
                self._db.execute("""CREATE TABLE IF NOT EXISTS preference_links (
                    claim_id TEXT NOT NULL REFERENCES preference_claims(id) ON DELETE CASCADE,
                    evidence_id TEXT NOT NULL REFERENCES preference_evidence(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL, PRIMARY KEY (claim_id, evidence_id))""")
        except BaseException:
            self._db.close()
            self._closed = True
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("PreferenceStore is closed")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def get_evidence(self, evidence_id):
        """Explicit user inspection only; never used by model context assembly."""
        self._string(evidence_id, 'evidence_id')
        with self._transaction():
            row = self._db.execute('SELECT * FROM preference_evidence WHERE id=?', (evidence_id,)).fetchone()
            if row is None:
                raise KeyError(evidence_id)
            return dict(row)

    @staticmethod
    def _string(value, name):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(name + " must be a nonempty string")
        return value

    @classmethod
    def _source(cls, source):
        if not isinstance(source, str) or source not in cls.SOURCES:
            raise ValueError("source must be explicit_user, manual or inferred")
        return source

    @staticmethod
    def _timestamp(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(name + " must be finite Unix seconds")
        try:
            result = float(value)
        except (OverflowError, ValueError):
            raise ValueError(name + " must be finite Unix seconds") from None
        if not math.isfinite(result):
            raise ValueError(name + " must be finite Unix seconds")
        return result

    def _now(self):
        return self._timestamp(self._clock(), "clock result")

    @classmethod
    def _scope(cls, scope):
        if scope is None:
            scope = {}
        if not isinstance(scope, dict):
            raise ValueError("scope must be a dictionary or None")
        if any(key not in ("activity", "session_id", "topic") for key in scope):
            raise ValueError("unknown scope key")
        for key, value in scope.items():
            cls._string(value, "scope." + key)
            if key == "activity" and value not in cls.ACTIVITIES:
                raise ValueError("invalid scope activity")
        return json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def _dimension_value(cls, dimension, value):
        if not isinstance(dimension, str) or dimension not in cls.DIMENSIONS:
            raise ValueError("invalid preference dimension")
        if not isinstance(value, str) or value not in cls.DIMENSIONS[dimension]:
            raise ValueError("invalid value for preference dimension")

    @classmethod
    def _evidence_ids(cls, evidence_ids):
        if not isinstance(evidence_ids, (list, tuple)) or not evidence_ids:
            raise ValueError("evidence_ids must be a nonempty list or tuple")
        ids = [cls._string(item, "evidence ID") for item in evidence_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate evidence IDs")
        return ids

    def _validate_evidence(self, ids):
        for evidence_id in ids:
            row = self._db.execute(
                "SELECT subject FROM preference_evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
            if row is None or row["subject"] != "user":
                raise ValueError("all evidence must exist and have subject 'user'")

    def add_evidence(self, text, source="explicit_user", subject="user"):
        self._string(text, "text")
        self._source(source)
        self._string(subject, "subject")
        with self._transaction():
            result = dict(id=uuid.uuid4().hex, text=text, source=source,
                          subject=subject, created_at=self._now())
            self._db.execute(
                "INSERT INTO preference_evidence VALUES (:id,:text,:source,:subject,:created_at)",
                result,
            )
            return result

    def _insert_claim(self, dimension, value, ids, scope, source, valid_until, now,
                      supersedes=None):
        claim_id = uuid.uuid4().hex
        self._db.execute("""INSERT INTO preference_claims
            (id,dimension,value,scope,source,valid_until,created_at,supersedes)
            VALUES (?,?,?,?,?,?,?,?)""",
            (claim_id, dimension, value, scope, source, valid_until, now, supersedes))
        self._db.executemany("INSERT INTO preference_links VALUES (?,?,?)",
                             [(claim_id, item, index) for index, item in enumerate(ids)])
        return claim_id

    def create_claim(self, dimension, value, evidence_ids, scope=None,
                     source="explicit_user", valid_until=None):
        self._dimension_value(dimension, value)
        ids = self._evidence_ids(evidence_ids)
        scope_json = self._scope(scope)
        self._source(source)
        if valid_until is not None:
            valid_until = self._timestamp(valid_until, "valid_until")
        with self._transaction():
            self._validate_evidence(ids)
            now = self._now()
            claim_id = self._insert_claim(dimension, value, ids, scope_json, source,
                                           valid_until, now)
            return next(row for row in self._claims(now) if row["id"] == claim_id)

    def _claims(self, now):
        rows = [dict(row) for row in self._db.execute(
            "SELECT * FROM preference_claims ORDER BY created_at, rowid")]
        values = {}
        for row in rows:
            if row["superseded"]:
                row["status"] = "superseded"
            elif row["valid_until"] is not None and row["valid_until"] <= now:
                row["status"] = "expired"
            elif row["source"] not in ("explicit_user", "manual"):
                row["status"] = "candidate"
            else:
                row["status"] = "active"
                values.setdefault((row["scope"], row["dimension"]), set()).add(row["value"])
        for row in rows:
            if row["status"] == "active" and len(values[(row["scope"], row["dimension"])]) > 1:
                row["status"] = "conflicted"
            row["active"] = row["status"] == "active"
            del row["superseded"]
            row["scope"] = json.loads(row["scope"])
            row["evidence_ids"] = [link[0] for link in self._db.execute(
                "SELECT evidence_id FROM preference_links WHERE claim_id=? ORDER BY position",
                (row["id"],))]
        return rows

    def list_claims(self, include_inactive=False):
        if not isinstance(include_inactive, bool):
            raise ValueError("include_inactive must be a bool")
        with self._transaction():
            rows = self._claims(self._now())
            return rows if include_inactive else [row for row in rows if row["active"]]

    def revise_claim(self, id, value, evidence_ids):
        self._string(id, "claim ID")
        ids = self._evidence_ids(evidence_ids)
        with self._transaction():
            old = self._db.execute("SELECT * FROM preference_claims WHERE id=?", (id,)).fetchone()
            if old is None or old["superseded"]:
                raise ValueError("claim does not exist or is already superseded")
            self._dimension_value(old["dimension"], value)
            self._validate_evidence(ids)
            now = self._now()
            new_id = self._insert_claim(old["dimension"], value, ids, old["scope"],
                                        old["source"], old["valid_until"], now, id)
            self._db.execute("UPDATE preference_claims SET superseded=1 WHERE id=?", (id,))
            return next(row for row in self._claims(now) if row["id"] == new_id)

    def delete_evidence(self, id):
        """Erase evidence and all directly dependent claims; return claim count.

        Superseded predecessors stay superseded even when their replacement is
        erased. Revisions backed solely by different evidence remain valid.
        """
        self._string(id, "evidence ID")
        with self._transaction():
            deleted = self._db.execute("""DELETE FROM preference_claims WHERE id IN
                (SELECT claim_id FROM preference_links WHERE evidence_id=?)""", (id,)).rowcount
            self._db.execute("DELETE FROM preference_evidence WHERE id=?", (id,))
            return deleted

    def delete_claim(self, id):
        self._string(id, "claim ID")
        with self._transaction():
            return self._db.execute("DELETE FROM preference_claims WHERE id=?", (id,)).rowcount > 0

    def close(self):
        """Close the connection; safe to call more than once."""
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True
