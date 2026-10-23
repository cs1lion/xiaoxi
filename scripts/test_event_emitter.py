# -*- coding: utf-8 -*-
"""发射层（server/contract/emitter.py）的单元测试。

    .\.venv\Scripts\python.exe scripts\test_event_emitter.py

**不连 socket、不起服务**：广播函数是注入的假函数。

七组断言
--------
  A 信封形状    v/id/seq/ts/type 齐全；可选字段空时**不出现**（不是 null）
  B 未知事件    不发、不抛、说明原因（一条事件名写错不该让任务挂掉）
  C 不编造      payload 取不到就留空并记 missing——**绝不填默认值**
  D 投递保障    至少一次失败要能让调用方知道；至多一次失败算发过
  E 迁移发射    一个 TransitionResult 的多个事件按声明顺序发，seq 递增
  F dry-run     不注入广播也能构造信封（供回放与单测）
  G 边界        不 import 业务模块、不自己开 socket、不维护全局 seq
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.contract import events_v2 as v2  # noqa: E402
from server.contract.emitter import (  # noqa: E402
    build_envelope, emit, emit_transition, missing_fields,
)

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


class Recorder:
    """假广播函数：记录收到什么，可配置返回值/抛异常。"""

    def __init__(self, ok: bool = True, boom: bool = False) -> None:
        self.calls: list[dict] = []
        self.ok = ok
        self.boom = boom

    def __call__(self, envelope: dict) -> bool:
        if self.boom:
            raise RuntimeError("broadcast exploded")
        self.calls.append(envelope)
        return self.ok


# ---------- A 信封形状 ----------

def test_envelope() -> None:
    print("[A 信封形状：必填齐全，可选空时不出现]")

    env = build_envelope(v2.AGENT_PRESENCE, {"state": "idle"}, seq=7)
    for key in ("v", "id", "seq", "ts", "type", "payload"):
        check(f"信封含 {key}", key in env, str(sorted(env)))
    check("v 是 2", env["v"] == 2, str(env["v"]))
    check("seq 原样保留", env["seq"] == 7, str(env["seq"]))
    check("ts 是毫秒（13 位量级）", env["ts"] > 10**12, str(env["ts"]))
    check("type 正确", env["type"] == v2.AGENT_PRESENCE, env["type"])

    # 可选字段为空时不能出现（§5.1 是可选不是可空）
    check("空 task_id 不出现在信封里", "task_id" not in env, str(env.keys()))
    check("空 step_id 不出现在信封里", "step_id" not in env, str(env.keys()))

    env2 = build_envelope(v2.STEP_STATE_CHANGE, {"from": "A", "to": "B"},
                          seq=1, task_id="t1", step_id="s1", session_id="sess",
                          turn_id="turn")
    check("有值时可选字段出现",
          all(k in env2 for k in ("task_id", "step_id", "session_id", "turn_id")),
          str(sorted(env2)))

    check("id 唯一（两次不同）",
          build_envelope(v2.ASK_USER, {}, seq=1)["id"]
          != build_envelope(v2.ASK_USER, {}, seq=1)["id"])

    # 显式 ts 可复现
    env3 = build_envelope(v2.ASK_USER, {}, seq=1, ts=1761000000000)
    check("显式 ts 原样保留", env3["ts"] == 1761000000000, str(env3["ts"]))

    try:
        build_envelope("", {}, seq=1)
        raised = ""
    except ValueError as exc:
        raised = str(exc)
    check("空 type 抛 ValueError", bool(raised), "没抛")


# ---------- B 未知事件 ----------

def test_unknown() -> None:
    print("[B 未知事件：不发、不抛、说明原因]")

    rec = Recorder()
    result = emit("NOT_AN_EVENT", {}, seq=1, broadcast=rec)
    check("未知事件 emitted=False", result.emitted is False)
    check("未知事件不调用广播", len(rec.calls) == 0, str(len(rec.calls)))
    check("未知事件带 reason", bool(result.reason), "没写原因")
    check("reason 点名了事件名", "NOT_AN_EVENT" in result.reason, result.reason)

    # 旧协议事件也不该被当 v2 发
    result = emit("commission_update", {}, seq=1, broadcast=rec)
    check("旧事件 commission_update 不被 v2 发射", result.emitted is False)


# ---------- C 不编造 ----------

def test_no_fabrication() -> None:
    print("[C 不编造：取不到就留空并记 missing，绝不填默认值]")

    # TASK_STATE_CHANGE 的必填是 from/to/attempt/risk
    result = emit(v2.TASK_STATE_CHANGE, {"from": "A", "to": "B"},
                  seq=1, broadcast=Recorder())
    check("缺 attempt/risk 被记进 missing",
          set(result.missing) == {"attempt", "risk"}, str(result.missing))
    check("missing 的字段没有出现在 payload 里",
          "attempt" not in result.envelope["payload"]
          and "risk" not in result.envelope["payload"],
          str(result.envelope["payload"]))
    # 关键：不能因为缺就填一个假 risk
    check("绝不填默认 risk", "risk" not in result.envelope["payload"],
          "填了假 risk")

    check("missing_fields 对齐全的 payload 返回空",
          missing_fields(v2.AGENT_PRESENCE, {"state": "idle"}) == ())

    # 值明确为 None 也算缺
    check("payload 值为 None 算缺",
          missing_fields(v2.AGENT_PRESENCE, {"state": None}) == ("state",))

    # 未定义必填的事件不报缺
    check("未知事件不报缺", missing_fields("NOPE", {}) == ())


# ---------- D 投递保障 ----------

def test_delivery() -> None:
    print("[D 投递保障：至少一次失败要让调用方知道；至多一次失败算发过]")

    # 至少一次 + 广播失败 -> emitted=False
    rec = Recorder(ok=False)
    result = emit(v2.TASK_STATE_CHANGE,
                  {"from": "A", "to": "B", "attempt": 0, "risk": "low"},
                  seq=1, broadcast=rec)
    check("至少一次失败 -> emitted=False", result.emitted is False,
          str(result.emitted))
    check("至少一次失败带 reason", bool(result.reason), "没写原因")
    check("delivery 标记正确", result.delivery == "at_least_once", result.delivery)

    # 至多一次 + 广播失败 -> 仍算发过（§5.2 可丢）
    rec = Recorder(ok=False)
    result = emit(v2.TOOL_EXECUTION, {"tool": "web_fetch", "status": "running"},
                  seq=1, broadcast=rec)
    check("至多一次失败 -> emitted=True（可丢）", result.emitted is True,
          str(result.emitted))
    check("至多一次失败仍带 reason（可审计）", bool(result.reason), "没写")

    # 广播抛异常：不能冒泡打断任务
    rec = Recorder(boom=True)
    try:
        result = emit(v2.TASK_STATE_CHANGE,
                      {"from": "A", "to": "B", "attempt": 0, "risk": "low"},
                      seq=1, broadcast=rec)
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    check("广播抛异常不冒泡", raised is False, "异常冒出来了")
    check("异常时至少一次 -> emitted=False", result.emitted is False)
    check("异常原因写进 reason", "RuntimeError" in result.reason, result.reason)

    # 成功路径
    rec = Recorder()
    result = emit(v2.AGENT_PRESENCE, {"state": "idle"}, seq=1, broadcast=rec)
    check("成功 -> emitted=True", result.emitted is True)
    check("成功 -> 广播收到一次", len(rec.calls) == 1, str(len(rec.calls)))
    check("成功 -> reason 为空", result.reason == "", result.reason)


# ---------- E 迁移发射 ----------

def test_transition() -> None:
    print("[E 迁移发射：多个事件按声明顺序发，seq 递增]")

    # T8 声明 STEP_STATE_CHANGE，T2 声明 CONTRACT_PROPOSED —— 用一个双事件的
    # 假 TransitionResult 验证顺序与 seq
    result = SimpleNamespace(
        rule_id="T-X", source="CLARIFYING", target="PROPOSED",
        events=(v2.TASK_STATE_CHANGE, v2.CONTRACT_PROPOSED),
        state=SimpleNamespace(attempt=2),
    )
    facts = SimpleNamespace(
        risk="medium", contract_id="c-1", version=1, goal="整理便笺",
        artifact={"kind": "report_md"}, highlighted_slots=[],
        criteria=[], budget={},
    )
    rec = Recorder()
    results = emit_transition(result, seq=10, broadcast=rec,
                              task_id="t1", facts=facts)
    check("发出两个事件", len(results) == 2, str(len(results)))
    check("两个都送达", all(r.emitted for r in results), str([r.reason for r in results]))
    check("顺序与声明一致",
          [r.envelope["type"] for r in results]
          == [v2.TASK_STATE_CHANGE, v2.CONTRACT_PROPOSED],
          str([r.envelope["type"] for r in results]))
    check("seq 递增 10, 11",
          [r.envelope["seq"] for r in results] == [10, 11],
          str([r.envelope["seq"] for r in results]))
    check("task_id 透传到两条", all(r.envelope.get("task_id") == "t1"
                                  for r in results))

    # from/to 从 result 的 source/target 取
    payload0 = results[0].envelope["payload"]
    check("from 取自 result.source", payload0.get("from") == "CLARIFYING",
          str(payload0.get("from")))
    check("to 取自 result.target", payload0.get("to") == "PROPOSED",
          str(payload0.get("to")))
    check("attempt 取自 state", payload0.get("attempt") == 2,
          str(payload0.get("attempt")))
    check("risk 取自 facts", payload0.get("risk") == "medium",
          str(payload0.get("risk")))

    # 空 events 的迁移不发任何东西
    empty = SimpleNamespace(events=(), source="A", target="B", state=None)
    check("events 为空 -> 不发", emit_transition(empty, seq=1) == [])

    # 取不到的字段不编造
    bare = SimpleNamespace(rule_id="T-Y", source="A", target="B",
                           events=(v2.TASK_STATE_CHANGE,),
                           state=SimpleNamespace(attempt=0))
    results = emit_transition(bare, seq=1, broadcast=Recorder())
    check("无 facts 时 risk 缺失被记",
          "risk" in results[0].missing, str(results[0].missing))
    check("无 facts 时 risk 不在 payload",
          "risk" not in results[0].envelope["payload"], "编了 risk")

    # Step 的 retries 映射成 attempt
    step = SimpleNamespace(rule_id="S-X", source="CREATED", target="READY",
                           events=(v2.STEP_STATE_CHANGE,),
                           state=SimpleNamespace(retries=3))
    results = emit_transition(step, seq=1, broadcast=Recorder())
    check("Step 的 retries 映射成 attempt",
          results[0].envelope["payload"].get("attempt") == 3,
          str(results[0].envelope["payload"]))


# ---------- F dry-run ----------

def test_dry_run() -> None:
    print("[F dry-run：不注入广播也能构造信封]")

    result = emit(v2.AGENT_PRESENCE, {"state": "thinking"}, seq=1)
    check("dry-run emitted=True", result.emitted is True)
    check("dry-run 有信封", bool(result.envelope), "信封为空")
    check("dry-run reason 说明是 dry-run",
          "dry-run" in result.reason, result.reason)
    check("dry-run 仍报 missing",
          emit(v2.TASK_STATE_CHANGE, {"from": "A"}, seq=1).missing
          == ("to", "attempt", "risk"),
          str(emit(v2.TASK_STATE_CHANGE, {"from": "A"}, seq=1).missing))


# ---------- G 边界 ----------

def test_boundary() -> None:
    print("[G 边界：不 import 业务模块、不自己开 socket、不维护全局 seq]")

    path = ROOT / "server" / "contract" / "emitter.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    forbidden = ("server.assistant", "server.memory", "server.dialogue",
                 "server.interaction", "server.persona", "server.app",
                 "server.task")
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == m or alias.name.startswith(m + ".")
                       for m in forbidden):
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and any(
                    module == m or module.startswith(m + ".") for m in forbidden):
                bad.append(module)
    check("不 import 业务模块（含 server.task）", not bad, str(bad))

    check("不 import socket/http/urllib",
          not any(m in source for m in ("import socket", "import http",
                                        "import urllib")),
          "碰了网络")

    # 不维护全局 seq：模块级不该有可变计数器
    module_level_ints = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    module_level_ints.append(target.id)
    check("模块级只有常量（大写），没有全局 seq 变量",
          all(name in {"ENVELOPE_VERSION", "Broadcast"} or name.isupper()
              for name in module_level_ints),
          str(module_level_ints))

    # 广播必须由参数注入，不能自己去找连接
    check("emit 签名有 broadcast 参数", "broadcast" in source)
    check("不 import app（拿连接列表）", "server.app" not in source)


def main() -> int:
    test_envelope()
    test_unknown()
    test_no_fabrication()
    test_delivery()
    test_transition()
    test_dry_run()
    test_boundary()

    print()
    print("=" * 72)
    if failed:
        print(f"FAILED {len(failed)}")
        for item in failed[:20]:
            print("  - " + item)
        return 1
    print(f"PASS {passed}/{passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
