# -*- coding: utf-8 -*-
"""契约 v2 · §5 事件协议 v2 常量的自洽性测试。

    .\.venv\Scripts\python.exe scripts\test_events_v2.py

为什么需要它
------------
`check_protocol.py` 的第 12 类检查保证「协议文档写全了」。但常量自己是否自洽
（九类都定了投递保障、禁止项没混进来、命令字段齐全）它管不到——那是本文件的活。

分开的理由：checker 比的是**两份文件**（代码常量 vs PROTOCOL.md）；
本文件比的是**常量内部**。混在一起，文档改了会连累常量测试，定位变难。

六组断言
--------
  A 九类齐全      规范 §5.2 的九类一个不多一个不少
  B 投递保障      九类都定保障，且不重叠；至少一次 / 至多一次 分档正确
  C 载荷字段      每类都有 payload 字段定义，非空
  D 禁止项        TASK_SNAPSHOT / VOICE_STATE / progress 不得混入
  E 命令          五个命令齐全，字段齐全，都有 request_id（幂等键）
  F 协议文档      本节已写进 PROTOCOL.md（与 checker 同源，但独立验证）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.contract import events_v2 as v2  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


# 规范 §5.2 表格里的九类，逐字抄自 docs/PIXEL_WORKSHOP_SPEC_V2.md:811-819。
# **故意独立抄一遍**，不从 events_v2 读——否则常量写错了这里跟着错，测不出来。
SPEC_EVENTS = (
    "TASK_STATE_CHANGE", "STEP_STATE_CHANGE", "CONTRACT_PROPOSED", "ASK_USER",
    "TOOL_EXECUTION", "VERIFICATION_REPORT", "DELIVERY_EFFECT", "MEMORY_UPDATE",
    "AGENT_PRESENCE",
)


def test_event_set() -> None:
    print("[A 九类齐全：规范 §5.2 的九类一个不多一个不少]")

    check("事件总数是 9", len(v2.EVENT_TYPES) == 9, str(len(v2.EVENT_TYPES)))
    check("与规范表格逐字一致",
          tuple(v2.EVENT_TYPES) == SPEC_EVENTS,
          f"{sorted(set(SPEC_EVENTS) - set(v2.EVENT_TYPES))} 缺 / "
          f"{sorted(set(v2.EVENT_TYPES) - set(SPEC_EVENTS))} 多")
    check("无重复", len(set(v2.EVENT_TYPES)) == len(v2.EVENT_TYPES), "有重复")

    # 名字必须是 SCREAMING_SNAKE（前端按这个名字 switch）
    for name in v2.EVENT_TYPES:
        check(f"{name} 是 SCREAMING_SNAKE",
              name == name.upper() and "_" in name, name)

    check("is_v2_event 认全部九类",
          all(v2.is_v2_event(n) for n in v2.EVENT_TYPES))
    check("is_v2_event 不认旧事件",
          not v2.is_v2_event("commission_update")
          and not v2.is_v2_event("thinking"))


def test_guarantees() -> None:
    print("[B 投递保障：九类都定保障，且不重叠]")

    covered = set(v2.AT_LEAST_ONCE) | set(v2.AT_MOST_ONCE)
    check("九类都有保障",
          covered == set(v2.EVENT_TYPES),
          f"没定：{sorted(set(v2.EVENT_TYPES) - covered)}")
    check("不重叠",
          not (set(v2.AT_LEAST_ONCE) & set(v2.AT_MOST_ONCE)),
          str(set(v2.AT_LEAST_ONCE) & set(v2.AT_MOST_ONCE)))

    # 规范点名的两档必须对得上
    check("TASK_STATE_CHANGE 是至少一次",
          v2.TASK_STATE_CHANGE in v2.AT_LEAST_ONCE)
    check("CONTRACT_PROPOSED 是至少一次",
          v2.CONTRACT_PROPOSED in v2.AT_LEAST_ONCE)
    check("VERIFICATION_REPORT 是至少一次",
          v2.VERIFICATION_REPORT in v2.AT_LEAST_ONCE)
    check("TOOL_EXECUTION 是至多一次（可丢）",
          v2.TOOL_EXECUTION in v2.AT_MOST_ONCE)
    check("AGENT_PRESENCE 是至多一次（可丢）",
          v2.AGENT_PRESENCE in v2.AT_MOST_ONCE)

    check("delivery_guarantee 返回正确值",
          v2.delivery_guarantee(v2.TASK_STATE_CHANGE) == "at_least_once"
          and v2.delivery_guarantee(v2.TOOL_EXECUTION) == "at_most_once")
    check("delivery_guarantee 对未知事件返回空串",
          v2.delivery_guarantee("NOT_AN_EVENT") == "")


def test_payload() -> None:
    print("[C 载荷字段：每类都有定义且非空]")

    check("每类都有 payload 定义",
          set(v2.PAYLOAD_FIELDS) == set(v2.EVENT_TYPES),
          f"缺：{sorted(set(v2.EVENT_TYPES) - set(v2.PAYLOAD_FIELDS))}")
    for name, fields in v2.PAYLOAD_FIELDS.items():
        check(f"{name} 的 payload 非空", bool(fields), "空元组")

    # 规范点名的必填字段（抽查最能体现契约的几处）
    check("TASK_STATE_CHANGE 带 from/to",
          {"from", "to"} <= set(v2.PAYLOAD_FIELDS[v2.TASK_STATE_CHANGE]))
    check("STEP_STATE_CHANGE 带 agent",
          "agent" in v2.PAYLOAD_FIELDS[v2.STEP_STATE_CHANGE])
    check("CONTRACT_PROPOSED 带 contract_id/version",
          {"contract_id", "version"} <= set(v2.PAYLOAD_FIELDS[v2.CONTRACT_PROPOSED]))
    check("DELIVERY_EFFECT 带 idempotency_key",
          "idempotency_key" in v2.PAYLOAD_FIELDS[v2.DELIVERY_EFFECT])
    check("MEMORY_UPDATE 带 provenance",
          "provenance" in v2.PAYLOAD_FIELDS[v2.MEMORY_UPDATE])
    check("AGENT_PRESENCE 的 payload 只有 state",
          tuple(v2.PAYLOAD_FIELDS[v2.AGENT_PRESENCE]) == ("state",),
          str(v2.PAYLOAD_FIELDS[v2.AGENT_PRESENCE]))

    # AGENT_PRESENCE 四态
    check("AGENT_PRESENCE 四态齐全",
          tuple(v2.AGENT_PRESENCE_STATES) ==
          ("idle", "listening", "thinking", "speaking"),
          str(v2.AGENT_PRESENCE_STATES))


def test_forbidden() -> None:
    print("[D 禁止项：规范明确不加的三样]")

    check("TASK_SNAPSHOT 不在事件表里",
          "TASK_SNAPSHOT" not in v2.EVENT_TYPES)
    check("VOICE_STATE 不在事件表里",
          "VOICE_STATE" not in v2.EVENT_TYPES)
    check("TASK_SNAPSHOT 被列进禁止项",
          "TASK_SNAPSHOT" in v2.FORBIDDEN_EVENT_TYPES)
    check("VOICE_STATE 被列进禁止项",
          "VOICE_STATE" in v2.FORBIDDEN_EVENT_TYPES)
    check("progress 被列进禁止字段",
          "progress" in v2.FORBIDDEN_PAYLOAD_FIELDS)

    # progress 绝不能出现在 TOOL_EXECUTION 的 payload 里
    check("TOOL_EXECUTION 不含 progress",
          "progress" not in v2.PAYLOAD_FIELDS[v2.TOOL_EXECUTION],
          str(v2.PAYLOAD_FIELDS[v2.TOOL_EXECUTION]))
    # 规范改用 steps_done / steps_total 表达进度（这两个是可选字段，
    # 不在必填表里，但协议文档必须写明它们的名字——否则实现方不知道发什么）。
    protocol = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    check("协议写明用 steps_done/steps_total 表达进度",
          "steps_done" in protocol and "steps_total" in protocol,
          "协议里找不到这两个字段名")
    check("协议写明 indeterminate 兜底（给不出百分比时）",
          "indeterminate" in protocol, "协议里没写 indeterminate")


def test_commands() -> None:
    print("[E 命令：五个齐全，字段齐全，都有幂等键]")

    check("命令总数是 5", len(v2.COMMAND_TYPES) == 5, str(len(v2.COMMAND_TYPES)))
    expected = ("CONFIRM_CONTRACT", "ANSWER", "CANCEL_TASK",
                "APPROVE_SIDE_EFFECT", "RETRY_STEP")
    check("与规范 §5.3 一致",
          tuple(v2.COMMAND_TYPES) == expected, str(v2.COMMAND_TYPES))

    check("每个命令都有字段定义",
          set(v2.COMMAND_FIELDS) == set(v2.COMMAND_TYPES),
          f"缺：{sorted(set(v2.COMMAND_TYPES) - set(v2.COMMAND_FIELDS))}")

    # 幂等键：五个命令都必须带 request_id，否则网络重试会产生第二次副作用
    for name, fields in v2.COMMAND_FIELDS.items():
        check(f"{name} 带 request_id（幂等键）",
              "request_id" in fields, str(fields))
        check(f"{name} 带 task_id", "task_id" in fields, str(fields))

    check("CONFIRM_CONTRACT 带 version（防陈旧确认）",
          "version" in v2.COMMAND_FIELDS[v2.CMD_CONFIRM_CONTRACT])
    check("APPROVE_SIDE_EFFECT 带 effect_id",
          "effect_id" in v2.COMMAND_FIELDS[v2.CMD_APPROVE_SIDE_EFFECT])
    check("RETRY_STEP 带 step_id",
          "step_id" in v2.COMMAND_FIELDS[v2.CMD_RETRY_STEP])


def test_protocol_doc() -> None:
    print("[F 协议文档：本节已写进 PROTOCOL.md]")

    protocol = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    check("PROTOCOL.md 有第 25 节",
          "## 二十五、事件协议 v2" in protocol)

    missing = [n for n in v2.EVENT_TYPES if n not in protocol]
    check("九类事件都写进协议", not missing, str(missing))

    missing_cmd = [n for n in v2.COMMAND_TYPES if n not in protocol]
    check("五个命令都写进协议", not missing_cmd, str(missing_cmd))

    check("信封版本 2 写明", "v: 2" in protocol or "v：2" in protocol)
    check("首帧走 REST（无快照事件）写明",
          "REST 全量" in protocol or "首帧" in protocol)
    check("明确写了不加 TASK_SNAPSHOT",
          "TASK_SNAPSHOT" in protocol)
    check("明确写了不加 progress",
          "progress" in protocol)
    check("明确写了 VOICE_STATE 改名",
          "VOICE_STATE" in protocol)


def main() -> int:
    test_event_set()
    test_guarantees()
    test_payload()
    test_forbidden()
    test_commands()
    test_protocol_doc()

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
