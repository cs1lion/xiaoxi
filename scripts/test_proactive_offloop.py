# -*- coding: utf-8 -*-
"""第二十批回归门（#31 残留，2026-10-31 评审实证）：主动开口的真实函数体
必须把无界检索跑在事件循环外。

与 test_voice_offloop.py 的分工要说清：那边用替身驱动 app 处理器，
`proactive_turn` 是 AsyncMock、`ambient_event` 是 lambda——**替身天然测不到
被 mock 掉的真实函数体**（评审发现的盲区正在于此）。这里反过来：不打桩
函数本身，只打桩它的**依赖**（manager.retriever / llm / store_message），
驱动真身，断言 SQLite/检索工作发生的**线程身份**。判据与第十六批一致：
同步版恒在循环线程，to_thread 版恒在 asyncio worker，双向无灰区。

覆盖三条行为：
1. `proactive_turn` 真身：`retriever.search` 与 `context_for` 在工作线程跑；
2. 许可位前置（本批改的短路顺序）：allow_* 全关时**一次检索都不起**；
3. `ambient_event` 冷启动破冰路径：经 `to_thread` 调用（app.py 的形态）时
   检索在工作线程。函数本身仍可从任意线程同步直调——卸载点在调用方，
   这里钉的就是调用方形态 + 真身不新增循环内阻塞。

只用临时库（DB_PATH=:memory: 在任何 server import 之前，#39 哨兵口径）。
Run: python -B scripts/test_proactive_offloop.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DB_PATH"] = ":memory:"

from server.dialogue import orchestrator as O  # noqa: E402
from server.memory.store import MemoryStore  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


class FakeMemory:
    """retriever.search 返回 (Memory, score) 元组——真身只读 .value/.id。"""

    def __init__(self, id_: int, value: str) -> None:
        self.id = id_
        self.value = value


class ThreadSpyRetriever:
    """记录 search / build_context 的执行线程；返回固定假候选（结构同真 retriever）。"""

    def __init__(self, store) -> None:
        self.store = store
        self.calls: list[tuple[str, threading.Thread]] = []

    def search(self, query, **kw):
        self.calls.append(("search", threading.current_thread()))
        return [(FakeMemory(1, "周五要交评测报告初稿"), 0.9)]

    def build_context(self, query, **kw):
        self.calls.append(("build_context", threading.current_thread()))
        return "", [], False

    def names_on_loop(self, loop_thread: threading.Thread) -> list[str]:
        return [n for n, t in self.calls if t is loop_thread]


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def complete(self, messages, **kw):
        return self.reply


def proactivity_cfg(**flags: bool) -> dict:
    base = {"default_enabled": True, "min_interval_seconds": 0,
            "only_when_user_recently_idle_seconds": 0, "max_per_day": 3,
            "allow_remind_todo": flags.get("todo", False),
            "allow_continue_episode": flags.get("episode", False)}
    return {"proactivity": base}


def seed_todo(store: MemoryStore) -> None:
    store.add({"kind": "todo", "subject": "user", "key": "周五交稿",
               "value": "周五要交评测报告初稿", "source": "stated",
               "importance": 0.8})


def drive_proactive_turn(orch, spy: ThreadSpyRetriever, llm_reply: str):
    orig_manager, orig_llm, orig_store_msg = O.manager, O.llm, O.store_message
    orig_pacing = orch._pacing_override
    messages_seen: list = []

    def fake_store_message(session_id, role, text, mode="text"):
        messages_seen.append((session_id, role, text, mode,
                              threading.current_thread()))
        return 1

    O.manager = SimpleNamespace(enabled=True, retriever=spy, store=spy.store,
                                context_for=lambda q: (
                                    spy.calls.append(("context_for",
                                                      threading.current_thread())),
                                    "〔记忆块〕")[1])
    O.llm = FakeLLM(llm_reply)
    O.store_message = fake_store_message
    orch.pacing = proactivity_cfg(todo=True, episode=True)
    orch.state["proactivity_enabled"] = True
    orch.state["proactive_suppressed_until"] = 0.0
    orch.state["last_proactive_at"] = 0.0
    orch.state["proactive_count_today"] = 0

    async def go():
        return await orch.proactive_turn(session_id="s1", idle_seconds=99999)

    try:
        loop_thread = threading.current_thread()
        result = asyncio.run(go())
    finally:
        O.manager, O.llm, O.store_message = orig_manager, orig_llm, orig_store_msg
        orch._pacing_override = orig_pacing
    return result, loop_thread, messages_seen


def main() -> int:
    store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="prooff-")) / "m.db"))
    seed_todo(store)
    orch = O.orchestrator

    print("[proactive_turn 真身：检索与落库在工作线程]")
    spy = ThreadSpyRetriever(store)
    result, loop_thread, msgs = drive_proactive_turn(
        orch, spy, "[开心]对了，你之前记的那件事别忘啦。")
    check("真身产出了结果（不是替身路径）", result is not None and result.proactive,
          repr(result))
    names = [n for n, _t in spy.calls]
    check("search 与 context_for 都发生了", "search" in names and "context_for" in names,
          repr(names))
    check("检索全部在工作线程（无一条在循环线程）",
          not spy.names_on_loop(loop_thread), repr(spy.names_on_loop(loop_thread)))
    check("落库 store_message 在工作线程",
          bool(msgs) and msgs[0][4] is not loop_thread,
          repr([(m[0], m[4].name) for m in msgs]))

    print("[许可位前置：flag 全关时一次检索都不起]")
    spy2 = ThreadSpyRetriever(store)
    orig_manager2, orig_llm2, orig_msg2 = O.manager, O.llm, O.store_message
    orig_pacing2 = orch._pacing_override
    O.manager = SimpleNamespace(enabled=True, retriever=spy2, store=store,
                                context_for=lambda q: "")
    O.llm = FakeLLM("[base]不该被调用")
    O.store_message = lambda *a, **k: 1
    orch.pacing = proactivity_cfg(todo=False, episode=False)
    orch.state.update({"proactivity_enabled": True, "proactive_suppressed_until": 0.0,
                       "last_proactive_at": 0.0, "proactive_count_today": 0})

    async def go2():
        return await orch.proactive_turn(session_id="s1", idle_seconds=99999)

    try:
        result2 = asyncio.run(go2())
    finally:
        O.manager, O.llm, O.store_message = orig_manager2, orig_llm2, orig_msg2
        orch._pacing_override = orig_pacing2
    check("flag 全关返回 None", result2 is None, repr(result2))
    check("flag 全关时零次检索（改前会先付一次全库读再判 flag）",
          not spy2.calls, repr(spy2.calls))

    print("[ambient 破冰：to_thread 形态（app.py 的调用方式）检索离环]")
    spy3 = ThreadSpyRetriever(store)

    class NoContentStore:
        def list_reflections(self, limit=1):
            return []

        def get_mood(self):
            return {}

        def list_sparks(self, states=()):
            return []

    orig_manager3 = O.manager
    orig_pacing3 = orch._pacing_override
    O.manager = SimpleNamespace(enabled=True, retriever=spy3, store=NoContentStore())
    orch.pacing = proactivity_cfg(todo=True)
    orch.state.update({"proactivity_enabled": True, "proactive_suppressed_until": 0.0,
                       "last_ambient_tier": None, "last_ambient_at": 0.0,
                       "voice_cycle": 0, "ambient_seq": 0})
    # 退避阈值刚过（28s≥28s 配置）；无 reflection/mood/spark → 走破冰 → search。
    # 空查询走 spy 的 all_rows 返回种子待办——真身取 [0] 的 value 组台词。

    async def go3():
        async def loop_watchdog(stop: asyncio.Event):
            while not stop.is_set():
                if spy3.names_on_loop(threading.main_thread()):
                    return
                await asyncio.sleep(0.01)
        stop = asyncio.Event()
        w = asyncio.ensure_future(loop_watchdog(stop))
        event = await asyncio.to_thread(orch.ambient_event, idle_seconds=30,
                                        now=1000000.0)
        stop.set()
        await w
        return event

    try:
        event3 = asyncio.run(go3())
    finally:
        O.manager = orig_manager3
        orch._pacing_override = orig_pacing3
    check("破冰真的走了检索（测试驱动到了目标分支）",
          any(n == "search" for n, _t in spy3.calls), repr(spy3.calls))
    check("to_thread 形态下检索不在循环线程",
          not spy3.names_on_loop(threading.main_thread()),
          repr(spy3.names_on_loop(threading.main_thread())))
    check("产出了 mumble 事件（行为不变）",
          isinstance(event3, dict) and event3.get("type") == "mumble", repr(event3))

    store.close()
    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
