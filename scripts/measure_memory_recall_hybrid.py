# -*- coding: utf-8 -*-
"""记忆召回 · 改前 / 改后对照实验（2026-11-08）。

**只测量，不改生产配置**；改后臂走的是**真实生产路径**（`MemoryRetriever.search`
的同义桥救援），不是脚本里另写一套扩展逻辑。对齐 docs/MEMORY_RESEARCH_NEXT.md:158
「第三轮：检索与关联」——使用同一记忆集合比较词法 / 混合检索，本轮只做这两臂。

三臂（同一 store、同一 8 条种子、同一 32 条用例、同一 min_score）：

- ``baseline``：改前行为。``retrieval.synonym_bridge.enabled = false``，生产代码原样。
- ``bridge_rescue``：改后行为。生产 ``search()`` 打开同义桥，词表是**一次性临时文件**
  （由 evals/memory_recall_synonyms.tsv 的 8 键合成表转成生产的
  `触发词<TAB>扩展词` 格式）。只在首轮零命中时补一次，基础硬门不变。
- ``gate_off``（诊断臂，不是产品提案）：``min_similarity=0``，其余不变，用来区分
  「卡在门」还是「卡在排序」。**不得写回生产配置**。
- 可选 ``--lexicon PATH``：把现有生产词表直接作为 ``lexicon`` 臂（替代
  ``bridge_rescue``），用于对同一 32 用例做「现词表 / 新词表」前后对照。
  此时不生成临时词表；传入的文件就是被测对象，脚本只读不改。

四条硬约束：
1. **不写真实库**：种子落在 ``MemoryStore(':memory:')``；默认三臂唯一的写盘是临时目录里的
   测量词表（用完即删），不产生可提交产物；``--lexicon`` 模式完全不写盘。
2. **不含真实用户对话**：种子/用例/词表全部人工合成。
3. **不抬高 RECALL_FLOOR**：scripts/test_memory_retrieval_quality.py 保持 0.75；
   本脚本不定义地板线、不判红。
4. **零网络、零 API Key、无墙钟依赖**；不改 ``server/`` 之外的生产配置。

**口径**：本表数字**不是生产召回基准**（合成语料 + 合成概念桥），不能对外陈述
检索质量。它定位「2-gram 硬门是不是瓶颈」以及桥接救援在每一档改述上的读数。

Run: .venv/Scripts/python.exe -B scripts/measure_memory_recall_hybrid.py
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# issue #39：必须在 import 任何 server singleton 之前隔离。
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
with patch("dotenv.load_dotenv", return_value=False):
    from server.memory.retriever import (  # noqa: E402
        MemoryRetriever, parse_bridge_lexicon,
    )
    from server.memory.store import MemoryStore  # noqa: E402

import measure_memory_recall_paraphrase as base  # noqa: E402
from test_memory_retrieval_quality import SHOULD_MISS_UNRELATED  # noqa: E402

SEED = base.SEED
CASES = base.CASES
BRIDGE = ROOT / "evals" / "memory_recall_synonyms.tsv"
BANDS = base.BANDS

# (名称, min_similarity, 是否走生产桥接, 是否诊断臂)
ARMS = (
    ("baseline", 0.08, False, False),
    ("bridge_rescue", 0.08, True, False),
    ("gate_off", 0.0, False, True),
)

# --lexicon 模式：第三臂直接读生产词表，臂名固定为 lexicon。
LEXICON_ARMS = (
    ("baseline", 0.08, False, False),
    ("lexicon", 0.08, True, False),
    ("gate_off", 0.0, False, True),
)


def arms_for(lexicon: Path | None) -> tuple:
    """有 --lexicon 时用三臂对照，否则保持原实验三臂。"""
    return LEXICON_ARMS if lexicon is not None else ARMS


def load_bridge(seeds: dict[str, str], path: Path = BRIDGE) -> dict[str, list[str]]:
    """读实验概念桥，要求与种子 key 一一对应。列数/空词/未知 key 当场报错。"""
    bridge: dict[str, list[str]] = {}
    for number, parts in base.read_tsv(path, 2):
        key, raw = parts
        terms = [term.strip() for term in raw.split(",") if term.strip()]
        if key not in seeds:
            raise ValueError(f"{path.name}:{number} 引用了种子里没有的 key：{key}")
        if key in bridge:
            raise ValueError(f"{path.name}:{number} key 重复：{key}")
        if not terms:
            raise ValueError(f"{path.name}:{number} 概念词为空：{key}")
        bridge[key] = terms
    missing = sorted(set(seeds) - set(bridge))
    if missing:
        raise ValueError(f"{path.name} 缺少这些 key 的概念桥：{missing}")
    return bridge


def materialize_lexicon(seeds: dict[str, str], bridge: dict[str, list[str]],
                        folder: str) -> Path:
    """把实验的 key→概念词表转成生产格式：触发词<TAB>该条记忆的检索文本。

    这是一次性测量词表，不代表生产已覆盖真实口语同义；生产默认词表是空的。
    """
    lines = ["# 测量用一次性词表（合成），由 evals/memory_recall_synonyms.tsv 生成"]
    for key, terms in bridge.items():
        expansion = f"{key} {seeds[key]}"
        for term in terms:
            lines.append(f"{term}\t{expansion}")
    path = Path(folder) / "bridge.tsv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 用生产解析器验一遍，避免测量词表和线上格式悄悄漂移。
    parse_bridge_lexicon(path.read_text(encoding="utf-8-sig"))
    return path


def run_arm(seeds: dict[str, str], cases: list[list[str]],
            bridge: dict[str, list[str]], *, min_similarity: float,
            use_bridge: bool, lexicon_path: Path | None) -> dict:
    """单臂：同一 store 上逐条检索，返回逐条明细 + 控制组。"""
    store = MemoryStore(":memory:")
    try:
        retriever = MemoryRetriever(store)
        retriever.cfg = dict(retriever.cfg)
        # 与既有基线同口径：min_score=0（隔离 min_score=0.12 的二次门），
        # min_similarity 按臂取值。
        retriever.cfg.update(min_score=0, min_similarity=min_similarity,
                             max_inject_chars=600)
        retriever.cfg["synonym_bridge"] = {
            "enabled": bool(use_bridge),
            "lexicon_path": str(lexicon_path or ""),
        }
        ids = {
            key: store.upsert(kind="fact", key=key, value=value,
                              subject="user", source="stated", importance=.5)
            for key, value in seeds.items()
        }

        rows = []
        for canonical, query, key, band in cases:
            # 生产臂要判断救援是否真的触发，先在同一个 retriever 上关掉桥接跑首轮。
            first: list[int] | None = None
            if use_bridge:
                retriever.cfg["synonym_bridge"]["enabled"] = False
                first = [memory.id for memory, _score in retriever.search(query)]
                retriever.cfg["synonym_bridge"]["enabled"] = True
            got = [memory.id for memory, _score in retriever.search(query)]
            sims = retriever._similarity(query, store.retrieval_ranking_rows())
            rows.append({
                "canonical": canonical, "query": query,
                "matched": tuple(k for k, terms in bridge.items()
                                 if any(t in query for t in terms)),
                "expected_key": key, "band": band,
                "hit": ids[key] in got,
                "top1_hit": bool(got) and got[0] == ids[key],
                "retrieved": len(got),
                "rescued": bool(got) and first is not None and not first,
                # 硬门看的就是这个值：< min_similarity 直接 continue（retriever.py 硬门）
                "sim": round(sims.get(ids[key], 0.0), 4),
                "shared_bigrams": len(base.bigrams(query)
                                      & base.bigrams(f"{key} {seeds[key]}")),
            })

        control = []
        for query in SHOULD_MISS_UNRELATED:
            got = [memory.id for memory, _score in retriever.search(query)]
            control.append({"query": query, "found": bool(got),
                            "retrieved": len(got)})
    finally:
        store._conn.close()

    bands = {}
    for band in BANDS:
        subset = [row for row in rows if row["band"] == band]
        hits = sum(row["hit"] for row in subset)
        bands[band] = {
            "n": len(subset), "hit": hits,
            "recall": round(hits / len(subset), 4) if subset else 0.0,
            "top1_hit": sum(row["top1_hit"] for row in subset),
            "rescued": sum(row["rescued"] for row in subset),
            "avg_retrieved": round(sum(row["retrieved"] for row in subset) / len(subset), 3)
            if subset else 0.0,
            "miss": [row for row in subset if not row["hit"]],
        }
    total_hit = sum(row["hit"] for row in rows)
    return {
        "rows": rows, "bands": bands,
        "control": control,
        "totals": {
            "n": len(rows), "hit": total_hit,
            "recall": round(total_hit / len(rows), 4) if rows else 0.0,
            "top1_hit": sum(row["top1_hit"] for row in rows),
            "rescued": sum(row["rescued"] for row in rows),
            "avg_retrieved": round(sum(row["retrieved"] for row in rows) / len(rows), 3)
            if rows else 0.0,
            "zero_shared_miss": sum(row["shared_bigrams"] == 0
                                    for row in rows if not row["hit"]),
            "miss": len(rows) - total_hit,
        },
    }


def measure(seed_path: Path = SEED, cases_path: Path = CASES,
            bridge_path: Path = BRIDGE, lexicon: Path | None = None) -> dict:
    """跑完三臂，返回 {arm: 明细}。纯测量：不写报告、不判红。

    ``lexicon`` 非空时，第三臂直接使用该生产词表（不再合成临时表）；
    词表格式由生产解析器严格校验，坏行当场报错。
    """
    seeds = dict((parts[0], parts[1]) for _n, parts in base.read_tsv(seed_path, 2))
    cases = [parts for _n, parts in base.read_tsv(cases_path, 4)]
    if lexicon is not None:
        lexicon = Path(lexicon)
        bridge = parse_bridge_lexicon(lexicon.read_text(encoding="utf-8-sig"))
        return {
            name: run_arm(seeds, cases, bridge,
                          min_similarity=min_similarity, use_bridge=use_bridge,
                          lexicon_path=lexicon if use_bridge else None)
            for name, min_similarity, use_bridge, _diag in LEXICON_ARMS
        }
    bridge = load_bridge(seeds, bridge_path)
    with tempfile.TemporaryDirectory(prefix="recall-bridge-") as folder:
        lexicon_path = materialize_lexicon(seeds, bridge, folder)
        return {
            name: run_arm(seeds, cases, bridge,
                          min_similarity=min_similarity, use_bridge=use_bridge,
                          lexicon_path=lexicon_path if use_bridge else None)
            for name, min_similarity, use_bridge, _diag in ARMS
        }


def _print_misses(arm: str, result: dict) -> None:
    misses = [row for row in result["rows"] if not row["hit"]]
    if not misses:
        print(f"  {arm} 无漏检")
        return
    print(f"  {arm} 漏检 {len(misses)} 条（原查询 sim / 共享 2-gram / 概念命中）：")
    for row in misses:
        print(f"    [{row['band']:<10}] sim={row['sim']:<7} "
              f"bigrams={row['shared_bigrams']:<2} "
              f"concept={','.join(row['matched']) or '-'}  {row['query']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lexicon", type=Path, default=None,
        help="生产同义桥词表路径；给出时第三臂名改为 lexicon，且不生成临时词表")
    args = parser.parse_args()
    tracks = measure(lexicon=args.lexicon)
    arms = arms_for(args.lexicon)

    print("=== 记忆召回对照实验（同一记忆集合；纯合成语料，非生产基准）===")
    if args.lexicon is not None:
        lexicon_count = len(parse_bridge_lexicon(
            Path(args.lexicon).read_text(encoding="utf-8-sig")))
        print(f"种子 {len(base.read_tsv(SEED, 2))} 条 / 用例 "
              f"{len(base.read_tsv(CASES, 4))} 条 / 生产词表 {lexicon_count} 条")
        print(f"词表：{args.lexicon}（只读，不改文件）")
        print("gate：min_similarity（baseline/lexicon=0.08，gate_off=0）；"
              "min_score=0（与 0.750 (n=32) 基线同口径）\n")
    else:
        print(f"种子 {len(base.read_tsv(SEED, 2))} 条 / 用例 "
              f"{len(base.read_tsv(CASES, 4))} 条 / 概念桥 "
              f"{len(load_bridge(dict((p[0], p[1]) for _n, p in base.read_tsv(SEED, 2))))} key")
        print("gate：min_similarity 按臂取值（baseline/bridge_rescue=0.08，"
              "gate_off=0）；min_score=0（与 0.750 (n=32) 基线同口径）\n")

    print(f"{'band':<12}" + "".join(f"{arm:>28}" for arm, *_ in arms))
    print(f"{'':<12}" + "".join(f"{'hit/n  recall  top1  avg#':>28}"
                                for _arm, *_ in arms))
    for band in BANDS:
        line = f"{band:<12}"
        for arm, *_ in arms:
            item = tracks[arm]["bands"][band]
            line += (f"{item['hit']:>3}/{item['n']:<3}"
                     f"{item['recall']:>7.3f}"
                     f"{item['top1_hit']:>6}"
                     f"{item['avg_retrieved']:>7.3f}        ")
        print(line)
    print(f"{'合计':<12}" + "".join(
        f"{tracks[arm]['totals']['hit']:>3}/{tracks[arm]['totals']['n']:<3}"
        f"{tracks[arm]['totals']['recall']:>7.3f}"
        f"{tracks[arm]['totals']['top1_hit']:>6}"
        f"{tracks[arm]['totals']['avg_retrieved']:>7.3f}        "
        for arm, *_ in arms))

    print("\n--- 控制组：语料中没有对应主题的 6 条查询（应保持 found=False）---")
    for arm, *_ in arms:
        control = tracks[arm]["control"]
        injected = sum(row["found"] for row in control)
        avg = round(sum(row["retrieved"] for row in control) / len(control), 3)
        print(f"{arm:<14} 误命中 {injected}/{len(control)} 条查询，平均注入 {avg} 条")

    print("\n--- 救援触发与漏检归因 ---")
    for arm, *_ in arms:
        totals = tracks[arm]["totals"]
        print(f"  {arm}：救援触发 {totals['rescued']} 条；漏检 {totals['miss']} 条，"
              f"其中与 key+value 零共享 2-gram {totals['zero_shared_miss']} 条")
        _print_misses(arm, tracks[arm])

    if args.lexicon is not None:
        print("\n口径：合成标注集；生产词表只读用于测量，文件内容即被测对象；"
              "RECALL_FLOOR、config/memory.yaml 未改动。")
    else:
        print("\n口径：合成标注集 + 合成概念桥，不得对外陈述检索质量；"
              "RECALL_FLOOR、config/memory.yaml、config/memory_synonyms.tsv 均未改动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
