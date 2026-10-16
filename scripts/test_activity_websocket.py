"""Actual WebSocket resume routing over reconstructed temporary object stores.

The production app is AST-loaded through load_ws(), never imported. Only speech,
session logging and provider boundaries are fakes; service routing, selection,
version metadata, WebSocket presentation and reply delivery are real code.
"""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.dict(os.environ, {'DB_PATH': ':memory:', 'LLM_PROVIDER': 'mock', 'LLM_API_KEY': ''}):
    from server.assistant.service import AssistantService
try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


def synthetic_audio():
    """A small valid WAV; no speech engine or playback device is involved."""
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\x00\x00' * 320)
    return buffer.getvalue()


class ActivityWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-activity-ws-')
        self.directory = Path(self.temp.name)
        self.manager = SimpleNamespace(enabled=True)
        self.llm = SimpleNamespace(offline=True,
            complete=AsyncMock(side_effect=AssertionError('Resume must not invoke a model')))
        self.service = AssistantService(self.directory, self.llm, self.manager)
        self.env = load_ws()
        self.env.update(assistant=self.service, manager=self.manager,
                        __package__='activity_ws_test')
        self.sessions, self.ended, self.stored, self.recent, self.speech = [], [], [], [], []
        self.sockets, self.model_sentinels, self.stream_calls = [], [], []
        self.raw_audio = synthetic_audio()
        owner = self

        class TTS:
            engine = 'test'
            offline = False

            async def speak(self, text, **kwargs):
                owner.speech.append((text, kwargs))
                yield SimpleNamespace(audio=owner.raw_audio, text=text)

        async def forbidden_stream(**kwargs):
            self.stream_calls.append(kwargs)
            raise AssertionError('Resume must not enter ordinary model streaming')
            yield  # Make the sentinel an async iterator like the real boundary.

        def recent(session_id, limit):
            self.recent.append((session_id, limit))
            return []

        module = ModuleType('activity_ws_test.dialogue.orchestrator')
        module.store_message = lambda *args: self.stored.append(args)
        module.store_recent = recent
        self.module_patch = patch.dict('sys.modules', {module.__name__: module})
        self.module_patch.start()
        self.env.update(tts=TTS(), llm=self.llm,
            store=SimpleNamespace(start_session=self.sessions.append, end_session=self.ended.append),
            orchestrator=SimpleNamespace(stream_respond=forbidden_stream,
                state={'proactivity_enabled': False}, set_proactivity=lambda _: None,
                executive_route=lambda text, executive=None, memory_enabled=False: None))
        self.forbid_model_and_tools()

    def forbid_model_and_tools(self):
        model = AsyncMock(side_effect=AssertionError('Resume must bypass action model'))
        loop = AsyncMock(side_effect=AssertionError('Resume must not enter model/tool loop'))
        resolver = AsyncMock(side_effect=AssertionError('Resume must not infer a thread summary'))
        self.service.loop.model, self.service.loop.start = model, loop
        self.service.thread_conversation.resolver = resolver
        self.model_sentinels.extend((model, loop, resolver))

    async def connect(self):
        socket = FakeSocket()
        endpoint = asyncio.create_task(self.env['websocket_endpoint'](socket))
        self.sockets.append((socket, endpoint))
        ready = await socket.wait('ready')
        return socket, endpoint, ready['session_id']

    async def disconnect(self, socket, endpoint):
        if not endpoint.done():
            socket.inbox.put_nowait({'type': 'websocket.disconnect'})
        await asyncio.wait_for(endpoint, 2)
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        try:
            for socket, endpoint in self.sockets:
                await self.disconnect(socket, endpoint)
        finally:
            self.module_patch.stop()
            self.service.close()
            self.temp.cleanup()

    async def resume(self, socket, turn_id):
        socket.send('text', text='接着昨天', turn_id=turn_id)
        try:
            done = await socket.wait('done', turn_id)
        except TimeoutError:
            self.fail(f'Resume did not finish; actual WebSocket events: {socket.events!r}')
        result = (await socket.wait('assistant_result', turn_id))['result']
        events = [event for event in socket.events if event.get('turn_id') == turn_id]
        kinds = [event['type'] for event in events]
        self.assertEqual(kinds, ['asr', 'thinking', 'assistant_result', 'emotion',
                                 'text_delta', 'audio', 'done'])
        self.assertEqual(next(event for event in events if event['type'] == 'text_delta')['delta'], result['text'])
        audio = next(event for event in events if event['type'] == 'audio')
        self.assertEqual(base64.b64decode(audio['data'], validate=True), self.raw_audio)
        self.assertEqual(audio['text'], result['text'])
        self.assertEqual(audio['index'], 0)
        self.assertEqual(audio['emotion'], '认真')
        self.assertEqual(done['text'], result['text'])
        self.assertEqual(done['delivery']['audio_status'], 'synthesized')
        self.assertFalse(done['delivery']['playback_confirmed'])
        self.assertEqual(self.speech[-1], (result['text'], {'emotion': 'base'}))
        return result

    async def test_new_connection_and_reconstructed_service_resume_selected_v1_with_audio(self):
        self.assertNotIn('server.app', sys.modules)
        first = self.service.notes.create('演示稿第一版', '第一版正文：尚有两项测试没通过。')
        selection = self.service.notes.select(first['id'], version=1,
            expected_revision=self.service.notes.selection()['revision'])
        second = self.service.notes.update(first['id'], '演示稿第二版',
                                           '第二版正文：仍未发布。', version=1)
        unrelated = self.service.notes.create('最新无关笔记', '不能按更新时间猜对象。')
        thread = self.service.threads.create('发布检查')
        self.service.participation.select(thread['id'])
        thread = self.service.threads.update(thread['id'], expected_revision=thread['revision'],
                                             next_step='先处理没通过的两项测试')
        thread = self.service.threads.link_material(thread['id'],
            expected_revision=thread['revision'], note_id=first['id'], version=1, purpose='draft')
        draft = thread['materials'][0]
        before = self.service.notes.selection()
        note_rows = self.service.notes.history(first['id'])
        runtime_before = self.service.runtime.db.execute('SELECT count(*) FROM agent_runs').fetchone()[0]

        socket, endpoint, original_session = await self.connect()
        first_result = await self.resume(socket, 'original-connection')
        await self.disconnect(socket, endpoint)
        self.service.close()
        self.service = AssistantService(self.directory, self.llm, self.manager)
        self.env['assistant'] = self.service
        self.forbid_model_and_tools()
        socket, endpoint, new_session = await self.connect()
        new_result = await self.resume(socket, 'new-connection')

        self.assertNotEqual(original_session, new_session)
        self.assertEqual(first_result, new_result)
        view = new_result['activity']
        self.assertEqual(view['activity_id'], selection['activity_id'])
        self.assertEqual(view['selection']['scope'], 'thread')
        self.assertIsNone(view['selection']['revision'])
        self.assertEqual(view['selection']['note_id'], first['id'])
        self.assertEqual(view['selection']['selected_version'], 1)
        self.assertEqual(view['selection']['current_version'], 2)
        self.assertEqual(view['selection']['status'], 'stale')
        self.assertEqual(view['material_source']['material_id'], draft['id'])
        self.assertEqual(view['material_source']['thread_id'], thread['id'])
        self.assertEqual(view['material_source']['ref'],
                         {'type': 'note', 'id': first['id'], 'version': 1})
        self.assertEqual(view['note']['title'], first['title'])
        self.assertEqual(view['note']['selected_version'], 1)
        self.assertEqual(view['note']['current_version'], 2)
        self.assertEqual(view['note']['status'], 'stale')
        self.assertEqual(view['thread']['id'], thread['id'])
        self.assertEqual(view['thread']['next_step'], thread['next_step'])
        self.assertEqual(view['object_refs'], [
            {'type': 'note', 'id': first['id'], 'version': 1},
            {'type': 'thread', 'id': thread['id'], 'version': thread['revision']}])
        self.assertIn(first['title'], new_result['text'])
        self.assertIn(thread['next_step'], new_result['text'])
        self.assertNotIn(second['title'], new_result['text'])
        self.assertNotIn(unrelated['title'], new_result['text'])
        self.assertNotIn('昨天完成', new_result['text'])
        self.assertNotIn('body', view['note'])
        self.assertNotIn(first['body'], str(view))
        self.assertNotIn(second['body'], str(view))
        self.assertEqual(new_result['receipts'], [])
        self.assertEqual(new_result['steps'], 0)
        self.assertEqual(self.service.notes.selection(), before)
        self.assertEqual(self.service.notes.history(first['id']), note_rows)
        self.assertEqual(self.service.threads.get(thread['id']), thread)
        self.assertEqual(self.service.runtime.db.execute('SELECT count(*) FROM agent_runs').fetchone()[0], runtime_before)
        self.assertEqual(self.service.store.list_tasks(), [])
        self.assertEqual(self.service.store.list_reminders(), [])
        self.assertEqual(self.stream_calls, [])
        self.llm.complete.assert_not_called()
        for sentinel in self.model_sentinels:
            sentinel.assert_not_called()
        self.assertEqual(self.sessions, [original_session, new_session])
        self.assertEqual(self.recent, [(original_session, 3), (new_session, 3)])
        self.assertEqual([row[0] for row in self.stored if row[1] == 'user'],
                         [original_session, new_session])
        self.assertEqual(len(self.speech), 2)
        self.assertNotIn('server.app', sys.modules)


if __name__ == '__main__':
    unittest.main(verbosity=2)
