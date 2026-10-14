"""Run: python scripts/test_thread_proposals.py; temp DB only, no app imports."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import uuid


# Load a private package namespace, avoiding server's application bootstrap.
_ROOT = Path(__file__).resolve().parents[1] / 'server/interaction'
_PACKAGE = '_standalone_proposal_tests'
_package = types.ModuleType(_PACKAGE)
_package.__path__ = [str(_ROOT)]
sys.modules[_PACKAGE] = _package
for _name in ('threads', 'proposals'):
    _spec = importlib.util.spec_from_file_location(_PACKAGE + '.' + _name,
                                                 _ROOT / (_name + '.py'))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
ThreadStore = sys.modules[_PACKAGE + '.threads'].ThreadStore
RevisionConflict = sys.modules[_PACKAGE + '.threads'].RevisionConflict
ProposalService = sys.modules[_PACKAGE + '.proposals'].ProposalService


class ProposalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = ThreadStore(Path(temp.name) / 'proposals.sqlite')
        self.addCleanup(self.store.close)
        self.thread = self.store.create('Synthetic test thread')
        self.tid = self.thread['id']
        self.now = 1000.0
        self.service = ProposalService(self.store, clock=lambda: self.now)

    def proposal(self, utterance='记个决定：采用方案甲'):
        return self.service.propose(self.tid, utterance)

    def test_parser_commands_and_no_automatic_write(self):
        for command, kind in (('记个决定', 'decision'), ('记录决定', 'decision'),
                              ('记个问题', 'question'), ('记个进展', 'progress')):
            for colon in ('：', ':'):
                original = command + colon + ' 测试内容 '
                result = self.proposal(original)
                self.assertEqual(result['kind'], kind)
                self.assertEqual(result['text'], '测试内容')
                self.assertEqual(result['source_text'], original)
                self.assertEqual(result['expected_revision'], 1)
                self.assertEqual(result['version'], 1)
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(result['expires_at'], 1600)
                self.assertEqual(uuid.UUID(result['id']).version, 4)
        self.assertEqual(self.store.get(self.tid), self.thread)
        self.assertEqual(len(self.service.list_pending(self.tid)), 8)

    def test_parser_fails_closed(self):
        commands = ('', ' ', None, 123, '我们决定采用甲', '记个决定采用甲',
                    '记个决定：', '记个问题：   ', '不要记个决定：采用甲',
                    '别记录决定：采用甲', '不是要记个决定：采用甲',
                    '如果我说记个决定：采用甲', '假设记个决定：采用甲',
                    '请记个决定：采用甲', '“记个决定：采用甲”',
                    '"记个决定：采用甲"', '示例：记个决定：采用甲',
                    '记个决定：采用甲\n记个问题：为什么', '记个决定：采用甲\n',
                    '\n记个决定：采用甲', ' 记个决定：采用甲',
                    '记录问题：为什么', '记录进展：完成', '记个决定：空\x00值')
        for text in commands:
            with self.subTest(text=text), self.assertRaisesRegex(
                    ValueError, '^explicit_record_command_required$'):
                self.proposal(text)
        for separator in ('\r', '\v', '\f', '\x85', '\u2028', '\u2029'):
            with self.assertRaises(ValueError):
                self.proposal('记个决定：甲' + separator + '乙')
        self.assertEqual(self.service.list_pending(self.tid), [])
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_source_and_output_bounds(self):
        maximum = self.service.MAX_TEXT
        result = self.proposal('记个决定：' + '字' * maximum)
        self.assertEqual(len(result['text']), maximum)
        self.assertLessEqual(len(result['source_text']), self.service.MAX_SOURCE_TEXT)
        for text in ('记个决定：' + '字' * (maximum + 1),
                     '记个决定：' + ' ' * maximum + '字'):
            with self.assertRaises(ValueError):
                self.proposal(text)
        for text in ('', ' ', None, 5, '字' * (maximum + 1), '甲\n乙', '甲\x00乙'):
            with self.assertRaises(ValueError):
                self.service.revise(result['id'], 1, text)
        self.assertEqual(self.service.get(result['id']), result)
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_copies_and_revise_refresh_then_confirm(self):
        candidate = self.proposal()
        token = candidate['id']
        candidate['text'] = 'external mutation'
        fetched = self.service.get(token)
        fetched['version'] = 100
        listed = self.service.list_pending(self.tid)
        listed[0]['source_text'] = 'external mutation'
        self.store.update(self.tid, expected_revision=1, title='Changed')
        revised = self.service.revise(token, 1, '用户修订后的文字')
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['expected_revision'], 2)
        self.assertEqual(revised['source_text'], '记个决定：采用方案甲')
        self.assertEqual(revised['expires_at'], candidate['expires_at'])
        self.assertEqual(self.store.get(self.tid)['events'], [])
        confirmed = self.service.confirm(token, 2)
        self.assertEqual(confirmed['revision'], 3)
        event, = confirmed['events']
        self.assertEqual(event['text'], '用户修订后的文字')
        self.assertEqual(event['source'], 'user_stated')
        self.assertEqual(event['source_id'], 'proposal:' + token)
        self.assertEqual(event['dedupe_key'], event['source_id'])
        self.assert_consumed(token, 2)

    def assert_consumed(self, token, version=1):
        for action in (lambda: self.service.get(token),
                       lambda: self.service.confirm(token, version),
                       lambda: self.service.revise(token, version, 'new'),
                       lambda: self.service.reject(token, version)):
            with self.assertRaises(KeyError):
                action()

    def test_stale_thread_is_not_retried_or_consumed(self):
        candidate = self.proposal()
        self.store.update(self.tid, expected_revision=1, title='Concurrent edit')
        with patch.object(self.store, 'add_event', wraps=self.store.add_event) as add:
            with self.assertRaises(RevisionConflict):
                self.service.confirm(candidate['id'], 1)
            self.assertEqual(add.call_count, 1)
            self.assertEqual(add.call_args.kwargs['expected_revision'], 1)
        self.assertEqual(self.service.get(candidate['id']), candidate)
        self.assertEqual(self.store.get(self.tid)['events'], [])

    def test_versions_validate_before_mutating(self):
        candidate = self.proposal()
        token = candidate['id']
        for action in (lambda v: self.service.confirm(token, v),
                       lambda v: self.service.revise(token, v, 'new'),
                       lambda v: self.service.reject(token, v)):
            for version in (2, 99):
                with self.assertRaises(RevisionConflict):
                    action(version)
            for version in (True, False, 0, -1, 1.0, '1', None):
                with self.assertRaises(ValueError):
                    action(version)
        self.assertEqual(self.service.get(token), candidate)
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_rejection_forgets_sensitive_fields(self):
        candidate = self.proposal()
        result = self.service.reject(candidate['id'], 1)
        self.assertEqual(result['status'], 'rejected')
        self.assertTrue(result['rejected'])
        self.assertNotIn('text', result)
        self.assertNotIn('source_text', result)
        self.assert_consumed(candidate['id'])
        self.assertEqual(self.service.list_pending(self.tid), [])
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_expiry_exact_boundary_and_no_extension_on_revise(self):
        candidate = self.proposal()
        self.now = 1599.999
        revised = self.service.revise(candidate['id'], 1, 'Refreshed')
        self.assertEqual(revised['expires_at'], 1600)
        self.now = 1600
        self.assert_consumed(candidate['id'], 2)
        self.assertEqual(self.service.list_pending(self.tid), [])
        self.assertEqual(self.store.get(self.tid), self.thread)

    def test_capacity_only_evicts_expired(self):
        first = self.proposal()
        self.now += 1
        others = [self.proposal() for _ in range(127)]
        self.assertEqual(len({c['id'] for c in [first] + others}), 128)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.proposal()
        self.assertEqual(self.service.get(first['id']), first)
        self.now = first['expires_at']
        replacement = self.proposal()
        self.assert_consumed(first['id'])
        self.assertEqual(self.service.get(others[0]['id']), others[0])
        self.assertEqual(len(self.service.list_pending(self.tid)), 128)
        self.assertEqual(self.service.get(replacement['id']), replacement)

    def test_missing_and_deleted_threads_never_recreated(self):
        candidate = self.proposal()
        for action in (lambda: self.service.propose('missing', '记个决定：测试'),
                       lambda: self.service.list_pending('missing')):
            with self.assertRaises(KeyError):
                action()
        self.store.delete(self.tid, expected_revision=1)
        for action in (lambda: self.service.confirm(candidate['id'], 1),
                       lambda: self.service.revise(candidate['id'], 1, 'new'),
                       lambda: self.proposal(),
                       lambda: self.service.list_pending(self.tid)):
            with self.assertRaises(KeyError):
                action()
        self.assertEqual(self.store.list_threads(), [])
        self.assertEqual(self.service.discard_thread(self.tid), 1)
        self.assert_consumed(candidate['id'])
        self.assertEqual(self.service.discard_thread(self.tid), 0)

    def test_memory_is_local_and_lists_are_scoped(self):
        first = self.proposal()
        other = self.store.create('Other synthetic thread')
        second = self.service.propose(other['id'], '记个问题：为什么')
        self.assertEqual(self.service.list_pending(self.tid), [first])
        self.assertEqual(self.service.list_pending(other['id']), [second])
        fresh = ProposalService(self.store, clock=lambda: self.now)
        self.assertEqual(fresh.list_pending(self.tid), [])
        with self.assertRaises(KeyError):
            fresh.get(first['id'])
        self.assertEqual(self.service.discard_thread(self.tid), 1)
        self.assertEqual(self.service.get(second['id']), second)

    def test_concurrent_confirm_is_one_time(self):
        candidate = self.proposal()
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
        self.assertEqual(len(self.store.get(self.tid)['events']), 1)

    def change(self, operation, text='新的用户文字', **kwargs):
        return self.service.propose_change(
            self.tid, operation=operation, text=text,
            source_text=kwargs.pop('source_text', '第二个改成新的用户文字'), **kwargs)

    def add_user_event(self, text='原决定', kind='decision'):
        thread = self.store.get(self.tid)
        result = self.store.add_event(
            self.tid, expected_revision=thread['revision'], kind=kind, text=text)
        return result['events'][-1]

    def test_three_operations_preview_without_any_write(self):
        event = self.add_user_event()
        before = self.store.get(self.tid)
        with patch.object(self.store, 'add_event', side_effect=AssertionError('preview wrote')):
            with patch.object(self.store, 'revise_event', side_effect=AssertionError('preview wrote')):
                with patch.object(self.store, 'update', side_effect=AssertionError('preview wrote')):
                    added = self.change('add_event', kind='question', source_text='记个问题：如何验证')
                    revised = self.change('revise_event', target_event_id=event['id'])
                    step = self.change('next_step', text='先做一次彩排')
        self.assertEqual(self.store.get(self.tid), before)
        self.assertEqual(added['operation'], 'add_event')
        self.assertIsNone(added['target_event_id'])
        self.assertEqual(revised['operation'], 'revise_event')
        self.assertEqual(revised['original_text'], '原决定')
        self.assertEqual(revised['target_event_id'], event['id'])
        self.assertEqual(step['operation'], 'next_step')
        self.assertEqual(step['original_text'], '')
        self.assertIsNone(step['target_event_id'])
        for item in (added, revised, step):
            self.assertEqual(item['expected_revision'], before['revision'])
            self.assertEqual(item['version'], 1)
            self.assertEqual(item['status'], 'pending')

    def test_confirm_dispatches_each_operation_and_consumes(self):
        added = self.change('add_event', text='用户新增决定')
        first = self.service.confirm(added['id'], 1)
        event, = first['events']
        self.assertEqual(event['text'], '用户新增决定')
        self.assertEqual(event['source'], 'user_stated')
        self.assert_consumed(added['id'])
        revised = self.change('revise_event', text='用户改口后的决定', target_event_id=event['id'])
        second = self.service.confirm(revised['id'], 1)
        self.assertEqual(len(second['events']), 1)
        self.assertEqual(second['events'][0]['id'], event['id'])
        self.assertEqual(second['events'][0]['text'], '用户改口后的决定')
        self.assertEqual(second['events'][0]['source_id'], event['source_id'])
        self.assert_consumed(revised['id'])
        step = self.change('next_step', text='彩排之后检查字幕')
        final = self.service.confirm(step['id'], 1)
        self.assertEqual(final['next_step'], '彩排之后检查字幕')
        self.assertEqual(final['events'], second['events'])
        self.assertEqual(final['revision'], second['revision'] + 1)
        self.assert_consumed(step['id'])

    def test_exact_snapshot_revision_required_before_preview(self):
        self.store.update(self.tid, expected_revision=1, title='Concurrent title')
        for operation in ('add_event', 'next_step'):
            with self.subTest(operation=operation), self.assertRaises(RevisionConflict):
                self.change(operation, expected_revision=1)
            for invalid in (True, False, 0, -1, 1.0, '2'):
                with self.subTest(operation=operation, revision=invalid), self.assertRaises(ValueError):
                    self.change(operation, expected_revision=invalid)
        result = self.change('next_step', expected_revision=2)
        self.assertEqual(result['expected_revision'], 2)
        self.assertEqual(self.service.list_pending(self.tid), [result])

    def test_resolved_target_kind_source_and_identifiers_fail_closed(self):
        user_event = self.add_user_event()
        thread = self.store.get(self.tid)
        receipt_thread = self.store.add_event(
            self.tid, expected_revision=thread['revision'], kind='tool_receipt',
            text='合成测试执行回执', source='tool_verified', source_id='offline:test')
        receipt = receipt_thread['events'][-1]
        other = self.store.create('Other synthetic thread')
        other_event = self.store.add_event(
            other['id'], expected_revision=1, kind='decision', text='其他线程决定')['events'][0]
        for invalid in (None, '', ' ', 3, 'x\x00y', 'x' * (self.store.MAX_IDENTIFIER + 1)):
            with self.subTest(target=invalid), self.assertRaises(ValueError):
                self.change('revise_event', target_event_id=invalid)
        for missing in ('missing', other_event['id']):
            with self.subTest(target=missing), self.assertRaises(KeyError):
                self.change('revise_event', target_event_id=missing)
        for kind in ('tool_receipt', 'unknown', None, 4, True):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.change('add_event', kind=kind)
        with self.assertRaises(ValueError):
            self.change('revise_event', kind='question', target_event_id=user_event['id'])
        with self.assertRaisesRegex(ValueError, 'user_stated'):
            self.change('revise_event', target_event_id=receipt['id'])
        for operation in ('add_event', 'next_step'):
            with self.assertRaises(ValueError):
                self.change(operation, target_event_id=user_event['id'])
        for operation in ('unknown', None, 1):
            with self.assertRaises(ValueError):
                self.change(operation)
        with self.assertRaises(KeyError):
            self.service.propose_change('missing', operation='next_step', text='新下一步',
                                        source_text='下一步改成新下一步')
        self.assertEqual(self.service.list_pending(self.tid), [])
        self.assertEqual(self.store.get(self.tid), receipt_thread)

    def test_source_is_retained_bounded_nonempty_user_input(self):
        source = ' 第二个改成先彩排 '
        result = self.change('next_step', text='先彩排', source_text=source)
        self.assertEqual(result['source_text'], source)
        for invalid in ('', ' ', None, 12, '\x00', '甲\x00乙',
                        '字' * (self.service.MAX_SOURCE_TEXT + 1), '甲\n乙', '\x01', '\u200b'):
            with self.subTest(source=invalid), self.assertRaises(ValueError):
                self.change('next_step', source_text=invalid)
        revised = self.service.revise(result['id'], 1, '先彩排再检查')
        self.assertEqual(revised['source_text'], source)

    def test_revise_and_confirm_never_retarget_to_other_thread_or_event(self):
        first = self.add_user_event('第一决定')
        second = self.add_user_event('第二决定')
        preview = self.change('revise_event', target_event_id=second['id'])
        other = self.store.create('Later selected thread')
        self.store.add_event(other['id'], expected_revision=1, kind='decision', text='其他决定')
        preview['target_event_id'] = first['id']
        preview['thread_id'] = other['id']
        fresh = self.service.revise(preview['id'], 1, '第二决定改成彩排')
        self.assertEqual(fresh['thread_id'], self.tid)
        self.assertEqual(fresh['target_event_id'], second['id'])
        result = self.service.confirm(preview['id'], 2)
        self.assertEqual(result['events'][0]['text'], '第一决定')
        self.assertEqual(result['events'][1]['text'], '第二决定改成彩排')
        self.assertEqual(self.store.get(other['id'])['events'][0]['text'], '其他决定')

    def test_concurrent_modification_for_each_operation_is_not_retried(self):
        event = self.add_user_event()
        for operation, method in (('add_event', 'add_event'), ('revise_event', 'revise_event'),
                                  ('next_step', 'update')):
            kwargs = {'target_event_id': event['id']} if operation == 'revise_event' else {}
            candidate = self.change(operation, **kwargs)
            thread = self.store.get(self.tid)
            concurrent = self.store.update(self.tid, expected_revision=thread['revision'], title=operation)
            with patch.object(self.store, method, wraps=getattr(self.store, method)) as mutate:
                with self.assertRaises(RevisionConflict):
                    self.service.confirm(candidate['id'], 1)
                self.assertEqual(mutate.call_count, 1)
                self.assertEqual(mutate.call_args.kwargs['expected_revision'], candidate['expected_revision'])
            self.assertEqual(self.service.get(candidate['id']), candidate)
            self.assertEqual(self.store.get(self.tid), concurrent)
            refreshed = self.service.revise(candidate['id'], 1, candidate['text'])
            self.assertEqual(refreshed['expected_revision'], concurrent['revision'])
            self.assertEqual(refreshed['target_event_id'], candidate['target_event_id'])
            self.service.reject(candidate['id'], 2)

    def test_explicit_repreview_refreshes_original_text_for_same_target(self):
        event = self.add_user_event('最初内容')
        candidate = self.change('revise_event', text='最终内容', target_event_id=event['id'])
        changed = self.store.revise_event(self.tid, event['id'], expected_revision=2, text='并发修改内容')
        with self.assertRaises(RevisionConflict):
            self.service.confirm(candidate['id'], 1)
        revised = self.service.revise(candidate['id'], 1, '最终内容')
        self.assertEqual(revised['original_text'], '并发修改内容')
        self.assertEqual(revised['target_event_id'], event['id'])
        self.assertEqual(revised['expected_revision'], changed['revision'])
        self.assertEqual(revised['source_text'], candidate['source_text'])
        self.assertEqual(revised['expires_at'], candidate['expires_at'])
        self.service.confirm(candidate['id'], 2)
        step = self.change('next_step', text='演示前彩排')
        changed = self.store.update(self.tid, expected_revision=4, next_step='先检查字幕')
        revised = self.service.revise(step['id'], 1, '演示前彩排')
        self.assertEqual(revised['original_text'], '先检查字幕')
        self.assertEqual(revised['expected_revision'], changed['revision'])

    def test_modified_proposals_expire_and_deleted_threads_stay_deleted(self):
        event = self.add_user_event()
        candidates = [self.change('revise_event', target_event_id=event['id']),
                      self.change('next_step')]
        self.now = 1600
        for candidate in candidates:
            self.assert_consumed(candidate['id'])
        candidates = [self.change('revise_event', target_event_id=event['id']),
                      self.change('next_step')]
        self.store.delete(self.tid, expected_revision=2)
        for candidate in candidates:
            with self.assertRaises(KeyError):
                self.service.confirm(candidate['id'], 1)
            with self.assertRaises(KeyError):
                self.service.revise(candidate['id'], 1, '无法写入已删除线程')
        self.assertEqual(self.service.discard_thread(self.tid), 2)
        self.assertEqual(self.store.list_threads(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
