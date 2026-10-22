"""Task / Step 双层状态集（规范 §4.2）。

只放枚举值与查表函数：13 个 Task 状态、11 个 Step 状态、终态集合、
阻塞原因、可恢复目标与台账投影状态。这里不做迁移判断，也不持有业务对象。

边界：不 import 任何业务模块，不读文件、不发网络、不打印。
"""
from __future__ import annotations

# §4.2 Task 状态（顺序与规范表格一致）
TASK_STATES: tuple[str, ...] = (
    "CLARIFYING",
    "PROPOSED",
    "SPEC_LOCKED",
    "DISPATCHED",
    "EXECUTING",
    "VERIFYING",
    "BLOCKED",
    "ACCEPTED",
    "DELIVERING",
    "DELIVERED",
    "CANCELLED",
    "FAILED",
    "TIMEOUT",
)

# §4.2 终态：DELIVERED / CANCELLED / FAILED / TIMEOUT
TASK_TERMINAL: frozenset[str] = frozenset({
    "DELIVERED",
    "CANCELLED",
    "FAILED",
    "TIMEOUT",
})

# §4.2 Step 状态（顺序与规范表格一致）
STEP_STATES: tuple[str, ...] = (
    "CREATED",
    "READY",
    "DISPATCHED",
    "RUNNING",
    "OBSERVED",
    "VERIFIED",
    "REJECTED",
    "FAILED",
    "UNKNOWN",
    "SKIPPED",
    "CANCELLED",
)

# §4.2 终态：VERIFIED / FAILED / SKIPPED / CANCELLED
STEP_TERMINAL: frozenset[str] = frozenset({
    "VERIFIED",
    "FAILED",
    "SKIPPED",
    "CANCELLED",
})

# §4.2 / §6.6 BLOCKED.reason 的五个取值
BLOCKED_REASONS: tuple[str, ...] = (
    "need_clarification",
    "need_approval",
    "need_authorization",
    "retry_exhausted",
    "ambiguous_result",
)

# §4.4 T11：DISPATCHED 也在可恢复目标里（回执不明，先对账再重派）
RESUME_TARGETS: tuple[str, ...] = (
    "SPEC_LOCKED",
    "DISPATCHED",
    "EXECUTING",
    "VERIFYING",
    "DELIVERING",
)

# §4.3 台账七态
LEDGER_STATUSES: tuple[str, ...] = (
    "drafting",
    "briefing",
    "running",
    "blocked",
    "auditing",
    "ready_to_report",
    "done",
)


class StateValueError(ValueError):
    """状态值不在枚举内。

    与「迁移不合法」区分开：这里是构造/入参错误，不是状态机判定结果。
    """


def require_task_state(value: object) -> str:
    """返回合法的 Task 状态名，否则抛 StateValueError。"""
    if isinstance(value, str) and value in TASK_STATES:
        return value
    raise StateValueError(f"未知的 Task 状态：{value!r}")


def require_step_state(value: object) -> str:
    """返回合法的 Step 状态名，否则抛 StateValueError。"""
    if isinstance(value, str) and value in STEP_STATES:
        return value
    raise StateValueError(f"未知的 Step 状态：{value!r}")


def is_task_terminal(status: str) -> bool:
    """Task 是否终态；未知值不算终态。"""
    return isinstance(status, str) and status in TASK_TERMINAL


def is_step_terminal(status: str) -> bool:
    """Step 是否终态；未知值不算终态。"""
    return isinstance(status, str) and status in STEP_TERMINAL
