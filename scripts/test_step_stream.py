"""Offline transport contract tests. Every connection is an injected fake."""
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
import unittest
import wave

_spec = importlib.util.spec_from_file_location(
    'step_stream_under_test', Path(__file__).resolve().parents[1] / 'server/speech/step_stream.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
StepStream = _module.StepStream
URL = 'wss://api.stepfun.com/v1/realtime/asr/stream'
PREFIX = 'conversation.item.input_audio_transcription.'


class FakeSocket:
    def __init__(self, events=()):
        self.events = asyncio.Queue()
        for event in events:
            self.events.put_nowait(json.dumps(event) if isinstance(event, dict) else event)
        self.sent = []
        self.closed = False
        self.block_close = False

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        value = await self.events.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self):
        self.closed = True
        if self.block_close:
            await asyncio.Event().wait()


class FakeConnector:
    def __init__(self, ws):
        self.ws = ws
        self.calls = []

    async def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.ws


class StepStreamTests(unittest.IsolatedAsyncioTestCase):
    async def opened(self, events=(), **kwargs):
        ws = FakeSocket([{'type': 'session.created'}, {'type': 'session.updated'}, *events])
        connector = FakeConnector(ws)
        stream = StepStream(URL, 'offline-test-key', connector=connector, **kwargs)
        await stream.open()
        self.addAsyncCleanup(stream.close)
        return stream, ws, connector

    def test_config_validation(self):
        for url in ('', 'https://api.stepfun.com', 'ws://example.com', 'wss://',
                    'wss://user:secret@example.com', 'wss://example.com:bad', 'wss://x/#fragment'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                StepStream(url, 'secret')
        for key in ('', ' ', None, 'secret\r\nInjected: true'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                StepStream(URL, key)
        with self.assertRaises(ValueError):
            StepStream(URL, 'key', wire_format='mp3')

    async def test_session_configuration(self):
        stream, ws, connector = await self.opened()
        url, options = connector.calls[0]
        self.assertEqual(url, URL)
        self.assertEqual(options['additional_headers'], {'Authorization': 'Bearer offline-test-key'})
        self.assertEqual(options['max_size'], 128 * 1024)
        event = ws.sent[0]
        self.assertTrue(event['event_id'])
        self.assertEqual(event['type'], 'session.update')
        config = event['session']['audio']['input']
        self.assertEqual(config['format'], dict(type='pcm', codec='pcm_s16le', rate=16000, bits=16, channel=1))
        self.assertEqual(config['transcription'], dict(model='stepaudio-2.5-asr-stream', language='en',
                         full_rerun_on_commit=True, enable_itn=True, enable_timestamp_align=True))
        self.assertEqual(config['turn_detection'], dict(type='server_vad', silence_duration_ms=800, threshold=.6))
        with self.assertRaisesRegex(RuntimeError, 'already_open'):
            await stream.open()

    async def test_cumulative_correction_and_authoritative_final(self):
        word = {'word': 'hello', 'start': 0, 'end': .4, 'final': True}
        events = [
            {'type': PREFIX + 'delta', 'item_id': 'a', 'text': 'hello', 'stash': 'hello', 'words': [word]},
            {'type': PREFIX + 'delta', 'item_id': 'a', 'text': 'hello their', 'stash': 'their'},
            {'type': PREFIX + 'delta', 'item_id': 'a', 'text': 'hello there', 'stash': ''},
            {'type': PREFIX + 'completed', 'item_id': 'a', 'transcript': 'Hello there!', 'text': 'wrong', 'stash': 'wrong', 'words': [word]},
            {'type': PREFIX + 'delta', 'item_id': 'b', 'text': 'new'},
        ]
        stream, _, _ = await self.opened(events)
        first = await stream.receive()
        self.assertEqual(first, dict(type='partial', item_id='a', text='hello', stash='hello', words=[word]))
        self.assertEqual((await stream.receive())['text'], 'hello their')
        self.assertEqual((await stream.receive())['text'], 'hello there')
        self.assertEqual(await stream.receive(), dict(type='final', item_id='a', text='Hello there!', stash='', words=[word]))
        self.assertEqual((await stream.receive())['text'], 'new')

    async def test_pcm_encoding_and_limits(self):
        stream, ws, _ = await self.opened()
        pcm = b'\x01\x00' * 16000
        await stream.send_audio(pcm)
        event = ws.sent[-1]
        self.assertEqual(event['type'], 'input_audio_buffer.append')
        self.assertNotEqual(event['event_id'], ws.sent[0]['event_id'])
        self.assertEqual(base64.b64decode(event['audio']), pcm)
        for invalid in (b'', b'1', b'0' * 32002, '00', bytearray(b'00')):
            with self.subTest(invalid=type(invalid)), self.assertRaises(ValueError):
                await stream.send_audio(invalid)

    async def test_optional_wav(self):
        stream, ws, _ = await self.opened(wire_format='wav')
        pcm = b'\x12\x34' * 32
        await stream.send_audio(pcm)
        with wave.open(io.BytesIO(base64.b64decode(ws.sent[-1]['audio'])), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            self.assertEqual(wav.readframes(32), pcm)

    async def test_open_waits_for_updated_and_times_out(self):
        ws = FakeSocket([{'type': 'session.created'}])
        stream = StepStream(URL, 'key', connector=FakeConnector(ws))
        stream.OPEN_TIMEOUT = .01
        with self.assertRaisesRegex(RuntimeError, '^step_stream_open_timeout$'):
            await stream.open()
        self.assertTrue(ws.closed)
        self.assertIsNone(stream._ws)

    async def test_open_connector_error_sanitized(self):
        async def fail(*args, **kwargs):
            raise OSError('Authorization Bearer TOP-SECRET')
        stream = StepStream(URL, 'TOP-SECRET', connector=fail)
        with self.assertRaisesRegex(RuntimeError, '^step_stream_open_failed$'):
            await stream.open()

    async def test_provider_and_transport_errors_sanitized(self):
        for event, code in (({'type': 'error', 'error': {'code': 'TOP-SECRET', 'message': 'TOP-SECRET'}}, 'provider_error'),
                            (OSError('TOP-SECRET'), 'receive_failed'),
                            (RuntimeError('TOP-SECRET'), 'receive_failed'),
                            ('not json TOP-SECRET', 'invalid_json'),
                            ('[]', 'invalid_event'),
                            ('x' * (128 * 1024 + 1), 'event_too_large')):
            with self.subTest(code=code):
                stream, _, _ = await self.opened([event])
                with self.assertRaisesRegex(RuntimeError, '^step_stream_' + code + '$'):
                    await stream.receive()

    async def test_bounds_and_invalid_words(self):
        good = dict(word='word', start=0, end=1, final=False)
        invalid = [None, {}, dict(good, start=-1), dict(good, end=-1), dict(good, start=True),
                   dict(good, final=1), dict(good, end=float('inf')), dict(good, end=float('nan'))]
        stream, _, _ = await self.opened([{'type': PREFIX + 'delta', 'item_id': 'a' * 300,
            'text': 'x' * 13000, 'stash': 'y' * 13000, 'words': invalid + [good]}])
        event = await stream.receive()
        self.assertEqual(len(event['text']), 12000)
        self.assertEqual(len(event['stash']), 12000)
        self.assertEqual(len(event['item_id']), 256)
        self.assertEqual(event['words'], [good])
        self.assertEqual(len(StepStream._words([good] * 3000)), 2048)

    async def test_close_bounded_idempotent_and_not_ready(self):
        stream, ws, _ = await self.opened()
        ws.block_close = True
        stream.CLOSE_TIMEOUT = .01
        await asyncio.wait_for(stream.close(), .5)
        await stream.close()
        self.assertTrue(ws.closed)
        with self.assertRaisesRegex(RuntimeError, 'not_ready'):
            await stream.receive()
        with self.assertRaisesRegex(RuntimeError, 'not_ready'):
            await stream.send_audio(b'00')

    async def test_send_error_sanitized(self):
        stream, ws, _ = await self.opened()
        async def fail(_):
            raise OSError('TOP-SECRET')
        ws.send = fail
        with self.assertRaisesRegex(RuntimeError, '^step_stream_send_failed$'):
            await stream.send_audio(b'00')

    async def test_late_cancellation_resistant_connector_is_closed(self):
        ws = FakeSocket([{'type': 'session.updated'}])
        entered = asyncio.Event()
        released = asyncio.Event()
        async def resistant_connector(*args, **kwargs):
            entered.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                await released.wait()
            return ws
        stream = StepStream(URL, 'key', connector=resistant_connector)
        task = asyncio.create_task(stream.open())
        await entered.wait()
        task.cancel()
        await stream.close()
        released.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, .5)
        self.assertTrue(ws.closed)
        self.assertEqual(ws.sent, [])
        self.assertIsNone(stream._ws)
        self.assertFalse(stream._ready)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            await stream.open()

    async def test_close_fences_pending_open_without_cancellation(self):
        ws = FakeSocket([{'type': 'session.updated'}])
        entered = asyncio.Event()
        released = asyncio.Event()
        async def connector(*args, **kwargs):
            entered.set()
            await released.wait()
            return ws
        stream = StepStream(URL, 'key', connector=connector)
        task = asyncio.create_task(stream.open())
        await entered.wait()
        with self.assertRaisesRegex(RuntimeError, 'already_open'):
            await stream.open()
        await stream.close()
        released.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(ws.closed)
        self.assertEqual(ws.sent, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
