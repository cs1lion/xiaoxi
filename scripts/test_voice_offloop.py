# -*- coding: utf-8 -*-
"""语音快环不离环门禁（第十一、十二批，2026-10-27）。

第十批把 memory REST 路由卸载出事件循环后，review 发现同一模式在语音快环
自己身上还有两批存量：
- 第十一批：_narrate_turn / _touch_reply / _proactive_check 的 mumble
  分支 / _confirm_action 里共 4 处同步 store_message 写事务，加
  _narrate_turn 里 1 处同步 starmap_node 读；
- 第十二批：websocket_endpoint 与 proactive_socket 两个 WS 端点里共 7 处
  同步会话调用（start_session ×2、has_session、end_session ×2、
  resume_session），其中 resume 分支的存在性校验直接在 await ws.receive()
  循环体内，卡的是整条事件循环（含其他连接）。
已全部改走 asyncio.to_thread（与 #31 检索卸载、executive_route 台账读
卸载、scan_due 卸载同一模式）。

本门禁钉两条行为：

1. 卸载落位：这些处理器与端点的 SQLite 工作必须发生在工作线程，不在循环
   线程上。判据是线程身份，不是墙钟——同步版恒在循环线程，to_thread 版
   恒在 asyncio_* worker，双向无灰区，故可进 ALWAYS_REQUIRED；
2. 行为不变：落库与会话调用参数逐字不变（session_id / role / text / mode），
   happy path 不出现错误收尾，重连后重发的 ready 帧带原 session_id。

驱动方式与 test_activity_websocket.py 相同：AST 装载生产 app.py，用
__package__ + sys.modules 补丁让处理器里的懒导入命中记录线程的假模块；
两个 WS 端点用 FakeSocket 完整驱动（ready → 合法 resume → 断开）。
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
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws

PASSED: list[str] = []
FAILED: list[str] = []

NARRATED = "年糕是用户养的猫，名字叫年糕。"
TOUCH_LINE = "摸头杀台词：又熬夜了吧。"
MUMBLE_TEXT = "你停在那儿快两分钟了哦。"
CONFIRM_TEXT = "确认结果文本"
PACKAGE = "voice_offloop_test"
RESUME_CANDIDATE = "abcdef0123456789"


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


class StoreRecorder:
    """websocket_endpoint / proactive_socket 的 store 替身。

    两个端点在事件循环里直接跑 start_session / has_session / end_session /
    resume_session（第十二批已卸载到 to_thread）。这里记录调用与执行线程。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, threading.Thread]] = []

    def _record(self, name: str, session_id) -> None:
        self.calls.append((name, session_id, threading.current_thread()))

    def start_session(self, session_id) -> None:
        self._record("start_session", session_id)

    def end_session(self, session_id) -> None:
        self._record("end_session", session_id)

    def has_session(self, session_id) -> bool:
        self._record("has_session", session_id)
        return True

    def resume_session(self, session_id) -> None:
        self._record("resume_session", session_id)


def build_ws_env(store_recorder: StoreRecorder):
    env, patch_ctx = build_env(Recorder())
    env["store"] = store_recorder
    return env, patch_ctx


async def drive_ws_endpoint(env):
    """完整走一遍主 WS 端点：连接 ready → 合法 resume → 断开。"""
    socket = FakeSocket()
    task = asyncio.create_task(env["websocket_endpoint"](socket))
    ready1 = await socket.wait("ready")
    socket.send("resume", session_id=RESUME_CANDIDATE)
    ready2 = await socket.wait("ready", after=1)
    socket.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 10)
    return ready1, ready2


async def drive_proactive_socket(env):
    socket = FakeSocket()
    task = asyncio.create_task(env["proactive_socket"](socket))
    socket.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 10)


def assert_store_offloop(name: str, store_recorder: StoreRecorder,
                         loop_thread: threading.Thread) -> None:
    on_loop = [call for call in store_recorder.calls if call[2] is loop_thread]
    check(f"{name}：会话 SQLite 全部跑在工作线程（不在循环线程）",
          bool(store_recorder.calls) and not on_loop,
          f"on_loop={[c[0] for c in on_loop]} all={[c[0] for c in store_recorder.calls]}")


def sids_of(store_recorder: StoreRecorder, name: str) -> list:
    return [sid for call_name, sid, _thread in store_recorder.calls if call_name == name]


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

    print("[websocket_endpoint：连接/重连/收尾的 SQLite 全部在工作线程]")
    store_recorder = StoreRecorder()
    env, patch_ctx = build_ws_env(store_recorder)
    with patch_ctx:
        loop_thread = threading.current_thread()
        ready1, ready2 = asyncio.run(drive_ws_endpoint(env))
    names = [name for name, _sid, _thread in store_recorder.calls]
    check("ws：start/has/resume 各一次，end 两次（重连换绑 + 收尾）",
          names.count("start_session") == 1 and names.count("has_session") == 1
          and names.count("resume_session") == 1 and names.count("end_session") == 2,
          repr(names))
    assert_store_offloop("ws", store_recorder, loop_thread)
    old_sid = ready1.get("session_id")
    check("ws：start_session 带本连接新 session_id",
          sids_of(store_recorder, "start_session") == [old_sid],
          repr(sids_of(store_recorder, "start_session")))
    check("ws：has/resume 带候选 session_id",
          sids_of(store_recorder, "has_session") == [RESUME_CANDIDATE]
          and sids_of(store_recorder, "resume_session") == [RESUME_CANDIDATE],
          f"has={sids_of(store_recorder, 'has_session')} resume={sids_of(store_recorder, 'resume_session')}")
    check("ws：end 先带旧 id（换绑前）后带新 id（收尾）",
          sids_of(store_recorder, "end_session") == [old_sid, RESUME_CANDIDATE],
          repr(sids_of(store_recorder, "end_session")))
    check("ws：首帧 ready 正常下发", bool(old_sid), repr(ready1))
    check("ws：重连后重发的 ready 带原 session_id",
          ready2.get("session_id") == RESUME_CANDIDATE, repr(ready2))

    print("[proactive_socket：连接与收尾的 SQLite 在工作线程]")
    store_recorder2 = StoreRecorder()
    env2, patch_ctx2 = build_ws_env(store_recorder2)
    with patch_ctx2:
        loop_thread2 = threading.current_thread()
        asyncio.run(drive_proactive_socket(env2))
    names2 = [name for name, _sid, _thread in store_recorder2.calls]
    check("proactive：start/end 各一次", names2 == ["start_session", "end_session"],
          repr(names2))
    assert_store_offloop("proactive", store_recorder2, loop_thread2)
    proactive_sids = [sid for _name, sid, _thread in store_recorder2.calls]
    check("proactive：start/end 带同一非空 session_id",
          len(proactive_sids) == 2 and proactive_sids[0] == proactive_sids[1]
          and bool(proactive_sids[0]), repr(proactive_sids))

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
