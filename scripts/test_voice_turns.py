"""Offline acceptance tests: python scripts/test_voice_turns.py.

Compile the actual WebSocket definitions from app.py without importing its
application bootstrap: no config/.env, models, network, or user data is loaded.
All provider and persistence boundaries are explicit in-memory fakes.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import importlib.util
from pathlib import Path
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from typing import Any
import unittest
import uuid

import numpy as np


class Disconnect(Exception):
    pass


class FakeSocket:
    def __init__(self):
        self.inbox = asyncio.Queue()
        self.events = []
        self.changed = asyncio.Event()

    async def accept(self):
        pass

    async def receive(self):
        return await self.inbox.get()

    async def send_text(self, text):
        self.events.append(json.loads(text))
        self.changed.set()

    def send(self, kind, **fields):
        self.inbox.put_nowait({"type": "websocket.receive", "text": json.dumps({"type": kind, **fields})})

    def audio(self, data=b"\x00\x00" * 320):
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": data})

    async def wait(self, kind, turn_id=None, *, after=0):
        async def find():
            while True:
                self.changed.clear()
                for event in self.events[after:]:
                    if event["type"] == kind and (turn_id is None or event.get("turn_id") == turn_id):
                        return event
                await self.changed.wait()
        return await asyncio.wait_for(find(), 2)


async def noop(*args, **kwargs):
    pass


def load_ws():
    path = Path(__file__).resolve().parents[1] / "server" / "app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # _maybe_diary / _maybe_self_evolution / _narrate_turn 是 F1/F3/F4 集成新增的
    # 后台函数，被 _run_turn / _proactive_check / websocket_endpoint 引用；编译进来
    # 后它们在隔离环境里经自有 try/except 自然降级（懒导入失败即返回），否则调用处
    # 会 NameError 并被 _proactive_check 的 except 吞掉，proactive 流程静默中止。
    # _executive_delegate / _executive_finish / _touch_reply 同理：V3 派单收工与
    # 摸头回应被 _run_turn / websocket_endpoint spawn，隔离环境里 executive=None、
    # 懒导入失败，两者都落进自身 except 安全返回，但名字必须编译进来才不至于
    # 在调用点炸 NameError 把整轮语音流程带崩（done 事件永不到达 → 超时）。
    names = {"VoiceTurn", "Connection", "_present_reply", "_confirm_action", "_run_turn", "_transcribe_turn", "_proactive_check", "websocket_endpoint", "proactive_socket", "_maybe_diary", "_maybe_self_evolution", "_narrate_turn", "_node_snippet", "_aqi_action", "_executive_delegate", "_executive_finish", "_touch_reply"}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) and node.name in names:
            if isinstance(node, ast.AsyncFunctionDef):
                node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MAX_AUDIO_SECONDS" for t in node.targets):
            nodes.append(node)
    spec = importlib.util.spec_from_file_location('isolated_reply_delivery', path.parent / 'dialogue' / 'delivery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 协议 §23：aqi_blocks 组装器无相对导入（纯 stdlib），隔离环境直接装真身，
    # 快环积木随路下发与 aqi_action 回传都在真实实现上验证。
    blocks_spec = importlib.util.spec_from_file_location('isolated_aqi_blocks', path.parent / 'dialogue' / 'aqi_blocks.py')
    blocks_module = importlib.util.module_from_spec(blocks_spec)
    blocks_spec.loader.exec_module(blocks_module)
    env = dict(asyncio=asyncio, base64=base64, json=json, time=time, uuid=uuid,
               Any=Any, np=np, WebSocket=FakeSocket, WebSocketDisconnect=Disconnect,
               settings=SimpleNamespace(audio_sample_rate=16000, tts_default_emotion="base"),
               EMOTIONS=("base", "调侃", "得意", "认真", "收敛", "轻微不服", "变小气"),
               persona_cfg=lambda: {"name": "fake", "tts_emotions": {
                   key: {} for key in ("base", "调侃", "得意", "认真", "收敛", "轻微不服", "变小气")}},
               store=SimpleNamespace(start_session=lambda _: None, end_session=lambda _: None),
               llm=SimpleNamespace(aclose=noop), manager=SimpleNamespace(enabled=True),
               # 打磨轮：_proactive_check 的有声 mumble 分支改用模块级导入，
               # 隔离环境注入内存假实现即可验证整条真声链路。
               detect_emotion=lambda text: "base",
               store_message=lambda *args, **kwargs: None,
               commission_accept_line=lambda: "收工快乐。",
               assistant=SimpleNamespace(should_route=lambda _, session_id='': False, waiting_input=lambda _: None),
              # V3 内阁总管接缝：隔离环境不装配 ExecutiveService，路由函数返回 None，
              # _run_turn 的总管分支因此整段跳过，既有语音路径逐字节不变。
              executive=None,
               tts=SimpleNamespace(engine='silent'), deliver_reply=module.deliver_reply,
               aqi_blocks_message=blocks_module.aqi_blocks_message,
               note_snippet=blocks_module.note_snippet,
               to_wav_bytes=lambda audio, rate: audio.tobytes())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


class VoiceTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = load_ws()
        self.asr_calls = 0
        self.proactive_calls = 0
        self.stream_calls = []

        async def transcribe(data):
            self.asr_calls += 1
            return SimpleNamespace(text="hello", duration_ms=20)

        async def stream(**kwargs):
            self.stream_calls.append(kwargs)
            yield {"type": "thinking"}
            await kwargs["on_memories"]([SimpleNamespace(id=1, kind="fact", value="fake", source="stated")])
            yield {"type": "emotion", "emotion": "base"}
            yield {"type": "text_delta", "delta": "hi"}
            yield {"type": "audio", "audio": b"fake", "index": 0, "text": "hi", "emotion": "base"}
            yield {"type": "done", "text": "hi", "emotion": "base", "latency_ms": 0}

        async def proactive(**kwargs):
            self.proactive_calls += 1
            return SimpleNamespace(text="proactive")

        self.env["recognizer"] = SimpleNamespace(transcribe_wav=transcribe)
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, proactive_turn=proactive,
                                                   state={"proactivity_enabled": False}, set_proactivity=lambda _: None,
                                                   executive_route=lambda text, executive=None, memory_enabled=False: None)
        self.ws = FakeSocket()
        self.endpoint = asyncio.create_task(self.env["websocket_endpoint"](self.ws))
        await self.ws.wait("ready")

    async def asyncTearDown(self):
        self.ws.inbox.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(self.endpoint, 2)
        await asyncio.sleep(0)

    async def configure(self, **fields):
        after = len(self.ws.events)
        self.ws.send("config", **fields)
        return await self.ws.wait("config_ack", after=after)

    async def test_default_natural_voice_forwarded_for_text_and_microphone(self):
        self.ws.send("text", turn_id="text-natural", text="hello")
        await self.ws.wait("done", "text-natural")
        self.ws.send("start", turn_id="voice-natural")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="voice-natural")
        await self.ws.wait("done", "voice-natural")
        self.assertEqual([call["voice_emotion"] for call in self.stream_calls], ["base", "base"])

    async def test_selected_voice_and_automatic_forwarded_to_orchestrator(self):
        for index, (selection, expected) in enumerate((("认真", "认真"), ("", None))):
            ack = await self.configure(emotion=selection)
            self.assertEqual(ack["emotion"], selection)
            turn_id = f"selected-{index}"
            self.ws.send("text", turn_id=turn_id, text="hello")
            await self.ws.wait("done", turn_id)
            self.assertEqual(self.stream_calls[-1]["voice_emotion"], expected)

    async def test_partial_config_preserves_selected_and_automatic_voice(self):
        for index, selection in enumerate(("认真", "")):
            await self.configure(emotion=selection)
            for fields in ({"memory": False}, {"proactivity": False}, {}):
                ack = await self.configure(**fields)
                self.assertEqual(ack["emotion"], selection)
            turn_id = f"partial-{index}"
            self.ws.send("text", turn_id=turn_id, text="hello")
            await self.ws.wait("done", turn_id)
            self.assertEqual(self.stream_calls[-1]["voice_emotion"], selection or None)

    async def test_invalid_voice_preserves_previous_selection_and_socket(self):
        await self.configure(emotion="认真")
        for index, invalid in enumerate(("unknown", [], {}, 123)):
            ack = await self.configure(emotion=invalid)
            self.assertTrue(ack.get("emotion_error"))
            self.assertEqual(ack["emotion"], "认真")
            turn_id = f"invalid-{index}"
            self.ws.send("text", turn_id=turn_id, text="hello")
            await self.ws.wait("done", turn_id)
            self.assertEqual(self.stream_calls[-1]["voice_emotion"], "认真")

    async def test_connection_uses_configured_voice_and_legacy_default(self):
        socket = FakeSocket()
        self.env["settings"].tts_default_emotion = "认真"
        self.assertEqual(self.env["Connection"](socket).emotion_override, "认真")
        del self.env["settings"].tts_default_emotion
        self.assertEqual(self.env["Connection"](socket).emotion_override, "base")

    async def test_all_voice_events_tagged(self):
        self.ws.send("start", turn_id="voice-1")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="voice-1")
        await self.ws.wait("done", "voice-1")
        events = [e for e in self.ws.events if e["type"] != "ready"]
        self.assertTrue(all(e["turn_id"] == "voice-1" for e in events))
        self.assertTrue({"recording", "asr", "thinking", "emotion", "text_delta", "audio", "done", "memory_notice"} <= {e["type"] for e in events})

    async def test_start_cancels_asr_and_drops_late_result(self):
        started, cancelled, release, returned = [asyncio.Event() for _ in range(4)]
        async def stubborn(data):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            returned.set()
            return SimpleNamespace(text="late", duration_ms=10)
        self.env["recognizer"].transcribe_wav = stubborn
        self.ws.send("start", turn_id="old")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="old")
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send("start", turn_id="new")
        await self.ws.wait("recording", "new")
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertFalse(any(e["type"] == "asr" for e in self.ws.events))
        self.ws.send("audio_end", turn_id="new")
        await self.ws.wait("done", "new")

    async def test_late_generation_and_notice_cannot_touch_new_turn(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]
        async def stubborn(**kwargs):
            yield {"type": "thinking"}
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            await kwargs["on_memories"]([SimpleNamespace(id=2, kind="fact", value="late", source="stated")])
            returned.set()
            yield {"type": "text_delta", "delta": "late"}
        self.env["orchestrator"].stream_respond = stubborn
        self.ws.send("text", turn_id="old", text="hello")
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send("start", turn_id="new")
        await self.ws.wait("recording", "new")
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertFalse(any(e["type"] in ("memory_notice", "text_delta") for e in self.ws.events))
        self.ws.send("audio_end", turn_id="new")
        await self.ws.wait("done", "new")

    async def test_stale_barge_and_audio_end_do_not_cancel_recording(self):
        self.ws.send("start", turn_id="new")
        self.ws.audio()
        self.ws.send("barge_in", turn_id="old")
        self.ws.send("audio_end", turn_id="old")
        self.ws.send("audio_end", turn_id="new")
        await self.ws.wait("done", "new")
        self.assertEqual(self.asr_calls, 1)
        self.assertFalse(any(e["type"] == "playback_stopped" for e in self.ws.events))

    async def test_matching_barge_clears_audio_and_terminates(self):
        self.ws.send("start", turn_id="a")
        self.ws.audio()
        self.ws.send("barge_in", turn_id="a")
        await self.ws.wait("playback_stopped", "a")
        self.ws.send("audio_end", turn_id="a")
        self.ws.send("text", turn_id="b", text="next")
        await self.ws.wait("done", "b")
        self.assertEqual(self.asr_calls, 0)
        await self.ws.wait("interrupted", "a")

    async def test_limit_clears_buffer_and_skips_asr(self):
        self.ws.send("start", turn_id="long")
        self.ws.audio(b"\0" * (30 * 16000 * 2 + 2))
        error = await self.ws.wait("error", "long")
        self.assertTrue(error["terminal"])
        self.assertEqual(error["code"], "audio_too_long")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="long")
        self.ws.send("start", turn_id="fresh")
        self.ws.send("audio_end", turn_id="fresh")
        await self.ws.wait("done", "fresh")
        self.assertEqual(self.asr_calls, 0)

    async def test_exact_limit_allowed_at_configured_rate(self):
        self.env["settings"].audio_sample_rate = 8000
        self.ws.send("start", turn_id="exact")
        self.ws.audio(b"\0" * (30 * 8000 * 2))
        self.ws.send("audio_end", turn_id="exact")
        await self.ws.wait("done", "exact")
        self.assertEqual(self.asr_calls, 1)

    async def test_empty_asr_is_terminal(self):
        async def empty(data):
            return SimpleNamespace(text="  ", duration_ms=20)
        self.env["recognizer"].transcribe_wav = empty
        self.ws.send("start", turn_id="empty")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="empty")
        await self.ws.wait("asr", "empty")
        await self.ws.wait("done", "empty")

    async def test_asr_failure_is_terminal_and_socket_recovers(self):
        async def fail(data):
            raise RuntimeError("fake failure")
        self.env["recognizer"].transcribe_wav = fail
        self.ws.send("start", turn_id="fail")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="fail")
        error = await self.ws.wait("error", "fail")
        self.assertTrue(error["terminal"])
        self.ws.send("text", turn_id="next", text="hello")
        await self.ws.wait("done", "next")

    async def test_malformed_pcm_is_terminal(self):
        self.ws.send("start", turn_id="odd")
        self.ws.audio(b"x")
        self.ws.send("audio_end", turn_id="odd")
        await self.ws.wait("error", "odd")
        self.assertEqual(self.asr_calls, 0)

    async def test_legacy_no_ids(self):
        self.ws.send("start")
        recording = await self.ws.wait("recording")
        self.ws.audio()
        self.ws.send("audio_end")
        await self.ws.wait("done", recording["turn_id"])
        self.assertTrue(recording["turn_id"].startswith("server-"))

    async def test_proactive_does_not_override_recording(self):
        self.ws.send("start", turn_id="user")
        self.ws.send("proactive_check")
        self.ws.send("audio_end", turn_id="user")
        await self.ws.wait("done", "user")
        self.assertEqual(self.proactive_calls, 0)

    async def test_proactive_announces_server_id(self):
        self.ws.send("proactive_check")
        event = await self.ws.wait("turn_started")
        self.assertEqual(event["source"], "proactive")
        await self.ws.wait("done", event["turn_id"])

    async def test_voiced_mumble_icebreaker_uses_shared_voice_path(self):
        """灵动伴随 + 空闲到达首次阈值（25~30s）：冷启动破冰兜底产出有声 mumble，
        连接级气泡先到，随后经 deliver_reply 真声链路完成整轮（静音 TTS 下有文本无音频）。"""
        stored = []
        self.env["store_message"] = lambda *args, **kwargs: stored.append(args)

        async def silent_proactive(**kwargs):
            return None

        self.env["orchestrator"].proactive_turn = silent_proactive
        self.env["orchestrator"].ambient_event = lambda **kwargs: {
            "type": "mumble", "event_id": "mu_ice", "text": "你停在那儿快两分钟了哦。",
            "voice": True, "trigger_reason": "icebreaker_28s",
            "source_refs": ["config:response_examples.json"], "interactive": True,
            "dedupe_key": "mu_ice", "expires_at": time.time() + 60,
            "bubble": {"text": "你停在那儿快两分钟了哦。", "ttl_ms": 60000}}
        self.ws.send("proactive_check")
        bubble = await self.ws.wait("mumble")
        self.assertTrue(bubble["voice"], "主动搭话默认真声（mumble_voice: true）")
        self.assertEqual(bubble["source_refs"], ["config:response_examples.json"])
        started = await self.ws.wait("turn_started")
        self.assertEqual(started["source"], "mumble")
        done = await self.ws.wait("done", started["turn_id"])
        self.assertEqual(done["text"], "你停在那儿快两分钟了哦。")
        self.assertEqual(done["delivery"]["text_status"], "complete")
        self.assertEqual(done["delivery"]["audio_status"], "none")  # 静音 TTS：文本完整，可恢复
        self.assertEqual(stored[-1][1:], ("assistant", "你停在那儿快两分钟了哦。", "mumble"))

    async def test_late_proactive_cannot_replace_user(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]
        async def stubborn(**kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            returned.set()
            return SimpleNamespace(text="late proactive")
        self.env["orchestrator"].proactive_turn = stubborn
        self.ws.send("proactive_check")
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send("start", turn_id="user")
        await self.ws.wait("recording", "user")
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertFalse(any(e["type"] == "turn_started" for e in self.ws.events))

    async def test_three_argument_run_turn_compatibility(self):
        socket = FakeSocket()
        conn = self.env["Connection"](socket)
        await self.env["_run_turn"](conn, "already generated", "proactive")
        first = socket.events[0]
        self.assertEqual(first["type"], "turn_started")
        self.assertEqual(socket.events[-1]["type"], "done")
        self.assertTrue(all(e["turn_id"] == first["turn_id"] for e in socket.events))


    async def test_three_argument_run_turn_as_conn_task(self):
        socket = FakeSocket()
        conn = self.env["Connection"](socket)
        conn.spawn(self.env["_run_turn"](conn, "hello", "proactive"))
        task = conn.task
        await asyncio.wait_for(task, 2)
        self.assertFalse(task.cancelled())
        self.assertEqual(socket.events[-1]["type"], "done")

    async def test_proactive_socket_announces_and_disconnects(self):
        socket = FakeSocket()
        task = asyncio.create_task(self.env["proactive_socket"](socket))
        socket.send("hello", away_seconds=100, proactivity=True)
        await socket.wait("ok")
        socket.send("check")
        started = await socket.wait("turn_started")
        await socket.wait("done", started["turn_id"])
        socket.inbox.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 2)

    async def test_disconnect_fences_uncooperative_provider_without_closing_shared_llm(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]
        closed = []
        async def close_llm():
            closed.append(True)
        async def stubborn(**kwargs):
            started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            returned.set()
            yield {"type": "text_delta", "delta": "too late"}
        self.env["llm"].aclose = close_llm
        self.env["orchestrator"].stream_respond = stubborn
        self.ws.send("text", turn_id="a", text="hello")
        await asyncio.wait_for(started.wait(), 2)
        self.ws.inbox.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(self.endpoint, 2)
        count = len(self.ws.events)
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertEqual(len(self.ws.events), count)
        self.assertEqual(closed, [])

    async def test_duplicate_audio_end_does_not_duplicate_asr(self):
        self.ws.send("start", turn_id="a")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="a")
        self.ws.send("audio_end", turn_id="a")
        await self.ws.wait("done", "a")
        self.assertEqual(self.asr_calls, 1)

    async def test_assistant_result_tagged_and_late_result_fenced(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]
        stored = []
        module = ModuleType("voice_test.dialogue.orchestrator")
        module.store_message = lambda *args: stored.append(args)
        module.store_recent = lambda session_id, limit: []
        self.env["__package__"] = "voice_test"
        self.env["assistant"].should_route = lambda _, session_id='': True

        async def action(text, session, history=None):
            if text == "slow":
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                returned.set()
            return {"text": "action result"}

        self.env["assistant"].start = action
        with patch.dict("sys.modules", {module.__name__: module}):
            self.ws.send("text", turn_id="action", text="fast")
            await self.ws.wait("done", "action")
            result = await self.ws.wait("assistant_result", "action")
            self.assertEqual(result["result"]["text"], "action result")
            self.ws.send("text", turn_id="late-action", text="slow")
            await asyncio.wait_for(started.wait(), 2)
            self.ws.send("start", turn_id="new")
            await self.ws.wait("recording", "new")
            release.set()
            await asyncio.wait_for(returned.wait(), 2)
            await asyncio.sleep(0)
            self.assertFalse(any(e["type"] == "assistant_result" and e["turn_id"] == "late-action" for e in self.ws.events))
            self.assertEqual(len(stored), 2)
            self.ws.send("audio_end", turn_id="new")
            await self.ws.wait("done", "new")

    async def test_legacy_task_keeps_busy_tracking_during_begin(self):
        socket = FakeSocket()
        conn = self.env["Connection"](socket)
        original_send = socket.send_text
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused_send(text):
            if json.loads(text)["type"] == "turn_started":
                entered.set()
                await release.wait()
            await original_send(text)

        socket.send_text = paused_send
        conn.spawn(self.env["_run_turn"](conn, "hello", "proactive"))
        task = conn.task
        await asyncio.wait_for(entered.wait(), 2)
        self.assertIs(conn.task, task)
        self.assertTrue(conn.busy)
        release.set()
        await asyncio.wait_for(task, 2)

    async def test_barge_cancels_unannounced_proactive_result(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]

        async def stubborn(**kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            returned.set()
            return SimpleNamespace(text="late")

        self.env["orchestrator"].proactive_turn = stubborn
        self.ws.send("proactive_check")
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send("barge_in")
        # config_ack is a receive-loop barrier, not a timing assumption.
        self.ws.send("config")
        await self.ws.wait("config_ack")
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.assertFalse(any(e["type"] == "turn_started" for e in self.ws.events))

    async def test_malformed_events_do_not_disconnect(self):
        for raw in ("[]", '"string"', "null", "123", "{bad"):
            self.ws.inbox.put_nowait({"type": "websocket.receive", "text": raw})
        for invalid in (123, [], {}, None):
            self.ws.send("text", text=invalid)
        self.ws.send("text", turn_id="valid", text="hello")
        await self.ws.wait("done", "valid")

    async def test_terminal_provider_error_stops_stream(self):
        async def stream(**kwargs):
            yield {"type": "error", "message": "failed"}
            yield {"type": "text_delta", "delta": "invalid after terminal"}

        self.env["orchestrator"].stream_respond = stream
        self.ws.send("text", turn_id="failed", text="hello")
        error = await self.ws.wait("error", "failed")
        self.assertTrue(error["terminal"])
        self.ws.send("start", turn_id="new")
        await self.ws.wait("recording", "new")
        self.assertFalse(any(e["type"] == "text_delta" for e in self.ws.events))

    async def test_old_error_cannot_terminate_new_recording(self):
        started, release, returned = [asyncio.Event() for _ in range(3)]
        async def stubborn(data):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            returned.set()
            raise RuntimeError("old provider failed late")
        self.env["recognizer"].transcribe_wav = stubborn
        self.ws.send("start", turn_id="old")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="old")
        await asyncio.wait_for(started.wait(), 2)
        self.ws.send("start", turn_id="new")
        await self.ws.wait("recording", "new")
        release.set()
        await asyncio.wait_for(returned.wait(), 2)
        await asyncio.sleep(0)
        self.ws.send("audio_end", turn_id="new")
        await self.ws.wait("done", "new")
        self.assertFalse(any(e["type"] == "error" for e in self.ws.events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
