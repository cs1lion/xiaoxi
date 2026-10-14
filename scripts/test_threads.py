"""Standalone stdlib tests: python scripts/test_threads.py (no app bootstrap)."""

from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    'standalone_threads', Path(__file__).resolve().parents[1] / 'server/interaction/threads.py')
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ThreadStore = _MODULE.ThreadStore
RevisionConflict = _MODULE.RevisionConflict
_PARTICIPATION_SPEC = importlib.util.spec_from_file_location(
    'standalone_participation', Path(__file__).resolve().parents[1] / 'server/interaction/participation.py')
_PARTICIPATION_MODULE = importlib.util.module_from_spec(_PARTICIPATION_SPEC)
_PARTICIPATION_SPEC.loader.exec_module(_PARTICIPATION_MODULE)
Participation = _PARTICIPATION_MODULE.Participation


class ThreadStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'threads.sqlite'
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.thread = self.store.create('Plan', session_id='session-one')
        self.tid = self.thread['id']

    def add(self, **kwargs):
        args = dict(expected_revision=1, kind='decision', text='A decision')
        args.update(kwargs)
        return self.store.add_event(self.tid, **args)

    def test_defaults_and_persistence(self):
        self.assertEqual(self.thread['revision'], 1)
        self.assertEqual(self.thread['status'], 'discussing')
        self.assertFalse(self.thread['allow_callbacks'])
        self.assertEqual(self.thread['events'], [])
        self.assertEqual(self.thread['session_id'], 'session-one')
        self.store.close()
        with self.assertRaises(RuntimeError):
            self.store.get(self.tid)
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        self.assertEqual(other.get(self.tid), self.thread)
        self.store.close()

    def test_selection_validation_and_no_thread_mutation(self):
        self.assertIsNone(self.store.get_selection())
        self.assertEqual(self.store.select_thread(self.tid), {'thread_id': self.tid})
        for thread_id in ('', ' ', True, [], 'x' * 257, 'bad\x00id'):
            with self.subTest(thread_id=thread_id), self.assertRaises(ValueError):
                self.store.select_thread(thread_id)
        with self.assertRaises(KeyError):
            self.store.select_thread('missing')
        with self.assertRaisesRegex(ValueError, '^invalid_thread_id$'):
            Participation(self.store).select(True)
        self.assertEqual(self.store.get_selection(), self.tid)
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_selection_shared_restart_and_explicit_unlink(self):
        first = Participation(self.store)
        first.select(self.tid)
        second = Participation(self.store)
        self.assertEqual(second.selected_id, self.tid)
        self.assertEqual(second.current(), self.thread)
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        remote = Participation(other)
        self.assertEqual(remote.context()['id'], self.tid)
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        restarted = Participation(self.store)
        self.assertEqual(restarted.selected_id, self.tid)
        self.assertEqual(remote.select(None), {'thread_id': None})
        self.assertIsNone(restarted.selected_id)
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.assertIsNone(Participation(self.store).current())
        # Legacy direct access remains live, including a direct assignment.
        restarted = Participation(self.store)
        restarted.selected_id = self.tid
        self.assertEqual(remote.selected_id, self.tid)

    def test_legacy_database_migration_preserves_data(self):
        thread = self.add(dedupe_key='preserved')
        gone = self.store.create('Deleted before migration')
        tombstone = self.store.delete(gone['id'], expected_revision=1)
        self.store.close()
        # This is exactly the previous schema: selection did not exist.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DROP TABLE thread_selection')
            db.commit()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.assertIsNone(self.store.get_selection())
        self.assertEqual(self.store.get(self.tid), thread)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT * FROM thread_tombstones').fetchone(),
                             (gone['id'], tombstone['deleted_at']))
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.store.select_thread(self.tid)
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.get_selection(), self.tid)

    def test_delete_clears_selection_in_its_commit(self):
        self.store.select_thread(self.tid)
        observer = ThreadStore(self.path)
        self.addCleanup(observer.close)
        with self.assertRaises(RevisionConflict):
            self.store.delete(self.tid, expected_revision=2)
        self.assertEqual(observer.get_selection(), self.tid)
        self.store.delete(self.tid, expected_revision=1)
        # No Participation.current() call or route cleanup is needed.
        self.assertIsNone(observer.get_selection())
        with self.assertRaises(KeyError):
            observer.select_thread(self.tid)
        self.assertIsNone(self.store.get_selection())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT * FROM thread_selection').fetchone(), (1, None))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_current_late_deleted_read_cannot_clear_new_choice(self):
        participant = Participation(self.store)
        participant.select(self.tid)
        replacement = self.store.create('Replacement')
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        original_get = self.store.get

        def deleted_between_reads(thread_id):
            other.delete(self.tid, expected_revision=1)
            other.select_thread(replacement['id'])
            return original_get(thread_id)

        with patch.object(self.store, 'get', side_effect=deleted_between_reads):
            self.assertIsNone(participant.current())
        self.assertEqual(participant.selected_id, replacement['id'])
        self.assertEqual(participant.current()['id'], replacement['id'])

    def test_atomic_cross_connection_selection_vs_delete(self):
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        for _ in range(10):
            thread = self.store.create('Racing selection')
            thread_id = thread['id']
            self.store.select_thread(thread_id)
            gate = threading.Barrier(2)
            outcomes = []

            def selector():
                gate.wait()
                try:
                    other.select_thread(thread_id)
                    outcomes.append('selected')
                except KeyError:
                    outcomes.append('already_deleted')
                except Exception as error:
                    outcomes.append(error)

            def deleter():
                gate.wait()
                try:
                    self.store.delete(thread_id, expected_revision=1)
                    outcomes.append('deleted')
                except Exception as error:
                    outcomes.append(error)

            workers = [threading.Thread(target=target) for target in (selector, deleter)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)
                self.assertFalse(worker.is_alive())
            self.assertEqual(len(outcomes), 2)
            self.assertIn('deleted', outcomes)
            self.assertTrue('selected' in outcomes or 'already_deleted' in outcomes, outcomes)
            self.assertIsNone(self.store.get_selection())
            self.assertIsNone(other.get_selection())
            with self.assertRaises(KeyError):
                other.select_thread(thread_id)

    def test_update_statuses_and_cas(self):
        revision = 1
        for status in sorted(ThreadStore.STATUSES):
            updated = self.store.update(self.tid, expected_revision=revision, status=status,
                                        title='New title', next_step='Next', allow_callbacks=True)
            revision += 1
            self.assertEqual(updated['revision'], revision)
            self.assertEqual(updated['status'], status)
            self.assertTrue(updated['allow_callbacks'])
        with self.assertRaises(RevisionConflict):
            self.store.update(self.tid, expected_revision=1, title='Stale')
        with self.assertRaises(RevisionConflict):
            self.store.delete(self.tid, expected_revision=1)
        self.assertEqual(self.store.get(self.tid)['title'], 'New title')

    def test_validation(self):
        for title in ('', ' ', None, True, 'x' * 201, 'bad\x00title'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.store.create(title)
        for kwargs in ({'status': []}, {'status': 'invented'}, {'title': ''},
                       {'allow_callbacks': 1}, {'next_step': 'x' * 4001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.update(self.tid, expected_revision=1, **kwargs)
        for text in ('', ' ', None, 1, 'x' * 4001):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.add(text=text)
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_revision_validation_every_mutation(self):
        event_thread = self.add()
        event_id = event_thread['events'][0]['id']
        for revision in (True, False, 0, -1, 1.0, '1', None, 2 ** 63):
            for operation in (
                lambda: self.store.update(self.tid, expected_revision=revision),
                lambda: self.store.delete(self.tid, expected_revision=revision),
                lambda: self.add(expected_revision=revision),
                lambda: self.store.revise_event(self.tid, event_id, expected_revision=revision, text='new'),
            ):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    operation()
        self.assertEqual(self.store.get(self.tid)['revision'], 2)

    def test_missing_ids_never_create(self):
        for operation in (
            lambda: self.store.get('missing'),
            lambda: self.store.update('missing', expected_revision=1),
            lambda: self.store.delete('missing', expected_revision=1),
            lambda: self.store.add_event('missing', expected_revision=1, kind='question', text='Why?'),
            lambda: self.store.revise_event('missing', 'event', expected_revision=1, text='new'),
        ):
            with self.assertRaises(KeyError):
                operation()
        self.assertEqual(len(self.store.list_threads()), 1)

    def test_delete_tombstone_and_late_writes(self):
        thread = self.add(dedupe_key='once')
        event_id = thread['events'][0]['id']
        tombstone = self.store.delete(self.tid, expected_revision=2)
        self.assertEqual(set(tombstone), {'id', 'deleted_at'})
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        for operation in (
            lambda: self.store.get(self.tid),
            lambda: self.store.update(self.tid, expected_revision=2, title='revive'),
            lambda: self.add(expected_revision=2, dedupe_key='once'),
            lambda: self.store.revise_event(self.tid, event_id, expected_revision=2, text='new'),
            lambda: self.store.delete(self.tid, expected_revision=2),
        ):
            with self.assertRaises(KeyError):
                operation()
        self.assertEqual(self.store.list_threads(), [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM threads').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM thread_events').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT * FROM thread_tombstones').fetchone(),
                             (self.tid, tombstone['deleted_at']))
            self.assertEqual([row[1] for row in db.execute('PRAGMA table_info(thread_tombstones)')],
                             ['id', 'deleted_at'])
        with patch.object(_MODULE.uuid, 'uuid4', return_value=self.tid):
            with self.assertRaises(ValueError):
                self.store.create('collision')

    def test_exact_dedupe_and_mismatch(self):
        first = self.add(dedupe_key='once')
        self.assertEqual(self.add(dedupe_key='once'), first)
        for payload in ({'text': 'Different'}, {'kind': 'question'}, {'source_id': 'different'},
                        {'kind': 'tool_receipt', 'source': 'tool_verified', 'source_id': 'receipt'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.add(expected_revision=2, dedupe_key='once', **payload)
        self.assertEqual(self.store.get(self.tid), first)
        with self.assertRaises(RevisionConflict):
            self.add(dedupe_key='different')
        second_thread = self.store.create('Other')
        self.store.add_event(second_thread['id'], expected_revision=1, kind='question',
                             text='Different thread', dedupe_key='once')

    def test_provenance_combinations(self):
        for kind in ('decision', 'question', 'progress', 'tool_receipt', 'unknown'):
            for source in ('user_stated', 'tool_verified', 'inferred', 'llm', ''):
                valid = (kind in ThreadStore.USER_KINDS and source == 'user_stated') or (
                    kind == 'tool_receipt' and source == 'tool_verified')
                revision = self.store.get(self.tid)['revision']
                with self.subTest(kind=kind, source=source):
                    if valid:
                        result = self.add(expected_revision=revision, kind=kind, source=source, source_id='id')
                        self.assertEqual(result['events'][-1]['source'], source)
                    else:
                        with self.assertRaises(ValueError):
                            self.add(expected_revision=revision, kind=kind, source=source, source_id='id')
        for source_id in ('', ' ', None):
            with self.assertRaises(ValueError):
                self.add(kind='tool_receipt', source='tool_verified', source_id=source_id)

    def test_event_revision_preserves_provenance_and_dedupe(self):
        thread = self.add(dedupe_key='once')
        event = thread['events'][0]
        revised = self.store.revise_event(self.tid, event['id'], expected_revision=2, text='Corrected')
        self.assertEqual(revised['revision'], 3)
        self.assertEqual(revised['events'][0]['source'], 'user_stated')
        self.assertEqual(self.add(dedupe_key='once'), revised)
        with self.assertRaises(ValueError):
            self.add(expected_revision=3, dedupe_key='once', text='Corrected')
        with self.assertRaises(RevisionConflict):
            self.store.revise_event(self.tid, event['id'], expected_revision=2, text='stale')
        with self.assertRaises(KeyError):
            self.store.revise_event(self.tid, 'missing', expected_revision=3, text='new')
        receipt = self.add(expected_revision=3, kind='tool_receipt', source='tool_verified', source_id='job')
        with self.assertRaises(ValueError):
            self.store.revise_event(self.tid, receipt['events'][-1]['id'], expected_revision=4, text='Fake')
        self.assertEqual(self.store.get(self.tid), receipt)

    def test_event_limit_and_retry(self):
        for index in range(ThreadStore.MAX_EVENTS):
            self.add(expected_revision=index + 1, dedupe_key=str(index))
        with self.assertRaises(ValueError):
            self.add(expected_revision=ThreadStore.MAX_EVENTS + 1)
        retry = self.add(dedupe_key='0')
        self.assertEqual(len(retry['events']), ThreadStore.MAX_EVENTS)
        self.assertEqual(retry['revision'], ThreadStore.MAX_EVENTS + 1)

    def test_list_is_bounded_and_excludes_deleted(self):
        for index in range(105):
            self.store.create(str(index))
        self.store.delete(self.tid, expected_revision=1)
        listed = self.store.list_threads()
        self.assertEqual(len(listed), 100)
        self.assertNotIn(self.tid, [thread['id'] for thread in listed])
        self.assertEqual([t['updated_at'] for t in listed], sorted(
            [t['updated_at'] for t in listed], reverse=True))

    def test_atomic_cross_connection_cas(self):
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        gate = threading.Barrier(2)
        outcomes = []
        def writer(store):
            gate.wait()
            try:
                store.add_event(self.tid, expected_revision=1, kind='progress', text='work')
                outcomes.append('ok')
            except RevisionConflict:
                outcomes.append('conflict')
            except Exception as error:
                outcomes.append(error)
        workers = [threading.Thread(target=writer, args=(store,)) for store in (self.store, other)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(outcomes, ['ok', 'conflict'])
        thread = self.store.get(self.tid)
        self.assertEqual(thread['revision'], 2)
        self.assertEqual(len(thread['events']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
