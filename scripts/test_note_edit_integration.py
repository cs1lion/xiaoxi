"""Chat edits -> fixed-base previews -> API confirmation -> saved continuation.

Only temporary databases, injected resolution and actual local API routes are
used. Application bootstrap, production data and model/audio providers stay out.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.assistant.api import create_router
from server.assistant.note_proposals_api import create_note_proposals_router
from server.assistant.service import AssistantService


class NoteEditIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-note-edit-integration-')
        self.directory = Path(self.temp.name)
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True))
        self.calls = []
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '第二段：需要补前后对比。', 'new_text': '第二段：先展示旧方案，再展示新方案。'}]}

        async def resolve(payload):
            self.calls.append(payload)
            return self.answer

        self.service.note_conversation.resolver = resolve
        self.note = self.service.notes.create('演示稿', '第一段：说明目标。\n第二段：需要补前后对比。\n结尾：请大家核对。')
        self.select(self.note)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def select(self, note):
        return self.service.notes.select(note['id'], version=note['version'],
            expected_revision=self.service.notes.selection()['revision'])

    def client(self):
        app = FastAPI()
        app.include_router(create_router(self.service))
        app.include_router(create_note_proposals_router(self.service))
        return TestClient(app)

    async def edit(self, text='修改这份笔记，把第二段写得具体一点', session_id='note-session'):
        self.assertTrue(self.service.should_route(text, session_id))
        return await self.service.start(text, session_id)

    def confirm(self, client, candidate):
        return client.post('/api/note-proposals/' + candidate['id'] + '/confirm',
                           json={'expected_version': candidate['version']})

    async def test_chat_preview_confirm_explicit_reselect_and_reconstructed_resume(self):
        original_selection = self.service.notes.selection()
        result = await self.edit()
        candidate = result['note_proposal']
        expected_body = self.note['body'].replace('第二段：需要补前后对比。', '第二段：先展示旧方案，再展示新方案。')
        self.assertEqual(candidate['note_id'], self.note['id'])
        self.assertEqual(candidate['base_note_version'], 1)
        self.assertEqual(candidate['original_body'], self.note['body'])
        self.assertEqual(candidate['body'], expected_body)
        self.assertEqual(candidate['status'], 'pending')
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)
        self.assertEqual(self.service.notes.selection(), original_selection)
        with self.client() as client:
            response = self.confirm(client, candidate)
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()
            self.assertEqual(saved['version'], 2)
            self.assertEqual(saved['body'], expected_body)
            self.assertEqual(client.get('/api/notes/' + self.note['id'], params={'version': 1}).json(), self.note)
            selection = client.get('/api/notes/selection').json()
            self.assertEqual(selection['selected_version'], 1)
            self.assertEqual(selection['current_version'], 2)
            self.assertTrue(selection['stale'])
            self.assertEqual(selection['revision'], original_selection['revision'])
            self.assertEqual(self.confirm(client, candidate).status_code, 404)
            chosen = client.post('/api/notes/selection', json={
                'note_id': self.note['id'], 'version': 2, 'expected_revision': selection['revision']})
            self.assertEqual(chosen.status_code, 200, chosen.text)
        self.service.close()
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True))
        result = await self.service.start('接着昨天', 'new-connection')
        self.assertEqual(result['activity']['activity_id'], original_selection['activity_id'])
        self.assertEqual(result['activity']['note']['selected_version'], 2)
        self.assertEqual(result['activity']['note']['status'], 'selected')
        self.assertEqual(result['activity']['object_refs'][0], {'type': 'note', 'id': self.note['id'], 'version': 2})
        self.assertEqual(self.service.notes.get_version(self.note['id'], 1), self.note)
        self.assertEqual(self.service.notes.get_version(self.note['id'], 2)['body'], expected_body)

    async def test_selection_changes_during_resolution_cannot_retarget_candidate(self):
        other = self.service.notes.create('另一份笔记', '别的正文不属于本轮。')
        original_thread = self.service.threads.create('原事项')
        other_thread = self.service.threads.create('另一个事项')
        self.service.threads.link_material(original_thread['id'], expected_revision=original_thread['revision'],
            note_id=self.note['id'], version=self.note['version'], purpose='draft')
        self.service.participation.select(original_thread['id'])

        async def resolve(payload):
            self.calls.append(payload)
            self.select(other)
            self.service.participation.select(other_thread['id'])
            return self.answer

        self.service.note_conversation.resolver = resolve
        candidate = (await self.edit())['note_proposal']
        self.assertEqual(candidate['note_id'], self.note['id'])
        self.assertEqual(candidate['base_note_version'], 1)
        self.assertEqual(candidate['original_body'], self.note['body'])
        self.assertEqual(candidate['thread_id'], original_thread['id'])
        with self.client() as client:
            response = self.confirm(client, candidate)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.service.notes.get(other['id']), other)
        self.assertEqual(self.service.notes.selection()['note_id'], other['id'])
        self.assertEqual(self.service.participation.selected_id, other_thread['id'])
        self.assertEqual(self.service.threads.get(other_thread['id'])['events'], [])
        receipts = self.service.threads.get(original_thread['id'])['events']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['source'], 'tool_verified')
        self.assertEqual(json.loads(receipts[0]['text'])['object_ref'],
                         {'type': 'note', 'id': self.note['id'], 'version': 2})

    async def test_note_clarification_same_session_keeps_fixed_ref_and_other_session_does_not_inherit(self):
        self.answer = {'action': 'clarify'}
        await self.edit(session_id='original-session')
        other = self.service.notes.create('后来选的笔记', '不是原来的第二段。')
        self.select(other)
        followup = '只修改第二段，其他保持原样'
        self.assertFalse(self.service.should_route('语气轻松一点', 'different-session'))
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '第二段：需要补前后对比。', 'new_text': '第二段：加上对比图。'}]}
        self.assertTrue(self.service.should_route(followup, 'original-session'))
        result = await self.service.start(followup, 'original-session')
        candidate = result['note_proposal']
        self.assertEqual(candidate['note_id'], self.note['id'])
        self.assertEqual(candidate['base_note_version'], 1)
        self.assertEqual(candidate['original_body'], self.note['body'])
        self.assertEqual(self.service.notes.selection()['note_id'], other['id'])
        self.assertEqual(self.service.notes.get(other['id']), other)
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)

    async def test_manual_save_during_resolution_has_no_candidate_and_keeps_new_saved_version(self):
        async def resolve(payload):
            self.calls.append(payload)
            self.service.notes.update(self.note['id'], '手动新版', '另一页真实保存的完整正文。', 1)
            return self.answer

        self.service.note_conversation.resolver = resolve
        result = await self.edit()
        self.assertNotIn('note_proposal', result)
        current = self.service.notes.get(self.note['id'])
        self.assertEqual(current['version'], 2)
        self.assertEqual(current['body'], '另一页真实保存的完整正文。')
        self.assertEqual(self.service.notes.get_version(self.note['id'], 1), self.note)
        self.assertEqual(self.service.notes.selection()['selected_version'], 1)

    async def test_delete_during_resolution_cannot_recreate_original_or_edit_new_selection(self):
        other = self.service.notes.create('备用笔记', '备用内容不应被改动。')

        async def resolve(payload):
            self.calls.append(payload)
            self.service.notes.delete(self.note['id'], 1)
            self.select(other)
            return self.answer

        self.service.note_conversation.resolver = resolve
        result = await self.edit()
        self.assertNotIn('note_proposal', result)
        with self.assertRaises(KeyError):
            self.service.notes.get(self.note['id'])
        self.assertEqual(self.service.notes.get(other['id']), other)
        self.assertEqual(self.service.notes.selection()['note_id'], other['id'])

    async def test_confirmation_after_manual_save_conflicts_without_rebase_or_selection_change(self):
        candidate = (await self.edit())['note_proposal']
        manual = self.service.notes.update(self.note['id'], '手动修改', '已在另页保存。', 1)
        with self.client() as client:
            self.assertEqual(self.confirm(client, candidate).status_code, 409)
            still_pending = client.get('/api/note-proposals/' + candidate['id'])
            self.assertEqual(still_pending.status_code, 200)
            self.assertEqual(still_pending.json(), candidate)
        self.assertEqual(self.service.notes.get(self.note['id']), manual)
        self.assertEqual(self.service.notes.selection()['selected_version'], 1)
        self.assertEqual(self.service.notes.get_version(self.note['id'], 1), self.note)

    async def test_api_delete_immediately_discards_all_cached_originals_and_candidate_reads_fail(self):
        first = (await self.edit())['note_proposal']
        second = (await self.edit('润色这份笔记的第二段', 'second-session'))['note_proposal']
        other = self.service.notes.create('未删除原稿', '无关正文。')
        unrelated = self.service.note_proposals.propose(other['id'], 1, other['title'],
            '无关正文的独立建议。', '修改另一份原稿')
        with self.client() as client:
            response = client.post('/api/notes/' + self.note['id'] + '/delete', json={'version': 1})
            self.assertEqual(response.status_code, 200, response.text)
            # Inspect before any candidate GET can perform lazy cleanup: API
            # deletion must immediately release every cached original body.
            self.assertNotIn(first['id'], self.service.note_proposals._pending)
            self.assertNotIn(second['id'], self.service.note_proposals._pending)
            self.assertIn(unrelated['id'], self.service.note_proposals._pending)
            for candidate in (first, second):
                self.assertEqual(client.get('/api/note-proposals/' + candidate['id']).status_code, 404)
                self.assertEqual(self.confirm(client, candidate).status_code, 404)
            self.assertEqual(client.get('/api/note-proposals/' + unrelated['id']).status_code, 200)
            self.assertEqual(client.get('/api/notes/' + self.note['id'], params={'version': 1}).status_code, 404)
        self.assertIsNone(self.service.notes.selection()['note_id'])
        self.assertEqual(self.service.notes.get(other['id']), other)

    async def test_historical_selection_refuses_edit_before_resolver(self):
        current = self.service.notes.update(self.note['id'], '当前版', '当前版应先明确关联。', 1)
        resolver = AsyncMock(side_effect=AssertionError('historical edit must stop before model'))
        self.service.note_conversation.resolver = resolver
        result = await self.edit()
        self.assertNotIn('note_proposal', result)
        resolver.assert_not_called()
        self.assertEqual(self.service.notes.get(self.note['id']), current)
        self.assertEqual(self.service.notes.selection()['selected_version'], 1)

    async def test_no_selection_does_not_guess_recent_note_or_read_a_body(self):
        self.service.notes.select(None, version=None,
            expected_revision=self.service.notes.selection()['revision'])
        resolver = AsyncMock(side_effect=AssertionError('unselected edit must stop before model'))
        self.service.note_conversation.resolver = resolver
        with patch.object(self.service.notes, 'get_version', side_effect=AssertionError('unselected body read')):
            result = await self.edit()
        self.assertNotIn('note_proposal', result)
        resolver.assert_not_called()
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)
        self.assertIsNone(self.service.notes.selection()['note_id'])

    async def test_memory_disabled_reads_no_selection_or_body_and_calls_no_resolver(self):
        self.service.memory_manager = SimpleNamespace(enabled=False)
        resolver = AsyncMock(side_effect=AssertionError('disabled edit must stop before model'))
        self.service.note_conversation.resolver = resolver
        with (patch.object(self.service.notes, 'selection', side_effect=AssertionError('disabled selection read')),
              patch.object(self.service.notes, 'get_version', side_effect=AssertionError('disabled body read')),
              patch.object(self.service.notes, 'get', side_effect=AssertionError('disabled note read'))):
            result = await self.edit()
        self.assertNotIn('note_proposal', result)
        resolver.assert_not_called()
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)

    async def test_visible_patch_preserves_entire_long_original_and_immutable_tail(self):
        body = '可见段：需要改进。\n' + '背景材料。' * 1200 + '\n尾部必须完整保留。'
        long_note = self.service.notes.create('长原稿', body)
        self.select(long_note)
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '可见段：需要改进。', 'new_text': '可见段：先列出三项具体改进。'}]}
        candidate = (await self.edit('修改这份笔记的可见段'))['note_proposal']
        expected = body.replace('可见段：需要改进。', '可见段：先列出三项具体改进。')
        self.assertEqual(candidate['original_body'], body)
        self.assertEqual(candidate['body'], expected)
        self.assertTrue(candidate['body'].endswith('尾部必须完整保留。'))
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('尾部必须完整保留。', str(self.calls[0]))
        with self.client() as client:
            response = self.confirm(client, candidate)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['body'], expected)
        self.assertEqual(self.service.notes.get_version(long_note['id'], 1)['body'], body)
        self.assertEqual(self.service.notes.selection()['selected_version'], 1)

    async def test_model_cannot_patch_text_outside_supplied_visible_excerpt(self):
        body = '前言。' * 1500 + '\n不可见尾段：需要改进。'
        long_note = self.service.notes.create('长原稿', body)
        self.select(long_note)
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '不可见尾段：需要改进。', 'new_text': '不可见尾段：已修改。'}]}
        result = await self.edit('修改这份笔记的尾段')
        self.assertNotIn('note_proposal', result)
        self.assertEqual(self.service.notes.get(long_note['id']), long_note)

    async def test_redacted_original_stops_before_model_even_with_secret_after_excerpt(self):
        body = '普通材料。' * 1000 + '\nAPI_KEY=private-fixture-value'
        credential_note = self.service.notes.create('凭据测试原稿', body)
        self.select(credential_note)
        resolver = AsyncMock(side_effect=AssertionError('redacted edit must stop before model'))
        self.service.note_conversation.resolver = resolver
        result = await self.edit('润色这份笔记的开头')
        self.assertNotIn('note_proposal', result)
        self.assertNotIn('private-fixture-value', str(result))
        resolver.assert_not_called()
        self.assertEqual(self.service.notes.get(credential_note['id']), credential_note)

    async def test_agent_loop_pending_clarification_keeps_priority_over_note_edit_request(self):
        resolver = AsyncMock(side_effect=AssertionError('pending action has priority over note resolution'))
        self.service.note_conversation.resolver = resolver
        decisions = iter([{'action': 'clarify', 'text': '任务标题是什么？'},
                          {'action': 'final', 'text': '已收到补充，尚未保存操作。'}])

        async def decide(payload):
            return next(decisions)

        self.service.loop.model = decide
        pending = await self.service.start('帮我创建任务', 'pending-session')
        self.assertEqual(pending['status'], 'waiting_input')
        result = await self.service.start('修改这份笔记的第二段', 'pending-session')
        self.assertEqual(result['run_id'], pending['run_id'])
        self.assertNotIn('note_proposal', result)
        resolver.assert_not_called()
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)
        self.assertEqual(self.service.store.list_tasks(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
