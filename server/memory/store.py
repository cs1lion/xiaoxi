from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from ..config import settings

KINDS = ("fact", "episode", "todo", "self_state")
SOURCES = ("stated", "inferred", "uncertain")
STATUSES = ("active", "superseded", "done")
SCHEMA_VERSION = 1

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
    use_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_key_active ON memories(key) WHERE status = 'active';

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        existing = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if existing and existing != SCHEMA_VERSION:
            raise RuntimeError(
                f"memory.db schema v{existing} != v{SCHEMA_VERSION}; "
                f"备份后删除 {self.db_path} 再启动"
            )
        self._conn.executescript(SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()
        self.version = 0  # bumped on any write so the retriever can invalidate

    # ---------- low level ----------
    def _write(self, sql: str, params: Iterable[Any]) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            self.version += 1
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
    ) -> int:
        now = time.time()
        value = " ".join(value.strip().split())
        key = (key or value).strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, value FROM memories WHERE key = ? AND status = 'active'", (key,)
            ).fetchone()
            if row is not None and row["value"] == value:
                self._conn.execute(
                    """UPDATE memories
                       SET kind = ?, subject = ?, source = ?, importance = ?, confidence = ?, updated_at = ?
                       WHERE id = ?""",
                    (kind, subject, source, importance, confidence, now, row["id"]),
                )
                self._conn.commit()
                self.version += 1
                return int(row["id"])

            if row is not None:
                # same key, different value -> the old one stays as history
                self._conn.execute(
                    "UPDATE memories SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                supersedes = supersedes if supersedes is not None else int(row["id"])
            if supersedes is not None:
                self._conn.execute(
                    "UPDATE memories SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (now, supersedes),
                )
            cur = self._conn.execute(
                """INSERT INTO memories
                   (kind, subject, key, value, source, status, supersedes, importance,
                    confidence, session_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (kind, subject, key, value, source, status, supersedes, importance, confidence, session_id, now, now),
            )
            self._conn.commit()
            self.version += 1
            return int(cur.lastrowid)

    def add(self, mem: dict[str, Any]) -> int:
        return self.upsert(**mem)

    def get(self, memory_id: int) -> Memory | None:
        rows = self._query("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return Memory(**dict(rows[0])) if rows else None

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

    def delete(self, memory_id: int) -> bool:
        """Hard delete: the row itself, plus the uniqueness slot it occupied."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
            self.version += 1
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
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            self.version += 1
            return cur.rowcount

    def mark_used(self, ids: list[int]) -> None:
        if not ids:
            return
        now = time.time()
        with self._lock:
            self._conn.executemany(
                "UPDATE memories SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
                [(now, i) for i in ids],
            )
            self._conn.commit()
            self.version += 1

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

    def add_message(self, session_id: str, role: str, text: str, mode: str = "text") -> None:
        self._write(
            "INSERT INTO messages (session_id, role, text, mode, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, mode, time.time()),
        )

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


store = MemoryStore()