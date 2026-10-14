"""Grounded editing contracts with temporary notes and injected model output."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.service import AssistantService


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = AssistantService(Path(self.temp.name), SimpleNamespace(offline=True))
        self.c = self.s.note_conversation
        self.note = self.s.notes.create('演示稿', '第一段：说明目标。\n第二段：补充对比。\n第三段：总结。')
        self.s.notes.select(self.note['id'], version=1, expected_revision=1)
        self.answer = {'action': 'edit', 'replacements': [{'old_text': '第二段：补充对比。', 'new_text': '第二段：先展示前后对比。'}]}
        self.calls = []
        async def resolve(payload):
            self.calls.append(payload)
            return self.answer
        self.c.resolver = resolve

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    async def edit(self, text='修改这份笔记，把第二段写得自然一点', **kwargs):
        return await self.s.start(text, 'a', **kwargs)

    async def test_multiple_nonoverlapping_patches_preserve_surrounding_text(self):
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '第二段：补充对比。', 'new_text': '第二段：先看前后对比。'},
            {'old_text': '第一段：说明目标。', 'new_text': '第一段：说清目标。'}]}
        result = await self.edit()
        self.assertEqual(result['note_proposal']['body'], '第一段：说清目标。\n第二段：先看前后对比。\n第三段：总结。')
        self.assertEqual(self.s.notes.get(self.note['id']), self.note)

    async def test_strict_literal_replacement_does_not_need_model(self):
        result = await self.edit('笔记替换：「补充对比」→「展示前后对比」')
        self.assertEqual(self.calls, [])
        self.assertIn('展示前后对比', result['note_proposal']['body'])
        self.assertEqual(result['note_proposal']['source_text'], '笔记替换：「补充对比」→「展示前后对比」')

    async def test_rename_preserves_full_body(self):
        self.answer = {'action': 'rename', 'title': '演示稿：新版标题'}
        result = await self.edit('把笔记标题改成演示稿：新版标题')
        self.assertEqual(result['note_proposal']['body'], self.note['body'])
        self.assertEqual(result['note_proposal']['title'], '演示稿：新版标题')

    async def test_bad_json_shape_and_types_never_produce_preview(self):
        for value in [None, [], {'action': 'tool_call'}, {'action': 'edit', 'body': '伪造整稿'},
                      {'action': 'edit', 'replacements': [], 'note_id': 'other'},
                      {'action': 'rename', 'title': True}, {'action': 'rename', 'title': 'x' * 161},
                      {'action': 'edit', 'replacements': [{'old_text': None, 'new_text': 'new'}]},
                      {'action': 'edit', 'replacements': [{'old_text': '补充对比', 'new_text': '\x00'}]}]:
            self.answer = value
            result = await self.edit()
            self.assertNotIn('note_proposal', result)
        self.assertEqual(self.s.notes.get(self.note['id']), self.note)

    async def test_overlaps_and_duplicates_are_unresolved(self):
        for patches in [[{'old_text': '第二段：补充对比。', 'new_text': '改写'},
                         {'old_text': '补充对比', 'new_text': '另一改写'}],
                        [{'old_text': '补充对比', 'new_text': '改写'}] * 2]:
            self.answer = {'action': 'edit', 'replacements': patches}
            self.assertNotIn('note_proposal', await self.edit())

    async def test_duplicate_match_even_beyond_visible_tail_is_unresolved(self):
        body = '重复短句\n' + 'x' * 4000 + '\n重复短句'
        self.note = self.s.notes.update(self.note['id'], self.note['title'], body, 1)
        self.s.notes.select(self.note['id'], version=2, expected_revision=2)
        self.answer = {'action': 'edit', 'replacements': [{'old_text': '重复短句', 'new_text': '改写'}]}
        result = await self.edit()
        self.assertNotIn('note_proposal', result)
        self.assertEqual(self.s.notes.get(self.note['id'])['body'], body)

    async def test_overlapping_substring_match_is_ambiguous(self):
        note = self.s.notes.update(self.note['id'], self.note['title'], 'ababab', 1)
        self.s.notes.select(note['id'], version=2, expected_revision=2)
        self.answer = {'action': 'edit', 'replacements': [{'old_text': 'abab', 'new_text': 'X'}]}
        self.assertNotIn('note_proposal', await self.edit())
        self.assertEqual(self.s.notes.get(note['id'])['body'], 'ababab')

    async def test_task_word_in_clarification_keeps_note_target(self):
        self.answer = {'action': 'clarify'}
        await self.edit()
        self.answer = {'action': 'edit', 'replacements': [{'old_text': '第二段：补充对比。', 'new_text': '第二段：写清任务说明。'}]}
        text = '把任务介绍那段改自然一点'
        self.assertTrue(self.c.should_route(text, 'a'))
        result = await self.s.start(text, 'a')
        self.assertEqual(result['note_proposal']['note_id'], self.note['id'])
        self.assertEqual(self.s.store.list_tasks(), [])

    async def test_explicit_new_task_request_escapes_old_note_question(self):
        self.answer = {'action': 'clarify'}
        await self.edit()
        text = '帮我创建一个任务'
        self.assertFalse(self.c.should_route(text, 'a'))
        await self.s.start(text, 'a')
        self.assertIsNone(self.c._state('a'))

    async def test_noop_and_excessive_result_are_unresolved(self):
        for value in [{'action': 'rename', 'title': self.note['title']},
                      {'action': 'edit', 'replacements': [{'old_text': '补充对比', 'new_text': '补充对比'}]},
                      {'action': 'edit', 'replacements': [{'old_text': '补充对比', 'new_text': 'x' * 12001}]}]:
            self.answer = value
            self.assertNotIn('note_proposal', await self.edit())

    async def test_history_is_bounded_redacted_and_does_not_select_target(self):
        history = [{'role': 'assistant', 'content': 'x' * 1000}] * 7
        history.append({'role': 'user', 'content': 'api_key=private-example'})
        result = await self.edit(history=history)
        self.assertEqual(len(self.calls[-1]['history']), 6)
        self.assertLessEqual(len(self.calls[-1]['history'][0]['content']), 800)
        self.assertNotIn('private-example', json.dumps(self.calls[-1], ensure_ascii=False))
        self.assertEqual(result['note_proposal']['note_id'], self.note['id'])

    async def test_ignore_clears_clarification_and_returns_to_chat(self):
        self.answer = {'action': 'clarify'}
        await self.edit()
        self.answer = {'action': 'ignore'}
        result = await self.s.start('今天不想写，随便聊聊吧', 'a')
        self.assertEqual(result['status'], 'not_applicable')
        self.assertFalse(self.c.should_route('今天好累', 'a'))

    async def test_cancel_and_explicit_activity_change_drop_old_question(self):
        self.answer = {'action': 'clarify'}
        await self.edit()
        result = await self.s.start('算了', 'a')
        self.assertNotIn('note_proposal', result)
        self.assertIsNone(self.c._state('a'))
        await self.edit()
        await self.s.start('继续这件事', 'a')
        self.assertIsNone(self.c._state('a'))

    async def test_old_delayed_request_never_replaces_new_clarification(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(_):
            started.set()
            await release.wait()
            return self.answer
        self.c.resolver = delayed
        old = asyncio.create_task(self.edit())
        await started.wait()
        async def clarify(_):
            return {'action': 'clarify'}
        self.c.resolver = clarify
        await self.edit('修改这份笔记，哪里需要补充？')
        release.set()
        result = await old
        self.assertEqual(result['status'], 'superseded')
        self.assertIsNotNone(self.c._state('a'))
        self.assertEqual(self.s.note_proposals.list_pending(self.note['id']), [])

    async def test_new_normal_request_and_swallowed_cancel_fence_preview(self):
        started = asyncio.Event()
        async def delayed(_):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return self.answer
        self.c.resolver = delayed
        old = asyncio.create_task(self.edit())
        await started.wait()
        old.cancel()
        self.assertEqual((await old)['status'], 'superseded')
        self.assertEqual(self.s.note_proposals.list_pending(self.note['id']), [])
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed_again(_):
            started.set()
            await release.wait()
            return self.answer
        self.c.resolver = delayed_again
        old = asyncio.create_task(self.edit())
        await started.wait()
        await self.s.start('继续这件事', 'a')
        release.set()
        self.assertEqual((await old)['status'], 'superseded')
        self.assertEqual(self.s.note_proposals.list_pending(self.note['id']), [])

    async def test_offline_unknown_reference_does_not_fake_edit(self):
        self.c.resolver = self.s._resolve_note_edit
        result = await self.edit()
        self.assertNotIn('note_proposal', result)

    async def test_model_malformed_json_and_failure_are_clarification(self):
        class Invalid:
            offline = False
            async def complete(self, *args, **kwargs):
                return '```json\n{}\n```'
        self.s.llm = Invalid()
        self.c.resolver = self.s._resolve_note_edit
        self.assertNotIn('note_proposal', await self.edit())

    async def test_session_bounds_and_ttl(self):
        now = [100.0]
        self.c.clock = lambda: now[0]
        self.answer = {'action': 'clarify'}
        for i in range(65):
            await self.s.start('修改这份笔记，在哪里改？', str(i))
        self.assertEqual(len(self.c._sessions), 64)
        self.assertEqual(len(self.c._requests), 64)
        self.assertIsNone(self.c._state('0'))
        now[0] += 601
        self.assertFalse(self.c.should_route('今天好累', '64'))

    async def test_memory_off_route_and_handle_read_no_selection(self):
        self.s.memory_manager = SimpleNamespace(enabled=False)
        with patch.object(self.s.notes, 'selection', side_effect=AssertionError('memory off read')):
            self.assertTrue(self.s.should_route('把第二段改自然一点', 'a'))
            self.assertNotIn('note_proposal', await self.s.start('把第二段改自然一点', 'a'))
        self.assertEqual(self.calls, [])

    def test_reading_and_other_chat_keep_existing_route(self):
        for text in ['帮我整理这份笔记', '总结这份笔记', '解释笔记里的这句', '看看这份笔记',
                     '今天好累', '笔记软件有什么好用的', '不要修改这份笔记',
                     '他说“修改这份笔记”', '假设修改这份笔记', '扮演一个说修改这份笔记的人']:
            self.assertFalse(self.c.should_route(text, 'a'), text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
