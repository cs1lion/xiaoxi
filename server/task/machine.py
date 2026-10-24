"""Task / Step 双层状态机（规范 §4.4 / §4.5）。

两层各自独立：
- Task 级管一份合同的生命周期（§4.2 的 13 态）；
- Step 级管 DAG 节点的生命周期（§4.2 的 11 态），一个 Task 下可多个 Step 并行。

状态对象全是 frozen dataclass，transition / attempt 从不修改入参：被拒绝时
调用方拿到的还是原来那个对象，不存在「拒了一半」的中间态。

事件名只作为字符串描述符放在 TransitionRule.events 里，本模块不发事件。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from .errors import GuardRejected, IllegalTransition
from .model import StepFacts, TaskFacts
from .paths import path_in_contract_scope
from .retry import step_retry_decision, task_retry_decision
from .states import (
    RESUME_TARGETS,
    STEP_STATES,
    STEP_TERMINAL,
    TASK_STATES,
    TASK_TERMINAL,
    is_step_terminal,
)


@dataclass(frozen=True)
class TaskState:
    """Task 的不可变状态。"""

    status: str = "CLARIFYING"
    attempt: int = 0
    last_failure_signature: str = ""
    blocked_reason: str = ""
    resume_to: str = ""
    confirmed_by: str = ""
    closed_reason: str = ""
    trail: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepState:
    """Step 的不可变状态。"""

    status: str = "CREATED"
    retries: int = 0
    last_failure_signature: str = ""
    trail: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransitionResult:
    """一次成功迁移的结果：规则、来源、展开后的目标、要发的事件、新状态。"""

    rule_id: str
    source: str
    target: str
    events: tuple[str, ...]
    state: object


@dataclass(frozen=True)
class TransitionRule:
    """迁移表的一行。guard 返回空串表示通过，非空即拒绝原因。"""

    rule_id: str
    source: str
    target: str
    trigger: str
    guard_text: str
    events: tuple[str, ...]
    guard: Callable[[Any, Any], str]
    patch: Callable[[Any, Any], dict]


def _missing_permissions(required: tuple[str, ...], granted: tuple[str, ...]) -> list[str]:
    granted_set = set(granted or ())
    return [item for item in (required or ()) if item not in granted_set]


def _patch_none(state: Any, facts: Any) -> dict:
    return {}


# ---------- Task 守卫（T1–T24） ----------

def _t1_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.rounds_used >= facts.max_rounds:
        return f"追问轮数 {facts.rounds_used} 已达 max_rounds {facts.max_rounds}"
    if not facts.gaps_changed:
        return "缺口集合没有变化：重复追问同一件事没有意义"
    return ""


def _t2_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.gate_ready:
        return ""
    if facts.conservative_filled and facts.gap_slots_all_highlighted:
        return ""
    return "GateResult 不是 ready，且没有用保守默认值补齐并全部高亮"


def _t4_guard(state: TaskState, facts: TaskFacts) -> str:
    if not facts.contract_version_incremented:
        return "契约 version 没有递增（修改契约必须先升版）"
    return ""


def _t5_guard(state: TaskState, facts: TaskFacts) -> str:
    if not facts.contract_id_matches:
        return "contract_id 与当前不一致"
    if not facts.contract_version_matches:
        return "确认的 version 与当前不一致"
    if facts.risk in ("medium", "high"):
        confirmed = set(facts.confirmed_slots or ())
        missing = [slot for slot in (facts.highlighted_slots or ()) if slot not in confirmed]
        if missing:
            return (
                f"risk={facts.risk} 还有 {len(missing)} 个高亮槽位未确认："
                f"{', '.join(missing)}"
            )
    return ""


def _t6_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.risk != "low":
        return f"倒计时自动开工只允许 risk=low，实际 {facts.risk}"
    effects = tuple(facts.side_effects or ())
    bad = [effect.type for effect in effects if not effect.is_local_reversible]
    if bad:
        return f"存在不可逆或非本地的副作用：{', '.join(bad)}"
    highlighted = tuple(facts.highlighted_slots or ())
    if highlighted:
        return f"还有 {len(highlighted)} 个高亮槽位未确认"
    if not facts.start_policy_approved:
        return "确认门策略未批准（§4.7 冲突项未拍板）"
    return ""


def _t8_guard(state: TaskState, facts: TaskFacts) -> str:
    missing = _missing_permissions(facts.permissions_required, facts.permissions_granted)
    if missing:
        return f"缺少权限：{', '.join(missing)}"
    if not facts.worker_available:
        return "没有可用的 Worker"
    if facts.budget_exhausted:
        return "预算已耗尽"
    if not facts.frozen_version_current:
        return "契约 version 已不是冻结版"
    return ""


def _t9_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.blocked_reason not in ("need_authorization", "need_approval"):
        return (
            "SPEC_LOCKED 的阻塞只能是 need_authorization / need_approval，"
            f"实际 {facts.blocked_reason or '（空）'}"
        )
    if facts.resume_to != "SPEC_LOCKED":
        return f"resume_to 必须是 SPEC_LOCKED，实际 {facts.resume_to or '（空）'}"
    return ""


def _t10_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.dispatch_receipts <= 0:
        return "没有任何 dispatch_receipt（只证明发出去了，还不能说在跑）"
    return ""


def _t11_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.dispatch_receipts != 0:
        return f"已有 {facts.dispatch_receipts} 条回执，不走 ambiguous_result"
    if not facts.ack_timed_out:
        return "未超过 dispatch_ack_timeout"
    return ""


def _t12_guard(state: TaskState, facts: TaskFacts) -> str:
    states = tuple(facts.step_states or ())
    unresolved = [item for item in states if item == "UNKNOWN"]
    if unresolved:
        return f"unresolved_steps：{len(unresolved)} 个 Step 处于 UNKNOWN"
    open_states = [item for item in states if not is_step_terminal(item)]
    if open_states:
        return f"还有 {len(open_states)} 个 Step 未到终态：{', '.join(open_states)}"
    effects = tuple(facts.undeclared_effects or ())
    if effects:
        return f"发现 {len(effects)} 个未声明的副作用：{', '.join(effects)}"
    return ""


def _t13_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.blocked_reason not in ("retry_exhausted", "need_authorization"):
        return (
            "EXECUTING 的阻塞原因只能是 retry_exhausted / need_authorization，"
            f"实际 {facts.blocked_reason or '（空）'}"
        )
    return ""


def _t14_guard(state: TaskState, facts: TaskFacts) -> str:
    if tuple(facts.undeclared_effects or ()) or facts.budget_hard_exhausted:
        return ""
    return "没有未声明副作用，预算也未硬耗尽"


def _t15_guard(state: TaskState, facts: TaskFacts) -> str:
    if not facts.inflight_reconciled:
        return "还有在飞副作用未对账"
    return ""


def _t16_decision(state: TaskState, facts: TaskFacts):
    return task_retry_decision(
        attempt=state.attempt,
        max_retries=facts.max_retries,
        failure_signature=facts.failure_signature,
        last_failure_signature=state.last_failure_signature,
        has_failed_required=facts.failed_required,
        human_required_count=facts.human_required_count,
    )


def _t16_guard(state: TaskState, facts: TaskFacts) -> str:
    decision = _t16_decision(state, facts)
    if decision.action != "retry":
        return decision.reason
    return ""


def _t17_reason(state: TaskState, facts: TaskFacts) -> str:
    """T17 的 blocked_reason；还能自动重试时返回空串（即 T17 不成立）。

    规范 §4.4（6b23691 修订）：存在 kind=human 的 required 标准 ->
    need_clarification；重试耗尽或签名重复 -> retry_exhausted；
    两者并存取 need_clarification（要人来判，比「再试也没用」更具体）。
    guard 与 patch 共用这一个判据，避免两处判断漂移。
    """
    if _t16_decision(state, facts).action == "retry":
        return ""
    if facts.human_required_count > 0:
        return "need_clarification"
    return "retry_exhausted"


def _t17_guard(state: TaskState, facts: TaskFacts) -> str:
    if _t17_reason(state, facts):
        return ""
    # 还能自动重试：拒绝理由复用 T16 的判定文字（含 attempt / max_retries 数值）。
    return _t16_decision(state, facts).reason


def _t18_guard(state: TaskState, facts: TaskFacts) -> str:
    if not facts.all_required_passed:
        return "还有 required 标准未通过"
    if facts.semantic_without_evidence > 0:
        return f"{facts.semantic_without_evidence} 条 semantic 标准没有证据"
    if facts.human_required_count > 0:
        return f"有 {facts.human_required_count} 条 human 标准必须人工判定（§4.10 SM-17）"
    return ""


def _t19_guard(state: TaskState, facts: TaskFacts) -> str:
    effects = tuple(facts.side_effects or ())
    if facts.approve_side_effect:
        return ""
    if facts.risk == "low" and not effects:
        return ""
    return f"risk={facts.risk} 且声明了 {len(effects)} 个副作用，必须 APPROVE_SIDE_EFFECT"


def _t20_guard(state: TaskState, facts: TaskFacts) -> str:
    """T20：每条声明的副作用，在 effect_log 里**恰好一条 status=done**。

    三种失败要能分开报，否则排查时分不出是哪一种：
      1. 声明了但记录里根本没这个键        -> 0 条记录
      2. 有记录但一条 done 都没有           -> N 条记录 / 0 条成功
      3. 重复 done                          -> M 条成功
    """
    records = tuple(facts.effect_records or ())
    for key in facts.declared_effect_keys or ():
        matched = [record for record in records if record.idempotency_key == key]
        succeeded = [record for record in matched if record.status == "done"]
        if not matched:
            return (
                f"idempotency_key {key!r} 在 effect_log 里没有任何记录"
                f"（声明了这个副作用但没执行过）"
            )
        if not succeeded:
            statuses = ", ".join(record.status for record in matched)
            return (
                f"idempotency_key {key!r} 有 {len(matched)} 条记录但没有成功的："
                f"status = {statuses}（需要恰好 1 条 done）"
            )
        if len(succeeded) != 1:
            return (
                f"idempotency_key {key!r} 有 {len(succeeded)} 条成功记录（应为 1，疑似重复执行）"
            )
    return ""


def _t21_guard(state: TaskState, facts: TaskFacts) -> str:
    if facts.blocked_reason != "need_approval":
        return f"DELIVERING 的阻塞原因只能是 need_approval，实际 {facts.blocked_reason or '（空）'}"
    return ""


def _t22_guard(state: TaskState, facts: TaskFacts) -> str:
    if (facts.answer_text or "").strip():
        return ""
    authorized = (
        bool(facts.authorization_effect_id)
        and facts.authorization_effect_id == facts.blocked_effect_id
    )
    if authorized:
        return ""
    return "没有回答，也没有命中阻塞时的 side_effect.id 的授权"


def _t24_guard(state: TaskState, facts: TaskFacts) -> str:
    if not facts.inflight_reconciled:
        return "还有在飞副作用未对账"
    if facts.open_steps_remaining != 0:
        return f"还有 {facts.open_steps_remaining} 个 Step 未置 CANCELLED"
    return ""


# ---------- Task patch ----------

def _patch_t9(state: TaskState, facts: TaskFacts) -> dict:
    return {"blocked_reason": facts.blocked_reason, "resume_to": facts.resume_to}


def _patch_t11(state: TaskState, facts: TaskFacts) -> dict:
    return {"blocked_reason": "ambiguous_result", "resume_to": "DISPATCHED"}


def _patch_t13(state: TaskState, facts: TaskFacts) -> dict:
    # blocked_reason 必须带上：守卫已限定它 ∈ {retry_exhausted, need_authorization}，
    # 不带的话 BLOCKED 会落成空 reason（§4.2 非法组合），台账投影也拿不到
    # blocked_kind。2026-10-24 收口轮由「reason 产生者」回归锁抓出。
    return {"blocked_reason": facts.blocked_reason, "resume_to": "EXECUTING"}


def _patch_t5(state: TaskState, facts: TaskFacts) -> dict:
    return {"confirmed_by": "user"}


def _patch_t6(state: TaskState, facts: TaskFacts) -> dict:
    return {"confirmed_by": "countdown"}


def _patch_t16(state: TaskState, facts: TaskFacts) -> dict:
    return {
        "attempt": state.attempt + 1,
        "last_failure_signature": facts.failure_signature,
    }


def _patch_t17(state: TaskState, facts: TaskFacts) -> dict:
    return {"blocked_reason": _t17_reason(state, facts), "resume_to": "VERIFYING"}


def _patch_t21(state: TaskState, facts: TaskFacts) -> dict:
    return {"blocked_reason": "need_approval", "resume_to": "DELIVERING"}


def _clear_block(state: TaskState, facts: TaskFacts) -> dict:
    return {"blocked_reason": "", "resume_to": ""}


def _patch_t23(state: TaskState, facts: TaskFacts) -> dict:
    return {"closed_reason": "cancelled", "blocked_reason": "", "resume_to": ""}


def _patch_t24(state: TaskState, facts: TaskFacts) -> dict:
    return {"closed_reason": "cancelled", "blocked_reason": "", "resume_to": ""}


# ---------- Step 守卫（S1–S15） ----------

def _s1_guard(state: StepState, facts: StepFacts) -> str:
    deps = tuple(facts.dep_states or ())
    bad = [item for item in deps if item != "VERIFIED"]
    if bad:
        return f"依赖未全部 VERIFIED：{', '.join(bad)}"
    return ""


def _s2_guard(state: StepState, facts: StepFacts) -> str:
    if facts.task_terminal:
        return "Task 已进入终态，Step 应走取消而不是跳过"
    deps = tuple(facts.dep_states or ())
    blocked = [item for item in deps if item in ("FAILED", "SKIPPED")]
    if blocked or not facts.condition_holds:
        return ""
    return "依赖都正常且条件成立，没有跳过理由"


def _s3_guard(state: StepState, facts: StepFacts) -> str:
    missing = _missing_permissions(facts.permissions_required, facts.permissions_granted)
    if missing:
        return f"缺少权限：{', '.join(missing)}"
    if facts.concurrency >= facts.max_concurrent:
        return f"并发 {facts.concurrency} 已达 max_concurrent {facts.max_concurrent}"
    return ""


def _s4_guard(state: StepState, facts: StepFacts) -> str:
    if not (facts.ack_receipt or "").strip():
        return "没有执行回执（pid / 首个 progress / HTTP 202 都没有）"
    return ""


def _s5_guard(state: StepState, facts: StepFacts) -> str:
    if not facts.dispatch_recorded:
        return "没有 dispatch_receipt，确定没发出去，走重派而不是 UNKNOWN"
    if not facts.ack_timed_out:
        return "未超过 dispatch_ack_timeout"
    return ""


def _s6_guard(state: StepState, facts: StepFacts) -> str:
    path = (facts.artifact_path or "").strip()
    if not path:
        return "没有产出物路径"
    return path_in_contract_scope(path)


def _s7_guard(state: StepState, facts: StepFacts) -> str:
    if not facts.error_retryable:
        return ""
    if state.retries >= facts.max_step_retries:
        return ""
    return (
        f"错误可重试且重试次数 {state.retries} < "
        f"max_step_retries {facts.max_step_retries}"
    )


def _s8_guard(state: StepState, facts: StepFacts) -> str:
    if not facts.dispatch_recorded:
        return "没有 dispatch_receipt"
    if facts.wall_elapsed_s > facts.max_wall_s or facts.connection_lost:
        return ""
    return "未超时且连接未断"


def _s9_guard(state: StepState, facts: StepFacts) -> str:
    results = tuple(facts.criterion_results or ())
    if not results:
        return "没有断言结果"
    failed = [item for item in results if not item]
    if failed:
        return f"还有 {len(failed)} 条断言未通过"
    return ""


def _s10_guard(state: StepState, facts: StepFacts) -> str:
    results = tuple(facts.criterion_results or ())
    if not results:
        return "没有断言结果"
    if all(results):
        return "全部断言都通过了，不应打回"
    return ""


def _s11_decision(state: StepState, facts: StepFacts):
    return step_retry_decision(
        retries=state.retries,
        max_step_retries=facts.max_step_retries,
        failure_signature=facts.failure_signature,
        last_failure_signature=state.last_failure_signature,
        retryable=facts.error_retryable,
    )


def _s11_guard(state: StepState, facts: StepFacts) -> str:
    decision = _s11_decision(state, facts)
    if decision.action != "retry":
        return decision.reason
    return ""


def _s12_guard(state: StepState, facts: StepFacts) -> str:
    decision = _s11_decision(state, facts)
    if decision.action == "retry":
        return decision.reason
    return ""


def _s13_guard(state: StepState, facts: StepFacts) -> str:
    if not (facts.reconciliation_evidence or "").strip():
        return "没有对账证据"
    if not facts.reconciled_executed:
        return "对账结论是未执行，不能当已执行"
    return ""


def _s14_guard(state: StepState, facts: StepFacts) -> str:
    if not (facts.reconciliation_evidence or "").strip():
        return "没有对账证据（「查过，没执行」才算）"
    if facts.reconciled_executed:
        return "对账结论是已执行，不能重派"
    return ""


def _s15_guard(state: StepState, facts: StepFacts) -> str:
    if not facts.worker_cancel_returned:
        return "Worker 的 cancel() 还没返回"
    return ""


def _patch_s11(state: StepState, facts: StepFacts) -> dict:
    return {
        "retries": state.retries + 1,
        "last_failure_signature": facts.failure_signature,
    }


# ---------- 规则表 ----------

def _task_rule(
    rule_id: str,
    source: str,
    target: str,
    trigger: str,
    guard_text: str,
    events: tuple[str, ...],
    guard: Callable[[TaskState, TaskFacts], str],
    patch: Callable[[TaskState, TaskFacts], dict] = _patch_none,
) -> TransitionRule:
    return TransitionRule(rule_id, source, target, trigger, guard_text, events, guard, patch)


def _step_rule(
    rule_id: str,
    source: str,
    target: str,
    trigger: str,
    guard_text: str,
    events: tuple[str, ...],
    guard: Callable[[StepState, StepFacts], str],
    patch: Callable[[StepState, StepFacts], dict] = _patch_none,
) -> TransitionRule:
    return TransitionRule(rule_id, source, target, trigger, guard_text, events, guard, patch)


TASK_RULES: tuple[TransitionRule, ...] = (
    _task_rule("T1", "CLARIFYING", "CLARIFYING", "用户回答，仍有缺口",
               "rounds_used < max_rounds 且 gaps_changed", ("ASK_USER",), _t1_guard),
    _task_rule("T2", "CLARIFYING", "PROPOSED", "缺口清零，或轮数耗尽",
               "gate_ready 或（conservative_filled 且 gap_slots_all_highlighted）",
               ("CONTRACT_PROPOSED",), _t2_guard),
    _task_rule("T3", "CLARIFYING", "CANCELLED", "用户放弃 / 静默超时",
               "无", ("TASK_STATE_CHANGE",), lambda s, f: ""),
    _task_rule("T4", "PROPOSED", "CLARIFYING", "用户提出修改，产生新缺口",
               "contract_version_incremented", ("TASK_STATE_CHANGE",), _t4_guard),
    _task_rule("T5", "PROPOSED", "SPEC_LOCKED", "CONFIRM_CONTRACT",
               "contract_id/version 与当前一致；medium/high 时 confirmed_slots ⊇ highlighted_slots",
               ("TASK_STATE_CHANGE",), _t5_guard, _patch_t5),
    _task_rule("T6", "PROPOSED", "SPEC_LOCKED", "契约卡倒计时结束",
               "risk=low 且副作用全部本地可逆且无高亮且 start_policy_approved",
               ("TASK_STATE_CHANGE",), _t6_guard, _patch_t6),
    _task_rule("T7", "PROPOSED", "CANCELLED", "卡片超时未确认 / 用户取消",
               "无", ("TASK_STATE_CHANGE",), lambda s, f: ""),
    _task_rule("T8", "SPEC_LOCKED", "DISPATCHED", "派单",
               "权限齐备、Worker 可用、预算未耗尽、契约版本仍是冻结版",
               ("STEP_STATE_CHANGE",), _t8_guard),
    _task_rule("T9", "SPEC_LOCKED", "BLOCKED", "无可用适配器 / 无容量",
               "blocked_reason ∈ {need_authorization, need_approval} 且 resume_to=SPEC_LOCKED",
               ("ASK_USER",), _t9_guard, _patch_t9),
    _task_rule("T10", "DISPATCHED", "EXECUTING", "任一 Step 进入 RUNNING",
               "dispatch_receipts > 0", ("TASK_STATE_CHANGE",), _t10_guard),
    _task_rule("T11", "DISPATCHED", "BLOCKED", "全部 Step 无回执且超过 dispatch_ack_timeout",
               "dispatch_receipts == 0 且 ack_timed_out",
               ("ASK_USER",), _t11_guard, _patch_t11),
    _task_rule("T12", "EXECUTING", "VERIFYING", "全部 Step 终态且已交产出物",
               "step_states 全终态、无 UNKNOWN/DISPATCHED、undeclared_effects 为空",
               ("VERIFICATION_REPORT",), _t12_guard),
    _task_rule("T13", "EXECUTING", "BLOCKED", "Step 失败且重试耗尽 / 熔断 / 需授权",
               "blocked_reason ∈ {retry_exhausted, need_authorization}",
               ("ASK_USER",), _t13_guard, _patch_t13),
    _task_rule("T14", "EXECUTING", "FAILED", "契约违规",
               "undeclared_effects 非空 或 budget_hard_exhausted",
               ("TASK_STATE_CHANGE",), _t14_guard),
    _task_rule("T15", "EXECUTING", "TIMEOUT", "墙钟超限",
               "inflight_reconciled", ("TASK_STATE_CHANGE",), _t15_guard),
    _task_rule("T16", "VERIFYING", "EXECUTING", "存在 required 标准未过且可自动重试",
               "task_retry_decision(...).action == retry",
               ("TASK_STATE_CHANGE",), _t16_guard, _patch_t16),
    _task_rule("T17", "VERIFYING", "BLOCKED", "同上但不可自动重试",
               "task_retry_decision(...).action == block；human -> need_clarification，"
               "否则 retry_exhausted；resume_to=VERIFYING",
               ("ASK_USER",), _t17_guard, _patch_t17),
    _task_rule("T18", "VERIFYING", "ACCEPTED", "验收全过",
               "all_required_passed 且 semantic_without_evidence=0 且 human_required_count=0",
               ("VERIFICATION_REPORT",), _t18_guard),
    _task_rule("T19", "ACCEPTED", "DELIVERING", "交付确认",
               "approve_side_effect 或（risk=low 且无副作用）",
               ("TASK_STATE_CHANGE",), _t19_guard),
    _task_rule("T20", "DELIVERING", "DELIVERED", "副作用全部完成",
               "每个 declared_effect_key 在 effect_log 里恰好一条 status=done（成功）",
               ("TASK_STATE_CHANGE",), _t20_guard),
    _task_rule("T21", "DELIVERING", "BLOCKED", "目标拒绝 / 需要重授权",
               "blocked_reason == need_approval",
               ("ASK_USER",), _t21_guard, _patch_t21),
    _task_rule("T22", "BLOCKED", "{{resume_to}}", "ANSWER / 授权 / 人工改判",
               "answer_text 非空 或 授权对象命中阻塞时的 side_effect.id",
               ("TASK_STATE_CHANGE",), _t22_guard, _clear_block),
    _task_rule("T23", "BLOCKED", "CANCELLED", "用户取消",
               "无", ("TASK_STATE_CHANGE",), lambda s, f: "", _patch_t23),
    _task_rule("T24", "*", "CANCELLED", "CANCEL_TASK",
               "inflight_reconciled 且 open_steps_remaining == 0",
               ("TASK_STATE_CHANGE",), _t24_guard, _patch_t24),
)


STEP_RULES: tuple[TransitionRule, ...] = (
    _step_rule("S1", "CREATED", "READY", "依赖全部 VERIFIED（或无依赖）",
               "dep_states 为空 或 全部 VERIFIED", ("STEP_STATE_CHANGE",), _s1_guard),
    _step_rule("S2", "CREATED", "SKIPPED", "任一依赖 FAILED/SKIPPED，或条件不成立",
               "非 task_terminal 且（依赖失败/跳过 或 not condition_holds）",
               ("STEP_STATE_CHANGE",), _s2_guard),
    _step_rule("S3", "READY", "DISPATCHED", "交给 Worker",
               "permissions_required ⊆ granted 且 concurrency < max_concurrent",
               ("STEP_STATE_CHANGE",), _s3_guard),
    _step_rule("S4", "DISPATCHED", "RUNNING", "收到执行回执",
               "ack_receipt 非空", ("STEP_STATE_CHANGE",), _s4_guard),
    _step_rule("S5", "DISPATCHED", "UNKNOWN", "超过 dispatch_ack_timeout 无回执",
               "dispatch_recorded 且 ack_timed_out", ("STEP_STATE_CHANGE",), _s5_guard),
    _step_rule("S6", "RUNNING", "OBSERVED", "Worker 交出产出物",
               "artifact_path 非空且在契约范围内",
               ("TOOL_EXECUTION",), _s6_guard),
    _step_rule("S7", "RUNNING", "FAILED", "执行报错",
               "not error_retryable 或 state.retries >= max_step_retries",
               ("TOOL_EXECUTION",), _s7_guard),
    _step_rule("S8", "RUNNING", "UNKNOWN", "超过 budget.max_wall_s 或连接中断",
               "dispatch_recorded 且（wall_elapsed_s > max_wall_s 或 connection_lost）",
               ("TOOL_EXECUTION",), _s8_guard),
    _step_rule("S9", "OBSERVED", "VERIFIED", "Step 级断言全过",
               "criterion_results 非空且全部 True",
               ("VERIFICATION_REPORT",), _s9_guard),
    _step_rule("S10", "OBSERVED", "REJECTED", "断言未过",
               "criterion_results 非空且存在 False",
               ("VERIFICATION_REPORT",), _s10_guard),
    _step_rule("S11", "REJECTED", "READY", "重做",
               "step_retry_decision(...).action == retry",
               ("STEP_STATE_CHANGE",), _s11_guard, _patch_s11),
    _step_rule("S12", "REJECTED", "FAILED", "不再重做",
               "step_retry_decision(...).action == block",
               ("STEP_STATE_CHANGE",), _s12_guard),
    _step_rule("S13", "UNKNOWN", "OBSERVED", "对账确认已产生产出物",
               "reconciliation_evidence 非空且 reconciled_executed",
               ("TOOL_EXECUTION",), _s13_guard),
    _step_rule("S14", "UNKNOWN", "READY", "对账确认未执行",
               "reconciliation_evidence 非空且 not reconciled_executed",
               ("STEP_STATE_CHANGE",), _s14_guard),
    _step_rule("S15", "*", "CANCELLED", "Task 取消 / 熔断",
               "worker_cancel_returned", ("STEP_STATE_CHANGE",), _s15_guard),
)


RESUME_PLACEHOLDER = "{{resume_to}}"


class _MachineBase:
    """TaskMachine / StepMachine 的共同骨架；子类只给规则表与状态集。"""

    RULES: tuple[TransitionRule, ...] = ()
    STATES: tuple[str, ...] = ()
    TERMINAL: frozenset[str] = frozenset()

    @classmethod
    def _rules_from(cls, status: str) -> tuple[TransitionRule, ...]:
        if not isinstance(status, str) or status not in cls.STATES:
            return ()
        if status in cls.TERMINAL:
            return ()
        return tuple(
            rule for rule in cls.RULES
            if rule.source == status or rule.source == "*"
        )

    @classmethod
    def rules_from(cls, status: str) -> tuple[TransitionRule, ...]:
        """该状态下可用的规则（含 * 通配）；终态返回空。"""
        return cls._rules_from(status)

    @classmethod
    def _applies(cls, rule: TransitionRule, status: str) -> bool:
        if status in cls.TERMINAL:
            return False
        return rule.source == status or rule.source == "*"

    @classmethod
    def _expand(cls, rule: TransitionRule, state: Any) -> str:
        if rule.target == RESUME_PLACEHOLDER:
            return state.resume_to
        return rule.target

    @classmethod
    def _allowed(cls, status: str) -> tuple[str, ...]:
        targets: list[str] = []
        for rule in cls._rules_from(status):
            candidates = RESUME_TARGETS if rule.target == RESUME_PLACEHOLDER else (rule.target,)
            for item in candidates:
                if item and item not in targets:
                    targets.append(item)
        return tuple(targets)

    @classmethod
    def _matches(cls, rule: TransitionRule, state: Any, target: str) -> bool:
        # {{resume_to}} 的展开值域是 RESUME_TARGETS。edges() 是静态表，
        # 因此这里按值域匹配，保证「表里有的边绝不抛 IllegalTransition」；
        # 与具体 resume_to 冲突时按守卫拒绝处理（见 _apply）。
        if rule.target == RESUME_PLACEHOLDER:
            return target in RESUME_TARGETS
        return target == rule.target

    @classmethod
    def _apply(
        cls,
        rule: TransitionRule,
        state: Any,
        facts: Any,
        requested: str = "",
    ) -> TransitionResult:
        target = cls._expand(rule, state)
        if rule.target == RESUME_PLACEHOLDER and target and target not in RESUME_TARGETS:
            # §4.2：resume_to 的值域就是 RESUME_TARGETS，「必须显式给出」。
            # 展开到值域外的目标（如 CLARIFYING——它是合法 Task 状态，但不是
            # 合法的恢复目标）属于非法状态组合：state 本身坏了，迁过去等于
            # 把坏状态洗白。空串不算坏值，走下面的补位 / 不可用分支。
            raise GuardRejected(
                state.status,
                target,
                (rule.rule_id,),
                (f"resume_to {target!r} 不在 {RESUME_TARGETS} 里（§4.2 非法状态组合）",),
            )
        if requested in RESUME_TARGETS:
            if target and target != requested:
                raise GuardRejected(
                    state.status,
                    requested,
                    (rule.rule_id,),
                    (f"resume_to 是 {target}，不能回到 {requested}",),
                )
            # resume_to 为空时，由 attempt 点名的目标补位。
            target = requested
        if not target or target not in cls.STATES:
            raise GuardRejected(
                state.status,
                target,
                (rule.rule_id,),
                (f"目标状态不可用：resume_to={state.resume_to!r}",),
            )
        patch = rule.patch(state, facts) or {}
        new_state = replace(
            state,
            status=target,
            trail=tuple(state.trail) + (rule.rule_id,),
            **patch,
        )
        return TransitionResult(rule.rule_id, state.status, target, rule.events, new_state)

    @classmethod
    def transition(cls, state: Any, rule_id: str, facts: Any) -> TransitionResult:
        """按规则 id 迁移；规则不存在 / 来源不符 / 守卫拒绝都抛 TransitionError。"""
        rule = next((item for item in cls.RULES if item.rule_id == rule_id), None)
        if rule is None:
            raise IllegalTransition(state.status, "", ())
        if not cls._applies(rule, state.status):
            raise IllegalTransition(state.status, cls._expand(rule, state), cls._allowed(state.status))
        reason = rule.guard(state, facts)
        if reason:
            raise GuardRejected(
                state.status, cls._expand(rule, state), (rule.rule_id,), (reason,)
            )
        return cls._apply(rule, state, facts)

    @classmethod
    def attempt(cls, state: Any, target: str, facts: Any) -> TransitionResult:
        """按目标尝试迁移：同目标的多条规则按表序取首个通过者。"""
        candidates = tuple(
            rule for rule in cls._rules_from(state.status)
            if cls._matches(rule, state, target)
        )
        if not candidates:
            raise IllegalTransition(state.status, target, cls._allowed(state.status))
        reasons: list[str] = []
        for rule in candidates:
            reason = rule.guard(state, facts)
            if not reason:
                return cls._apply(rule, state, facts, requested=target)
            reasons.append(reason)
        raise GuardRejected(
            state.status,
            target,
            tuple(rule.rule_id for rule in candidates),
            tuple(reasons),
        )

    @classmethod
    def allows(cls, source: str, target: str) -> bool:
        """(source, target) 是否在迁移表里；终态一律 False。"""
        if not isinstance(source, str) or source not in cls.STATES:
            return False
        if source in cls.TERMINAL:
            return False
        return target in cls._allowed(source)

    @classmethod
    def edges(cls) -> tuple[tuple[str, str], ...]:
        """全部 (from, to)；* 展开到非终态源，{{resume_to}} 展开到每个可恢复目标。"""
        result: list[tuple[str, str]] = []
        for rule in cls.RULES:
            if rule.source == "*":
                sources = tuple(item for item in cls.STATES if item not in cls.TERMINAL)
            else:
                sources = (rule.source,)
            targets = RESUME_TARGETS if rule.target == RESUME_PLACEHOLDER else (rule.target,)
            for source in sources:
                for target in targets:
                    pair = (source, target)
                    if pair not in result:
                        result.append(pair)
        return tuple(result)


class TaskMachine(_MachineBase):
    """Task 级状态机。"""

    RULES = TASK_RULES
    STATES = TASK_STATES
    TERMINAL = TASK_TERMINAL


class StepMachine(_MachineBase):
    """Step 级状态机。"""

    RULES = STEP_RULES
    STATES = STEP_STATES
    TERMINAL = STEP_TERMINAL
