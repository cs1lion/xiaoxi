# -*- coding: utf-8 -*-
"""多连接共享同一 retriever 实例的并发检索门禁（第四十五批，2026-10-27）。

第八批判定索引 convoy「当前规模接受并记录」，第三十批查清生产形态：
所有语音连接经 manager 单例（server/memory/manager.py:383）共享同一个
`manager.retriever`（manager.py:59 自建实例），并发检索与并发写触发的索引
重建都压在这一个实例的 `_index_lock` 上。第十四批的 memory_concurrency 测
的是索引不变量，但每个用例各自建独立 store/retriever——从未钉住「多线程
调用**同一个** retriever 实例」这一生产真实形态。本门禁补上这一环。

**钉什么、为什么不钉「逐位相等」**（本批的实测教训，写下来免得后人重踩）：
本门禁第一版要求「并发读的返回值与无并发单线程逐位相等」，连红两次。
原因不是并发破坏了检索，是 TF-IDF 的 idf 依赖文档总数 n——
idf = log((1+n)/(1+df)) + 1，任何插入（哪怕与查询零重合）都改变全部
词的 idf，近 tie 的尾部行因此合法换序。这是正确行为，不是损坏：语料变
了结果就该变。并发真正要保证的是另外四件事：

1. **无死锁**：N 个线程共享一个实例，一半并发检索、一半并发 upsert
   （改可检索文本 → `_index_content` 失效 → 触发重建），全部在超时前
   结束。RLock 可重入 + 单临界区，设计上不该死；
2. **无撕裂读**：每次读返回的 (block, used_ids, found) 内部自洽——
   block 里各行 JSON 的 id 序列与 used_ids 完全相同、found 与 used_ids
   的非空一致。锁内 zip(ids, sims) 的契约若被破坏（如重建被改成多次
   锁内替换），这里会红。

   **关于这条断言的诚实定位**（第四十五批判别力探针的结论）：此刻它在
   单锁设计下**不可观察**——探针注入「先换 _matrix 后换 _ids + 2ms
   窗口」的破坏版 _ensure_index，2000 次锁外快照抓到 0 次不一致，因为
   破坏版的窗口在 `with self._index_lock:` 块内，reader 的快照被同一把
   锁挡住，造不出撕裂态。也就是说它防的是一个**尚未存在**的风险。保留
   而不删除，是因为它是「有牙齿的惰性断言」：一旦未来有人把重建改成
   多次锁内替换（本仓第二十一批清点时评估过的方案），这条会立刻红。
   不要因为它此刻常绿就以为它在防护什么正在发生的事。
3. **该稳的必须稳**：零匹配查询（ASCII 探针，与中文语料零重合）在任何
   语料下都返回 ("", [], False)；各匹配查询的 top-1（显著占优的锚点行）
   不被并发写入挤掉；
4. **竞争结束后实例未被打坏**：共享实例在收敛后的连续两次调用结果一致，
   且与一个在最终语料上全新构建的实例逐位相等——即并发撬动没有在索引
   状态里留下不一致。

判据全是行为与值相等，无墙钟依赖，故可进 ALWAYS_REQUIRED。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_PATH", tempfile.mktemp(prefix="shared-retr-", suffix=".db"))

from server.memory.retriever import MemoryRetriever  # noqa: E402
from server.memory.store import MemoryStore  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

READERS = 4
WRITERS = 3
WRITER_ROUNDS = 20
READER_ROUNDS = 40
SEED_ROWS = 300

# 零匹配探针：ASCII 串与中文语料零 ngram 重合，任何语料下都必须落空。
QUERY_MISS = "quantum entanglement lab"
QUERIES = [
    "用户养的猫叫什么名字",
    "用户习惯什么时段写代码",
    "文件夹编号0123的归档说明",
    QUERY_MISS,
]


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def seed(store: MemoryStore) -> None:
    for i in range(SEED_ROWS):
        store.upsert(kind="fact", key=f"归档:文件夹编号{i:04d}",
                     value=f"库存记录第{i}条：文件夹编号{i:04d}的归档说明与签收单",
                     source="stated", importance=0.3, session_id="seed",
                     evidence_state="manual")
    store.upsert(kind="fact", key="宠物:猫的名字", value="用户养的猫名字叫年糕",
                 source="stated", importance=0.8, session_id="seed",
                 evidence_state="manual")
    store.upsert(kind="fact", key="作息:写代码时段", value="用户可能习惯深夜写代码",
                 source="inferred", importance=0.6, session_id="seed",
                 evidence_state="manual")


def apply_cfg(retriever: MemoryRetriever) -> None:
    retriever.cfg = dict(retriever.cfg)
    retriever.cfg.update({"min_score": 0, "min_similarity": 0.05,
                          "max_inject_chars": 4000})


def block_ids(block: str) -> list[int]:
    return [json.loads(line)["id"] for line in block.splitlines()
            if line.startswith("{")]


def main() -> int:
    store = MemoryStore(":memory:")
    seed(store)
    retriever = MemoryRetriever(store)  # 全部线程共享这一个实例
    apply_cfg(retriever)

    errors: list[str] = []
    torn: list[str] = []
    miss_bad: list[str] = []
    top1_bad: list[str] = []
    reader_rounds = {"n": 0}

    # 竞争前的 top-1（锚点行显著占优，并发写不该把它挤掉）。
    top1_before = {q: retriever.build_context(q, mark_used=False)[1][:1]
                   for q in QUERIES if q != QUERY_MISS}

    def reader() -> None:
        try:
            for i in range(READER_ROUNDS):
                q = QUERIES[i % len(QUERIES)]
                block, used, found = retriever.build_context(q, mark_used=False)
                reader_rounds["n"] += 1
                if q == QUERY_MISS:
                    if (block, used, found) != ("", [], False):
                        miss_bad.append(f"{q!r} -> {(block[:40], used, found)!r}")
                    continue
                # 内部自洽：block 的 id 序列与 used_ids 逐一对应，found 与非空一致。
                if block_ids(block) != used or found != bool(used) or not used:
                    torn.append(f"{q!r}: ids={block_ids(block)} used={used} found={found}")
                elif used[:1] != top1_before[q]:
                    top1_bad.append(f"{q!r}: top1={used[:1]} want={top1_before[q]}")
        except Exception as exc:  # noqa: BLE001 - 并发缺陷必须留痕
            errors.append(f"reader: {type(exc).__name__}: {exc}")

    def writer(base: int) -> None:
        try:
            for i in range(WRITER_ROUNDS):
                # 每笔写都改可检索文本 → _index_content 失效 → 下次检索重建，
                # 与 reader 的 _similarity 在 _index_lock 上竞争（convoy 真实
                # 发生）。语料与全部基线查询零 ngram 重合：不制造假的结果
                # 漂移（漂移是 idf 的合法行为，见模块 docstring）。
                store.upsert(kind="fact", key=f"并发:写轮{base}-{i:03d}",
                             value=f"无关填充{base}号第{i}次，与全部基线查询零重合",
                             source="stated", importance=0.3, session_id="conc",
                             evidence_state="manual")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"writer{base}: {type(exc).__name__}: {exc}")

    print(f"[{READERS} 读 + {WRITERS} 写共享同一 retriever："
          f"无死锁、无撕裂读、该稳的稳]")
    threads = [threading.Thread(target=reader, name=f"reader{k}")
               for k in range(READERS)]
    threads += [threading.Thread(target=writer, args=(n,), name=f"writer{n}")
                for n in range(WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    check("无死锁：全部线程在超时前结束",
          all(not t.is_alive() for t in threads),
          "，".join(t.name for t in threads if t.is_alive()) or "ok")
    check("并发读轮数足够（竞争真实发生）", reader_rounds["n"] >= READERS * 10,
          f"rounds={reader_rounds['n']}")
    check("无未捕获异常", not errors, "；".join(errors[:2]) or "ok")
    check("无撕裂读：每次读 block 与 used_ids 内部自洽", not torn,
          "；".join(torn[:2]) or "ok")
    check("零匹配查询在任意语料下都落空", not miss_bad,
          "；".join(miss_bad[:2]) or "ok")
    check("匹配查询的 top-1 锚点不被并发写挤掉", not top1_bad,
          "；".join(top1_bad[:2]) or "ok")

    print("[竞争结束后：共享实例未被并发打坏]")
    drifted = []
    for q in QUERIES:
        first = retriever.build_context(q, mark_used=False)
        second = retriever.build_context(q, mark_used=False)
        fresh = MemoryRetriever(store)
        apply_cfg(fresh)
        fresh_result = fresh.build_context(q, mark_used=False)
        if first != second:
            drifted.append(f"{q!r}: 连续两次调用不一致")
        if first != fresh_result:
            drifted.append(f"{q!r}: 共享实例与全新实例不等")
    check("收敛后共享实例稳定且与全新实例逐位相等", not drifted,
          "；".join(drifted[:2]) or "ok")
    store.close()

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
