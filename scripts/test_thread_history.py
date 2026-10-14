"""Standalone thread-version contracts; synthetic temporary SQLite only.

Run: python -B scripts/test_thread_history.py. Imports the pure store by path;
does not bootstrap the app, read configuration, or call a model.
"""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    'standalone_thread_history',
    Path(__file__).resolve().parents[1] / 'server/interaction/threads.py')
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ThreadStore = _MODULE.ThreadStore
RevisionConflict = _MODULE.RevisionConflict


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thread-history-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.sqlite'
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.thread = self.store.create('一个反复修订的方案', session_id='origin-session')
        self.tid = self.thread['id']

    def add(self, **changes):
        args = dict(expected_revision=1, kind='decision', text='采用方案 A')
        args.update(changes)
        return self.store.add_event(self.tid, **args)

    @staticmethod
    def evidence(**changes):
        result = {'state': 'user_confirmed', 'request_text': '把刚才讨论的方案记下来',
                  'session_id': 'request-session', 'captured_at': 1704067200}
        result.update(changes)
        return result

    @staticmethod
    def ref(version=1):
        return {'type': 'note', 'id': 'saved-material', 'version': version}

    def history(self, event_id):
        return self.store.event_history(self.tid, event_id)

    def test_defaults_detached_metadata_and_tool_provenance(self):
        thread = self.add()
        event = thread['events'][0]
        self.assertEqual(event['version'], 1)
        self.assertEqual(event['evidence'], {'state': 'manual'})
        self.assertEqual(event['object_refs'], [])
        history = self.history(event['id'])
        self.assertEqual(history[0]['previous_version'], None)
        history[0]['evidence']['state'] = 'legacy'
        event['object_refs'].append(self.ref())
        self.assertEqual(self.history(event['id'])[0]['evidence'], {'state': 'manual'})
        self.assertEqual(self.store.get(self.tid)['events'][0]['object_refs'], [])
        receipt = self.add(expected_revision=2, kind='tool_receipt', text='已保存一个版本',
                           source='tool_verified', source_id='run:0')
        self.assertEqual(receipt['events'][-1]['evidence'], {'state': 'tool_verified'})
        self.assertEqual(receipt['events'][-1]['source_id'], 'run:0')

    def test_captured_request_and_fixed_ref_are_not_live_selection(self):
        evidence, refs = self.evidence(), [self.ref(2)]
        thread = self.add(evidence=evidence, object_refs=refs)
        event = thread['events'][0]
        evidence['request_text'] = '后来另一句'
        refs[0]['version'] = 99
        other = self.store.create('另一个事项', session_id='another-session')
        self.store.select_thread(other['id'])
        saved = self.history(event['id'])[0]
        self.assertEqual(saved['evidence'], self.evidence())
        self.assertEqual(saved['object_refs'], [self.ref(2)])
        self.assertEqual(self.store.get(self.tid)['session_id'], 'origin-session')
        self.assertNotIn('message_id', saved['evidence'])

    def test_manual_revision_clears_old_evidence_and_references(self):
        thread = self.add(evidence=self.evidence(), object_refs=[self.ref(1)], dedupe_key='once')
        event = thread['events'][0]
        before = self.history(event['id'])[0]
        revised = self.store.revise_event(self.tid, event['id'], expected_revision=2, text='改用方案 B')
        current = revised['events'][0]
        self.assertEqual(current['id'], event['id'])
        self.assertEqual(current['version'], 2)
        self.assertEqual(current['evidence'], {'state': 'manual'})
        self.assertEqual(current['object_refs'], [])
        history = self.history(event['id'])
        self.assertEqual(history[0], before)
        self.assertEqual([item['text'] for item in history], ['采用方案 A', '改用方案 B'])
        self.assertEqual([item['previous_version'] for item in history], [None, 1])

    def test_each_confirmed_revision_keeps_its_own_request_and_note_version(self):
        event = self.add(evidence=self.evidence(), object_refs=[self.ref(1)])['events'][0]
        changed = self.evidence(request_text='因为预算变了，把方案改为 B',
                                session_id='new-session', captured_at=1717200000)
        self.store.revise_event(self.tid, event['id'], expected_revision=2,
                               text='预算变化，改用方案 B', evidence=changed, object_refs=[self.ref(3)])
        history = self.history(event['id'])
        self.assertEqual(history[0]['evidence']['session_id'], 'request-session')
        self.assertEqual(history[1]['evidence'], changed)
        self.assertEqual([item['object_refs'][0]['version'] for item in history], [1, 3])
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.assertEqual(self.history(event['id']), history)
        self.assertEqual(self.store.get(self.tid)['events'][0]['version'], 2)

    def test_same_text_manual_revision_does_not_reuse_confirmed_evidence(self):
        event = self.add(evidence=self.evidence(), object_refs=[self.ref()])['events'][0]
        thread = self.store.revise_event(self.tid, event['id'], expected_revision=2, text=event['text'])
        self.assertEqual(thread['events'][0]['evidence'], {'state': 'manual'})
        self.assertEqual(thread['events'][0]['object_refs'], [])
        self.assertEqual(len(self.history(event['id'])), 2)

    def test_version_save_time_is_distinct_from_event_creation_time(self):
        with patch.object(_MODULE.time, 'time', return_value=1704067200):
            event = self.add()['events'][0]
        with patch.object(_MODULE.time, 'time', return_value=1717200000):
            revised = self.store.revise_event(self.tid, event['id'], expected_revision=2, text='改用方案 B')
        current = revised['events'][0]
        self.assertEqual(current['created_at'], 1704067200)
        self.assertEqual(current['updated_at'], 1717200000)
        self.assertEqual([row['created_at'] for row in self.history(event['id'])],
                         [1704067200, 1717200000])

    def test_original_schema_migration_only_preserves_the_known_current_text(self):
        path = Path(self.temp.name) / 'legacy.sqlite'
        payload = json.dumps(['decision', '旧版最后保存的文字', 'user_stated', ''],
                             ensure_ascii=True, separators=(',', ':'))
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''
                CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT NOT NULL, session_id TEXT NOT NULL,
                    status TEXT NOT NULL, next_step TEXT NOT NULL, allow_callbacks INTEGER NOT NULL,
                    revision INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE thread_events(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id)
                    ON DELETE CASCADE, kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL,
                    source_id TEXT NOT NULL, dedupe_key TEXT NOT NULL, dedupe_payload TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE thread_tombstones(id TEXT PRIMARY KEY, deleted_at REAL NOT NULL);
                ''')
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)',
                       ('old-thread', 'Old', 'known-origin', 'discussing', '', 0, 4, 10, 20))
            db.execute('INSERT INTO thread_events VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('old-event', 'old-thread', 'decision', '旧版最后保存的文字',
                        'user_stated', '', 'retry', payload, 11, 19))
            db.commit()
        store = ThreadStore(path)
        self.addCleanup(store.close)
        first = store.get('old-thread')
        history = store.event_history('old-thread', 'old-event')
        self.assertEqual(history[0]['text'], '旧版最后保存的文字')
        self.assertEqual(history[0]['evidence'], {'state': 'legacy'})
        self.assertEqual(history[0]['object_refs'], [])
        self.assertEqual(history[0]['previous_version'], None)
        self.assertEqual(history[0]['created_at'], 19)
        self.assertEqual(history[0]['updated_at'], 19)
        self.assertEqual(len(history), 1)
        self.assertEqual(store.add_event('old-thread', expected_revision=1, kind='decision',
            text='旧版最后保存的文字', dedupe_key='retry'), first)
        store.revise_event('old-thread', 'old-event', expected_revision=4, text='迁移后的明确修订')
        expected = store.event_history('old-thread', 'old-event')
        store.close()
        reopened = ThreadStore(path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.event_history('old-thread', 'old-event'), expected)
        self.assertEqual([row['evidence']['state'] for row in expected], ['legacy', 'manual'])
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_metadata_validation_never_partially_writes(self):
        invalid = [True, [], {}, {'state': []}, {'state': 'model_claim'},
                   {'state': 'manual', 'request_text': '不能夹带'},
                   {'state': 'tool_verified'},
                   self.evidence(request_text=''), self.evidence(request_text='x' * 4006),
                   self.evidence(session_id='x' * 257), self.evidence(session_id='bad\x00id'),
                   self.evidence(message_id=17)]
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.add(evidence=evidence)
        self.assertEqual(self.store.get(self.tid), self.thread)
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM thread_event_versions').fetchone()[0], 0)
        evidence = self.evidence(request_text='x' * 4005)
        self.assertEqual(self.add(evidence=evidence)['events'][0]['evidence'], evidence)

    def test_supported_capture_times_and_invalid_numbers(self):
        for index, value in enumerate((0, 253402300799, 1704067200.25)):
            with self.subTest(value=value):
                saved = self.add(expected_revision=index + 1, evidence=self.evidence(captured_at=value))
                self.assertEqual(saved['events'][-1]['evidence']['captured_at'], value)
        before = self.store.get(self.tid)
        for value in (True, False, -1, 253402300800, float('nan'), float('inf'),
                      float('-inf'), 10 ** 1000, None, '1704067200'):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                self.add(expected_revision=4, evidence=self.evidence(captured_at=value))
        self.assertEqual(self.store.get(self.tid), before)

    def test_object_ref_validation_only_accepts_bounded_exact_note_versions(self):
        invalid = [True, {}, (), [self.ref()] * 7, [None],
                   [{'type': 'thread', 'id': 'id', 'version': 1}],
                   [{'type': 'note', 'id': '', 'version': 1}],
                   [{'type': 'note', 'id': 'bad\x00id', 'version': 1}],
                   [{'type': 'note', 'id': 'x' * 257, 'version': 1}]]
        invalid.extend([[self.ref(value)] for value in (True, 0, -1, 1.0, 2 ** 63, None)])
        invalid.extend([[dict(self.ref(), title='extra')], [{'type': 'note', 'id': 'id'}]])
        for refs in invalid:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.add(object_refs=refs)
        self.assertEqual(self.store.get(self.tid), self.thread)
        # The store does not open a separate note database to authenticate refs.
        saved = self.add(object_refs=[self.ref(9223372036854775807)] * 6)
        self.assertEqual(len(saved['events'][0]['object_refs']), 6)

    def test_tool_receipt_cannot_claim_user_confirmation(self):
        for evidence in ({'state': 'manual'}, self.evidence()):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.add(kind='tool_receipt', source='tool_verified', source_id='run:1', evidence=evidence)
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_dedupe_preserves_original_payload_after_revision(self):
        evidence, refs = self.evidence(), [self.ref()]
        event = self.add(evidence=evidence, object_refs=refs, dedupe_key='captured')['events'][0]
        revised = self.store.revise_event(self.tid, event['id'], expected_revision=2, text='后来修订')
        self.assertEqual(self.add(evidence=evidence, object_refs=refs, dedupe_key='captured'), revised)
        for changes in ({'evidence': self.evidence(session_id='other')},
                        {'evidence': self.evidence(captured_at=1704067201)},
                        {'object_refs': [self.ref(2)]}, {'text': '后来修订'}):
            args = dict(evidence=evidence, object_refs=refs, dedupe_key='captured')
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.add(expected_revision=3, **args)
        self.assertEqual(self.store.get(self.tid), revised)
        self.assertEqual(len(self.history(event['id'])), 2)

    def test_cross_connection_revision_race_appends_one_version(self):
        event = self.add()['events'][0]
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        barrier = threading.Barrier(2)
        outcomes = []

        def revise(store, text):
            barrier.wait()
            try:
                store.revise_event(self.tid, event['id'], expected_revision=2, text=text)
                outcomes.append('ok')
            except RevisionConflict:
                outcomes.append('conflict')
            except Exception as error:
                outcomes.append(error)

        workers = [threading.Thread(target=revise, args=(store, text))
                   for store, text in ((self.store, '方案 B'), (other, '方案 C'))]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(outcomes, ['ok', 'conflict'])
        history = self.history(event['id'])
        self.assertEqual([row['version'] for row in history], [1, 2])
        self.assertEqual(history[0]['text'], '采用方案 A')
        self.assertEqual(self.store.get(self.tid)['revision'], 3)
        self.assertEqual(other.event_history(self.tid, event['id']), history)

    def test_failed_bump_rolls_back_both_current_and_snapshot_writes(self):
        with patch.object(self.store, '_bump', side_effect=RevisionConflict('controlled')):
            with self.assertRaises(RevisionConflict):
                self.add()
        self.assertEqual(self.store.get(self.tid), self.thread)
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM thread_events').fetchone()[0], 0)
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM thread_event_versions').fetchone()[0], 0)
        event = self.add()['events'][0]
        before, history = self.store.get(self.tid), self.history(event['id'])
        with patch.object(self.store, '_bump', side_effect=RevisionConflict('controlled')):
            with self.assertRaises(RevisionConflict):
                self.store.revise_event(self.tid, event['id'], expected_revision=2, text='未提交的修订')
        self.assertEqual(self.store.get(self.tid), before)
        self.assertEqual(self.history(event['id']), history)

    def test_history_requires_the_exact_thread_and_delete_cascades_every_version(self):
        event = self.add()['events'][0]
        self.store.revise_event(self.tid, event['id'], expected_revision=2, text='方案 B')
        other = self.store.create('不相关事项')
        with self.assertRaises(KeyError):
            self.store.event_history(other['id'], event['id'])
        with self.assertRaises(KeyError):
            self.store.event_history(self.tid, 'missing-event')
        self.store.delete(self.tid, expected_revision=3)
        for query in ('SELECT count(*) FROM thread_events', 'SELECT count(*) FROM thread_event_versions'):
            self.assertEqual(self.store._db.execute(query).fetchone()[0], 0)
        with self.assertRaises(KeyError):
            self.history(event['id'])
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        with self.assertRaises(KeyError):
            self.history(event['id'])
        self.assertEqual(self.store._db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_version_capacity_refuses_without_losing_early_history(self):
        self.assertEqual(ThreadStore.MAX_EVENT_VERSIONS, 200)
        event = self.add()['events'][0]
        for version in range(2, 201):
            self.store.revise_event(self.tid, event['id'], expected_revision=version,
                                   text=f'明确保存的修订 {version}')
        before = self.store.get(self.tid)
        with self.assertRaisesRegex(ValueError, 'version limit'):
            self.store.revise_event(self.tid, event['id'], expected_revision=201, text='不能覆盖历史')
        history = self.history(event['id'])
        self.assertEqual(len(history), 200)
        self.assertEqual(history[0]['text'], '采用方案 A')
        self.assertEqual(history[-1]['previous_version'], 199)
        self.assertEqual(self.store.get(self.tid), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
