"""Autonomous Mind V2 质量门禁回归：低质量输出必须被发现，而不是被当成成功。

为什么单独立一套：test_reflect_cycle / test_evolution_soak 的手写 payload 只覆盖
"形状对/形状错"，覆盖不到"形状对但内容是近义复述 / 空洞套话 / 编造来源"——
而那正是自进化最容易退化成的样子。这里把六个质量维度钉成断言。

四层：
  A 单元   新颖度度量、空洞判定、各闸门的拒绝原因、last_reflected_at 盖章
  B 集成   从脚本化 provider 经反思、裂变、连边到持久化的完整链路
  C 纵向   连续多轮：是在积累分支，还是在换词复述
  D 回归   每一类低质量 payload 都必须被拒，且种子不得被消化

明确边界：本测试证明闸门与状态机在工作，**不**证明真实模型的语义质量。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.assistant.loop import SelfEvolutionCycle, validate_reflection_payload
from server.memory import seeds as seed_mod
from server.memory.seeds import question_novelty, similarity
from server.memory.store import MemoryStore

TODAY = "2026-10-17"
PARENT_Q = "观察人类为什么总喜欢在深夜做推翻白天的决定？"

CFG = {"enabled": True, "idle_minutes": 1, "max_per_day": 99, "allow_sources": [],
       "max_retries": 0, "max_model_calls_per_cycle": 1, "max_reflection_inputs": 12,
       "similarity_dedupe": 0.86, "child_novelty_min": 0.45}


def notes():
    return [{"id": "n1", "text": "今天一天被消息打断十几次，根本没进入状态。"}]


def payload(discovery, question, reason, refs):
    return {
        "reflection": {"title": discovery[:40],
                       "narrative": f"我翻了他的日记和笔记，想到{discovery}。",
                       "discovery": discovery, "source_refs": refs},
        "child_seeds": [{"title": question[:40], "question": question, "reason": reason,
                         "source_refs": refs}],
        "epiphany_edges": [],
    }


GOOD = payload("他深夜改稿可能和白天被打断有关",
               "一个人终于获得不被打断的时间时，对原方案的判断会发生什么变化？",
               "白天被切碎会导致晚上补偿性推翻，需要验证完整时间块是否改变判断",
               ["diary:2026-10-17", "note:n1"])


class Reflector:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    async def complete_raw(self, messages, **kwargs):
        self.calls += 1
        if not self.payloads:
            raise RuntimeError("scripted payloads exhausted")
        return {"text": json.dumps(self.payloads.pop(0), ensure_ascii=False)}


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aqi-quality-")
        self.store = MemoryStore(str(Path(self.temp.name) / "memory.db"))
        self.store.add_diary(date=TODAY, title="他又半夜改稿", body="嘴上说算了，凌晨又打开。",
                             mood="调侃", energy=0.4, topic_keys=["作息"], source_message_ids=[1])
        seed_mod.bootstrap_seeds(self.store, now_iso=TODAY)
        self.now = 2_000_000_000.0
        self.material = seed_mod.gather_reflection_material(
            self.store, seed=self.store.get_seed("seed_night_decisions"), notes_provider=notes)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def cycle(self, provider):
        return SelfEvolutionCycle(store=self.store, provider=provider, cfg=CFG,
                                  clock=lambda: self.now)

    async def reflect(self, provider):
        self.now += 7200
        return await self.cycle(provider).reflect(idle_seconds=99999, notes_provider=notes)


class ConfigSurfaceTests(unittest.TestCase):
    """memory.yaml 里写的自进化配置必须真的生效。

    self_evolution_cfg() 只放行 SELF_EVOLUTION_DEFAULTS 里登记过的键，
    漏登记 = 配置项静默失效。max_model_calls_per_cycle / max_retries 曾经
    就是这样变成死配置的（默认值恰好相等，所以一直看不出症状）。
    """

    def test_documented_budget_keys_survive_the_filter(self):
        import io

        import yaml
        from server.memory.evolution import SELF_EVOLUTION_DEFAULTS, self_evolution_cfg

        raw = yaml.safe_load(io.open(
            Path(__file__).resolve().parents[1] / "config" / "memory.yaml", encoding="utf-8").read())
        configured = raw.get("self_evolution") or {}
        cfg = self_evolution_cfg()
        for key in ("max_reflection_inputs", "max_model_calls_per_cycle", "max_retries",
                    "similarity_dedupe", "child_novelty_min"):
            self.assertIn(key, SELF_EVOLUTION_DEFAULTS, f"{key} 没登记，cfg 会把它整掉")
            self.assertEqual(cfg.get(key), configured.get(key),
                             f"{key} 配置值与生效值不一致——过滤器吃了它")

    def test_tuning_the_budget_actually_changes_the_cycle(self):
        """把模型调用上限调到 1，第二轮重试就不该再发生。"""
        temp = tempfile.TemporaryDirectory(prefix="aqi-cfg-")
        try:
            store = MemoryStore(str(Path(temp.name) / "memory.db"))
            store.add_diary(date=TODAY, title="t", body="b", mood="base", energy=0.5,
                            topic_keys=[], source_message_ids=[1])
            seed_mod.bootstrap_seeds(store, now_iso=TODAY)
            clock = {"t": 2_000_000_000.0}
            bad = BAD_CASES["hollow_generalization"]
            result = asyncio.run(SelfEvolutionCycle(
                store=store, provider=Reflector([bad, bad, bad]),
                cfg=dict(CFG, max_retries=0, max_model_calls_per_cycle=1),
                clock=lambda: clock["t"]).reflect(idle_seconds=99999, notes_provider=notes))
            self.assertEqual(result["trace"]["model_calls"], 1, "上限没生效，重试偷偷跑多了")
            store.close()
        finally:
            temp.cleanup()


# ---------- A. 单元：度量与闸门 ----------

class NoveltyMeasureTests(unittest.TestCase):
    def test_paraphrase_scores_far_below_genuine_departure(self):
        """新颖度度量必须把"换措辞"和"新角度"分开——这是它存在的唯一理由。"""
        paraphrase = question_novelty(PARENT_Q, "人类为什么总在深夜推翻白天做过的决定？")
        departure = question_novelty(PARENT_Q, "深夜做出的决定和白天做出的决定，判断质量差在哪里？")
        self.assertLess(paraphrase, 0.45, f"近义复述竟得 {paraphrase:.2f}，闸门会漏")
        self.assertGreaterEqual(departure, 0.45, f"真正不同的子问题只得 {departure:.2f}，闸门会误杀")

    def test_verbatim_copy_scores_zero(self):
        self.assertEqual(question_novelty(PARENT_Q, PARENT_Q), 0.0)

    def test_similarity_alone_cannot_separate_the_two_cases(self):
        """记录这个度量局限：相似度阈值分不开这两类，所以不能只靠它判新颖。"""
        paraphrase = similarity(PARENT_Q, "人类为什么总在深夜推翻白天做过的决定？")
        trivial = similarity("第0个全新的子问题？", "第1个全新的子问题？")
        self.assertLess(paraphrase, 0.86)
        self.assertGreater(trivial, 0.86)
        self.assertGreater(trivial, paraphrase, "改阈值解决不了：两类在相似度轴上顺序是反的")


class GateReasonTests(unittest.TestCase):
    def setUp(self):
        self.material = [{"ref": "diary:2026-10-17", "kind": "diary", "text": "x"},
                         {"ref": "note:n1", "kind": "note", "text": "y"}]

    def reject(self, **overrides):
        base = json.loads(json.dumps(GOOD))
        for key, value in overrides.items():
            if value is None:
                base[key] = None
            else:
                base[key] = value
        return validate_reflection_payload(base, self.material)[1]

    def test_good_payload_passes(self):
        normalized, reason = validate_reflection_payload(GOOD, self.material)
        self.assertIsNot(normalized, reason)

    def test_fabricated_source_refs_are_rejected(self):
        bad = payload("他和白天被打断有关", "不被打断的时候，人的判断会变成什么样？",
                      "笔记里提到了具体次数，需要验证是否形成系统性偏差",
                      ["diary:1999-01-01", "note:does-not-exist"])
        self.assertEqual(validate_reflection_payload(bad, self.material)[1], "source_refs_untraceable")

    def test_missing_source_refs_are_rejected(self):
        self.assertEqual(self.reject(reflection={"title": "t", "narrative": "n" * 30,
                                                 "discovery": "d" * 20, "source_refs": []}),
                         "source_refs_missing")

    def test_template_reason_is_rejected(self):
        bad = payload("他和白天被打断有关", "不被打断的时候，人的判断会变成什么样？",
                      "由本次反思派生", ["diary:2026-10-17"])
        self.assertEqual(validate_reflection_payload(bad, self.material)[1], "child_reason_template")

    def test_too_short_reason_is_rejected(self):
        bad = payload("他和白天被打断有关", "不被打断的时候，人的判断会变成什么样？",
                      "嗯", ["diary:2026-10-17"])
        self.assertEqual(validate_reflection_payload(bad, self.material)[1], "child_reason_template")

    def test_hollow_discovery_is_rejected(self):
        bad = payload("这个问题值得进一步深入研究", "不被打断的时候，人的判断会变成什么样？",
                      "需要可度量的分类才能继续查", ["diary:2026-10-17"])
        self.assertEqual(validate_reflection_payload(bad, self.material)[1], "discovery_hollow")

    def test_hollow_child_question_is_rejected(self):
        bad = payload("他和白天被打断有关", "是否可以探索更多可能性？",
                      "原问题还有很多方面可以展开来看", ["diary:2026-10-17"])
        self.assertEqual(validate_reflection_payload(bad, self.material)[1], "child_question_hollow")

    def test_short_but_concrete_discovery_is_allowed(self):
        """短短一句具体的发现不该被长度误杀——空洞只看套话，不看字数。"""
        bad = payload("他会累", "不被打断的时候，人的判断会变成什么样？",
                      "需要验证疲惫是否是唯一驱动因素", ["diary:2026-10-17"])
        normalized, reason = validate_reflection_payload(bad, self.material)
        self.assertIsNotNone(normalized, reason)

    def test_material_is_optional_for_backward_compatibility(self):
        normalized, reason = validate_reflection_payload(GOOD)
        self.assertIsNotNone(normalized, reason)


# ---------- B. 集成：完整链路 ----------

class ReflectChainTests(_Base):
    async def test_good_reflection_persists_traceable_lineage(self):
        result = await self.reflect(Reflector([GOOD]))
        self.assertEqual(result["status"], "ok", result["trace"])
        parent = self.store.get_seed("seed_night_decisions")
        self.assertEqual(parent["status"], "digested")
        # last_reflected_at 必须盖章，否则调度器的"最久未反思优先"是死阀门
        self.assertIsNotNone(parent["last_reflected_at"])
        child_id = parent["child_seed_ids"][0]
        self.assertEqual(self.store.get_seed(child_id)["parent_seed_ids"], ["seed_night_decisions"])
        self.assertTrue(self.store.list_edges(kind="parent_child"))
        self.assertTrue(self.store.list_edges(kind="related_memory"))
        refl = self.store.list_reflections(seed_id="seed_night_decisions")[0]
        self.assertTrue(refl["processed"])
        # 反思记录的 source_refs 必须能在材料里找到
        self.assertTrue(set(refl["source_refs"]) & {m["ref"] for m in self.material})

    async def test_scheduler_spreads_attention_after_stamping(self):
        """同一批种子里，刚反思过的不该立刻又被选中。"""
        first = await self.reflect(Reflector([GOOD]))
        self.assertEqual(first["status"], "ok")
        parent = self.store.get_seed("seed_night_decisions")
        second = seed_mod.pick_seed_to_reflect(self.store, now=self.now + 10)
        self.assertNotEqual(second["id"], parent["id"],
                            "last_reflected_at 盖章后仍选中同一个：防复读阀门失效")


# ---------- C. 纵向：积累还是复读 ----------

BRANCHES = [
    ("完整时间块可能比总时长更能预测判断质量",
     "一个人终于获得不被打断的时间时，对原方案的判断会发生什么变化？",
     "白天被切碎会导致晚上补偿性推翻，需要验证完整时间块是否改变判断"),
    ("块型结构似乎比块的数量更重要",
     "如果把一天切成大小不同的时间块，哪几种块型最容易让人改主意？",
     "前一轮停在总时长，这一轮需要可度量的块型分类才能继续查"),
    ("改主意的成本可能与承诺的公开程度有关",
     "一个人把计划说出口之后，推翻它所需的证据会变多还是变少？",
     "从时间结构转到承诺结构，这是另一条独立的线索"),
    ("深夜的特殊性可能只是安静，而非时段本身",
     "如果把同样的安静搬到正午，人还会更容易推翻自己吗？",
     "需要区分安静与时段两个变量，否则结论无法归因"),
    ("复盘质量可能取决于间隔而不是总时长",
     "两次复盘之间隔多久，最容易让人看出自己当初判断错在哪？",
     "从单次判断转到多次复盘的间隔效应"),
    ("外部打断可能只是放大了原本就存在的犹豫",
     "一个人本来就犹豫的决定，被打断后改变主意的概率会高多少？",
     "需要验证打断是原因还是放大器，这决定后续要不要继续查"),
]


class LongitudinalTests(_Base):
    async def test_multi_round_accumulates_instead_of_restating(self):
        reflector = Reflector([payload(d, q, r, ["diary:2026-10-17", "note:n1"])
                               for d, q, r in BRANCHES])
        results = [await self.reflect(reflector) for _ in BRANCHES]
        ok = [r for r in results if r["status"] == "ok"]
        self.assertEqual(len(ok), len(BRANCHES),
                         f"健康的多轮探索不该大量失败：{[r.get('reason') for r in results]}")

        seeds = self.store.list_seeds(limit=500)
        children = [s for s in seeds if s.get("parent_seed_ids")]
        self.assertGreaterEqual(len(children), len(ok), "每轮成功反思都应长出一个分支")

        questions = [s.get("question", "") for s in seeds]
        pairs = [similarity(a, b) for i, a in enumerate(questions) for b in questions[i + 1:]]
        mean = sum(pairs) / len(pairs)
        self.assertLess(mean, 0.5, f"种子两两平均相似度 {mean:.2f} 偏高，疑似换词复述")

        stamped = {s.get("last_reflected_at") for s in seeds if s.get("last_reflected_at") is not None}
        self.assertGreaterEqual(len(stamped), min(3, len(ok)), "注意力没有分散，疑似反复啃同一颗")

        self.assertGreaterEqual(len(self.store.list_edges()), len(ok), "边没有随探索增长")
        self.assertEqual(len(self.store.list_reflections()), len(ok))

    async def test_repetitive_rounds_are_refused_not_accumulated(self):
        """反退化：每轮都交同一个换措辞的子问题，树不许疯长。"""
        reflector = Reflector([payload("他又在深夜改稿", f"人类为什么总在深夜推翻白天做过的决定？变体{i}",
                                       "需要验证疲惫是否是唯一驱动因素", ["diary:2026-10-17"])
                               for i in range(4)])
        before = self.store.count_seeds()
        for _ in range(4):
            result = await self.reflect(reflector)
            self.assertEqual(result["status"], "failed", "换措辞的复述不该被当成成功反思")
        self.assertEqual(self.store.count_seeds(), before, "近义繁殖：种子数不该增长")
        digested = [s for s in self.store.list_seeds(limit=99) if s["status"] == "digested"]
        self.assertEqual(digested, [], "失败的反思绝不该把种子标记为已消化")


# ---------- D. 回归：低质量输出必须被发现 ----------

BAD_CASES = {
    "paraphrase_of_parent": payload("原问题值得再想想", PARENT_Q,
                                    "换个说法再问一遍，看看能不能问得更清楚", ["diary:2026-10-17"]),
    "hollow_generalization": payload("这个问题值得进一步深入研究", "是否可以探索更多可能性？",
                                     "原问题还有很多方面可以展开来看", ["diary:2026-10-17"]),
    "template_reason": payload("他和白天被打断有关", "不被打断的时候，人的判断会变成什么样？",
                               "由本次反思派生", ["diary:2026-10-17"]),
    "fabricated_source": payload("他在笔记里写过被打断的事",
                                 "被打断的记忆会怎样改变一个人对时间的估计？",
                                 "笔记里提到了具体次数，需要验证是否形成系统性偏差",
                                 ["diary:1999-01-01", "note:does-not-exist"]),
    "near_synonym_of_existing": payload("时间块会影响判断",
                                        "数字生命在没有收到指令的空白时间里，“走神”应该是什么感觉呢？",
                                        "换个说法再问一次同一个问题", ["diary:2026-10-17"]),
}


class LowQualityRegressionTests(_Base):
    async def _one_bad_case(self, name, bad):
        """每个反例独立建库、独立关库——Windows 上句柄不释放会清不掉临时目录。"""
        temp = tempfile.TemporaryDirectory(prefix="aqi-quality-bad-")
        try:
            store = MemoryStore(str(Path(temp.name) / "memory.db"))
            store.add_diary(date=TODAY, title="他又半夜改稿", body="嘴上说算了，凌晨又打开。",
                            mood="调侃", energy=0.4, topic_keys=["作息"], source_message_ids=[1])
            seed_mod.bootstrap_seeds(store, now_iso=TODAY)
            before = store.count_seeds()
            clock = {"t": self.now}

            async def run():
                return await SelfEvolutionCycle(
                    store=store, provider=Reflector([bad]), cfg=CFG,
                    clock=lambda: clock["t"]).reflect(idle_seconds=99999, notes_provider=notes)

            clock["t"] += 7200
            result = await run()
            self.assertEqual(result["status"], "failed",
                             f"{name} 被当成成功反思——质量闸门漏放")
            self.assertFalse(result["events"], "失败的反思不该产出前台事件")
            digested = [s for s in store.list_seeds(limit=99) if s["status"] == "digested"]
            self.assertEqual(digested, [], f"{name} 失败却消化了种子")
            self.assertEqual(store.count_seeds(), before, f"{name} 失败却长出了种子")
            for refl in store.list_reflections():
                self.assertFalse(refl["processed"], f"{name} 失败却留下了已处理反思")
            store.close()
        finally:
            temp.cleanup()

    async def test_every_bad_case_is_refused_and_never_digests(self):
        for name, bad in BAD_CASES.items():
            with self.subTest(case=name):
                await self._one_bad_case(name, bad)

    async def test_retry_can_recover_after_a_bad_round(self):
        """有限重试：第一轮坏、第二轮好，最终应当成功——坏输出不该毒死整个循环。"""
        cfg = dict(CFG, max_retries=1, max_model_calls_per_cycle=2)
        self.now += 7200
        result = await SelfEvolutionCycle(store=self.store,
                                          provider=Reflector([BAD_CASES["hollow_generalization"], GOOD]),
                                          cfg=cfg, clock=lambda: self.now).reflect(
            idle_seconds=99999, notes_provider=notes)
        self.assertEqual(result["status"], "ok", result["trace"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
