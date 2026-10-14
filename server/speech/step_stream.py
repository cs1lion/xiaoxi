"""Explicit, opt-in StepFun streaming ASR transport.

Protocol reference: https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream.md
The reference describes WAV uploads but configures PCM. ``wire_format='pcm'``
therefore sends raw PCM matching the session format; ``'wav'`` wraps each chunk
in a mono 16 kHz WAV container. Neither choice has been verified against the live
service. This module does not derive credentials or URLs from any LLM settings.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import uuid
import wave
from urllib.parse import urlsplit


class StepStream:
    MAX_MESSAGE = 128 * 1024
    MAX_TEXT = 12000
    MAX_WORDS = 2048
    OPEN_TIMEOUT = 10.0
    CLOSE_TIMEOUT = 2.0

    def __init__(self, url, key, wire_format='pcm', connector=None):
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            valid_url = (parsed is not None and parsed.scheme == 'wss'
                         and parsed.hostname and not parsed.username
                         and not parsed.password and not parsed.fragment
                         and not any(c.isspace() for c in url))
            if parsed is not None:
                parsed.port  # Validate malformed ports without exposing their value.
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError('step_stream_requires_explicit_wss_url')
        if not isinstance(key, str) or not key.strip() or any(c in key for c in '\r\n'):
            raise ValueError('step_stream_requires_api_key')
        if wire_format not in ('pcm', 'wav'):
            raise ValueError('step_stream_invalid_wire_format')
        self._url = url
        self._key = key.strip()
        self.wire_format = wire_format
        self._connector = connector
        self._ws = None
        self._ready = False
        self._closed = False
        self._opening = False

    @staticmethod
    def _event(event_type, **fields):
        return {'event_id': uuid.uuid4().hex, 'type': event_type, **fields}

    async def open(self):
        if self._closed:
            raise RuntimeError('step_stream_closed')
        if self._ws is not None or self._opening:
            raise RuntimeError('step_stream_already_open')
        self._opening = True
        deadline = asyncio.get_running_loop().time() + self.OPEN_TIMEOUT
        try:
            async with asyncio.timeout(self.OPEN_TIMEOUT):
                connector = self._connector
                if connector is None:
                    from websockets.asyncio.client import connect
                    connector = connect
                self._ws = await connector(
                    self._url,
                    additional_headers={'Authorization': 'Bearer ' + self._key},
                    max_size=self.MAX_MESSAGE,
                    max_queue=16,
                    open_timeout=self.OPEN_TIMEOUT,
                    close_timeout=self.CLOSE_TIMEOUT,
                )
                # A connector may swallow cancellation and return after owner close.
                # Attach first so cleanup owns that late socket, then fence all use.
                if self._closed or asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                await self._send(self._event('session.update', session={
                    'audio': {'input': {
                        'format': {'type': 'pcm', 'codec': 'pcm_s16le',
                                   'rate': 16000, 'bits': 16, 'channel': 1},
                        'transcription': {
                            'model': 'stepaudio-2.5-asr-stream', 'language': 'en',
                            'full_rerun_on_commit': True, 'enable_itn': True,
                            'enable_timestamp_align': True,
                        },
                        'turn_detection': {'type': 'server_vad',
                                           'silence_duration_ms': 800,
                                           'threshold': 0.6},
                    }},
                }))
                while True:
                    event = await self._read_event()
                    if self._closed or asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError
                    if event.get('type') == 'session.updated':
                        self._ready = True
                        return self
        except asyncio.CancelledError:
            await self.close()
            raise
        except TimeoutError:
            await self.close()
            raise RuntimeError('step_stream_open_timeout') from None
        except Exception:
            await self.close()
            raise RuntimeError('step_stream_open_failed') from None
        finally:
            self._opening = False

    async def _send(self, event):
        await self._ws.send(json.dumps(event, separators=(',', ':'), ensure_ascii=False))

    async def send_audio(self, pcm):
        if not isinstance(pcm, bytes) or not pcm or len(pcm) > 32000 or len(pcm) % 2:
            raise ValueError('step_stream_invalid_pcm_chunk')
        if not self._ready or self._ws is None:
            raise RuntimeError('step_stream_not_ready')
        payload = pcm
        if self.wire_format == 'wav':
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm)
            payload = output.getvalue()
        try:
            await self._send(self._event('input_audio_buffer.append',
                                        audio=base64.b64encode(payload).decode('ascii')))
        except Exception:
            raise RuntimeError('step_stream_send_failed') from None

    async def _read_event(self):
        raw = await self._ws.recv()
        if not isinstance(raw, (str, bytes)):
            raise RuntimeError('step_stream_invalid_event')
        if len(raw) > self.MAX_MESSAGE or (isinstance(raw, str) and
                                          len(raw.encode('utf-8')) > self.MAX_MESSAGE):
            raise RuntimeError('step_stream_event_too_large')
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeError('step_stream_invalid_json') from None
        if not isinstance(event, dict):
            raise RuntimeError('step_stream_invalid_event')
        if event.get('type') == 'error':
            # Never reflect arbitrary server messages/codes: they may echo credentials.
            raise RuntimeError('step_stream_provider_error')
        return event

    @classmethod
    def _text(cls, value):
        return value[:cls.MAX_TEXT] if isinstance(value, str) else ''

    @classmethod
    def _words(cls, value):
        result = []
        if not isinstance(value, list):
            return result
        remaining = cls.MAX_TEXT
        for entry in value[:cls.MAX_WORDS]:
            if not isinstance(entry, dict):
                continue
            word, start, end, final = (entry.get(k) for k in ('word', 'start', 'end', 'final'))
            if (not isinstance(word, str) or not word or len(word) > remaining
                    or type(start) not in (int, float) or type(end) not in (int, float)
                    or (isinstance(start, float) and not math.isfinite(start))
                    or (isinstance(end, float) and not math.isfinite(end))
                    or start < 0 or end < start or not isinstance(final, bool)):
                continue
            result.append({'word': word, 'start': start, 'end': end, 'final': final})
            remaining -= len(word)
        return result

    async def receive(self):
        if not self._ready or self._ws is None:
            raise RuntimeError('step_stream_not_ready')
        try:
            while True:
                event = await self._read_event()
                kind = event.get('type')
                if kind not in ('conversation.item.input_audio_transcription.delta',
                                'conversation.item.input_audio_transcription.completed'):
                    continue
                final = kind.endswith('.completed')
                # Each delta is a fresh cumulative hypothesis, not an append operation.
                # Keep the unsure suffix separate; never guess overlaps or deduplicate.
                extra = event.get('extra')
                stash = event.get('stash', extra if isinstance(extra, str) else '')
                if isinstance(extra, dict):
                    stash = event.get('stash', extra.get('stash', ''))
                return {
                    'type': 'final' if final else 'partial',
                    'item_id': self._text(event.get('item_id'))[:256],
                    'text': self._text(event.get('transcript') if final else event.get('text')),
                    'stash': '' if final else self._text(stash),
                    'words': self._words(event.get('words')),
                }
        except RuntimeError as exc:
            # Only locally generated error codes are allowed out of the adapter.
            if str(exc) in ('step_stream_provider_error', 'step_stream_invalid_event',
                            'step_stream_event_too_large', 'step_stream_invalid_json'):
                raise RuntimeError(str(exc)) from None
            raise RuntimeError('step_stream_receive_failed') from None
        except Exception:
            raise RuntimeError('step_stream_receive_failed') from None

    async def close(self):
        self._closed = True
        ws, self._ws = self._ws, None
        self._ready = False
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=self.CLOSE_TIMEOUT)
            except Exception:
                pass
