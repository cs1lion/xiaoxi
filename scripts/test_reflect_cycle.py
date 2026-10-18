"""V2 后台反思与强制裂变契约（PROTOCOL §14.2/§14.3）。只用临时库。

Run: python -B scripts/test_reflect_cycle.py
Covers: 一次反思派生可追溯子种子、Seed→digested、related_memory/epiphany 边、
跨来源 epiphany、失败不消化、近义子种子被拒。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.assistant.loop import SelfEvolutionCycle
from server.memory import seeds as S
from server.memory.store import MemoryStore

TODAY = "2026-10-13"
CFG = {"enabled": True, "idle_minutes": 1, "max_per_day": 3,
       "allow_sources": ["taste", "worldview", "spark"], "max_retries": 1,
       "max_model_calls_per_cycle": 3, "max_reflection_inputs": 12, "similarity_dedupe": 0.86}


class ReflectProvider:
    def __init__(self, payload=None, *, error=None, texts=None):
        self.payload = payload
        self.error = error
        self.texts = list(texts or [])
        self.calls = 0

    async def complete_raw(self, messages, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.texts:
            return {"text": self.texts.pop(0)}
        return {"text": json.dumps(self.payload, ensure_ascii=False)}


def _notes():
    return [{"id": "n1", "text": "今天一天被消息打断十几次，根本没进入状态。"}]


def _valid_payload():
    return {
        "reflection": {"title": "深夜与打断", "narrative": "我翻了他的日记和最近的笔记，忽然明白他半夜改稿不只是熬，是白天被切碎了。",
                       "discovery": "他深夜推翻白天，可能和白天频繁被打断有关", "source_refs": [f"diary:{TODAY}", "note:n1"]},
        "child_seeds": [{"title": "无干扰时间", "question": "一个人终于获得不被打断的时间时，对原方案的判断会发生什么变化？",
                         "reason": "来自注意力中断的发现", "source_refs": [f"diary:{TODAY}", "note:n1"]}],
        "epiphany_edges": [{"source_ref": f"diary:{TODAY}", "target_ref": "note:n1",
                            "reason": "日记的完美主义与笔记的注意力中断共同指向判断变化"}]}


class ReflectCycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="reflect-")) / "memory.db"))
        self.now = 2_000_000_000.0
        self.store.add_diary(date=TODAY, title="他又半夜改稿", body="嘴上说算了，凌晨又打开。", mood="调侃",
                             energy=0.4, topic_keys=["作息"], source_message_ids=[1])
        S.bootstrap_seeds(self.store, now_iso=TODAY)

    def tearDown(self):
        self.store.close()

    def _cycle(self, provider):
        return SelfEvolutionCycle(store=self.store, provider=provider, cfg=CFG, clock=lambda: self.now)

    async def test_reflection_forks_traceable_child_and_digests_seed(self):
        provider = ReflectProvider(_valid_payload())
        result = await self._cycle(provider).reflect(idle_seconds=99999, notes_provider=_notes)
        self.assertEqual(result["status"], "ok", result["trace"])
        parent = self.store.get_seed("seed_night_decisions")
        self.assertEqual(parent["status"], "digested")
        self.assertTrue(parent["child_seed_ids"], "parent records the forked child")
        child_id = parent["child_seed_ids"][0]
        child = self.store.get_seed(child_id)
        self.assertEqual(child["parent_seed_ids"], ["seed_night_decisions"])
        # parent_child + related_memory + epiphany edges all persisted
        self.assertEqual(len(self.store.list_edges(kind="parent_child")), 1)
        self.assertTrue(any(e["source"] == f"seed:{child_id}" or e["target"] == f"seed:{child_id}"
                            for e in self.store.list_edges(kind="parent_child")))
        self.assertTrue(self.store.list_edges(kind="related_memory"))
        self.assertTrue(self.store.list_edges(kind="epiphany"))
        # reflection is processed and carries the children
        refls = self.store.list_reflections(seed_id="seed_night_decisions")
        self.assertTrue(refls and refls[0]["processed"])
        self.assertIn(child_id, refls[0]["child_seed_ids"])

    async def test_cross_source_epiphany_edge_has_reason(self):
        provider = ReflectProvider(_valid_payload())
        await self._cycle(provider).reflect(idle_seconds=99999, notes_provider=_notes)
        edges = self.store.list_edges(kind="epiphany")
        self.assertEqual(len(edges), 1)
        self.assertEqual({edges[0]["source"], edges[0]["target"]}, {f"diary:{TODAY}", "note:n1"})
        self.assertIn("注意力中断", edges[0]["reason"])

    async def test_digested_seed_can_be_reactivated(self):
        provider = ReflectProvider(_valid_payload())
        await self._cycle(provider).reflect(idle_seconds=99999, notes_provider=_notes)
        self.assertIsNotNone(S.transition_seed(self.store, "seed_night_decisions", "active"))

    async def test_invalid_reflection_retries_then_fails_without_digesting(self):
        # provider keeps returning a payload with no child seeds -> never digests
        provider = ReflectProvider({"reflection": {"narrative": "嗯", "discovery": "嗯"}, "child_seeds": []})
        result = await self._cycle(provider).reflect(idle_seconds=99999, notes_provider=_notes)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.get_seed("seed_night_decisions")["status"], "active")
        self.assertEqual(self.store.list_reflections(seed_id="seed_night_decisions"), [])
        self.assertGreaterEqual(provider.calls, 2)  # retried, bounded

    async def test_near_duplicate_child_is_refused_and_not_digested(self):
        # the only proposed child restates an existing seed -> no distinct child
        dup = _valid_payload()
        dup["child_seeds"] = [{"title": "重复", "question": "观察人类为什么总喜欢在深夜做推翻白天的决定？",
                               "reason": "原问题已经覆盖了这一点，换个说法再问一遍"}]
        provider = ReflectProvider(dup)
        result = await self._cycle(provider).reflect(idle_seconds=99999, notes_provider=_notes)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "no_distinct_child")
        self.assertEqual(self.store.get_seed("seed_night_decisions")["status"], "active")
        # any reflection left behind must NOT be processed
        for refl in self.store.list_reflections(seed_id="seed_night_decisions"):
            self.assertFalse(refl["processed"])

    async def test_model_failure_is_silent(self):
        provider = ReflectProvider(error=RuntimeError("down"))
        result = await self._cycle(provider).reflect(idle_seconds=99999, notes_provider=_notes)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.get_seed("seed_night_decisions")["status"], "active")


if __name__ == "__main__":
    unittest.main(verbosity=2)
