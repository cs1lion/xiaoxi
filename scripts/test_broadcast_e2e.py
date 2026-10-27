# -*- coding: utf-8 -*-
"""广播链端到端门禁（第四十四批，2026-10-27）。

审计结论：test_v2_broadcast.py 覆盖 hub 信封语义（A）、真实沙盒链路的
bridge（B）、命令校验表（C）、命令动作与 off-loop（D/E），
web/events-v2.test.mjs 覆盖客户端；但**「台账回调从 worker 线程 →
call_soon_threadsafe → _spawn_broadcast/_spawn_v2_send → conn.send →
对端 FakeSocket 收到」这条跨线程端到端链从未被驱动**——
test_voice_turns.py 的 `_make_v2_sink` 是 no-op 替身。

这条链是协议 §19（commission_update 变更通知）与 §25（v2 广播）的共同
基础设施，且它的正确性依赖一个容易静默破坏的不变量：回调来自任意线程，
必须经 call_soon_threadsafe 转回事件循环——就地 create_task 在 worker
线程里会炸或静默丢帧。本门禁用真 Connection + 真 sink，从
asyncio.to_thread 的 worker 线程触发两条链，钉住帧真的到达对端。

判据是「帧到达 + 内容正确」，不是时序；两条链各发一次，无墙钟依赖，
故可进 ALWAYS_REQUIRED。
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_PATH", tempfile.mktemp(prefix="bcast-e2e-", suffix=".db"))

from server.broadcast import V2BroadcastHub  # noqa: E402

try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws

PASSED: list[str] = []
FAILED: list[str] = []

APP_PATH = ROOT / "server" / "app.py"
BROADCAST_NAMES = ("_commission_changed", "_spawn_broadcast", "_broadcast_commission",
                   "_make_v2_sink", "_spawn_v2_send")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def load_broadcast_env() -> dict:
    """load_ws 的全量 env + 追编广播链函数（其 names 集不含它们）。"""
    env = load_ws()
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    wanted = set(BROADCAST_NAMES)
    nodes = [node for node in tree.body
             if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
             and node.name in wanted]
    for node in nodes:
        node.decorator_list = []
    if len(nodes) != len(wanted):
        raise RuntimeError(f"broadcast 函数编译不全：{len(nodes)}/{len(wanted)}")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP_PATH), "exec"), env)
    # _loop / _startup_tasks 是 app.py 模块级赋值，AST 装载不执行；不注入则
    # _commission_changed 静默丢弃、_spawn_broadcast NameError。
    env.setdefault("_loop", None)
    env.setdefault("_startup_tasks", set())
    env["v2_hub"] = V2BroadcastHub()
    return env


def attach(env: dict, loop: asyncio.AbstractEventLoop):
    socket = FakeSocket()
    conn = env["Connection"](socket)
    env["_connections"].add(conn)
    detach = env["v2_hub"].attach(conn.session_id,
                                  env["_make_v2_sink"](conn, loop))
    return socket, conn, detach


def frames_of(socket: FakeSocket, kind: str) -> list[dict]:
    return [event for event in socket.events if event.get("type") == kind]


async def main() -> int:
    env = load_broadcast_env()
    loop = asyncio.get_running_loop()
    env["_loop"] = loop
    sock_a, conn_a, detach_a = attach(env, loop)
    sock_b, conn_b, detach_b = attach(env, loop)

    print("[§19 台账回调从 worker 线程 → 两个连接都收到 commission_update]")
    commission = {"id": "c-e2e", "status": "running", "title": "端到端广播探针"}
    await asyncio.to_thread(env["_commission_changed"], commission)
    await asyncio.sleep(0.05)
    for name, sock in (("A", sock_a), ("B", sock_b)):
        frames = frames_of(sock, "commission_update")
        check(f"连接 {name} 收到 commission_update 且载荷一致",
              len(frames) == 1 and frames[0].get("commission") == commission,
              repr(sock.events[:2]))

    print("[§25 v2 hub 从 worker 线程 → 两个连接都收到信封且 seq 从 1 起]")
    await asyncio.to_thread(env["v2_hub"].publish, "TASK_STATE_CHANGE",
                            {"from": "running", "to": "auditing"}, task_id="c-e2e")
    await asyncio.sleep(0.05)
    for name, sock in (("A", sock_a), ("B", sock_b)):
        envelopes = frames_of(sock, "TASK_STATE_CHANGE")
        check(f"连接 {name} 收到 TASK_STATE_CHANGE 信封且 seq=1",
              len(envelopes) == 1 and envelopes[0].get("seq") == 1
              and envelopes[0].get("task_id") == "c-e2e",
              repr(sock.events[:3]))

    print("[closed 连接不再收帧（旁路通知不因断连而炸）]")
    conn_b.closed = True
    await asyncio.to_thread(env["_commission_changed"], dict(commission, id="c-e2e-2"))
    await asyncio.sleep(0.05)
    check("断开的连接 B 不再收到新帧",
          len(frames_of(sock_b, "commission_update")) == 1,
          repr(len(frames_of(sock_b, "commission_update"))))
    check("在用的连接 A 仍收到第二帧",
          len(frames_of(sock_a, "commission_update")) == 2,
          repr(len(frames_of(sock_a, "commission_update"))))

    detach_a()
    detach_b()
    env["_connections"].discard(conn_a)
    env["_connections"].discard(conn_b)
    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
