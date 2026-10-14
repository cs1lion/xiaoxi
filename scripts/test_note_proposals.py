"""Temporary notes only: no app singleton, personal data, model or audio calls."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import uuid


_ROOT = Path(__file__).resolve().parents[1] / 'server/assistant'
_PACKAGE = '_standalone_note_proposal_tests'
_package = types.ModuleType(_PACKAGE)
_package.__path__ = [str(_ROOT)]
sys.modules[_PACKAGE] = _package
for _name in ('notes', 'note_proposals'):
    _spec = importlib.util.spec_from_file_location(_PACKAGE + '.' + _name,
                                                 _ROOT / (_name + '.py'))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
NoteStore = sys.modules[_PACKAGE + '.notes'].NoteStore
NoteProposalService = sys.modules[_PACKAGE + '.note_proposals'].NoteProposalService
NoteProposalConflict = sys.modules[_PACKAGE + '.note_proposals'].NoteProposalConflict


class Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='aqi-note-proposals-')
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'notes.db'
        self.store = NoteStore(self.path)
        self.note = self.store.create('合成演示稿', '第一段。\n第二段。', '合成原稿来源')
        self.nid = self.note['id']
        self.now = 1000.0
        self.service = NoteProposalService(self.store, clock=lambda: self.now)

    def propose(self, **kwargs):
        args = dict(note_id=self.nid, version=1, title='修改后演示稿',
                    body='第一段更清楚。\n第二段保留。', source_text='请把第一段写清楚，第二段保留。')
        args.update(kwargs)
        return self.service.propose(**args)

    def assert_consumed(self, token, version=1):
        for action in (lambda: self.service.get(token),
                       lambda: self.service.confirm(token, version),
                       lambda: self.service.revise(token, version, '标题', '正文'),
                       lambda: self.service.reject(token, version)):
            with self.assertRaises(KeyError):
                action()

    def test_preview_retains_exact_base_and_never_writes(self):
        original = self.store.get_version(self.nid, 1)
        with patch.object(self.store, 'update', side_effect=AssertionError('preview wrote')):
            with patch.object(self.store, 'create', side_effect=AssertionError('preview wrote')):
                with patch.object(self.store, 'delete', side_effect=AssertionError('preview wrote')):
                    candidate = self.propose()
        self.assertEqual(candidate['note_id'], self.nid)
        self.assertEqual(candidate['base_note_version'], 1)
        self.assertEqual(candidate['original_title'], original['title'])
        self.assertEqual(candidate['original_body'], original['body'])
        self.assertEqual(candidate['version'], 1)
        self.assertEqual(candidate['expires_at'], 1600)
        self.assertEqual(candidate['status'], 'pending')
        self.assertIsNone(candidate['thread_id'])
        self.assertEqual(uuid.UUID(candidate['id']).version, 4)
        self.assertEqual(self.store.get(self.nid), original)
        self.assertEqual(self.store.get_version(self.nid, 1), original)
        self.assertEqual(len(self.store.history(self.nid)), 1)

    def test_confirm_creates_one_immutable_version_and_preserves_note_source(self):
        candidate = self.propose(title=' 修改后标题 ', body=' \n修改后正文\n ')
        result = self.service.confirm(candidate['id'], 1)
        self.assertEqual(result['id'], self.nid)
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['title'], candidate['title'])
        self.assertEqual(result['body'], candidate['body'])
        self.assertEqual(result['source'], '合成原稿来源')
        self.assertEqual(self.store.get_version(self.nid, 1), self.note)
        self.assertEqual(self.store.get_version(self.nid, 2), result)
        self.assertEqual([r['version'] for r in self.store.history(self.nid)], [2, 1])
        self.assert_consumed(candidate['id'])

    def test_returns_detached_dictionaries_and_revise_never_changes_base(self):
        candidate = self.propose(thread_id='synthetic-thread-a')
        token = candidate['id']
        candidate['note_id'] = 'forged'
        candidate['thread_id'] = 'synthetic-thread-b'
        self.service.get(token)['body'] = 'forged'
        self.service.list_pending(self.nid)[0]['original_body'] = 'forged'
        self.now = 1100
        revised = self.service.revise(token, 1, ' 新标题 ', ' 新正文 ')
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['note_id'], self.nid)
        self.assertEqual(revised['thread_id'], 'synthetic-thread-a')
        self.assertEqual(revised['base_note_version'], 1)
        self.assertEqual(revised['original_body'], self.note['body'])
        self.assertEqual(revised['original_title'], self.note['title'])
        self.assertEqual(revised['source_text'], candidate['source_text'])
        self.assertEqual(revised['expires_at'], 1600)
        self.assertEqual(self.store.get(self.nid), self.note)
        self.assertEqual(self.service.confirm(token, 2)['body'], '新正文')

    def test_current_version_is_required_and_read_race_is_not_refreshed(self):
        changed = self.store.update(self.nid, '并发标题', '并发正文', 1)
        with self.assertRaises(NoteProposalConflict):
            self.propose()
        candidate = self.propose(version=2)
        self.assertEqual(candidate['original_title'], changed['title'])
        self.assertEqual(candidate['original_body'], changed['body'])
        original_read = self.store.get_version
        def concurrent_read(note_id, version):
            original = original_read(note_id, version)
            self.store.update(note_id, '再次并发', '第三版正文', version)
            return original
        with patch.object(self.store, 'get_version', side_effect=concurrent_read):
            with self.assertRaises(NoteProposalConflict):
                self.propose(version=2)
        self.assertEqual(self.service.list_pending(self.nid), [candidate])

    def test_editing_base_blocks_confirm_and_revise_without_refresh(self):
        candidate = self.propose()
        current = self.store.update(self.nid, '最新标题', '最新正文', 1)
        with self.assertRaises(NoteProposalConflict):
            self.service.revise(candidate['id'], 1, '提案标题', '提案正文')
        with self.assertRaises(NoteProposalConflict):
            self.service.confirm(candidate['id'], 1)
        self.assertEqual(self.service.get(candidate['id']), candidate)
        self.assertEqual(self.store.get(self.nid), current)
        fresh = self.propose(version=2)
        self.assertEqual(fresh['base_note_version'], 2)
        self.assertEqual(fresh['original_body'], current['body'])
        self.assertNotEqual(fresh['id'], candidate['id'])

    def test_cas_race_performs_only_one_write_attempt_with_bound_version(self):
        candidate = self.propose()
        other = NoteStore(self.path)
        original_update = self.store.update
        def concurrent_update(note_id, title, body, version):
            other.update(note_id, '另一个连接', '真实并发编辑', version)
            return original_update(note_id, title, body, version)
        with patch.object(self.store, 'update', side_effect=concurrent_update) as update:
            with self.assertRaises(NoteProposalConflict):
                self.service.confirm(candidate['id'], 1)
            self.assertEqual(update.call_count, 1)
            self.assertEqual(update.call_args.args, (self.nid, candidate['title'], candidate['body'], 1))
        self.assertEqual(self.store.get(self.nid)['body'], '真实并发编辑')
        self.assertEqual(self.service.get(candidate['id']), candidate)
        self.assertEqual(len(self.store.history(self.nid)), 2)

    def test_delete_before_or_during_confirm_never_recreates_or_exposes_original(self):
        candidate = self.propose()
        self.store.delete(self.nid, 1)
        with self.assertRaises(KeyError):
            self.service.get(candidate['id'])
        self.assert_consumed(candidate['id'])
        self.assertEqual(self.store.search(), [])
        with self.assertRaises(KeyError):
            self.service.list_pending(self.nid)
        self.note = self.store.create('新的合成笔记', '原稿')
        self.nid = self.note['id']
        candidate = self.propose()
        original_update = self.store.update
        def concurrent_delete(note_id, title, body, version):
            self.store.delete(note_id, version)
            return original_update(note_id, title, body, version)
        with patch.object(self.store, 'update', side_effect=concurrent_delete) as update:
            with self.assertRaises(KeyError):
                self.service.confirm(candidate['id'], 1)
            self.assertEqual(update.call_count, 1)
        self.assert_consumed(candidate['id'])
        self.assertEqual(self.store.search(), [])

    def test_selection_switch_and_other_note_edits_never_retarget(self):
        other = self.store.create('另外一篇', '其他正文')
        selection = self.store.select(self.nid, version=1, expected_revision=1)
        candidate = self.propose()
        switched = self.store.select(other['id'], version=1, expected_revision=selection['revision'])
        self.store.update(other['id'], '其他新版', '其他新正文', 1)
        result = self.service.confirm(candidate['id'], 1)
        self.assertEqual(result['id'], self.nid)
        self.assertEqual(result['version'], 2)
        self.assertEqual(self.store.selection()['note_id'], other['id'])
        self.assertEqual(self.store.selection()['revision'], switched['revision'])
        self.assertEqual(self.store.get(other['id'])['body'], '其他新正文')

    def test_versions_and_invalid_fields_fail_without_mutation(self):
        candidate = self.propose()
        token = candidate['id']
        for action in (lambda v: self.service.confirm(token, v),
                       lambda v: self.service.revise(token, v, '有效标题', '有效正文'),
                       lambda v: self.service.reject(token, v)):
            for invalid in (True, False, None, 0, -1, 1.0, '1', 9223372036854775808):
                with self.subTest(version=invalid), self.assertRaises(ValueError):
                    action(invalid)
            with self.assertRaises(NoteProposalConflict):
                action(2)
        for title, body in [(None, '正文'), ('', '正文'), (' ', '正文'),
                            ('标题' * 81, '正文'), ('标题', None), ('标题', True),
                            ('标题', ''), ('标题', '字' * 20001), ('标题', 'a\x00b'),
                            ('\x00', '正文'), ('\u200b', '正文'), ('标题', '\x01')]:
            with self.subTest(title=title, body=body), self.assertRaises(ValueError):
                self.propose(title=title, body=body)
            with self.assertRaises(ValueError):
                self.service.revise(token, 1, title, body)
        self.assertEqual(self.service.get(token), candidate)
        self.assertEqual(self.store.get(self.nid), self.note)

    def test_identifiers_note_versions_and_source_are_bounded_and_typed(self):
        for invalid in (None, 2, True, '', ' ', 'x\x00y', 'x' * 257):
            with self.subTest(identifier=invalid), self.assertRaises(ValueError):
                self.propose(note_id=invalid)
        for invalid in (2, True, '', ' ', 'x\x00y', 'x' * 257):
            with self.subTest(thread=invalid), self.assertRaises(ValueError):
                self.propose(thread_id=invalid)
        for version in (True, False, 0, -1, 1.0, '1', None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.propose(version=version)
        for source in ('', ' ', None, True, 'x\x00y', '\u200b', '\x01', '字' * 12001):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.propose(source_text=source)
        with self.assertRaises(KeyError):
            self.propose(note_id='missing')
        source = ' 第一段改短。\n第二段保留。 '
        candidate = self.propose(title='字' * 160, body='字' * 20000, source_text=source)
        self.assertEqual(candidate['source_text'], source)
        self.assertEqual(len(candidate['title']), 160)
        self.assertEqual(len(candidate['body']), 20000)

    def test_rejection_expiry_and_revision_do_not_extend_deadline(self):
        rejected = self.propose()
        result = self.service.reject(rejected['id'], 1)
        self.assertEqual(result, {'id': rejected['id'], 'version': 1, 'status': 'rejected', 'rejected': True})
        self.assertNotIn('body', result)
        self.assert_consumed(rejected['id'])
        candidate = self.propose()
        self.now = 1599.99
        revised = self.service.revise(candidate['id'], 1, '修订标题', '修订正文')
        self.assertEqual(revised['expires_at'], 1600)
        self.now = 1600
        self.assert_consumed(candidate['id'], 2)
        self.assertEqual(self.service.list_pending(self.nid), [])
        self.assertEqual(self.store.get(self.nid), self.note)

    def test_capacity_scope_and_process_local_memory(self):
        first = self.propose()
        self.now += 1
        others = [self.propose() for _ in range(127)]
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.propose()
        self.assertEqual(self.service.get(first['id']), first)
        self.now = first['expires_at']
        replacement = self.propose()
        self.assert_consumed(first['id'])
        self.assertEqual(self.service.get(others[0]['id']), others[0])
        self.assertEqual(len(self.service.list_pending(self.nid)), 128)
        other = self.store.create('其他笔记', '其他内容')
        self.assertEqual(self.service.list_pending(other['id']), [])
        fresh = NoteProposalService(self.store)
        self.assertEqual(fresh.list_pending(self.nid), [])
        with self.assertRaises(KeyError):
            fresh.get(replacement['id'])

    def test_simultaneous_confirm_is_once(self):
        candidate = self.propose()
        barrier = threading.Barrier(3)
        results = []
        def confirm():
            barrier.wait()
            try:
                results.append(self.service.confirm(candidate['id'], 1))
            except Exception as error:
                results.append(error)
        workers = [threading.Thread(target=confirm) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(sum(isinstance(item, KeyError) for item in results), 1)
        self.assertEqual(self.store.get(self.nid)['version'], 2)
        self.assertEqual(len(self.store.history(self.nid)), 2)

    def test_confirmation_callback_runs_after_consumption_and_cannot_mutate_result(self):
        calls = []
        def callback(candidate, saved):
            self.assert_consumed(candidate['id'])
            calls.append((dict(candidate), dict(saved)))
            candidate['thread_id'] = 'forged'
            saved['body'] = 'forged'
            return ['已保存笔记，关联事项发生版本冲突。']
        self.service = NoteProposalService(self.store, clock=lambda: self.now, on_confirm=callback)
        candidate = self.propose(thread_id='original-thread')
        saved = self.service.confirm(candidate['id'], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]['thread_id'], 'original-thread')
        self.assertEqual(calls[0][1]['version'], 2)
        self.assertEqual(saved['body'], candidate['body'])
        self.assertEqual(saved['companion_warnings'], ['已保存笔记，关联事项发生版本冲突。'])
        self.assertEqual(self.store.get(self.nid)['body'], candidate['body'])

    def test_callback_failure_is_success_with_warning_and_never_retries(self):
        def fail(candidate, saved):
            raise RuntimeError('synthetic callback failure')
        self.service = NoteProposalService(self.store, clock=lambda: self.now, on_confirm=fail)
        candidate = self.propose(thread_id='original-thread')
        with patch.object(self.store, 'update', wraps=self.store.update) as update:
            saved = self.service.confirm(candidate['id'], 1)
            self.assertEqual(update.call_count, 1)
        self.assertEqual(saved['version'], 2)
        self.assertIn('已保存', saved['companion_warnings'][0])
        self.assertEqual(self.store.get_version(self.nid, 2)['body'], candidate['body'])
        self.assert_consumed(candidate['id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
