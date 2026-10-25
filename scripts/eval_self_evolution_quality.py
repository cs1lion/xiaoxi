"""Autonomous Mind V2 质量评测：把「管线跑通」和「自进化有价值」分开。

为什么单独立一个评测而不是塞进单元测试：现有 test_reflect_cycle /
test_evolution_soak 只证明**状态机**正确（裂变了、连边了、失败了不消化），
它们的 payload 是手写的，所以既证明不了真实模型的语义质量，也从来不回答
"这一堆种子到底有没有在积累"。本文件回答后一个问题。

六个维度（对应 EVOLUTION_QUALITY_EVAL.md 的四个维度 + 需求新增的两项）：
  1. 来源相关性   source_refs 是否指向本轮真实读到的材料
  2. 子问题新颖性  与父问题/兄弟/历史相比是否带来新内容（新内容占比，非相似度）
  3. 推理连贯性   reason 是否说清了派生依据，且不是模板句
  4. 无空洞泛化   discovery/question 是否只是"进一步深入研究"式套话
  5. 跨轮累积价值  多轮之后 taste/worldview/seeds/edges 是否真的长出了东西
  6. 反重复与反退化  是否出现近义繁殖、换词复述、同一问题重开

用法：
    python -B scripts/eval_self_evolution_quality.py            # 离线脚本化模型
    python -B scripts/eval_self_evolution_quality.py --live     # 真实模型（需配置）

--live 才会评真实语义；离线模式只评管线与结构质量，结论里会明确标注。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 生产库哨兵（#39/#41）：`server.memory.store` 在模块底部建了 `store = MemoryStore()`
# 单例，import 即开库。必须写在下面任何 `from server...` 之前。
import os  # noqa: E402
os.environ.setdefault('DB_PATH', ':memory:')

from server.memory import seeds as seed_mod
from server.memory.seeds import question_novelty, similarity
from server.memory.store import MemoryStore

TODAY = "2026-10-17"

# ---------- 判定规则 ----------

HOLLOW_MARKERS = ("进一步深入研究", "深入研究", "探索更多", "更多可能性", "多种可能",
                  "有待进一步", "持续关注", "值得关注", "引人深思", "耐人寻味",
                  "总而言之", "综上所述", "具有重要价值", "有重要意义")
TEMPLATE_REASONS = ("由本次反思派生", "派生自本次反思", "基于反思", "新问题")
MIN_REASON = 8
NOVELTY_MIN = 0.45
DEDUPE_MAX = 0.86


def judge_reflection(payload: dict, material: list[dict], parent_question: str,
                     existing_questions: list[str]) -> dict:
    """对一轮反思按六维度打分。返回 {dimension: (passed, detail)}。"""
    verdict: dict[str, tuple[bool, str]] = {}
    refl = payload.get("reflection") or {}
    refs = [r for r in (refl.get("source_refs") or []) if isinstance(r, str) and r.strip()]
    available = {item.get("ref") for item in material}

    # 1. 来源相关性
    hits = [r for r in refs if r in available]
    verdict["source_relevance"] = (bool(hits), f"{len(hits)}/{len(refs)} 个引用指向本轮真实材料")

    # 2. 子问题新颖性（与父问题比新内容占比，与历史比相似度）
    children = payload.get("child_seeds") or []
    novelties = [question_novelty(parent_question, c.get("question", "")) for c in children]
    worst_novel = min(novelties) if novelties else 0.0
    verdict["child_novelty_vs_parent"] = (
        worst_novel >= NOVELTY_MIN, f"最低新内容占比 {worst_novel:.2f}（阈值 {NOVELTY_MIN}）")

    # 3. 与既有种子/兄弟的相似度（近义繁殖）
    worst_sim = 0.0
    for child in children:
        question = child.get("question", "")
        for other in existing_questions:
            worst_sim = max(worst_sim, similarity(question, other))
    verdict["no_near_synonym_proliferation"] = (
        worst_sim < DEDUPE_MAX, f"与既有问题最高相似度 {worst_sim:.2f}（阈值 {DEDUPE_MAX}）")

    # 4. 推理连贯性：reason 说清了依据，且不是模板句
    reasons = [(c.get("reason") or "").strip() for c in children]
    template = [r for r in reasons if r in TEMPLATE_REASONS or len(r) < MIN_REASON]
    verdict["reasoning_coherence"] = (not template, f"{len(reasons) - len(template)}/{len(reasons)} 个子问题给了实质派生理由")

    # 5. 无空洞泛化
    texts = [refl.get("discovery", "")] + [c.get("question", "") for c in children]
    hollow = [t for t in texts if any(m in t for m in HOLLOW_MARKERS)]
    verdict["no_hollow_generalization"] = (not hollow, f"{len(hollow)} 处空洞套话")

    return verdict


# ---------- 脚本化"模型"：只用于验证管线，不证明语义 ----------

class ScriptedReflector:
    """按预先写好的 payload 序列回应；模拟一个会反思的模型。"""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    async def complete_raw(self, messages, **kwargs):
        self.calls += 1
        if not self.payloads:
            raise RuntimeError("scripted payloads exhausted")
        return {"text": json.dumps(self.payloads.pop(0), ensure_ascii=False)}


def _payload(discovery, question, reason, refs):
    return {
        "reflection": {"title": discovery[:40], "narrative": f"我翻了他的日记和笔记，想到{discovery}。",
                       "discovery": discovery, "source_refs": refs},
        "child_seeds": [{"title": question[:40], "question": question, "reason": reason,
                         "source_refs": refs}],
        "epiphany_edges": [],
    }


# 正例：相关、有新意、有依据、可执行
GOOD_ROUNDS = [
    _payload("他深夜改稿可能和白天被打断有关",
             "一个人终于获得不被打断的时间时，对原方案的判断会发生什么变化？",
             "白天被切碎会导致晚上补偿性推翻，需要验证完整时间块是否改变判断",
             ["diary:2026-10-17", "note:n1"]),
    _payload("完整时间块似乎比总时长更能预测判断质量",
             "如果把一天切成大小不同的时间块，哪几种块型最容易让人改主意？",
             "从「被打断的代价」转到「块型结构」，需要可度量的分类",
             ["diary:2026-10-17", "note:n1"]),
]

# 反例集：每一轮都该被某一维度判负
BAD_ROUNDS = {
    "paraphrase_of_parent": _payload("原问题值得再想想",
                                     "观察人类为什么总喜欢在深夜做推翻白天的决定？",
                                     "换个说法再问一遍，看看能不能问得更清楚",
                                     ["diary:2026-10-17"]),
    "hollow_generalization": _payload("这个问题值得进一步深入研究",
                                      "是否可以探索更多可能性？",
                                      "原问题还有很多方面可以展开来看",
                                      ["diary:2026-10-17"]),
    "template_reason": _payload("他和白天被打断有关",
                                "不被打断的时候，人的判断会变成什么样？",
                                "由本次反思派生",
                                ["diary:2026-10-17"]),
    "fabricated_source": _payload("他在笔记里写过被打断的事",
                                  "被打断的记忆会怎样改变一个人对时间的估计？",
                                  "笔记里提到了具体次数，需要验证是否形成系统性偏差",
                                  ["diary:1999-01-01", "note:does-not-exist"]),
    # 真正的近义复述：和既有种子只差几个字（相似度实测 0.97，超过 0.86 阈值）
    "near_synonym_of_sibling": _payload("时间块会影响判断",
                                        "数字生命在没有收到指令的空白时间里，“走神”应该是什么感觉呢？",
                                        "换个说法再问一次同一个问题",
                                        ["diary:2026-10-17"]),
}


# ---------- 纵向：连续多轮，看积累还是复读 ----------

def run_longitudinal(rounds: int = 6) -> dict:
    """跑 N 轮反思，统计种子图是长出了新东西还是在换词复述。"""
    from server.assistant.loop import SelfEvolutionCycle

    store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="aqi-eval-")) / "memory.db"))
    store.add_diary(date=TODAY, title="他又半夜改稿", body="嘴上说算了，凌晨又打开。",
                    mood="调侃", energy=0.4, topic_keys=["作息"], source_message_ids=[1])
    seed_mod.bootstrap_seeds(store, now_iso=TODAY)
    notes = [{"id": "n1", "text": "今天一天被消息打断十几次，根本没进入状态。"}]
    cfg = {"enabled": True, "idle_minutes": 1, "max_per_day": 99, "allow_sources": [],
           "max_retries": 0, "max_model_calls_per_cycle": 1, "max_reflection_inputs": 12,
           "similarity_dedupe": DEDUPE_MAX, "child_novelty_min": NOVELTY_MIN}
    clock = {"t": 2_000_000_000.0}

    # 每一轮都必须是一个**真正不同**的问题，否则系统会正确地拒绝它，
    # 纵向统计就退化成"全是失败"，测不到积累。
    branches = [
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
    reflector = ScriptedReflector([
        _payload(discovery, question, reason, ["diary:2026-10-17", "note:n1"])
        for discovery, question, reason in branches[:rounds]
    ])
    cycle = SelfEvolutionCycle(store=store, provider=reflector, cfg=cfg, clock=lambda: clock["t"])

    results = []
    for i in range(rounds):
        clock["t"] += 7200
        import asyncio
        result = asyncio.run(cycle.reflect(idle_seconds=99999, notes_provider=lambda: notes))
        results.append(result)
        clock["t"] += 1

    seeds = store.list_seeds(limit=500)
    questions = [s.get("question", "") for s in seeds]
    statuses = [s.get("status") for s in seeds]
    children = [s for s in seeds if s.get("parent_seed_ids")]
    # 两两相似度：越高说明越像换词复述
    pair_sims = [similarity(a, b) for i, a in enumerate(questions) for b in questions[i + 1:]]
    return {
        "rounds": rounds,
        "ok": sum(1 for r in results if r.get("status") == "ok"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
        "reasons": sorted({r.get("reason") for r in results if r.get("status") != "ok"}),
        "seeds_total": len(seeds),
        "children_forked": len(children),
        "digested": statuses.count("digested"),
        "edges": len(store.list_edges()),
        "reflections": len(store.list_reflections()),
        "max_pair_similarity": round(max(pair_sims), 3) if pair_sims else 0.0,
        "mean_pair_similarity": round(sum(pair_sims) / len(pair_sims), 3) if pair_sims else 0.0,
        "distinct_last_reflected": len({s.get("last_reflected_at") for s in seeds
                                        if s.get("last_reflected_at") is not None}),
    }


def main() -> int:
    # Windows 控制台默认 GBK，打印 ✓/✗ 会直接 UnicodeEncodeError；
    # 评测报告只在终端看，钉成 UTF-8 即可，不碰文件编码。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="用真实模型跑（需配置 key）")
    parser.add_argument("--rounds", type=int, default=6)
    args = parser.parse_args()

    print("=" * 72)
    print("Autonomous Mind V2 自进化质量评测")
    print("=" * 72)
    if args.live:
        print("模式：--live 未实装（需要真实 key 与预算审批）。本文件目前只跑离线结构评测。")
        return 2

    print("\n模式：离线（脚本化模型）—— 只评**管线与结构质量**，不证明真实 LLM 语义质量。\n")

    print("-" * 72)
    print("A. 正例：一轮「好」反思应当六维全过")
    print("-" * 72)
    store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="aqi-eval-")) / "memory.db"))
    store.add_diary(date=TODAY, title="他又半夜改稿", body="嘴上说算了，凌晨又打开。",
                    mood="调侃", energy=0.4, topic_keys=["作息"], source_message_ids=[1])
    seed_mod.bootstrap_seeds(store, now_iso=TODAY)
    notes = [{"id": "n1", "text": "今天一天被消息打断十几次，根本没进入状态。"}]
    parent = store.get_seed("seed_night_decisions")
    material = seed_mod.gather_reflection_material(store, seed=parent, notes_provider=lambda: notes)
    existing = [s.get("question", "") for s in store.list_seeds(limit=99)]
    for index, payload in enumerate(GOOD_ROUNDS):
        verdict = judge_reflection(payload, material, parent["question"], existing)
        bad = [k for k, (ok, _) in verdict.items() if not ok]
        flag = "PASS" if not bad else f"FAIL {bad}"
        print(f"  正例{index + 1}: {flag}")
        for key, (ok, detail) in verdict.items():
            mark = '✓' if ok else '✗'
            print(f"      {mark} {key:32s} {detail}")

    print("\n" + "-" * 72)
    print("B. 反例：每一类低质量输出都必须被对应维度判负")
    print("-" * 72)
    expected = {
        "paraphrase_of_parent": "child_novelty_vs_parent",
        "hollow_generalization": "no_hollow_generalization",
        "template_reason": "reasoning_coherence",
        "fabricated_source": "source_relevance",
        "near_synonym_of_sibling": "no_near_synonym_proliferation",
    }
    caught = 0
    for name, payload in BAD_ROUNDS.items():
        verdict = judge_reflection(payload, material, parent["question"], existing)
        failing = [k for k, (ok, _) in verdict.items() if not ok]
        want = expected[name]
        ok = want in failing
        caught += ok
        mark = '✓' if ok else '✗'
        print(f"  {mark} {name:28s} 命中维度: {failing or '（全过——漏放了！）'}")
    print(f"\n  反例捕获 {caught}/{len(BAD_ROUNDS)}")

    print("\n" + "-" * 72)
    print("C. 纵向：连续多轮是积累还是复读")
    print("-" * 72)
    stats = run_longitudinal(args.rounds)
    for key, value in stats.items():
        print(f"  {key:26s} {value}")
    store.close()

    accumulating = (stats["children_forked"] >= stats["ok"]
                    and stats["mean_pair_similarity"] < 0.5
                    and stats["distinct_last_reflected"] >= min(3, stats["ok"]))
    print(f"\n  纵向判定: {'在积累（长出新分支、相似度低、注意力有分散）' if accumulating else '疑似复读或停滞'}")

    print("\n" + "=" * 72)
    print("结论：离线模式证明的是闸门与统计量在工作，不是思想质量合格。")
    print("      真实语义质量需 --live 实装后按 EVOLUTION_QUALITY_EVAL.md 模板补录。")
    print("=" * 72)
    return 0 if caught == len(BAD_ROUNDS) and accumulating else 1


if __name__ == "__main__":
    raise SystemExit(main())
