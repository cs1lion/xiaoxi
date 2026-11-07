# -*- coding: utf-8 -*-
"""记忆召回 · 口语改述分层测量（2026-11-06）。

**只测量、不改判据**（对齐 scripts/measure_intent_corpus.py:2-4 的定位）：
把「口语改述 → 字符 2-gram 门零命中」从定性结论变成可复现、可归因、分层的
数字，为 docs/MEMORY_RESEARCH_NEXT.md:158 要求的「使用同一记忆集合比较词法 /
语义 / 图扩展」准备**前置基线**。

四条硬约束（改之前先读）：
1. **不写 `data/`**：种子记忆落在 `MemoryStore(':memory:')`，全程零磁盘库；
   run_gates.py:1111-1125 的 TEST_SENTINEL 是第二道机械锁。
2. **不含真实用户对话**：evals/memory_recall_{seed,paraphrase}.tsv 全为人工合成，
   严禁从 data/memory.db 的 messages.text / evidence_quote 复制。
3. **不抬高 RECALL_FLOOR**：scripts/test_memory_retrieval_quality.py:125 保持
   0.75；本脚本不定义任何地板线，不判红、不改退出码语义，只打印数字。
4. **不换向量库 / 不改 retriever**：直接调用现有 `MemoryRetriever.search()`，
   不改 server/ 与 config/memory.yaml；零网络、零 Key、零墙钟依赖。

**口径**：本表的数字**不是生产召回基准**（test_memory_retrieval_quality.py:120-124
已明禁拿合成语料数字对外陈述检索质量）。它回答的是「哪一类改述被词法门挡住、
挡住是因为没有共享 2-gram 还是打分不足」，不是「检索器好不好」。

Run: .venv/Scripts/python.exe -B scripts/measure_memory_recall_paraphrase.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# issue #39：必须在 import 任何 server singleton 之前隔离。
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
with patch("dotenv.load_dotenv", return_value=False):
    from server.memory.retriever import MemoryRetriever  # noqa: E402
    from server.memory.store import MemoryStore  # noqa: E402

SEED = ROOT / "evals" / "memory_recall_seed.tsv"
CASES = ROOT / "evals" / "memory_recall_paraphrase.tsv"
BANDS = ("literal", "synonym", "colloquial", "asr_drop")


def read_tsv(path: Path, columns: int) -> list[tuple[int, list[str]]]:
    """读 TSV，返回 (行号, 列)。注释行与空行跳过；列数不符当场报错。"""
    out: list[tuple[int, list[str]]] = []
    # utf-8-sig：Write 工具在 Windows 上会写 BOM，普通 utf-8 会让首行注释
    # 变成 "\ufeff# ..." 而被当成非法数据行（2026-11-06 首跑实测）。
    text = path.read_text(encoding="utf-8-sig")
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != columns or any(not cell.strip() for cell in parts):
            raise ValueError(f"{path.name}:{number} 列数或空字段不合法：{line[:80]}")
        out.append((number, [cell.strip() for cell in parts]))
    return out


def bigrams(text: str) -> set[str]:
    """复刻 retriever.py:92 的 char 2-gram 视角（不含空格）。"""
    compact = text.replace(" ", "")
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def measure(seed_path: Path = SEED, cases_path: Path = CASES) -> dict:
    """纯测量：返回逐条明细 + 分层汇总。不写任何文件、不判红。"""
    from evals.checks import char_error_rate

    seeds = [(parts[0], parts[1]) for _n, parts in read_tsv(seed_path, 2)]
    cases = [parts for _n, parts in read_tsv(cases_path, 4)]
    by_key = dict(seeds)
    unknown = sorted({parts[2] for parts in cases if parts[2] not in by_key})
    if unknown:
        raise ValueError(f"标注集引用了种子里没有的 key：{unknown}")
    bad_bands = sorted({parts[3] for parts in cases if parts[3] not in BANDS})
    if bad_bands:
        raise ValueError(f"未知 band：{bad_bands}")

    store = MemoryStore(":memory:")
    try:
        retriever = MemoryRetriever(store)
        retriever.cfg = dict(retriever.cfg)
        # 与 test_memory_retrieval_quality.py:158-160 同一隔离口径。
        retriever.cfg.update(min_score=0, min_similarity=.08, max_inject_chars=600)
        ids = {
            key: store.upsert(kind="fact", key=key, value=value,
                              subject="user", source="stated", importance=.5)
            for key, value in seeds
        }
        rows = []
        for canonical, query, key, band in cases:
            got = [memory.id for memory, _score in retriever.search(query)]
            shared = len(bigrams(query) & bigrams(f"{key} {by_key[key]}"))
            rows.append({
                "canonical": canonical, "query": query, "expected_key": key,
                "band": band, "hit": ids[key] in got, "retrieved": len(got),
                "shared_bigrams": shared,
                "cer": round(char_error_rate(canonical, query), 4),
            })
    finally:
        store._conn.close()

    bands = {}
    for band in BANDS:
        subset = [row for row in rows if row["band"] == band]
        hits = sum(row["hit"] for row in subset)
        misses = [row for row in subset if not row["hit"]]
        bands[band] = {
            "n": len(subset), "hit": hits,
            "recall": round(hits / len(subset), 4) if subset else 0.0,
            "mean_cer": round(sum(row["cer"] for row in subset) / len(subset), 4) if subset else 0.0,
            "zero_shared_miss": sum(row["shared_bigrams"] == 0 for row in misses),
            "miss": len(misses),
        }
    total_hit = sum(row["hit"] for row in rows)
    all_misses = [row for row in rows if not row["hit"]]
    return {
        "rows": rows, "bands": bands,
        "totals": {
            "n": len(rows), "hit": total_hit,
            "paraphrase_recall": round(total_hit / len(rows), 4) if rows else 0.0,
            "miss": len(all_misses),
            "zero_shared_miss": sum(row["shared_bigrams"] == 0 for row in all_misses),
        },
    }


def main() -> int:
    result = measure()
    print("=== 记忆召回改述分层测量（纯合成语料，非生产基准）===")
    print(f"{'band':<12} {'n':>3} {'hit':>4} {'recall':>8} {'mean_CER':>10} {'零共享漏检':>10}")
    for band in BANDS:
        item = result["bands"][band]
        print(f"{band:<12} {item['n']:>3} {item['hit']:>4} {item['recall']:>8.3f} "
              f"{item['mean_cer']:>10.3f} {item['zero_shared_miss']:>6}/{item['miss']:<3}")
    total = result["totals"]
    print(f"\nparaphrase_recall = {total['paraphrase_recall']:.3f} (n={total['n']})  "
          f"← 只报数，不设地板线、不判红")
    print(f"零命中中「与 key+value 无共享 2-gram」= "
          f"{total['zero_shared_miss']}/{total['miss']}（其余为 sim < min_similarity）")
    print("口径 1：band 是**构造手法标签，不是难度刻度**。实测命中率与 band 无单调关系"
          "（只错 1 字的 asr_drop 仍留大量 2-gram，换词更彻底的 synonym 反而更低）；"
          "真正的解释变量是 shared_bigrams，不是「口语/ASR/近义」这个分类名。")
    print("口径 2：mean_CER 以短的字面问法为参考，改述更长时编辑距离可超参考长度，"
          "故 >1 属正常，不是「错误率超过 100%」。")
    print("口径 3：合成语料易过拟合，不得对外陈述检索质量；"
          "RECALL_FLOOR 与 server/memory/retriever.py 均未改动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
