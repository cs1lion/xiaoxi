"""Scheduler liveness contract: a transient store error must not stop reminders."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.api import (SCHEDULER_STATE, SchedulerState,  # noqa: E402
                                  scheduler, scheduler_snapshot)

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


class FakeStore:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    def scan_due(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("database is locked")
        return []


class FakeService:
    def __init__(self, failures: int = 0) -> None:
        self.store = FakeStore(failures)


async def wait_for(predicate, timeout: float = 1.0) -> bool:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return False


async def transient_failure_then_success() -> None:
    state = SchedulerState()
    service = FakeService(failures=2)
    task = asyncio.create_task(
        scheduler(service, state, base_delay=0.01, max_delay=0.02))
    try:
        check("transient failure does not stop the loop",
              await wait_for(lambda: state.scan_count >= 3), str(state.snapshot()))
        check("success timestamp is recorded", state.last_success_at is not None)
        check("consecutive failures reset after success", state.consecutive_failures == 0)
        check("last error remains available for diagnosis",
              bool(state.last_error) and "database is locked" in (state.last_error or ""))
        check("healthy snapshot is not degraded",
              not state.snapshot()["degraded"], str(state.snapshot()))
        check("scan attempts keep increasing", state.scan_count >= 3)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    check("cancellation marks the scheduler stopped", not state.alive)


async def persistent_failure_is_visible() -> None:
    state = SchedulerState()
    service = FakeService(failures=10_000)
    task = asyncio.create_task(
        scheduler(service, state, base_delay=0.01, max_delay=0.02))
    try:
        check("persistent failure is counted",
              await wait_for(lambda: state.consecutive_failures >= 2),
              str(state.snapshot()))
        snapshot = state.snapshot()
        check("persistent failure reports degraded while alive",
              bool(snapshot["alive"]) and bool(snapshot["degraded"]), str(snapshot))
        check("failure keeps the exception type and message",
              "RuntimeError" in (state.last_error or ""), str(state.last_error))
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    check("failure loop stops only on cancellation", not state.alive)


def snapshot_contract() -> None:
    SCHEDULER_STATE.start()
    SCHEDULER_STATE.attempt()
    SCHEDULER_STATE.success()
    snapshot = scheduler_snapshot()
    SCHEDULER_STATE.stop()
    expected = {"alive", "started_at", "last_scan_at", "last_success_at",
                "last_error", "consecutive_failures", "scan_count", "degraded"}
    check("snapshot exposes the health contract", expected <= set(snapshot), str(snapshot))
    check("snapshot reports zero failures after a success",
          snapshot["consecutive_failures"] == 0 and snapshot["scan_count"] == 1)


async def scan_due_does_not_block_the_loop() -> None:
    """调度器的 scan_due 写事务不得卡住事件循环。

    判据是行为，不是「代码里有没有 to_thread」：给 scan_due 打 40ms 延迟桩，
    模拟最坏形状的写事务（持锁加慢盘），心跳协程用 sleep(0) 打点（Windows
    15.6ms 定时器精度会盖住被测对象），量调度期间循环还能不能跑。同步对照
    证明这个测量器看得见阻塞。

    抗抖动：忙机上别的进程会抢走 CPU，单次采样的「最长停顿」会把「机器忙」
    误判成「代码阻塞」（2026-11-08 高负载单轮实测 42.64 ms 判红，单独连跑
    3 次全绿；本机 20 路忙循环下旧测法实测 130.71 ms）。所以不再比「停顿多长」，
    改问「scan_due 执行的那 40 ms 里事件循环还能不能跑」：按每次 scan_due 的
    起止时刻开窗口，数窗口里心跳打点次数。同步阻塞时循环整段停摆，计数恰好为
    0；卸载到线程时，即使满负载也有几千次（本机实测 3683~4300）。调度抖动只会
    减少计数，压不到 0，也不会像取最小值那样把「偶发一次大停顿」的真阻塞洗白。
    """
    import time

    state = SchedulerState()
    service = FakeService()
    windows: list[tuple[float, float]] = []

    def slow_scan():
        started = time.perf_counter()
        time.sleep(0.04)
        windows.append((started, time.perf_counter()))
        return []

    service.store.scan_due = slow_scan

    beats: list[float] = []  # 每次心跳打点的时刻
    stop = asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            await asyncio.sleep(0)
            beats.append(time.perf_counter())

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    task = asyncio.create_task(
        scheduler(service, state, base_delay=0.01, max_delay=0.02))
    try:
        ran = await wait_for(lambda: len(windows) >= 8, timeout=4.0)
        check("slow scan_due 被调用过（测试不是空转）", ran, f"scans={len(windows)}")
        check("调度器状态健康（卸载不影响存活与降级判据）",
              state.alive and not state.snapshot()["degraded"], str(state.snapshot()))
        measured = list(windows)
    finally:
        stop.set()
        await beat
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    ticks = [sum(1 for at in beats if start < at <= end)
             for start, end in measured]
    least = min(ticks) if ticks else 0
    check("scan_due 执行期间事件循环仍在跑（每轮窗口内都有心跳）",
          least >= 1,
          f"{len(ticks)} 轮窗口心跳最少 {least} 次"
          f"（同步阻塞时为 0，高负载实测 3000+）")

    # 对照：同样的 40ms 同步 scan_due 直接跑在循环上，心跳必须被卡住。
    control_gaps: list[float] = []
    stop2 = asyncio.Event()

    async def heartbeat2():
        last = time.perf_counter()
        while not stop2.is_set():
            await asyncio.sleep(0)
            now = time.perf_counter()
            control_gaps.append((now - last) * 1000)
            last = now

    beat2 = asyncio.create_task(heartbeat2())
    await asyncio.sleep(0)
    slow_scan()
    stop2.set()
    await beat2
    check("对照：同步跑法确实卡住循环（证明测量器看得见）", max(control_gaps) > 20,
          f"最长停顿仅 {max(control_gaps):.2f} ms")


def main() -> int:
    print("scheduler health")
    asyncio.run(transient_failure_then_success())
    asyncio.run(persistent_failure_is_visible())
    asyncio.run(scan_due_does_not_block_the_loop())
    snapshot_contract()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
