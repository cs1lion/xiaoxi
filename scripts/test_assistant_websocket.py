"""Actual WS definitions + real stores in a temp directory, no app bootstrap.

No production databases, model network or speech providers are used.
"""
import asyncio
import base64
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.service import AssistantService
from server.assistant.loop import Tool
try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-assistant-ws-')
        self.service = AssistantService(Path(self.temp.name), SimpleNamespace(offline=True))
        self.env = load_ws()
        self.env.update(assistant=self.service, __package__='assistant_ws_test')
        self.stored, self.speech, self.payloads = [], [], []
        module = ModuleType('assistant_ws_test.dialogue.orchestrator')
        module.store_message = lambda *args: self.stored.append(args)
        module.store_recent = lambda session_id, limit: []
        self.modules = patch.dict('sys.modules', {module.__name__: module})
        self.modules.start()
        owner = self

        class TTS:
            async def speak(self, text, **kwargs):
                owner.speech.append((text, kwargs))
                yield SimpleNamespace(audio=b'fake-audio', text=text)

        async def stream(**kwargs):
            yield {'type': 'done', 'text': 'ordinary'}

        self.env.update(tts=TTS(), orchestrator=SimpleNamespace(stream_respond=stream,
            state={'proactivity_enabled': False}, set_proactivity=lambda _: None))
        self.ws = FakeSocket()
        self.endpoint = asyncio.create_task(self.env['websocket_endpoint'](self.ws))
        self.session_id = (await self.ws.wait('ready'))['session_id']

    async def asyncTearDown(self):
        self.ws.inbox.put_nowait({'type': 'websocket.disconnect'})
        await asyncio.wait_for(self.endpoint, 2)
        await asyncio.sleep(0)
        self.modules.stop()
        self.service.close()
        self.temp.cleanup()

    def model(self, decisions):
        async def decide(payload):
            self.payloads.append(payload)
            return decisions.pop(0)
        self.service.loop.model = decide

    async def text(self, value, turn_id='request'):
        self.ws.send('text', text=value, turn_id=turn_id)
        await self.ws.wait('done', turn_id)
        return (await self.ws.wait('assistant_result', turn_id))['result']

    async def confirm(self, result, turn_id='confirm', approve=True):
        self.send_confirm(result, turn_id, approve)
        await self.ws.wait('done', turn_id)
        return (await self.ws.wait('assistant_result', turn_id))['result']

    def send_confirm(self, result, turn_id, approve=True):
        self.ws.send('assistant_confirm', run_id=result['run_id'],
                     confirmation_id=result['confirmation']['id'], approve=approve, turn_id=turn_id)

    def assert_spoken(self, turn_id, text):
        events = [e for e in self.ws.events if e.get('turn_id') == turn_id]
        types = [e['type'] for e in events]
        self.assertLess(types.index('emotion'), types.index('text_delta'))
        self.assertLess(types.index('text_delta'), types.index('audio'))
        audio = next(e for e in events if e['type'] == 'audio')
        self.assertEqual(base64.b64decode(audio['data']), b'fake-audio')
        self.assertEqual(audio['text'], text)
        self.assertEqual(events[-1]['delivery']['audio_status'], 'synthesized')
        self.assertFalse(events[-1]['delivery']['playback_confirmed'])

    async def test_clarification_nonkeyword_reply_and_confirmation_are_spoken(self):
        self.model([
            {'action': 'clarify', 'text': '几点提醒你练英语？'},
            {'action': 'tool_call', 'tool': 'reminder.create',
             'arguments': {'content': '练英语', 'scheduled_at': time.time() + 3600}},
            {'action': 'final', 'text': '练英语的提醒已经安排好了。'},
        ])
        self.ws.send('config', emotion='调侃')
        await self.ws.wait('config_ack')
        first = await self.text('提醒我练英语')
        self.assertEqual(first['status'], 'waiting_input')
        second = await self.text('九点半', 'follow-up')
        self.assertEqual(second['run_id'], first['run_id'])
        self.assertEqual(second['status'], 'waiting_confirmation')
        self.assertEqual(self.service.store.list_reminders(), [])
        final = await self.confirm(second)
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(len(self.service.store.list_reminders()), 1)
        self.assertEqual(self.payloads[1]['user_text'], '提醒我练英语')
        self.assertEqual(self.payloads[1]['latest_user_text'], '九点半')
        self.assertEqual([x['role'] for x in self.payloads[1]['input_history']], ['user', 'assistant', 'user'])
        for turn_id, result in [('request', first), ('follow-up', second), ('confirm', final)]:
            self.assert_spoken(turn_id, result['text'])
        self.assertTrue(all(kwargs['emotion'] == '调侃' for _, kwargs in self.speech))
        self.assertEqual([x[2] for x in self.stored if x[1] == 'user'], ['提醒我练英语', '九点半'])

    async def test_thread_ignore_falls_back_to_ordinary_dialogue(self):
        thread = self.service.threads.create('演示')
        self.service.participation.select(thread['id'])
        answer = {'action': 'clarify'}

        async def resolve(payload):
            return answer

        self.service.thread_conversation.resolver = resolve
        await self.service.start('把刚才那个记下来', self.session_id)
        text = '今天有点累，先不记了'
        self.assertTrue(self.service.should_route(text, self.session_id))
        answer = {'action': 'ignore'}
        stream_calls, statuses = [], []
        original_start = self.service.start

        async def observed_start(*args, **kwargs):
            result = await original_start(*args, **kwargs)
            statuses.append(result['status'])
            return result

        async def ordinary(**kwargs):
            stream_calls.append(kwargs)
            yield {'type': 'text_delta', 'delta': '那就先歇一会儿。'}
            yield {'type': 'done', 'text': '那就先歇一会儿。'}

        self.env['orchestrator'].stream_respond = ordinary
        with patch.object(self.service, 'start', side_effect=observed_start):
            self.ws.send('text', text=text, turn_id='ordinary-fallback')
            done = await self.ws.wait('done', 'ordinary-fallback')
        self.assertEqual(statuses, ['not_applicable'])
        self.assertEqual(len(stream_calls), 1)
        self.assertEqual(stream_calls[0]['user_text'], text)
        self.assertEqual(stream_calls[0]['session_id'], self.session_id)
        self.assertEqual(done['text'], '那就先歇一会儿。')
        self.assertFalse(any(e['type'] == 'assistant_result' for e in self.ws.events))
        self.assertEqual(self.stored, [])
        self.assertEqual(self.speech, [])
        self.assertEqual(self.service.threads.get(thread['id']), thread)

    async def test_double_confirmation_executes_exactly_once(self):
        self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '周报'}},
                    {'action': 'final', 'text': '已创建周报任务。'}])
        result = await self.text('创建周报任务')
        await self.confirm(result)
        self.send_confirm(result, 'duplicate')
        error = await self.ws.wait('error', 'duplicate')
        self.assertEqual(error['code'], 'assistant_confirmation_invalid')
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        self.assertEqual(len(self.payloads), 2)

    async def test_other_session_cannot_confirm(self):
        self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'other'}}])
        result = await self.service.start('创建任务', 'other-session')
        self.send_confirm(result, 'foreign')
        self.assertTrue((await self.ws.wait('error', 'foreign'))['terminal'])
        self.assertEqual(self.service.store.list_tasks(), [])
        self.assertEqual(self.service.runtime.get(result['run_id'])['status'], 'waiting_confirmation')
        self.assertEqual(self.speech, [])

    async def test_rejection_is_spoken_without_executing_or_model_recall(self):
        self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '周报'}}])
        result = await self.text('创建周报任务')
        final = await self.confirm(result, approve=False)
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(self.service.store.list_tasks(), [])
        self.assert_spoken('confirm', final['text'])

    async def test_tool_failure_receipt_and_reply_are_spoken(self):
        def fail():
            raise ValueError('fixture_failure')
        self.service.loop.tools['task.fail'] = Tool('task.fail', 'fail', {}, (), fail, True)
        self.model([{'action': 'tool_call', 'tool': 'task.fail', 'arguments': {}},
                    {'action': 'final', 'text': '操作失败了，没有创建任务。'}])
        result = await self.text('创建任务')
        final = await self.confirm(result)
        self.assertEqual(final['receipts'][0]['status'], 'failed')
        self.assertEqual(self.service.store.list_tasks(), [])
        self.assert_spoken('confirm', final['text'])

    async def test_audio_failure_keeps_action_text_and_done(self):
        class BrokenTTS:
            async def speak(self, *args, **kwargs):
                raise RuntimeError('fake provider')
                yield
        self.env['tts'] = BrokenTTS()
        self.model([{'action': 'final', 'text': '这是完整文字。'}])
        result = await self.text('查看任务')
        self.assertTrue((await self.ws.wait('error', 'request'))['recoverable'])
        done = await self.ws.wait('done', 'request')
        self.assertEqual(done['text'], result['text'])
        self.assertEqual(done['delivery']['audio_status'], 'failed')

    async def test_cancelled_tts_cannot_revive_action_audio(self):
        started, cancelled, release, returned = [asyncio.Event() for _ in range(4)]
        class LateTTS:
            async def speak(self, text, **kwargs):
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                returned.set()
                yield SimpleNamespace(audio=b'late', text=text)
        self.env['tts'] = LateTTS()
        self.model([{'action': 'final', 'text': '旧结果。'}])
        self.ws.send('text', text='查看任务', turn_id='old')
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send('start', turn_id='new')
        await self.ws.wait('recording', 'new')
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertFalse(any(e['type'] in ('audio', 'done', 'error') and e.get('turn_id') == 'old'
                             for e in self.ws.events))
        self.ws.send('audio_end', turn_id='new')
        await self.ws.wait('done', 'new')

    async def test_cancelled_confirmation_keeps_receipt_without_late_output_or_replay(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]
        async def decide(payload):
            self.payloads.append(payload)
            if not payload['observations']:
                return {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'only-once'}}
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            returned.set()
            return {'action': 'final', 'text': '迟到结果。'}
        self.service.loop.model = decide
        result = await self.text('创建任务')
        self.send_confirm(result, 'old-confirm')
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        self.ws.send('start', turn_id='new')
        await self.ws.wait('recording', 'new')
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertFalse(any(e['type'] in ('assistant_result', 'audio', 'done') and e.get('turn_id') == 'old-confirm'
                             for e in self.ws.events))
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        self.assertEqual(self.service.runtime.get(result['run_id'])['state']['observations'][0]['status'], 'succeeded')
        self.assertEqual(self.service.runtime.get(result['run_id'])['status'], 'interrupted')
        self.ws.send('audio_end', turn_id='new')
        await self.ws.wait('done', 'new')

    async def test_proactive_speaks_without_user_history_or_second_generation(self):
        conn = self.env['Connection'](self.ws)
        await self.env['_run_turn'](conn, '提醒你看看结果。', 'proactive')
        self.assertEqual(self.stored, [])
        self.assertEqual(self.payloads, [])
        self.assertEqual(self.speech[0][0], '提醒你看看结果。')
        self.assert_spoken(conn.turn.turn_id, '提醒你看看结果。')

    async def test_malformed_confirmation_does_not_disconnect(self):
        for fields in ({'approve': 1}, {'run_id': [], 'confirmation_id': 'c', 'approve': True}):
            self.ws.send('assistant_confirm', turn_id='bad', **fields)
        self.ws.send('start', turn_id='valid')
        await self.ws.wait('recording', 'valid')
        self.ws.send('audio_end', turn_id='valid')
        await self.ws.wait('done', 'valid')
        self.assertEqual(self.service.store.list_tasks(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
