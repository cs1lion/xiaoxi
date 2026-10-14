"""Saved objects -> selected versions -> reconstructed service -> real routing.

Temporary stores and injected decisions only, never the application bootstrap.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.assistant.api import create_router
from server.assistant.service import AssistantService


class ActivityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-activity-integration-')
        self.directory = Path(self.temp.name)
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def select(self, note):
        return self.service.notes.select(note['id'], version=note['version'],
            expected_revision=self.service.notes.selection()['revision'])

    def model(self, decisions, captured):
        async def decide(payload):
            captured.append(payload)
            return decisions.pop(0)
        self.service.loop.model = decide

    def client(self):
        app = FastAPI()
        app.include_router(create_router(self.service))
        return TestClient(app)

    async def test_resume_after_service_reconstruction_uses_selected_objects_and_exact_versions(self):
        old = self.service.notes.create('演示稿第一版', '第二段：需要补前后对比。')
        selection = self.select(old)
        thread = self.service.threads.create('项目演示')
        self.service.participation.select(thread['id'])
        thread = self.service.threads.update(thread['id'], expected_revision=thread['revision'],
                                             next_step='补前后对比')
        thread = self.service.threads.link_material(thread['id'], expected_revision=thread['revision'],
            note_id=old['id'], version=1, purpose='draft')
        self.service.notes.update(old['id'], '演示稿新版', '这份新版不能悄悄替代旧稿。', 1)
        self.service.notes.create('无关的最新笔记', '不能按更新时间猜恢复对象。')
        self.service.store.create_task(title='更新得更晚的任务')
        self.service.close()
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True))
        async def forbidden(*args):
            raise AssertionError('Resume must not invent a model summary or execute tools')
        self.service.loop.model = forbidden
        self.service.thread_conversation.resolver = forbidden
        result = await self.service.start('接着昨天', 'new-connection')
        view = result['activity']
        self.assertEqual(view['activity_id'], selection['activity_id'])
        self.assertEqual(view['selection']['note_id'], old['id'])
        self.assertEqual(view['note']['selected_version'], 1)
        self.assertEqual(view['note']['current_version'], 2)
        self.assertEqual(view['thread']['id'], thread['id'])
        self.assertIn('演示稿第一版', result['text'])
        self.assertIn('补前后对比', result['text'])
        self.assertNotIn('无关的最新笔记', result['text'])
        self.assertNotIn('昨天完成', result['text'])
        self.assertEqual(view['object_refs'][0], {'type': 'note', 'id': old['id'], 'version': 1})
        self.assertEqual(self.service.notes.get_version(old['id'], 1)['body'], '第二段：需要补前后对比。')
        self.assertNotIn('第二段：', json.dumps(view, ensure_ascii=False))

    async def test_note_selection_api_cas_and_historical_read(self):
        with self.client() as client:
            note = client.post('/api/notes', json={'title': '周报', 'body': 'v1原文'}).json()
            selected = client.get('/api/notes/selection')
            self.assertEqual(selected.headers['cache-control'], 'no-store')
            payload = {'note_id': note['id'], 'version': 1, 'expected_revision': selected.json()['revision']}
            denied = client.post('/api/notes/selection', json=payload, headers={'Origin': 'https://other.example'})
            self.assertEqual(denied.status_code, 403)
            chosen = client.post('/api/notes/selection', json=payload)
            self.assertEqual(chosen.status_code, 200)
            self.assertEqual(client.post('/api/notes/selection', json=payload).status_code, 409)
            client.patch('/api/notes/' + note['id'], json={'title': '周报新版', 'body': 'v2原文', 'version': 1})
            view = client.get('/api/activity')
            self.assertEqual(view.headers['cache-control'], 'no-store')
            self.assertEqual(view.json()['note']['status'], 'stale')
            past = client.get('/api/notes/' + note['id'], params={'version': 1})
            self.assertEqual(past.json()['body'], 'v1原文')
            self.assertEqual(client.get('/api/notes/' + note['id']).json()['body'], 'v2原文')
            history = client.get('/api/notes/' + note['id'] + '/history').json()['items']
            self.assertEqual({row['version'] for row in history}, {1, 2})
            self.assertTrue(all('body' not in row for row in history))
            current_selection = client.get('/api/notes/selection').json()
            self.assertEqual(current_selection['selected_version'], 1)
            cleared = client.post('/api/notes/selection', json={'note_id': None, 'version': None,
                'expected_revision': current_selection['revision']})
            self.assertIsNone(cleared.json()['note_id'])
            self.assertIsNone(client.get('/api/activity').json()['note'])

    async def test_clarification_keeps_original_note_after_other_tab_selects_and_edits(self):
        note = self.service.notes.create('原稿', '原稿正文不能被另一页替换。')
        self.select(note)
        captured = []
        self.model([{'action': 'clarify', 'text': '整理成几段？'},
                    {'action': 'final', 'text': '已给出整理草稿，尚未保存。'}], captured)
        first = await self.service.start('帮我整理这份笔记', 'same-session')
        self.assertEqual(first['status'], 'waiting_input')
        self.service.notes.update(note['id'], '原稿新版', '新版正文仍不能进入旧run。', 1)
        other = self.service.notes.create('别的笔记', '别的正文绝不能迁入。')
        self.select(other)
        final = await self.service.start('两段', 'same-session')
        self.assertEqual(first['run_id'], final['run_id'])
        data = captured[1]['context']['activity_data']['note_data']
        self.assertEqual(data['ref'], {'type': 'note', 'id': note['id'], 'version': 1})
        self.assertEqual(data['body'], '原稿正文不能被另一页替换。')
        self.assertEqual(data['current_version'], 2)
        self.assertEqual(data['status'], 'stale')
        self.assertNotIn(other['id'], json.dumps(captured[1]['context'], ensure_ascii=False))
        self.assertEqual(self.service.notes.selection()['note_id'], other['id'])

    async def test_unbound_or_deleted_note_does_not_adopt_new_selection(self):
        captured = []
        self.model([{'action': 'clarify', 'text': '要几段？'}, {'action': 'final', 'text': '草稿。'}], captured)
        await self.service.start('帮我写周报', 's')
        other = self.service.notes.create('后来才选', '不属于原run。')
        self.select(other)
        await self.service.start('两段', 's')
        self.assertIsNone(captured[1]['context']['activity_data'])
        original = self.service._run_context('整理这份笔记', 'x')
        self.service.notes.delete(other['id'], other['version'])
        replacement = self.service.notes.create('替代对象', '不能自动采用。')
        self.select(replacement)
        continued = self.service._run_context('继续整理', 'x', original=original)
        self.assertIsNone(continued['activity_data']['note_data'])
        self.assertEqual(continued['activity_data']['note_status'], 'source_changed')
        self.assertNotIn(replacement['id'], json.dumps(continued, ensure_ascii=False))

    async def test_read_tools_are_bounded_and_never_save_or_execute_prior_receipts(self):
        note = self.service.notes.create('参考稿', '正文：只有草稿，还未发布。')
        self.select(note)
        captured = []
        self.model([{'action': 'tool_call', 'tool': 'note.search', 'arguments': {'query': '参考'}},
                    {'action': 'tool_call', 'tool': 'note.read', 'arguments': {'note_id': note['id'], 'version': 1}},
                    {'action': 'final', 'text': '已经读取草稿，没有保存改动。'}], captured)
        before = self.service.notes.get(note['id'])
        selected = self.service.notes.selection()
        result = await self.service.start('搜索笔记参考', 's')
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(all(receipt['status'] == 'succeeded' for receipt in result['receipts']))
        self.assertFalse(result['receipts'][1]['data']['grants_authorization'])
        self.assertEqual(self.service.notes.get(note['id']), before)
        self.assertEqual(self.service.notes.selection(), selected)
        receipt = self.service._read_receipt(result['run_id'])
        self.assertEqual(receipt['status'], 'completed')
        self.assertNotIn('context', receipt)
        self.assertNotIn('user_text', receipt)
        self.assertEqual(self.service.store.list_tasks(), [])

    async def test_disabled_auto_context_and_read_tools_do_not_read_activity_stores(self):
        self.service.memory_manager = SimpleNamespace(enabled=False)
        with (patch.object(self.service.notes, 'selection', side_effect=AssertionError('disabled read')),
             patch.object(self.service.notes, 'search', side_effect=AssertionError('disabled search')),
             patch.object(self.service.notes, 'get_version', side_effect=AssertionError('disabled body'))):
            view = await self.service.start('接着昨天', 's')
            self.assertEqual(view['activity']['object_refs'], [])
            context = self.service._run_context('帮我整理这份笔记', 's')
            self.assertIsNone(context['activity_data'])
            self.assertEqual(self.service._search_notes('secret')['status'], 'disabled')
            self.assertEqual(self.service._read_note('id', 1)['status'], 'disabled')

    async def test_public_read_results_bound_nested_data_and_redact_keyed_credentials(self):
        source = [{'tool': 'test', 'status': 'succeeded', 'data': {
            'password': 'do-not-expose-plain-value',
            'nested': [{'description': ('large\\escaped\n' * 1200)} for _ in range(12)],
        }} for _ in range(12)]
        public = self.service._safe_public(source)
        rendered = json.dumps(public, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        self.assertLessEqual(len(rendered), 8000)
        self.assertNotIn('do-not-expose-plain-value', rendered)
        self.assertIn('[REDACTED: sensitive credential]', rendered)
        self.assertEqual(source[0]['data']['password'], 'do-not-expose-plain-value')
        for value in (float('nan'), float('inf')):
            self.assertIsNone(self.service._safe_public(value))

    async def test_initial_activity_and_action_bind_one_thread_when_selection_changes_during_read(self):
        first = self.service.threads.create('最初事项')
        later = self.service.threads.create('另页后来事项')
        self.service.participation.select(first['id'])
        current = self.service.participation.current

        def switch_after_capture():
            captured = current()
            self.service.participation.select(later['id'])
            return captured

        # The real broker must not re-read a different selection; no artifact
        # request means no independent personal-note metadata gathering either.
        with (patch.object(self.service.notes, 'selection', side_effect=AssertionError('unrequested note read')),
              patch.object(self.service.participation, 'current', side_effect=switch_after_capture) as captured):
            context = self.service._run_context('帮我安排任务', 's')
        self.assertEqual(captured.call_count, 1)
        self.assertEqual(context['companion_thread_id'], first['id'])
        self.assertEqual(context['companion_thread']['id'], first['id'])
        self.assertIsNone(context['activity_data'])
        self.assertEqual(self.service.participation.selected_id, later['id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
