# -*- coding: utf-8 -*-
"""memory 检索并发不变量（第九批，2026-10-27）。

上批（第八批）复验确认：`_similarity` 持 `_index_lock` 期间跑
`_ensure_index` 的全量 fit_transform，所有语音连接经 manager 单例共享同一个
retriever，而 memory 系测试零并发覆盖（grep threading/concurrent 零命中）。
本测试把该竞争面钉成不变量，不是性能测试（判据不是快，是不坏）：

1. 无死锁：并发读 + 并发写（写触发索引失效与重建）全部在有限时间内返回；
2. 索引自洽：任意时刻 `_ids` / `_matrix` / `_index_content` 三者同代
   （矩阵行数 == ids 数 == 内容键行数）——若 `_ensure_index` 的重建被改成
   分多次锁内替换，快照会抓到跨代的三代不一致；
3. 空分支行为：空 rows 使索引退空且三代归零自洽，回填后恢复覆盖全部 ids；
4. 最终一致：竞争结束后索引收敛到当前语料。

「撕裂读」（读到半个索引）从外部不可观测：`_similarity` 在锁内 zip ids 与
矩阵行，锁外拿到的分数字典必然对应某一完整代，旧代快照是 retriever 文档化
的可接受语义（最终一致而非线性一致）。故不设跨代比较断言——那种写法只会在
合法的代际切换上假红，本测试首版就犯过（语料永不为空时它还是条空转断言）。

判据是行为：并发把不变量打坏时红，与实现细节（锁的粒度）无关。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", tempfile.mktemp(prefix="mem-conc-", suffix=".db"))

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


def build_store() -> MemoryStore:
    store = MemoryStore(tempfile.mktemp(prefix="mem-conc-", suffix=".db"))
    for i in range(40):
        store.upsert(kind="fact", key=f"并发:条目{i:03d}",
                     value=f"并发写入的第{i:03d}条记忆内容，供索引重建竞争使用",
                     source="stated", importance=0.3, session_id="conc",
                     evidence_state="manual")
    return store


def snapshot_index(r: MemoryRetriever) -> tuple[int, int, int]:
    """锁外快照三代状态：ids 数 / 矩阵行数 / 内容键行数。"""
    with r._index_lock:
        ids = len(r._ids)
        rows = 0 if r._matrix is None else r._matrix.shape[0]
        content = 0 if r._index_content is None else len(r._index_content)
    return ids, rows, content


def main() -> int:
    print("[并发读 + 并发写：索引三代状态自洽，无死锁]")
    store = build_store()
    r = MemoryRetriever(store)
    # 预热：先建一次索引，之后的竞争发生在「已建 → 写失效 → 重建」窗口。
    r.build_context("并发写入的记忆内容", mark_used=False)

    violations: list[str] = []
    errors: list[str] = []
    done = threading.Event()
    reader_rounds = {"n": 0}

    def reader() -> None:
        try:
            while not done.is_set():
                rows = store.retrieval_ranking_rows()
                r._similarity("并发写入的记忆内容", rows)
                ids, matrix_rows, content = snapshot_index(r)
                # 不变量：矩阵与 ids 必须同代；内容键行数 == ids 数
                # （_ensure_index 里三者是一起换的，锁内完成）。
                if not (ids == matrix_rows == content):
                    violations.append(f"index 三代不一致 ids={ids} matrix={matrix_rows} content={content}")
                reader_rounds["n"] += 1
        except Exception as exc:  # noqa: BLE001 - 并发缺陷必须留痕，不能静默
            errors.append(f"reader: {type(exc).__name__}: {exc}")

    def writer(base: int) -> None:
        try:
            for i in range(30):
                # 每笔写都改可检索文本 → _index_content 失效 → 触发重建，
                # 与 reader 的 _similarity 在 _index_lock 上竞争。
                store.upsert(kind="fact", key=f"并发:写轮{base}-{i:03d}",
                             value=f"第{base}号写线程的第{i:03d}次并发写入内容",
                             source="stated", importance=0.3, session_id="conc",
                             evidence_state="manual")
                r.build_context("并发写入的记忆内容", mark_used=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"writer{base}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=reader, name="reader")]
    threads += [threading.Thread(target=writer, args=(n,), name=f"writer{n}")
                for n in (1, 2)]
    for t in threads:
        t.start()
    # 写线程 30 轮，量级秒级；reader 无固定轮数，写完后由 done 收。
    for t in threads[1:]:
        t.join(timeout=120)
    done.set()
    threads[0].join(timeout=30)

    check("无死锁：全部线程在超时前结束",
          all(not t.is_alive() for t in threads),
          "，".join(t.name for t in threads if t.is_alive()) or "ok")
    check("并发读轮数足够（竞争真实发生）", reader_rounds["n"] >= 10,
          f"rounds={reader_rounds['n']}")
    check("索引三代状态全程自洽", not violations,
          "；".join(violations[:3]) or "ok")
    check("无未捕获异常", not errors, "；".join(errors[:3]) or "ok")

    # 竞争结束后索引必须收敛到与当前语料一致（最终一致性）。
    rows = store.retrieval_ranking_rows()
    r._similarity("收敛检查", rows)
    ids, matrix_rows, content = snapshot_index(r)
    check("竞争结束后索引收敛（ids == 语料行数）", ids == len(rows),
          f"ids={ids} rows={len(rows)}")

    print("[空语料分支：retrieval_ranking_rows 空时 _ensure_index 走空分支]")
    # set_disabled 软停用不出现在 retrieval_candidates（SQL 无 disabled 过滤），
    # 所以空分支要靠真实空候选集触发，不是软停用能模拟的。
    sims_empty = r._similarity("空分支查询", [])
    ids, matrix_rows, content = snapshot_index(r)
    check("空候选集下三代归零且分数为空",
          sims_empty == {} and (ids, matrix_rows, content) == (0, 0, 0),
          f"sims={len(sims_empty)} ids={ids} matrix={matrix_rows} content={content}")
    # 退空后可恢复：重新喂真实候选集，索引回填且收敛。
    rows = store.retrieval_ranking_rows()
    sims_back = r._similarity("并发写入的记忆内容", rows)
    ids, matrix_rows, content = snapshot_index(r)
    check("退空后回填：索引恢复且三代自洽",
          bool(sims_back) and ids == matrix_rows == content == len(rows),
          f"sims={len(sims_back)} ids={ids} matrix={matrix_rows} content={content} rows={len(rows)}")
    store.close()

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
