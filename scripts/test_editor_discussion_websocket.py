"""Actual WebSocket definitions and ephemeral editor service, without bootstrap.

Drafts and discussion history exist only in RAM. Model, ASR, TTS, ordinary chat,
tools and persistence boundaries are explicit local fakes; no production data
or provider is opened by this acceptance suite.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.editor_insights import EditorInsightService
try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


BODY = ('第一段说明读者的处境和这篇文章希望解决的问题。\n'
        '这里的结论埋在铺垫后面。\n'
        + '后文提供一个具体例子，解释选择的理由与下一步行动。' * 4)
ANCHOR = '这里的结论埋在铺垫后面。'
POINT = '主张出现得有点晚。读者先看到过程，却还不知道这些例子要支持什么判断。'
REPLY = '因为这段先讲了过程。保留铺垫也可以，但先补一句读者马上能理解的主张。'


class EditorDiscussionWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now, self.allowed = 1000.0, True
        self.analysis_calls, self.reply_calls, self.speech = [], [], []
        self.assistant_calls, self.ordinary_calls, self.stored, self.releases = [], [], [], []
        self.service = EditorInsightService(SimpleNamespace(offline=False),
            enabled=lambda: self.allowed, clock=lambda: self.now)

        async def resolve(payload):
            self.analysis_calls.append(payload)
            return {'action': 'insight', 'anchor': ANCHOR, 'point': POINT}

        async def reply(payload):
            self.reply_calls.append(payload)
            return {'text': REPLY}

        self.service.resolver = resolve
        self.service.discussion_resolver = reply
        self.insight = await self.make_insight('draft-editor')
        self.context = self.reference(self.insight)
        self.env = load_ws()
        self.env.update(editor_insights=self.service, __package__='server')
        self.connections = []
        owner = self

        class TrackingConnection(self.env['Connection']):
            def __init__(self, ws):
                super().__init__(ws)
                owner.connections.append(self)

        self.env['Connection'] = TrackingConnection
        module = ModuleType('server.dialogue.orchestrator')
        module.store_message = lambda *args: self.stored.append(args)
        module.store_recent = lambda *args: self.stored.append(('read_recent', *args)) or []
        self.module_patch = patch.dict('sys.modules', {module.__name__: module})
        self.module_patch.start()

        def should_route(text, session_id=''):
            self.assistant_calls.append(('should_route', text, session_id))
            return False

        def waiting_input(session_id):
            self.assistant_calls.append(('waiting_input', session_id))
            return None

        async def ordinary(**kwargs):
            self.ordinary_calls.append(kwargs)
            yield {'type': 'done', 'text': 'ordinary', 'emotion': 'base'}

        async def transcribe(data, **kwargs):
            return SimpleNamespace(text='为什么？', duration_ms=20)

        owner = self

        class TTS:
            async def speak(self, text, **kwargs):
                owner.speech.append((text, kwargs))
                yield SimpleNamespace(audio=b'editor-discussion-audio', text=text)

        self.env.update(
            assistant=SimpleNamespace(should_route=should_route, waiting_input=waiting_input),
            orchestrator=SimpleNamespace(stream_respond=ordinary,
                state={'proactivity_enabled': False}, set_proactivity=lambda _: None,
                executive_route=lambda text, executive=None, memory_enabled=False: None),
            recognizer=SimpleNamespace(transcribe_wav=transcribe), tts=TTS())
        self.ws = FakeSocket()
        self.ws.headers = {'origin': 'http://testserver', 'host': 'testserver'}
        self.ws.url = SimpleNamespace(scheme='ws')
        self.endpoint = asyncio.create_task(self.env['websocket_endpoint'](self.ws))
        self.session_id = (await self.ws.wait('ready'))['session_id']

    async def asyncTearDown(self):
        try:
            for release in self.releases:
                release.set()
            self.ws.inbox.put_nowait({'type': 'websocket.disconnect'})
            await asyncio.wait_for(self.endpoint, 2)
            self.service.close()
            await asyncio.sleep(0)
        finally:
            self.module_patch.stop()

    async def make_insight(self, session, revision=1):
        self.service.state(session, revision, True)
        result = await self.service.analyze(session, revision, f'演示草稿：{session}', BODY, True)
        self.assertEqual(result['status'], 'insight')
        return result['insight']

    @staticmethod
    def reference(insight):
        return {'insight_id': insight['id'], 'session_id': insight['session_id'],
                'draft_revision': insight['draft_revision']}

    def turn_events(self, turn):
        return [item for item in self.ws.events if item.get('turn_id') == turn]

    def assert_ephemeral(self):
        self.assertEqual(self.assistant_calls, [])
        self.assertEqual(self.ordinary_calls, [])
        self.assertEqual(self.stored, [])
        self.assertFalse(any(item['type'] in ('assistant_result', 'memory_notice')
            for item in self.ws.events))

    def assert_terminal_context_error(self, event):
        self.assertEqual(event['code'], 'editor_discussion_unavailable')
        self.assertEqual(event['scope'], 'editor_insight')
        self.assertIs(event['terminal'], True)

    async def barrier(self):
        after = len(self.ws.events)
        self.ws.send('config')
        await self.ws.wait('config_ack', after=after)

    async def test_why_binds_server_insight_before_asr_and_speaks_naturally_without_storage(self):
        self.ws.send('config', emotion='认真')
        await self.ws.wait('config_ack')
        self.ws.send('text', text='为什么？', turn_id='why', editor_insight=self.context)
        done = await self.ws.wait('done', 'why')
        events = self.turn_events('why')
        self.assertEqual([event['type'] for event in events],
            ['editor_discussion', 'asr', 'thinking', 'emotion', 'text_delta', 'audio', 'done'])
        self.assertEqual(events[0]['insight'], self.insight)
        self.assertEqual(events[1]['text'], '为什么？')
        self.assertEqual(done['text'], REPLY)
        self.assertEqual(done['delivery']['audio_status'], 'synthesized')
        self.assertFalse(done['delivery']['playback_confirmed'])
        self.assertEqual(base64.b64decode(next(e for e in events if e['type'] == 'audio')['data']),
                         b'editor-discussion-audio')
        self.assertEqual(self.speech, [(REPLY, {'emotion': 'base'})])
        self.assertEqual(len(self.reply_calls), 1)
        payload = json.dumps(self.reply_calls[0], ensure_ascii=False)
        for expected in ('为什么？', ANCHOR, POINT):
            self.assertIn(expected, payload)
        self.assert_ephemeral()

    async def test_same_insight_keeps_why_disagreement_and_rewrite_in_ephemeral_context(self):
        prompts = ['为什么？', '我想保留这段，不同意你的判断。', '那给我一个改写示例，但不要保存。']
        for index, prompt in enumerate(prompts):
            turn = f'follow-{index}'
            self.ws.send('text', text=prompt, turn_id=turn, editor_insight=self.context)
            await self.ws.wait('done', turn)
            self.assertEqual((await self.ws.wait('editor_discussion', turn))['insight'], self.insight)
        self.assertEqual(len(self.reply_calls), 3)
        last_payload = json.dumps(self.reply_calls[-1], ensure_ascii=False)
        for prompt in prompts:
            self.assertIn(prompt, last_payload)
        self.assertEqual(len(self.speech), 3)
        self.assert_ephemeral()

    async def test_voice_context_is_bound_at_start_and_audio_end_cannot_replace_it(self):
        other = await self.make_insight('different-draft')
        self.ws.send('start', turn_id='voice', editor_insight=self.context)
        await self.ws.wait('recording', 'voice')
        self.ws.audio()
        self.ws.send('audio_end', turn_id='voice', editor_insight=self.reference(other))
        await self.ws.wait('done', 'voice')
        events = self.turn_events('voice')
        self.assertEqual(events[0]['type'], 'editor_discussion')
        self.assertEqual(events[0]['insight']['id'], self.insight['id'])
        self.assertEqual(next(e for e in events if e['type'] == 'asr')['text'], '为什么？')
        self.assertEqual(self.reply_calls[0]['draft']['title'], '演示草稿：draft-editor')
        self.assert_ephemeral()

    async def test_malformed_or_forged_context_is_terminal_and_never_falls_back(self):
        invalids = [None, [], {}, {**self.context, 'body': '伪造草稿'},
                    {**self.context, 'point': '伪造观点'},
                    {**self.context, 'draft_revision': True},
                    {**self.context, 'session_id': 'different-editor'},
                    {**self.context, 'insight_id': 'missing'}]
        for index, invalid in enumerate(invalids):
            turn = f'invalid-{index}'
            self.ws.send('text', text='为什么？', turn_id=turn, editor_insight=invalid)
            self.assert_terminal_context_error(await self.ws.wait('error', turn))
            self.assertEqual([e['type'] for e in self.turn_events(turn)], ['error'])
        self.assertEqual(self.reply_calls, [])
        self.assertEqual(self.speech, [])
        self.assert_ephemeral()
        self.ws.send('text', text='普通对话', turn_id='recover')
        await self.ws.wait('done', 'recover')
        self.assertEqual(len(self.ordinary_calls), 1)

    async def test_invalid_voice_context_does_not_record_or_transcribe(self):
        self.ws.send('start', turn_id='invalid-voice', editor_insight={**self.context, 'draft_revision': '1'})
        self.assert_terminal_context_error(await self.ws.wait('error', 'invalid-voice'))
        self.ws.audio()
        self.ws.send('audio_end', turn_id='invalid-voice')
        await self.barrier()
        self.assertEqual([e['type'] for e in self.turn_events('invalid-voice')], ['error'])
        self.assertEqual(self.reply_calls, [])
        self.assert_ephemeral()

    async def test_cross_origin_and_missing_origin_never_bind_editor_context(self):
        for index, origin in enumerate((None, 'http://evil.test', 'https://testserver', 'http://testserver.evil.test')):
            if origin is None:
                self.ws.headers.pop('origin', None)
            else:
                self.ws.headers['origin'] = origin
            turn = f'origin-{index}'
            self.ws.send('text', text='为什么？', turn_id=turn, editor_insight=self.context)
            self.assert_terminal_context_error(await self.ws.wait('error', turn))
            self.assertEqual([e['type'] for e in self.turn_events(turn)], ['error'])
        self.assertEqual(self.reply_calls, [])
        self.assertEqual(self.speech, [])
        self.assert_ephemeral()

    async def test_sensitive_or_oversized_user_input_stops_before_model_and_storage(self):
        for index, prompt in enumerate(('password=do-not-send-this-value', '甲' * 1001)):
            turn = f'input-boundary-{index}'
            self.ws.send('text', text=prompt, turn_id=turn, editor_insight=self.context)
            event = await self.ws.wait('error', turn)
            self.assert_terminal_context_error(event)
            self.assertNotIn('do-not-send-this-value', event['message'])
        self.assertEqual(self.reply_calls, [])
        self.assertEqual(self.speech, [])
        self.assert_ephemeral()

    async def test_editor_discussion_takes_priority_over_practice_and_experience_modes(self):
        mode_calls, asr_options = [], []

        async def mode_reply(*args, **kwargs):
            mode_calls.append((args, kwargs))
            yield {'type': 'done', 'text': 'wrong-mode'}

        async def transcribe(data, **kwargs):
            asr_options.append(kwargs)
            return SimpleNamespace(text='为什么？', duration_ms=20)

        conn = self.connections[0]
        conn.practice = SimpleNamespace(mode='active', respond=mode_reply)
        conn.experience = SimpleNamespace(mode='expression', respond=mode_reply)
        self.env['recognizer'].transcribe_wav = transcribe
        # Should a routing regression reach an ordinary mode, its module import
        # remains isolated instead of initializing the real speech provider.
        fake_tts_module = ModuleType('server.speech.tts')
        fake_tts_module.tts = self.env['tts']
        with patch.dict('sys.modules', {fake_tts_module.__name__: fake_tts_module}):
            self.ws.send('start', turn_id='mode-priority', editor_insight=self.context)
            self.ws.audio()
            self.ws.send('audio_end', turn_id='mode-priority')
            done = await self.ws.wait('done', 'mode-priority')
        self.assertEqual(done['text'], REPLY)
        self.assertEqual(asr_options, [{}])
        self.assertEqual(mode_calls, [])
        self.assertEqual(len(self.reply_calls), 1)
        self.assert_ephemeral()

    async def test_edit_disable_dismiss_and_expiry_make_context_terminal_before_model(self):
        actions = ['edit', 'disable', 'dismiss', 'expire', 'policy-disabled']
        for action in actions:
            with self.subTest(action=action):
                self.allowed = True
                self.now += 400
                insight = await self.make_insight(f'boundary-{action}')
                context = self.reference(insight)
                if action == 'edit':
                    self.service.state(context['session_id'], 2, True)
                elif action == 'disable':
                    self.service.state(context['session_id'], 1, False)
                elif action == 'dismiss':
                    self.service.dismiss(**context)
                elif action == 'expire':
                    self.now += self.service.TTL_SECONDS + 1
                else:
                    self.allowed = False
                self.ws.send('text', text='为什么？', turn_id=action, editor_insight=context)
                self.assert_terminal_context_error(await self.ws.wait('error', action))
        self.assertEqual(self.reply_calls, [])
        self.assertEqual(self.speech, [])
        self.assert_ephemeral()

    async def test_edit_during_asr_cannot_deliver_a_stale_discussion(self):
        started, release = asyncio.Event(), asyncio.Event()
        self.releases.append(release)

        async def transcribe(data, **kwargs):
            started.set()
            await release.wait()
            return SimpleNamespace(text='为什么？', duration_ms=20)

        self.env['recognizer'].transcribe_wav = transcribe
        self.ws.send('start', turn_id='asr-edit', editor_insight=self.context)
        self.ws.audio()
        self.ws.send('audio_end', turn_id='asr-edit')
        await asyncio.wait_for(started.wait(), 2)
        self.service.state('draft-editor', 2, True)
        release.set()
        self.assert_terminal_context_error(await self.ws.wait('error', 'asr-edit'))
        self.assertFalse(any(e['type'] in ('text_delta', 'audio', 'done') for e in self.turn_events('asr-edit')))
        self.assertEqual(self.reply_calls, [])
        self.assert_ephemeral()

    async def test_cancelled_discussion_provider_cannot_deliver_late_reply_or_poison_next_turn(self):
        started, cancelled, release, returned = [asyncio.Event() for _ in range(4)]
        self.releases.append(release)

        async def stubborn(payload):
            self.reply_calls.append(payload)
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            returned.set()
            return {'text': '迟到的讨论回复'}

        self.service.discussion_resolver = stubborn
        self.ws.send('text', text='为什么？', turn_id='old-reply', editor_insight=self.context)
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send('start', turn_id='new-turn')
        await self.ws.wait('recording', 'new-turn')
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.ws.send('audio_end', turn_id='new-turn')
        await self.ws.wait('done', 'new-turn')
        self.assertFalse(any(e['type'] in ('text_delta', 'audio', 'done', 'error')
            for e in self.turn_events('old-reply')))
        self.assertEqual(self.speech, [])
        self.assert_ephemeral()

        async def fast(payload):
            self.reply_calls.append(payload)
            return {'text': REPLY}

        self.service.discussion_resolver = fast
        self.ws.send('text', text='那我保留原文', turn_id='after-cancel', editor_insight=self.context)
        await self.ws.wait('done', 'after-cancel')
        self.assertEqual(self.reply_calls[-1]['history'], [])
        self.assert_ephemeral()

    async def test_new_text_cancels_bound_voice_asr_even_if_asr_ignores_cancellation(self):
        started, cancelled, release, returned = [asyncio.Event() for _ in range(4)]
        self.releases.append(release)

        async def stubborn(data, **kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            returned.set()
            return SimpleNamespace(text='旧录音的为什么', duration_ms=20)

        self.env['recognizer'].transcribe_wav = stubborn
        self.ws.send('start', turn_id='old-asr', editor_insight=self.context)
        self.ws.audio()
        self.ws.send('audio_end', turn_id='old-asr')
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send('text', text='我想保留这段', turn_id='new-text', editor_insight=self.context)
        await self.ws.wait('done', 'new-text')
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertFalse(any(e['type'] in ('asr', 'text_delta', 'audio', 'done', 'error')
            for e in self.turn_events('old-asr')))
        self.assertEqual(len(self.reply_calls), 1)
        self.assertNotIn('旧录音', json.dumps(self.reply_calls[0], ensure_ascii=False))
        self.assert_ephemeral()

    async def test_edit_during_synthesis_drops_late_audio_and_ends_the_turn(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]
        self.releases.append(release)

        class LateTTS:
            async def speak(self, text, **kwargs):
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                returned.set()
                yield SimpleNamespace(audio=b'stale-draft-audio', text=text)

        self.env['tts'] = LateTTS()
        self.ws.send('text', text='为什么？', turn_id='tts-edit', editor_insight=self.context)
        await asyncio.wait_for(started.wait(), 2)
        self.service.state('draft-editor', 2, True)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        self.assert_terminal_context_error(await self.ws.wait('error', 'tts-edit'))
        self.assertFalse(any(e['type'] in ('audio', 'done') for e in self.turn_events('tts-edit')))
        self.assert_ephemeral()

    async def test_cancel_during_synthesis_drops_uncancellable_audio_without_affecting_next_turn(self):
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
                yield SimpleNamespace(audio=b'late-cancelled-audio', text=text)

        self.env['tts'] = LateTTS()
        self.ws.send('text', text='为什么？', turn_id='old-tts', editor_insight=self.context)
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send('start', turn_id='next-recording')
        await self.ws.wait('recording', 'next-recording')
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.ws.send('audio_end', turn_id='next-recording')
        await self.ws.wait('done', 'next-recording')
        self.assertFalse(any(e['type'] in ('audio', 'done', 'error') for e in self.turn_events('old-tts')))
        self.assert_ephemeral()

    async def test_provider_error_is_terminal_generic_and_does_not_route_to_tools(self):
        async def failed(payload):
            raise RuntimeError('secret-provider-implementation-detail')

        self.service.discussion_resolver = failed
        self.ws.send('text', text='保存改写内容并创建任务', turn_id='provider-failure', editor_insight=self.context)
        event = await self.ws.wait('error', 'provider-failure')
        self.assert_terminal_context_error(event)
        self.assertNotIn('secret-provider', json.dumps(event))
        self.assertEqual(self.speech, [])
        self.assert_ephemeral()

    async def test_edit_while_audio_waits_for_send_lock_cannot_emit_stale_audio(self):
        audio_ready, release_audio, blocked = [asyncio.Event() for _ in range(3)]
        self.releases.append(release_audio)

        class LockGate:
            def __init__(self):
                self.lock = asyncio.Lock()

            async def __aenter__(self):
                if self.lock.locked():
                    blocked.set()
                await self.lock.acquire()

            async def __aexit__(self, *args):
                self.lock.release()

        class ReadyTTS:
            async def speak(self, text, **kwargs):
                audio_ready.set()
                await release_audio.wait()
                yield SimpleNamespace(audio=b'waiting-lock-audio', text=text)

        socket = FakeSocket()
        conn = self.env['Connection'](socket)
        turn = await conn.begin('send-lock-edit')
        turn.editor_insight = dict(self.context)
        gate = LockGate()
        conn.send_lock = gate
        self.env['tts'] = ReadyTTS()
        delivery = asyncio.create_task(self.env['_present_reply'](conn, turn, REPLY, 'base', voice_emotion='base'))
        try:
            await asyncio.wait_for(audio_ready.wait(), 2)
            await gate.lock.acquire()
            release_audio.set()
            await asyncio.wait_for(blocked.wait(), 2)
            self.service.state('draft-editor', 2, True)
            gate.lock.release()
            await asyncio.wait_for(delivery, 2)
        finally:
            if gate.lock.locked():
                gate.lock.release()
            if not delivery.done():
                delivery.cancel()
                await asyncio.gather(delivery, return_exceptions=True)
        self.assertFalse(any(event['type'] in ('audio', 'done') for event in socket.events))
        self.assert_terminal_context_error(socket.events[-1])

    async def test_edit_while_done_waits_for_send_lock_converts_it_to_terminal_error(self):
        blocked = asyncio.Event()

        class LockGate:
            def __init__(self):
                self.lock = asyncio.Lock()

            async def __aenter__(self):
                if self.lock.locked():
                    blocked.set()
                await self.lock.acquire()

            async def __aexit__(self, *args):
                self.lock.release()

        socket = FakeSocket()
        conn = self.env['Connection'](socket)
        turn = await conn.begin('done-lock-edit')
        turn.editor_insight = dict(self.context)
        gate = LockGate()
        conn.send_lock = gate
        await gate.lock.acquire()
        terminal = asyncio.create_task(conn.finish(turn, {'type': 'done', 'text': REPLY}))
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            self.service.state('draft-editor', 2, True)
            gate.lock.release()
            await asyncio.wait_for(terminal, 2)
        finally:
            if gate.lock.locked():
                gate.lock.release()
            if not terminal.done():
                terminal.cancel()
                await asyncio.gather(terminal, return_exceptions=True)
        self.assertEqual(len(socket.events), 1)
        self.assert_terminal_context_error(socket.events[0])
        self.assertFalse(turn.live)


if __name__ == '__main__':
    unittest.main(verbosity=2)
