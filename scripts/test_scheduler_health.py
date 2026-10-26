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
    """
    import time

    state = SchedulerState()
    service = FakeService()
    calls = {"n": 0}

    def slow_scan():
        calls["n"] += 1
        time.sleep(0.04)
        return []

    service.store.scan_due = slow_scan

    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat():
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0)
            now = time.perf_counter()
            gaps.append((now - last) * 1000)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    task = asyncio.create_task(
        scheduler(service, state, base_delay=0.01, max_delay=0.02))
    try:
        ran = await wait_for(lambda: calls["n"] >= 2, timeout=2.0)
        check("slow scan_due 被调用过（测试不是空转）", ran, f"calls={calls['n']}")
        check("调度器状态健康（卸载不影响存活与降级判据）",
              state.alive and not state.snapshot()["degraded"], str(state.snapshot()))
        worst = max(gaps) if gaps else 0.0
        check("scan_due 不再阻塞事件循环（最长停顿 < 10 ms）", worst < 10.0,
              f"最长停顿 {worst:.2f} ms")
    finally:
        stop.set()
        await beat
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # 对照：同样的 40ms 同步 scan_due 直接跑在循环上，心跳必须被卡住。
    gaps.clear()
    stop2 = asyncio.Event()

    async def heartbeat2():
        last = time.perf_counter()
        while not stop2.is_set():
            await asyncio.sleep(0)
            now = time.perf_counter()
            gaps.append((now - last) * 1000)
            last = now

    beat2 = asyncio.create_task(heartbeat2())
    await asyncio.sleep(0)
    slow_scan()
    stop2.set()
    await beat2
    check("对照：同步跑法确实卡住循环（证明测量器看得见）", max(gaps) > 20,
          f"最长停顿仅 {max(gaps):.2f} ms")


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
