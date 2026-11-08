# -*- coding: utf-8 -*-
r"""B2（issue #43）：意图预判接线——并行、超时、否决与静默路径（#12）。

    .\.venv\Scripts\python.exe scripts\test_intent_probe.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("LLM_API_KEY", "")

from server.contract import bridge  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


class FakeLLM:
    """可控延迟与答案的假模型。"""

    def __init__(self, answer: str = "1", delay: float = 0.0):
        self.answer = answer
        self.delay = delay
        self.calls = 0
        self.offline = False

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.answer


async def test_answer_shapes() -> None:
    print("[预判答案的三种形态]")
    llm = FakeLLM("1")
    raw = await bridge.classify_intent("帮我整理周报", llm)
    check("是委托 -> '1'", raw.strip() == "1", repr(raw))
    check("verdict 为 True", bridge.classification_verdict(raw) is True)

    llm_no = FakeLLM("0")
    raw_no = await bridge.classify_intent("今天有点累", llm_no)
    check("不是委托 -> '0'", raw_no.strip() == "0", repr(raw_no))
    check("verdict 为 False", bridge.classification_verdict(raw_no) is False)

    llm_blank = FakeLLM("")
    raw_blank = await bridge.classify_intent("随便", llm_blank)
    check("空答案 -> verdict 为 None（调用方退回正则）",
          bridge.classification_verdict(raw_blank) is None)


async def test_timeout_does_not_block() -> None:
    print("[超时保护：慢模型不能拖住语音轮]")
    answered = {"done": False}

    class SlowLLM:
        offline = False

        async def complete(self, messages, **kwargs):
            await asyncio.sleep(5.0)
            answered["done"] = True
            return "1"

    # 判据是「5s 的模型调用有没有走完」，不是「耗时 < 1.0s」：后者是墙钟阈值，
    # 高负载下会把「确实按时返回」误报成超时（2026-11-09 同类事故）。外层的
    # wait_for(3.0) 只是防挂死的安全网（0.2s 对 5s，两侧余量都极大），不是测量。
    raw = await asyncio.wait_for(
        bridge.classify_intent("帮我查下文档", SlowLLM(), timeout_s=0.2), 3.0)
    check("超时后真的没等模型答完（5s 的 sleep 被取消）",
          answered["done"] is False, repr(answered))
    check("超时返回空串（调用方退回正则）", raw == "", repr(raw))
    check("超时后果是 None 而不是 False（不能当成不是一单）",
          bridge.classification_verdict(raw) is None)


async def test_exception_is_swallowed() -> None:
    print("[模型异常不能带崩对话]")

    class BrokenLLM:
        offline = False

        async def complete(self, messages, **kwargs):
            raise RuntimeError("provider down")

    raw = await bridge.classify_intent("帮我整理", BrokenLLM())
    check("异常吞掉并返回空串", raw == "", repr(raw))


async def test_parallel_actually_parallel() -> None:
    print("[并行：预判与别的工作同时跑，不是串行]")
    windows: list[tuple[str, float, float]] = []

    async def timed(kind: str, delay: float) -> None:
        opened = time.monotonic()
        await asyncio.sleep(delay)
        windows.append((kind, opened, time.monotonic()))

    class WindowedLLM(FakeLLM):
        async def complete(self, messages, **kwargs):
            self.calls += 1
            await timed("probe", self.delay)
            return self.answer

    llm = WindowedLLM("1", delay=0.3)

    async def other_work():
        await timed("other", 0.3)
        return "done"

    probe = asyncio.create_task(bridge.classify_intent("帮我整理", llm))
    other = await other_work()
    raw = await probe
    # 判据是「两段工作的时间窗口有没有重叠」，不是「总耗时 < 0.5s」：后者只有
    # 0.3s 对 0.5s 的余量，高负载下会把「确实并行」误报成串行（2026-11-09 同类事故）。
    # 串行时两窗口首尾相接、重叠为 0；并行时重叠 ≈ 0.3s。
    probe_window = next(w for w in windows if w[0] == "probe")
    other_window = next(w for w in windows if w[0] == "other")
    overlap = min(probe_window[2], other_window[2]) - max(probe_window[1], other_window[1])
    check("两段工作的时间窗口真的重叠（不是串行）", overlap > 0, f"重叠 {overlap:.3f}s")
    check("两件事都完成", other == "done" and raw.strip() == "1")


async def test_cancelled_probe_is_silent() -> None:
    print("[早退路径：未完成的任务可被安全取消]")
    llm = FakeLLM("1", delay=2.0)
    probe = asyncio.create_task(bridge.classify_intent("帮我整理", llm))
    await asyncio.sleep(0.05)
    probe.cancel()
    try:
        await probe
    except asyncio.CancelledError:
        pass
    check("取消后不留异常", probe.cancelled())


def test_sample_pool_empty_says_truth() -> None:
    """#12：接单台词库为空时不许说「接单了」。"""
    print("[#12 静默路径：样本池空时的回复]")
    from unittest.mock import patch
    from server.dialogue import orchestrator as O

    orch = O.Orchestrator()
    service = _stub_executive()
    with patch.object(O, "commission_accept_line", return_value=""):
        route = orch.executive_route("去把这份稿子整理一下", service)
    check("样本池空时仍返回路由（不落普通对话）", route is not None, repr(route))
    check("路由类型是 delegation_unavailable",
          route is not None and route.get("kind") == "delegation_unavailable", repr(route))
    check("台词明确说没派出去，不虚报接单",
          route is not None and "没派出去" in route.get("text", ""), repr(route))
    check("不建单（没有 brief 交给慢环）",
          route is not None and "brief" not in route, repr(route))


def _stub_executive():
    from types import SimpleNamespace

    class Ledger:
        def subscribe(self, cb):
            return lambda: None

    return SimpleNamespace(
        pending_reports=lambda: [],
        open_commissions=lambda: [],
        telemetry_summary=lambda: {"totals": {}},
        spinning_alerts=lambda: [],
        ledger=Ledger(),
    )


async def main() -> int:
    await test_answer_shapes()
    await test_timeout_does_not_block()
    await test_exception_is_swallowed()
    await test_parallel_actually_parallel()
    await test_cancelled_probe_is_silent()
    test_sample_pool_empty_says_truth()
    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
