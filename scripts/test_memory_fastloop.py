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
        # 线程池是按事件循环建的：asyncio.run 退出会 shutdown 默认执行器，
        # 所以每个 asyncio.run 段落前都要重新预热一次，否则首次 to_thread
        # 的建池成本（实测约 3.5ms）会落进被测间隙，负载下叠加调度抖动
        # 就把 10ms 阈值打穿（2026-10-27 实测 11.93ms 假红）。
        manager.retriever.build_context(query, mark_used=False)
        await orchestrator._turn_context_async(query, "warm", [])
        await asyncio.to_thread(lambda: None)

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

        print("[5] 心境衰减与 mark_used 也不再阻塞事件循环")
        # 生产里这两个操作是微秒级，心跳分辨不出来。判据换成「即使它们慢，
        # 循环也不许被卡住」：两段各注入 40ms 假延迟，对应库写入抖动几十
        # 毫秒的真实形状。同步路径先用同样的延迟做对照——若对照不卡，说
        # 明这个测量器看不见这种停顿，「异步不卡」就是测量失灵。
        slow = {"mood": 0, "mark_used": 0}
        original_mood = orchestrator._circadian_and_mood
        original_mark = orchestrator._mark_used

        def slow_mood(*, enabled):
            slow["mood"] += 1
            time.sleep(0.04)
            return original_mood(enabled=enabled)

        def slow_mark(used_ids, session_id, used_lesson_ids):
            slow["mark_used"] += 1
            time.sleep(0.04)
            return original_mark(used_ids, session_id, used_lesson_ids)

        orchestrator._circadian_and_mood = slow_mood
        orchestrator._mark_used = slow_mark
        try:
            ctrl_elapsed, ctrl_worst, _ = await _measure(
                lambda: _run_sync(orchestrator, query))
            print(f"       对照（同步路径）：耗时 {ctrl_elapsed:6.2f} ms，"
                  f"循环最长停顿 {ctrl_worst:6.2f} ms")
            check("对照路径里两段延迟都生效了（对照不是空转）",
                  slow["mood"] >= 1 and slow["mark_used"] >= 1,
                  f"mood={slow['mood']} mark_used={slow['mark_used']}")
            check("注入延迟后同步路径确实卡住（证明测量器看得见）",
                  ctrl_worst > 20, f"最长停顿仅 {ctrl_worst:.2f} ms")
            slow["mood"] = slow["mark_used"] = 0
            slow_elapsed, slow_worst, slow_samples = await _measure(
                lambda: orchestrator._turn_context_async(query, "slow", []))
            print(f"       异步路径：耗时 {slow_elapsed:6.2f} ms，"
                  f"循环最长停顿 {slow_worst:6.2f} ms，采样 {slow_samples} 次")
            check("异步路径里两段卸载都跑到了",
                  slow["mood"] >= 1 and slow["mark_used"] >= 1,
                  f"mood={slow['mood']} mark_used={slow['mark_used']}")
            check("注入 40ms 延迟后循环最长停顿仍 < 10 ms",
                  slow_worst < 10.0, f"最长停顿 {slow_worst:.2f} ms")
        finally:
            orchestrator._circadian_and_mood = original_mood
            orchestrator._mark_used = original_mark

        print("[6] lessons_for 也不再阻塞事件循环（#59）")
        # #59 的病灶：lessons_for 曾留在 _turn_context 里同步全量扫 active
        # lessons。桩打在 manager.lessons_for 上——那是 _lessons_for 与内联
        # 路径共同的真实调用点，同步/异步两条路都拦得到。
        #
        # 判据是**增量**，不是绝对阈值：`_turn_context` 收尾段本身就在循环
        # 上跑（limit_context 的测量回调反复 JSON 序列化），它的耗时随语料
        # 与机器负载波动（并发 session 跑门禁时实测到 67ms），绝对阈值会把
        # 这份固有成本误判成本单回归。增量判据直接隔离变量：同一异步路径
        # 「带 40ms 延迟」与「不带」的最长停顿差——延迟落 worker 则差接近
        # 零，落循环则差约 40ms。同步对照仍保留，证明测量器看得见阻塞。
        lessons_calls = {"n": 0}
        original_lessons = manager.lessons_for

        def slow_lessons(text):
            lessons_calls["n"] += 1
            time.sleep(0.04)
            return original_lessons(text)

        manager.lessons_for = slow_lessons
        try:
            ctrl2_elapsed, ctrl2_worst, _ = await _measure(
                lambda: _run_sync(orchestrator, query))
            print(f"       对照（同步路径）：耗时 {ctrl2_elapsed:6.2f} ms，"
                  f"循环最长停顿 {ctrl2_worst:6.2f} ms")
            check("对照路径里 lessons 延迟生效了（对照不是空转）",
                  lessons_calls["n"] >= 1, f"lessons={lessons_calls['n']}")
            check("注入延迟后同步路径确实卡住（证明测量器看得见）",
                  ctrl2_worst > 20, f"最长停顿仅 {ctrl2_worst:.2f} ms")

            base_elapsed, base_worst, _ = await _measure(
                lambda: orchestrator._turn_context_async(query, "base", []))
            lessons_calls["n"] = 0
            slow_elapsed, slow_worst, _ = await _measure(
                lambda: orchestrator._turn_context_async(query, "slow", []))
            delta = slow_worst - base_worst
            print(f"       异步路径：基线最长停顿 {base_worst:6.2f} ms；"
                  f"带 40ms 延迟 {slow_worst:6.2f} ms；差 {delta:6.2f} ms")
            check("异步路径里 lessons 卸载跑到了",
                  lessons_calls["n"] >= 1, f"lessons={lessons_calls['n']}")
            check("lessons 延迟不落循环（带/不带延迟的停顿差 < 15 ms）",
                  delta < 15.0,
                  f"差 {delta:.2f} ms（基线 {base_worst:.2f} / 带延迟 {slow_worst:.2f}）")
        finally:
            manager.lessons_for = original_lessons

        store._conn.close()

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


async def _run_sync(orchestrator, query):
    """在协程里直接同步跑，模拟改动前 stream_respond 的行为。"""
    orchestrator._turn_context(query, "p", [])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
