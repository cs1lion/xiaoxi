"""Deliver an already generated reply without model, storage or app imports.

Audio events match the streaming dialogue contract. ``done`` confirms generation
and synthesis only; it never claims that the browser played or the user heard it.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, AsyncIterator, Callable


def _consume(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()


async def deliver_reply(
    text: str,
    tts: Any,
    *,
    emotion: str = "base",
    voice_emotion: str | None = None,
    is_current: Callable[[], bool] | None = None,
    max_audio_chunks: int = 128,
    max_audio_bytes: int = 16 * 1024 * 1024,
    max_audio_chunk_bytes: int = 9 * 1024 * 1024,
    audio_timeout: float = 35.0,
) -> AsyncIterator[dict[str, Any]]:
    """Yield emotion, exact text, bounded raw audio and delivery status.

    The voice override changes synthesis, while event emotion continues to drive
    the character. Empty audio is deliberate silence, not a playable chunk.
    A failed provider keeps the text available. Cancellation or a stale caller
    never emits a late audio, error or done event.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not isinstance(emotion, str) or not emotion or len(emotion) > 32:
        raise ValueError("emotion must be a nonempty bounded string")
    if voice_emotion is not None and (not isinstance(voice_emotion, str) or len(voice_emotion) > 32):
        raise ValueError("voice_emotion must be a bounded string or None")
    for limit in (max_audio_chunks, max_audio_bytes, max_audio_chunk_bytes):
        if type(limit) is not int or limit < 0:
            raise ValueError("audio limits must be nonnegative integers")
    if (isinstance(audio_timeout, bool) or not isinstance(audio_timeout, (int, float))
            or not math.isfinite(audio_timeout) or audio_timeout <= 0):
        raise ValueError("audio_timeout must be positive finite seconds")
    if is_current is not None and not callable(is_current):
        raise ValueError("is_current must be callable or None")

    def current() -> bool:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        return is_current is None or bool(is_current())

    if not current():
        return
    yield {"type": "emotion", "emotion": emotion}
    if not current():
        return
    yield {"type": "text_delta", "delta": text}
    if not current():
        return

    succeeded = failed = total_bytes = observed = 0
    iterator = None
    pending = None
    try:
        if text and tts is not None and getattr(tts, "engine", None) != "silent":
            if getattr(tts, "offline", False):
                raise ValueError("Audio provider is unavailable")
            iterator = tts.speak(text, emotion=voice_emotion or emotion).__aiter__()
            while current():
                pending = asyncio.ensure_future(anext(iterator))
                ready, _ = await asyncio.wait({pending}, timeout=audio_timeout)
                if not ready:
                    raise TimeoutError("Audio provider deadline")
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    break
                if not current():
                    return
                audio = chunk.audio
                if not isinstance(audio, bytes):
                    raise ValueError("Audio must be raw bytes")
                observed += 1
                total_bytes += len(audio)
                # Base64 expands 9 MiB of raw audio to the browser's 12 MiB cap.
                if (observed > max_audio_chunks or total_bytes > max_audio_bytes
                        or len(audio) > max_audio_chunk_bytes):
                    raise ValueError("Audio bounds exceeded")
                if not isinstance(chunk.text, str):
                    raise ValueError("Audio text must be a string")
                if not audio:
                    continue
                event = {"type": "audio", "audio": audio, "index": succeeded,
                         "text": chunk.text, "emotion": emotion}
                # 协议第十一节：固定回复与流式路径共用同一微句切分，chunk 字段
                # 由 provider 的 SpeechChunk 带上。没有该字段（旧 provider）时
                # 事件与打磨前逐字节一致。
                envelope = getattr(chunk, "chunk", None)
                if isinstance(envelope, dict):
                    event["chunk"] = {key: envelope[key] for key in
                                      ("seq", "of", "chars", "pause_after_ms", "breath")
                                      if key in envelope}
                yield event
                succeeded += 1
    except Exception:
        failed = 1
        if not current():
            return
        yield {"type": "error", "code": "reply_tts_failed", "recoverable": True,
               "message": "音频暂不可用，文字回复已保留。"}
    finally:
        # Do not wait indefinitely for a provider that swallows cancellation.
        # Closing a running async generator is deferred until its pull settles.
        def close_iterator(task: asyncio.Future | None = None) -> None:
            if task is not None:
                _consume(task)
            close = getattr(iterator, "aclose", None)
            if close is not None:
                closing = asyncio.ensure_future(close())
                closing.add_done_callback(_consume)

        if pending is not None and not pending.done():
            pending.cancel()
            pending.add_done_callback(close_iterator)
        elif iterator is not None:
            close_iterator()

    if not current():
        return
    yield {"type": "done", "text": text, "emotion": emotion,
           "delivery": {"text_status": "complete",
                        "audio_status": ("partial" if succeeded else "failed") if failed
                                        else ("synthesized" if succeeded else "none"),
                        "audio_failed_chunks": failed,
                        "audio_succeeded_chunks": succeeded,
                        "playback_confirmed": False}}
