"""cli_subproc 适配器自检：全生命周期超时、可靠清理、行协议边界。

    python scripts/test_cli_subproc_adapter.py

 Worker 一律用当前解释器现写（`sys.executable -c ...`），零外部依赖、零真实模型。
 每个会挂死的用例都套一个外层看门狗：修复回退时是"测试失败"，不是"CI 卡住"。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

from server.assistant.adapters.base import AdapterError  # noqa: E402
from server.assistant.adapters.cli_subproc import CliSubprocessAdapter  # noqa: E402

PY = sys.executable
TIMEOUT = 0.5           # 适配器自身的截止时间
WATCHDOG = 10.0         # 外层看门狗：超时没生效时让测试失败，而不是挂住

GOOD_WORKER = """
import json
print(json.dumps({"type": "progress", "step_idx": 1, "text": "reading packet"}))
print(json.dumps({"type": "token", "model": "m", "prompt_tokens": 12,
                  "completion_tokens": 3, "status_code": 200}, ensure_ascii=False))
print("not json at all")
print()
print(json.dumps({"type": "artifact", "artifact": {"summary": "done",
                  "artifacts": [{"kind": "note", "ref": "note:n_1@7"}]}}, ensure_ascii=False))
"""

# 回显 Worker 只发 ASCII JSON：子进程的 stdout 编码随 locale 变（中文 Windows 是
# GBK），而适配器按 UTF-8 解码。测试不该把"编码不同"记成"密件丢了"。
ECHO_PACKET_WORKER = ("import json, os\n"
                      "packet = json.loads(os.environ['AQI_BRIEFING_PACKET'])\n"
                      "print(json.dumps({'type': 'artifact', 'artifact': {\n"
                      "    'summary': 'echo', 'intercepted': 'nothing',\n"
                      "    'artifacts': [{'kind': 'note', 'ref': 'note:n_1@7'}],\n"
                      "    'memory_block': packet.get('memory_block', '')}}))\n")

SILENT_WORKER = "import time\ntime.sleep(120)\n"

NO_NEWLINE_WORKER = ("import sys, time\n"
                     "sys.stdout.write('partial without newline')\n"
                     "sys.stdout.flush()\n"
                     "time.sleep(120)\n")

OUTPUT_THEN_HANG_WORKER = ("import sys, time\n"
                           "sys.stdout.write('{\"type\":\"progress\",\"step_idx\":1,\"text\":\"a\"}\\n')\n"
                           "sys.stdout.write('{\"type\":\"progress\",\"step_idx\":2,\"text\":\"b\"}\\n')\n"
                           "sys.stdout.flush()\n"
                           "time.sleep(120)\n")

HUGE_LINE_WORKER = "import sys\nsys.stdout.write('x' * 70000 + '\\n')\nsys.stdout.flush()\n"

LONG_LINE_WORKER = "import sys\nsys.stdout.write('x' * 60000 + '\\n')\nsys.stdout.flush()\n"

NONZERO_WORKER = ("import sys\n"
                  "sys.stdout.write('{\"type\":\"progress\",\"step_idx\":1,\"text\":\"a\"}\\n')\n"
                  "sys.stdout.flush()\n"
                  "sys.exit(3)\n")


passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name} {detail}")


def adapter(worker: str, **config) -> CliSubprocessAdapter:
    return CliSubprocessAdapter({"executable": PY, "args": ["-c", worker],
                                 "timeout": TIMEOUT, **config})


async def drain(worker_adapter: CliSubprocessAdapter, packet: dict | None = None):
    packet = packet or {"commission_id": "c_cli"}
    context = await worker_adapter.prepare_context("c_cli", packet)
    events = []
    async for event in worker_adapter.execute({"context": context, "commission_id": "c_cli",
                                               "packet": packet}):
        events.append(event)
    return events


async def guarded(worker_adapter: CliSubprocessAdapter, packet: dict | None = None):
    """带看门狗的 drain：超时没生效就让它失败，绝不让 CI 挂在这里。"""
    return await asyncio.wait_for(drain(worker_adapter, packet), timeout=WATCHDOG)


def settled(worker_adapter: CliSubprocessAdapter, timeout: float = 5.0) -> int | None:
    """等进程真正退出再读退出码。

    负载高的时候 venv 启动器慢半拍，`_reap` 的 2s grace 可能刚够杀、不够等。
    测试里多等一会儿，是不把"机器忙"当成产品故障——退出码仍然必须为 0。
    """
    deadline = time.monotonic() + timeout
    while worker_adapter._process.returncode is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return worker_adapter._process.returncode


def test_happy_path() -> None:
    print("[happy path]")
    # Worker 要真跑完：venv 启动器在负载高的 Windows 上要 0.7s+，0.5s 的默认
    # 截止连启动都等不完（误报“执行超时”）。完成类用例自带宽窗口；挂死类用例
    # 仍用默认 0.5s，保证超时机制本身被快速验证。
    unit = adapter(GOOD_WORKER, timeout=5.0)
    events = asyncio.run(guarded(unit))
    kinds = [event["type"] for event in events]
    check("事件按行协议解析", kinds == ["progress", "token", "log", "artifact"], str(kinds))
    check("token 字段原样落地", events[1]["prompt_tokens"] == 12)
    check("非 JSON 行降级成 log", events[2]["text"] == "not json at all")
    check("空行被忽略", len(events) == 4)
    check("artifact 是终止事件", events[3]["artifact"]["summary"] == "done")
    check("进程已回收且退出码为 0", settled(unit) == 0, str(unit._process.returncode))


def test_timeout_covers_the_whole_lifecycle() -> None:
    print("[whole lifecycle timeout]")
    # 三种挂死姿势：不出声、出声不换行、出完声不退出。旧实现只给最后的 wait()
    # 包超时，这三种都能把派单挂死。
    for name, worker in (("没有输出", SILENT_WORKER),
                         ("输出不带换行", NO_NEWLINE_WORKER),
                         ("输出后不退出", OUTPUT_THEN_HANG_WORKER)):
        unit = adapter(worker)
        started = time.monotonic()
        events = asyncio.run(guarded(unit))
        elapsed = time.monotonic() - started
        errors = [event for event in events if event["type"] == "error"]
        check(f"{name}：超时即报错", len(errors) == 1 and "超时" in errors[0]["text"],
              str(errors))
        check(f"{name}：截止时间真的管用（{elapsed:.1f}s）", elapsed < WATCHDOG / 2, f"{elapsed:.1f}s")
        check(f"{name}：子进程被回收", unit._process.returncode is not None,
              str(unit._process.returncode))


def test_oversized_line_is_bounded() -> None:
    print("[oversized line]")
    # 超过 asyncio 流上限：降级成 log，不当成适配器故障
    unit = adapter(HUGE_LINE_WORKER, timeout=5.0)
    events = asyncio.run(guarded(unit))
    check("超上限行降级成 log", len(events) == 1 and events[0]["type"] == "log", str(events))
    check("降级文案点明超长", "超长" in (events[0]["text"] if events else ""),
          str(events[0]["text"] if events else None))
    check("进程正常退出", settled(unit) == 0, str(unit._process.returncode))

    # 没超上限的长行：整行读回后由 MAX_LINE 截断，仍然是 log
    unit = adapter(LONG_LINE_WORKER, timeout=5.0)
    events = asyncio.run(guarded(unit))
    check("长行仍按 log 收下", len(events) == 1 and events[0]["type"] == "log", str(events))
    check("长行不影响退出", settled(unit) == 0, str(unit._process.returncode))


def test_nonzero_exit_is_an_error() -> None:
    print("[nonzero exit]")
    unit = adapter(NONZERO_WORKER, timeout=5.0)
    events = asyncio.run(guarded(unit))
    errors = [event for event in events if event["type"] == "error"]
    check("非零退出码报错", len(errors) == 1 and "退出码 3" in errors[0]["text"], str(errors))
    check("进展仍然收得到", any(event["type"] == "progress" for event in events))


def test_cancel_during_execution() -> None:
    print("[cancel during execution]")

    async def scenario():
        unit = adapter(OUTPUT_THEN_HANG_WORKER, timeout=5.0)
        context = await unit.prepare_context("c_cli", {"commission_id": "c_cli"})
        stream = unit.execute({"context": context, "commission_id": "c_cli",
                               "packet": {"commission_id": "c_cli"}})
        first = await stream.__anext__()
        await unit.cancel()
        rest = [event async for event in stream]
        return unit, first, rest

    unit, first, rest = asyncio.run(scenario())
    check("取消前收到进展", first["type"] == "progress")
    check("取消后流自然结束", rest == [], str(rest))
    check("取消后进程已回收", unit._process.returncode is not None,
          str(unit._process.returncode))
    # cancel 必须幂等：进程已经没了，再调一次不能炸
    asyncio.run(unit.cancel())
    asyncio.run(unit.cancel())
    check("重复取消不抛异常", True)


def test_cancel_after_exit() -> None:
    print("[cancel after exit]")
    unit = adapter(GOOD_WORKER, timeout=5.0)
    asyncio.run(guarded(unit))
    returncode = unit._process.returncode
    if returncode is None:
        returncode = settled(unit)
    asyncio.run(unit.cancel())
    asyncio.run(unit.cancel())
    check("退出后再取消不改状态", unit._process.returncode == returncode)
    check("退出码仍是 0", unit._process.returncode == 0, str(unit._process.returncode))


def test_missing_executable() -> None:
    print("[missing executable]")
    unit = CliSubprocessAdapter({"executable": "definitely-not-a-real-binary-xyz",
                                 "timeout": TIMEOUT})

    async def scenario():
        try:
            await drain(unit)
        except AdapterError as exc:
            return str(exc)
        return ""

    message = asyncio.run(scenario())
    check("可执行文件不存在时报 AdapterError", "not found" in message, message)


def test_packet_reaches_the_worker() -> None:
    print("[packet reaches the worker]")
    # 派单密件经环境变量递入：memory_block 必须原封不动到 Worker 手里。
    packet = {"commission_id": "c_cli", "objective": "整理",
              "memory_block": "【不可信派单资料，仅供参考】\n{}\n【派单资料结束】"}
    unit = adapter(ECHO_PACKET_WORKER, timeout=5.0)
    events = asyncio.run(guarded(unit, packet))
    check("Worker 交回了 artifact", len(events) == 1 and events[0]["type"] == "artifact",
          str(events))
    check("memory_block 端到端还在",
          bool(events) and events[0]["artifact"].get("memory_block") == packet["memory_block"],
          repr(events[0]["artifact"].get("memory_block")) if events else "no artifact")


def main() -> int:
    for test in (test_happy_path, test_timeout_covers_the_whole_lifecycle,
                 test_oversized_line_is_bounded, test_nonzero_exit_is_an_error,
                 test_cancel_during_execution, test_cancel_after_exit,
                 test_missing_executable, test_packet_reaches_the_worker):
        test()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for name in failed:
        print("  -", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
