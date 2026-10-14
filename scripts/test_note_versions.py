"""Chosen note versions, migrations and races; temporary SQLite files only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.notes import NoteStore, SelectionConflict


class NoteVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-note-versions-')
        self.path = Path(self.temp.name) / 'notes.db'
        self.store = NoteStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def choose(self, note, version=None):
        return self.store.select(note['id'], version=note['version'] if version is None else version,
                                 expected_revision=self.store.selection()['revision'])

    def test_empty_selection_has_stable_workspace_identity_across_restart(self):
        first = self.store.selection()
        self.assertEqual(first['status'], 'none')
        self.assertIsNone(first['note_id'])
        self.assertIsNone(first['selected_version'])
        self.assertIsNone(first['current_version'])
        self.assertEqual(first['revision'], 1)
        self.assertTrue(first['activity_id'])
        self.assertEqual(NoteStore(self.path).selection(), first)

    def test_update_preserves_exact_chosen_version_and_selection_revision(self):
        note = self.store.create('周报', '原稿', '明确粘贴')
        selected = self.choose(note)
        updated = self.store.update(note['id'], '周报第二版', '改稿', 1)
        selection = self.store.selection()
        self.assertEqual(selection['revision'], selected['revision'])
        self.assertEqual(selection['activity_id'], selected['activity_id'])
        self.assertEqual(selection['selected_at'], selected['selected_at'])
        self.assertEqual(selection['selected_version'], 1)
        self.assertEqual(selection['current_version'], 2)
        self.assertTrue(selection['stale'])
        self.assertFalse(selection['missing'])
        self.assertEqual(selection['status'], 'stale')
        self.assertEqual(self.store.get_version(note['id'], 1), note)
        self.assertEqual(self.store.get_version(note['id'], 2), updated)
        self.assertEqual(NoteStore(self.path).selection(), selection)

    def test_explicit_promote_changes_ref_but_keeps_workspace_identity(self):
        note = self.store.create('稿件', '版本一')
        old = self.choose(note)
        updated = self.store.update(note['id'], '稿件', '版本二', 1)
        selected = self.choose(updated)
        self.assertEqual(selected['selected_version'], 2)
        self.assertEqual(selected['revision'], old['revision'] + 1)
        self.assertEqual(selected['activity_id'], old['activity_id'])
        self.assertEqual(selected['status'], 'selected')
        self.assertFalse(selected['stale'])

    def test_explicit_older_version_and_different_note_keep_activity_id(self):
        first = self.store.create('甲', '甲一')
        identity = self.choose(first)['activity_id']
        self.store.update(first['id'], '甲', '甲二', 1)
        selected = self.choose(first, version=1)
        self.assertTrue(selected['stale'])
        second = self.store.create('乙', '乙一')
        other = self.choose(second)
        self.assertEqual(other['activity_id'], identity)
        self.assertEqual(other['note_id'], second['id'])
        self.assertEqual(other['selected_version'], 1)

    def test_explicit_unlink_preserves_notes_history_and_workspace_id(self):
        note = self.store.create('稿件', '正文')
        chosen = self.choose(note)
        cleared = self.store.select(None, version=None, expected_revision=chosen['revision'])
        self.assertEqual(cleared['status'], 'none')
        self.assertEqual(cleared['activity_id'], chosen['activity_id'])
        self.assertEqual(cleared['revision'], chosen['revision'] + 1)
        self.assertIsNone(cleared['selected_at'])
        self.assertEqual(self.store.get_version(note['id'], 1)['body'], '正文')
        self.assertEqual(len(self.store.history(note['id'])), 1)

    def test_stale_selection_cannot_clear_or_replace_newer_choice(self):
        first = self.store.create('甲', '甲文')
        second = self.store.create('乙', '乙文')
        old = self.store.selection()
        selected = self.choose(first)
        with self.assertRaisesRegex(SelectionConflict, 'selection_conflict'):
            self.store.select(second['id'], version=1, expected_revision=old['revision'])
        with self.assertRaises(SelectionConflict):
            self.store.select(None, version=None, expected_revision=old['revision'])
        self.assertEqual(self.store.selection(), selected)

    def test_missing_version_cannot_be_selected_and_does_not_modify_selection(self):
        note = self.store.create('稿件', '正文')
        old = self.store.selection()
        for note_id, version in ((note['id'], 2), ('missing-note', 1)):
            with self.assertRaises(KeyError):
                self.store.select(note_id, version=version, expected_revision=old['revision'])
        self.assertEqual(self.store.selection(), old)

    def test_history_is_metadata_only_and_snapshot_reads_are_detached(self):
        note = self.store.create('原标题', '原正文', '手动来源')
        self.store.update(note['id'], '新标题', '新正文', 1)
        history = self.store.history(note['id'])
        self.assertEqual([row['version'] for row in history], [2, 1])
        self.assertTrue(all('body' not in row and row['source'] == '手动来源' for row in history))
        returned = self.store.get_version(note['id'], 1)
        returned['body'] = '不可覆盖历史'
        self.assertEqual(self.store.get_version(note['id'], 1)['body'], '原正文')
        self.assertEqual(self.store.get(note['id'])['body'], '新正文')

    def test_history_is_bounded_but_exact_old_metadata_and_body_remain_readable(self):
        note = self.store.create('第一版', '第一版正文', '手动来源')
        for version in range(1, 61):
            self.store.update(note['id'], f'第{version + 1}版', f'正文{version + 1}', version)
        recent = self.store.history(note['id'])
        self.assertEqual([row['version'] for row in recent], list(range(61, 11, -1)))
        self.assertEqual([row['version'] for row in self.store.history(note['id'], limit=1)], [61])
        self.assertEqual(len(self.store.history(note['id'], limit=100)), 61)
        self.assertEqual(self.store.get_version_info(note['id'], 1),
                         {key: value for key, value in note.items() if key != 'body'})
        self.assertEqual(self.store.get_version(note['id'], 1), note)

    def test_metadata_reads_never_fetch_body_columns(self):
        note = self.store.create('版本一', '不应读取的正文')
        self.store.update(note['id'], '版本二', '也不应读取的正文', 1)
        original_connection = self.store.connection

        @contextmanager
        def metadata_connection():
            with original_connection() as db:
                def authorize(action, table, column, database, trigger):
                    if action == sqlite3.SQLITE_READ and table in ('notes', 'note_revisions') and column == 'body':
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                yield db

        with patch.object(self.store, 'connection', metadata_connection):
            info = self.store.get_version_info(note['id'], 1)
            self.assertEqual(info['title'], '版本一')
            self.assertNotIn('body', info)
            self.assertEqual([row['version'] for row in self.store.history(note['id'])], [2, 1])
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.get_version(note['id'], 1)

    def test_history_limit_is_strictly_validated(self):
        note = self.store.create('稿件', '正文')
        for invalid in (True, False, 0, -1, 1.0, '1', None, 101):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, 'invalid limit'):
                    self.store.history(note['id'], limit=invalid)
        with self.assertRaises(KeyError):
            self.store.history('missing-note')

    def test_failed_current_version_cas_does_not_create_history_or_change_selection(self):
        note = self.store.create('稿件', '第一版')
        selected = self.choose(note)
        self.store.update(note['id'], '稿件', '第二版', 1)
        with self.assertRaises(ValueError):
            self.store.update(note['id'], '稿件', '不应保存', 1)
        self.assertEqual([row['version'] for row in self.store.history(note['id'])], [2, 1])
        self.assertEqual(self.store.selection()['revision'], selected['revision'])
        self.assertEqual(self.store.get_version(note['id'], 1)['body'], '第一版')

    def test_delete_selected_note_clears_ref_and_all_snapshots_atomically(self):
        note = self.store.create('稿件', '第一版')
        selected = self.choose(note)
        self.store.update(note['id'], '稿件', '第二版', 1)
        self.assertEqual(self.store.delete(note['id'], 2), {'deleted': True})
        cleared = NoteStore(self.path).selection()
        self.assertEqual(cleared['status'], 'none')
        self.assertEqual(cleared['revision'], selected['revision'] + 1)
        self.assertEqual(cleared['activity_id'], selected['activity_id'])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM note_revisions WHERE id=?',
                                        (note['id'],)).fetchone()[0], 0)
        for version in (1, 2):
            with self.assertRaises(KeyError):
                self.store.get_version(note['id'], version)
            with self.assertRaises(KeyError):
                self.store.get_version_info(note['id'], version)
        with self.assertRaises(KeyError):
            self.store.history(note['id'])

    def test_deleting_previous_note_does_not_clear_newer_selection(self):
        old = self.store.create('旧稿', '旧文')
        new = self.store.create('新稿', '新文')
        self.choose(old)
        newer = self.choose(new)
        self.store.delete(old['id'], 1)
        self.assertEqual(self.store.selection(), newer)

    def test_failed_delete_preserves_note_snapshots_and_chosen_ref(self):
        note = self.store.create('稿件', '第一版')
        self.choose(note)
        self.store.update(note['id'], '稿件', '第二版', 1)
        before = self.store.selection()
        with self.assertRaises(ValueError):
            self.store.delete(note['id'], 1)
        self.assertEqual(self.store.selection(), before)
        self.assertEqual(len(self.store.history(note['id'])), 2)

    def test_missing_chosen_snapshot_is_reported_without_falling_forward(self):
        note = self.store.create('稿件', '第一版')
        selected = self.choose(note)
        self.store.update(note['id'], '稿件', '第二版', 1)
        # Simulate incomplete external data; normal NoteStore mutations cannot
        # remove one historical version while retaining a current note.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM note_revisions WHERE id=? AND version=1', (note['id'],))
        missing = self.store.selection()
        self.assertEqual(missing['status'], 'missing')
        self.assertTrue(missing['missing'])
        self.assertTrue(missing['stale'])
        self.assertEqual(missing['selected_version'], 1)
        self.assertEqual(missing['current_version'], 2)
        self.assertEqual(missing['revision'], selected['revision'])
        with self.assertRaises(KeyError):
            self.store.get_version(note['id'], 1)
        with self.assertRaises(KeyError):
            self.store.get_version_info(note['id'], 1)

    def test_missing_current_note_is_reported_read_only(self):
        note = self.store.create('稿件', '正文')
        selected = self.choose(note)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM notes WHERE id=?', (note['id'],))
        missing = self.store.selection()
        self.assertTrue(missing['missing'])
        self.assertFalse(missing['stale'])
        self.assertEqual(missing['revision'], selected['revision'])
        self.assertEqual(missing['note_id'], note['id'])
        self.assertIsNone(missing['current_version'])
        with self.assertRaises(KeyError):
            self.store.get_version(note['id'], 1)

    def test_strict_version_and_selection_revision_validation(self):
        note = self.store.create('稿件', '正文')
        before = self.store.selection()
        for invalid in (True, False, 0, -1, 1.0, '1', None, 2 ** 63):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    self.store.select(note['id'], version=invalid, expected_revision=before['revision'])
                with self.assertRaises(ValueError):
                    self.store.select(note['id'], version=1, expected_revision=invalid)
                with self.assertRaises(ValueError):
                    self.store.get_version(note['id'], invalid)
                with self.assertRaises(ValueError):
                    self.store.get_version_info(note['id'], invalid)
        with self.assertRaises(ValueError):
            self.store.select(None, version=1, expected_revision=before['revision'])
        self.assertEqual(self.store.selection(), before)

    def test_atomic_selection_cas_across_connections(self):
        one = self.store.create('甲', '甲文')
        two = self.store.create('乙', '乙文')
        other = NoteStore(self.path)
        barrier = threading.Barrier(2)
        revision = self.store.selection()['revision']
        def choose(store, note):
            barrier.wait()
            try:
                return store.select(note['id'], version=1, expected_revision=revision)['note_id']
            except SelectionConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(choose, self.store, one)
            second = workers.submit(choose, other, two)
            results = [first.result(), second.result()]
        self.assertEqual(results.count('conflict'), 1)
        chosen_id = next(result for result in results if result != 'conflict')
        self.assertEqual(self.store.selection()['note_id'], chosen_id)
        self.assertEqual(self.store.selection(), other.selection())

    def test_atomic_delete_does_not_clear_racing_new_selection(self):
        old = self.store.create('旧稿', '旧文')
        new = self.store.create('新稿', '新文')
        chosen = self.choose(old)
        other = NoteStore(self.path)
        barrier = threading.Barrier(2)
        def choose_new():
            barrier.wait()
            try:
                other.select(new['id'], version=1, expected_revision=chosen['revision'])
                return True
            except SelectionConflict:
                return False
        def remove_old():
            barrier.wait()
            return self.store.delete(old['id'], 1)
        with ThreadPoolExecutor(max_workers=2) as workers:
            selecting = workers.submit(choose_new)
            deleting = workers.submit(remove_old)
            did_select = selecting.result()
            self.assertEqual(deleting.result(), {'deleted': True})
        self.assertEqual(self.store.selection()['note_id'], new['id'] if did_select else None)
        self.assertEqual(self.store.get(new['id'])['body'], '新文')


class NoteMigrationTests(unittest.TestCase):
    def test_legacy_database_backfills_only_known_current_version_without_rewriting_it(self):
        with tempfile.TemporaryDirectory(prefix='aqi-notes-legacy-') as directory:
            path = Path(directory) / 'notes.db'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('''CREATE TABLE notes (
                    id TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,
                    source TEXT NOT NULL,version INTEGER NOT NULL,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
                db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?)',
                           ('legacy-note', '旧库标题', '仅有第四版正文', '旧库来源', 4, 10.0, 40.0))
                db.row_factory = sqlite3.Row
                original = dict(db.execute('SELECT * FROM notes').fetchone())
            store = NoteStore(path)
            self.assertEqual(store.get('legacy-note'), original)
            self.assertEqual(store.get_version('legacy-note', 4), original)
            self.assertEqual([row['version'] for row in store.history('legacy-note')], [4])
            with self.assertRaises(KeyError):
                store.get_version('legacy-note', 1)
            chosen = store.select('legacy-note', version=4, expected_revision=1)
            store.update('legacy-note', '新版标题', '第五版正文', 4)
            reopened = NoteStore(path)
            self.assertEqual(reopened.get_version('legacy-note', 4), original)
            self.assertEqual([row['version'] for row in reopened.history('legacy-note')], [5, 4])
            self.assertEqual(reopened.selection()['activity_id'], chosen['activity_id'])
            self.assertEqual(reopened.selection()['selected_version'], 4)
            self.assertEqual(reopened.selection()['current_version'], 5)
            self.assertTrue(reopened.selection()['stale'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
