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


def load_transport():
    path = Path(__file__).resolve().parents[1] / "server" / "app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))
             and node.name in {"VoiceTurn", "Connection", "_run_turn"}]
    env = dict(asyncio=asyncio, base64=base64, json=json, time=time, uuid=uuid,
               Any=Any, WebSocket=Socket, settings=SimpleNamespace(tts_default_emotion="base"),
               EMOTIONS=("base", "认真"),
               assistant=SimpleNamespace(should_route=lambda _, session_id='': False,
                                         waiting_input=lambda _: False),
              executive=None)
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
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None: None)
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
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None: None)
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
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None: None)
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
        self.env["orchestrator"] = SimpleNamespace(stream_respond=stream, executive_route=lambda text, executive=None: None)
        await self.env["_run_turn"](self.conn, "解释", "voice")
        wire = next(e for e in self.socket.events if e["type"] == "audio")
        self.assertNotIn("word_timings", wire)
        self.assertEqual(wire["performance"], audio["performance"])
        self.assertIn("turn_id", wire)


if __name__ == "__main__":
    unittest.main()
