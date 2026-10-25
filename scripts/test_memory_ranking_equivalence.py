# -*- coding: utf-8 -*-
"""#31 回归：两阶段检索必须与「全表建对象再排序」**逐条等价**。

这里的判据不是「看起来差不多」。`_legacy_search` 是改动前 `search()` 的
逐行副本（读全表 → 每行建 Memory → 同一套门槛与打分 → 排序 → 取 top_k），
新实现换的只是「先只读排序需要的 13 列，排完再给 top_k 取完整行」。
两者对同一份数据、同一个 query 必须给出**完全相同的 (id, score) 序列**。

同时钉住三件事：
- 打平（同分）时的次序不能变——SQL 的 ORDER BY 是隐性 tie-breaker；
- 13 列投影不能漏掉任何打分真正读到的字段；
- `present_memory` 仍拿到完整记录（不是投影），字段缺失会让它静默丢弃记录。

Run: .venv/Scripts/python.exe -B scripts/test_memory_ranking_equivalence.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 断言「逐条等价」必须把时钟钉住：recency 依赖 time.time()，两次调用若各取
# 一次，分数会差 1e-9 量级，于是每一行都"不相等"而看起来像实现有 bug。
import unittest.mock as mock  # noqa: E402

FROZEN_NOW = 1_800_000_000.0

def freeze_clock():
    return mock.patch("server.memory.retriever.time.time", return_value=FROZEN_NOW)

# 生产库哨兵：import 期就会建模块级 store 单例，必须先钉住库。
os.environ.setdefault("DB_PATH", ":memory:")

from server.config import memory_cfg  # noqa: E402
from server.memory.retriever import MemoryRetriever  # noqa: E402
from server.memory.store import Memory, MemoryStore  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def legacy_search(r: MemoryRetriever, store: MemoryStore, query: str, *,
                  top_k: int | None = None, kinds=None, subject=None,
                  include_done: bool = False) -> list[tuple[Memory, float]]:
    """`search()` 改动前的实现，逐行保留当对照器。不要"顺手优化"它。"""
    cfg = r.cfg
    top_k = int(cfg.get("top_k", 6)) if top_k is None else max(0, int(top_k))
    query = (query or "").strip()
    now = time.time()
    candidates = store.retrieval_candidates(include_done=include_done)
    sims = r._similarity(query, candidates)
    min_similarity = float(cfg.get("min_similarity", 0.08))
    w_imp = float(cfg.get("importance_weight", 0.35))
    w_rec = float(cfg.get("recency_weight", 0.25))
    w_sim = float(cfg.get("similarity_weight", 0.4))
    min_score = float(cfg.get("min_score", 0.0))
    infer_decay_hours = float(memory_cfg()["write"].get("infer_decay_hours", 72))

    out: list[tuple[Memory, float]] = []
    for memory in candidates:
        if memory.status not in (("active", "done") if include_done else ("active",)):
            continue
        if memory.disabled:
            continue
        if kinds and memory.kind not in kinds:
            continue
        if subject and memory.subject != subject:
            continue
        sim = sims.get(memory.id, 0.0)
        if query and sim < min_similarity:
            continue
        if memory.source in ("inferred", "uncertain"):
            age_h = (now - memory.updated_at) / 3600
            if age_h > infer_decay_hours or age_h < 0:
                continue
        score = w_sim * sim + w_imp * memory.importance + w_rec * r._recency(
            memory.updated_at, now)
        score *= 0.5 + 0.5 * memory.confidence
        if score < min_score:
            continue
        score *= r._utility_factor(memory)
        out.append((memory, score))

    out.sort(key=lambda pair: pair[1], reverse=True)
    return out[:top_k]


def seed(store: MemoryStore, *, filler: int = 40) -> None:
    """锚点 + 中性填充 + 打平组（同分记录用于钉 tie-break 次序）。"""
    store.upsert(kind="fact", key="宠物:猫的名字", value="用户养的猫名字叫年糕",
                 source="stated", importance=0.8, evidence_state="manual")
    store.upsert(kind="fact", key="作息:写代码时段", value="用户可能习惯深夜写代码",
                 source="inferred", importance=0.5, evidence_state="manual")
    store.upsert(kind="fact", key="爱好:乐器", value="用户好像在学吉他",
                 source="uncertain", importance=0.5, evidence_state="manual")
    store.upsert(kind="episode", key="旅行:杭州", value="用户去年独自去了杭州",
                 source="stated", subject="user", importance=0.6,
                 evidence_state="manual")
    store.upsert(kind="self_state", subject="self", key="心情:平静",
                 value="我今天心情挺平静", source="stated", importance=0.4,
                 evidence_state="manual")
    # 同分组：同一秒内连续写入，key/value 各不同但 importance/confidence 相同，
    # 分数接近到会打平——专门用来钉「打平时谁在前」。
    for i in range(6):
        store.upsert(kind="fact", key=f"同事:第{i}位", value=f"用户提到过同事第{i}位",
                     source="stated", importance=0.5, evidence_state="manual")
    for i in range(filler):
        store.upsert(kind="fact", key=f"归档:文件夹编号{i:04d}",
                     value=f"库存记录第{i}条：文件夹编号{i:04d}的归档说明与签收单",
                     source="stated", importance=0.3, evidence_state="manual")


def main() -> int:
    print("[1] 主路径：两阶段 vs 全表建对象，逐条等价")
    store = MemoryStore(":memory:")
    seed(store)
    r = MemoryRetriever(store)
    r.cfg = dict(r.cfg)
    r.cfg.update(min_score=0.0, min_similarity=0.08, top_k=6)

    queries = [
        "还记得我的猫叫什么名字吗",
        "猫的名字叫什么",
        "用户习惯什么时段写代码",
        "用户最近在学什么乐器",
        "去年去了哪里旅行",
        "同事",
        "quantum entanglement lab coordinates",
        "",
    ]
    mismatches = 0
    for query in queries:
        # 每次都用全新的对照器实例：索引缓存是实例状态，不能互相影响
        probe = MemoryRetriever(store)
        probe.cfg = r.cfg
        with freeze_clock():
            want = [(m.id, s) for m, s in legacy_search(probe, store, query)]
            got = [(m.id, s) for m, s in r.search(query)]
        same_ids = [i for i, _ in want] == [i for i, _ in got]
        # 逐位相等而不是 isclose：两条路径读的是同一批 numeric，任何差异都说明
        # 输入不同（投影漏了字段），不是浮点噪声。
        same_scores = (len(want) == len(got)
                       and all(a[1] == b[1] for a, b in zip(want, got)))
        if not (same_ids and same_scores):
            mismatches += 1
            print(f"       query={query!r}\n         want={want}\n         got ={got}")
        check(f"等价：{query!r}", same_ids and same_scores,
              f"want={want} got={got}")
    check("全部查询无差异", mismatches == 0, f"{mismatches} 处不等于")

    print("[2] 过滤参数（kinds / subject / include_done）同样等价")
    for label, kwargs in [
        ("kinds=('fact',)", {"kinds": ("fact",)}),
        ("kinds=('episode','self_state')", {"kinds": ("episode", "self_state")}),
        ("subject='self'", {"subject": "self"}),
        ("top_k=1", {"top_k": 1}),
        ("top_k=0", {"top_k": 0}),
    ]:
        probe = MemoryRetriever(store)
        probe.cfg = r.cfg
        with freeze_clock():
            want = [(m.id, s) for m, s in
                    legacy_search(probe, store, "用户 同事 猫", **kwargs)]
            got = [(m.id, s) for m, s in r.search("用户 同事 猫", **kwargs)]
        check(f"等价：{label}", want == got, f"want={want} got={got}")

    print("[3] 停用位与过期推断档：两阶段不能漏掉这些门槛")
    target = next(m for m in store.retrieval_candidates() if m.key == "宠物:猫的名字")
    store.set_disabled(target.id, True)
    ids = [m.id for m, _s in r.search("猫的名字叫什么")]
    check("停用条目不出现在结果里", target.id not in ids, f"ids={ids}")
    store.set_disabled(target.id, False)
    ids = [m.id for m, _s in r.search("猫的名字叫什么")]
    check("重新启用即回来", target.id in ids, f"ids={ids}")

    print("[4] top_k 截断只对幸存者建对象，但拿到的必须是完整记录")
    hits = r.search("猫的名字叫什么")
    check("有命中", bool(hits))
    if hits:
        memory, _score = hits[0]
        # present_memory 会读这些字段；投影里没有它们，若拿投影当记录就会被丢弃
        for field in ("evidence_state", "evidence_quote", "evidence_message_id",
                      "session_id", "decision", "preference_target"):
            check(f"完整记录带 {field}", hasattr(memory, field))
        check("是 Memory 而不是投影", isinstance(memory, Memory),
              f"实际 {type(memory).__name__}")
        block, used, found = r.build_context("猫的名字叫什么", mark_used=False)
        check("build_context 仍能装配出记录", found and bool(used) and "年糕" in block,
              f"found={found} len(block)={len(block)}")
        record = json.loads(block.split("\n")[1])
        check("JSON 记录带 provenance 字段",
              record.get("evidence", {}).get("state") in ("manual", "linked", "legacy"),
              json.dumps(record, ensure_ascii=False)[:200])

    print("[5] 空库与全停用：不炸，且 found=False（负向约束可达）")
    empty = MemoryStore(":memory:")
    er = MemoryRetriever(empty)
    er.cfg = dict(er.cfg)
    er.cfg.update(min_score=0.0, min_similarity=0.08)
    block, used, found = er.build_context("任何东西", mark_used=False)
    check("空库 = 显式没找到", (block, used, found) == ("", [], False),
          f"{(block[:40], used, found)}")
    check("空库 search 不抛", er.search("任何东西") == [])

    print("[6] 打平时的次序与 ORDER BY 一致（隐性 tie-breaker 没丢）")
    tie = MemoryStore(":memory:")
    # 同一时刻写入、importance/confidence 全同：分数只差在 updated_at
    for i in range(8):
        tie.upsert(kind="fact", key=f"同分:第{i}位", value=f"同分记录第{i}位内容",
                   source="stated", importance=0.5, evidence_state="manual")
    tr = MemoryRetriever(tie)
    tr.cfg = dict(tr.cfg)
    tr.cfg.update(min_score=0.0, min_similarity=0.08, top_k=8)
    tie_sql_order = [m.id for m in tie.retrieval_candidates()]
    tie_got = [m.id for m, _s in tr.search("同分记录第")]
    check("打平时结果集完整", sorted(tie_got) == sorted(tie_sql_order),
          f"{tie_got} vs {tie_sql_order}")
    check("打平时次序沿用 SQL 次序", tie_got == tie_sql_order,
          f"{tie_got} vs {tie_sql_order}")

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
