"""Independent, single-user SQLite task/reminder store (no memory dependency).

Times are UTC Unix seconds. All mutations run under an RLock and BEGIN
IMMEDIATE; occurrence uniqueness also protects independent store connections.
Quiet hours only change delivery_mode, never suppress the durable inbox.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AssistantStore:
    SCHEMA_VERSION = 1
    EXPIRY_SECONDS = 24 * 60 * 60
    TASK_STATUSES = {"open", "in_progress", "waiting", "done", "cancelled"}
    DEFAULT_PREFERENCES = {
        "reminder_enabled": True,
        "followup_enabled": False,
        "recommendation_enabled": False,
        "voice_interrupt_enabled": False,
        "timezone": "UTC",
        "quiet_hours": None,
    }

    def __init__(self, path="data/assistant.db", clock=time.time):
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.path = str(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._closed = False
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate()
        except Exception:
            self._conn.close()
            self._closed = True
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise ValueError("store is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _migrate(self):
        with self._transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise ValueError("assistant database schema is newer than supported")
            if version < 1:
                statements = [
                    """CREATE TABLE tasks (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                        status TEXT NOT NULL, next_action TEXT NOT NULL DEFAULT '',
                        due_at REAL, timezone TEXT NOT NULL DEFAULT 'UTC',
                        created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        completed_at REAL, version INTEGER NOT NULL)""",
                    """CREATE TABLE reminders (
                        id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id),
                        content TEXT NOT NULL, scheduled_at REAL NOT NULL,
                        timezone TEXT NOT NULL, status TEXT NOT NULL,
                        expires_at REAL NOT NULL, created_at REAL NOT NULL,
                        updated_at REAL NOT NULL, version INTEGER NOT NULL)""",
                    """CREATE TABLE notifications (
                        id TEXT PRIMARY KEY, category TEXT NOT NULL,
                        source_type TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES reminders(id),
                        occurrence_key TEXT NOT NULL UNIQUE, content TEXT NOT NULL,
                        status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                        scheduled_at REAL NOT NULL, delivery_mode TEXT NOT NULL,
                        missed INTEGER NOT NULL, acknowledged_at REAL, dismissed_at REAL,
                        version INTEGER NOT NULL)""",
                    "CREATE TABLE preferences (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)",
                    """CREATE TABLE requests (operation TEXT NOT NULL, request_id TEXT NOT NULL,
                        arguments TEXT NOT NULL, result TEXT NOT NULL,
                        PRIMARY KEY(operation, request_id))""",
                    "CREATE INDEX reminders_due ON reminders(status, scheduled_at)",
                    "CREATE INDEX reminders_task ON reminders(task_id)",
                    "CREATE INDEX notifications_source ON notifications(source_id, status)",
                ]
                for statement in statements:
                    db.execute(statement)
                db.execute("INSERT INTO preferences VALUES (1, ?)",
                           (json.dumps(self.DEFAULT_PREFERENCES),))
                db.execute("PRAGMA user_version = 1")

    @staticmethod
    def _text(value, name, nonempty=False):
        if not isinstance(value, str) or (nonempty and not value.strip()):
            raise ValueError(f"{name} must be {'nonempty ' if nonempty else ''}text")
        return value

    @staticmethod
    def _timestamp(value, name="scheduled_at"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be finite UTC seconds")
        try:
            value = float(value)
        except (ValueError, OverflowError):
            raise ValueError(f"{name} must be finite UTC seconds") from None
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite UTC seconds")
        return value

    def _now(self):
        return self._timestamp(self.clock(), "clock")

    @staticmethod
    def _zone(name):
        if not isinstance(name, str):
            raise ValueError("timezone must be an IANA timezone")
        if name == "UTC":
            return timezone.utc
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown IANA timezone") from None

    @staticmethod
    def _row(db, table, object_id):
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("id must be nonempty text")
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
        if row is None:
            raise KeyError(object_id)
        result = dict(row)
        if table == "notifications":
            result["missed"] = bool(result["missed"])
        return result

    @staticmethod
    def _prefs(db):
        return json.loads(db.execute("SELECT data FROM preferences WHERE id=1").fetchone()[0])

    def _replay(self, db, operation, request_id, arguments):
        if request_id is None:
            return None
        self._text(request_id, "request_id", True)
        row = db.execute("SELECT arguments,result FROM requests WHERE operation=? AND request_id=?",
                         (operation, request_id)).fetchone()
        if row is not None:
            if row["arguments"] != json.dumps(arguments, sort_keys=True):
                raise ValueError("request_id already used with different arguments")
            return json.loads(row["result"])
        return None

    @staticmethod
    def _remember(db, operation, request_id, arguments, result):
        if request_id is not None:
            db.execute("INSERT INTO requests VALUES (?, ?, ?, ?)",
                       (operation, request_id, json.dumps(arguments, sort_keys=True), json.dumps(result)))

    def create_task(self, title, description="", request_id=None):
        self._text(title, "title", True)
        self._text(description, "description")
        arguments = {"title": title, "description": description}
        with self._transaction() as db:
            replay = self._replay(db, "create_task", request_id, arguments)
            if replay is not None:
                return replay
            now, task_id = self._now(), uuid4().hex
            db.execute("""INSERT INTO tasks
                (id,title,description,status,timezone,created_at,updated_at,version)
                VALUES (?, ?, ?, 'open', ?, ?, ?, 1)""",
                       (task_id, title, description, self._prefs(db)["timezone"], now, now))
            result = self._row(db, "tasks", task_id)
            self._remember(db, "create_task", request_id, arguments, result)
            return result

    def list_tasks(self):
        with self._transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY created_at,id")]

    def update_task(self, task_id, changes):
        allowed = {"title", "description", "status", "next_action", "due_at", "timezone", "version"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError("unknown task changes")
        changes = dict(changes)
        for key in ("title", "description", "next_action"):
            if key in changes:
                self._text(changes[key], key, key == "title")
        if "status" in changes and (not isinstance(changes["status"], str) or changes["status"] not in self.TASK_STATUSES):
            raise ValueError("invalid task status")
        if "due_at" in changes and changes["due_at"] is not None:
            changes["due_at"] = self._timestamp(changes["due_at"], "due_at")
        if "timezone" in changes:
            self._zone(changes["timezone"])
        expected = changes.pop("version", None)
        if expected is not None and (type(expected) is not int or expected < 1):
            raise ValueError("version must be a positive integer")
        with self._transaction() as db:
            task = self._row(db, "tasks", task_id)
            if expected is not None and expected != task["version"]:
                raise ValueError("task version conflict")
            if not changes:
                return task
            now = self._now()
            changes.update(updated_at=now, version=task["version"] + 1)
            if "status" in changes:
                changes["completed_at"] = now if changes["status"] == "done" else None
            columns = ", ".join(f"{key}=?" for key in changes)
            db.execute(f"UPDATE tasks SET {columns} WHERE id=?", (*changes.values(), task_id))
            if changes.get("status") in {"done", "cancelled"}:
                ids = db.execute("SELECT id FROM reminders WHERE task_id=? AND status IN ('scheduled','queued')",
                                 (task_id,)).fetchall()
                for row in ids:
                    self._cancel(db, row["id"], now)
            return self._row(db, "tasks", task_id)

    def create_reminder(self, content, scheduled_at, task_id=None, request_id=None):
        self._text(content, "content", True)
        scheduled_at = self._timestamp(scheduled_at)
        arguments = {"content": content, "scheduled_at": scheduled_at, "task_id": task_id}
        with self._transaction() as db:
            replay = self._replay(db, "create_reminder", request_id, arguments)
            if replay is not None:
                return replay
            if task_id is not None:
                task = self._row(db, "tasks", task_id)
                if task["status"] in {"done", "cancelled"}:
                    raise ValueError("cannot remind a completed or cancelled task")
            now, reminder_id = self._now(), uuid4().hex
            db.execute("""INSERT INTO reminders VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, 1)""",
                       (reminder_id, task_id, content, scheduled_at, self._prefs(db)["timezone"],
                        scheduled_at + self.EXPIRY_SECONDS, now, now))
            result = self._row(db, "reminders", reminder_id)
            self._remember(db, "create_reminder", request_id, arguments, result)
            return result

    def list_reminders(self):
        with self._transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM reminders ORDER BY scheduled_at,id")]

    @staticmethod
    def _invalidate_pending(db, reminder_id, now):
        db.execute("""UPDATE notifications SET status='dismissed', dismissed_at=?, version=version+1
                      WHERE source_id=? AND status='pending'""", (now, reminder_id))

    def _cancel(self, db, reminder_id, now):
        reminder = self._row(db, "reminders", reminder_id)
        if reminder["status"] in {"scheduled", "queued"}:
            self._invalidate_pending(db, reminder_id, now)
            db.execute("UPDATE reminders SET status='cancelled', updated_at=?, version=version+1 WHERE id=?",
                       (now, reminder_id))
        return self._row(db, "reminders", reminder_id)

    def cancel_reminder(self, id):
        """Cancel undelivered occurrences; an acknowledged delivery cannot be recalled."""
        with self._transaction() as db:
            return self._cancel(db, id, self._now())

    def snooze_reminder(self, id, scheduled_at):
        scheduled_at = self._timestamp(scheduled_at)
        with self._transaction() as db:
            reminder = self._row(db, "reminders", id)
            if reminder["status"] == "cancelled":
                raise ValueError("cannot snooze a cancelled reminder")
            if reminder["task_id"] is not None:
                task = self._row(db, "tasks", reminder["task_id"])
                if task["status"] in {"done", "cancelled"}:
                    raise ValueError("cannot snooze a completed or cancelled task")
            now = self._now()
            self._invalidate_pending(db, id, now)
            db.execute("""UPDATE reminders SET scheduled_at=?, expires_at=?, status='scheduled',
                          updated_at=?, version=version+1 WHERE id=?""",
                       (scheduled_at, scheduled_at + self.EXPIRY_SECONDS, now, id))
            return self._row(db, "reminders", id)

    def preferences(self):
        with self._transaction() as db:
            return self._prefs(db)

    @staticmethod
    def _minute(value):
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            raise ValueError("quiet hours require HH:MM")
        try:
            hour, minute = int(value[:2]), int(value[3:])
        except ValueError:
            raise ValueError("quiet hours require HH:MM") from None
        if not (0 <= hour <= 23 and 0 <= minute <= 59) or value != f"{hour:02d}:{minute:02d}":
            raise ValueError("invalid quiet hours")
        return hour * 60 + minute

    def set_preferences(self, changes):
        if not isinstance(changes, dict) or set(changes) - set(self.DEFAULT_PREFERENCES):
            raise ValueError("unknown preference changes")
        changes = dict(changes)
        for key in ("reminder_enabled", "followup_enabled", "recommendation_enabled", "voice_interrupt_enabled"):
            if key in changes and type(changes[key]) is not bool:
                raise ValueError(f"{key} must be boolean")
        if "timezone" in changes:
            self._zone(changes["timezone"])
        if changes.get("quiet_hours") is not None:
            hours = changes["quiet_hours"]
            if not isinstance(hours, dict) or set(hours) != {"start", "end"}:
                raise ValueError("quiet_hours must be null or {start, end}")
            self._minute(hours["start"])
            self._minute(hours["end"])
        with self._transaction() as db:
            prefs = self._prefs(db)
            prefs.update(changes)
            encoded = json.dumps(prefs)
            db.execute("UPDATE preferences SET data=? WHERE id=1", (encoded,))
            return json.loads(encoded)

    def _quiet(self, prefs, now):
        hours = prefs["quiet_hours"]
        if hours is None:
            return False
        local = datetime.fromtimestamp(now, self._zone(prefs["timezone"]))
        minute = local.hour * 60 + local.minute
        start, end = self._minute(hours["start"]), self._minute(hours["end"])
        if start == end:
            return True  # explicitly selected full-day quiet mode
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end

    @staticmethod
    def _expire(db, now):
        db.execute("""UPDATE notifications SET status='expired', version=version+1
                      WHERE status='pending' AND expires_at<=?""", (now,))
        db.execute("""UPDATE reminders SET status='expired', updated_at=?, version=version+1
                      WHERE status IN ('scheduled','queued') AND expires_at<=?""", (now, now))

    def scan_due(self):
        """Atomically create and return only new notifications within a 24h window."""
        with self._transaction() as db:
            now = self._now()
            self._expire(db, now)
            prefs = self._prefs(db)
            if not prefs["reminder_enabled"]:
                return []
            mode = "silent" if self._quiet(prefs, now) else "text"
            due = db.execute("""SELECT r.* FROM reminders r LEFT JOIN tasks t ON t.id=r.task_id
                WHERE r.status='scheduled' AND r.scheduled_at<=? AND r.expires_at>?
                AND (r.task_id IS NULL OR t.status NOT IN ('done','cancelled'))
                ORDER BY r.scheduled_at,r.id""", (now, now)).fetchall()
            created = []
            for row in due:
                notification_id = uuid4().hex
                occurrence = f"{row['id']}:{row['version']}:{row['scheduled_at']}"
                cursor = db.execute("""INSERT OR IGNORE INTO notifications
                    (id,category,source_type,source_id,occurrence_key,content,status,
                     created_at,expires_at,scheduled_at,delivery_mode,missed,version)
                    VALUES (?, 'reminder', 'reminder', ?, ?, ?, 'pending', ?, ?, ?, ?, ?, 1)""",
                    (notification_id, row["id"], occurrence, row["content"], now,
                     row["expires_at"], row["scheduled_at"], mode, int(now > row["scheduled_at"])))
                db.execute("UPDATE reminders SET status='queued', updated_at=? WHERE id=?", (now, row["id"]))
                if cursor.rowcount:
                    created.append(self._row(db, "notifications", notification_id))
            return created

    def list_notifications(self):
        """Return inbox and notification history, including dismissed/expired records."""
        with self._transaction() as db:
            self._expire(db, self._now())
            return [self._row(db, "notifications", row[0]) for row in
                    db.execute("SELECT id FROM notifications ORDER BY created_at,id").fetchall()]

    def ack_notification(self, id):
        with self._transaction() as db:
            now = self._now()
            self._expire(db, now)
            notification = self._row(db, "notifications", id)
            if notification["status"] == "pending":
                db.execute("""UPDATE notifications SET status='presented', acknowledged_at=?,
                              version=version+1 WHERE id=?""", (now, id))
                db.execute("""UPDATE reminders SET status='notified', updated_at=?
                              WHERE id=? AND status='queued'""", (now, notification["source_id"]))
            return self._row(db, "notifications", id)

    def dismiss_notification(self, id):
        with self._transaction() as db:
            now = self._now()
            self._expire(db, now)
            notification = self._row(db, "notifications", id)
            if notification["status"] in {"pending", "presented"}:
                db.execute("""UPDATE notifications SET status='dismissed', dismissed_at=?,
                              version=version+1 WHERE id=?""", (now, id))
            return self._row(db, "notifications", id)

    def close(self):
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
