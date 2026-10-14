from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Any

import numpy as np
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from .static import AppStaticFiles

from .config import ROOT, avatar_cfg, ensure_dirs, pacing_cfg, persona_cfg, settings
from .dialogue.orchestrator import EMOTIONS, orchestrator
from .dialogue.delivery import deliver_reply
from .llm import llm
from .memory.manager import manager
from .memory.store import store
from .speech.asr import recognizer, to_wav_bytes

ensure_dirs()

app = FastAPI(title="聊天角色系统", version="0.1.0")

from .assistant.service import AssistantService
from .assistant.api import create_router, scheduler

assistant = AssistantService(ROOT / "data", llm, manager)
orchestrator.context_broker = assistant.context_broker
orchestrator.activity_facade = assistant.activity
app.include_router(create_router(assistant))
from .interaction.threads_api import create_threads_router
app.include_router(create_threads_router(assistant))
from .interaction.proposals_api import create_proposals_router
app.include_router(create_proposals_router(assistant))
from .assistant.note_proposals_api import create_note_proposals_router
app.include_router(create_note_proposals_router(assistant))
from .speech.music_api import create_music_router
app.include_router(create_music_router(settings))
from .speech.tts import tts
from .speech.taste_api import create_taste_router
app.include_router(create_taste_router(tts))
from .assistant.editor_insights import EditorInsightService
from .assistant.editor_insights_api import create_editor_insights_router
editor_insights = EditorInsightService(llm, enabled=lambda: manager.enabled)
app.include_router(create_editor_insights_router(editor_insights, tts))
from .memory.api import create_diary_router, create_starmap_router
app.include_router(create_diary_router(manager))
app.include_router(create_starmap_router(manager))
_scheduler_task: asyncio.Task | None = None


@app.websocket('/ws/interpret')
async def interpretation_socket(ws: WebSocket) -> None:
    from .speech.interpretation import serve_interpretation
    await serve_interpretation(ws, recognizer, llm)


@app.websocket('/ws/interpret-stream')
async def stream_interpretation_socket(ws: WebSocket) -> None:
    from .speech.step_stream import StepStream
    from .speech.stream_interpretation import serve_stream_interpretation
    def factory():
        return StepStream(settings.asr_stream_url, settings.asr_stream_api_key,
                          wire_format=settings.asr_stream_wire_format)
    await serve_stream_interpretation(ws, llm, factory)


# ---------------------------------------------------------------- REST: memory
@app.get("/api/persona")
async def get_persona() -> dict[str, Any]:
    cfg = persona_cfg()
    return {
        "name": cfg["name"],
        "tagline": cfg["tagline"],
        "tendencies": cfg["tendencies"],
        "voices": list(cfg["tts_emotions"].keys()),
        "default_voice": settings.tts_default_emotion if settings.tts_default_emotion in EMOTIONS else "base",
        "style": cfg["voice_style"],
    }


@app.get("/api/avatar")
async def get_avatar() -> Any:
    path = ROOT / "config" / "avatar.yaml"
    if not path.exists():
        return JSONResponse({"error": "avatar.yaml missing"}, status_code=404)
    return json.loads(json.dumps(yaml.safe_load(path.read_text(encoding="utf-8"))))


@app.get("/api/avatar")
async def get_avatar() -> dict[str, Any]:
    """姿态契约。前端据此构建参数表、表情目标和手势轨道。"""
    return avatar_cfg()


@app.get("/api/memories")
async def list_memories(include_history: bool = False, kind: str | None = None, limit: int = 200) -> Any:
    return {
        "items": manager.snapshot(include_history=include_history, limit=limit),
        "count": store.count(),
        "enabled": manager.enabled,
    }

@app.get("/api/memories/context")
async def memories_context(query: str, subject: str | None = None, limit: int = 10) -> Any:
    """获取与查询相关的记忆，用于驱动表情/动作"""
    if not manager.enabled:
        return {"items": [], "found": False}
    block, found = manager.context_with_status(query, subject=subject)
    items = manager.snapshot(include_history=False, limit=limit)
    # 过滤相关记忆
    relevant = [m for m in items if any(kw in m["value"].lower() for kw in query.lower().split())]
    return {
        "items": relevant[:limit],
        "found": found,
        "context": block,
    }

@app.patch("/api/memories/{memory_id}")
async def correct_memory(memory_id: int, payload: dict[str, Any]) -> Any:
    value = (payload.get("value") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="value required")
    memory = manager.correct(memory_id, value)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory.to_dict()


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: int, purge_history: bool = True) -> Any:
    removed = manager.forget(memory_id, purge_history=purge_history)
    if not removed:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"deleted": memory_id, "removed_rows": removed}


@app.get("/api/memories/history")
async def memory_history(key: str) -> Any:
    return {"key": key, "items": manager.history_of(key)}


@app.post("/api/memories/forget-key")
async def forget_key(payload: dict[str, Any]) -> Any:
    key = (payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    return {"deleted": manager.forget_key(key)}


@app.post("/api/memory/toggle")
async def toggle_memory(payload: dict[str, Any]) -> Any:
    manager.enabled = bool(payload.get("enabled", True))
    return {"enabled": manager.enabled}


@app.post("/api/memory/reset")
async def reset_memory(payload: dict[str, Any]) -> Any:
    count = manager.forget_all(kind=payload.get("kind"), subject=payload.get("subject"))
    return {"deleted": count}


@app.post("/api/memory/manual")
async def add_memory(payload: dict[str, Any]) -> Any:
    value = (payload.get("value") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="value required")
    memory = manager.remember(
        kind=payload.get("kind", "fact"),
        subject=payload.get("subject", "user"),
        key=payload.get("key") or value,
        value=value,
        source=payload.get("source", "stated"),
        importance=float(payload.get("importance", 0.6)),
        confidence=float(payload.get("confidence", 0.9)),
    )
    return memory.to_dict() if memory else None


@app.get("/api/session/{session_id}/transcript")
async def transcript(session_id: str, limit: int = 100) -> Any:
    return {"items": store.recent_messages(session_id, limit=limit)}


@app.get("/api/health")
async def health() -> Any:
    return {
        "ok": True,
        "llm": {
            "provider": llm.provider,
            "model": llm.model,
            "extract_model": settings.llm_extract_model or llm.model,
            "offline": llm.offline,
        },
        "tts": {"engine": settings.tts_engine, "model": settings.tts_model, "voice": settings.tts_voice},
        "asr": {"engine": settings.asr_engine, "model": settings.asr_model_id if settings.asr_engine == "stepaudio" else settings.asr_model},
        "memories": store.count(),
        "proactivity": orchestrator.state["proactivity_enabled"],
    }


# ---------------------------------------------------------------- WebSocket
MAX_AUDIO_SECONDS = 30


class VoiceTurn:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.live = True
        self.editor_insight: dict[str, Any] | None = None


class Connection:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.session_id = uuid.uuid4().hex[:12]
        self.buffer = bytearray()
        self.recording = False
        self.task: asyncio.Task | None = None
        self.playing = False
        self.proactivity = False
        default_voice = getattr(settings, "tts_default_emotion", "base")
        self.emotion_override = default_voice if default_voice in EMOTIONS else "base"
        self.last_spoke_at = time.time()
        self.turn: VoiceTurn | None = None
        self.closed = False
        self.revision = 0
        self.tasks: set[asyncio.Task] = set()
        self.send_lock = asyncio.Lock()

    def current(self, turn: VoiceTurn) -> bool:
        return not self.closed and self.turn is turn and turn.live

    def editor_current(self, turn: VoiceTurn) -> bool:
        if turn.editor_insight is None:
            return True
        try:
            editor_insights.context(**turn.editor_insight)
            return True
        except Exception:
            return False

    async def bind_editor_insight(self, turn: VoiceTurn, event: dict[str, Any]) -> bool:
        if 'editor_insight' not in event:
            return True
        context = event['editor_insight']
        try:
            from urllib.parse import urlsplit
            headers = getattr(self.ws, 'headers', {})
            origin = urlsplit(headers.get('origin', ''))
            scheme = 'https' if getattr(getattr(self.ws, 'url', None), 'scheme', 'ws') == 'wss' else 'http'
            if origin.scheme != scheme or origin.netloc != headers.get('host'):
                raise ValueError('same_origin_required')
            if (not isinstance(context, dict)
                    or set(context) != {'insight_id', 'session_id', 'draft_revision'}
                    or any(not isinstance(context[key], str) or not 0 < len(context[key]) <= 100
                           for key in ('insight_id', 'session_id'))
                    or type(context['draft_revision']) is not int
                    or not 0 <= context['draft_revision'] <= 2 ** 53 - 1):
                raise ValueError('invalid_editor_context')
            insight = editor_insights.context(**context)
            turn.editor_insight = dict(context)
            await self.send_turn(turn, {'type': 'editor_discussion', 'insight': insight})
            return True
        except Exception:
            await self.finish(turn, {'type': 'error', 'code': 'editor_discussion_unavailable',
                                    'scope': 'editor_insight', 'editor_context_valid': False, 'terminal': True,
                                    'message': '刚才的观点已失效。回到稿子继续编辑后，再选择一条观点讨论。'})
            return False

    @property
    def busy(self) -> bool:
        return self.recording or self.playing or bool(self.task and not self.task.done())

    def spawn(self, coroutine: Any) -> None:
        self.task = asyncio.create_task(coroutine)
        self.tasks.add(self.task)
        self.task.add_done_callback(self._settled)

    def _settled(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Consume transport failures after a disconnect.

    async def send(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            if not self.closed:
                await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def send_turn(self, turn: VoiceTurn, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            if self.current(turn):
                if not self.editor_current(turn) and payload.get('type') not in {'error', 'interrupted', 'playback_stopped'}:
                    if payload.get('type') != 'done':
                        return
                    payload = {'type': 'error', 'code': 'editor_discussion_unavailable',
                               'scope': 'editor_insight', 'editor_context_valid': False, 'terminal': True,
                               'message': '稿子已经变化，刚才的讨论已停止。回到稿子后再看看。'}
                await self.ws.send_text(json.dumps({**payload, "turn_id": turn.turn_id}, ensure_ascii=False))

    async def finish(self, turn: VoiceTurn, payload: dict[str, Any]) -> None:
        if self.current(turn):
            self.recording = False
            self.playing = False
            self.buffer.clear()
            await self.send_turn(turn, payload)
            turn.live = False

    async def cancel(self, *, playback: bool = False) -> None:
        self.revision += 1
        turn = self.turn
        was_live = turn is not None and turn.live
        if turn is not None:
            turn.live = False
        if self.task is not asyncio.current_task():
            if self.task and not self.task.done():
                self.task.cancel()
            self.task = None
        # A legacy three-argument _run_turn may itself be conn.task.
        # Keep tracking that task while begin() invalidates the previous turn.
        self.recording = False
        self.playing = False
        self.buffer.clear()
        if was_live:
            # Stops response delivery; does not claim to undo executed tools.
            await self.send({"type": "interrupted", "turn_id": turn.turn_id})
        if turn is not None and playback:
            await self.send({"type": "playback_stopped", "turn_id": turn.turn_id})

    async def begin(self, turn_id: Any = None) -> VoiceTurn:
        await self.cancel()
        turn = VoiceTurn(turn_id if isinstance(turn_id, str) and turn_id else "server-" + uuid.uuid4().hex)
        self.turn = turn
        self.last_spoke_at = time.time()
        return turn

    def matches(self, event: dict[str, Any]) -> bool:
        return self.turn is not None and (event.get("turn_id") is None or event["turn_id"] == self.turn.turn_id)

    async def close(self) -> None:
        self.closed = True
        await self.cancel()
        for task in list(self.tasks):
            task.cancel()
        # Do not wait for cancellation-resistant providers or close shared llm.


async def _present_reply(conn: Connection, turn: VoiceTurn, text: str, emotion: str,
                         *, voice_emotion: str | None = None) -> None:
    """Speak an existing result once, using the same turn fence as live dialogue."""
    started = time.monotonic()
    reply = deliver_reply(text, tts, emotion=emotion,
                          voice_emotion=voice_emotion or conn.emotion_override or None,
                          is_current=lambda: conn.current(turn) and conn.editor_current(turn))
    try:
        async for event in reply:
            if not conn.current(turn):
                return
            if not conn.editor_current(turn):
                break
            payload = dict(event)
            if event['type'] == 'audio':
                audio = payload.pop('audio')
                payload = {**payload, 'type': 'audio', 'data': base64.b64encode(audio).decode('ascii')}
            if event['type'] == 'done':
                payload['latency_ms'] = int((time.monotonic() - started) * 1000)
                await conn.finish(turn, payload)
                return
            await conn.send_turn(turn, payload)
    finally:
        await reply.aclose()
    if conn.current(turn) and not conn.editor_current(turn):
        await conn.finish(turn, {'type': 'error', 'code': 'editor_discussion_unavailable',
                                'scope': 'editor_insight', 'editor_context_valid': False, 'terminal': True,
                                'message': '稿子已经变化，刚才的讨论已停止。回到稿子后再看看。'})


async def _confirm_action(conn: Connection, turn: VoiceTurn, event: dict[str, Any]) -> None:
    """Confirm only this connection's parameter-bound action; never replay a tool."""
    try:
        if not conn.current(turn):
            return
        run = assistant.runtime.get(event['run_id'])
        if run['state']['context'].get('interaction_session_id') != conn.session_id:
            raise ValueError('confirmation_session_mismatch')
        conn.playing = True
        await conn.send_turn(turn, {'type': 'thinking'})
        result = await assistant.resume(event['run_id'], event['confirmation_id'], event['approve'])
        if not conn.current(turn):
            return
        from .dialogue.orchestrator import store_message
        store_message(conn.session_id, 'assistant', result['text'], 'assistant_action')
        await conn.send_turn(turn, {'type': 'assistant_result', 'result': result})
        await _present_reply(conn, turn, result['text'], '认真')
    except asyncio.CancelledError:
        await conn.finish(turn, {'type': 'interrupted'})
        raise
    except (KeyError, ValueError):
        await conn.finish(turn, {'type': 'error', 'code': 'assistant_confirmation_invalid',
                                'message': '这次确认已失效或不属于当前对话。请查看任务面板中的实际结果。',
                                'terminal': True})
    except Exception:
        await conn.finish(turn, {'type': 'error', 'code': 'assistant_confirmation_failed',
                                'message': '未能取得确认结果，请查看任务面板；不会自动重试。',
                                'terminal': True})


async def _narrate_turn(conn: Connection, turn: VoiceTurn, node_id: str) -> None:
    """Star-map narration: one bounded retelling of a memory node (protocol section 13)."""
    from .dialogue.orchestrator import detect_emotion, store_message
    from .memory.starmap import narrate_opening, starmap_node
    try:
        if not conn.current(turn):
            return
        if not manager.enabled:
            await conn.finish(turn, {"type": "error", "code": "narrate_unavailable",
                                     "message": "记忆已关闭，打开记忆后就能听小溪讲了。", "terminal": True})
            return
        record = starmap_node(manager, node_id)
        if record is None:
            await conn.finish(turn, {"type": "error", "code": "narrate_unavailable",
                                     "message": "这条记忆已经不在了。", "terminal": True})
            return
        await conn.send_turn(turn, {"type": "turn_started", "source": "narration"})
        await conn.send_turn(turn, {"type": "thinking"})
        text = await narrate_opening(record, llm)
        if not conn.current(turn):
            return
        if not text:
            await conn.finish(turn, {"type": "error", "code": "narrate_unavailable",
                                     "message": "这条记忆暂时讲不出来。", "terminal": True})
            return
        store_message(conn.session_id, "assistant", text, "narration")
        await _present_reply(conn, turn, text, detect_emotion(text))
    except asyncio.CancelledError:
        await conn.finish(turn, {"type": "interrupted"})
        raise
    except Exception as exc:
        await conn.finish(turn, {"type": "error", "message": str(exc), "terminal": True})


async def _maybe_diary(conn: Connection) -> None:
    """Background episodic diary synthesis (protocol section 10); never blocks a turn."""
    if not manager.enabled:
        return
    try:
        from .memory.diary import diary_notice_payload, synthesize_diary
        result = await synthesize_diary(provider=llm)
        if result is None or conn.closed:
            return
        payload = diary_notice_payload(result.get("notice_items"))
        if payload is not None:
            await conn.send(payload)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def _maybe_self_evolution(conn: Connection) -> None:
    """One bounded idle self-evolution cycle; events are quiet and connection-level."""
    try:
        from .assistant.loop import SelfEvolutionCycle
        from .memory.evolution import self_evolution_cfg, self_evolution_payload
        if not self_evolution_cfg().get("enabled"):
            return
        outcome = await SelfEvolutionCycle(provider=llm).run(
            idle_seconds=time.time() - conn.last_spoke_at)
        for change in outcome.get("events") or []:
            payload = self_evolution_payload(change)
            if payload is not None and not conn.closed:
                await conn.send(payload)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def _run_turn(conn: Connection, text: str, mode: str, turn: VoiceTurn | None = None) -> None:
    if turn is None:
        turn = await conn.begin()
        await conn.send_turn(turn, {"type": "turn_started", "source": mode})
    if not conn.current(turn):
        return
    try:
        async def _notice(memories: list[Any]) -> None:
            await conn.send_turn(turn, {
                "type": "memory_notice",
                "items": [
                    {"id": m.id, "kind": m.kind, "value": m.value, "source": m.source}
                    for m in memories
                ],
            })

        conn.playing = True
        conn.last_spoke_at = time.time()
        if turn.editor_insight is not None:
            await conn.send_turn(turn, {'type': 'thinking', 'scope': 'editor_insight'})
            try:
                result = await editor_insights.reply(**turn.editor_insight, text=text,
                    is_current=lambda: conn.current(turn) and conn.editor_current(turn))
                if not conn.current(turn):
                    return
                await _present_reply(conn, turn, result['text'], 'base', voice_emotion='base')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                messages = {
                    'insight_discussion_limit': '这条观点已聊了几轮。先回到稿子继续写，之后再看看。',
                    'insight_discussion_timeout': '这次讨论等得有点久。你的稿子没变，可以稍后再问。',
                    'insight_discussion_output_truncated': '这次回复没有完整生成。你的输入保留，可以再问一次。',
                    'insight_discussion_empty_response': '这次没有收到回复。你的输入保留，可以再问一次。',
                    'insight_discussion_invalid_reply': '这次回复暂时无法使用。你的输入保留，可以再问一次。',
                    'sensitive_discussion': '这句话可能包含凭据，已跳过这次讨论。',
                    'discussion_must_be_1_to_1000_characters': '这次请用一段不超过 1000 字的话继续讨论。',
                }
                message = messages.get(getattr(exc, 'reason', str(exc)),
                    '这条观点暂时无法继续讨论。可以回到稿子，稍后再问。')
                await conn.finish(turn, {'type': 'error', 'code': 'editor_discussion_unavailable',
                                        'scope': 'editor_insight', 'editor_context_valid': conn.editor_current(turn),
                                        'message': message, 'terminal': True})
            return
        if mode == "proactive":
            # Already generated assistant speech: never feed it back as user input.
            await conn.send_turn(turn, {"type": "thinking"})
            await _present_reply(conn, turn, text, 'base')
            return
        experience = getattr(conn, 'experience', None)
        if experience is not None and experience.mode != 'off':
            from .speech.tts import tts
            async for event in experience.respond(text, llm, tts, lambda: conn.current(turn),
                                                  voice_emotion=conn.emotion_override or None):
                if not conn.current(turn):
                    return
                payload = dict(event)
                if event['type'] == 'audio':
                    payload['data'] = base64.b64encode(payload.pop('audio')).decode('ascii')
                if event['type'] in ('done', 'error') and not event.get('recoverable'):
                    if event['type'] == 'error':
                        payload['terminal'] = True
                    await conn.finish(turn, payload)
                    return
                await conn.send_turn(turn, payload)
            await conn.finish(turn, {'type': 'done', 'text': '', 'latency_ms': 0})
            return
        practice = getattr(conn, 'practice', None)
        if practice is not None and practice.mode != 'off':
            from .speech.tts import tts
            events = (practice.review(llm, lambda: conn.current(turn)) if mode == 'practice_review'
                      else practice.respond(text, llm, tts, lambda: conn.current(turn)))
            async for event in events:
                if not conn.current(turn):
                    return
                payload = dict(event)
                if event['type'] == 'audio':
                    payload['data'] = base64.b64encode(payload.pop('audio')).decode('ascii')
                if event['type'] in ('done', 'error') and not event.get('recoverable'):
                    if event['type'] == 'error':
                        payload['terminal'] = True
                    await conn.finish(turn, payload)
                    return
                await conn.send_turn(turn, payload)
            await conn.finish(turn, {'type': 'done', 'text': '', 'latency_ms': 0})
            return
        if assistant.should_route(text, conn.session_id) or assistant.waiting_input(conn.session_id):
            from .dialogue.orchestrator import store_message, store_recent
            await conn.send_turn(turn, {"type": "thinking"})
            result = await assistant.start(text, conn.session_id,
                history=store_recent(conn.session_id, 3) if manager.enabled else [])
            if not conn.current(turn):
                return
            if result.get('status') != 'not_applicable':
                store_message(conn.session_id, "user", text, mode)
                store_message(conn.session_id, "assistant", result["text"], "assistant_action")
                await conn.send_turn(turn, {"type": "assistant_result", "result": result})
                await _present_reply(conn, turn, result['text'], '认真')
                return
        async for event in orchestrator.stream_respond(
            session_id=conn.session_id, user_text=text, on_memories=_notice,
            voice_emotion=conn.emotion_override or None,
        ):
            if not conn.current(turn):
                return
            kind = event["type"]
            payload = dict(event)
            if kind == "audio":
                # Preserve the sentence's validated performance plan and any
                # provider word timings through the WebSocket boundary.
                payload["data"] = base64.b64encode(payload.pop("audio")).decode("ascii")
            if kind == 'error' and event.get('recoverable'):
                await conn.send_turn(turn, payload)
                continue
            if kind in ("done", "error"):
                if kind == "error":
                    payload.setdefault("message", "error")
                    payload["terminal"] = True
                await conn.finish(turn, payload)
                conn.spawn(_maybe_diary(conn))
                return
            await conn.send_turn(turn, payload)
        # A provider that ends without a terminal event must not leave a live turn.
        conn.spawn(_maybe_diary(conn))
        await conn.finish(turn, {"type": "done", "text": "", "latency_ms": 0})
    except asyncio.CancelledError:
        await conn.finish(turn, {"type": "interrupted"})
        raise
    except Exception as exc:  # pragma: no cover - defensive
        await conn.finish(turn, {"type": "error", "message": str(exc), "terminal": True})


async def _transcribe_turn(conn: Connection, turn: VoiceTurn, data: bytes) -> None:
    try:
        if not conn.current(turn):
            return
        if len(data) % 2:
            raise ValueError("PCM16 audio must contain complete samples")
        if data:
            wav = to_wav_bytes(np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0, settings.audio_sample_rate)
            if (turn.editor_insight is None and
                    ((getattr(conn, 'practice', None) is not None and conn.practice.mode != 'off')
                    or (getattr(conn, 'experience', None) is not None and conn.experience.mode == 'expression'))):
                transcript = await recognizer.transcribe_wav(wav, language='en')
            else:
                transcript = await recognizer.transcribe_wav(wav)
            if not conn.current(turn):
                return
            text, duration = transcript.text.strip(), transcript.duration_ms
        else:
            text, duration = "", 0
        await conn.send_turn(turn, {"type": "asr", "text": text, "duration_ms": duration})
        if not text:
            await conn.finish(turn, {"type": "done", "text": "", "latency_ms": 0})
        else:
            await _run_turn(conn, text, "voice", turn)
    except asyncio.CancelledError:
        await conn.finish(turn, {"type": "interrupted"})
        raise
    except Exception as exc:
        await conn.finish(turn, {"type": "error", "message": str(exc), "terminal": True})


async def _proactive_check(conn: Connection, revision: int) -> None:
    try:
        await _maybe_self_evolution(conn)
        result = await orchestrator.proactive_turn(
            session_id=conn.session_id, idle_seconds=time.time() - conn.last_spoke_at,
        )
        source = "proactive"
        self_topic_turn = getattr(orchestrator, "self_topic_turn", None)
        if result is None and self_topic_turn is not None:
            result = await self_topic_turn(
                session_id=conn.session_id, idle_seconds=time.time() - conn.last_spoke_at,
            )
            source = "self_topic"
        # busy includes this check itself; revision fences even a user turn that
        # has already finished or was cancelled while the provider ignored cancel.
        if conn.closed or conn.revision != revision or not result:
            return
        turn = await conn.begin()
        await conn.send_turn(turn, {"type": "turn_started", "source": source})
        await _run_turn(conn, result.text, "proactive", turn)
        if source == "self_topic":
            spark_id = getattr(result, "spark_id", None)
            if spark_id:
                from .memory.diary import spend_spark
                spend_spark(store, spark_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        # There is no announced turn to fail when a background idle check fails.
        return


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    conn = Connection(ws)
    store.start_session(conn.session_id)
    await conn.send(
        {
            "type": "ready",
            "session_id": conn.session_id,
            "persona": persona_cfg()["name"],
            "sample_rate": settings.audio_sample_rate,
        }
    )
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (blob := message.get("bytes")) is not None:
                if conn.recording and conn.turn is not None:
                    if len(conn.buffer) + len(blob) > MAX_AUDIO_SECONDS * settings.audio_sample_rate * 2:
                        await conn.finish(conn.turn, {
                            "type": "error", "code": "audio_too_long", "terminal": True,
                            "message": "PCM16 audio exceeds the 30 second limit",
                        })
                    else:
                        conn.buffer.extend(blob)
                continue

            raw = message.get("text")
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")

            if kind == "start":
                turn = await conn.begin(event.get("turn_id"))
                if not await conn.bind_editor_insight(turn, event):
                    continue
                conn.recording = True
                await conn.send_turn(turn, {"type": "recording", "state": True})
            elif kind == "audio_end":
                if not conn.matches(event) or not conn.recording:
                    continue
                turn = conn.turn
                conn.recording = False
                data = bytes(conn.buffer)
                conn.buffer.clear()
                conn.spawn(_transcribe_turn(conn, turn, data))
            elif kind == "text":
                if not isinstance(event.get("text"), str):
                    continue
                text = event["text"].strip()
                if not text:
                    continue
                turn = await conn.begin(event.get("turn_id"))
                if not await conn.bind_editor_insight(turn, event):
                    continue
                await conn.send_turn(turn, {"type": "asr", "text": text, "duration_ms": 0})
                conn.spawn(_run_turn(conn, text, "text", turn))
            elif kind == "barge_in":
                if conn.matches(event) or (conn.turn is None and event.get("turn_id") is None):
                    await conn.cancel(playback=True)
            elif kind == "narrate":
                node_id = event.get("node_id")
                if not isinstance(node_id, str) or not 0 < len(node_id) <= 100:
                    continue
                if conn.busy:
                    # 连接级错误，不回显 turn_id：客户端挂在 TurnGate 之前消费（协议第十三节）
                    await conn.send({"type": "error", "code": "narrate_busy", "recoverable": True,
                                     "message": "小溪正在讲别的，稍等一下再点。"})
                    continue
                turn = await conn.begin(event.get("turn_id"))
                conn.spawn(_narrate_turn(conn, turn, node_id))
            elif kind == 'assistant_confirm':
                if (type(event.get('approve')) is not bool
                        or any(not isinstance(event.get(key), str) or not 0 < len(event[key]) <= 100
                               for key in ('run_id', 'confirmation_id'))):
                    continue
                turn = await conn.begin(event.get('turn_id'))
                await conn.send_turn(turn, {'type': 'turn_started', 'source': 'assistant_confirm'})
                conn.spawn(_confirm_action(conn, turn, event))
            elif kind == 'practice_review':
                if getattr(conn, 'practice', None) is not None and conn.practice.mode != 'off':
                    turn = await conn.begin(event.get('turn_id'))
                    conn.spawn(_run_turn(conn, '', 'practice_review', turn))
            elif kind == "config":
                if "emotion" in event:
                    voice_emotion = event["emotion"]
                    if not isinstance(voice_emotion, str) or (voice_emotion and voice_emotion not in EMOTIONS):
                        await conn.send({"type": "config_ack", "emotion": conn.emotion_override,
                                         "emotion_error": "无效的语气设置，未应用。"})
                        continue
                experience_ack = {}
                if 'experience' in event or 'experience_source' in event:
                    from .speech.experience import ExperienceSession
                    request_id = event.get('experience_request_id')
                    if isinstance(request_id, str) and 0 < len(request_id) <= 100:
                        experience_ack['experience_request_id'] = request_id
                    try:
                        if ('experience_request_id' in event
                                and 'experience_request_id' not in experience_ack):
                            raise ValueError('experience_request_id_invalid')
                        if 'practice' in event:
                            raise ValueError('choose_one_mode')
                        previous = getattr(conn, 'experience', None)
                        candidate = ExperienceSession()
                        if previous is not None:
                            candidate.configure(mode=previous.mode, scene=previous.scene)
                            candidate.set_source(previous.source)
                        config = event.get('experience', {})
                        if not isinstance(config, dict):
                            raise ValueError('experience_config_required')
                        candidate.configure(**config)
                        if 'experience_source' in event:
                            candidate.set_source(event['experience_source'])
                        if candidate.mode in ('listen', 'expression') and not candidate.source.get('text', '').strip():
                            raise ValueError('selected_source_required')
                    except (ValueError, TypeError):
                        await conn.send({'type': 'config_ack', **experience_ack,
                                         'experience_error': '设置无效；听看与原句练习需要选定原文，旧设置未改变。'})
                        continue
                    await conn.cancel(playback=True)
                    if previous is None:
                        conn.experience = candidate
                    else:
                        previous.configure(mode=candidate.mode, scene=candidate.scene)
                        previous.set_source(candidate.source)
                    if candidate.mode != 'off' and getattr(conn, 'practice', None):
                        conn.practice.configure(mode='off')
                if 'practice' in event:
                    from .speech.practice import PracticeSession
                    if getattr(conn, 'practice', None) is None:
                        conn.practice = PracticeSession()
                    try:
                        config = event['practice']
                        if not isinstance(config, dict):
                            raise ValueError('practice_config_required')
                        conn.practice.configure(**config)
                        if conn.practice.mode != 'off' and getattr(conn, 'experience', None):
                            conn.experience.configure(mode='off')
                        await conn.cancel(playback=True)
                    except (ValueError, TypeError):
                        await conn.send({'type': 'config_ack', 'practice_error': '无效的练习设置，未应用。'})
                        continue
                if "proactivity" in event:
                    conn.proactivity = bool(event["proactivity"])
                    orchestrator.set_proactivity(conn.proactivity)
                if "memory" in event:
                    manager.enabled = bool(event["memory"])
                if "emotion" in event:
                    conn.emotion_override = event["emotion"]
                await conn.send(
                    {
                        "type": "config_ack",
                        **experience_ack,
                        "proactivity": orchestrator.state["proactivity_enabled"],
                        "memory": manager.enabled,
                        "emotion": conn.emotion_override,
                        "practice": ({'mode': conn.practice.mode, 'level': conn.practice.level, 'correction': conn.practice.correction}
                                     if getattr(conn, 'practice', None) else None),
                        "experience": (conn.experience.snapshot() if getattr(conn, 'experience', None) else None),
                    }
                )
            elif kind == "proactive_check":
                if (not conn.busy
                        and (getattr(conn, 'practice', None) is None or conn.practice.mode == 'off')
                        and (getattr(conn, 'experience', None) is None or conn.experience.mode == 'off')):
                    conn.spawn(_proactive_check(conn, conn.revision))
    except WebSocketDisconnect:
        pass
    finally:
        await conn.close()
        store.end_session(conn.session_id)


@app.websocket("/ws/proactive")
async def proactive_socket(ws: WebSocket) -> None:
    """Browser-side presence hook: tells the server how long the user has been away."""
    await ws.accept()
    conn = Connection(ws)
    store.start_session(conn.session_id)
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            try:
                event = json.loads(message.get("text") or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "hello":
                try:
                    away = float(event.get("away_seconds", 0))
                except (TypeError, ValueError):
                    continue
                conn.last_spoke_at = time.time() - away
                orchestrator.set_proactivity(bool(event.get("proactivity", False)))
                await ws.send_text(json.dumps({"type": "ok", "proactivity": orchestrator.state["proactivity_enabled"]}))
            elif event.get("type") == "check":
                if not conn.busy:
                    conn.spawn(_proactive_check(conn, conn.revision))
            elif event.get("type") == "barge_in":
                if conn.matches(event) or (conn.turn is None and event.get("turn_id") is None):
                    await conn.cancel(playback=True)
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
    finally:
        await conn.close()
        store.end_session(conn.session_id)


@app.get("/")
async def index() -> Any:
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/eval/scenarios")
async def scenarios() -> Any:
    path = ROOT / "eval" / "scenarios.json"
    if not path.exists():
        return JSONResponse({"items": []})
    return json.loads(path.read_text(encoding="utf-8"))


app.mount("/static", AppStaticFiles(directory=str(ROOT / "web")), name="static")


@app.on_event("startup")
async def startup() -> None:
    global _scheduler_task
    orchestrator.set_proactivity(False)
    _scheduler_task = asyncio.create_task(scheduler(assistant))


@app.on_event("shutdown")
async def shutdown() -> None:
    editor_insights.close()
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    await llm.aclose()
