# -*- coding: utf-8 -*-
"""契约 v2 · §5 事件协议 v2 的**单一真相源**。

为什么单独一个模块
------------------
9 类事件的字符串散落在四处：协议文档、后端发送点、前端 case、checker。
每处各写一遍字面量，改一处就漂一处，而两端都不报错——这正是
`scripts/check_protocol.py` 存在的原因。

把它们收在这里，checker 与实现都从这里取，只有**协议文档**必须保持独立
（它要给人读，不能 import 代码）。checker 负责比对两边。

规范来源：`docs/PIXEL_WORKSHOP_SPEC_V2.md` §5（`:772-846`）。

**状态**：2026-10-23 由用户确认这 9 类事件的名字与字段。
本模块只定义名字与载荷字段名，不发送任何事件、不碰网络。
"""
from __future__ import annotations

# 信封版本。这是**信封**版本，不是 schema 版本：加字段靠「未知字段忽略」，
# 不靠 v 递增（否则每加一个字段都要全链路发版）。
ENVELOPE_VERSION = 2

# §5.1 信封字段。`task_id` / `step_id` / `session_id` / `turn_id` 是可选。
ENVELOPE_FIELDS = ("v", "id", "seq", "ts", "type")

# 九类事件（§5.2）。顺序与规范表格一致，便于对读。
TASK_STATE_CHANGE = "TASK_STATE_CHANGE"
STEP_STATE_CHANGE = "STEP_STATE_CHANGE"
CONTRACT_PROPOSED = "CONTRACT_PROPOSED"
ASK_USER = "ASK_USER"
TOOL_EXECUTION = "TOOL_EXECUTION"
VERIFICATION_REPORT = "VERIFICATION_REPORT"
DELIVERY_EFFECT = "DELIVERY_EFFECT"
MEMORY_UPDATE = "MEMORY_UPDATE"
AGENT_PRESENCE = "AGENT_PRESENCE"

EVENT_TYPES = (
    TASK_STATE_CHANGE,
    STEP_STATE_CHANGE,
    CONTRACT_PROPOSED,
    ASK_USER,
    TOOL_EXECUTION,
    VERIFICATION_REPORT,
    DELIVERY_EFFECT,
    MEMORY_UPDATE,
    AGENT_PRESENCE,
)

# §5.2 每类事件的 payload 关键字段。checker 用它做「协议文档写没写」的比对。
PAYLOAD_FIELDS = {
    TASK_STATE_CHANGE: ("from", "to", "attempt", "risk"),
    STEP_STATE_CHANGE: ("from", "to", "agent", "attempt"),
    CONTRACT_PROPOSED: ("contract_id", "version", "goal", "artifact", "risk",
                        "highlighted_slots", "criteria", "budget"),
    ASK_USER: ("question", "options", "missing"),
    TOOL_EXECUTION: ("tool", "status"),
    VERIFICATION_REPORT: ("passed", "criteria"),
    DELIVERY_EFFECT: ("effect_id", "type", "target_class", "status",
                      "idempotency_key"),
    MEMORY_UPDATE: ("action", "memory_ref", "source", "confidence",
                    "provenance"),
    AGENT_PRESENCE: ("state",),
}

# 投递保障（§5.2）。「至少一次」的丢了会让画面与真相不一致，必须断线补齐；
# 「至多一次」的少闪几下无所谓，慢消费者直接丢旧帧。
AT_LEAST_ONCE = (
    TASK_STATE_CHANGE, STEP_STATE_CHANGE, CONTRACT_PROPOSED, ASK_USER,
    VERIFICATION_REPORT, DELIVERY_EFFECT, MEMORY_UPDATE,
)
AT_MOST_ONCE = (TOOL_EXECUTION, AGENT_PRESENCE)

# §5.2 AGENT_PRESENCE 的四态。与既有 turn_started / thinking / done 语义对齐，
# 不引入第二套词汇。
AGENT_PRESENCE_STATES = ("idle", "listening", "thinking", "speaking")

# §5.3 前端 → 后端命令。**与事件成对**：只发事件不接命令，前端按钮点了没反应。
CMD_CONFIRM_CONTRACT = "CONFIRM_CONTRACT"
CMD_ANSWER = "ANSWER"
CMD_CANCEL_TASK = "CANCEL_TASK"
CMD_APPROVE_SIDE_EFFECT = "APPROVE_SIDE_EFFECT"
CMD_RETRY_STEP = "RETRY_STEP"

COMMAND_TYPES = (
    CMD_CONFIRM_CONTRACT, CMD_ANSWER, CMD_CANCEL_TASK,
    CMD_APPROVE_SIDE_EFFECT, CMD_RETRY_STEP,
)

COMMAND_FIELDS = {
    CMD_CONFIRM_CONTRACT: ("task_id", "contract_id", "version",
                           "confirmed_slots", "request_id"),
    CMD_ANSWER: ("task_id", "answer", "request_id"),
    CMD_CANCEL_TASK: ("task_id", "request_id"),
    CMD_APPROVE_SIDE_EFFECT: ("task_id", "effect_id", "request_id"),
    CMD_RETRY_STEP: ("task_id", "step_id", "request_id"),
}

# §5.2 明确**不加**的三样东西。写成常量是为了让「有人手滑加回来」时能被测出来。
FORBIDDEN_EVENT_TYPES = (
    "TASK_SNAPSHOT",     # 首帧走 REST 全量，再发快照就是造第二个真相源
    "VOICE_STATE",       # v0.1 旧名，已改名 AGENT_PRESENCE
)
FORBIDDEN_PAYLOAD_FIELDS = (
    "progress",          # 假进度条破坏「画面等于真实状态」，用 steps_done/steps_total
)


def is_v2_event(name: str) -> bool:
    """这个名字是不是 v2 新增的事件。"""
    return name in EVENT_TYPES


def delivery_guarantee(name: str) -> str:
    """该事件的投递保障。未知事件返回空串（不猜）。"""
    if name in AT_LEAST_ONCE:
        return "at_least_once"
    if name in AT_MOST_ONCE:
        return "at_most_once"
    return ""
