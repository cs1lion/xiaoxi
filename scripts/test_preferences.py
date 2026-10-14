"""Offline preference-store regression tests: python scripts/test_preferences.py."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.interaction import PreferenceStore


class PreferenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="preferences-test-")
        self.path = Path(self.temp.name) / "preferences.sqlite"
        self.now = 1700000000.0
        self.store = PreferenceStore(self.path, clock=lambda: self.now)
        self.evidence = self.store.add_evidence("Please keep answers short")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def restart(self):
        self.store.close()
        self.store = PreferenceStore(self.path, clock=lambda: self.now)

    def claim(self, value="short", **kwargs):
        return self.store.create_claim("detail", value, [self.evidence["id"]], **kwargs)

    def test_defaults_are_isolated_in_memory(self):
        first, second = PreferenceStore(), PreferenceStore()
        try:
            evidence = first.add_evidence("A preference")
            first.create_claim("humor", "light", [evidence["id"]])
            self.assertEqual(second.list_claims(True), [])
            with self.assertRaises(ValueError):
                second.create_claim("humor", "light", [evidence["id"]])
            self.assertEqual(first._db.execute("PRAGMA database_list").fetchone()[2], "")
        finally:
            first.close()
            second.close()

    def test_all_dimensions_and_sources(self):
        for dimension, values in PreferenceStore.DIMENSIONS.items():
            for value in values:
                result = self.store.create_claim(dimension, value, [self.evidence["id"]],
                    scope={"topic": dimension + value}, source="manual")
                self.assertTrue(result["active"])
                self.assertEqual(result["status"], "active")
                self.assertEqual(result["evidence_ids"], [self.evidence["id"]])

    def test_strict_scopes(self):
        for scope in ({"unknown": "x"}, {"activity": "gaming"}, {"activity": None},
                      {"session_id": 1}, {"topic": []}, {"topic": ""},
                      {"topic": " "}, [], "global", {1: "x"}):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.claim(scope=scope)
        self.assertEqual(self.claim()["scope"], {})
        self.assertEqual(self.claim(scope={})["scope"], {})
        for activity in ("work", "writing", "technical", "chat"):
            scope = {"activity": activity, "session_id": "s1", "topic": "项目"}
            result = self.claim(scope=scope)
            scope["topic"] = "changed"
            self.assertEqual(result["scope"]["topic"], "项目")
        self.assertEqual(len(self.store.list_claims()), 6)

    def test_missing_evidence_and_atomic_revision(self):
        for ids in ([], (), None, "id", ["missing"], [self.evidence["id"], "missing"],
                    [self.evidence["id"], self.evidence["id"]], [None]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.store.create_claim("detail", "short", ids)
        self.assertEqual(self.store.list_claims(True), [])
        old = self.claim()
        with self.assertRaises(ValueError):
            self.store.revise_claim(old["id"], "detailed", ["missing"])
        self.assertEqual(self.store.list_claims(), [old])
        with self.assertRaises(ValueError):
            self.store.revise_claim("missing", "short", [self.evidence["id"]])

    def test_inferred_never_promoted_or_conflicts(self):
        inferred = self.claim("detailed", source="inferred")
        self.assertEqual(inferred["status"], "candidate")
        self.assertFalse(inferred["active"])
        explicit = self.claim()
        self.assertEqual(self.store.list_claims(), [explicit])
        revised = self.store.revise_claim(inferred["id"], "short", [self.evidence["id"]])
        self.assertEqual(revised["source"], "inferred")
        self.assertEqual(revised["status"], "candidate")
        self.store.delete_claim(explicit["id"])
        self.restart()
        self.assertEqual(self.store.list_claims(), [])
        self.assertEqual({c["status"] for c in self.store.list_claims(True)},
                         {"candidate", "superseded"})

    def test_conflicts_no_latest_wins_and_exact_scope(self):
        scope = {"activity": "work", "topic": "report"}
        first = self.claim(scope=scope)
        self.now += 1
        second = self.claim("detailed", scope={"topic": "report", "activity": "work"})
        self.assertEqual(second["status"], "conflicted")
        self.assertEqual(self.store.list_claims(), [])
        rows = self.store.list_claims(True)
        self.assertEqual({r["status"] for r in rows}, {"conflicted"})
        self.assertEqual({r["id"] for r in rows}, {first["id"], second["id"]})
        global_claim = self.claim()
        other_scope = self.claim("detailed", scope={"activity": "chat"})
        self.assertEqual({r["id"] for r in self.store.list_claims()},
                         {global_claim["id"], other_scope["id"]})
        self.restart()
        self.assertEqual(len([r for r in self.store.list_claims(True)
                              if r["status"] == "conflicted"]), 2)

    def test_revision_preserves_scope_source_expiry_and_resolves_conflict(self):
        scope = {"activity": "technical", "session_id": "s", "topic": "Python"}
        old = self.claim(scope=scope, source="manual", valid_until=self.now + 50)
        other = self.claim("detailed", scope=scope)
        new_evidence = self.store.add_evidence("I now want detailed answers")
        new = self.store.revise_claim(old["id"], "detailed", [new_evidence["id"]])
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(new["supersedes"], old["id"])
        self.assertEqual(new["scope"], scope)
        self.assertEqual(new["source"], "manual")
        self.assertEqual(new["valid_until"], old["valid_until"])
        self.assertEqual(new["status"], "active")
        self.assertEqual({r["id"] for r in self.store.list_claims()}, {new["id"], other["id"]})
        rows = {r["id"]: r for r in self.store.list_claims(True)}
        self.assertEqual(rows[old["id"]]["status"], "superseded")
        with self.assertRaises(ValueError):
            self.store.revise_claim(old["id"], "short", [self.evidence["id"]])

    def test_delete_evidence_erases_all_dependents_and_sensitive_payload(self):
        secret = "sensitive-unique-user-text-6789432"
        evidence = self.store.add_evidence(secret)
        self.store.create_claim("detail", "short", [evidence["id"], self.evidence["id"]],
                                scope={"topic": "sensitive-derived-topic-439872"})
        self.store.create_claim("humor", "off", [evidence["id"]], source="inferred")
        survivor = self.claim()
        self.assertEqual(self.store.delete_evidence(evidence["id"]), 2)
        self.assertEqual(self.store.delete_evidence(evidence["id"]), 0)
        self.restart()
        self.assertEqual(self.store.list_claims(True), [survivor])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT * FROM preference_evidence WHERE id=?",
                                         (evidence["id"],)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM preference_links").fetchone()[0], 1)
            self.assertNotIn(secret, "\n".join(db.iterdump()))
            self.assertNotIn("sensitive-derived-topic-439872", "\n".join(db.iterdump()))
        self.assertNotIn(secret.encode(), self.path.read_bytes())
        self.assertNotIn(b"sensitive-derived-topic-439872", self.path.read_bytes())
        with self.assertRaises(ValueError):
            self.store.create_claim("humor", "off", [evidence["id"]])

    def test_deletion_never_revives_superseded_predecessor(self):
        old = self.claim()
        replacement_evidence = self.store.add_evidence("Long answers now")
        self.store.revise_claim(old["id"], "detailed", [replacement_evidence["id"]])
        self.assertEqual(self.store.delete_evidence(replacement_evidence["id"]), 1)
        self.restart()
        self.assertEqual(self.store.list_claims(), [])
        self.assertEqual(self.store.list_claims(True)[0]["status"], "superseded")
        self.assertTrue(self.store.delete_claim(old["id"]))
        self.assertFalse(self.store.delete_claim(old["id"]))
        self.restart()
        self.assertEqual(self.store.list_claims(True), [])

    def test_delete_revision_keeps_predecessor_superseded(self):
        old = self.claim()
        replacement = self.store.revise_claim(old["id"], "detailed", [self.evidence["id"]])
        self.assertTrue(self.store.delete_claim(replacement["id"]))
        self.restart()
        self.assertEqual(self.store.list_claims(), [])
        self.assertEqual(self.store.list_claims(True)[0]["status"], "superseded")

    def test_expiration_at_exact_boundary_and_conflict_resolution(self):
        permanent = self.claim()
        expiring = self.claim("detailed", valid_until=self.now + 5)
        self.assertEqual(expiring["status"], "conflicted")
        self.assertEqual(self.store.list_claims(), [])
        self.now += 5
        self.assertEqual(self.store.list_claims(), [permanent])
        rows = {r["id"]: r for r in self.store.list_claims(True)}
        self.assertEqual(rows[expiring["id"]]["status"], "expired")
        self.assertEqual(self.claim(valid_until=self.now)["status"], "expired")
        revised = self.store.revise_claim(expiring["id"], "short", [self.evidence["id"]])
        self.assertEqual(revised["status"], "expired")
        self.restart()
        self.assertEqual(self.store.list_claims(), [permanent])

    def test_subject_pollution_rejected(self):
        for subject in ("assistant", "third_party", "User", " user ", "system"):
            evidence = self.store.add_evidence("Someone else's preference", subject=subject)
            for source in ("explicit_user", "manual", "inferred"):
                with self.subTest(subject=subject, source=source), self.assertRaises(ValueError):
                    self.store.create_claim("detail", "short", [evidence["id"]], source=source)
            old = self.claim(scope={"topic": subject})
            with self.assertRaises(ValueError):
                self.store.revise_claim(old["id"], "detailed", [self.evidence["id"], evidence["id"]])
            self.assertIn(old, self.store.list_claims())

    def test_argument_validation_and_closed_store(self):
        for text in (None, 3, "", " "):
            with self.assertRaises(ValueError):
                self.store.add_evidence(text)
        for source in ("automatic", "", None, [], 1):
            with self.assertRaises(ValueError):
                self.claim(source=source)
            with self.assertRaises(ValueError):
                self.store.add_evidence("text", source=source)
        for dimension, value in (("unknown", "short"), ([], "short"), ("detail", "long"),
                                 ("detail", []), ("humor", "short")):
            with self.assertRaises(ValueError):
                self.store.create_claim(dimension, value, [self.evidence["id"]])
        for until in (True, "1700000001", float("inf"), float("nan"), 10 ** 1000):
            with self.assertRaises(ValueError):
                self.claim(valid_until=until)
        with self.assertRaises(ValueError):
            self.store.list_claims(include_inactive=1)
        with self.assertRaises(ValueError):
            PreferenceStore(clock=None)
        self.store.close()
        self.store.close()
        with self.assertRaises(RuntimeError):
            self.store.list_claims()

    def test_transaction_lock_thread_safety(self):
        def create(index):
            return self.claim("short" if index % 2 else "detailed")
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(create, range(24)))
        self.assertEqual(len({r["id"] for r in rows}), 24)
        self.assertEqual(len(self.store.list_claims(True)), 24)
        self.assertEqual(self.store.list_claims(), [])
        self.assertEqual({r["status"] for r in self.store.list_claims(True)}, {"conflicted"})
        self.assertEqual(self.store.delete_evidence(self.evidence["id"]), 24)
        self.assertEqual(self.store.list_claims(True), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
