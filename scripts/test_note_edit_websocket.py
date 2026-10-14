"""Actual app AST and note edit service, with only local TTS/resolver fakes.

All persisted objects live in a temporary directory. No app bootstrap, user
database, model network, speech provider or playback device is initialized.
"""
from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.dict(os.environ, {'DB_PATH': ':memory:', 'LLM_PROVIDER': 'mock', 'LLM_API_KEY': ''}):
    from server.assistant.service import AssistantService
try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


class NoteEditWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-note-edit-ws-')
        self.service = AssistantService(Path(self.temp.name), SimpleNamespace(offline=True))
        self.note = self.service.notes.create('演示稿', '开头：先讲目标。\n第二段：需要补对比。')
        self.selection = self.service.notes.select(self.note['id'], version=1,
            expected_revision=self.service.notes.selection()['revision'])
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '第二段：需要补对比。', 'new_text': '第二段：先展示前后对比。'}]}
        self.calls, self.stored, self.speech, self.releases = [], [], [], []

        async def resolve(payload):
            self.calls.append(payload)
            return self.answer

        self.service.note_conversation.resolver = resolve
        self.env = load_ws()
        self.env.update(assistant=self.service, __package__='note_edit_ws_test')
        module = ModuleType('note_edit_ws_test.dialogue.orchestrator')
        module.store_message = lambda *args: self.stored.append(args)
        module.store_recent = lambda session_id, limit: []
        self.module_patch = patch.dict('sys.modules', {module.__name__: module})
        self.module_patch.start()
        owner = self

        class TTS:
            async def speak(self, text, **kwargs):
                owner.speech.append((text, kwargs))
                yield SimpleNamespace(audio=b'note-preview-audio', text=text)

        async def ordinary(**kwargs):
            yield {'type': 'done', 'text': 'ordinary'}

        self.env.update(tts=TTS(), orchestrator=SimpleNamespace(stream_respond=ordinary,
            state={'proactivity_enabled': False}, set_proactivity=lambda _: None))
        self.ws = FakeSocket()
        self.endpoint = asyncio.create_task(self.env['websocket_endpoint'](self.ws))
        self.session_id = (await self.ws.wait('ready'))['session_id']

    async def asyncTearDown(self):
        try:
            for release in self.releases:
                release.set()
            self.ws.inbox.put_nowait({'type': 'websocket.disconnect'})
            await asyncio.wait_for(self.endpoint, 2)
            await asyncio.sleep(0)
        finally:
            self.module_patch.stop()
            self.service.close()
            self.temp.cleanup()

    async def test_real_note_candidate_is_spoken_once_with_turn_and_voice_selection(self):
        self.ws.send('config', emotion='认真')
        await self.ws.wait('config_ack')
        text = '修改这份笔记的第二段'
        self.ws.send('text', text=text, turn_id='note-preview')
        done = await self.ws.wait('done', 'note-preview')
        event = await self.ws.wait('assistant_result', 'note-preview')
        result = event['result']
        candidate = result['note_proposal']
        self.assertEqual(candidate['note_id'], self.note['id'])
        self.assertEqual(candidate['base_note_version'], 1)
        self.assertEqual(candidate['original_body'], self.note['body'])
        self.assertEqual(candidate['body'], '开头：先讲目标。\n第二段：先展示前后对比。')
        events = [item for item in self.ws.events if item.get('turn_id') == 'note-preview']
        kinds = [item['type'] for item in events]
        self.assertEqual(kinds, ['asr', 'thinking', 'assistant_result', 'emotion', 'text_delta', 'audio', 'done'])
        self.assertEqual(next(item for item in events if item['type'] == 'text_delta')['delta'], result['text'])
        audio = next(item for item in events if item['type'] == 'audio')
        self.assertEqual(base64.b64decode(audio['data']), b'note-preview-audio')
        self.assertEqual(audio['text'], result['text'])
        self.assertEqual(self.speech, [(result['text'], {'emotion': '认真'})])
        self.assertEqual(done['text'], result['text'])
        self.assertEqual(done['delivery']['audio_status'], 'synthesized')
        self.assertFalse(done['delivery']['playback_confirmed'])
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)
        self.assertEqual(self.service.notes.selection(), self.selection)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([row[2] for row in self.stored if row[1] == 'user'], [text])

    async def test_cancelled_resolver_cannot_create_or_speak_a_late_note_candidate(self):
        started, cancelled, release, returned = [asyncio.Event() for _ in range(4)]
        self.releases.append(release)

        async def stubborn(payload):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            returned.set()
            return self.answer

        self.service.note_conversation.resolver = stubborn
        self.ws.send('text', text='修改这份笔记的第二段', turn_id='old-note')
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send('start', turn_id='new-turn')
        await self.ws.wait('recording', 'new-turn')
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.ws.send('audio_end', turn_id='new-turn')
        await self.ws.wait('done', 'new-turn')
        self.assertFalse(any(item['type'] in ('assistant_result', 'audio', 'done', 'error')
            and item.get('turn_id') == 'old-note' for item in self.ws.events))
        self.assertEqual(self.service.note_proposals.list_pending(self.note['id']), [])
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)
        self.assertEqual(self.service.notes.selection(), self.selection)
        self.assertEqual(self.speech, [])
        self.assertEqual(self.stored, [])

    async def test_cancelled_preview_audio_keeps_review_candidate_without_saving_or_late_events(self):
        started, cancelled, release, returned = [asyncio.Event() for _ in range(4)]
        self.releases.append(release)

        class LateTTS:
            async def speak(self, text, **kwargs):
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                returned.set()
                yield SimpleNamespace(audio=b'late-note-audio', text=text)

        self.env['tts'] = LateTTS()
        self.ws.send('text', text='修改这份笔记的第二段', turn_id='old-preview')
        event = await self.ws.wait('assistant_result', 'old-preview')
        candidate = event['result']['note_proposal']
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send('start', turn_id='new-turn')
        await self.ws.wait('recording', 'new-turn')
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.ws.send('audio_end', turn_id='new-turn')
        await self.ws.wait('done', 'new-turn')
        self.assertFalse(any(item['type'] in ('audio', 'done', 'error')
            and item.get('turn_id') == 'old-preview' for item in self.ws.events))
        self.assertEqual(self.service.note_proposals.get(candidate['id']), candidate)
        self.assertEqual(self.service.notes.get(self.note['id']), self.note)
        self.assertEqual(self.service.notes.selection(), self.selection)


if __name__ == '__main__':
    unittest.main(verbosity=2)
