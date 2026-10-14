"""One complex matter across revisions and reconstructed chat services.

Temporary stores, real candidate confirmation routes, injected model boundaries.
No app bootstrap, network provider, user database or microphone is used.
"""
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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.assistant.service import AssistantService
from server.interaction.threads import RevisionConflict
from server.interaction.threads_api import create_threads_router
from server.interaction.proposals_api import create_proposals_router


class ThreadContinuityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-thread-continuity-')
        self.directory = Path(self.temp.name)
        self.manager = SimpleNamespace(enabled=True)
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True), None)
        # Deliberately use only the existing explicit activity switch in these
        # tests; constructing ContextBuilder would require a real retriever.
        self.service.memory_manager = self.manager
        self.thread = self.service.threads.create('两天的城市行程设计')
        self.tid = self.thread['id']
        self.service.participation.select(self.tid)
        self.note = self.service.notes.create('行程草案', '先查天气，再确定交通。')
        self.select_note(self.note)
        self.thread = self.service.threads.link_material(self.tid,
            expected_revision=self.thread['revision'], note_id=self.note['id'],
            version=self.note['version'], purpose='draft')
        self.material_id = self.thread['materials'][0]['id']

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def select_note(self, note):
        self.service.notes.select(note['id'], version=note['version'],
            expected_revision=self.service.notes.selection()['revision'])

    def confirm_http(self, candidate):
        app = FastAPI()
        app.include_router(create_threads_router(self.service))
        app.include_router(create_proposals_router(self.service))
        with TestClient(app) as client:
            response = client.post('/api/thread-proposals/' + candidate['id'] + '/confirm',
                                   json={'expected_version': candidate['version']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def update_draft_http(self, version):
        app = FastAPI()
        app.include_router(create_threads_router(self.service))
        live = self.service.threads.get(self.tid)
        with TestClient(app) as client:
            response = client.patch(f'/api/threads/{self.tid}/materials/{self.material_id}',
                json={'expected_revision': live['revision'], 'version': version})
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()
        self.assertEqual(view['thread_id'], self.tid)
        draft = next(item for item in view['items'] if item['id'] == self.material_id)
        self.assertEqual(draft['version'], version)
        self.assertEqual(draft['purpose'], 'draft')
        self.thread = self.service.threads.get(self.tid)
        self.assertEqual(self.thread['revision'], view['revision'])
        return view

    def reopen(self):
        self.service.close()
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True), None)
        self.service.memory_manager = self.manager

    async def test_complex_matter_keeps_reasons_versions_refs_across_new_session(self):
        original = '记个决定：周六用共享单车，因为想沿河停下来拍照'
        self.service.thread_conversation.clock = lambda: 1000.0
        result = await self.service.start(original, 'friday-chat')
        self.assertEqual(self.service.threads.get(self.tid)['events'], [])
        event = self.confirm_http(result['proposal'])['events'][0]
        # The second day's changed condition has its own source and note version.
        note2 = self.service.notes.update(self.note['id'], '雨天行程', '周六预报有雨。', 1)
        self.select_note(note2)
        self.update_draft_http(note2['version'])
        self.service.thread_conversation.clock = lambda: 91000.0
        revised_request = '把共享单车改成地铁，因为周六预报有雨，沿河拍照暂缓'
        async def resolve(payload):
            return {'action': 'revise_event', 'kind': 'decision',
                    'target_event_id': event['id'], 'text': '周六改用地铁，因预报有雨，沿河拍照暂缓'}
        self.service.thread_conversation.resolver = resolve
        revised = await self.service.start(revised_request, 'saturday-chat')
        self.assertEqual(revised['proposal']['original_text'], event['text'])
        self.confirm_http(revised['proposal'])
        # Many later events must not make the earlier decision disappear.
        for i in range(20):
            live = self.service.threads.get(self.tid)
            self.service.threads.add_event(self.tid, expected_revision=live['revision'],
                kind='progress', text=f'其他行程条目 {i}')
        self.reopen()
        self.assertEqual(self.service.participation.selected_id, self.tid)
        history = self.service.threads.event_history(self.tid, event['id'])
        self.assertEqual([v['version'] for v in history], [1, 2])
        self.assertEqual(history[1]['previous_version'], 1)
        self.assertEqual(history[0]['evidence']['request_text'], original)
        self.assertEqual(history[1]['evidence']['request_text'], revised_request)
        self.assertEqual(history[0]['evidence']['captured_at'], 1000)
        self.assertEqual(history[1]['evidence']['captured_at'], 91000)
        self.assertEqual([v['object_refs'][0]['version'] for v in history], [1, 2])
        self.assertEqual(self.service.notes.get_version(self.note['id'], 1)['body'], '先查天气，再确定交通。')
        query = '为什么之前用共享单车，后来改成地铁？'
        chat_view = self.service.context_broker.build(query, 'sunday-chat')['response_policy']['companion_thread']
        action_view = self.service._run_context(query, 'another-chat')['companion_thread']
        for view in (chat_view, action_view):
            self.assertIn(event['id'], [e['id'] for e in view['events']])
            rendered = json.dumps(view, ensure_ascii=False)
            self.assertIn('沿河停下来拍照', rendered)
            self.assertIn('周六预报有雨', rendered)
            self.assertNotIn('friday-chat', rendered)
            self.assertNotIn('saturday-chat', rendered)
            self.assertLessEqual(len(json.dumps(view, ensure_ascii=False, separators=(',', ':'))), 8000)

    async def test_reference_and_request_capture_survive_inflight_selection_changes(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def resolve(payload):
            started.set()
            await release.wait()
            return {'action': 'add_event', 'kind': 'decision', 'text': '先设计雨天备选路线'}
        self.service.thread_conversation.resolver = resolve
        self.service.thread_conversation.clock = lambda: 5000.0
        pending = asyncio.create_task(self.service.start('把刚才那个记下来', 'original-session'))
        await started.wait()
        other = self.service.threads.create('另一件事')
        self.service.participation.select(other['id'])
        other_note = self.service.notes.create('别的稿', '不属于原讨论。')
        self.select_note(other_note)
        self.service.thread_conversation.clock = lambda: 6000.0
        release.set()
        candidate = (await pending)['proposal']
        self.assertEqual(candidate['thread_id'], self.tid)
        self.assertEqual(candidate['object_refs'], [{'type': 'note', 'id': self.note['id'], 'version': 1}])
        changed = self.service.proposals.revise(candidate['id'], 1, '先查雨天室内路线')
        self.assertEqual(changed['evidence']['captured_at'], 5000)
        self.assertEqual(changed['source_text'], '把刚才那个记下来')
        saved = self.confirm_http(changed)
        self.assertEqual(saved['events'][0]['evidence']['session_id'], 'original-session')
        self.assertEqual(self.service.threads.get(other['id'])['events'], [])

    async def test_clarification_keeps_original_note_links(self):
        async def clarify(_): return {'action': 'clarify'}
        self.service.thread_conversation.resolver = clarify
        await self.service.start('把刚才那个记下来', 'same-session')
        other_note = self.service.notes.create('后来选择', '不能变成原澄清的依据。')
        self.select_note(other_note)
        async def resolve(_): return {'action': 'add_event', 'kind': 'decision', 'text': '先查天气'}
        self.service.thread_conversation.resolver = resolve
        result = await self.service.start('先查天气', 'same-session')
        saved = self.confirm_http(result['proposal'])
        self.assertEqual(saved['events'][0]['object_refs'][0]['id'], self.note['id'])

    async def test_disabled_strict_preview_never_reads_selected_note_or_history(self):
        self.manager.enabled = False
        with (patch.object(self.service.notes, 'selection', side_effect=AssertionError('disabled refs')),
              patch.object(self.service.threads, 'event_history', side_effect=AssertionError('disabled history'))):
            candidate = (await self.service.start('记个决定：先查天气', 'off'))['proposal']
            self.assertEqual(candidate['object_refs'], [])
            self.assertNotIn('companion_thread', self.service.context_broker.build('为什么改了决定', enabled=False)['response_policy'])
            self.assertEqual(self.service._read_thread(self.tid, '为什么改了决定'), {'status': 'disabled'})

    async def test_candidate_metadata_is_detached_and_unchanged_by_editing(self):
        candidate = (await self.service.start('记个决定：先查天气', 'origin'))['proposal']
        candidate['evidence']['request_text'] = '伪造原话'
        candidate['object_refs'][0]['version'] = 99
        original = self.service.proposals.get(candidate['id'])
        self.assertEqual(original['evidence']['request_text'], '记个决定：先查天气')
        self.assertEqual(original['object_refs'][0]['version'], 1)
        revised = self.service.proposals.revise(candidate['id'], 1, '天气未确认，不能认定取消')
        saved = self.confirm_http(revised)
        self.assertEqual(saved['events'][0]['text'], '天气未确认，不能认定取消')
        self.assertEqual(saved['events'][0]['evidence'], original['evidence'])

    async def test_read_tool_retains_complete_long_decision_and_final_qualification(self):
        text = '完整的路线讨论背景。' * 160 + '这只是预算测算，没有决定追加费用。'
        saved = self.service.threads.add_event(self.tid, expected_revision=self.thread['revision'],
            kind='decision', text=text)
        view = self.service._read_thread(self.tid, '预算')
        self.assertEqual(view['events'][0]['text'], text)
        self.assertFalse(view['grants_authorization'])
        self.assertEqual(view['ref']['version'], saved['revision'])

    async def test_conflict_and_rejection_leave_no_new_versions(self):
        candidate = (await self.service.start('记个决定：先查天气', 'a'))['proposal']
        self.service.threads.update(self.tid, expected_revision=self.thread['revision'], next_step='核对预算')
        with self.assertRaises(RevisionConflict):
            self.service.proposals.confirm(candidate['id'], 1)
        self.assertEqual(self.service.threads.get(self.tid)['events'], [])
        self.service.proposals.reject(candidate['id'], 1)
        self.reopen()
        self.assertEqual(self.service.threads.get(self.tid)['events'], [])

    async def test_saved_note_receipt_has_actual_fixed_structured_ref(self):
        source = self.service.activity.working_source()
        updated = self.service.notes.update(self.note['id'], '雨天方案', '已保存的新稿。', 1)
        candidate = {'id': 'synthetic-save', 'thread_id': self.tid, 'source_context': source,
                     **{key: source[key] for key in ('scope', 'thread_revision', 'material_id', 'purpose')}}
        warnings = self.service._note_saved_receipt(candidate, updated)
        self.assertEqual(warnings, [])
        event = self.service.threads.get(self.tid)['events'][0]
        self.assertEqual(event['source'], 'tool_verified')
        self.assertEqual(event['evidence']['state'], 'tool_verified')
        self.assertEqual(event['object_refs'], [{'type': 'note', 'id': self.note['id'], 'version': 2}])
        self.assertEqual(self.service.threads.get(self.tid)['status'], 'discussing')

    async def test_early_record_shown_for_clarification_remains_a_valid_ordinal_target(self):
        live = self.service.threads.add_event(self.tid, expected_revision=self.thread['revision'],
            kind='decision', text='共享单车预算不超过三百元')
        early = live['events'][0]
        for i in range(20):
            live = self.service.threads.add_event(self.tid, expected_revision=live['revision'],
                kind='progress', text=f'无关进度条目 {i}')
        async def clarify(_): return {'action': 'clarify'}
        self.service.thread_conversation.resolver = clarify
        clarification = await self.service.start('把共享单车预算改一下', 'ordinal-session')
        self.assertIn('1. 共享单车预算不超过三百元', clarification['text'])
        async def resolve(payload):
            self.assertEqual(payload['numbered_event_ids'][0], early['id'])
            self.assertIn(early['id'], [event['id'] for event in payload['thread']['events']])
            return {'action': 'revise_event', 'kind': 'decision',
                    'target_event_id': early['id'], 'text': '共享单车预算不超过两百元'}
        self.service.thread_conversation.resolver = resolve
        result = await self.service.start('第一个改成预算不超过两百元', 'ordinal-session')
        self.assertEqual(result['proposal']['target_event_id'], early['id'])
        self.confirm_http(result['proposal'])
        self.assertEqual(self.service.threads.event_history(self.tid, early['id'])[-1]['text'],
                         '共享单车预算不超过两百元')


if __name__ == '__main__':
    unittest.main()
