# -*- coding: utf-8 -*-
"""语音快环处理器不离环门禁（第十一批，2026-10-27）。

第十批把 memory REST 路由卸载出事件循环后，review 发现同一模式在语音快环
自己身上还有 5 处：_narrate_turn / _touch_reply / _proactive_check 的
mumble 分支 / _confirm_action 里共 4 处同步 store_message 写事务，加
_narrate_turn 里 1 处同步 starmap_node 读。已全部改走 asyncio.to_thread
（与 #31 检索卸载、executive_route 台账读卸载、scan_due 卸载同一模式）。

本门禁钉两条行为：

1. 卸载落位：这些处理器的 SQLite 工作必须发生在工作线程，不在循环线程
   上。判据是线程身份，不是墙钟——同步版恒在循环线程，to_thread 版恒在
   asyncio_* worker，双向无灰区，故可进 ALWAYS_REQUIRED；
2. 行为不变：落库参数逐字不变（session_id / role / text / mode），happy
   path 不出现错误收尾。

驱动方式与 test_activity_websocket.py 相同：AST 装载生产 app.py，用
__package__ + sys.modules 补丁让处理器里的懒导入命中记录线程的假模块。
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from .test_voice_turns import load_ws
except ImportError:
    from test_voice_turns import load_ws

PASSED: list[str] = []
FAILED: list[str] = []

NARRATED = "年糕是用户养的猫，名字叫年糕。"
TOUCH_LINE = "摸头杀台词：又熬夜了吧。"
MUMBLE_TEXT = "你停在那儿快两分钟了哦。"
CONFIRM_TEXT = "确认结果文本"
PACKAGE = "voice_offloop_test"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


class Recorder:
    """记录每次调用的参数与执行线程。store_message 对 narrate/touch/confirm
    走懒导入补丁，对 mumble 走模块级 env 名，两条路都记到同一份列表。"""

    def __init__(self) -> None:
        self.store_message: list[tuple[tuple, threading.Thread]] = []
        self.starmap_node: list[tuple[tuple, threading.Thread]] = []

    def record_store_message(self, *args) -> int:
        self.store_message.append((args, threading.current_thread()))
        return 1

    def record_starmap_node(self, manager, node_id) -> dict:
        self.starmap_node.append(((manager, node_id), threading.current_thread()))
        return {"id": 7, "kind": "memory", "value": "年糕是用户的猫",
                "importance": 0.8, "subject": "user", "created_at": 1000.0}


class FakeTTS:
    engine = "test"
    offline = False

    async def speak(self, text, **kwargs):
        yield SimpleNamespace(audio=b"\x00\x00" * 320, text=text)


def fake_conn(session_id: str = "s1", revision: int = 0):
    turn = SimpleNamespace(turn_id="t1", live=False)
    conn = SimpleNamespace(
        session_id=session_id, closed=False, revision=revision,
        recording=False, playing=False, turn=None, last_spoke_at=0.0,
        emotion_override=None,
        current=lambda t: True, editor_current=lambda t: True,
        begin=AsyncMock(return_value=turn),
        send=AsyncMock(), send_turn=AsyncMock(), finish=AsyncMock())
    return turn, conn


def build_env(recorder: Recorder):
    env = load_ws()
    env.update(__package__=PACKAGE, tts=FakeTTS())
    orchestrator_mod = ModuleType(f"{PACKAGE}.dialogue.orchestrator")
    orchestrator_mod.store_message = recorder.record_store_message
    orchestrator_mod.detect_emotion = lambda text: "base"
    orchestrator_mod.touch_sample_line = lambda region: TOUCH_LINE
    starmap_mod = ModuleType(f"{PACKAGE}.memory.starmap")
    starmap_mod.starmap_node = recorder.record_starmap_node

    async def narrate_opening(record, llm):
        return NARRATED

    starmap_mod.narrate_opening = narrate_opening
    env["orchestrator"] = SimpleNamespace(
        proactive_turn=AsyncMock(return_value=None),
        self_topic_turn=None,
        ambient_event=lambda idle_seconds=0.0: {
            "type": "mumble", "voice": True, "text": MUMBLE_TEXT, "event_id": "mu_gate"},
        state={"proactivity_enabled": False}, set_proactivity=lambda _: None,
        executive_route=lambda *a, **k: None)
    env["store_message"] = recorder.record_store_message
    env["assistant"] = SimpleNamespace(
        runtime=SimpleNamespace(get=lambda run_id: {
            "state": {"context": {"interaction_session_id": "s1"}}}),
        resume=AsyncMock(return_value={"text": CONFIRM_TEXT}),
        should_route=lambda _t, session_id="": False,
        waiting_input=lambda _s: None)
    patch_ctx = patch.dict(sys.modules, {
        f"{PACKAGE}.dialogue.orchestrator": orchestrator_mod,
        f"{PACKAGE}.memory.starmap": starmap_mod,
    })
    return env, patch_ctx


def assert_offloop(name: str, calls, loop_thread: threading.Thread) -> tuple:
    check(f"{name}：落库只发生一次", len(calls) == 1, f"calls={len(calls)}")
    args, thread = calls[0]
    check(f"{name}：SQLite 工作跑在工作线程（不在循环线程）",
          thread is not loop_thread, f"thread={thread.name}")
    return args


def assert_clean_finish(name: str, conn) -> None:
    """happy path 必须真的走完：既无 error 帧，也收到 done 帧。

    只断言「无 error」不够：_touch_reply 与 mumble 的 except 是静默 return，
    落库之后 _present_reply 炸掉的话 finish 根本不被调用，测试仍会全绿。
    """
    payloads = [call.args[1] for call in conn.finish.call_args_list
                if len(call.args) > 1 and isinstance(call.args[1], dict)]
    errors = [p for p in payloads if p.get("type") == "error"]
    check(f"{name}：happy path 无错误收尾", not errors, repr(payloads))
    dones = [p for p in payloads if p.get("type") == "done"]
    check(f"{name}：语音链路走完（收到 done 帧）", bool(dones), repr(payloads))


async def drive_narrate(env):
    turn, conn = fake_conn()
    await env["_narrate_turn"](conn, turn, "mem:7")
    return conn


async def drive_touch(env):
    turn, conn = fake_conn()
    await env["_touch_reply"](conn, 0, "head")
    return conn


async def drive_mumble(env):
    turn, conn = fake_conn()
    await env["_proactive_check"](conn, 0)
    return conn


async def drive_confirm(env):
    turn, conn = fake_conn()
    await env["_confirm_action"](conn, turn, {
        "run_id": "r1", "confirmation_id": "c1", "approve": True})
    return conn


CASES = [
    ("narrate", drive_narrate, ("s1", "assistant", NARRATED, "narration")),
    ("touch", drive_touch, ("s1", "assistant", TOUCH_LINE, "touch")),
    ("mumble", drive_mumble, ("s1", "assistant", MUMBLE_TEXT, "mumble")),
    ("confirm", drive_confirm, ("s1", "assistant", CONFIRM_TEXT, "assistant_action")),
]


def main() -> int:
    for name, driver, expect_args in CASES:
        print(f"[{name}：落库卸载到工作线程且参数不变]")
        recorder = Recorder()
        env, patch_ctx = build_env(recorder)
        with patch_ctx:
            loop_thread = threading.current_thread()
            conn = asyncio.run(driver(env))
        if not recorder.store_message:
            check(f"{name}：store_message 被调用", False, "未记录到落库调用")
            continue
        args = assert_offloop(name, recorder.store_message, loop_thread)
        check(f"{name}：落库参数不变", args == expect_args, f"{args!r} != {expect_args!r}")
        assert_clean_finish(name, conn)
        if recorder.starmap_node:
            node_args = assert_offloop(f"{name}/starmap_node", recorder.starmap_node, loop_thread)
            check(f"{name}：节点 id 透传", node_args[1] == "mem:7", repr(node_args))

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
