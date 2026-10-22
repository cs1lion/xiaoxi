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


# §4.4 T20 / §5.2 DELIVERY_EFFECT 的 effect_log 记录。
# 一条记录 = 一次副作用尝试的结果。字段：
#   effect_id       本条记录自身的 id（§5.2 DELIVERY_EFFECT 载荷要它；APPROVE_SIDE_EFFECT
#                   命令也靠它定位副作用）。身份字段，无默认值。
#   idempotency_key 幂等键（契约层给，同一副作用永远同一键）
#   status          闭集 done | failed | replayed，只有 done 算成功
#   type / target_class  副作用类型与作用对象类别
#   at              epoch ms，服务端时钟
#
# 为什么**不**加 attempt / detail（2026-10-22 判断，条件已变，见下）：
#   attempt 仍无消费者：T20 不读它，且将来的「按 attempt 去重」会和「按 key 去重」打架。
#   detail 现在**有**消费者了——delivery.py 的失败原因需要落盘（DeliveryStep.error 只活在
#   内存里）。但落盘是第 8 步之后的事，届时一并加；现在加会先造一个没人持久化的字段。
#
# 为什么用 frozen dataclass 而不是 dict：status 是**闭集**。用 dict 时
# status="DONE" 或拼错成 "succes" 会静默落进「不是 done」的分支，T20 把它当
# 失败记录永远拒绝，而**没人知道是拼错了**——读不到就不报错、只给出错误结论，
# 正是这个包要避免的形状。所以非法值在**构造时**就 raise ValueError。
EFFECT_STATUSES: tuple[str, ...] = ("done", "failed", "replayed")
EFFECT_STATUS_SET = frozenset(EFFECT_STATUSES)


@dataclass(frozen=True)
class EffectRecord:
    """effect_log 的一条记录。构造即校验，坏值不给进守卫的机会。"""

    effect_id: str
    idempotency_key: str
    status: str
    type: str = ""
    target_class: str = ""
    at: int = 0

    def __post_init__(self) -> None:
        if not self.effect_id:
            raise ValueError("EffectRecord 缺少 effect_id")
        if not self.idempotency_key:
            raise ValueError("EffectRecord 缺少 idempotency_key")
        if self.status not in EFFECT_STATUS_SET:
            raise ValueError(
                f"EffectRecord.status 必须是 {EFFECT_STATUSES} 之一，实际 {self.status!r}"
            )

    @staticmethod
    def from_mapping(raw: Any) -> "EffectRecord":
        """从一条 dict（未来从 JSONL 读回）构造；缺字段与坏 status 都抛 ValueError。"""
        if not isinstance(raw, dict):
            raise ValueError(f"effect_log 记录必须是对象，实际 {type(raw).__name__}")
        return EffectRecord(
            effect_id=_text(raw.get("effect_id")),
            idempotency_key=_text(raw.get("idempotency_key")),
            status=_text(raw.get("status")),
            type=_text(raw.get("type")),
            target_class=_text(raw.get("target_class")),
            at=int(raw.get("at") or 0),
        )


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
    effect_records: tuple[EffectRecord, ...] = ()
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
