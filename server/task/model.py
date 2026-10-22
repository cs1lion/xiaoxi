"""Task / Step 状态机的数据结构（规范 §4.2–§4.8）。

两个事实包（TaskFacts / StepFacts）是守卫的唯一输入：守卫只看事实，
不看全局变量、不读文件、不碰时钟，所以同一条迁移可以用同一条 facts 重放。

SideEffectDecl 与 server/contract/model.py 的 SideEffect 保持同一组字段语义，
但本包不 import 契约层，方便单独审查「副作用是否可逆」。

全部 dataclass 都是 frozen：被拒的迁移在结构上不可能改到原 state。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _flag(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


@dataclass(frozen=True)
class SideEffectDecl:
    """契约里声明的一条副作用。

    id 是契约内的稳定标识，target 是动作对象，target_class 区分
    本地 / 自身 / 第三方；idempotency_key 用于 §4.4 T20 的「恰好一次」核对。
    """

    id: str
    type: str
    target: str = ""
    target_class: str = "local"
    reversible: bool = False
    idempotency_key: str = ""

    @staticmethod
    def from_mapping(raw: dict) -> "SideEffectDecl":
        """从契约 JSON 的一段映射构造；缺 id / type 视为契约结构错误。"""
        if not isinstance(raw, dict):
            raise ValueError(f"副作用声明必须是对象，实际 {type(raw).__name__}")
        effect_id = _text(raw.get("id"))
        effect_type = _text(raw.get("type"))
        if not effect_id:
            raise ValueError("副作用声明缺少 id")
        if not effect_type:
            raise ValueError(f"副作用 {effect_id!r} 缺少 type")
        return SideEffectDecl(
            id=effect_id,
            type=effect_type,
            target=_text(raw.get("target")),
            target_class=_text(raw.get("target_class")) or "local",
            reversible=_flag(raw.get("reversible")),
            idempotency_key=_text(raw.get("idempotency_key")),
        )

    @property
    def is_local_reversible(self) -> bool:
        """只有「可逆且本地」的副作用才允许倒计时自动开工（§4.7 冲突项 b）。"""
        return self.reversible and self.target_class == "local"


@dataclass(frozen=True)
class TaskFacts:
    """Task 级迁移的全部事实输入（每个字段都有默认值）。"""

    rounds_used: int = 0
    max_rounds: int = 3
    gaps_changed: bool = False
    gate_ready: bool = False
    conservative_filled: bool = False
    gap_slots_all_highlighted: bool = False
    contract_version_incremented: bool = False
    contract_id_matches: bool = True
    contract_version_matches: bool = True
    risk: str = "low"
    confirmed_slots: tuple[str, ...] = ()
    highlighted_slots: tuple[str, ...] = ()
    side_effects: tuple[SideEffectDecl, ...] = ()
    start_policy_approved: bool = False
    permissions_required: tuple[str, ...] = ()
    permissions_granted: tuple[str, ...] = ()
    worker_available: bool = True
    budget_exhausted: bool = False
    frozen_version_current: bool = True
    blocked_reason: str = ""
    resume_to: str = ""
    dispatch_receipts: int = 0
    step_states: tuple[str, ...] = ()
    ack_timed_out: bool = False
    undeclared_effects: tuple[str, ...] = ()
    budget_hard_exhausted: bool = False
    inflight_reconciled: bool = True
    failed_required: bool = False
    max_retries: int = 3
    failure_signature: str = ""
    all_required_passed: bool = False
    semantic_without_evidence: int = 0
    human_required_count: int = 0
    approve_side_effect: bool = False
    declared_effect_keys: tuple[str, ...] = ()
    effect_records: tuple[dict[str, Any], ...] = ()
    answer_text: str = ""
    authorization_effect_id: str = ""
    blocked_effect_id: str = ""
    open_steps_remaining: int = 0


@dataclass(frozen=True)
class StepFacts:
    """Step 级迁移的全部事实输入（每个字段都有默认值）。"""

    dep_states: tuple[str, ...] = ()
    condition_holds: bool = True
    task_terminal: bool = False
    permissions_required: tuple[str, ...] = ()
    permissions_granted: tuple[str, ...] = ()
    concurrency: int = 0
    max_concurrent: int = 4
    dispatch_recorded: bool = False
    ack_receipt: str = ""
    ack_timed_out: bool = False
    artifact_path: str = ""
    criterion_results: tuple[bool, ...] = ()
    max_step_retries: int = 2
    error_retryable: bool = True
    failure_signature: str = ""
    wall_elapsed_s: float = 0.0
    max_wall_s: float = 900.0
    connection_lost: bool = False
    worker_cancel_returned: bool = False
    reconciliation_evidence: str = ""
    reconciled_executed: bool = False
