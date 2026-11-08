"""Execute actual app transport definitions without bootstrap, config or providers.

Run: .venv/Scripts/python.exe -B scripts/test_speech_performance_route.py
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any
import unittest
import uuid


class Socket:
    def __init__(self):
        self.events = []
    async def send_text(self, text):
        self.events.append(json.loads(text))


async def _reject_call(*_args, **_kwargs):
    """运输层测试不该触发真实模型调用；真被调用说明隔离破了，立刻炸开。"""
    raise AssertionError("test_speech_performance_route 不得触发真实模型调用")


def load_transport():
    path = Path(__file__).resolve().parents[1] / "server" / "app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))
             and node.name in {"VoiceTurn", "Connection", "_run_turn", "_present_reply",
                               "_maybe_diary", "_executive_delegate", "_executive_finish"}]
    env = dict(asyncio=asyncio, base64=base64, json=json, time=time, uuid=uuid,
               Any=Any, WebSocket=Socket, settings=SimpleNamespace(tts_default_emotion="base"),
               EMOTIONS=("base", "认真"),
               assistant=SimpleNamespace(should_route=lambda _, session_id='': False,
                                         waiting_input=lambda _: False),
              executive=None, manager=SimpleNamespace(enabled=False),
               # server/app.py:724 的 _run_turn 读全局 llm；不喂它，运输层一跑就
               # NameError（2026-10-25 实测本文件整组红）。offline=True 让意图预判
               # 不建任务，complete_raw 兜底防离线层退化出真实调用。
               llm=SimpleNamespace(offline=True, complete_raw=_reject_call),
               # _v2_presence(app.py:889) 在真机发广播，env 里不钉 no-op 就是
               # NameError；与 evals/checks.py 的 barge_in_timeline 同款处理。
               _v2_presence=lambda _conn, _state: None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


class SpeechPerformanceRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = load_transport()
        self.socket = Socket()
        self.conn = self.env["Connection"](self.socket)
        self.plan = {"version": 1, "source": "llm", "cues": [
            {"intent": "self_reference", "gesture": "self", "anchor": "我觉得",
             "start_char": 0, "end_char": 3, "strength": 0.4}]}
        self.timings = [{"text": "我觉得", "start": 0.1, "end": 0.7}]

    def audio_event(self):
        return {"type": "audio", "audio": b"\x00mp3\xff", "index": 0,
                "text": "我觉得可以试试。", "emotion": "认真",
                "performance": self.plan, "word_timings": self.timings}

    async def test_real_transport_preserves_plan_actual_timings_and_turn_fence(self):
        audio = self.audio_event()
        calls = []
        async def stream(**kwargs):
            calls.append(kwargs)
            yield {"type": "thinking"}
            yield audio
            yield {"type": "done", "text": audio["text"], "emotion": "认真"}
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None, memory_enabled=False: None)
        turn = await self.conn.begin("semantic-turn")
        await self.env["_run_turn"](self.conn, "详细解释", "voice", turn)
        self.assertEqual([e["type"] for e in self.socket.events], ["thinking", "audio", "done"],
                         self.socket.events)
        wire = self.socket.events[1]
        self.assertEqual(wire["performance"], self.plan)
        self.assertEqual(wire["word_timings"], self.timings)
        self.assertEqual(wire["text"], audio["text"])
        self.assertEqual(base64.b64decode(wire["data"]), audio["audio"])
        self.assertNotIn("audio", wire)
        self.assertTrue(all(e["turn_id"] == "semantic-turn" for e in self.socket.events))
        self.assertFalse(turn.live)
        self.assertEqual(calls[0]["voice_emotion"], "base")
        self.assertIn("audio", audio)  # Encoding must not mutate provider events.

    async def test_stale_provider_audio_and_done_cannot_escape_after_replacement(self):
        waiting, release = asyncio.Event(), asyncio.Event()
        async def stream(**kwargs):
            yield {"type": "thinking"}
            waiting.set()
            await release.wait()
            yield self.audio_event()
            yield {"type": "done", "text": "late text"}
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None, memory_enabled=False: None)
        turn = await self.conn.begin("old")
        running = asyncio.create_task(self.env["_run_turn"](self.conn, "解释", "voice", turn))
        await asyncio.wait_for(waiting.wait(), 2)
        await self.conn.begin("replacement")
        release.set()
        await asyncio.wait_for(running, 2)
        self.assertEqual([e["type"] for e in self.socket.events], ["thinking", "interrupted"])
        self.assertTrue(all(e["turn_id"] == "old" for e in self.socket.events))

    async def test_actual_send_lock_rechecks_staleness_before_serializing_audio(self):
        produced = asyncio.Event()
        async def stream(**kwargs):
            produced.set()
            yield self.audio_event()
            yield {"type": "done", "text": "late text"}
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None, memory_enabled=False: None)
        turn = await self.conn.begin("old")
        await self.conn.send_lock.acquire()
        running = asyncio.create_task(self.env["_run_turn"](self.conn, "解释", "voice", turn))
        await asyncio.wait_for(produced.wait(), 2)
        # Change the turn while the real send_turn is waiting for its lock.
        self.conn.turn = self.env["VoiceTurn"]("replacement")
        self.conn.send_lock.release()
        await asyncio.wait_for(running, 2)
        self.assertEqual(self.socket.events, [])

    async def test_no_word_timestamps_are_added_when_provider_has_none(self):
        audio = self.audio_event()
        audio.pop("word_timings")
        audio["performance"] = {"version": 1, "source": "fallback", "cues": []}
        async def stream(**kwargs):
            yield audio
            yield {"type": "done", "text": audio["text"]}
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None, memory_enabled=False: None)
        await self.env["_run_turn"](self.conn, "解释", "voice")
        wire = next(e for e in self.socket.events if e["type"] == "audio")
        self.assertNotIn("word_timings", wire)
        self.assertEqual(wire["performance"], audio["performance"])
        self.assertIn("turn_id", wire)

    async def test_executive_route_ledger_read_does_not_block_the_loop(self):
        """快环幸存项：台账读不得卡住事件循环（判据是行为，不是有没有 to_thread）。

        判据是「注入的同步窗口里事件循环有没有继续跑」，不是「最长停顿 < 10ms」。
        后者量的是墙钟：并发跑两份全量时，同一条断言实测被拖到 13.15ms 而假红
        （2026-11-09）。窗口内的心跳打点数是「循环有没有被让出去」这个事实——
        机器越慢窗口只会越长、打点只会更多，不会把真跑过的绿洗成红。

        做法照 scripts/test_scheduler_health.py：心跳协程用 sleep(0) 记时间戳，
        桩在同步 sleep 前后各记一个时间戳当窗口，数落进窗口的打点数。
        对照臂：同一个桩同步跑在循环上，窗口内打点数必须是 0，证明测量器看得见阻塞。
        """
        async def stream(**kwargs):
            yield {"type": "done", "text": "好", "emotion": "认真"}
        delays = {"n": 0}
        windows: list[tuple[float, float]] = []

        def slow_route(text, executive=None, memory_enabled=False):
            delays["n"] += 1
            opened = time.perf_counter()
            time.sleep(0.04)
            windows.append((opened, time.perf_counter()))
            return None

        self.env["orchestrator"] = SimpleNamespace(
            stream_respond=stream, executive_route=slow_route)

        beats: list[float] = []
        stop = asyncio.Event()

        async def heartbeat():
            while not stop.is_set():
                await asyncio.sleep(0)
                beats.append(time.perf_counter())

        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        turn = await self.conn.begin("ledger-read")
        started = time.perf_counter()
        await self.env["_run_turn"](self.conn, "那单进度怎么样了", "voice", turn)
        elapsed = (time.perf_counter() - started) * 1000
        stop.set()
        await beat

        self.assertEqual(delays["n"], 1, "桩没被调用，测试是空转")
        self.assertGreater(elapsed, 30, f"轮次只花了 {elapsed:.2f} ms，延迟没生效")
        self.assertEqual(len(windows), 1, "桩没有记录到同步窗口")
        opened, closed = windows[0]
        inside = sum(1 for at in beats if opened < at <= closed)
        self.assertGreaterEqual(
            inside, 1,
            f"40ms 同步台账读期间事件循环一次都没被调度（窗口内打点 {inside} 个），"
            "说明它仍在循环上阻塞")

        # 对照：同一段同步延迟直接跑在循环上，窗口内必须一个打点都没有。
        beats.clear()
        windows.clear()
        stop2 = asyncio.Event()

        async def heartbeat2():
            while not stop2.is_set():
                await asyncio.sleep(0)
                beats.append(time.perf_counter())

        beat2 = asyncio.create_task(heartbeat2())
        await asyncio.sleep(0)
        slow_route("对照", None, False)
        stop2.set()
        await beat2
        self.assertEqual(len(windows), 1, "对照没有记录到同步窗口")
        opened, closed = windows[0]
        inside = sum(1 for at in beats if opened < at <= closed)
        self.assertEqual(
            inside, 0,
            f"同步对照窗口内仍有 {inside} 个打点，测量器看不见阻塞")


if __name__ == "__main__":
    unittest.main()
