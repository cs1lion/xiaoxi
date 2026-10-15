"""V2 自主进化连续浸泡测试（协议 §14-15）：多轮完整自进化，不只看单次成功。

Run: python -B scripts/test_evolution_soak.py
受控数据驱动多轮 reflect()，覆盖：子种子可追溯、跨来源 epiphany、近义不增殖、
已消化种子留史并可再激活、心境跨轮保留与衰减、Self-Taste 有界调整、
失败不假成功、资源上限。主观质量维度见 docs/EVOLUTION_QUALITY_EVAL.md（需真实 LLM）。
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
from server.memory.diary import decay_mood
from server.memory.preference import consider_viewpoint
from server.memory.store import MemoryStore

CFG = {"enabled": True, "idle_minutes": 1, "max_per_day": 100, "max_retries": 1,
       "max_model_calls_per_cycle": 2, "max_reflection_inputs": 6, "similarity_dedupe": 0.86,
       "allow_sources": ["taste", "worldview", "spark"]}
NOTE = "今天一天被消息打断十几次，根本没进入状态。"


class ScriptedProvider:
    def __init__(self, item):
        self.item = item
        self.calls = 0

    async def complete_raw(self, messages, **kwargs):
        self.calls += 1
        if isinstance(self.item, Exception):
            raise self.item
        return {"text": json.dumps(self.item, ensure_ascii=False)}


def payload(child_question, *, discovery, epiphany_from="diary:2026-10-13", epiphany_to="note:n1",
            taste=None):
    refs = ["diary:2026-10-13", "note:n1"]
    body = {
        "reflection": {"title": "反思", "narrative": "我翻了他的日记和笔记，慢慢看出一点关联。",
                       "discovery": discovery, "source_refs": refs},
        "child_seeds": [{"title": child_question[:20], "question": child_question,
                         "reason": "来自本次反思的发现", "source_refs": refs}],
        "epiphany_edges": [{"source_ref": epiphany_from, "target_ref": epiphany_to,
                            "reason": "日记与笔记共同指向同一个判断变化"}],
    }
    if taste:
        body["taste"] = taste
    return body


class EvolutionSoakTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="soak-")) / "memory.db"))
        self.addCleanup(self.store.close)
        self.now = 2_000_000_000.0
        self.store.add_diary(date="2026-10-13", title="他又半夜改稿", body="凌晨又打开。",
                             mood="调侃", energy=0.4, topic_keys=["作息"], source_message_ids=[1])
        S.bootstrap_seeds(self.store, now_iso="2026-10-13")

    async def _round(self, item):
        provider = ScriptedProvider(item)
        cycle = SelfEvolutionCycle(store=self.store, provider=provider, cfg=CFG, clock=lambda: self.now)
        result = await cycle.reflect(idle_seconds=99999,
                                     notes_provider=lambda: [{"id": "n1", "text": NOTE}])
        self.now += 120  # clear the idle gate for the next round
        return result, provider

    async def test_multi_round_forks_traceable_children(self):
        seeds_before = self.store.count_seeds()
        r1, p1 = await self._round(payload("一个人终于获得不被打断的时间时，判断会怎么变？",
                                           discovery="他深夜改稿可能和白天被打断有关"))
        self.assertEqual(r1["status"], "ok")
        self.assertLessEqual(p1.calls, CFG["max_model_calls_per_cycle"], "模型调用有上限")
        self.assertLessEqual(len(r1["trace"]["material_refs"]), CFG["max_reflection_inputs"], "选材有上限")
        parent = next(s for s in self.store.list_seeds() if s["child_seed_ids"])
        cid = parent["child_seed_ids"][0]
        child = self.store.get_seed(cid)
        self.assertEqual(child["parent_seed_ids"], [parent["id"]])
        self.assertEqual(child["source_refs"], ["diary:2026-10-13", "note:n1"])
        edges = self.store.list_edges(kind="parent_child")
        self.assertTrue(any(e["target"] == f"seed:{cid}" and e["reason"] for e in edges), "裂变边带理由")
        self.assertTrue(self.store.list_edges(kind="epiphany"), "跨来源 epiphany 边形成")

        r2, _ = await self._round(payload("把反复切换的杂事集中处理，能减少深夜推翻白天吗？",
                                          discovery="也许杂事切碎了他的白天"))
        self.assertEqual(r2["status"], "ok")
        self.assertGreater(self.store.count_seeds(), seeds_before + 1, "持续裂变，思想树生长")

    async def test_no_near_synonym_proliferation(self):
        await self._round(payload("不被打断时判断会怎么变？", discovery="d1"))
        count_after_first = self.store.count_seeds()
        dup, _ = await self._round(payload("不被打断时判断会怎么变？", discovery="d2"))
        self.assertEqual(dup["status"], "failed", "近义子种子被拒")
        self.assertEqual(dup["reason"], "no_distinct_child")
        self.assertEqual(self.store.count_seeds(), count_after_first, "近义不增殖")

    async def test_digested_seed_keeps_history_and_reactivates(self):
        await self._round(payload("无干扰时间如何改变决策？", discovery="d"))
        parent = next(s for s in self.store.list_seeds() if s["child_seed_ids"])
        self.assertEqual(parent["status"], "digested")
        self.assertTrue(self.store.list_reflections(seed_id=parent["id"]))
        self.assertTrue(parent["child_seed_ids"])
        self.assertIsNotNone(S.transition_seed(self.store, parent["id"], "active"))

    async def test_failure_never_marks_success(self):
        before = {s["id"]: s["status"] for s in self.store.list_seeds()}
        err, _ = await self._round(RuntimeError("provider down"))
        self.assertEqual(err["status"], "failed")
        invalid, _ = await self._round({"reflection": {"narrative": "x", "discovery": "y"}, "child_seeds": []})
        self.assertEqual(invalid["status"], "failed")
        after = {s["id"]: s["status"] for s in self.store.list_seeds()}
        self.assertEqual(before, after, "失败轮不消化任何种子")
        self.assertEqual([r for r in self.store.list_reflections() if r["processed"]], [], "无假成功的反思")

    def test_mood_persists_and_decays(self):
        self.store.save_mood(narrative="总觉得缺个关键环节", reason="逻辑卡点",
                             source_refs=["seed:s"], state="active", expires_at=self.now)
        self.assertEqual(self.store.get_mood()["narrative"], "总觉得缺个关键环节")
        self.assertEqual(decay_mood(self.store, now=self.now - 1)["state"], "active")
        self.assertEqual(decay_mood(self.store, now=self.now, easing_seconds=100)["state"], "easing")
        self.assertEqual(decay_mood(self.store, now=self.now + 101)["state"], "resolved")

    def test_self_taste_adjusts_by_evidence_not_randomly(self):
        ledger = []
        ledger, action = consider_viewpoint(ledger, topic="表达", stance="直接清楚比冗长好",
                                            now=1, prior_signals=0)
        self.assertEqual(action, "ignored", "单次信号不改变人格（防抖）")
        ledger, action = consider_viewpoint(ledger, topic="表达", stance="直接清楚比冗长好",
                                            now=2, prior_signals=1)
        self.assertEqual(action, "added")
        first = ledger[0]["confidence"]
        ledger, _ = consider_viewpoint(ledger, topic="表达", stance="直接清楚比冗长好", now=3)
        self.assertGreater(ledger[0]["confidence"], first)
        capped = [{"topic": "表达", "stance": "s", "confidence": 0.88, "support": 9}]
        out, _ = consider_viewpoint(capped, topic="表达", stance="s", now=4)
        self.assertLessEqual(out[0]["confidence"], 0.9, "置信度有上限，不随机膨胀")


if __name__ == "__main__":
    unittest.main(verbosity=2)
