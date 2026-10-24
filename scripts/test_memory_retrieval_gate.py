# -*- coding: utf-8 -*-
"""F1（issue #35 / #30）回归：检索假命中修复——char ngram (2,3) 的相关性桥。

根因（2026-10-24 诊断）：(1,3) 字符 ngram 下，单个共享字（虚字「的」或任何
孤立内容字）在 TF-IDF 里没有消歧上下文；小语料 IDF 学不出它的常见性，无关
中文查询靠一两个字蹭到 sim 0.1-0.35，越过 min_similarity=0.08 → found=True，
「检索为空注入负向约束」的设计约束被架空（幻觉温床）。

修复：ngram 起点升到 2——相关性至少需要一座二元桥；单字查询（无二元组）
显式落空，站在负向约束的安全侧。信号侧不受损：锚点命中反而更干净
（修复前噪声 0.28 与信号 0.5 交叠；修复后噪声 0.0，信号 0.17-0.52）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", tempfile.mktemp(prefix="f1-gate-", suffix=".db"))

from server.memory.store import MemoryStore  # noqa: E402
from server.memory.retriever import MemoryRetriever  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def seeded_retriever() -> tuple[MemoryRetriever, MemoryStore]:
    store = MemoryStore(tempfile.mktemp(prefix="f1-gate-", suffix=".db"))
    anchors = {
        "宠物:猫的名字": "用户养的猫名字叫年糕",
        "作息:写代码时段": "用户可能习惯深夜写代码",
        "爱好:乐器": "用户好像在学吉他",
    }
    for i in range(8):
        store.upsert(kind="fact", key=f"归档:文件夹编号{i:04d}",
                     value=f"库存记录第{i}条：文件夹编号{i:04d}的归档说明与签收单",
                     source="stated", importance=0.3, session_id="gate",
                     evidence_state="manual")
    for key, value in anchors.items():
        store.upsert(kind="fact", key=key, value=value, source="stated",
                     importance=0.8, session_id="gate", evidence_state="manual")
    return MemoryRetriever(store), store


def main() -> int:
    print("[假命中场景：单字/虚字桥必须断，负向约束可达]")
    r, _store = seeded_retriever()
    for query in ["明天天气怎么样", "今天中午吃什么好", "帮我订一张去北京的票",
                  "quantum entanglement lab", "猫"]:
        block, used, found = r.build_context(query, mark_used=False)
        check(f"无关查询不再命中：{query!r}",
              found is False and used == [] and block == "",
              f"found={found} used={used}")

    print("[真命中场景：锚点检索保持，信号更干净]")
    expect = {
        "猫的名字叫什么": "宠物:猫的名字",
        "用户习惯什么时段写代码": "作息:写代码时段",
        "用户最近在学什么乐器": "爱好:乐器",
        "年糕": "宠物:猫的名字",
    }
    rows = r.store.retrieval_candidates()
    name = {m.id: m.key for m in rows}
    for query, want_key in expect.items():
        _block, used, found = r.build_context(query, mark_used=False)
        sims = r._similarity(query, rows)
        top = sorted(sims.items(), key=lambda pair: -pair[1])
        top_key = name.get(top[0][0]) if top else None
        check(f"相关查询仍命中且对位：{query!r}",
              found is True and bool(used) and top_key == want_key,
              f"found={found} top={top_key} want={want_key}")

    print("[软停用语义在新 ngram 下保持（S2 语义回归）]")
    r2, store2 = seeded_retriever()
    rows2 = store2.retrieval_candidates()
    target = next(m for m in rows2 if m.key == "宠物:猫的名字")
    store2.set_disabled(target.id, True)
    _block, used, found = r2.build_context("猫的名字叫什么", mark_used=False)
    check("停用条目出装配", found is False and used == [], f"found={found} used={used}")
    store2.set_disabled(target.id, False)
    _block, used, found = r2.build_context("猫的名字叫什么", mark_used=False)
    check("重新启用即恢复", found is True and bool(used), f"found={found}")

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
