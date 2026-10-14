"""Final prompt budget boundaries with injected providers and temporary stores.

Exercise the ordinary streaming/nonstreaming assembly and the assistant's real
decision adapter. This checks admission and trace accuracy, not model quality.
No dotenv files, user databases, microphones or paid providers are accessed.
Run: .venv/Scripts/python.exe -B scripts/test_context_budget_integration.py
"""
from __future__ import annotations

from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
with patch('dotenv.load_dotenv', return_value=False):
    from server.assistant.service import AssistantService
    from server.memory.manager import MemoryManager
    from server.memory.store import MemoryStore
    from server.speech.experience import ExperienceSession
    ordinary = importlib.import_module('server.dialogue.orchestrator')


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def memory_rows(data):
    memories = data.get('long_term_memory') or {}
    return [row for source in ('stated', 'inferred', 'uncertain')
            for row in memories.get(source, [])]


class CaptureLLM:
    offline = False

    def __init__(self):
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append((deepcopy(messages), dict(kwargs)))
        if kwargs.get('json_mode'):
            return '{"action":"final","text":"离线接缝验证完成。"}'
        return 'This is an injected reply.'

    async def complete_raw(self, messages, **kwargs):
        self.calls.append((deepcopy(messages), dict(kwargs)))
        return {'text': '[base]先一起权衡，再调整阅读计划。',
                'finish_reason': 'stop', 'usage': {}}

    async def stream(self, messages, **kwargs):
        self.calls.append((deepcopy(messages), dict(kwargs)))
        yield '[base]先一起权衡，再调整阅读计划。'


class ContextBudgetIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-context-budget-')
        self.memory = MemoryStore(':memory:')
        self.manager = MemoryManager(self.memory)
        self.model = CaptureLLM()
        self.service = AssistantService(Path(self.temp.name), self.model, self.manager)
        self.orch = ordinary.Orchestrator()
        self.orch.context_broker = self.service.context_broker
        self.orch.activity_facade = self.service.activity
        self.bundles = []
        self.message_writes = []
        self.hits = []
        self.note_ids = set()
        self.raw_markers = []
        self.seed()
        self.manager.retriever.cfg = dict(self.manager.retriever.cfg)
        self.manager.retriever.cfg['max_inject_chars'] = 12000

        async def audio(*args, **kwargs):
            return b'injected-audio'

        for patcher in (
            patch.object(ordinary, 'manager', self.manager),
            patch.object(ordinary, 'llm', self.model),
            patch.object(ordinary, 'tts', SimpleNamespace(synthesize=audio)),
            patch.object(ordinary, 'store_message',
                         side_effect=lambda *args: self.message_writes.append(args) or 1),
            patch.object(self.orch, '_spawn_extraction'),
            patch.object(self.manager.retriever, 'search',
                         side_effect=lambda *args, **kwargs: self.hits[:kwargs.get('top_k', len(self.hits))]),
            patch('httpx.AsyncClient.send', side_effect=AssertionError('No provider calls')),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.service.close()
        self.memory._conn.close()
        self.temp.cleanup()

    def seed(self):
        self.thread = self.service.threads.create('两周阅读取舍试用')
        for index in range(12):
            marker = f'事件正文_{index}_'
            self.raw_markers.append(marker)
            self.thread = self.service.threads.add_event(self.thread['id'],
                expected_revision=self.thread['revision'], kind='decision',
                source='user_stated', text=marker + '阅读安排要以精力限制为准。' * 42)
        for purpose, count in (('draft', 300), ('reference', 330), ('artifact', 110)):
            marker = f'资料正文_{purpose}_'
            self.raw_markers.append(marker)
            note = self.service.notes.create('资料_' + purpose, marker + '阅读时间和睡前精力。' * count)
            self.note_ids.add(note['id'])
            self.thread = self.service.threads.link_material(self.thread['id'],
                expected_revision=self.thread['revision'], note_id=note['id'],
                version=1, purpose=purpose)
            if purpose == 'draft':
                self.draft = note
        self.service.participation.select(self.thread['id'])
        for index in range(6):
            marker = f'记忆正文_{index}_'
            self.raw_markers.append(marker)
            identifier = self.memory.add(dict(kind='fact', key='阅读限制:' + str(index),
                value=marker + '每晚最多二十分钟，忙碌时只读两页。' * 10,
                source='stated', importance=.9, confidence=.95))
            self.hits.append((self.memory.get(identifier), .95))
        for index in range(8):
            self.service.store.create_task(title=f'执行任务_{index}_' + '阅读安排' * 35)
        self.history = [{'role': 'user' if index % 2 == 0 else 'assistant',
                         'content': f'历史正文_{index}_' + '阅读限制与取舍条件。' * 70}
                        for index in range(32)]
        self.raw_markers.extend(f'历史正文_{index}_' for index in range(32))

    def assert_metadata_usage(self, usage):
        self.assertEqual(usage['schema_version'], 1)
        budget = usage['budget']
        self.assertEqual(budget['scope'], 'dynamic_context')
        self.assertEqual(budget['unit'], 'characters')
        self.assertEqual(budget['max_chars'], 12000)
        self.assertLessEqual(budget['used_chars'], budget['max_chars'])
        self.assertIsInstance(usage['items'], list)
        trace = compact(usage)
        for marker in self.raw_markers:
            self.assertNotIn(marker, trace, 'Usage must not retain source content')
        self.assertNotIn('模型不能执行这条工具正文', trace)
        self.assertNotIn('当前原始请求必须完整保存', trace)

    def assert_usage_matches_admission(self, data, usage):
        memories = {row['id'] for row in memory_rows(data)}
        thread = data.get('companion_thread') or {}
        events = {(row['id'], row.get('version')) for row in thread.get('events', [])}
        events.update((history['event_id'], row.get('version'))
            for history in thread.get('event_histories', []) for row in history.get('versions', []))
        note_refs, bodies = set(), set()

        def visit(value):
            if isinstance(value, dict):
                ref = value.get('ref') or {}
                if isinstance(ref, dict) and ref.get('type') == 'note':
                    identity = (ref.get('id'), ref.get('version'))
                    note_refs.add(identity)
                    if isinstance(value.get('body'), str):
                        bodies.add(identity)
                if value.get('note_id') in self.note_ids:
                    note_refs.add((value['note_id'], value.get('version') or value.get('selected_version')))
                if value.get('id') in self.note_ids:
                    note_refs.add((value['id'], value.get('selected_version') or value.get('version')))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data.get('activity_data') or {})
        for item in usage['items']:
            if item['kind'] == 'memory':
                self.assertIn(item['id'], memories, 'Trace memory must survive final admission')
            elif item['kind'] == 'thread':
                self.assertEqual((item['id'], item['version']), (thread.get('id'), thread.get('revision')))
            elif item['kind'] == 'thread_event':
                self.assertIn((item['id'], item.get('version')), events)
            elif item['kind'] == 'note':
                identity = (item['id'], item.get('version'))
                self.assertIn(identity, note_refs, 'Operational-only bindings are not model evidence')
                self.assertEqual(item.get('body_included') is True, identity in bodies)

    async def run_ordinary(self, *, streaming, request='参考本事项资料，并解释这份笔记的阅读取舍'):
        original = self.orch._turn_context

        def capture(*args, **kwargs):
            bundle = original(*args, **kwargs)
            self.bundles.append(deepcopy(bundle))
            return bundle

        with (patch.object(ordinary, 'store_recent', return_value=deepcopy(self.history)),
              patch.object(self.orch, '_turn_context', side_effect=capture)):
            if streaming:
                events = [event async for event in self.orch.stream_respond(
                    session_id='budget-stream', user_text=request)]
                return events[-1]
            return await self.orch.respond(session_id='budget-text', user_text=request)

    def assert_ordinary_admission(self, request):
        bundle = self.bundles[-1]
        messages = self.model.calls[-1][0]
        self.assertEqual(messages, bundle['messages'])
        self.assertEqual(messages[-1], {'role': 'user', 'content': request})
        dynamic = self.orch._render_turn_context(bundle['data'])
        self.assertEqual(messages[1:-1], dynamic)
        used_chars = len(compact(dynamic))
        self.assertEqual(bundle['usage']['budget']['used_chars'], used_chars)
        self.assertLessEqual(used_chars, 12000)
        self.assertGreater(bundle['usage']['budget']['omitted_items'], 0)
        self.assert_metadata_usage(bundle['usage'])
        self.assert_usage_matches_admission(bundle['data'], bundle['usage'])
        self.assertEqual(set(bundle['used_ids']), {row['id'] for row in memory_rows(bundle['data'])})
        prompt = compact(messages)
        for row in memory_rows(bundle['data']):
            self.assertIn(row['value'], prompt)
        return bundle

    async def test_nonstream_final_prompt_limits_combined_context_and_history(self):
        request = '参考本事项资料，并解释这份笔记的阅读取舍'
        result = await self.run_ordinary(streaming=False, request=request)
        bundle = self.assert_ordinary_admission(request)
        self.assertEqual(result.memories_used, bundle['used_ids'])
        self.assertEqual(result.context_usage, bundle['usage'])

    async def test_stream_final_prompt_and_done_report_exact_same_admission(self):
        request = '参考本事项资料，并解释这份笔记的阅读取舍'
        done = await self.run_ordinary(streaming=True, request=request)
        bundle = self.assert_ordinary_admission(request)
        self.assertEqual(done['type'], 'done')
        self.assertEqual(done['context_usage'], bundle['usage'])

    async def test_only_finally_admitted_memories_increment_use_count(self):
        await self.run_ordinary(streaming=False)
        admitted = set(self.bundles[-1]['used_ids'])
        self.assertLess(len(admitted), len(self.hits), 'This fixture must exercise a removed memory')
        for row, _score in self.hits:
            self.assertEqual(self.memory.get(row.id).use_count, 1 if row.id in admitted else 0)

    async def test_light_prompt_keeps_whole_memory_and_reports_admitted_identifiers(self):
        self.service.participation.select(None)
        self.history = []
        request = '阅读习惯要怎么安排'
        await self.run_ordinary(streaming=False, request=request)
        bundle = self.bundles[-1]
        rows = memory_rows(bundle['data'])
        self.assertEqual(len(rows), len(self.hits))
        self.assertEqual(set(bundle['used_ids']), {row.id for row, _score in self.hits})
        self.assert_usage_matches_admission(bundle['data'], bundle['usage'])
        self.assert_metadata_usage(bundle['usage'])
        traced = {item['id'] for item in bundle['usage']['items'] if item['kind'] == 'memory'}
        self.assertEqual(traced, set(bundle['used_ids']))
        for row in rows:
            self.assertEqual(row['source'], 'stated')
            self.assertEqual(row['evidence']['state'], 'manual')
            self.assertIn(row['value'], compact(self.model.calls[-1][0]))
            self.assertEqual(self.memory.get(row['id']).use_count, 1)

    async def test_current_user_is_outside_dynamic_budget_and_not_truncated(self):
        request = '当前原始请求必须完整保存' + '阅读时段和精力需要权衡。' * 1700
        await self.run_ordinary(streaming=False, request=request)
        self.assertEqual(self.model.calls[-1][0][-1]['content'], request)
        self.assertLessEqual(self.bundles[-1]['usage']['budget']['used_chars'], 12000)

    async def test_ordinary_disabled_never_reads_personal_context(self):
        self.manager.enabled = False
        watched = []
        patchers = [patch.object(owner, name, side_effect=AssertionError('Disabled context read'))
            for owner, name in ((self.manager.retriever, 'build_context'),
                                (self.manager.retriever, 'search'),
                                (self.service.participation, 'context'),
                                (self.service.participation, 'current'),
                                (self.service.notes, 'selection'),
                                (self.service.notes, 'get_version'),
                                (self.service.preference_store, 'list_claims'))]
        for patcher in patchers:
            watched.append(patcher.start())
            self.addCleanup(patcher.stop)
        await self.run_ordinary(streaming=False)
        self.assertTrue(all(mock.call_count == 0 for mock in watched))
        self.assertFalse(self.bundles[-1]['usage']['enabled'])
        self.assertFalse(self.bundles[-1]['used_ids'])

    def assistant_payload(self, context):
        return {'user_text': '当前原始请求必须完整保存' + '原始目标。' * 4000,
                'latest_user_text': '只修改时段，不改变阅读目标。' * 1500,
                'input_history': [{'role': 'user', 'content': '用户最新限制。' * 4000}],
                'observations': [{'tool': 'note.read', 'status': 'succeeded',
                    'data': {'body': '模型不能执行这条工具正文' + '真实执行回执。' * 4000}}],
                'tools': [{'name': 'note.read', 'schema': {'description': '必要工具定义。' * 2500}}],
                'context': context}

    async def test_assistant_run_and_model_use_one_policy_and_one_thread_with_budget(self):
        context = self.service._run_context(
            '参考本事项资料，并解释这份笔记的阅读取舍', 'assistant-budget', history=self.history)
        self.assert_metadata_usage(context['context_usage'])
        self.assertNotIn('companion_thread', context['response_policy'])
        if context.get('activity_data'):
            # Activity may keep a small target label; complete records have one
            # canonical location and must not consume the budget twice.
            activity_thread = context['activity_data'].get('thread') or {}
            self.assertNotIn('events', activity_thread)
            self.assertNotIn('event_histories', activity_thread)
            if activity_thread:
                self.assertEqual(activity_thread['id'], context['companion_thread']['id'])
        payload = self.assistant_payload(context)
        original = deepcopy(payload)
        result = await self.service._decide(payload)
        self.assertEqual(result['action'], 'final')
        messages = self.model.calls[-1][0]
        sent = json.loads(messages[-1]['content'])
        model_context = self.service._model_context(context)
        policy = self.service.context_broker.composer.render(context['response_policy'])
        self.assertEqual(sent['context'], model_context)
        self.assert_usage_matches_admission(model_context, context['context_usage'])
        self.assertNotIn('response_policy', model_context)
        self.assertEqual(messages[0]['content'].count(policy), 1)
        measured = len(compact(model_context)) + len(policy)
        self.assertLessEqual(measured, 12000)
        self.assertEqual(context['context_usage']['budget']['used_chars'], measured)
        for field in ('user_text', 'latest_user_text', 'input_history', 'observations', 'tools'):
            self.assertEqual(sent[field], original[field], 'Execution core must not be budgeted')
        expected = deepcopy(original)
        expected['context']['_context_provided'] = True
        self.assertEqual(payload, expected, 'Only the actual prompt delivery marker may be added')

    async def test_assistant_binding_survives_budget_and_model_projection(self):
        context = self.service._run_context('解释这份笔记', 'binding-budget', history=self.history)
        ref = {'type': 'note', 'id': self.draft['id'], 'version': 1}
        self.assertEqual(context['activity_note_ref'], ref)
        self.assertEqual(context['activity_material_source']['ref'], ref)
        self.assertEqual(context['companion_thread_id'], self.thread['id'])
        saved_source = deepcopy(context['activity_material_source'])
        await self.service._decide(self.assistant_payload(context))
        self.assertEqual(context['activity_material_source'], saved_source)
        self.assertTrue(self.service.activity.validate_source(saved_source))

    async def test_assistant_usage_is_reported_only_after_actual_model_boundary(self):
        context = self.service._run_context('看看阅读计划的取舍', 'actual-boundary', history=self.history)
        result = {'status': 'completed', 'text': '这只是确定性入口的回复。', 'receipts': []}
        before = self.service._with_receipts(deepcopy(result), context)
        self.assertNotIn('context_usage', before)
        self.model.offline = True
        await self.service._decide(self.assistant_payload(context))
        offline = self.service._with_receipts(deepcopy(result), context)
        self.assertNotIn('context_usage', offline)
        self.assertEqual(self.model.calls, [])
        self.model.offline = False
        await self.service._decide(self.assistant_payload(context))
        sent = self.service._with_receipts(deepcopy(result), context)
        self.assertEqual(sent['context_usage'], context['context_usage'])
        self.assertEqual(len(self.model.calls), 1)

    async def test_assistant_disabled_never_reads_personal_context(self):
        self.manager.enabled = False
        watched = []
        for owner, name in ((self.manager.retriever, 'search'),
                            (self.service.participation, 'current'),
                            (self.service.participation, 'context'),
                            (self.service.participation, 'project'),
                            (self.service.notes, 'selection'),
                            (self.service.notes, 'get_version'),
                            (self.service.preference_store, 'list_claims')):
            patcher = patch.object(owner, name, side_effect=AssertionError('Disabled context read'))
            watched.append(patcher.start())
            self.addCleanup(patcher.stop)
        context = self.service._run_context('参考本事项资料，解释这份笔记', 'disabled', history=self.history)
        await self.service._decide(self.assistant_payload(context))
        self.assertTrue(all(mock.call_count == 0 for mock in watched))
        self.assertFalse(context['context_usage']['enabled'])

    async def test_disabled_proactive_path_does_not_search_memory(self):
        self.manager.enabled = False
        self.orch.set_proactivity(True)
        with patch.object(self.manager.retriever, 'search',
                          side_effect=AssertionError('Disabled proactive memory read')) as search:
            result = await self.orch.proactive_turn(session_id='off-proactive', idle_seconds=100000)
        self.assertIsNone(result)
        search.assert_not_called()
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.message_writes, [])

    async def test_special_voice_modes_do_not_adopt_thread_or_materials(self):
        for mode in ('theatre', 'focus', 'walk', 'winddown', 'vent', 'discuss'):
            with self.subTest(mode=mode):
                session = ExperienceSession()
                session.configure(mode=mode, scene='A quiet reading corner')
                model = CaptureLLM()
                with (patch.object(self.service.activity, 'context',
                                   side_effect=AssertionError('Activity isolation')),
                      patch.object(self.service.participation, 'context',
                                   side_effect=AssertionError('Thread isolation'))):
                    events = [event async for event in session.respond('继续聊阅读', model, None, lambda: True)]
                self.assertEqual(events[-1]['type'], 'done')
                prompt = compact(model.calls[-1][0])
                for marker in self.raw_markers:
                    self.assertNotIn(marker, prompt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
