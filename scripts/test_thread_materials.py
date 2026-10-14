"""Explicit fixed-version material contracts; pure store and temporary DBs only.

Run: python -B scripts/test_thread_materials.py. No app/config/model imports.
"""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    'standalone_thread_materials',
    Path(__file__).resolve().parents[1] / 'server/interaction/threads.py')
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ThreadStore = _MODULE.ThreadStore
RevisionConflict = _MODULE.RevisionConflict


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thread-materials-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.sqlite'
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.thread = self.store.create('明确资料用途的事项', session_id='origin-session')
        self.tid = self.thread['id']

    def link(self, **changes):
        args = dict(expected_revision=1, note_id='fixed-note', version=1, purpose='reference')
        args.update(changes)
        return self.store.link_material(self.tid, **args)

    def test_empty_defaults_and_detached_current_materials(self):
        self.assertEqual(self.thread['materials'], [])
        saved = self.link()
        material = saved['materials'][0]
        self.assertEqual(set(material), {'id', 'thread_id', 'note_id', 'version', 'purpose',
                                         'created_at', 'updated_at'})
        self.assertEqual(material['thread_id'], self.tid)
        self.assertEqual(material['note_id'], 'fixed-note')
        self.assertEqual(material['purpose'], 'reference')
        self.assertEqual(saved['revision'], 2)
        material['purpose'] = 'draft'
        saved['materials'].clear()
        self.assertEqual(self.store.get(self.tid)['materials'][0]['purpose'], 'reference')
        self.assertIsNone(self.store.get_selection())

    def test_purposes_and_creation_order_do_not_follow_updated_time(self):
        with patch.object(_MODULE.time, 'time', return_value=100):
            first = self.link()['materials'][0]
        with patch.object(_MODULE.time, 'time', return_value=200):
            second = self.link(expected_revision=2, note_id='draft-note', purpose='draft')['materials'][1]
        with patch.object(_MODULE.time, 'time', return_value=300):
            third = self.link(expected_revision=3, note_id='result-note', purpose='artifact')['materials'][2]
        with patch.object(_MODULE.time, 'time', return_value=400):
            updated = self.store.update_material(self.tid, first['id'], expected_revision=4, purpose='artifact')
        self.assertEqual([item['id'] for item in updated['materials']],
                         [first['id'], second['id'], third['id']])
        self.assertEqual(updated['materials'][0]['created_at'], 100)
        self.assertEqual(updated['materials'][0]['updated_at'], 400)
        self.assertEqual([item['purpose'] for item in updated['materials']], ['artifact', 'draft', 'artifact'])

    def test_exact_link_retry_requires_live_cas_and_does_not_bump(self):
        saved = self.link()
        with patch.object(_MODULE.time, 'time', return_value=253402300799):
            retry = self.link(expected_revision=2)
        self.assertEqual(retry, saved)
        with self.assertRaises(RevisionConflict):
            self.link(expected_revision=1)
        self.assertEqual(self.store.get(self.tid), saved)

    def test_fresh_noop_at_max_revision_does_not_require_an_increment(self):
        material = self.link()['materials'][0]
        maximum = 9223372036854775807
        with self.store._transaction():
            self.store._db.execute('UPDATE threads SET revision=? WHERE id=?', (maximum, self.tid))
        saved = self.store.get(self.tid)
        self.assertEqual(self.link(expected_revision=maximum), saved)
        self.assertEqual(self.store.update_material(self.tid, material['id'], expected_revision=maximum,
                                                    version=1, purpose='reference'), saved)
        for action in (
            lambda: self.link(expected_revision=maximum, note_id='new-note'),
            lambda: self.store.update_material(self.tid, material['id'], expected_revision=maximum, version=2),
            lambda: self.store.remove_material(self.tid, material['id'], expected_revision=maximum),
        ):
            with self.assertRaisesRegex(ValueError, 'revision exhausted'):
                action()
        self.assertEqual(self.store.get(self.tid), saved)

    def test_same_ref_has_one_purpose_changed_only_by_explicit_update(self):
        saved = self.link()
        material = saved['materials'][0]
        with self.assertRaisesRegex(ValueError, 'material_already_linked'):
            self.link(expected_revision=2, purpose='draft')
        self.assertEqual(self.store.get(self.tid), saved)
        revised = self.store.update_material(self.tid, material['id'], expected_revision=2, purpose='draft')
        self.assertEqual(len(revised['materials']), 1)
        self.assertEqual(revised['materials'][0]['id'], material['id'])
        self.assertEqual(revised['materials'][0]['purpose'], 'draft')

    def test_single_draft_applies_to_link_and_update(self):
        draft = self.link(purpose='draft')['materials'][0]
        with self.assertRaisesRegex(ValueError, '^draft_already_linked$'):
            self.link(expected_revision=2, note_id='another-note', purpose='draft')
        reference = self.link(expected_revision=2, note_id='another-note')['materials'][1]
        with self.assertRaisesRegex(ValueError, '^draft_already_linked$'):
            self.store.update_material(self.tid, reference['id'], expected_revision=3, purpose='draft')
        changed = self.store.update_material(self.tid, draft['id'], expected_revision=3, purpose='reference')
        changed = self.store.update_material(self.tid, reference['id'],
                                             expected_revision=changed['revision'], purpose='draft')
        self.assertEqual([item['id'] for item in changed['materials'] if item['purpose'] == 'draft'],
                         [reference['id']])

    def test_material_limit_retry_and_remove_free_a_slot(self):
        self.assertEqual(ThreadStore.MAX_MATERIALS, 12)
        saved = self.thread
        for index in range(12):
            saved = self.link(expected_revision=saved['revision'], note_id=f'note-{index}')
        with self.assertRaisesRegex(ValueError, 'material_limit'):
            self.link(expected_revision=saved['revision'], note_id='thirteenth-note')
        self.assertEqual(self.link(expected_revision=saved['revision'], note_id='note-0'), saved)
        removed = self.store.remove_material(self.tid, saved['materials'][0]['id'], expected_revision=13)
        linked = self.link(expected_revision=removed['revision'], note_id='replacement')
        self.assertEqual(len(linked['materials']), 12)
        self.assertEqual(linked['materials'][-1]['note_id'], 'replacement')

    def test_same_note_can_have_distinct_fixed_versions_and_collision_is_rejected(self):
        saved = self.link(version=1)
        saved = self.link(expected_revision=2, version=2, purpose='artifact')
        first, second = saved['materials']
        with self.assertRaisesRegex(ValueError, 'material_already_linked'):
            self.store.update_material(self.tid, first['id'], expected_revision=3, version=2)
        self.assertEqual(self.store.get(self.tid), saved)
        changed = self.store.update_material(self.tid, second['id'], expected_revision=3,
                                             version=3, purpose='draft')
        self.assertEqual(changed['materials'][1]['id'], second['id'])
        self.assertEqual(changed['materials'][1]['note_id'], 'fixed-note')
        self.assertEqual(changed['materials'][1]['version'], 3)

    def test_one_note_in_two_matters_keeps_versions_and_selection_independent(self):
        first = self.link(version=1, purpose='draft')
        other = self.store.create('另一个事项', session_id='other-session')
        second = self.store.link_material(other['id'], expected_revision=1,
                                          note_id='fixed-note', version=3, purpose='draft')
        self.store.select_thread(other['id'])
        first = self.store.update_material(self.tid, first['materials'][0]['id'],
                                           expected_revision=2, version=2)
        self.assertEqual(first['materials'][0]['version'], 2)
        self.assertEqual(self.store.get(other['id']), second)
        self.assertEqual(self.store.get_selection(), other['id'])
        self.assertEqual(first['session_id'], 'origin-session')

    def test_update_and_unlink_do_not_rewrite_historical_event_refs(self):
        ref = {'type': 'note', 'id': 'fixed-note', 'version': 1}
        event = self.store.add_event(self.tid, expected_revision=1, kind='decision',
                                    text='当时参考第一版', object_refs=[ref])['events'][0]
        saved = self.link(expected_revision=2, purpose='draft')
        material = saved['materials'][0]
        history = self.store.event_history(self.tid, event['id'])
        changed = self.store.update_material(self.tid, material['id'], expected_revision=3,
                                             version=2, purpose='artifact')
        self.assertEqual(changed['events'][0]['object_refs'], [ref])
        removed = self.store.remove_material(self.tid, material['id'], expected_revision=4)
        self.assertEqual(removed['materials'], [])
        self.assertEqual(removed['events'][0]['object_refs'], [ref])
        self.assertEqual(self.store.event_history(self.tid, event['id']), history)

    def test_shape_validation_does_not_guess_external_note_existence(self):
        for note_id in ('', ' ', None, True, [], 'bad\x00id', 'x' * 257):
            with self.subTest(note_id=note_id), self.assertRaises(ValueError):
                self.link(note_id=note_id)
        for version in (True, False, 0, -1, 1.0, '1', None, 2 ** 63):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.link(version=version)
        for purpose in ('', 'current', 'Reference', None, True, [], {}):
            with self.subTest(purpose=purpose), self.assertRaises(ValueError):
                self.link(purpose=purpose)
        self.assertEqual(self.store.get(self.tid), self.thread)
        # There is no NoteStore or note database in this isolated store test.
        saved = self.link(note_id='unverified-external-note', version=9223372036854775807)
        self.assertEqual(saved['materials'][0]['version'], 9223372036854775807)

    def test_update_requires_a_valid_field_and_noop_preserves_revision(self):
        saved = self.link()
        material = saved['materials'][0]
        with self.assertRaisesRegex(ValueError, 'material_update_required'):
            self.store.update_material(self.tid, material['id'], expected_revision=2)
        for changes in ({'version': True}, {'version': 0}, {'version': 2 ** 63},
                        {'purpose': 'unknown'}, {'purpose': []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.update_material(self.tid, material['id'], expected_revision=2, **changes)
        self.assertEqual(self.store.update_material(self.tid, material['id'], expected_revision=2,
                                                    version=1, purpose='reference'), saved)
        with self.assertRaises(RevisionConflict):
            self.store.update_material(self.tid, material['id'], expected_revision=1, version=1)

    def test_missing_or_foreign_ids_never_mutate_or_create_associations(self):
        saved = self.link()
        material_id = saved['materials'][0]['id']
        other = self.store.create('另一事项')
        for action in (
            lambda: self.store.link_material('missing', expected_revision=1,
                                              note_id='note', version=1, purpose='reference'),
            lambda: self.store.update_material(self.tid, 'missing', expected_revision=2, version=2),
            lambda: self.store.remove_material(self.tid, 'missing', expected_revision=2),
            lambda: self.store.update_material(other['id'], material_id, expected_revision=1, version=2),
            lambda: self.store.remove_material(other['id'], material_id, expected_revision=1),
        ):
            with self.assertRaises(KeyError):
                action()
        for identifier in ('', ' ', True, [], 'bad\x00id', 'x' * 257):
            for action in (
                lambda: self.store.update_material(self.tid, identifier, expected_revision=2, version=2),
                lambda: self.store.remove_material(self.tid, identifier, expected_revision=2),
            ):
                with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                    action()
        self.assertEqual(self.store.get(self.tid), saved)
        self.assertEqual(self.store.get(other['id']), other)

    def test_revision_validation_and_stale_cas_apply_to_every_mutation(self):
        saved = self.link()
        material_id = saved['materials'][0]['id']
        for revision in (True, False, 0, -1, 1.0, '2', None, 2 ** 63):
            for action in (
                lambda: self.link(expected_revision=revision),
                lambda: self.store.update_material(self.tid, material_id,
                                                    expected_revision=revision, version=2),
                lambda: self.store.remove_material(self.tid, material_id, expected_revision=revision),
            ):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    action()
        for action in (
            lambda: self.link(expected_revision=1),
            lambda: self.store.update_material(self.tid, material_id, expected_revision=1, version=2),
            lambda: self.store.remove_material(self.tid, material_id, expected_revision=1),
        ):
            with self.assertRaises(RevisionConflict):
                action()
        self.assertEqual(self.store.get(self.tid), saved)

    def test_legacy_database_starts_empty_without_promoting_selection_or_event_refs(self):
        event = self.store.add_event(self.tid, expected_revision=1, kind='decision', text='旧决定',
            object_refs=[{'type': 'note', 'id': 'legacy-linked-note', 'version': 4}])['events'][0]
        self.store.select_thread(self.tid)
        saved = self.store.get(self.tid)
        history = self.store.event_history(self.tid, event['id'])
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            # Previous release has exactly these records, but no material table.
            db.execute('DROP TABLE thread_materials')
            db.commit()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.get(self.tid), saved)
        self.assertEqual(self.store.get_selection(), self.tid)
        self.assertEqual(self.store.event_history(self.tid, event['id']), history)
        self.assertEqual(self.store.get(self.tid)['materials'], [])
        linked = self.link(expected_revision=2, note_id='explicit-new-note', version=2)
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.get(self.tid), linked)
        self.assertEqual(self.store._db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_cross_connection_link_and_update_have_one_cas_winner(self):
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        self.race(
            lambda: self.link(purpose='draft'),
            lambda: other.link_material(self.tid, expected_revision=1,
                                        note_id='other-note', version=1, purpose='draft'))
        saved = self.store.get(self.tid)
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(len(saved['materials']), 1)
        material_id = saved['materials'][0]['id']
        self.race(
            lambda: self.store.update_material(self.tid, material_id, expected_revision=2, version=2),
            lambda: other.update_material(self.tid, material_id, expected_revision=2, version=3))
        saved = self.store.get(self.tid)
        self.assertEqual(saved['revision'], 3)
        self.assertEqual(len(saved['materials']), 1)
        self.assertIn(saved['materials'][0]['version'], (2, 3))
        self.assertEqual(other.get(self.tid), saved)

    def test_cross_connection_remove_vs_update_cannot_leave_a_partial_change(self):
        saved = self.link()
        material_id = saved['materials'][0]['id']
        other = ThreadStore(self.path)
        self.addCleanup(other.close)
        self.race(
            lambda: self.store.remove_material(self.tid, material_id, expected_revision=2),
            lambda: other.update_material(self.tid, material_id, expected_revision=2, version=2))
        saved = self.store.get(self.tid)
        self.assertEqual(saved['revision'], 3)
        self.assertTrue(not saved['materials'] or saved['materials'][0]['version'] == 2)
        self.assertEqual(other.get(self.tid), saved)

    def race(self, first, second):
        barrier, outcomes = threading.Barrier(2), []

        def work(action):
            barrier.wait()
            try:
                action()
                outcomes.append('ok')
            except RevisionConflict:
                outcomes.append('conflict')
            except Exception as error:
                outcomes.append(error)

        workers = [threading.Thread(target=work, args=(action,)) for action in (first, second)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(outcomes, ['ok', 'conflict'])

    def test_failed_revision_bump_rolls_back_link_update_and_remove(self):
        with patch.object(self.store, '_bump', side_effect=RevisionConflict('controlled')):
            with self.assertRaises(RevisionConflict):
                self.link()
        self.assertEqual(self.store.get(self.tid), self.thread)
        saved = self.link()
        material_id = saved['materials'][0]['id']
        for action in (
            lambda: self.store.update_material(self.tid, material_id, expected_revision=2, version=2),
            lambda: self.store.remove_material(self.tid, material_id, expected_revision=2),
        ):
            with patch.object(self.store, '_bump', side_effect=RevisionConflict('controlled')):
                with self.assertRaises(RevisionConflict):
                    action()
            self.assertEqual(self.store.get(self.tid), saved)

    def test_delete_cascades_materials_and_never_revives_the_thread(self):
        saved = self.link(purpose='draft')
        material_id = saved['materials'][0]['id']
        self.store.select_thread(self.tid)
        self.store.delete(self.tid, expected_revision=2)
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM thread_materials').fetchone()[0], 0)
        self.assertIsNone(self.store.get_selection())
        self.store.close()
        self.store = ThreadStore(self.path)
        self.addCleanup(self.store.close)
        for action in (
            lambda: self.link(expected_revision=2),
            lambda: self.store.update_material(self.tid, material_id, expected_revision=2, version=2),
            lambda: self.store.remove_material(self.tid, material_id, expected_revision=2),
        ):
            with self.assertRaises(KeyError):
                action()
        self.assertEqual(self.store._db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
