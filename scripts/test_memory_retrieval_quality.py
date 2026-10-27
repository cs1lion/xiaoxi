"""Retrieval quality on a labeled query set: precision / recall + negative-constraint gate.

Run: python -B scripts/test_memory_retrieval_quality.py
Only isolated in-memory SQLite; never touches the application's real memories.

Why this exists: issue #30 / F-1. An unrelated Chinese query scored sim 0.35,
found=True, and three irrelevant records were injected. That incident is the
whole point of this file. A **false hit is worse than a miss**: when nothing
relevant exists, the negative-constraint branch ("no relevant memory") is what
stops the model confabulating a shared experience. A false hit keeps that
branch from ever firing, so the gap gets filled with invention. A miss is the
safe direction; a false hit is the dangerous one.

So the hard assertion here is on the SHOULD_MISS half of the labeled set, not
on recall. Precision and recall are reported as measured numbers because the
number is the deliverable; the pass/fail floor on recall is deliberately soft
and documented, so that raising it means improving the retriever rather than
tuning the queries until they pass.

Labeling convention: every seeded memory owns one distinct topic, and the
queries are phrased the way a user would actually ask. SHOULD_HIT names the
memory that ought to come back; SHOULD_MISS names topics with no seeded memory
at all.

2026-10-26 R1 定性结论（规模复测 + 相似度分布，均为**另案实测**）：
  假命中不随语料规模消失（语料从 8 条扩到 100 条，假命中由 1/8 涨到 3/8），
  所以不是小语料假象；但假命中的 top1 相似度**落在正当命中的区间内部**，
  lexical 层面没有任何阈值能把它们分开。三条里两条（科幻电影、游泳池）本就是
  主题相邻，注入不算错；真正无关的那条（航班几点起飞 ~ 作息:起床）只共享
  天早/早上/点起 三个 bigram 与 天早上 一个 trigram 却拿到 top1 高分——这是 TF-IDF 的固有上限，不是调参问题。因此：
  * 不修 retriever（提高 min_similarity 会连坐正当命中，违背本文件硬约束）；
  * 标注集拆成两个名单：真无关（硬断言）与主题相邻（允许命中，只记录）；
  * KNOWN_FALSE_HITS 重新定性为「TF-IDF 固有上限」而非「待修 bug」。
  要真正解决需要语义检索（embedding），那是另一个量级的决策。

  「另案实测」指：这组数字来自 R1 的独立探针（自建大语料 + filler 主题），
  **本文件的 CORPUS 只有 8 条、复现不了它**；别把这里的比例当成本文件的运行结果。

  具体相似度数字不写在这里：检索器改动就会过期，而本文件无法察觉。带 HEAD/日期
  锚的实测值记在 KNOWN_FALSE_HITS 的注释里，复现方式同处。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
with patch('dotenv.load_dotenv', return_value=False):
    from server.memory.store import MemoryStore
    from server.memory.retriever import MemoryRetriever

# 语料：一条记忆一个主题，彼此用词尽量不重叠，避免"碰巧同主题"的假命中。
CORPUS = [
    ("饮食:饮品偏好", "用户长期喜欢喝绿茶，早上那杯最重要"),
    ("工作:日程", "用户周五下午有一场产品岗面试"),
    ("娱乐:游戏", "用户最近在玩文明六，喜欢慢节奏运营"),
    ("宠物:猫", "用户养了一只叫豆豆的橘猫，三岁"),
    ("健康:过敏", "用户对花生过敏，误食会起疹子"),
    ("居住:城市", "用户住在杭州，搬过去两年了"),
    ("作息:起床", "用户每天早上七点起床，周末也差不多"),
    ("学习:语言", "用户正在自学日语，目标是 N3"),
]

# (用户会怎么问, 期望命中的语料下标)
SHOULD_HIT = [
    ("我喜欢喝什么茶", 0),
    ("周五下午要干什么", 1),
    ("最近在玩什么游戏", 2),
    ("我的猫叫什么名字", 3),
    ("我什么东西不能吃", 4),
    ("我住在哪个城市", 5),
    ("我早上一般几点起", 6),
    ("我在学什么语言", 7),
]

# 真无关：语料里完全没有对应主题，必须 found=False（负向约束要能触发）。
# R1 已定性其中「明天早上的航班几点起飞」与「作息:起床」只共享 天早/早上/点起
# 三个 bigram 与 天早上 一个 trigram 却拿到 top1 高分——TF-IDF 固有上限，
# 带 HEAD 锚的实测值见 KNOWN_FALSE_HITS。复现方式同 KNOWN_FALSE_HITS 注释尾部。
#
# 条数下限：缩小名单等于缩小负向约束的覆盖面，必须显式决策而非静默发生。
# 由 test_unrelated_set_is_not_silently_shrunk 棘轮守住——实测曾有人把一条
# 真无关查询搬进 ADJACENT_OK，7 条测试全绿而没人发现名单从 6 缩到 5。
MIN_UNRELATED = 6
SHOULD_MISS_UNRELATED = [
    "今天天气怎么样",
    "怎么自己换轮胎",
    "比特币现在多少钱一个",
    "毛线钩针怎么起针",
    "明天早上的航班几点起飞",
    "帮我叫个外卖",
]

# 主题相邻：与语料中某条记忆话题相关。命中是合理行为，不判红，只记录。
# 拆开两个名单的理由：把"真无关"和"主题相邻"混成一个名单会让假命中率虚高
# （R1 另案实测：大语料上 3/8 里只有 1/8 是真无关，见 docstring）。
# 注意本文件 CORPUS 只有 8 条、不含电影/运动主题，所以这两条在当前语料下
# 命中 0/2（HEAD bdbf8c1 实测）——它们保留在这里有两个作用：报告里把「相邻」
# 与「真无关」分开列，以及由 test_adjacent_ok_never_enters_the_false_hit_gate
# 锁住「相邻查询不进入假命中硬断言口径」这个形状（否则同一条查询既被硬断言
# 又被豁免，取决于它落在哪个名单）。要量相邻命中需先把对应记忆加进 CORPUS，
# 那是改标注集，须单独一轮并重跑 recall/precision。
ADJACENT_OK = [
    "推荐一部科幻电影",
    "小区游泳池几点开门",
]

# 召回率下限。刻意压低并写清理由：这是"大多数自然问法能命中"的地板线，
# 不是"检索器很好"的证明。要抬高只有两条路——改进检索，或换更贴近真实
# 分布的问法；不允许为了凑数字改问法。
#
# 绝对值不可外推（R1 实测）：语料只有 8 条，n=8 时 TF-IDF 的 IDF 退化，
# 单个共享 bigram 就能主导余弦并越过 min_similarity。所以本文件的正确定位是
# **机制/回归门禁**——证明 bigram 门与负向约束分支的行为符合设计、且能抓住
# 新出现的假命中——而不是生产召回基准。真实库数百条时的 recall/precision 会
# 与本表不同，不要拿这组数字对外陈述检索质量。
RECALL_FLOOR = 0.75

# 已知假命中：测量跑出来、但根因已定性为「TF-IDF 固有上限」的项。列在这里
# 是为了让测试今天能绿，同时不让问题被忘记——每条都带定性结论，且测试
# **反向钉住**「它确实还是假命中」：哪天真修好了（换语义检索），测试立刻红，
# 提醒把条目划掉、让断言自动变严。不许悄悄删条目来凑绿。
KNOWN_FALSE_HITS = [
    # R1 定性：与「作息:起床」只共享 天早/早上/点起 三个 bigram 与 天早上
    # 一个 trigram，top1 相似度却落在正当命中区间内部——lexical 层面无阈值
    # 可分。属 TF-IDF 固有上限，
    # 非待修 bug；要修需上语义检索。
    #
    # 数字随时锚记录（AGENTS.md 三分法：这是"记录某一刻"，不是"陈述现状"）：
    #   HEAD bdbf8c1 / 2026-10-26：本条 top1 = 0.4906，正当命中 top1 区间
    #   0.4421–0.5216（6 条命中，另 2 条 paraphrase 未命中）→ 0.4906 在区间内。
    # 检索器改动后这些数会变，**结论要重测**：若新区间整体高于假命中相似度，
    # 阈值就重新可分，本条应从名单划掉并提高 min_similarity。
    # 复现：import 本模块的 CORPUS/SHOULD_HIT/SHOULD_MISS_UNRELATED，构造
    # 内存库后对每条查询打印 retriever.search(q)[0][1] 与命中的 key。
    "明天早上的航班几点起飞",
]

# 已知夹带：命中了正确记录、但同时注入无关记录的查询。同为假命中症状。
KNOWN_IMPRECISE = [
    # 命中「作息:起床」时，被 id=1「饮食:饮品偏好」一起带出——两者共享
    # bigram「早上」（语料里有「早上那杯最重要」）。R1 审出本条注释原先
    # 指错了记录，按实况更正。
    "我早上一般几点起",
]


class RetrievalQualityTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.retriever = MemoryRetriever(self.store)
        self.retriever.cfg = dict(self.retriever.cfg)
        self.retriever.cfg.update(min_score=0, min_similarity=.08, max_inject_chars=600)
        self.ids = [
            self.store.upsert(kind='fact', key=key, value=value,
                              subject='user', source='stated', importance=.5)
            for key, value in CORPUS
        ]

    def tearDown(self):
        self.store._conn.close()

    def measure(self):
        """跑标注集，返回 (precision, recall, 逐条明细)。"""
        hits = 0
        retrieved_relevant = retrieved_total = 0
        rows = []
        for query, expected_index in SHOULD_HIT:
            expected = self.ids[expected_index]
            got = [m.id for m, _score in self.retriever.search(query)]
            if expected in got:
                hits += 1
            # 精确率排除已登记的已知夹带：那些由 test_precision 单独反向钉住
            # （修好就红、提醒划掉），这里只管「未登记的夹带必须为 0」。
            if query not in KNOWN_IMPRECISE:
                retrieved_total += len(got)
                retrieved_relevant += sum(1 for mid in got if mid == expected)
            rows.append((query, expected, got, got == [expected]))
        recall = hits / len(SHOULD_HIT)
        precision = (retrieved_relevant / retrieved_total) if retrieved_total else 1.0
        return precision, recall, rows

    def test_precision_basis_is_not_empty(self):
        """棘轮：精确率的计算基数不许被登记名单吃空。

        measure() 只把**未登记** KNOWN_IMPRECISE 的查询计入分母。若将来登记
        名单覆盖了全部 SHOULD_HIT，分母为 0，precision 走 else 1.0——一条
        永久绿的假绿。本断言把「登记数 >= 查询数」变成红，逼你把夹带修掉、
        划掉条目，而不是靠扩大名单把指标关掉。
        """
        self.assertLess(len(KNOWN_IMPRECISE), len(SHOULD_HIT),
                        "KNOWN_IMPRECISE 覆盖了全部查询，precision 分母为 0 "
                        "会永久绿——请修检索并划掉条目")

    def test_unrelated_set_is_not_silently_shrunk(self):
        """棘轮：真无关名单不许被静默缩水，登记条目不许被移出它。

        两条独立的静默失效路径，都实测过「全绿而没人发现」：

        ① 把一条真无关查询搬进 ADJACENT_OK——名单从 6 缩到 5，
           test_false_hit_gate_* 与 test_negative_constraint_* 都跟着缩，
           负向约束的覆盖面少了而没有任何测试报警。
        ② 把 KNOWN_FALSE_HITS 的条目移出 SHOULD_MISS_UNRELATED——更要命：
           test_false_hit_gate_unrelated_never_injects 的反向钉住靠
           `q not in self.false_hits()` 判断「假命中已修好」，而 false_hits()
           只遍历 SHOULD_MISS_UNRELATED。条目一移出，反向钉住静默失效，
           已知假命中从此不受任何监控而测试全绿。

        与 test_precision_basis_is_not_empty 同类：防的是「靠改名单凑绿」。
        调 MIN_UNRELATED 是允许的，但必须是显式决策并同步说明理由。
        """
        escaped = [q for q in KNOWN_FALSE_HITS
                   if q not in SHOULD_MISS_UNRELATED]
        self.assertEqual(escaped, [],
                         "KNOWN_FALSE_HITS 的条目被移出了 SHOULD_MISS_UNRELATED，"
                         "反向钉住会静默失效——请移回，或连条目一起划掉")
        self.assertGreaterEqual(
            len(SHOULD_MISS_UNRELATED), MIN_UNRELATED,
            f"真无关名单只剩 {len(SHOULD_MISS_UNRELATED)} 条，低于下限 "
            f"{MIN_UNRELATED}——缩小名单等于缩小负向约束覆盖面，请显式决策"
            "并同步调整 MIN_UNRELATED")

    def false_hits(self):
        """返回当前仍构成假命中的真无关查询列表。"""
        bad = []
        for query in SHOULD_MISS_UNRELATED:
            block, used, found = self.retriever.build_context(query)
            if found or used or block:
                bad.append(query)
        return bad

    def test_adjacent_ok_never_enters_the_false_hit_gate(self):
        """锁住「主题相邻不进入假命中硬断言」这个形状。

        两个名单必须不相交。若同一条查询同时出现在 ADJACENT_OK 与
        SHOULD_MISS_UNRELATED，它就会既被硬断言（不许注入）又被豁免
        （允许命中）——行为取决于它落在哪个名单，而这个测试仍然全绿，
        因为 false_hits() 只遍历 SHOULD_MISS_UNRELATED、且该查询因在
        KNOWN_FALSE_HITS 之外而被断言。这是一处静默的自相矛盾。

        两条断言的分工要说清楚，别高估保护强度：

        (a) 名单不相交 —— **当前有效**，独立变红。实测：把 ADJACENT_OK 的
            条目加进 SHOULD_MISS_UNRELATED，本测试立刻 FAIL。

        (b) false_hits() 返回值不含 ADJACENT_OK 条目 —— **前置守卫，今天
            inert**。它被 (a) 逻辑支配：两名单既然不相交，false_hits() 又
            只遍历 SHOULD_MISS_UNRELATED，返回值就不可能含 ADJACENT_OK
            条目，所以它永远不可能独立变红。且当前语料下这两条相邻查询的
            build_context() 都返回 ('', [], False)（相邻命中 0/2），此时
            就算有人把 false_hits() 改成遍历两名单并集，也是语义上的
            no-op——不改任何数字，不该红。

        它什么时候活：只有相邻查询**真的命中**时才可能红（例如先给 CORPUS
        加了电影主题记忆、又改了 false_hits() 的遍历范围）。按 ADJACENT_OK
        注释，那须单独一轮改标注集并重跑 recall/precision。所以 (b) 是为
        那个未来留的守卫，不是今天的反向钉住——今天别指望它抓东西。
        """
        overlap = sorted(set(ADJACENT_OK) & set(SHOULD_MISS_UNRELATED))
        self.assertEqual(overlap, [],
                         "同一条查询不能既在 ADJACENT_OK（允许命中）又在 "
                         "SHOULD_MISS_UNRELATED（硬断言不许命中）——请二选一")
        leaked = [q for q in self.false_hits() if q in ADJACENT_OK]
        self.assertEqual(leaked, [],
                         "相邻查询不得进入假命中统计口径；若确实需要把它们算成"
                         "假命中，请先从 ADJACENT_OK 移到 SHOULD_MISS_UNRELATED")

    def test_false_hit_gate_unrelated_never_injects(self):
        """硬断言：真无关话题不许注入——否则负向约束永不触发。

        已知假命中除外（KNOWN_FALSE_HITS），但名单里每一条都被**反向钉住**：
        它必须仍然 是假命中。哪条修好了，本测试立刻红，提醒把条目划掉、
        让断言自动变严。不许悄悄删条目来凑绿。
        """
        unexpected = [q for q in self.false_hits() if q not in KNOWN_FALSE_HITS]
        self.assertEqual(unexpected, [],
                         "出现未登记的假命中（负向约束被绕过）：请修检索，"
                         "或带原因登记进 KNOWN_FALSE_HITS")
        healed = [q for q in KNOWN_FALSE_HITS if q not in self.false_hits()]
        self.assertEqual(healed, [],
                         f"这些已知假命中已修好，请从 KNOWN_FALSE_HITS 划掉：{healed}")

    def test_negative_constraint_branch_is_reachable_for_every_miss(self):
        """found=False 时必须真的走到负向约束（上层拼装依赖 build_context 的第三返回值）。

        顺带钉住「空块 + 空 used + found=False」三件套齐全，免得哪天改成
        found=True 但块为空——那比现在的假命中更隐蔽。
        """
        for query in SHOULD_MISS_UNRELATED:
            if query in KNOWN_FALSE_HITS:
                continue
            with self.subTest(query=query):
                self.assertEqual(self.retriever.build_context(query), ("", [], False))

    def test_precision_no_irrelevant_record_alongside_the_relevant_one(self):
        """硬断言：命中时不许夹带无关记录——夹带同样是假命中症状。"""
        unexpected = []
        for query, expected_index in SHOULD_HIT:
            got = [m.id for m, _score in self.retriever.search(query)]
            expected = self.ids[expected_index]
            if (expected in got and got != [expected]
                    and query not in KNOWN_IMPRECISE):
                unexpected.append((query, got))
        self.assertEqual(unexpected, [], "命中时夹带了未登记的无关记录")
        healed = [q for q in KNOWN_IMPRECISE
                  if [m.id for m, _s in self.retriever.search(q)]
                  == [self.ids[[e for qq, e in SHOULD_HIT if qq == q][0]]]]
        self.assertEqual(healed, [],
                         f"这些已知夹带已修好，请从 KNOWN_IMPRECISE 划掉：{healed}")

    def test_recall_meets_the_documented_floor(self):
        precision, recall, _rows = self.measure()
        self.assertGreaterEqual(recall, RECALL_FLOOR,
                                f"召回率 {recall:.2f} 低于地板线 {RECALL_FLOOR}；"
                                "要抬高请改进检索或换更贴近真实分布的问法，"
                                "不许为凑数字改问法")
        self.assertEqual(precision, 1.0, "命中时夹带无关记录")

    def test_report_the_measured_numbers(self):
        """把数字打出来——H3 的交付物就是这个表，不只是通过/失败。"""
        precision, recall, _rows = self.measure()
        false_hits = self.false_hits()
        adjacent_hits = []
        for query in ADJACENT_OK:
            _block, used, found = self.retriever.build_context(query)
            if found or used:
                adjacent_hits.append(query)
        print("\n=== 检索质量（标注集，离线内存库） ===")
        print(f"语料 {len(CORPUS)} 条；SHOULD_HIT {len(SHOULD_HIT)} 条；"
              f"真无关 {len(SHOULD_MISS_UNRELATED)} 条；主题相邻 {len(ADJACENT_OK)} 条")
        print(f"召回率 recall = {recall:.3f}（地板线 {RECALL_FLOOR}）")
        print(f"精确率 precision = {precision:.3f}（未登记夹带口径）")
        print(f"假命中 false hits = {len(false_hits)}/{len(SHOULD_MISS_UNRELATED)}"
              + (f"（已登记: {false_hits}）" if false_hits else "（负向约束可正常触发）"))
        print(f"主题相邻命中 {len(adjacent_hits)}/{len(ADJACENT_OK)}"
              "（允许命中，不判红）")
        print("=== 明细 ===")
        for query, expected_index in SHOULD_HIT:
            expected = self.ids[expected_index]
            got = [m.id for m, _score in self.retriever.search(query)]
            mark = "OK " if expected in got and got == [expected] else "MISS"
            extra = "" if got == [expected] else f"  实际={got}"
            print(f"  [{mark}] {query}  →  {CORPUS[expected_index][0]}{extra}")


if __name__ == '__main__':
    unittest.main()
