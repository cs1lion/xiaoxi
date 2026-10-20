"""自进化 WS 通知真的发出去了（协议 §十 self_evolution）。

为什么值得一个测试：2026-10-18 发现 `_maybe_self_evolution` 把
`SelfEvolutionCycle` 返回的事件**又翻译了一遍**。而 `run()` / `reflect()`
返回的已经是组装好的 WS 事件，`self_evolution_payload()` 只认裸 change
（顶层有 `kind`），拿一个已经带 `type` 的事件进去只会得到 None。结果是
**每一条自进化通知都被静默丢弃**：协议登记的 `self_evolution` 事件一个都没发，
前端 `noteSelfEvolution()` 从未触发，模式条上的"自进化"指示从不出现。

这条链路此没有任何测试覆盖——`test_reflect_cycle` / `test_evolution_soak`
断言的是存储状态（种子、边、reflection），不是 WS 出口。

与 test_voice_turns.py 同款隔离：只把 app.py 里的目标函数编译进一个受控命名空间，
不导入整个应用（会起单例、建目录、连数据库）。不联网、不需要 Key、确定。

    python -B scripts/test_self_evolution_send.py
"""
from __future__ import annotations

import ast
import asyncio
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os
os.environ["DB_PATH"] = ":memory:"

from server.config import settings  # noqa: E402
settings.db_path = ":memory:"

APP_PY = ROOT / "server" / "app.py"

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


class FakeConnection:
    """只记录 send() 收到的东西。"""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False
        self.last_spoke_at = 0.0

    async def send(self, payload):
        self.sent.append(payload)


class FakeCycle:
    """按脚本返回 outcome；不碰模型、不碰库。"""

    def __init__(self, outcome):
        self.outcome = outcome

    async def reflect(self, *, idle_seconds):
        return self.outcome

    async def run(self, *, idle_seconds):
        return self.outcome


def load_maybe_self_evolution():
    """把 app.py 的 `_maybe_self_evolution` 与它要用的常量编译进受控模块。

    不 import server.app：那会 ensure_dirs()、建 FastAPI 应用、连数据库。
    这里只取目标函数与 SELF_EVOLUTION_WS_EVENTS，其余依赖在函数体内懒导入，
    用假模块顶掉。

    `__package__` 必须设成 "server"：函数体里是 `from .assistant.loop import ...`
    这样的相对导入，没有包上下文会直接 ImportError。asyncio / time 也要注入，
    否则 except asyncio.CancelledError 那一行会 NameError。
    """
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))
    wanted = {"_maybe_self_evolution", "SELF_EVOLUTION_WS_EVENTS"}
    nodes = [n for n in tree.body
             if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in wanted)
             or (isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "SELF_EVOLUTION_WS_EVENTS"
                         for t in n.targets))]
    if not any(isinstance(n, ast.AsyncFunctionDef) and n.name == "_maybe_self_evolution"
               for n in nodes):
        raise SystemExit("app.py 里找不到 _maybe_self_evolution，签名变了？")
    module = types.ModuleType("server.app")
    module.__dict__["__name__"] = "server.app"
    module.__dict__["__package__"] = "server"
    module.__dict__["asyncio"] = asyncio
    module.__dict__["time"] = time
    # `SelfEvolutionCycle(provider=llm)` 要读模块级的 llm。缺它会在构造那一行
    # NameError，被函数末尾的 `except Exception: return` 静默吞掉——于是测试
    # 看到的是"什么都没发"，而不是真正的原因。必须显式给一个占位。
    module.__dict__["llm"] = object()
    sys.modules["server.app"] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP_PY), "exec"),
         module.__dict__)
    return module


def fake_evolution_module(payload_for=None):
    """顶掉 server.memory.evolution 的懒导入。"""
    module = types.ModuleType("server.memory.evolution")

    def self_evolution_cfg():
        return {"enabled": True}

    def self_evolution_payload(change):
        # 与真实实现同口径：只认裸 change，顶层 kind 必须在 CHANGE_KINDS 里。
        if not isinstance(change, dict) or change.get("kind") not in ("taste", "worldview", "spark"):
            return None
        topic, summary = change.get("topic"), change.get("summary")
        if not isinstance(topic, str) or not topic.strip():
            return None
        if not isinstance(summary, str) or not summary.strip():
            return None
        return {"type": "self_evolution", "quiet": True,
                "change": {"kind": change["kind"], "topic": topic.strip()[:40],
                           "summary": summary.strip()[:120]}}

    module.self_evolution_cfg = self_evolution_cfg
    module.self_evolution_payload = self_evolution_payload
    return module


def install_fakes(monkeypatch_src: str, outcome):
    """把 app.py 里 `_maybe_self_evolution` 会用到的懒导入换成假的。"""
    loop_module = types.ModuleType("server.assistant.loop")
    loop_module.SelfEvolutionCycle = lambda provider: FakeCycle(outcome)
    evolution = fake_evolution_module()
    saved = {}
    for name, module in (("server.assistant.loop", loop_module),
                         ("server.memory.evolution", evolution)):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = module
    return saved


def restore(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


async def run_case(outcome):
    module = load_maybe_self_evolution()
    saved = install_fakes("", outcome)
    conn = FakeConnection()
    try:
        await module._maybe_self_evolution(conn)
    finally:
        restore(saved)
    return conn.sent


def test_already_shaped_event_is_sent() -> None:
    print("[legacy cycle]")
    # run() 的产物：已经带 type 的完整 WS 事件
    outcome = {"status": "ok", "events": [
        {"type": "self_evolution", "quiet": True,
         "change": {"kind": "taste", "topic": "夜里做决定", "summary": "发现了什么"}}], "trace": {}}
    sent = asyncio.run(run_case(outcome))
    check("组装好的 self_evolution 事件原样发出", len(sent) == 1, f"sent={sent}")
    check("事件内容不被改写", sent and sent[0]["change"]["topic"] == "夜里做决定", sent)


def test_bare_change_is_translated() -> None:
    print("[bare change]")
    outcome = {"status": "ok", "events": [
        {"kind": "worldview", "topic": "耐心", "summary": "慢一点更好"}], "trace": {}}
    sent = asyncio.run(run_case(outcome))
    check("裸 change 被翻译成 self_evolution", len(sent) == 1, f"sent={sent}")
    check("翻译结果带协议形状",
          bool(sent) and sent[0]["type"] == "self_evolution"
          and sent[0]["change"]["kind"] == "worldview", sent)


def test_undocumented_event_is_not_sent() -> None:
    print("[undocumented]")
    # 未登记 type 不许自造事件发出去；curiosity 现在只进 trace，不再是 event。
    outcome = {"status": "ok", "events": [
        {"type": "internal_reflection", "quiet": True,
         "change": {"kind": "child_seed", "topic": "T", "summary": "S"}}], "trace": {}}
    sent = asyncio.run(run_case(outcome))
    check("协议外的 type 不发", sent == [], f"sent={sent}")


def test_unsupported_change_kind_is_dropped() -> None:
    print("[unsupported kind]")
    # child_seed 不在协议 §十 的 kind 取值域里，翻译必须失败而不是硬发
    outcome = {"status": "ok", "events": [
        {"kind": "child_seed", "topic": "T", "summary": "S"}], "trace": {}}
    sent = asyncio.run(run_case(outcome))
    check("取值域外的 kind 不发", sent == [], f"sent={sent}")


def test_disabled_config_sends_nothing() -> None:
    print("[disabled]")
    module = load_maybe_self_evolution()
    evolution = types.ModuleType("server.memory.evolution")
    evolution.self_evolution_cfg = lambda: {"enabled": False}
    loop_module = types.ModuleType("server.assistant.loop")
    loop_module.SelfEvolutionCycle = lambda provider: FakeCycle(
        {"status": "ok", "events": [{"type": "self_evolution", "change": {}}], "trace": {}})
    saved = {"server.assistant.loop": sys.modules.get("server.assistant.loop"),
             "server.memory.evolution": sys.modules.get("server.memory.evolution")}
    sys.modules["server.assistant.loop"] = loop_module
    sys.modules["server.memory.evolution"] = evolution
    conn = FakeConnection()
    try:
        asyncio.run(module._maybe_self_evolution(conn))
    finally:
        restore(saved)
    check("开关关着时一条都不发", conn.sent == [], f"sent={conn.sent}")


def test_empty_and_malformed_events() -> None:
    print("[robustness]")
    outcome = {"status": "ok", "events": [None, "nope", {}, {"type": "self_evolution"}],
               "trace": {}}
    sent = asyncio.run(run_case(outcome))
    check("坏形状不抛异常也不发垃圾", sent == [], f"sent={sent}")
    empty = asyncio.run(run_case({"status": "skipped", "events": [], "trace": {}}))
    check("没有事件时静默", empty == [], f"sent={empty}")


def test_closed_connection_is_not_written() -> None:
    print("[closed conn]")
    module = load_maybe_self_evolution()
    outcome = {"status": "ok", "events": [
        {"type": "self_evolution", "quiet": True, "change": {"kind": "taste", "topic": "T",
                                                            "summary": "S"}}], "trace": {}}
    saved = install_fakes("", outcome)
    conn = FakeConnection()
    conn.closed = True
    try:
        asyncio.run(module._maybe_self_evolution(conn))
    finally:
        restore(saved)
    check("连接已关闭时不发送", conn.sent == [], f"sent={conn.sent}")


def main() -> int:
    test_already_shaped_event_is_sent()
    test_bare_change_is_translated()
    test_undocumented_event_is_not_sent()
    test_unsupported_change_kind_is_dropped()
    test_disabled_config_sends_nothing()
    test_empty_and_malformed_events()
    test_closed_connection_is_not_written()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
