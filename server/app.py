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
from fastapi.staticfiles import StaticFiles

from .config import ROOT, avatar_cfg, ensure_dirs, pacing_cfg, persona_cfg, settings
from .dialogue.orchestrator import orchestrator
from .llm import llm
from .memory.manager import manager
from .memory.store import store
from .speech.asr import recognizer, to_wav_bytes
from .speech.tts import tts

ensure_dirs()

app = FastAPI(title="聊天角色系统", version="0.1.0")


# ---------------------------------------------------------------- REST: memory
@app.get("/api/persona")
async def get_persona() -> dict[str, Any]:
    cfg = persona_cfg()
    return {
        "name": cfg["name"],
        "tagline": cfg["tagline"],
        "tendencies": cfg["tendencies"],
        "voices": list(cfg["tts_emotions"].keys()),
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
        "llm": {"provider": llm.provider, "model": llm.model, "offline": llm.offline},
        "tts": tts.engine,
        "asr": settings.asr_model,
        "memories": store.count(),
        "proactivity": orchestrator.state["proactivity_enabled"],
    }


# ---------------------------------------------------------------- WebSocket
class Connection:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.session_id = uuid.uuid4().hex[:12]
        self.buffer = bytearray()
        self.recording = False
        self.task: asyncio.Task | None = None
        self.playing = False
        self.proactivity = False
        self.emotion_override = ""
        self.last_spoke_at = time.time()

    async def send(self, payload: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(payload, ensure_ascii=False))


async def _run_turn(conn: Connection, text: str, mode: str) -> None:
    try:
        await conn.send({"type": "thinking"})
        result = await orchestrator.respond(
            session_id=conn.session_id, user_text=text, mode=mode
        )
        if not result.text:
            await conn.send({"type": "done", "text": ""})
            return
        emotion = conn.emotion_override or result.emotion
        conn.playing = True
        conn.last_spoke_at = time.time()
        await conn.send({"type": "text", "text": result.text, "emotion": emotion})
        if result.saved:
            notices = [
                {"id": m["id"], "kind": m["kind"], "value": m["value"], "source": m["source"]}
                for m in result.saved
            ]
            await conn.send({"type": "memory_notice", "items": notices})
        audio_sent = False
        async for chunk in tts.speak(result.text, emotion):
            if chunk.audio:
                audio_sent = True
                await conn.send(
                    {
                        "type": "audio",
                        "index": chunk.index,
                        "text": chunk.text,
                        "emotion": chunk.emotion,
                        "data": base64.b64encode(chunk.audio).decode("ascii"),
                    }
                )
        conn.playing = False
        await conn.send({"type": "done", "text": result.text, "latency_ms": result.latency_ms, "audio": audio_sent})
    except asyncio.CancelledError:
        conn.playing = False
        await conn.send({"type": "interrupted"})
    except Exception as exc:  # pragma: no cover - defensive
        conn.playing = False
        await conn.send({"type": "error", "message": str(exc)})


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
    loop = asyncio.get_running_loop()
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (blob := message.get("bytes")) is not None:
                if conn.recording:
                    conn.buffer.extend(blob)
                continue

            raw = message.get("text")
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")

            if kind == "start":
                conn.recording = True
                conn.buffer.clear()
                await conn.send({"type": "recording", "state": True})
            elif kind == "audio_end":
                conn.recording = False
                if conn.task and not conn.task.done():
                    conn.task.cancel()
                data = bytes(conn.buffer)
                conn.buffer.clear()
                if not data:
                    continue
                wav = to_wav_bytes(np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0, settings.audio_sample_rate)
                transcript = await recognizer.transcribe_wav(wav)
                if not transcript.text:
                    await conn.send({"type": "asr", "text": "", "duration_ms": transcript.duration_ms})
                    continue
                await conn.send({"type": "asr", "text": transcript.text, "duration_ms": transcript.duration_ms})
                conn.task = loop.create_task(_run_turn(conn, transcript.text, "voice"))
            elif kind == "text":
                text = (event.get("text") or "").strip()
                if not text:
                    continue
                if conn.task and not conn.task.done():
                    conn.task.cancel()
                await conn.send({"type": "asr", "text": text, "duration_ms": 0})
                conn.task = loop.create_task(_run_turn(conn, text, "text"))
            elif kind == "barge_in":
                if conn.task and not conn.task.done():
                    conn.task.cancel()
                conn.playing = False
                await conn.send({"type": "playback_stopped"})
            elif kind == "config":
                if "proactivity" in event:
                    conn.proactivity = bool(event["proactivity"])
                    orchestrator.set_proactivity(conn.proactivity)
                if "memory" in event:
                    manager.enabled = bool(event["memory"])
                conn.emotion_override = str(event.get("emotion") or "")
                await conn.send(
                    {
                        "type": "config_ack",
                        "proactivity": orchestrator.state["proactivity_enabled"],
                        "memory": manager.enabled,
                        "emotion": conn.emotion_override,
                    }
                )
            elif kind == "proactive_check":
                idle = time.time() - conn.last_spoke_at
                result = await orchestrator.proactive_turn(session_id=conn.session_id, idle_seconds=idle)
                if result:
                    conn.task = loop.create_task(_run_turn(conn, result.text, "proactive"))
    except WebSocketDisconnect:
        pass
    finally:
        if conn.task and not conn.task.done():
            conn.task.cancel()
        store.end_session(conn.session_id)
        try:
            await llm.aclose()
        except Exception:  # pragma: no cover
            pass


@app.websocket("/ws/proactive")
async def proactive_socket(ws: WebSocket) -> None:
    """Browser-side presence hook: tells the server how long the user has been away."""
    await ws.accept()
    conn = Connection(ws)
    store.start_session(conn.session_id)
    loop = asyncio.get_running_loop()
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            event = json.loads(message.get("text") or "{}")
            if event.get("type") == "hello":
                conn.last_spoke_at = time.time() - float(event.get("away_seconds", 0))
                orchestrator.set_proactivity(bool(event.get("proactivity", False)))
                await ws.send_text(json.dumps({"type": "ok", "proactivity": orchestrator.state["proactivity_enabled"]}))
            elif event.get("type") == "check":
                idle = time.time() - conn.last_spoke_at
                result = await orchestrator.proactive_turn(session_id=conn.session_id, idle_seconds=idle)
                if result:
                    conn.task = loop.create_task(_run_turn(conn, result.text, "proactive"))
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
    finally:
        store.end_session(conn.session_id)


@app.get("/")
async def index() -> Any:
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/eval/scenarios")
async def scenarios() -> Any:
    path = ROOT / "eval" / "scenarios.json"
    if not path.exists():
        return JSONResponse({"items": []})
    return json.loads(path.read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=str(ROOT / "web")), name="static")


@app.on_event("startup")
async def startup() -> None:
    orchestrator.set_proactivity(False)


@app.on_event("shutdown")
async def shutdown() -> None:
    await llm.aclose()