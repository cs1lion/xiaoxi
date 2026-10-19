from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from .static import AppStaticFiles

from .config import ROOT, avatar_cfg, ensure_dirs, pacing_cfg, persona_cfg, settings
from .dialogue.aqi_blocks import (aqi_blocks_message, compose_delivery_card,
                                  compose_spinning_card, note_snippet)
from .dialogue.orchestrator import (EMOTIONS, commission_accept_line, detect_emotion,
                                   orchestrator, parse_emotion, spinning_intercept_line,
                                   store_message)
from .dialogue.delivery import deliver_reply
from .llm import llm
from .memory.manager import manager
from .memory.store import store
from .speech.asr import recognizer, to_wav_bytes

# 自进化通道允许发出的 WS 事件类型（协议 §十）。白名单之外的一个都不发：
# 自造事件比少发一个事件更难查——前端对未知 type 安全忽略，于是两边都看不出来。
SELF_EVOLUTION_WS_EVENTS = frozenset({"self_evolution"})

ensure_dirs()

app = FastAPI(title="聊天角色系统", version="0.1.0")

from .assistant.service import AssistantService
from .assistant.api import create_router, scheduler

assistant = AssistantService(ROOT / "data", llm, manager)
orchestrator.context_broker = assistant.context_broker
orchestrator.activity_facade = assistant.activity
# ExecutiveService 由 AssistantService 持有并统一关闭（同生命周期、同一关闭路径）。
from .assistant.executive_api import create_executive_router, create_telemetry_proxy_router
executive = assistant.executive
orchestrator.executive = executive
app.include_router(create_router(assistant))
app.include_router(create_executive_router(executive))
app.include_router(create_telemetry_proxy_router(executive))
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
# 启动期续跑的督办：create_task 的返回值必须有人拿住，否则可能被回收。
_startup_tasks: set[asyncio.Task] = set()
# 已连上的 /ws 连接。督办状态变化要推给所有在线连接（协议 §19 变更通知）。
# 用集合而不是列表：连接断开的清理顺序不确定，重复删也要幂等。
_connections: set["Connection"] = set()
# 台账订阅的取消函数。shutdown 时解绑，避免测试里残留订阅者。
_commission_unsubscribe = None
# startup 时抓住的主事件循环。台账回调可能来自别的线程，靠它转回事件循环。
_loop: asyncio.AbstractEventLoop | None = None
# 慢环执行内核（agent-runtime sidecar）。默认不启用，见 config 的 durable 段。
_durable_supervisor = None


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
async def get_avatar() -> dict[str, Any]:
    """姿态契约。前端据此构建参数表、表情目标和手势轨道。

    必须走 `avatar_cfg()`，不能直接读 yaml 文件：`check_protocol.py` 与所有服务端
    代码读的都是 `avatar_cfg()`，它在 config_hot 装了快照时返回覆盖后的结果
    （协议 §17：改配置不用重启）。直接读盘的那一版在热覆盖生效时会给出**过期**
    契约——前端按旧参数建姿态、校验却按新参数判，两头都不报错。

    这里原先有两个同名处理函数挂在同一路径上：先注册的读盘版本胜出，后注册的
    `avatar_cfg()` 版本被悄悄遮蔽，从来没有服务过请求。
    """
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
        # 协议 §23.4：星图查看随路下发真实节点摘录积木，并联动星图视图。
        snippet = _node_snippet(node_id, record)
        if snippet is not None:
            await conn.send_turn(turn, aqi_blocks_message([snippet], layout="stage_holo",
                                                          nav="starmap", gaze="holo_right",
                                                          source="ambient"))
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
        from .memory.evolution import self_evolution_cfg
        if not self_evolution_cfg().get("enabled"):
            return
        idle_seconds = time.time() - conn.last_spoke_at
        cycle = SelfEvolutionCycle(provider=llm)
        # V2 thought engine first: reflect on a seed and fork children. It shares the
        # idle/daily gate with the legacy cycle, so only one runs per window. When it
        # cannot produce a valid reflection this tick, fall back to the legacy
        # ledger/spark cycle so the existing self-evolution keeps working.
        outcome = await cycle.reflect(idle_seconds=idle_seconds)
        if outcome.get("status") in ("skipped", "failed"):
            outcome = await cycle.run(idle_seconds=idle_seconds)
        for event in outcome.get("events") or []:
            if not isinstance(event, dict) or conn.closed:
                continue
            # `events` 里已经是组装好的 WS 事件（run() 与 reflect() 各自调过
            # self_evolution_payload / 直接给出完整事件）。这里**不能再翻译一次**：
            # self_evolution_payload() 只认裸 change（顶层有 kind），拿一个已经
            # 带 type 的事件进去会返回 None。2026-10-18 修：此前正是这么写的，
            # 于是每一条自进化通知都被静默丢弃——协议 §十 的 self_evolution
            # 事件一个都没发出去，前端 noteSelfEvolution() 从未触发。
            if event.get("type") in SELF_EVOLUTION_WS_EVENTS:
                # 只放行协议形状完整的事件：change 必须是对象。缺了它，
                # 消费方读 change.kind 会拿到 None——又一种静默退化。
                if isinstance(event.get("change"), dict):
                    await conn.send(event)
            elif "type" not in event:
                # 裸 change：按协议 §十 的形状组装（kind 只允许 taste/worldview/spark）。
                from .memory.evolution import self_evolution_payload
                payload = self_evolution_payload(event)
                if payload is not None:
                    await conn.send(payload)
            # 其余带 type 的事件不发。当前唯一会走到这里的是 V2 思想引擎的
            # `curiosity_reflection`：它携带 child_seed_ids / reflection_id，
            # 与协议 §十 的 self_evolution{kind:taste|worldview|spark} 不是一回事。
            # 直接发等于自造 WS 事件（前端对未知 type 安全忽略，两边都看不出来）；
            # 映射成 self_evolution 又得先给 change.kind 加 `child_seed` 取值——
            # 那是协议变更，归 Role 0。在此之前宁可不发，也不要发一个语义不对的。
            # 前端因此收不到"我刚想通一件事"的信号；星图仍会显示新节点。
            # 见 docs/OPTIMIZATION_ROUND3_20261018.md §9。
    except asyncio.CancelledError:
        raise
    except Exception:
        return


def _node_snippet(node_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """协议 §23.4：星图节点 → note_snippet 积木。只取记录里真实存在的字段，
    五种节点形状（memory/diary/spark/seed/ledger）按可选字段依序取文本。"""
    if not isinstance(record, dict):
        return None
    text = next((str(record[key]).strip() for key in ("value", "body", "text", "stance", "question")
                 if isinstance(record.get(key), str) and record[key].strip()), "")
    if not text:
        return None
    title = next((str(record[key]).strip() for key in ("title", "topic")
                  if isinstance(record.get(key), str) and record[key].strip()), "")
    return note_snippet(text, title=title, ref=node_id)


async def _aqi_action(conn: Connection, cmd: str) -> None:
    """协议 §23.5：积木业务动作回传。只认白名单动词
    （dispatch:accept:<id> / dispatch:reassign:<id>），以台账真实状态为准；
    失败回连接级可恢复错误，不重试、不排队、不重复副作用。"""
    try:
        if conn.closed:
            return
        parts = cmd.split(":", 2)
        if len(parts) != 3 or parts[0] != "dispatch" or parts[1] not in ("accept", "reassign"):
            return
        commission_id = parts[2].strip()
        if not commission_id or len(commission_id) > 100:
            return
        action = parts[1]
        turn = await conn.begin()
        await conn.send_turn(turn, {"type": "turn_started", "source": "aqi_action"})
        try:
            if action == "accept":
                executive.accept(commission_id)
            else:
                executive.reassign(commission_id)
                # 换将之后这一单还得有人跑：驱动放后台，确认语音不傻等。
                conn.spawn(_executive_reassign_drive(commission_id))
        except Exception:
            await conn.finish(turn, {"type": "error", "code": "aqi_action_failed",
                                     "recoverable": True,
                                     "message": "这单的状态变了，没收工成功；看看战况抽屉里的实际状态。"})
            return
        # 确认台词必须是真实配置样本；没有样本就静默交差，不编造。
        line = commission_accept_line() if action == "accept" else spinning_intercept_line()
        if line:
            await conn.send_turn(turn, {"type": "thinking"})
            await _present_reply(conn, turn, line, detect_emotion(line))
        else:
            await conn.finish(turn, {"type": "done", "text": "", "latency_ms": 0})
    except asyncio.CancelledError:
        raise
    except Exception:
        # There is no announced turn to fail when a background action fails.
        return


async def _executive_reassign_drive(commission_id: str) -> None:
    """换将之后驱动执行体接着跑；失败只在台账里留痕，不向外抛。"""
    try:
        await executive.run_commission(commission_id)
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
        # V3 台前快环：委托接单 / 进度追问 / API 战况 / 验收反馈。
        # 全部用配置样本或真实台账数据回答，不发第二次模型请求；慢环另起后台任务。
        route = orchestrator.executive_route(text, executive,
                                              memory_enabled=manager.enabled)
        if route is not None:
            from .dialogue.orchestrator import store_message
            store_message(conn.session_id, "user", text, mode)
            # 界面自动领航（协议§二十四）：先于该轮语音下发，让她"亲手拉开面板"
            # 指给老板看；旧客户端读到未知类型整条忽略，不阻塞。
            # 字面量 {"type": "ui_nav"} 供 check_protocol.py 的静态字段扫描识别。
            if isinstance(route.get("ui_nav"), dict):
                nav_payload = {
                    "type": "ui_nav",
                    "target_view": "ops",
                    "sub_action": "focus_latest",
                    "gaze_target": "side",
                }
                nav_payload.update(route["ui_nav"])
                await conn.send_turn(turn, nav_payload)
            await conn.send_turn(turn, {"type": "thinking"})
            # 协议 §23：积木先递到她嘴边——卡片就位，再开口说这一单的事。
            # 必须在 _present_reply 收轮之前发，否则 turn fence 已关。
            # source=executive（快环台账直出）；delegation 附 nav=ops 自动滑抽屉。
            blocks = route.get("aqi_blocks") or []
            if blocks:
                await conn.send_turn(turn, aqi_blocks_message(
                    blocks, layout="stage_holo", nav=route.get("nav"),
                    gaze="holo_right", source="executive"))
            if route.get("text"):
                await _present_reply(conn, turn, route["text"], parse_emotion(route["text"])[0])
            else:
                await conn.finish(turn, {"type": "done", "text": "", "latency_ms": 0})
            kind = route.get("kind")
            if kind == "delegation":
                conn.spawn(_executive_delegate(route.get("brief") or text))
            elif kind in ("accept", "reject"):
                conn.spawn(_executive_finish(route.get("commission_id", ""), kind,
                                             route.get("feedback") or text))
            elif kind == "starmap_view":
                # 看星图是纯领航：没有后台动作要跑，台词说完即收。
                pass
            elif kind == "notes_view":
                # 看笔记/便笺同理，前端侧栏行动面板自己定位，不需要后台任务。
                pass
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


async def _send_proactive_blocks(conn: Connection, turn: VoiceTurn, source: str,
                                result) -> None:
    """协议 §23.6：主动开口轮次随路积木——熔断红卡 > 邀功金卡 > 偷师顿悟卡。

    只用真实台账数据组装：无熔断、无待验收、无 spark 时一枚都不发
    （不拿占位卡充数）。必须在 _run_turn 收轮之前 send_turn，否则
    turn fence 已关，卡片会被当成陈旧帧丢弃。
    """
    blocks: list[dict] = []
    try:
        alerts = executive.spinning_alerts()
    except Exception:
        alerts = []
    card = compose_spinning_card(alerts)
    if card is not None:
        blocks.append(card)
    if not blocks:
        try:
            pending = executive.pending_reports()
        except Exception:
            pending = []
        if pending:
            card = compose_delivery_card(pending[0])
            if card is not None:
                blocks.append(card)
    if not blocks and source == "self_topic":
        spark = getattr(result, "spark", None)
        if isinstance(spark, dict) and isinstance(spark.get("text"), str) and spark["text"].strip():
            card = note_snippet(spark["text"], title="偷师顿悟",
                                ref=f"spark:{spark.get('id')}",
                                actions=[{"label": "去星图看看", "cmd": "ui:starmap"}])
            if card is not None:
                blocks.append(card)
    if blocks:
        await conn.send_turn(turn, aqi_blocks_message(
            blocks, layout="stage_holo", gaze="holo_right",
            source="loop" if source == "self_topic" else "ambient"))


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
        # recording/playing/live-turn re-check: a user turn that started after the
        # spawn guard must never be cancelled by an ambient opener (conn.busy
        # cannot be used here because this very task is conn.task).
        if (conn.closed or conn.revision != revision
                or conn.recording or conn.playing
                or (conn.turn is not None and conn.turn.live)):
            return
        if result:
            turn = await conn.begin()
            await conn.send_turn(turn, {"type": "turn_started", "source": source})
            # 协议 §23.6：卡片先递到她嘴边，再开口说这一单的事。
            await _send_proactive_blocks(conn, turn, source, result)
            await _run_turn(conn, result.text, "proactive", turn)
            if source == "self_topic":
                spark_id = getattr(result, "spark_id", None)
                if spark_id:
                    from .memory.diary import spend_spark
                    spend_spark(store, spark_id)
            return
        # No voiced proactive this tick: surface an ambient tier (§16).
        event = orchestrator.ambient_event(idle_seconds=time.time() - conn.last_spoke_at)
        if event and not conn.closed and conn.revision == revision:
            if event.get("type") == "mumble" and event.get("voice"):
                # Real mumble through the shared voice path (deliver_reply); the frontend
                # renders a "mumble" turn as an ambient subtitle, not a chat bubble. Reuses
                # barge-in / interruption and the existing turn fence. The connection-level
                # bubble rides along first so the voiced opener also leaves its glimmer.
                await conn.send(event)
                turn = await conn.begin()
                # begin() 自增 revision，轮次有效性以 current(turn) 为准：
                # 用户轮次在栅栏与 begin() 之间抢进来时，这一轮已被取消，静默退出。
                if turn is not None and conn.current(turn):
                    text = event.get("text") or ""
                    await conn.send_turn(turn, {"type": "turn_started", "source": "mumble"})
                    store_message(conn.session_id, "assistant", text, "mumble")
                    await _present_reply(conn, turn, text, detect_emotion(text))
            else:
                await conn.send(event)  # micro_act (silent) or non-voiced mumble bubble
    except asyncio.CancelledError:
        raise
    except Exception:
        # There is no announced turn to fail when a background idle check fails.
        return


async def _touch_reply(conn: Connection, revision: int, region: str) -> None:
    """Touch head/face: speak a real persona sample (§7.2) through the shared voice path.

    Reuses deliver_reply (barge-in / interruption / turn fence). Only fires when the
    connection is idle; the sample text is real config, never fabricated.
    """
    try:
        if conn.closed or conn.revision != revision:
            return
        from .dialogue.orchestrator import detect_emotion, store_message, touch_sample_line
        line = touch_sample_line(region)
        if not line:
            return
        turn = await conn.begin()
        if turn is None or conn.revision != revision:
            return
        await conn.send_turn(turn, {"type": "turn_started", "source": "touch"})
        store_message(conn.session_id, "assistant", line, "touch")
        await _present_reply(conn, turn, line, detect_emotion(line))
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def _executive_delegate(text: str) -> None:
    """幕后慢环接单：组密件、选将、开局。任何失败都只在台账里留痕。

    这一句跑在 spawn 出来的后台任务里，语音流早就说完"接单了"——
    老板的时间不该花在等派单上。
    """
    try:
        # delegate 自己驱动执行体跑完（服务端唯一的执行所有权），所以这里必须
        # await：它可能跑满一个 Worker 超时，但仍在后台任务里，不占语音轮次。
        await executive.delegate(text)
    except Exception:
        return


async def _executive_finish(commission_id: str, action: str, feedback: str = "") -> None:
    """收工归档 / 挑刺打回。打回会顺手把漏审点炼成新判例。"""
    try:
        if action == "accept":
            executive.accept(commission_id)
        else:
            executive.reject(commission_id, feedback)
    except Exception:
        return


async def _broadcast_commission(commission: dict[str, Any]) -> None:
    """把一条督办的状态变化推给所有在线连接。

    载荷就是台账里那一条（与 GET /api/executive/commissions 的 items 元素同形），
    前端不必维护第二套字段映射。没有在线连接时静默丢弃：这是旁路通知，
    状态永远以 REST 台账为准，漏掉一次推送不该让人以为台账坏了。
    """
    if not _connections:
        return
    payload = {"type": "commission_update", "commission": commission}
    for conn in list(_connections):
        if conn.closed:
            continue
        try:
            await conn.send(payload)
        except Exception:
            # 一个连接发送失败不影响其它连接；它的清理走自己的 finally。
            continue


def _commission_changed(commission: dict[str, Any]) -> None:
    """台账回调是同步的，可能来自任意线程（写库路径），所以不能就地 create_task。

    在 startup 时抓住主事件循环，这里用 call_soon_threadsafe 把它转过去。
    没抓住循环（比如单元测试里没跑 startup）就静默丢弃：推送是旁路，
    状态永远以 REST 台账为准。
    """
    if _loop is None:
        return
    try:
        _loop.call_soon_threadsafe(_spawn_broadcast, commission)
    except RuntimeError:
        # 循环已经关了：服务在关闭途中，丢掉这次推送。
        return


def _spawn_broadcast(commission: dict[str, Any]) -> None:
    task = asyncio.create_task(_broadcast_commission(commission))
    _startup_tasks.add(task)
    task.add_done_callback(_startup_tasks.discard)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    # 同源校验：只挡浏览器页面，规则与 Connection.bind_editor_insight 的 netloc 比对一致。
    # 只比 netloc（host:port），不比较 scheme：TLS 终止在反代上时 scope 里是 ws/http、
    # 浏览器 Origin 却是 https，比 scheme 会把合法同源页面误拒。
    # 没有 Origin 头（本地脚本、直连测试、健康检查）或 Origin 为空串时按白名单放行。
    headers = getattr(ws, "headers", None) or {}
    host = str(headers.get("host") or "").lower()
    origin = headers.get("origin")
    if origin:
        from urllib.parse import urlsplit

        try:
            origin_netloc = urlsplit(origin).netloc.lower()
        except ValueError:
            # 畸形 Origin（如 "http://["）不是合法来源：按跨源拒绝，不能抛成 500。
            origin_netloc = ""
        if origin_netloc == "" or origin_netloc != host:
            # 拒绝发生在 accept 之前：uvicorn 把它落成 HTTP 403 握手失败，浏览器看不到
            # 1008/reason（它们只留在 ASGI 关闭帧与服务端日志里），这是预期行为。
            import logging

            logging.getLogger("server.app").warning(
                "websocket origin rejected: origin=%r host=%r", origin, headers.get("host"))
            await ws.close(code=1008, reason="cross_origin_denied")
            return
    await ws.accept()
    conn = Connection(ws)
    _connections.add(conn)
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
            elif kind == 'aqi_action':
                # 协议 §23.5：积木业务动作回传。cmd 白名单在校验函数里，未知动词静默忽略。
                cmd = event.get('cmd')
                if not isinstance(cmd, str) or not 0 < len(cmd) <= 200:
                    continue
                if conn.busy:
                    # 她正忙着（含这一轮语音确认）时不排队：按钮可稍后再点。
                    await conn.send({"type": "error", "code": "aqi_action_failed",
                                     "recoverable": True,
                                     "message": "小溪正忙着，这单没收工；稍后再点。"})
                    continue
                conn.spawn(_aqi_action(conn, cmd))
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
                    enabled = bool(event["proactivity"])
                    if enabled and not conn.proactivity:
                        # 开启主动模式（含三态胶囊切到"灵动伴随"）后，空闲窗口从
                        # 切换时刻起算：安静 25~30s 即可迎来首次主动搭话。
                        conn.last_spoke_at = time.time()
                    conn.proactivity = enabled
                    orchestrator.set_proactivity(enabled)
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
            elif kind == "touch":
                region = event.get("region")
                if region in ("head", "face") and not conn.busy:
                    conn.spawn(_touch_reply(conn, conn.revision, region))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(conn)
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


async def _start_durable_if_enabled() -> None:
    """按 config 的 durable 段拉起慢环执行内核；默认不启用，失败不阻断主服务。"""
    global _durable_supervisor
    from .config import agents_roster_cfg
    from .assistant.durable_supervisor import DurableSupervisor

    cfg = ((agents_roster_cfg() or {}).get("durable") or {})
    if not cfg.get("enabled"):
        return
    supervisor = DurableSupervisor(
        directory=cfg.get("directory") or "data/durable",
        node=str(cfg.get("node") or "node"),
        startup_timeout=float(cfg.get("startup_timeout") or 15.0),
        auto_restart=bool(cfg.get("auto_restart", True)),
        # 审批工具白名单是 sidecar 进程的环境变量，必须随进程一起给出：此前只有
        # 测试设过它，生产里一个工具都没登记，审批 hook 因此从未生效过。
        extra_env={"XIAOXI_APPROVAL_TOOLS": str(cfg.get("approval_tools") or "")},
    )
    try:
        await supervisor.start()
        if cfg.get("patrol_seconds"):
            await supervisor.start_patrol(float(cfg["patrol_seconds"]))
        bound = executive.bind_durable(
            supervisor.config(),
            apply_to=cfg.get("switch_agents") or None,
            # 内核被硬杀后监督器会换端口重起，写死的 endpoint 就失效了。适配器
            # 每次派单前问一次当前端点，重试才不会连到死端口。
            endpoint_provider=supervisor.current_endpoint,
        )
        print(f"[durable] 执行内核就绪 {supervisor.endpoint}；已绑定 {bound} 个 Worker")
        if cfg.get("shadow"):
            from .assistant.durable_shadow import DurableShadow
            shadow = DurableShadow(endpoint=supervisor.endpoint, token=supervisor.token,
                                   log_path=cfg.get("shadow_log") or "data/durable-shadow.jsonl")
            executive.shadow = shadow.run
            print(f"[durable] shadow 已开，对照日志 {shadow.log_path}")
        _durable_supervisor = supervisor
    except Exception as exc:
        # 配置开了但内核起不来：不阻断主服务。派单会转 BLOCKED 并明说不可用。
        print(f"[durable] 执行内核不可用：{exc}")
        try:
            await supervisor.stop()
        except Exception:
            pass


def _durable_lookup():
    """给重启对账用的内核查询函数；内核没起来就返回 None。

    返回的是同步函数而不是协程：`recover_interrupted` 是同步收口，它需要的只是
    "这一单在内核里现在是什么状态"。sidecar 只监听 loopback，查询走一次短连接
    即可，查询失败一律当作"内核里没有这一单"，由原有收口转 BLOCKED。
    """
    if _durable_supervisor is None or not _durable_supervisor.available:
        return None
    endpoint = _durable_supervisor.endpoint
    token = _durable_supervisor.token

    def lookup(commission_id: str, attempt: int = 0):
        """按 `commission_id@attempt` 查单；attempt 缺省 0 只为兼容旧调用。"""
        import httpx
        headers = {"authorization": f"Bearer {token}"}
        try:
            with httpx.Client(timeout=10.0) as client:
                found = client.get(f"{endpoint}/v1/lookup", headers=headers,
                                   params={"commission_id": commission_id,
                                           "attempt": int(attempt)}).json()
                if not found.get("found"):
                    return None
                task_id = int(found.get("task_id") or 0)
                if task_id <= 0:
                    return None
                return client.get(f"{endpoint}/v1/runs/{task_id}", headers=headers).json()
        except Exception:
            return None

    return lookup


@app.on_event("startup")
async def startup() -> None:
    global _scheduler_task, _loop, _commission_unsubscribe
    _loop = asyncio.get_running_loop()
    # 督办状态变化是旁路推送：台账写成功后通知所有在线连接（协议 §19）。
    _commission_unsubscribe = executive.ledger.subscribe(_commission_changed)
    orchestrator.set_proactivity(False)
    # 先把执行内核绑好，再收口续跑；否则重派出去的 durable 单没有端点可用。
    await _start_durable_if_enabled()
    # 重启收口要排在调度器之前：先把上次没跑完的单转成请示状态或重新派出，
    # 否则它们会一直占着 worker 的并发槽，新单直接没人接。
    recovered = executive.recover_interrupted(durable_lookup=_durable_lookup())
    _scheduler_task = asyncio.create_task(scheduler(assistant))
    for commission_id in recovered["resumed"]:
        task = asyncio.create_task(executive.resume(commission_id))
        _startup_tasks.add(task)
        task.add_done_callback(_startup_tasks.discard)


@app.on_event("shutdown")
async def shutdown() -> None:
    global _durable_supervisor, _commission_unsubscribe, _loop
    if _commission_unsubscribe is not None:
        _commission_unsubscribe()
        _commission_unsubscribe = None
    _loop = None
    editor_insights.close()
    if _durable_supervisor is not None:
        await _durable_supervisor.stop()
        _durable_supervisor = None
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    await llm.aclose()
