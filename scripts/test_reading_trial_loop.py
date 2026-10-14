"""Two-week reading trial: discussion, tradeoff, revision, reconnect, recall.

Temporary stores, real proposal/thread routes and an injected grounded model.
The model only restates what it was actually given, so a missing source fails
the test instead of being filled in. No bootstrap, network provider, user
database, microphone or paid call is used.
Run: .venv/Scripts/python.exe -B scripts/test_reading_trial_loop.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch.object(dotenv, 'load_dotenv', return_value=False):
    from server.assistant.note_proposals_api import create_note_proposals_router
    from server.assistant.service import AssistantService
    from server.dialogue import orchestrator as ordinary
    from server.interaction.proposals_api import create_proposals_router
    from server.interaction.threads_api import create_threads_router
    from server.memory.manager import MemoryManager
    from server.memory.store import MemoryStore


async def _audio(*args, **kwargs):
    return b'injected-audio'


class GroundedModel:
    """Replies only from the supplied context; never invents shared progress."""

    offline = False

    def __init__(self):
        self.prompts = []

    @staticmethod
    def _grounded(messages):
        joined = json.dumps(messages, ensure_ascii=False)
        facts = []
        if '两周阅读习惯' in joined:
            facts.append('已读到共同事项')
        if '阅读频率：待定' in joined:
            facts.append('已读到计划稿的待定项')
        if '晚饭后10分钟' in joined:
            facts.append('已读到已保存的取舍理由')
        if '新版计划正文' in joined:
            facts.append('已读到修订后的计划稿')
        if '不能读走的旧版正文' in joined:
            facts.append('错误读到了无关正文')
        return '；'.join(facts) if facts else '没有可用资料'

    async def complete_raw(self, messages, **kwargs):
        self.prompts.append(messages)
        return {'text': '[base]' + self._grounded(messages), 'finish_reason': 'stop', 'usage': {}}

    async def complete(self, messages, **kwargs):
        self.prompts.append(messages)
        if kwargs.get('json_mode'):
            return json.dumps({'action': 'final', 'text': self._grounded(messages)},
                              ensure_ascii=False)
        return self._grounded(messages)

    async def stream(self, messages, **kwargs):
        self.prompts.append(messages)
        yield '[base]' + self._grounded(messages)


class ReadingTrialLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-reading-trial-')
        self.addCleanup(self.teardown)
        self.directory = Path(self.temp.name)
        self.memory = MemoryStore(':memory:')
        self.manager = MemoryManager(self.memory)
        self.model = GroundedModel()
        self.service = AssistantService(self.directory, self.model, self.manager)
        self.thread = self.service.threads.create('两周阅读习惯')
        self.plan = self.service.notes.create('两周阅读计划',
            '第1天：选一本愿意继续的书。\n阅读频率：待定\n忙碌日最低行动：待定')
        self.service.participation.select(self.thread['id'])
        self.link_draft(self.plan, 1)
        self.other = self.service.threads.create('另一个无关事项')
        self.personal = self.service.notes.create('个人待办清单', '不能读走的旧版正文')
        self.service.notes.select(self.personal['id'], version=1,
            expected_revision=self.service.notes.selection()['revision'])
        self.orch = self.fresh_orchestrator()
        self.bundles = []

    def teardown(self):
        self.service.close()
        self.memory._conn.close()
        self.temp.cleanup()

    def fresh_orchestrator(self):
        orch = ordinary.Orchestrator()
        orch.context_broker = self.service.context_broker
        orch.activity_facade = self.service.activity
        return orch

    def relink_draft(self, note, version):
        """A matter keeps one draft; a new version needs an explicit update."""
        live = self.service.threads.get(self.thread['id'])
        draft = next(item for item in live['materials'] if item['purpose'] == 'draft')
        view = self.service.update_thread_material(self.thread['id'], draft['id'],
            expected_revision=live['revision'], version=version)
        self.thread = self.service.threads.get(self.thread['id'])
        self.assertEqual(view['revision'], self.thread['revision'])
        updated = next(item for item in view['items'] if item['id'] == draft['id'])
        self.assertEqual((updated['note_id'], updated['version'], updated['purpose']),
                         (note['id'], version, 'draft'))
        return updated

    def link_draft(self, note, version):
        live = self.service.threads.get(self.thread['id'])
        self.thread = self.service.threads.link_material(self.thread['id'],
            expected_revision=live['revision'], note_id=note['id'],
            version=version, purpose='draft')
        return next(item for item in self.thread['materials'] if item['purpose'] == 'draft')

    def client(self):
        app = FastAPI()
        app.include_router(create_threads_router(self.service))
        app.include_router(create_proposals_router(self.service))
        app.include_router(create_note_proposals_router(self.service))
        return TestClient(app)

    def usage_items(self, kind=None):
        usage = self.bundles[-1]['usage']
        return [item for item in usage['items'] if kind is None or item['kind'] == kind]

    def prompt_text(self):
        return json.dumps(self.model.prompts[-1], ensure_ascii=False)

    async def turn(self, request, session_id, *, streaming=False, history=()):
        original = self.orch._turn_context

        def capture(*args, **kwargs):
            bundle = original(*args, **kwargs)
            self.bundles.append(bundle)
            return bundle

        with (patch.object(self.orch, '_turn_context', side_effect=capture),
              patch.object(ordinary, 'store_recent', return_value=list(history)),
              patch.object(ordinary, 'store_message', side_effect=lambda *args: 1),
              patch.object(ordinary, 'llm', self.model),
              patch.object(ordinary, 'tts', SimpleNamespace(synthesize=_audio)),
              patch.object(ordinary, 'manager', self.manager),
              patch.object(self.orch, '_spawn_extraction')):
            if streaming:
                return [event async for event in self.orch.stream_respond(
                    session_id=session_id, user_text=request)]
            return await self.orch.respond(session_id=session_id, user_text=request)

    async def test_first_discussion_reads_matter_but_not_the_draft_body(self):
        reply = await self.turn('一起定一个两周阅读计划，先比较每天少读和隔天多读的取舍。', 'day1')
        # The matter and its records are available; the plan body is not read
        # until the user explicitly asks for it.
        self.assertIn('两周阅读习惯', self.prompt_text())
        self.assertNotIn('阅读频率：待定', self.prompt_text())
        self.assertEqual(reply.text, '已读到共同事项')
        thread = self.usage_items('thread')
        self.assertEqual([item['id'] for item in thread], [self.thread['id']])
        self.assertEqual(thread[0]['version'], self.thread['revision'])
        self.assertEqual(thread[0]['reason'], 'selected_matter')
        self.assertEqual(self.usage_items('note'), [])

    async def test_explicit_plan_request_supplies_the_fixed_draft_version(self):
        await self.turn('解释这份笔记，阅读频率怎么定', 'day1-plan')
        draft = self.usage_items('note')
        self.assertEqual([(item['id'], item['version'], item['purpose']) for item in draft],
                         [(self.plan['id'], 1, 'draft')])
        self.assertTrue(draft[0]['body_included'])
        self.assertEqual(draft[0]['reason'], 'explicit_note_request')
        self.assertIn('阅读频率：待定', self.prompt_text())
        # The unrelated personal note never stands in for the matter's draft.
        self.assertNotIn('不能读走的旧版正文', self.prompt_text())

    async def test_saved_tradeoff_recall_after_reconnect_keeps_reason_and_versions(self):
        self.service.thread_conversation.clock = lambda: 1000.0
        decision = await self.service.start(
            '记个决定：阅读频率用每天晚饭后10分钟，因为睡前精力更稳', 'day1')
        self.assertEqual(self.service.threads.get(self.thread['id'])['events'], [])
        with self.client() as client:
            response = client.post('/api/thread-proposals/' + decision['proposal']['id']
                                   + '/confirm',
                                   json={'expected_version': decision['proposal']['version']})
        self.assertEqual(response.status_code, 200, response.text)
        event = response.json()['events'][0]
        self.assertIn('睡前精力更稳', event['text'])
        # A revision saves a new version; the matter keeps the fixed old one.
        revised = self.service.notes.update(self.plan['id'], '两周阅读计划v2',
            '新版计划正文：晚饭后10分钟，忙碌日读两页。', 1)
        self.relink_draft(revised, 2)
        # A new connection restores the same matter without re-reading drafts.
        self.orch = self.fresh_orchestrator()
        resumed = await self.service.start('接着上次。', 'day7')
        self.assertIn('两周阅读习惯', resumed['text'])
        self.assertNotIn('新版计划正文', resumed['text'])
        reply = await self.turn('当时为什么选择这个阅读频率？', 'day7')
        # A recall question reads the saved reason, not the document body.
        self.assertIn('晚饭后10分钟', self.prompt_text())
        self.assertIn('睡前精力更稳', self.prompt_text())
        self.assertNotIn('新版计划正文', self.prompt_text())
        self.assertEqual(reply.text, '已读到共同事项；已读到已保存的取舍理由')
        events = self.usage_items('thread_event')
        self.assertIn((event['id'], event['version']),
                      [(item['id'], item.get('version')) for item in events])
        self.assertTrue(any(item['reason'] == 'matter_recall' for item in events))
        self.assertEqual(self.usage_items('note'), [])
        # Asking about the plan explicitly supplies the newly fixed version.
        await self.turn('解释这份笔记，现在的阅读安排是什么', 'day7-plan')
        draft = self.usage_items('note')
        self.assertEqual([(item['id'], item['version'], item['purpose']) for item in draft],
                         [(self.plan['id'], 2, 'draft')])
        self.assertIn('新版计划正文', self.prompt_text())
        self.assertNotIn('阅读频率：待定', self.prompt_text())
        # Old versions stay readable and unchanged for later review.
        self.assertEqual(self.service.notes.get_version(self.plan['id'], 1)['body'],
                         '第1天：选一本愿意继续的书。\n阅读频率：待定\n忙碌日最低行动：待定')
        self.assertEqual(self.service.notes.get_version(self.plan['id'], 2)['body'],
                         '新版计划正文：晚饭后10分钟，忙碌日读两页。')

    async def test_streamed_reconnect_carries_the_same_usage_trace(self):
        events = await self.turn('解释这份笔记', 'day2-stream', streaming=True)
        done = events[-1]
        self.assertEqual(done['type'], 'done')
        usage = done['context_usage']
        self.assertEqual(usage, self.bundles[-1]['usage'])
        draft = [item for item in usage['items'] if item['kind'] == 'note']
        self.assertEqual([(item['id'], item['version']) for item in draft],
                         [(self.plan['id'], 1)])
        self.assertTrue(all(item['body_included'] for item in draft))
        self.assertTrue(any(event['type'] == 'audio' for event in events))
        self.assertFalse(done['delivery']['playback_confirmed'])

    async def test_switching_to_a_matter_without_draft_never_reads_the_reading_plan(self):
        self.service.participation.select(self.other['id'])
        await self.turn('解释这份笔记', 'other-matter')
        self.assertNotIn('阅读频率：待定', self.prompt_text())
        self.assertNotIn('新版计划正文', self.prompt_text())
        self.assertEqual(self.usage_items('note'), [])
        context = self.service._run_context('解释这份笔记', 'other-matter')
        self.assertEqual(context['companion_thread_id'], self.other['id'])
        self.assertEqual(context['activity_data']['material_source']['status'], 'unbound')
        self.assertIsNone(context['activity_data']['note_data'])
        self.assertIn('还没有关联当前稿', context['activity_data']['resume']['text'])

    async def test_final_review_reports_only_what_the_user_actually_said(self):
        live = self.service.threads.get(self.thread['id'])
        self.service.threads.add_event(self.thread['id'], expected_revision=live['revision'],
            kind='progress', source='user_stated',
            text='第14天用户自述：只坚持了6天，晚饭后更容易开始。')
        reply = await self.turn('两周结束了，聊聊哪些规则该保留', 'day14')
        self.assertIn('只坚持了6天', self.prompt_text())
        self.assertNotIn('坚持了14天', self.prompt_text())
        self.assertEqual(reply.text, '已读到共同事项')


if __name__ == '__main__':
    unittest.main(verbosity=2)
