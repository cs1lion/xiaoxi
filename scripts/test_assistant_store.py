"""Offline deterministic assistant-store regression tests.

Run: python scripts/test_assistant_store.py
"""
import concurrent.futures
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant import AssistantStore


class AssistantStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="assistant-test-", dir=Path(__file__).resolve().parents[1])
        self.path = Path(self.temp.name) / "assistant.db"
        self.now = 1_700_000_000.0
        self.store = AssistantStore(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def restart(self):
        self.store.close()
        self.store = AssistantStore(self.path, clock=lambda: self.now)

    def test_defaults_and_schema(self):
        prefs = self.store.preferences()
        self.assertTrue(prefs["reminder_enabled"])
        for key in ("followup_enabled", "recommendation_enabled", "voice_interrupt_enabled"):
            self.assertFalse(prefs[key])
        self.assertEqual(prefs["timezone"], "UTC")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_idempotent_create_survives_restart(self):
        task = self.store.create_task("report", "weekly", request_id="task-1")
        reminder = self.store.create_reminder("submit", self.now + 10, task["id"], "reminder-1")
        self.restart()
        self.assertEqual(task, self.store.create_task("report", "weekly", "task-1"))
        self.assertEqual(reminder, self.store.create_reminder("submit", self.now + 10, task["id"], "reminder-1"))
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(len(self.store.list_reminders()), 1)
        with self.assertRaises(ValueError):
            self.store.create_task("different", request_id="task-1")
        with self.assertRaises(ValueError):
            self.store.create_reminder("submit", self.now + 11, task["id"], "reminder-1")

    def test_version_conflict_and_task_states(self):
        task = self.store.create_task("task")
        for status in ("in_progress", "waiting", "open", "done", "cancelled"):
            task = self.store.update_task(task["id"], {"status": status, "version": task["version"]})
            self.assertEqual(task["status"], status)
        with self.assertRaises(ValueError):
            self.store.update_task(task["id"], {"title": "race", "version": 1})
        with self.assertRaises(ValueError):
            self.store.create_reminder("no", self.now, task["id"])

    def test_terminal_task_cleans_scheduled_and_pending(self):
        for status in ("done", "cancelled"):
            task = self.store.create_task(status)
            queued = self.store.create_reminder("due", self.now, task["id"])
            later = self.store.create_reminder("later", self.now + 20, task["id"])
            notification = self.store.scan_due()[0]
            updated = self.store.update_task(task["id"], {"status": status})
            self.assertEqual(updated["status"], status)
            rows = {r["id"]: r for r in self.store.list_reminders()}
            self.assertEqual(rows[queued["id"]]["status"], "cancelled")
            self.assertEqual(rows[later["id"]]["status"], "cancelled")
            inbox = {n["id"]: n for n in self.store.list_notifications()}
            self.assertEqual(inbox[notification["id"]]["status"], "dismissed")
            self.assertEqual(self.store.ack_notification(notification["id"])["status"], "dismissed")
        self.now += 100
        self.assertEqual(self.store.scan_due(), [])

    def test_scan_and_ack_dedup_restart(self):
        reminder = self.store.create_reminder("due", self.now)
        notification = self.store.scan_due()[0]
        self.assertEqual(notification["category"], "reminder")
        self.assertEqual(notification["source_id"], reminder["id"])
        self.assertFalse(notification["missed"])
        self.assertEqual(self.store.scan_due(), [])
        self.restart()
        self.assertEqual(self.store.scan_due(), [])
        acknowledged = self.store.ack_notification(notification["id"])
        self.assertEqual(acknowledged["status"], "presented")
        self.assertEqual(self.store.ack_notification(notification["id"]), acknowledged)
        self.assertEqual(self.store.list_reminders()[0]["status"], "notified")
        self.assertEqual(self.store.cancel_reminder(reminder["id"])["status"], "notified")
        self.assertEqual(self.store.dismiss_notification(notification["id"])["status"], "dismissed")

    def test_cancel_before_and_after_queue(self):
        before = self.store.create_reminder("cancel before", self.now)
        self.store.cancel_reminder(before["id"])
        self.assertEqual(self.store.scan_due(), [])
        after = self.store.create_reminder("cancel after", self.now)
        notification = self.store.scan_due()[0]
        self.store.cancel_reminder(after["id"])
        self.assertEqual(self.store.ack_notification(notification["id"])["status"], "dismissed")
        self.assertEqual(self.store.scan_due(), [])

    def test_snooze_invalidates_old_occurrence(self):
        reminder = self.store.create_reminder("snooze", self.now)
        old = self.store.scan_due()[0]
        updated = self.store.snooze_reminder(reminder["id"], self.now + 30)
        self.assertEqual(updated["version"], reminder["version"] + 1)
        self.assertEqual(self.store.ack_notification(old["id"])["status"], "dismissed")
        self.assertEqual(self.store.scan_due(), [])
        self.now += 30
        new = self.store.scan_due()[0]
        self.assertNotEqual(old["occurrence_key"], new["occurrence_key"])
        self.assertNotEqual(old["id"], new["id"])
        self.store.ack_notification(new["id"])
        self.store.snooze_reminder(reminder["id"], self.now + 5)
        self.now += 5
        self.assertEqual(len(self.store.scan_due()), 1)

    def test_disabled_reminders_and_restart_preferences(self):
        self.store.create_reminder("later", self.now)
        self.store.set_preferences({"reminder_enabled": False})
        self.restart()
        self.assertFalse(self.store.preferences()["reminder_enabled"])
        self.assertEqual(self.store.scan_due(), [])
        self.store.set_preferences({"reminder_enabled": True})
        self.assertEqual(len(self.store.scan_due()), 1)

    def test_expiry_and_missed_marker(self):
        self.store.create_reminder("old", self.now - 86400)
        recent = self.store.create_reminder("missed", self.now - 60)
        notification = self.store.scan_due()[0]
        self.assertEqual(notification["source_id"], recent["id"])
        self.assertTrue(notification["missed"])
        self.assertEqual(self.store.list_reminders()[0]["status"], "expired")
        self.now += 86400
        self.assertEqual(self.store.ack_notification(notification["id"])["status"], "expired")
        self.assertEqual(self.store.scan_due(), [])

    def test_quiet_hours_overnight_silent_inbox(self):
        # 2023-11-14 22:13 UTC, within the overnight interval.
        self.store.set_preferences({"quiet_hours": {"start": "22:00", "end": "07:00"}, "timezone": "UTC"})
        self.store.create_reminder("quiet", self.now)
        notification = self.store.scan_due()[0]
        self.assertEqual(notification["delivery_mode"], "silent")
        self.assertEqual(notification["status"], "pending")
        self.now += 12 * 3600
        self.store.create_reminder("day", self.now)
        self.assertEqual(self.store.scan_due()[0]["delivery_mode"], "text")

    def test_concurrent_connections_atomic_dedup(self):
        for index in range(8):
            self.store.create_reminder(str(index), self.now)
        other = AssistantStore(self.path, clock=lambda: self.now)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda i: (self.store if i % 2 else other).scan_due(), range(16)))
            self.assertEqual(sum(len(result) for result in results), 8)
            self.assertEqual(len(self.store.list_notifications()), 8)
        finally:
            other.close()

    def test_concurrent_create_request_id(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            tasks = list(executor.map(lambda _: self.store.create_task("one", request_id="same"), range(16)))
        self.assertEqual(len({task["id"] for task in tasks}), 1)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_validation_and_unknown_ids(self):
        for value in (None, "", "   ", 42):
            with self.assertRaises(ValueError):
                self.store.create_task(value)
        for value in (float("nan"), float("inf"), "tomorrow", None, True):
            with self.assertRaises(ValueError):
                self.store.create_reminder("test", value)
        for changes in ({"unknown": True}, {"reminder_enabled": 1}, {"timezone": "Invalid/Zone"},
                        {"quiet_hours": {"start": "25:00", "end": "07:00"}}):
            with self.assertRaises(ValueError):
                self.store.set_preferences(changes)
        for operation in (lambda: self.store.update_task("missing", {"status": "done"}),
                          lambda: self.store.cancel_reminder("missing"),
                          lambda: self.store.snooze_reminder("missing", self.now),
                          lambda: self.store.ack_notification("missing"),
                          lambda: self.store.dismiss_notification("missing")):
            with self.assertRaises(KeyError):
                operation()
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.store.list_reminders(), [])

    def test_close_is_idempotent(self):
        self.store.close()
        self.store.close()
        with self.assertRaises(ValueError):
            self.store.list_tasks()

    def test_newer_schema_rejected_without_deletion(self):
        self.store.create_task("keep")
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version = 99")
        with self.assertRaises(ValueError):
            AssistantStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT title FROM tasks").fetchone()[0], "keep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
