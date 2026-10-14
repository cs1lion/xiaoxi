"""Conversation contract tests with temporary stores and injected resolution.

These test grounding and write boundaries, not the real model's accuracy.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.service import AssistantService


class Offline:
    offline = True


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = AssistantService(Path(self.temp.name), Offline())
        self.c = self.service.thread_conversation
        self.thread = self.service.threads.create('项目演示')
        self.service.participation.select(self.thread['id'])
        self.calls = []
        self.answer = {'action': 'clarify'}

        async def resolve(payload):
            self.calls.append(payload)
            return self.answer
        self.c.resolver = resolve

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def add(self, text, kind='decision', **extra):
        self.thread = self.service.threads.add_event(self.thread['id'],
            expected_revision=self.thread['revision'], kind=kind, text=text, **extra)
        return self.thread['events'][-1]

    async def test_continue_grounded_without_model_or_writes(self):
        self.add('先保留文字入口')
        self.add('任务已创建', 'tool_receipt', source='tool_verified', source_id='run:1')
        self.thread = self.service.threads.update(self.thread['id'],
            expected_revision=self.thread['revision'], next_step='准备两分钟演示')
        result = await self.service.start('继续', 'a')
        self.assertIn('先保留文字入口', result['text'])
        self.assertIn('准备两分钟演示', result['text'])
        self.assertNotIn('全部完成', result['text'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.threads.get(self.thread['id']), self.thread)

    async def test_no_selection_shows_choices_without_guessing(self):
        other = self.service.threads.create('另一个项目')
        self.service.participation.select(None)
        result = await self.service.start('继续昨天那件事', 'a')
        self.assertEqual({t['id'] for t in result['companion']['choices']},
                         {self.thread['id'], other['id']})
        self.assertIsNone(self.service.participation.current())
        self.assertEqual(self.calls, [])

    async def test_natural_add_uses_bounded_same_session_history(self):
        self.answer = {'action': 'add_event', 'kind': 'decision', 'text': '演示时保留文字入口'}
        history = [{'role': 'assistant', 'content': '演示时保留文字入口'}] * 8
        result = await self.service.start('把刚才那个记下来', 'a', history=history)
        self.assertEqual(len(self.calls[0]['history']), 6)
        self.assertEqual(result['proposal']['source_text'], '把刚才那个记下来')
        self.assertEqual(self.service.threads.get(self.thread['id'])['events'], [])
        saved = self.service.proposals.confirm(result['proposal']['id'], 1)
        self.assertEqual(saved['events'][0]['text'], '演示时保留文字入口')
        self.assertEqual(saved['events'][0]['source'], 'user_stated')

    async def test_revise_exact_event_preserves_others(self):
        first = self.add('第一决定')
        second = self.add('第二决定')
        await self.service.start('继续', 'a')
        self.answer = {'action': 'revise_event', 'kind': 'decision',
                       'target_event_id': second['id'], 'text': '先彩排'}
        result = await self.service.start('第二个改成先彩排', 'a')
        self.assertEqual(result['proposal']['original_text'], '第二决定')
        self.assertEqual(self.service.threads.get(self.thread['id']), self.thread)
        saved = self.service.proposals.confirm(result['proposal']['id'], 1)
        self.assertEqual(saved['events'][0], first)
        self.assertEqual(saved['events'][1]['id'], second['id'])
        self.assertEqual(saved['events'][1]['text'], '先彩排')

    async def test_next_step_preview_and_confirm(self):
        self.answer = {'action': 'next_step', 'text': '先检查字幕'}
        result = await self.service.start('下一步改成先检查字幕', 'a')
        self.assertEqual(result['proposal']['operation'], 'next_step')
        self.assertEqual(result['proposal']['original_text'], '')
        self.assertEqual(self.service.threads.get(self.thread['id'])['next_step'], '')
        saved = self.service.proposals.confirm(result['proposal']['id'], 1)
        self.assertEqual(saved['next_step'], '先检查字幕')

    async def test_ambiguous_then_clarify_original_thread_even_if_selection_changes(self):
        self.add('第一决定')
        second = self.add('第二决定')
        result = await self.service.start('把刚才那个改一下', 'a')
        self.assertNotIn('proposal', result)
        self.assertIn('2. 第二决定', result['text'])
        other = self.service.threads.create('别的事项')
        self.service.participation.select(other['id'])
        self.answer = {'action': 'revise_event', 'kind': 'decision',
                       'target_event_id': second['id'], 'text': '先彩排'}
        self.assertTrue(self.service.should_route('第二个，改成先彩排', 'a'))
        result = await self.service.start('第二个，改成先彩排', 'a')
        self.assertEqual(result['proposal']['thread_id'], self.thread['id'])
        self.assertEqual(self.calls[-1]['thread']['id'], self.thread['id'])
        self.assertIn('2. 第二决定', self.calls[-1]['history'][-1]['content'])
        self.assertEqual(self.service.threads.get(other['id'])['events'], [])

    async def test_other_session_never_inherits_clarification(self):
        self.add('私有指代')
        await self.service.start('把刚才那个改一下', 'a')
        self.assertFalse(self.service.should_route('就是第二个', 'b'))
        self.assertTrue(self.service.should_route('就是第二个', 'a'))

    async def test_freeform_answer_continues_clarification_at_original_thread(self):
        await self.service.start('把刚才那个记下来', 'a')
        other = self.service.threads.create('另一个项目')
        self.service.participation.select(other['id'])
        self.answer = {'action': 'add_event', 'kind': 'decision', 'text': '先彩排再演示'}
        self.assertTrue(self.service.should_route('先彩排再演示', 'a'))
        result = await self.service.start('先彩排再演示', 'a')
        self.assertEqual(result['proposal']['thread_id'], self.thread['id'])

    async def test_numbered_display_mapping_and_unknown_order(self):
        for i in range(6):
            self.add(f'决定{i + 1}')
        self.answer = {'action': 'revise_event', 'kind': 'decision',
                       'target_event_id': self.thread['events'][1]['id'], 'text': '先彩排'}
        result = await self.service.start('第二个改成先彩排', 'unknown')
        self.assertNotIn('proposal', result)
        self.assertEqual(self.calls, [])
        continued = await self.service.start('继续', 'a')
        self.assertIn('2. 决定：决定4', continued['text'])
        result = await self.service.start('第二个改成先彩排', 'a')
        self.assertNotIn('proposal', result)  # model picked a different numbering
        await self.service.start('继续', 'a')
        self.answer['target_event_id'] = self.thread['events'][3]['id']
        result = await self.service.start('第二个改成先彩排', 'a')
        self.assertEqual(result['proposal']['target_event_id'], self.thread['events'][3]['id'])

    async def test_polite_numbered_edits_keep_original_display_and_validate_id(self):
        first = self.add('第一决定')
        second = self.add('第二决定')
        other = self.service.threads.create('另一个项目')
        for text in ['把第二个改成先彩排', '请把第二个改成先彩排', '帮我把第二个改成先彩排',
                     '不是那个，第二个改成先彩排']:
            self.service.participation.select(self.thread['id'])
            await self.service.start('继续', 'a')
            self.service.participation.select(other['id'])
            self.answer = {'action': 'revise_event', 'kind': 'decision',
                           'target_event_id': first['id'], 'text': '先彩排'}
            result = await self.service.start(text, 'a')
            self.assertNotIn('proposal', result, text)
            self.service.participation.select(self.thread['id'])
            await self.service.start('继续', 'a')
            self.service.participation.select(other['id'])
            self.answer['target_event_id'] = second['id']
            result = await self.service.start(text, 'a')
            self.assertEqual(result['proposal']['thread_id'], self.thread['id'], text)
            self.assertEqual(result['proposal']['target_event_id'], second['id'], text)

    async def test_candidate_conversational_edit_requires_live_version(self):
        result = await self.service.start('记个决定：保留文字入口', 'a')
        proposal = result['proposal']
        self.answer = {'action': 'revise_preview', 'proposal_id': proposal['id'], 'text': '只在演示保留'}
        revised = await self.service.start('改成只在演示保留', 'a')
        self.assertEqual(revised['proposal']['id'], proposal['id'])
        self.assertEqual(revised['proposal']['version'], 2)
        self.assertEqual(revised['proposal']['source_text'], '记个决定：保留文字入口')
        self.assertEqual(self.service.threads.get(self.thread['id'])['events'], [])
        self.service.proposals.revise(proposal['id'], 2, '另一次界面修改')
        result = await self.service.start('改成每天保留', 'a')
        self.assertNotIn('proposal', result)
        self.assertIn('已更新', result['text'])

    async def test_foreign_target_and_tool_receipt_fail_closed(self):
        event = self.add('执行失败', 'tool_receipt', source='tool_verified', source_id='run:1')
        for target in [event['id'], 'invented-id']:
            self.answer = {'action': 'revise_event', 'kind': 'decision',
                           'target_event_id': target, 'text': '执行成功'}
            result = await self.service.start('把这条改成执行成功', 'a')
            self.assertNotIn('proposal', result)
        self.assertEqual(self.service.threads.get(self.thread['id']), self.thread)

    async def test_bad_schema_types_and_oversize_fail_closed(self):
        for value in [[], {'action': 'tool_call'}, {'action': 'next_step', 'text': 'x', 'thread_id': 'forged'},
                      {'action': 'add_event', 'kind': [], 'text': 'x'},
                      {'action': 'next_step', 'text': 'x' * 4001},
                      {'action': 'next_step', 'text': 'a\nb'}]:
            self.answer = value
            result = await self.service.start('下一步改成新的内容', 'a')
            self.assertNotIn('proposal', result)
        self.assertEqual(self.service.threads.get(self.thread['id']), self.thread)

    async def test_selection_change_during_resolution_keeps_snapshot(self):
        other = self.service.threads.create('另一个项目')
        async def resolve(_):
            self.service.participation.select(other['id'])
            return {'action': 'next_step', 'text': '先检查字幕'}
        self.c.resolver = resolve
        result = await self.service.start('下一步改成先检查字幕', 'a')
        self.assertEqual(result['proposal']['thread_id'], self.thread['id'])

    async def test_late_resolution_never_overwrites_newer_state_or_creates_preview(self):
        other = self.service.threads.create('另一个项目')
        for value in [{'action': 'next_step', 'text': '旧结果'}, {'action': 'ignore'}, {'action': 'clarify'}]:
            started, finish = asyncio.Event(), asyncio.Event()
            async def delayed(_):
                started.set()
                await finish.wait()
                return value
            self.c.resolver = delayed
            self.service.participation.select(self.thread['id'])
            pending = asyncio.create_task(self.service.start('下一步改成旧结果', 'a'))
            await started.wait()
            self.service.participation.select(other['id'])
            await self.service.start('继续', 'a')
            finish.set()
            result = await pending
            self.assertEqual(result['status'], 'superseded')
            self.assertEqual(self.c._state('a')['thread']['id'], other['id'])
            self.assertEqual(self.service.proposals.list_pending(self.thread['id']), [])

    async def test_provider_swallowing_cancellation_cannot_leave_preview(self):
        started = asyncio.Event()
        async def delayed(_):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {'action': 'next_step', 'text': '旧结果'}
        self.c.resolver = delayed
        pending = asyncio.create_task(self.service.start('下一步改成旧结果', 'a'))
        await started.wait()
        pending.cancel()
        result = await pending
        self.assertEqual(result['status'], 'superseded')
        self.assertEqual(self.service.proposals.list_pending(self.thread['id']), [])

    async def test_edit_or_delete_during_resolution_does_not_refresh(self):
        other = self.service.threads.create('另一个项目')
        async def edit(_):
            self.service.participation.select(other['id'])
            self.service.threads.update(self.thread['id'], expected_revision=1, next_step='并发修改')
            return {'action': 'next_step', 'text': '先彩排'}
        self.c.resolver = edit
        result = await self.service.start('下一步改成先彩排', 'a')
        self.assertNotIn('proposal', result)
        self.assertEqual(result['companion']['thread']['id'], self.thread['id'])
        self.assertEqual(self.service.threads.get(self.thread['id'])['next_step'], '并发修改')
        self.service.participation.select(self.thread['id'])
        async def delete(_):
            self.service.threads.delete(self.thread['id'], expected_revision=2)
            return {'action': 'next_step', 'text': '先彩排'}
        self.c.resolver = delete
        result = await self.service.start('下一步改成先彩排', 'a')
        self.assertNotIn('proposal', result)
        self.assertIsNone(result['companion']['thread'])
        self.assertEqual(len(self.service.threads.list_threads()), 1)

    async def test_memory_off_and_offline_do_not_call_resolver(self):
        result = await self.c.handle('把刚才那个记下来', 'a', history=[{'role': 'user', 'content': 'secret'}], enabled=False)
        self.assertEqual(self.calls, [])
        self.assertNotIn('proposal', result)
        self.c.resolver = self.service._resolve_thread
        result = await self.service.start('把刚才那个记下来', 'a')
        self.assertNotIn('proposal', result)
        result = await self.c.handle('记个决定：直接命令', enabled=False)
        self.assertIn('proposal', result)

    async def test_model_exception_and_malformed_json_are_clarification(self):
        class Broken:
            offline = False
            async def complete(self, *args, **kwargs):
                return '```json\n{}\n```'
        self.service.llm = Broken()
        self.c.resolver = self.service._resolve_thread
        result = await self.service.start('把刚才那个记下来', 'a')
        self.assertNotIn('proposal', result)

    async def test_expired_focus_and_capacity(self):
        now = [100.0]
        self.c.clock = lambda: now[0]
        for i in range(70):
            self.c._remember(str(i), self.thread, [])
        self.assertEqual(len(self.c._sessions), 64)
        self.assertIsNone(self.c._state('0'))
        now[0] += 601
        self.assertFalse(self.service.should_route('就是第二个', '69'))

    def test_regular_chat_negation_quotes_hypotheticals_do_not_route(self):
        for text in ['今天好累', '自然就ok', '我不想做这件事', '下午三点',
                     '不要把刚才那个记下来', '他说“把刚才那个记下来”',
                     '假设把刚才那个记下来', '扮演一个说“下一步改成吃饭”的人']:
            self.assertFalse(self.service.should_route(text, 'a'), text)

    async def test_old_preview_does_not_intercept_chat_and_ignore_passes_through(self):
        await self.service.start('记个决定：保留文字入口', 'a')
        for text in ['就是今天有点累', '刚才挺好玩的']:
            self.assertFalse(self.service.should_route(text, 'a'))
        await self.service.start('把刚才那个记下来', 'a')
        self.answer = {'action': 'ignore'}
        result = await self.service.start('今天有点累，先不记了', 'a')
        self.assertEqual(result['status'], 'not_applicable')
        self.assertFalse(self.service.should_route('你好', 'a'))

    async def test_strict_format_compatibility_and_no_implied_confirmation(self):
        result = await self.service.start('记个决定，把刚才那个记下来', 'a')
        self.assertNotIn('proposal', result)
        result = await self.service.start('记个决定：保留文字入口', 'a')
        self.assertFalse(self.service.should_route('确认', 'a'))
        self.assertEqual(self.service.threads.get(self.thread['id'])['events'], [])
        self.assertEqual(self.calls, [])

    async def test_pending_action_clarification_takes_priority(self):
        decisions = [{'action': 'clarify', 'text': '几点提醒？'}, {'action': 'final', 'text': '这次先不设置。'}]
        async def model(_):
            return decisions.pop(0)
        self.service.loop.model = model
        result = await self.service.start('提醒我彩排', 'a')
        self.assertEqual(result['status'], 'waiting_input')
        result = await self.service.start('继续', 'a')
        self.assertNotIn('companion', result)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
