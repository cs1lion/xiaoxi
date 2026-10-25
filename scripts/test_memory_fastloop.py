# -*- coding: utf-8 -*-
"""#31 回归：记忆检索不得阻塞事件循环。

判据不是「代码里有没有 `asyncio.to_thread`」——那是实现细节，改两行就能
让它消失而行为倒退回同步。这里量的是**行为**：检索期间，事件循环上的其他
协程还能不能跑。

做法：一个心跳协程尽可能快地打点（`sleep(0)`，中位间隔 0.004 ms），
主协程分别用两种方式跑一轮 turn context，看心跳被卡住多久。

坑（第一版就踩了）：不能用 `asyncio.sleep(0.001)` 做心跳。Windows 默认
定时器精度是 15.6 ms，`sleep(1ms)` 实测中位就是 15.7 ms，那个下限会把
所有改进都盖住，看起来「搬走之后仍然阻塞 15 ms」。测量器的分辨率本身
必须远小于被测对象。

Run: .venv/Scripts/python.exe -B scripts/test_memory_fastloop.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
os.environ["TTS_ENGINE"] = "silent"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


async def _heartbeat(stop: asyncio.Event, gaps: list[float]) -> None:
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0)          # 见模块 docstring：1 ms 分辨率不够
        now = time.perf_counter()
        gaps.append((now - last) * 1000)
        last = now


async def _measure(coro_factory) -> tuple[float, float, int]:
    """跑一次 coro，返回 (耗时, 循环最长停顿, 采样数)。"""
    gaps: list[float] = []
    stop = asyncio.Event()
    beat = asyncio.create_task(_heartbeat(stop, gaps))
    await asyncio.sleep(0)
    started = time.perf_counter()
    await coro_factory()
    elapsed = (time.perf_counter() - started) * 1000
    stop.set()
    await beat
    worst = max(gaps) if gaps else 0.0
    return elapsed, worst, len(gaps)


def _build_world(size: int):
    """建一个足够大的库，让检索耗时远超噪声。"""
    from server.memory.store import MemoryStore
    import server.memory.store as store_module

    store = MemoryStore(":memory:")
    store_module.store = store
    store.upsert(kind="fact", key="宠物:猫的名字", value="用户养的猫名字叫年糕",
                 source="stated", importance=0.8, evidence_state="manual")
    for i in range(max(0, size - 1)):
        store.upsert(kind="fact", key=f"归档:文件夹编号{i:04d}",
                     value=f"库存记录第{i}条：文件夹编号{i:04d}的归档说明与签收单",
                     source="stated", importance=0.3, evidence_state="manual")
    return store


async def main_async() -> int:
    # 语料要足够大，否则检索本身只要几毫秒，测量分辨不出「有没有搬走」。
    # 2000 条时同步路径只卡 8.4 ms，与阈值同量级；6000 条拉开到几十毫秒。
    size = int(os.environ.get("FASTLOOP_CORPUS", "6000"))
    print(f"[语料 {size} 条]")

    with patch("dotenv.load_dotenv", return_value=False):
        store = _build_world(size)
        from server.dialogue.orchestrator import Orchestrator
        from server.memory.manager import manager

        orchestrator = Orchestrator()
        query = "还记得我的猫叫什么名字吗"

        # 预热：首次 to_thread 会现建线程池，首次检索会建 TF-IDF 索引。
        # 这两笔都是**一次性**成本，不该算进「每轮阻塞」。
        manager.retriever.build_context(query, mark_used=False)
        await orchestrator._turn_context_async(query, "warm", [])

        print("[1] 同步调用（改动前 stream_respond 的做法）会卡住循环")
        def run_sync():
            orchestrator._turn_context(query, "p", [])

        sync_elapsed, sync_worst, _ = await _measure(
            lambda: asyncio.to_thread(lambda: None) if False else _run_sync(orchestrator, query))
        print(f"       耗时 {sync_elapsed:6.2f} ms，循环最长停顿 {sync_worst:6.2f} ms")
        check("同步路径确实会阻塞（证明这个测量能看见阻塞）",
              sync_worst > 20, f"最长停顿仅 {sync_worst:.2f} ms，测量器可能没生效")

        print("[2] async 调用（改动后，走 _turn_context_async）不卡循环")
        async_elapsed, async_worst, samples = await _measure(
            lambda: orchestrator._turn_context_async(query, "p", []))
        print(f"       耗时 {async_elapsed:6.2f} ms，循环最长停顿 {async_worst:6.2f} ms，"
              f"采样 {samples} 次")

        # 判据用绝对阈值而不是「比同步路径快多少」：真正的契约是「快环里不该有
        # 几十毫秒的同步停顿」，与今天这条路径恰好多慢无关。留 1 个数量级的余量。
        check("检索不再阻塞事件循环（最长停顿 < 10 ms）",
              async_worst < 10.0, f"最长停顿 {async_worst:.2f} ms")
        check("检索确实搬到了线程（改动前 > 20 ms 的停顿现在消失）",
              async_worst < sync_worst / 2,
              f"同步 {sync_worst:.2f} ms vs 异步 {async_worst:.2f} ms")

        print("[3] 卸载的只是检索：结果必须与同步路径完全一致")
        # 两条路径必须用**不同**的 session_id：`examples` 有会话级冷却
        # （composer 记最近 4 轮用过的示例，防止重复同一句口头禅），同 session
        # 连跑两次第二次本就该没有 examples——那不是两条路径不一致，是冷却生效。
        sync_ctx = orchestrator._turn_context(query, "cmp-sync", [])
        async_ctx = await orchestrator._turn_context_async(query, "cmp-async", [])
        check("记忆块相同",
              sync_ctx["data"]["long_term_memory"] == async_ctx["data"]["long_term_memory"],
              "memory 投影不一致")
        check("used_ids 相同",
              sync_ctx["used_ids"] == async_ctx["used_ids"],
              f"{sync_ctx['used_ids']} vs {async_ctx['used_ids']}")
        check("整段 messages 相同",
              sync_ctx["messages"] == async_ctx["messages"],
              "渲染后的 messages 不一致")
        check("两条路径都拿到了示例（冷却未干扰比对）",
              bool(sync_ctx["data"]["response_policy"].get("examples")) and
              bool(async_ctx["data"]["response_policy"].get("examples")),
              "examples 为空，比对其实没覆盖到")

        print("[4] 记忆关闭时不进线程（省一次无谓调度）")
        calls = {"n": 0}
        original = orchestrator._retrieve_memory_block
        def counting(text):
            calls["n"] += 1
            return original(text)
        orchestrator._retrieve_memory_block = counting
        was_enabled = manager.enabled
        manager.enabled = False            # property，不是可 patch 的普通属性
        try:
            await orchestrator._turn_context_async(query, "off", [])
        finally:
            manager.enabled = was_enabled
            orchestrator._retrieve_memory_block = original
        check("manager 关闭时不检索", calls["n"] == 0, f"仍调用了 {calls['n']} 次")

        store._conn.close()

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


async def _run_sync(orchestrator, query):
    """在协程里直接同步跑，模拟改动前 stream_respond 的行为。"""
    orchestrator._turn_context(query, "p", [])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
