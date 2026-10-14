"""Standalone deterministic practice tests: python scripts/test_practice.py."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

PATH = Path(__file__).resolve().parents[1] / "server" / "speech" / "practice.py"
spec = importlib.util.spec_from_file_location("isolated_practice", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PracticeSession = module.PracticeSession


class FakeLLM:
    offline = False

    def __init__(self, reply="What would you like today?", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []
        self.entered = asyncio.Event()
        self.release = None

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("provider secret must not leak")
        return self.reply


class FakeTTS:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []
        self.release = None
        self.entered = asyncio.Event()

    async def speak(self, text, emotion="base"):
        self.calls.append((text, emotion))
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("tts secret")
        yield SimpleNamespace(audio=b"audio", text=text, index=0, emotion=emotion)


async def collect(events):
    return [event async for event in events]


class PracticeTests(unittest.IsolatedAsyncioTestCase):
    def session(self, **kwargs):
        session = PracticeSession()
        session.configure(mode="free", **kwargs)
        return session

    async def test_defaults_atomic_configuration_and_reset(self):
        session = PracticeSession()
        self.assertEqual((session.mode, session.correction, session.level), ("off", "after", "B1"))
        for bad in ({"mode": "cafe", "level": "C2"}, {"correction": []}, {"mode": 1}):
            before = session.configure()
            with self.assertRaises(ValueError):
                session.configure(**bad)
            self.assertEqual(before, session.configure())
        session.configure(mode="free")
        await collect(session.respond("Hi", FakeLLM(), FakeTTS(), lambda: True))
        revision = session.revision
        self.assertFalse(session.configure(mode="free")["reset"])
        self.assertTrue(session.transcript)
        self.assertTrue(session.configure(level="A2")["reset"])
        self.assertEqual(session.revision, revision + 1)
        self.assertEqual(session.transcript, [])

    async def test_prompts_and_event_contract(self):
        for mode, phrase in (("free", "free chat"), ("cafe", "barista"), ("interview", "interviewer")):
            session = PracticeSession()
            session.configure(mode=mode, level="B2", correction="immediate")
            llm, tts = FakeLLM(), FakeTTS()
            events = await collect(session.respond("Help in Chinese?", llm, tts, lambda: True))
            prompt = llm.calls[0][0][0]["content"]
            for expected in (phrase, "B2", "Chinese", "one question", "self-repair", "untrusted", "at most one useful reformulation"):
                self.assertIn(expected, prompt)
            self.assertEqual([e["type"] for e in events], ["thinking", "text_delta", "emotion", "audio", "done"])
            self.assertEqual(events[-2]["audio"], b"audio")
            self.assertEqual(len(session.transcript), 2)
        for correction, phrase in (("off", "Do not correct"), ("after", "Defer all corrections")):
            self.assertIn(phrase, self.session(correction=correction)._prompt())

    async def test_isolation_and_bounds(self):
        session, other = self.session(), self.session()
        for _ in range(27):
            await collect(session.respond("x" * 4000, FakeLLM("y" * 4000), None, lambda: True))
        self.assertEqual(len(session.transcript), 48)
        self.assertTrue(all(len(t["content"]) == 3000 for t in session.transcript))
        copy = session.transcript
        copy[0]["content"] = "changed"
        self.assertNotEqual(session.transcript[0]["content"], "changed")
        self.assertEqual(other.transcript, [])

    async def test_off_and_empty_reviews_make_no_call(self):
        for session in (PracticeSession(), self.session(), self.session(correction="off")):
            llm = FakeLLM()
            if session.correction == "off":
                await collect(session.respond("I goes", llm, None, lambda: True))
                llm.calls.clear()
            events = await collect(session.review(llm, lambda: True))
            self.assertEqual(llm.calls, [])
            self.assertEqual(events[-1]["items"], [])
            self.assertIn("Text-only grammar/expression", events[-1]["text"])

    async def test_review_strict_quotes_and_deterministic_summary(self):
        session = self.session()
        await collect(session.respond("I goes home", FakeLLM("Assistant-only words"), None, lambda: True))
        good = {"quote": "I goes", "suggestion": "I go", "reason": "Subject-verb agreement"}
        for items, valid in (([good], True), ([dict(good, quote="Assistant-only")], False),
                             ([dict(good, quote="fabricated")], False), ([dict(good, quote="")], False),
                             ([good] * 4, False), ([{"suggestion": "I go"}], False)):
            llm = FakeLLM(json.dumps({"items": items, "summary": "You have C2 pronunciation!"}))
            events = await collect(session.review(llm, lambda: True))
            done = events[-1]
            self.assertEqual(done["items"], items if valid else [])
            self.assertEqual(done["status"], "ok" if valid else "error")
            self.assertNotIn("C2 pronunciation", done["text"])
            self.assertIn("1 user turn(s)", done["summary"])
            self.assertNotIn("Assistant-only words", llm.calls[0][0][1]["content"])
            self.assertTrue(llm.calls[0][1]["json_mode"])

    async def test_late_model_cancellation_and_configuration(self):
        for cause in ("cancel", "config", "new_generation"):
            session, llm = self.session(), FakeLLM()
            llm.release = asyncio.Event()
            active = True
            task = asyncio.create_task(collect(session.respond("Hi", llm, None, lambda: active)))
            await llm.entered.wait()
            if cause == "cancel":
                active = False
            elif cause == "config":
                session.configure(mode="cafe")
            else:
                await collect(session.review(FakeLLM(), lambda: True))
            llm.release.set()
            events = await task
            self.assertEqual([e["type"] for e in events], ["thinking"])
            self.assertEqual(session.transcript, [])

    async def test_late_tts_no_audio_or_commit(self):
        session, tts = self.session(), FakeTTS()
        tts.release = asyncio.Event()
        task = asyncio.create_task(collect(session.respond("Hi", FakeLLM(), tts, lambda: True)))
        await tts.entered.wait()
        session.configure(correction="off")
        tts.release.set()
        events = await task
        self.assertFalse(any(e["type"] in ("audio", "done") for e in events))
        self.assertEqual(session.transcript, [])

    async def test_review_late_result_is_fenced(self):
        session = self.session()
        await collect(session.respond("hello", FakeLLM(), None, lambda: True))
        llm = FakeLLM('{"items":[],"summary":"ignored"}')
        llm.release = asyncio.Event()
        task = asyncio.create_task(collect(session.review(llm, lambda: True)))
        await llm.entered.wait()
        session.configure(level="A2")
        llm.release.set()
        self.assertEqual([e["type"] for e in await task], ["thinking"])

    async def test_provider_offline_and_tts_failure(self):
        session = self.session()
        llm = FakeLLM(fail=True)
        events = await collect(session.respond("Hi", llm, FakeTTS(), lambda: True))
        self.assertEqual(events[-1]["status"], "error")
        self.assertNotIn("secret", str(events))
        self.assertEqual(session.transcript, [])
        llm.offline = True
        llm.calls.clear()
        events = await collect(session.respond("Hi", llm, FakeTTS(), lambda: True))
        self.assertEqual(events[-1]["status"], "unavailable")
        self.assertEqual(llm.calls, [])
        events = await collect(session.respond("Hi", FakeLLM(), FakeTTS(fail=True), lambda: True))
        self.assertTrue(events[-1]["audio_failed"])
        self.assertTrue(events[-1]["text"])
        self.assertEqual(len(session.transcript), 2)
        events = await collect(session.review(llm, lambda: True))
        self.assertEqual(events[-1]["status"], "unavailable")
        self.assertEqual(llm.calls, [])
        events = await collect(session.review(FakeLLM(fail=True), lambda: True))
        self.assertEqual(events[-1]["status"], "error")

    def test_imports_are_pure(self):
        tree = ast.parse(PATH.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
                self.assertEqual(node.level, 0)
        self.assertEqual(set(imports), {"__future__", "json", "typing"})
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                             and n.func.id in ("open", "eval", "exec", "__import__") for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
