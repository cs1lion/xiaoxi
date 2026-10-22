"""v2 Task 状态 -> 台账七态的单向投影（规范 §4.3）。

三条铁律：单向（台账不得反推 Task）、未知值降级（不往台账塞新状态值）、
旧客户端零改动（13 态只活在 TaskEngine 与 v2 事件里）。

本模块只算「应该写成什么」，不写库、不发事件。
"""
from __future__ import annotations

from dataclasses import dataclass

from .machine import TaskState
from .states import LEDGER_STATUSES, require_task_state

# §4.9 / §6.6：只有 need_clarification 落到 clarification，其余四类都算 approval。
_CLARIFICATION_REASONS = ("need_clarification",)


@dataclass(frozen=True)
class LedgerProjection:
    """一行台账该长什么样；has_row=False 表示纯语音追问阶段不建单。"""

    has_row: bool
    status: str = ""
    blocked_kind: str = ""
    audit_kind: str = ""


def blocked_kind_of(reason: str) -> str:
    """BLOCKED.reason -> 台账 blocked_kind（§6.6）。"""
    text = (reason or "").strip()
    if not text:
        return ""
    if text in _CLARIFICATION_REASONS:
        return "clarification"
    return "approval"


_TASK_TO_LEDGER = {
    "PROPOSED": "drafting",
    "SPEC_LOCKED": "briefing",
    "DISPATCHED": "briefing",
    "EXECUTING": "running",
    "VERIFYING": "auditing",
    "ACCEPTED": "ready_to_report",
    "DELIVERING": "ready_to_report",
    "DELIVERED": "done",
}


def project_task(state: TaskState, *, failed_to: str = "blocked") -> LedgerProjection:
    """把 Task 状态投影成台账行；CLARIFYING 不建行。"""
    status = require_task_state(state.status)
    if failed_to not in ("blocked", "done"):
        raise ValueError(f"failed_to 只接受 blocked / done，实际 {failed_to!r}")

    if status == "CLARIFYING":
        return LedgerProjection(has_row=False)

    if status == "BLOCKED":
        return _row("blocked", blocked_kind=blocked_kind_of(state.blocked_reason))
    if status == "CANCELLED":
        return _row("done", audit_kind="cancelled")
    if status == "FAILED":
        # §4.3：FAILED -> failed_to（默认 blocked），blocked_kind 一律 approval，另记 audit=failed。
        return _row(failed_to, blocked_kind="approval", audit_kind="failed")
    if status == "TIMEOUT":
        return _row("blocked", blocked_kind="approval", audit_kind="timeout")
    return _row(_TASK_TO_LEDGER[status])


def _row(status: str, *, blocked_kind: str = "", audit_kind: str = "") -> LedgerProjection:
    """所有带行的投影都从这里出，status 必须落在台账七态内。"""
    if status not in LEDGER_STATUSES:
        raise ValueError(f"投影出的台账状态不合法：{status!r}")
    return LedgerProjection(has_row=True, status=status, blocked_kind=blocked_kind,
                            audit_kind=audit_kind)
