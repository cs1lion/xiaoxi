"""Task / Step 双层状态机（规范 §4.1–§4.8）。

范围
----
§4.1 双层拆分、§4.2 状态集（Task 13 态 / Step 11 态）、§4.3 台账投影、
§4.4 Task 迁移表（T1–T24）、§4.5 Step 迁移表（S1–S15）、
§4.6 失败分类与签名、§4.7 重试与确认策略、§4.8 派发后不得重放。

范围外（本包不做）
------------------
§4.9 落账写库、§5 事件发送、§9 Verifier、effect_log 持久化、§4.10 评测集落地。
本包也不实现任何 Worker 适配器、不调度、不读时钟。

边界纪律
--------
- 零业务 import：只 import 标准库，不 import 任何 server 业务模块；
- 零 IO：不读文件、不开 socket、不连库；
- 零打印 / 零日志 / 零事件发送：事件名只是 TransitionRule.events 里的字符串描述符。
"""
from __future__ import annotations

from .errors import GuardRejected, IllegalTransition, TransitionError
from .failures import (
    DEFAULT_DESTINATION,
    FAILURE_CLASSES,
    RETRYABLE,
    Failure,
    build_signature,
    classify,
    destination_for,
    normalize_error_text,
)
from .machine import (
    STEP_RULES,
    TASK_RULES,
    StepMachine,
    StepState,
    TaskMachine,
    TaskState,
    TransitionResult,
    TransitionRule,
)
from .model import SideEffectDecl, StepFacts, TaskFacts
from .paths import path_in_contract_scope
from .projection import LedgerProjection, blocked_kind_of, project_task
from .replay import (
    REPLAY_REPLAN,
    REPLAY_SAME_KEY,
    REPLAY_UNKNOWN,
    DispatchEvidence,
    replay_decision,
    requires_reconciliation,
    unknown_exits,
)
from .retry import (
    START_POLICIES,
    RetryDecision,
    can_auto_start,
    step_retry_decision,
    task_retry_decision,
)
from .states import (
    BLOCKED_REASONS,
    LEDGER_STATUSES,
    RESUME_TARGETS,
    STEP_STATES,
    STEP_TERMINAL,
    TASK_STATES,
    TASK_TERMINAL,
    StateValueError,
    is_step_terminal,
    is_task_terminal,
    require_step_state,
    require_task_state,
)

__all__ = [
    # states (§4.2)
    "TASK_STATES", "TASK_TERMINAL", "STEP_STATES", "STEP_TERMINAL",
    "BLOCKED_REASONS", "RESUME_TARGETS", "LEDGER_STATUSES",
    "StateValueError", "require_task_state", "require_step_state",
    "is_task_terminal", "is_step_terminal",
    # errors
    "TransitionError", "IllegalTransition", "GuardRejected",
    # paths
    "path_in_contract_scope",
    # model
    "SideEffectDecl", "TaskFacts", "StepFacts",
    # machine (§4.4 / §4.5)
    "TaskState", "StepState", "TransitionResult", "TransitionRule",
    "TASK_RULES", "STEP_RULES", "TaskMachine", "StepMachine",
    # failures (§4.6)
    "FAILURE_CLASSES", "RETRYABLE", "DEFAULT_DESTINATION", "Failure",
    "normalize_error_text", "build_signature", "classify", "destination_for",
    # retry (§4.7)
    "START_POLICIES", "RetryDecision", "task_retry_decision",
    "step_retry_decision", "can_auto_start",
    # replay (§4.8)
    "REPLAY_REPLAN", "REPLAY_SAME_KEY", "REPLAY_UNKNOWN", "DispatchEvidence",
    "replay_decision", "unknown_exits", "requires_reconciliation",
    # projection (§4.3)
    "LedgerProjection", "blocked_kind_of", "project_task",
]
