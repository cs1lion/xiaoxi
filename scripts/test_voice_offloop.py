# -*- coding: utf-8 -*-
"""语音快环与 REST 路由不离环门禁（第十一至十七批，2026-10-27）。

第十批把 memory REST 路由（server/memory/api.py）卸载出事件循环后，review
发现同一模式在 app.py 自己身上还有多批存量：
- 第十一批：_narrate_turn / _touch_reply / _proactive_check 的 mumble
  分支 / _confirm_action 里共 4 处同步 store_message 写事务，加
  _narrate_turn 里 1 处同步 starmap_node 读；
- 第十二批：websocket_endpoint 与 proactive_socket 两个 WS 端点里共 7 处
  同步会话调用（start_session ×2、has_session、end_session ×2、
  resume_session），其中 resume 分支的存在性校验直接在 await ws.receive()
  循环体内，卡的是整条事件循环（含其他连接）；
- 第十五批：app.py 自有的 memory REST 路由实测（千级记忆库）后卸走 9 条：
  8 条 15-76ms 的重路由（list_memories / memories_context /
  correct_memory 的 correct 分支 / delete_memory / forget_key /
  reset_memory / add_memory / health 的 count），加 review 指出同为无界
  全表扫描的 history_of（memories 表没有 key 单列索引，EXPLAIN 实测
  SCAN memories + USE TEMP B-TREE FOR ORDER BY）；
- 第十六批：判据从「读耗时 vs 调度开销」换成「写提交 vs 读」——store 的
  写事务（BEGIN IMMEDIATE + commit，synchronous=FULL 每次 fsync）实测
  中位数几毫秒、最坏约 11ms（executive.accept 9.42ms / spend_spark
  median 9.05ms / set_disabled 逐行 median 3.97ms max 10.93ms），读
  （无脏页）亚毫秒（pending_reports 0.22ms / recent_messages 0.31ms）。
  按此卸掉 _aqi_action 的 executive.accept/reassign、_executive_finish 的
  accept/reject、_proactive_check 自发话题分支的 spend_spark，并**纠正
  第十五批的误判**：set_disabled 当时量的 0.50ms 是「反复拨同一行」的
  缓存快速路径假象（页与 WAL 尾部皆热、只脏同一页），逐行翻一次的干净
  分布是 median 3.97ms，故本批补卸。

保留同步的只剩有界索引读：_confirm_action 的 assistant.runtime.get（单表
主键 + json.loads）、_send_proactive_blocks 的 executive.pending_reports
（0.22ms）、transcript 的 store.recent_messages（0.31ms，命中
idx_msg_session）。

残余限界（勿误读「保留同步 = 绝不卡循环」）：store 是单连接 + 单 RLock，
所有读写串行；保留同步的轻读若撞上已卸载的写正持锁，会阻塞在 RLock 上
连带卡住循环，阻塞时长约等于那个写的时长。CommissionStore 的
_transaction 是 BEGIN IMMEDIATE（读也拿写锁），pending_reports 同样暴露
在这个争用面下。相较改前（全同步、重活 15-76ms 卡循环）仍是严格改进，
残余被写时长限界。

已全部改走 asyncio.to_thread（与 #31 检索卸载、executive_route 台账读
卸载、scan_due 卸载同一模式）。

本门禁钉两条行为：

1. 卸载落位：这些处理器、端点与路由的 SQLite 工作必须发生在工作线程，
   不在循环线程上。判据是线程身份，不是墙钟——同步版恒在循环线程，
   to_thread 版恒在 asyncio_* worker，双向无灰区，故可进 ALWAYS_REQUIRED；
2. 行为不变：落库与会话调用参数逐字不变（session_id / role / text / mode），
   happy path 不出现错误收尾，重连后重发的 ready 帧带原 session_id。

第十七批追加第三条：REST 路由的 limit 夹取——客户端 limit 无界直通
`LIMIT ?` 时，SQLite 的 `LIMIT -1` 是无限制（一个 `?limit=-1` 拉走全表）。
list_memories / memories_context / transcript 三条路由在路由层夹成
max(1, min(limit, 帽))，门禁断言 -1 与 0 夹成 1、超大值夹到各路由帽。

驱动方式与 test_activity_websocket.py 相同：AST 装载生产 app.py，用
__package__ + sys.modules 补丁让处理器里的懒导入命中记录线程的假模块；
两个 WS 端点用 FakeSocket 完整驱动（ready → 合法 resume → 断开），REST
路由用记录线程的 manager/store 替身直接调用。
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
    turn = SimpleNamespace(turn_id="t1", live=False, editor_insight=None)
    spawned: list = []

    def spawn(coro):
        task = asyncio.create_task(coro)
        spawned.append(task)
        return task

    conn = SimpleNamespace(
        session_id=session_id, closed=False, revision=revision,
        recording=False, playing=False, turn=None, last_spoke_at=0.0,
        emotion_override=None, spawn=spawn,
        current=lambda t: True, editor_current=lambda t: True,
        begin=AsyncMock(return_value=turn),
        send=AsyncMock(), send_turn=AsyncMock(), finish=AsyncMock())
    conn.spawned = spawned
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


async def drive_limit_clamps(env):
    """用恶意 limit 驱动三条路由：-1/0 应被夹成 1（SQLite 的 LIMIT -1 是
    无限制），超大值应被夹到路由帽。"""
    await env["list_memories"](limit=-1)
    await env["list_memories"](limit=99999)
    await env["memories_context"](query="测试", limit=99999)
    await env["transcript"](session_id="s1", limit=-1)
    await env["transcript"](session_id="s1", limit=0)
    await env["transcript"](session_id="s1", limit=99999)


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


async def drive_executive_writes(env, executive):
    """驱动 _executive_finish 的两个分支与 _aqi_action 的两个分支。"""
    await env["_executive_finish"]("c1", "accept")
    await env["_executive_finish"]("c1", "reject", "这段得改")
    turn, conn = fake_conn()
    await env["_aqi_action"](conn, "dispatch:accept:c1")
    await env["_aqi_action"](conn, "dispatch:reassign:c1")
    for task in list(getattr(conn, "spawned", [])):
        await asyncio.wait_for(task, 10)
    return conn


async def drive_proactive_selftopic(env):
    """驱动 _proactive_check 的自发话题分支（proactive_turn 静默 →
    self_topic_turn 产出带 spark_id 的结果 → _run_turn 的 proactive 分支 →
    spend_spark）。"""
    turn, conn = fake_conn()
    await env["_proactive_check"](conn, 0)
    return conn


async def drive_usage_sweep(env):
    """驱动 _maybe_self_evolution：第一站就是 usage sweep（第十九批已卸载到
    to_thread），其后的 SelfEvolutionCycle 懒导入在隔离环境必然失败、被自身
    except 吞掉——不影响对 sweep 的断言。"""
    turn, conn = fake_conn()
    await env["_maybe_self_evolution"](conn)
    return conn


async def drive_rest_routes(env):
    """驱动 10 条重路由：顺序调用，记录每次 SQLite 工作的执行线程。"""
    await env["list_memories"]()
    await env["memories_context"](query="测试")
    await env["correct_memory"](7, {"value": "改正后的内容"})
    await env["delete_memory"](7)
    await env["forget_key"]({"key": "某键"})
    await env["reset_memory"]({})
    await env["add_memory"]({"value": "手动记一条"})
    await env["health"]()
    await env["memory_history"](key="某键")
    await env["correct_memory"](8, {"disabled": True})


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

    print("[REST 路由：10 条路由的 SQLite 在工作线程]")
    rest_calls: list[tuple[str, threading.Thread]] = []

    class _RestManager:
        """只实现路由会碰的方法；其余属性走 __getattr__ 到真 env manager。"""

        enabled = True

        def snapshot(self, **kw):
            rest_calls.append(("snapshot", threading.current_thread()))
            return []

        def context_with_status(self, query, subject=None):
            rest_calls.append(("context_with_status", threading.current_thread()))
            return "", False

        def correct(self, memory_id, value):
            rest_calls.append(("correct", threading.current_thread()))
            return SimpleNamespace(to_dict=lambda: {"id": memory_id})

        def forget(self, memory_id, purge_history=True):
            rest_calls.append(("forget", threading.current_thread()))
            return 1

        def forget_key(self, key):
            rest_calls.append(("forget_key", threading.current_thread()))
            return 1

        def forget_all(self, **filters):
            rest_calls.append(("forget_all", threading.current_thread()))
            return 1

        def history_of(self, key):
            rest_calls.append(("history_of", threading.current_thread()))
            return []

        def set_disabled(self, memory_id, disabled):
            rest_calls.append(("set_disabled", threading.current_thread()))
            return SimpleNamespace(to_dict=lambda: {"id": memory_id})

        def remember(self, **item):
            rest_calls.append(("remember", threading.current_thread()))
            return SimpleNamespace(to_dict=lambda: {"id": 1})

    class _RestStore:
        def count(self):
            rest_calls.append(("count", threading.current_thread()))
            return 3

    class _Executive:
        """app.py 后台处理器（_aqi_action / _executive_finish / _proactive_check
        的 _send_proactive_blocks）的 executive 替身：记录台账写、参数与线程。"""

        def __init__(self):
            self.calls: list[tuple[str, tuple, threading.Thread]] = []

        def _record(self, name, *args):
            self.calls.append((name, args, threading.current_thread()))
            return {"commission": {"id": "c1"}}

        def accept(self, commission_id):
            return self._record("accept", commission_id)

        def reassign(self, commission_id):
            return self._record("reassign", commission_id)

        def reject(self, commission_id, feedback):
            return self._record("reject", commission_id, feedback)

        def spinning_alerts(self):
            return []

        def pending_reports(self):
            return []

        async def run_commission(self, commission_id):
            return {"commission": {"id": commission_id}}

    class _SparkStore:
        """spend_spark 的 store 替身：update_spark_state + get_spark 记录参数与线程。"""

        def __init__(self):
            self.calls: list[tuple[str, tuple, threading.Thread]] = []

        def update_spark_state(self, spark_id, state, **kw):
            self.calls.append(("update_spark_state", (spark_id, state),
                               threading.current_thread()))
            return True

        def get_spark(self, spark_id):
            self.calls.append(("get_spark", (spark_id,), threading.current_thread()))
            return {"id": spark_id, "text": "一个念头", "state": "spent"}

    env3, patch_ctx3 = build_ws_env(Recorder())
    env3["manager"] = _RestManager()
    env3["store"] = _RestStore()
    env3["executive"] = _Executive()
    # health 读 llm/settings 的属性面与 BUILD：隔离 env 的 llm 只有 aclose，
    # settings 只有语音字段，补上 health 真正读的那些。
    env3["llm"] = SimpleNamespace(provider="test", model="test-model",
                                  offline=True, aclose=None)
    env3["settings"] = SimpleNamespace(
        audio_sample_rate=16000, tts_default_emotion="base",
        llm_extract_model=None, tts_engine="test", tts_model="test",
        tts_voice="test", asr_engine="test", asr_model_id="test",
        asr_model="test")
    with patch_ctx3:
        loop_thread3 = threading.current_thread()
        asyncio.run(drive_rest_routes(env3))
    rest_names = [name for name, _thread in rest_calls]
    # list_memories 的 snapshot/count 走 asyncio.gather 并发派发，两个替身的
    # append 先后无保证（多 worker + GIL），故用多重集合比对而非精确序列；
    # 其余路由是顺序 await，次序确定。
    expected_rest = ["snapshot", "count", "context_with_status", "snapshot",
                     "correct", "forget", "forget_key", "forget_all",
                     "remember", "count", "history_of", "set_disabled"]
    check("REST：10 条路由的调用一次不少一次不多",
          sorted(rest_names) == sorted(expected_rest),
          f"got={rest_names!r} want={expected_rest!r}")
    check("REST：全部 SQLite 调用跑在工作线程（不在循环线程）",
          bool(rest_calls) and all(t is not loop_thread3 for _n, t in rest_calls),
          repr([(n, t.name) for n, t in rest_calls]))

    print("[executive 后台写：_executive_finish 与 _aqi_action 的台账写在工作线程]")
    executive = _Executive()
    env4, patch_ctx4 = build_ws_env(Recorder())
    env4["executive"] = executive
    env4["spinning_intercept_line"] = lambda: "换将成功。"
    with patch_ctx4:
        loop_thread4 = threading.current_thread()
        conn4 = asyncio.run(drive_executive_writes(env4, executive))
    exec_records = [(name, args) for name, args, _thread in executive.calls]
    check("executive：accept×2 / reassign / reject 都被调用且带对单号",
          sorted(exec_records) == [("accept", ("c1",)), ("accept", ("c1",)),
                                   ("reassign", ("c1",)), ("reject", ("c1", "这段得改"))],
          repr(exec_records))
    check("executive：全部台账写跑在工作线程（不在循环线程）",
          bool(executive.calls) and all(t is not loop_thread4 for _n, _a, t in executive.calls),
          repr([(n, t.name) for n, _a, t in executive.calls]))
    assert_clean_finish("executive", conn4)

    print("[spend_spark：_proactive_check 自发话题分支的火花写在工作线程]")
    spark_store = _SparkStore()
    env5, patch_ctx5 = build_ws_env(Recorder())
    env5["store"] = spark_store
    env5["executive"] = _Executive()

    async def self_topic(**kwargs):
        return SimpleNamespace(text="想起那盆多肉", spark_id=42)

    async def silent_proactive(**kwargs):
        return None

    async def stream_respond(**kwargs):
        yield {"type": "thinking"}
        await kwargs["on_memories"]([])
        yield {"type": "done", "text": "想起那盆多肉", "emotion": "base",
               "latency_ms": 0}

    env5["orchestrator"] = SimpleNamespace(
        proactive_turn=silent_proactive, self_topic_turn=self_topic,
        stream_respond=stream_respond, state={"proactivity_enabled": False},
        set_proactivity=lambda _: None, executive_route=lambda *a, **k: None)
    env5["assistant"] = SimpleNamespace(
        should_route=lambda _t, session_id="": False,
        waiting_input=lambda _s: None,
        start=AsyncMock(return_value={"status": "not_applicable", "text": ""}))
    # _proactive_check 里 `from .memory.diary import spend_spark` 是相对导入，
    # 隔离环境解析成 voice_offloop_test.memory.diary；不补丁会 ImportError 被
    # 外层 except 吞掉、整条分支静默返回。补上真模块，让真 spend_spark 配
    # _SparkStore 走完整链路。
    import server.memory.diary as real_diary
    diary_patch = patch.dict(sys.modules, {f"{PACKAGE}.memory.diary": real_diary})
    with patch_ctx5, diary_patch:
        loop_thread5 = threading.current_thread()
        conn5 = asyncio.run(drive_proactive_selftopic(env5))
    spark_records = [(name, args) for name, args, _thread in spark_store.calls]
    check("spend_spark：update_spark_state 与 get_spark 都被调用且火花号/状态正确",
          sorted(spark_records) == [("get_spark", (42,)),
                                    ("update_spark_state", (42, "spent"))],
          repr(spark_records))
    check("spend_spark：火花写跑在工作线程（不在循环线程）",
          bool(spark_store.calls) and all(t is not loop_thread5 for _n, _a, t in spark_store.calls),
          repr([(n, t.name) for n, _a, t in spark_store.calls]))
    assert_clean_finish("spend_spark", conn5)

    print("[limit 夹取：恶意 limit 到不了 SQLite]")
    limits_seen: list[tuple[str, object]] = []

    class _LimitManager:
        enabled = True

        def snapshot(self, **kw):
            limits_seen.append(("snapshot", kw.get("limit")))
            return []

        def context_with_status(self, query, subject=None):
            limits_seen.append(("context_with_status", None))
            return "", False

    class _LimitStore:
        def count(self):
            return 3

        def recent_messages(self, session_id, limit=100):
            limits_seen.append(("recent_messages", limit))
            return []

    env6, patch_ctx6 = build_ws_env(Recorder())
    env6["manager"] = _LimitManager()
    env6["store"] = _LimitStore()
    with patch_ctx6:
        asyncio.run(drive_limit_clamps(env6))
    snap_limits = [value for name, value in limits_seen if name == "snapshot"]
    msg_limits = [value for name, value in limits_seen if name == "recent_messages"]
    # list_memories(-1 → 1)、list_memories(99999 → 500 帽)、
    # memories_context(99999 → 50 帽)；transcript(-1 → 1、0 → 1)。
    check("limit：list_memories 的 -1 夹成 1、99999 夹成 500 帽",
          snap_limits[:2] == [1, 500], repr(snap_limits))
    check("limit：memories_context 的 99999 夹成 50 帽",
          len(snap_limits) > 2 and snap_limits[2] == 50, repr(snap_limits))
    check("limit：transcript 的 -1 与 0 都夹成 1、99999 夹成 200 帽",
          msg_limits == [1, 1, 200], repr(msg_limits))

    print("[usage sweep：_maybe_self_evolution 的批量写在工作线程]")
    sweep_calls: list[tuple[str, threading.Thread]] = []
    feedback_mod = ModuleType(f"{PACKAGE}.memory.feedback")
    feedback_mod.feedback_cfg = lambda: {
        "enabled": True, "sweep_enabled": True, "sweep_interval_minutes": 0.0}

    def recording_sweep(store, **kw):
        sweep_calls.append(("sweep_expired_usage", threading.current_thread()))
        return {"enabled": True, "swept": 200, "open_remaining": 0, "error": None}

    feedback_mod.sweep_expired_usage = recording_sweep
    env7, patch_ctx7 = build_ws_env(Recorder())
    sweep_patch = patch.dict(sys.modules, {f"{PACKAGE}.memory.feedback": feedback_mod})
    with patch_ctx7, sweep_patch:
        loop_thread7 = threading.current_thread()
        asyncio.run(drive_usage_sweep(env7))
    check("usage sweep：sweep_expired_usage 被调用（驱动真实发生）",
          len(sweep_calls) == 1, repr(sweep_calls))
    check("usage sweep：节流时间戳被推进（防未来优化掉节流）",
          env7["_LAST_USAGE_SWEEP_AT"] > 0, repr(env7["_LAST_USAGE_SWEEP_AT"]))
    check("usage sweep：批量写跑在工作线程（不在循环线程）",
          bool(sweep_calls) and all(t is not loop_thread7 for _n, t in sweep_calls),
          repr([(n, t.name) for n, t in sweep_calls]))

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
