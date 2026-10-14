from __future__ import annotations

import sqlite3
import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from ..config import settings

KINDS = ("fact", "episode", "todo", "self_state")
SOURCES = ("stated", "inferred", "uncertain")
STATUSES = ("active", "superseded", "done", "conflict")
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    subject       TEXT NOT NULL DEFAULT 'user',
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'stated',
    status        TEXT NOT NULL DEFAULT 'active',
    supersedes    INTEGER,
    importance    REAL NOT NULL DEFAULT 0.5,
    confidence    REAL NOT NULL DEFAULT 0.8,
    session_id    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    last_used_at  REAL,
    use_count     INTEGER NOT NULL DEFAULT 0,
    evidence_message_id INTEGER,
    evidence_quote TEXT NOT NULL DEFAULT '',
    evidence_state TEXT NOT NULL DEFAULT 'legacy',
    decision      TEXT NOT NULL DEFAULT 'legacy',
    last_observed_message_id INTEGER,
    identity_slot TEXT NOT NULL DEFAULT '',
    preference_target TEXT NOT NULL DEFAULT '',
    preference_level TEXT NOT NULL DEFAULT '',
    preference_scope TEXT NOT NULL DEFAULT '',
    preference_as_of REAL,
    retired_by_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    turns       INTEGER NOT NULL DEFAULT 0,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'text',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS memory_control (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    write_epoch INTEGER NOT NULL DEFAULT 0,
    write_floor INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO memory_control(id, write_epoch, write_floor) VALUES (1, 0, 0);
"""


@dataclass(slots=True)
class Memory:
    id: int
    kind: str
    subject: str
    key: str
    value: str
    source: str
    status: str
    supersedes: int | None
    importance: float
    confidence: float
    session_id: str | None
    created_at: float
    updated_at: float
    last_used_at: float | None
    use_count: int
    evidence_message_id: int | None = None
    evidence_quote: str = ""
    evidence_state: str = "legacy"
    decision: str = "legacy"
    last_observed_message_id: int | None = None
    identity_slot: str = ""
    preference_target: str = ""
    preference_level: str = ""
    preference_scope: str = ""
    preference_as_of: float | None = None
    retired_by_message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate()
        except BaseException:
            self._conn.close()
            self._closed = True
            raise
        self.version = 0  # bumped on any write so the retriever can invalidate

    # ---------- low level ----------
    @contextmanager
    def _transaction(self, *, track_write: bool = False, invalidate: bool = False):
        with self._lock:
            if self._closed:
                raise RuntimeError("MemoryStore is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            changes_before = self._conn.total_changes
            try:
                yield self._conn
                if invalidate:
                    self._invalidate_pending(self._conn)
                self._conn.commit()
                if track_write and self._conn.total_changes != changes_before:
                    self.version += 1
            except BaseException:
                self._conn.rollback()
                raise

    def _migrate(self) -> None:
        """Add fields without guessing preference semantics for existing rows."""
        with self._transaction() as db:
            existing = int(db.execute("PRAGMA user_version").fetchone()[0])
            if existing > SCHEMA_VERSION:
                raise RuntimeError(f"memory database schema v{existing} is newer than supported v{SCHEMA_VERSION}")
            # executescript commits an open transaction; execute each statement instead.
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memories)")}
            additions = {
                "evidence_message_id": "INTEGER",
                "evidence_quote": "TEXT NOT NULL DEFAULT ''",
                "evidence_state": "TEXT NOT NULL DEFAULT 'legacy'",
                "decision": "TEXT NOT NULL DEFAULT 'legacy'",
                "last_observed_message_id": "INTEGER",
                "identity_slot": "TEXT NOT NULL DEFAULT ''",
                "preference_target": "TEXT NOT NULL DEFAULT ''",
                "preference_level": "TEXT NOT NULL DEFAULT ''",
                "preference_scope": "TEXT NOT NULL DEFAULT ''",
                "preference_as_of": "REAL",
                "retired_by_message_id": "INTEGER",
            }
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
            db.execute("DROP INDEX IF EXISTS idx_mem_key_active")
            db.execute("DROP INDEX IF EXISTS idx_mem_identity_active")
            db.execute("""CREATE UNIQUE INDEX idx_mem_identity_active
                ON memories(kind, subject, key, identity_slot) WHERE status = 'active'""")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @property
    def write_epoch(self) -> int:
        return self.write_state()[0]

    @property
    def write_floor(self) -> int:
        return self.write_state()[1]

    def write_state(self) -> tuple[int, int]:
        row = self._query("SELECT write_epoch, write_floor FROM memory_control WHERE id = 1")[0]
        return int(row[0]), int(row[1])

    @staticmethod
    def _invalidate_pending(db) -> None:
        # This conservative global floor also blocks fresh re-extraction of old
        # messages after restart; merely cancelling the current await is insufficient.
        db.execute("""UPDATE memory_control SET write_epoch = write_epoch + 1,
                      write_floor = MAX(write_floor, COALESCE((SELECT MAX(id) FROM messages), 0))
                      WHERE id = 1""")

    def invalidate_pending(self) -> int:
        with self._transaction(invalidate=True) as db:
            epoch = int(db.execute("SELECT write_epoch FROM memory_control WHERE id = 1").fetchone()[0])
        return epoch + 1

    def _write(self, sql: str, params: Iterable[Any]) -> sqlite3.Cursor:
        with self._transaction(track_write=True) as db:
            cur = db.execute(sql, tuple(params))
        return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # ---------- memories ----------
    def upsert(
        self,
        *,
        kind: str,
        value: str,
        key: str | None = None,
        subject: str = "user",
        source: str = "stated",
        status: str = "active",
        importance: float = 0.5,
        confidence: float = 0.8,
        session_id: str | None = None,
        supersedes: int | None = None,
        evidence_message_id: int | None = None,
        evidence_quote: str = "",
        evidence_state: str = "manual",
        decision: str | None = None,
        expected_write_epoch: int | None = None,
        identity_slot: str = "",
        preference_target: str = "",
        preference_level: str = "",
        preference_scope: str = "",
        preference_as_of: float | None = None,
        force_revision: bool = False,
    ) -> int | None:
        if status not in STATUSES or source not in SOURCES:
            raise ValueError("invalid memory status or source")
        if evidence_state not in ("legacy", "manual", "linked"):
            raise ValueError("invalid evidence state")
        if not isinstance(evidence_quote, str):
            raise ValueError("evidence_quote must be text")
        if evidence_message_id is not None and (type(evidence_message_id) is not int or evidence_message_id < 1):
            raise ValueError("evidence_message_id must be a positive integer")
        if expected_write_epoch is not None and (type(expected_write_epoch) is not int or expected_write_epoch < 0):
            raise ValueError("expected_write_epoch must be a nonnegative integer")
        if not all(isinstance(field, str) for field in
                   (identity_slot, preference_target, preference_level, preference_scope)):
            raise ValueError("invalid preference fields")
        structured = bool(preference_target or preference_level or preference_scope)
        if structured and (not preference_target or preference_level not in ("like", "favorite", "dislike")
                           or preference_scope not in ("long_term", "recent", "current", "unspecified")):
            raise ValueError("incomplete preference fields")
        if structured and evidence_state != "linked":
            raise ValueError("structured preferences require verified user evidence")
        if preference_as_of is not None and not structured:
            raise ValueError("preference time requires a structured preference")
        if type(force_revision) is not bool:
            raise ValueError("force_revision must be a boolean")
        now = time.time()
        value = " ".join(value.strip().split())
        key = (key or value).strip().lower()
        with self._transaction(track_write=True) as db:
            control = db.execute("SELECT write_epoch, write_floor FROM memory_control WHERE id = 1").fetchone()
            if expected_write_epoch is not None and control["write_epoch"] != expected_write_epoch:
                return None
            if evidence_state == "linked" and evidence_message_id is not None and evidence_message_id <= control["write_floor"]:
                return None
            if evidence_state == "linked":
                evidence = db.execute("SELECT session_id, role, text, created_at FROM messages WHERE id = ?",
                                      (evidence_message_id,)).fetchone()
                if (evidence is None or evidence["role"] != "user" or not evidence_quote.strip()
                        or evidence_quote not in evidence["text"]
                        or (session_id is not None and evidence["session_id"] != session_id)):
                    raise ValueError("linked evidence requires an exact quote from a user message in this session")
                session_id = evidence["session_id"]
            if structured:
                # The final write transaction repeats the semantic gate. Model
                # metadata and a caller-supplied slot cannot grant set membership.
                from .preference import normalize_preference, preference_change_allowed
                verified = normalize_preference(
                    {"target": preference_target, "level": preference_level, "scope": preference_scope},
                    evidence_quote, value, key, kind, subject, source,
                    message_text=evidence["text"],
                )
                if verified is None:
                    raise ValueError("unsupported preference evidence")
                slot = json.dumps([preference_target, preference_scope], ensure_ascii=False, separators=(",", ":"))
                if identity_slot and identity_slot != slot:
                    raise ValueError("preference slot does not match verified evidence")
                identity_slot = slot
                # Never accept a model/caller timestamp in place of the message.
                preference_as_of = evidence["created_at"]
                # A superseded slot stays retired after further changes or a
                # restart. Only looking at active rivals allows an old favorite
                # to revive once its replacement is itself superseded.
                retired = db.execute(
                    """SELECT MAX(retired_by_message_id) FROM memories
                       WHERE kind = ? AND subject = ? AND key = ? AND
                         (identity_slot = ? OR (? = 'favorite' AND preference_level = 'favorite')
                          OR (preference_target = ? AND preference_level != '' AND
                              (preference_level = 'dislike') != (? = 'dislike')))""",
                    (kind, subject, key, identity_slot, preference_level, preference_target, preference_level),
                ).fetchone()[0]
                if retired is not None and evidence_message_id <= retired:
                    return None
            elif evidence_state == "linked" and identity_slot:
                raise ValueError("automatic slots require verified preference fields")
            row = db.execute(
                "SELECT * FROM memories WHERE kind = ? AND subject = ? AND key = ? AND identity_slot = ? AND status = 'active'",
                (kind, subject, key, identity_slot),
            ).fetchone()
            replacements: list[sqlite3.Row] = []
            if evidence_state == "linked":
                family = list(db.execute(
                    "SELECT * FROM memories WHERE kind = ? AND subject = ? AND key = ? AND status = 'active'",
                    (kind, subject, key)))
                if structured:
                    # Unknown legacy/manual semantics block an automatic widening
                    # of the property's cardinality. We never label old text by guess.
                    blockers = [m for m in family if not m["preference_target"]]
                    rivals = [m for m in family if m["id"] != (row["id"] if row else None) and (
                        (preference_level == "favorite" and m["preference_level"] == "favorite"
                         and m["preference_target"] != preference_target)
                        or (m["preference_target"] == preference_target and
                            (m["preference_level"] == "dislike") != (preference_level == "dislike")))]
                    if row is not None and row["preference_level"] != preference_level:
                        rivals.append(row)
                    latest = max((max(m["last_observed_message_id"] or 0,
                                      m["evidence_message_id"] or 0) for m in rivals), default=0)
                    if rivals and evidence_message_id < latest:
                        return None
                    can_change = (source == "stated" and evidence_message_id > latest
                                  and preference_change_allowed(evidence_quote, level=preference_level,
                                                                message_text=evidence["text"]))
                    if blockers or (rivals and not can_change):
                        status, decision, supersedes = "conflict", "conflict", None
                    elif rivals:
                        replacements = rivals
                        # A single deterministic predecessor is sufficient for
                        # navigation; all retired rows remain in the property history.
                        supersedes = max(rivals, key=lambda m: m["id"])["id"]
                        decision = "update"
                elif any(m["identity_slot"] for m in family):
                    # An unstructured output cannot replace or silently join a set.
                    status, decision, supersedes = "conflict", "conflict", None
            if (status == "active" and evidence_state == "linked" and row is not None
                    and (row["last_observed_message_id"] is not None or row["evidence_message_id"] is not None)):
                latest_message = max(row["last_observed_message_id"] or 0, row["evidence_message_id"] or 0)
                if latest_message > evidence_message_id:
                    return None
                if latest_message == evidence_message_id and row["value"] != value:
                    status, decision, supersedes = "conflict", "conflict", None
            same_claim = (row is not None and structured and
                          row["preference_target"] == preference_target and
                          row["preference_level"] == preference_level and
                          row["preference_scope"] == preference_scope)
            if status == "active" and row is not None and not force_revision and (row["value"] == value or same_claim):
                # Repetition is not fresh evidence and cannot refresh an inference's lifetime.
                # A current explicit assertion may strengthen its source, never weaken it.
                strength = {"uncertain": 0, "inferred": 1, "stated": 2}
                stronger = strength[source] > strength.get(row["source"], -1)
                valid_new_evidence = evidence_state in ("manual", "linked")
                replace_evidence = valid_new_evidence and (
                    stronger or (source == row["source"] and row["evidence_state"] == "legacy"))
                retained_source = source if stronger and valid_new_evidence else row["source"]
                latest_observed = row["last_observed_message_id"]
                if evidence_state == "linked" and strength[source] >= strength.get(row["source"], -1):
                    latest_observed = max(latest_observed or 0, row["evidence_message_id"] or 0, evidence_message_id)
                db.execute(
                    """UPDATE memories SET source = ?, evidence_message_id = ?, evidence_quote = ?,
                       evidence_state = ?, session_id = ?, confidence = ?, decision = 'noop',
                       last_observed_message_id = ?, preference_as_of = ? WHERE id = ?""",
                    (retained_source,
                     evidence_message_id if replace_evidence else row["evidence_message_id"],
                     evidence_quote if replace_evidence else row["evidence_quote"],
                     evidence_state if replace_evidence else row["evidence_state"],
                     session_id if replace_evidence else row["session_id"],
                     confidence if stronger and valid_new_evidence else row["confidence"],
                     latest_observed,
                     preference_as_of if replace_evidence and structured else row["preference_as_of"], row["id"]),
                )
                memory_id = int(row["id"])
            else:
                prior = db.execute("SELECT * FROM memories WHERE id = ?", (supersedes,)).fetchone() if supersedes is not None else None
                protected = source != "stated" and any(
                    candidate is not None and candidate["source"] == "stated" and candidate["status"] == "active"
                    for candidate in (row, prior)
                )
                if status == "conflict" or protected:
                    status, decision, supersedes = "conflict", "conflict", None
                elif status == "active":
                    if prior is not None and ((prior["kind"], prior["subject"], prior["key"]) != (kind, subject, key)
                            or (prior["identity_slot"] != identity_slot and prior not in replacements)):
                        raise ValueError("supersedes must refer to the same memory identity")
                    if row is not None:
                        db.execute("""UPDATE memories SET status = 'superseded', updated_at = ?,
                                   retired_by_message_id = COALESCE(?, retired_by_message_id) WHERE id = ?""",
                                   (now, evidence_message_id if structured else None, row["id"]))
                        supersedes = supersedes if supersedes is not None else int(row["id"])
                    if supersedes is not None:
                        db.execute("""UPDATE memories SET status = 'superseded', updated_at = ?,
                                   retired_by_message_id = COALESCE(?, retired_by_message_id) WHERE id = ?""",
                                   (now, evidence_message_id if structured else None, supersedes))
                    for replaced in replacements:
                        db.execute("""UPDATE memories SET status = 'superseded', updated_at = ?,
                                   retired_by_message_id = COALESCE(?, retired_by_message_id) WHERE id = ?""",
                                   (now, evidence_message_id, replaced["id"]))
                    decision = decision or ("update" if row is not None or supersedes is not None else "add")
                else:
                    supersedes = None
                    decision = decision or "add"
                cur = db.execute(
                    """INSERT INTO memories
                       (kind, subject, key, value, source, status, supersedes, importance,
                        confidence, session_id, created_at, updated_at, evidence_message_id,
                        evidence_quote, evidence_state, decision, last_observed_message_id,
                        identity_slot, preference_target, preference_level, preference_scope, preference_as_of)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (kind, subject, key, value, source, status, supersedes, importance, confidence,
                     session_id, now, now, evidence_message_id, evidence_quote, evidence_state, decision,
                     evidence_message_id if evidence_state == "linked" else None,
                     identity_slot, preference_target, preference_level, preference_scope, preference_as_of),
                )
                memory_id = int(cur.lastrowid)
        return memory_id

    def add(self, mem: dict[str, Any]) -> int | None:
        return self.upsert(**mem)

    def get(self, memory_id: int) -> Memory | None:
        rows = self._query("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return Memory(**dict(rows[0])) if rows else None

    def active_for(self, kind: str, subject: str, key: str, identity_slot: str = "") -> Memory | None:
        rows = self._query("SELECT * FROM memories WHERE kind = ? AND subject = ? AND key = ? AND identity_slot = ? AND status = 'active'",
                           (kind, subject, key.strip().lower(), identity_slot))
        return Memory(**dict(rows[0])) if rows else None

    def count_inferences(self, session_id: str) -> int:
        return int(self._query("SELECT COUNT(*) FROM memories WHERE session_id = ? AND source IN ('inferred', 'uncertain')",
                               (session_id,))[0][0])

    def list_memories(
        self,
        *,
        status: str | None = "active",
        kind: str | None = None,
        subject: str | None = None,
        limit: int = 200,
        include_history: bool = False,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE 1 = 1"
        params: list[Any] = []
        if status and not include_history:
            sql += " AND status = ?"
            params.append(status)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [Memory(**dict(r)) for r in self._query(sql, params)]

    def history_for_key(self, key: str, limit: int = 10) -> list[Memory]:
        rows = self._query(
            "SELECT * FROM memories WHERE key = ? ORDER BY created_at DESC LIMIT ?", (key, limit)
        )
        return [Memory(**dict(r)) for r in rows]

    def retrieval_candidates(self, *, include_done: bool = False) -> list[Memory]:
        """Read one live eligible set; historical rows cannot consume its limit.

        Unlike the paginated management list, retrieval has no implicit 5000-row
        cut-off. The retriever indexes and ranks this same query-time snapshot.
        """
        statuses = ("active", "done") if include_done else ("active",)
        placeholders = ",".join("?" for _ in statuses)
        rows = self._query(
            f"SELECT * FROM memories WHERE status IN ({placeholders}) "
            "ORDER BY updated_at DESC, id DESC", statuses)
        return [Memory(**dict(row)) for row in rows]

    def delete(self, memory_id: int) -> bool:
        """Hard delete: the row itself, plus the uniqueness slot it occupied."""
        with self._transaction(track_write=True, invalidate=True) as db:
            db.execute("UPDATE memories SET supersedes = NULL WHERE supersedes = ?", (memory_id,))
            cur = db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0

    def delete_all(self, *, kind: str | None = None, subject: str | None = None) -> int:
        sql = "DELETE FROM memories WHERE 1 = 1"
        params: list[Any] = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        with self._transaction(track_write=True, invalidate=True) as db:
            db.execute("UPDATE memories SET supersedes = NULL WHERE supersedes IN (" +
                       sql.replace("DELETE FROM", "SELECT id FROM", 1) + ")", params)
            cur = db.execute(sql, params)
        return cur.rowcount

    def delete_key(self, key: str, *, kind: str | None = None, subject: str | None = None,
                   identity_slot: str | None = None) -> int:
        sql = "DELETE FROM memories WHERE key = ?"
        params: list[Any] = [key.strip().lower()]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if subject is not None:
            sql += " AND subject = ?"
            params.append(subject)
        if identity_slot is not None:
            sql += " AND identity_slot = ?"
            params.append(identity_slot)
        with self._transaction(track_write=True, invalidate=True) as db:
            db.execute("UPDATE memories SET supersedes = NULL WHERE supersedes IN (" +
                       sql.replace("DELETE FROM", "SELECT id FROM", 1) + ")", params)
            cur = db.execute(sql, params)
        return cur.rowcount

    def mark_used(self, ids: list[int]) -> None:
        if not ids:
            return
        now = time.time()
        with self._transaction(track_write=True) as db:
            db.executemany(
                "UPDATE memories SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
                [(now, i) for i in ids],
            )

    def set_status(self, memory_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"bad status: {status}")
        self._write("UPDATE memories SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), memory_id))

    def link_supersedes(self, new_id: int, old_id: int) -> None:
        """New value wins, old row stays queryable via history_for_key()."""
        self._write("UPDATE memories SET supersedes = ? WHERE id = ?", (old_id, new_id))

    # ---------- sessions / messages ----------
    def start_session(self, session_id: str | None = None) -> str:
        sid = session_id or uuid.uuid4().hex[:12]
        self._write("INSERT OR REPLACE INTO sessions (id, started_at) VALUES (?, ?)", (sid, time.time()))
        return sid

    def end_session(self, session_id: str, summary: str | None = None) -> None:
        self._write(
            "UPDATE sessions SET ended_at = ?, summary = COALESCE(?, summary) WHERE id = ?",
            (time.time(), summary, session_id),
        )

    def add_message(self, session_id: str, role: str, text: str, mode: str = "text") -> int:
        cursor = self._write(
            "INSERT INTO messages (session_id, role, text, mode, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, mode, time.time()),
        )
        return int(cursor.lastrowid)

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT id, session_id, role, text, mode, created_at FROM messages WHERE id = ?", (message_id,))
        return dict(rows[0]) if rows else None

    def recent_message_records(self, session_id: str, limit: int = 12,
                               before_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, session_id, role, text, mode, created_at FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if before_id is not None:
            sql += " AND id <= ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in reversed(self._query(sql, params))]

    def recent_messages(self, session_id: str, limit: int = 12) -> list[dict[str, str]]:
        rows = self._query(
            "SELECT role, text, mode FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def count(self, status: str = "active") -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM memories WHERE status = ?", (status,))
        return int(rows[0]["n"]) if rows else 0

    def recent_turns_with_questions(self, session_id: str, limit: int = 6) -> list[dict[str, str]]:
        return self.recent_messages(session_id, limit=limit)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True


store = MemoryStore()
