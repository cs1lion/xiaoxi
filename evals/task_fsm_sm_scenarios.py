# -*- coding: utf-8 -*-
"""评测集 D 类：规范 §4.10 的 18 条迁移用例（SM-01–SM-18）编成可执行场景。

    $env:PYTHONIOENCODING="utf-8"
    & "D:/project/neurosama/.venv/Scripts/python.exe" evals/task_fsm_sm_scenarios.py

**这个文件是什么**
------------------
规范 `docs/PIXEL_WORKSHOP_SPEC_V2.md` §4.10（搜「SM-01」可见全表，本文写作时在 L750–767）
给了 18 条验收用例。`scripts/test_task_fsm.py` 的 [F] 节已逐条断言过它们，
但那是**逐边**形态：手搓一个 `TaskState`、喂一组 `TaskFacts`、断言单次迁移的结果。

本文件是**场景**形态。两者的区别只有一条，但是真区别：

    逐边：Given 是手搓的状态——`task("PROPOSED")` 想有就有。
    场景：Given 尽量由状态机自己产出来——从 CLARIFYING 经 T2 到 PROPOSED，
          从 DISPATCHED 经 T10 到 EXECUTING，从 VERIFYING 经 T18 到 ACCEPTED。

所以场景多守一层：**如果到达 Given 的那条迁移坏了，场景也红，而逐边断言不红。**
这不是重复覆盖，是覆盖「组合」而不只是「零件」。

**Given / When / Then 三段各变成了什么**
----------------------------------------
每条场景函数里三段分明，注释标出：

    Given  → 一串 transition 调用，把状态机开到规范描述的那个状态
    When   → 规范写的那个触发（一次 transition / 一次 deliver() / 一次 attempt）
    Then   → 断言终态、状态字段（resume_to / blocked_reason / attempt /
             confirmed_by / closed_reason）、事件名、以及被拒绝时的错误类型
             （IllegalTransition 与 GuardRejected 是两回事，断言必须区分）

**依赖注入执行器的场景**
------------------------
只有 **SM-11** 依赖注入执行器：它要跨 `server/task/delivery.py`（第 6 步产物），
用一个计数型假执行器验证「同一 idempotency_key 第二次不产生第二个副作用」。
其余 17 条只靠状态机自身的事实字段，不需要注入。

**SM-16 的「Worker cancel() 已返回」**：纯状态机层没有真 Worker，规范这一句
在本层落成 `worker_cancel_returned` 事实。场景据实标注这个映射，不冒充有真 Worker。

**没被静默跳过的**：18 条全部实现为可执行场景。若将来某条的 Given 需要本层
不具备的能力，按 AGENTS.md 纪律标「未验证」并写清缺什么，不删不跳过。

**与 run_evals.py 的关系**：本文件不被 `scripts/run_evals.py` 发现——那个 runner
扫 `evals/cases/*.yaml`，跑的是对话层用例（turns/prompt）。状态机场景是确定性
Python，没有 turns，形状不同。它是独立可跑的第五道门。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.task import (  # noqa: E402
    GuardRejected, IllegalTransition, StepFacts, StepMachine, StepState, TaskFacts,
    TaskMachine, TaskState, TransitionError,
)
from server.task.delivery import deliver  # noqa: E402
from server.task.delivery import EffectResult  # noqa: E402
from server.task.model import SideEffectDecl  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


# ---------- 场景夹具 ----------

def task(status: str, **kw) -> TaskState:
    return TaskState(status=status, **kw)


def step(status: str, **kw) -> StepState:
    return StepState(status=status, **kw)


def eff(effect_id: str = "e1", key: str = "k1", type_: str = "overwrite_file") -> SideEffectDecl:
    return SideEffectDecl(id=effect_id, type=type_, target="out/a.txt",
                          target_class="local", reversible=True,
                          idempotency_key=key)


def reach_proposed(**facts) -> TaskState:
    """Given 的标准入口：CLARIFYING --T2--> PROPOSED（缺口清零）。

    不手搓 PROPOSED——那样 T2 坏了这条场景照样绿。
    """
    clarifying = task("CLARIFYING")
    return TaskMachine.transition(clarifying, "T2", TaskFacts(gate_ready=True, **facts)).state


def reach_executing(step_states=("VERIFIED", "VERIFIED")) -> TaskState:
    """Given：SPEC_LOCKED --T8--> DISPATCHED --T10--> EXECUTING。"""
    locked = TaskMachine.transition(reach_proposed(), "T5", TaskFacts(risk="low")).state
    dispatched = TaskMachine.transition(locked, "T8", TaskFacts(
        permissions_required=("L0",), permissions_granted=("L0",),
        worker_available=True)).state
    return TaskMachine.transition(dispatched, "T10", TaskFacts(
        dispatch_receipts=1, step_states=step_states)).state


def reach_verifying() -> TaskState:
    """Given：EXECUTING --T12--> VERIFYING（全部 Step 终态、无未声明副作用）。"""
    return TaskMachine.transition(reach_executing(), "T12", TaskFacts(
        step_states=("VERIFIED", "VERIFIED"))).state


def reach_accepted() -> TaskState:
    """Given：VERIFYING --T18--> ACCEPTED（required 全过、semantic 有证据、无 human）。"""
    return TaskMachine.transition(reach_verifying(), "T18", TaskFacts(
        all_required_passed=True, semantic_without_evidence=0,
        human_required_count=0)).state


# ---------- SM-01–SM-18 ----------

def sm01() -> None:
    """SM-01 契约 risk=low、无副作用、无高亮槽位 | 用户确认
    Given: PROPOSED（由 T2 产出）  When: T5 CONFIRM_CONTRACT
    Then: SPEC_LOCKED，事件带 confirmed_by=user
    """
    proposed = reach_proposed()
    r = TaskMachine.transition(proposed, "T5", TaskFacts(
        risk="low", side_effects=(), highlighted_slots=()))
    check("SM-01 迁移到 SPEC_LOCKED", r.state.status == "SPEC_LOCKED", r.state.status)
    check("SM-01 confirmed_by=user", r.state.confirmed_by == "user", r.state.confirmed_by)


def sm02() -> None:
    """SM-02 契约 risk=medium | 卡倒计时结束
    Given: PROPOSED  When: T6（倒计时那条自动开工路径）
    Then: 不迁移，仍停在 PROPOSED
    """
    proposed = reach_proposed()
    before = proposed.status
    try:
        TaskMachine.transition(proposed, "T6", TaskFacts(
            risk="medium", start_policy_approved=True))
        check("SM-02 medium 不倒计时自动开工", False, "居然迁移了")
    except GuardRejected:
        check("SM-02 medium 倒计时不迁移（守卫拒绝）", True)
    check("SM-02 状态仍 PROPOSED", proposed.status == before, proposed.status)


def sm03() -> None:
    """SM-03 契约 risk=high，有 2 个高亮槽位 | 用户只确认了 1 个
    Given: PROPOSED  When: T5  Then: 拒绝 CONFIRM_CONTRACT，返回缺失槽位清单
    """
    proposed = reach_proposed()
    try:
        TaskMachine.transition(proposed, "T5", TaskFacts(
            risk="high", highlighted_slots=("deliver_to", "goal"),
            confirmed_slots=("goal",)))
        check("SM-03 只确认 1 个被拒", False, "没有拒绝")
    except GuardRejected as exc:
        msg = " ".join(exc.reasons)
        check("SM-03 报出缺失槽位 deliver_to", "deliver_to" in msg, msg)
    check("SM-03 状态仍 PROPOSED", proposed.status == "PROPOSED", proposed.status)


def sm04() -> None:
    """SM-04 契约 version=2，用户确认的是 version=1 | 发 CONFIRM_CONTRACT(v1)
    Given: PROPOSED  When: T5  Then: 拒绝（版本不匹配）
    """
    proposed = reach_proposed()
    try:
        TaskMachine.transition(proposed, "T5", TaskFacts(
            contract_version_matches=False))
        check("SM-04 旧 version 被拒", False, "没有拒绝")
    except GuardRejected:
        check("SM-04 旧 version 被拒（守卫拒绝）", True)


def sm05() -> None:
    """SM-05 第一次验收打回签名 S1 | 第二次打回，签名仍是 S1
    Given: VERIFYING 且已打回过一次（attempt=1, last_failure_signature=S1）
           ——这个 Given 由 T16 真实产出，不是手搓
    When: T17  Then: 直接 BLOCKED(retry_exhausted)，不消耗第 3 次
    """
    verifying = reach_verifying()
    # Given：第一次打回（T16 放行，attempt 0->1，记下 S1）——这条链要接着走，
    # 不能中途新建状态，否则 attempt 归零、签名丢失，第二次打回就还能自动重试。
    after_first = TaskMachine.transition(verifying, "T16", TaskFacts(
        failed_required=True, failure_signature="S1", max_retries=3)).state
    check("SM-05 Given：第一次打回到 EXECUTING",
          after_first.status == "EXECUTING", after_first.status)
    check("SM-05 Given：attempt 递增到 1", after_first.attempt == 1, str(after_first.attempt))
    # 同一台机器再验收一次回到 VERIFYING（attempt 与签名都在 state 上带着）
    back = TaskMachine.transition(after_first, "T12", TaskFacts(
        step_states=("VERIFIED", "VERIFIED"))).state
    check("SM-05 Given：回到 VERIFYING 且 attempt 仍是 1",
          back.status == "VERIFYING" and back.attempt == 1,
          f"{back.status}/attempt={back.attempt}")
    # When：第二次打回，签名仍是 S1
    r = TaskMachine.transition(back, "T17", TaskFacts(
        failed_required=True, failure_signature="S1", max_retries=3))
    check("SM-05 同签名直接 BLOCKED", r.state.status == "BLOCKED", r.state.status)
    check("SM-05 reason=retry_exhausted",
          r.state.blocked_reason == "retry_exhausted", r.state.blocked_reason)
    check("SM-05 不消耗第 3 次（attempt 仍 1）", r.state.attempt == 1, str(r.state.attempt))
    check("SM-05 resume_to=VERIFYING", r.state.resume_to == "VERIFYING", r.state.resume_to)


def sm06() -> None:
    """SM-06 第一次打回 S1，第二次 S2 | 第三次 S3
    Given: VERIFYING  When: 连续三次 T16（签名各不同）再一次 T17
    Then: 三次都用尽后才 BLOCKED
    """
    state = reach_verifying()
    for round_no, sig in enumerate(("S1", "S2", "S3"), start=1):
        state = TaskMachine.transition(state, "T16", TaskFacts(
            failed_required=True, failure_signature=sig, max_retries=3)).state
        check(f"SM-06 第 {round_no} 次打回（签名 {sig}）放行到 EXECUTING",
              state.status == "EXECUTING", state.status)
        check(f"SM-06 第 {round_no} 次后 attempt={round_no}",
              state.attempt == round_no, str(state.attempt))
        # 接着同一条链回 VERIFYING（不能新建，否则 attempt 归零）
        state = TaskMachine.transition(state, "T12", TaskFacts(
            step_states=("VERIFIED", "VERIFIED"))).state
    r = TaskMachine.transition(state, "T17", TaskFacts(
        failed_required=True, failure_signature="S4", max_retries=3))
    check("SM-06 三次用尽后 BLOCKED", r.state.status == "BLOCKED", r.state.status)
    check("SM-06 reason=retry_exhausted",
          r.state.blocked_reason == "retry_exhausted", r.state.blocked_reason)


def sm07() -> None:
    """SM-07 全部 Step VERIFIED，但有一步处于 UNKNOWN | 尝试 EXECUTING→VERIFYING
    Given: EXECUTING  When: T12  Then: 拒绝，reason 点名 UNKNOWN
    """
    executing = reach_executing()
    try:
        TaskMachine.transition(executing, "T12", TaskFacts(
            step_states=("VERIFIED", "UNKNOWN")))
        check("SM-07 有 UNKNOWN 被拒", False, "没有拒绝")
    except GuardRejected as exc:
        msg = " ".join(exc.reasons)
        check("SM-07 拒绝理由点名 UNKNOWN", "UNKNOWN" in msg, msg)


def sm08() -> None:
    """SM-08 产出物路径 ../../etc/passwd | Step 交产出物
    Given: Step 在 RUNNING  When: S6  Then: OBSERVED 被拒（越界）
    """
    running = step("RUNNING")
    try:
        StepMachine.transition(running, "S6", StepFacts(
            artifact_path="../../etc/passwd"))
        check("SM-08 越界路径被拒", False, "没有拒绝")
    except GuardRejected:
        check("SM-08 越界产出物路径被拒（守卫拒绝）", True)
    # 对照：合法路径同一条迁移放行——证明拒绝的是越界，不是 S6 本身
    ok = StepMachine.transition(running, "S6", StepFacts(
        artifact_path="out/report.md"))
    check("SM-08 对照：合法路径放行到 OBSERVED",
          ok.state.status == "OBSERVED", ok.state.status)


def sm09() -> None:
    """SM-09 契约只声明 overwrite_file | 执行中出现 send_message
    Given: EXECUTING  When: T14  Then: FAILED（未声明副作用）
    """
    executing = reach_executing()
    r = TaskMachine.transition(executing, "T14", TaskFacts(
        undeclared_effects=("send_message",)))
    check("SM-09 未声明副作用直接 FAILED", r.state.status == "FAILED", r.state.status)


def sm10() -> None:
    """SM-10 ACCEPTED，risk=high | 未经 APPROVE_SIDE_EFFECT 就交付
    Given: ACCEPTED（由 T18 产出，且契约带 high 风险副作用）
    When: T19  Then: 拒绝；DELIVERING 不成立
    """
    accepted = reach_accepted()
    try:
        TaskMachine.transition(accepted, "T19", TaskFacts(
            risk="high", side_effects=(eff(),), approve_side_effect=False))
        check("SM-10 未批准不得交付", False, "居然迁移了")
    except GuardRejected:
        check("SM-10 high 未批准被拒（守卫拒绝）", True)
    check("SM-10 状态仍 ACCEPTED", accepted.status == "ACCEPTED", accepted.status)
    # 对照：批准后同一条迁移放行
    ok = TaskMachine.transition(accepted, "T19", TaskFacts(
        risk="high", side_effects=(eff(),), approve_side_effect=True))
    check("SM-10 对照：批准后进入 DELIVERING",
          ok.state.status == "DELIVERING", ok.state.status)


def sm11() -> None:
    """SM-11 DELIVERING，同一 idempotency_key 被触发两次 | 第二次执行
    Given: 一条已声明的 overwrite_file，effect_log 为空
    When: deliver() 调两次（同一 key）  Then: 第二次不产生第二个副作用，
          只记 replayed；把新 effect_log 喂给 T20 能到 DELIVERED
    ——本场景跨 server/task/delivery.py，执行器是注入的计数假件。
    """
    calls = []

    def executor(effect):
        calls.append(effect.idempotency_key)
        return EffectResult(ok=True, value=f"first-result:{effect.idempotency_key}")

    declared = (eff(effect_id="e1", key="k1"),)
    # When 第一次：执行器被调一次，落一条 done
    first = deliver(declared, (), executor, now_ms=1761000000000)
    check("SM-11 第一次调用执行器 1 次", len(calls) == 1, str(len(calls)))
    check("SM-11 第一次落一条 done",
          [r.status for r in first.records] == ["done"],
          str([r.status for r in first.records]))
    # When 第二次：同一 key，执行器不再被调，只记 replayed
    second = deliver(declared, first.records, executor, now_ms=1761000000001)
    check("SM-11 第二次不调执行器（仍 1 次）", len(calls) == 1, str(len(calls)))
    check("SM-11 第二次只记 replayed，不产生第二个 done",
          [r.status for r in second.records] == ["done", "replayed"],
          str([r.status for r in second.records]))
    # Then：这份 effect_log 能让 T20 认可交付完成
    delivering = task("DELIVERING")
    r = TaskMachine.transition(delivering, "T20", TaskFacts(
        declared_effect_keys=("k1",), effect_records=second.records))
    check("SM-11 带着 done+replayed 的日志进入 DELIVERED",
          r.state.status == "DELIVERED", r.state.status)


def sm12() -> None:
    """SM-12 Worker 在 RUNNING 时断开 | 立刻重试
    Given: Step 在 RUNNING  When: attempt 直接回 READY
    Then: 拒绝（IllegalTransition，不是 GuardRejected——表里没有这条边）
    """
    running = step("RUNNING")
    try:
        StepMachine.attempt(running, "READY", StepFacts())
        check("SM-12 RUNNING 不能直接回 READY", False, "居然迁移了")
    except IllegalTransition:
        check("SM-12 立刻重试被拒（表外目标，IllegalTransition）", True)
    except TransitionError as exc:
        check("SM-12 立刻重试被拒（表外目标，IllegalTransition）", False,
              f"实际 {type(exc).__name__}")
    check("SM-12 状态仍 RUNNING", running.status == "RUNNING", running.status)


def sm13() -> None:
    """SM-13 UNKNOWN，对账发现文件已写出 | 对账
    Given: Step 在 UNKNOWN  When: S13（有对账证据且结论是已执行）
    Then: UNKNOWN→OBSERVED，不重跑
    """
    unknown = step("UNKNOWN")
    r = StepMachine.transition(unknown, "S13", StepFacts(
        reconciliation_evidence="sha256:abc", reconciled_executed=True))
    check("SM-13 对账已执行转到 OBSERVED", r.state.status == "OBSERVED", r.state.status)


def sm14() -> None:
    """SM-14 UNKNOWN，对账确认未执行 | 对账
    Given: Step 在 UNKNOWN  When: S14（有对账证据且结论是未执行）
    Then: UNKNOWN→READY，可用同一幂等键重派
    """
    unknown = step("UNKNOWN")
    r = StepMachine.transition(unknown, "S14", StepFacts(
        reconciliation_evidence="sha256:abc", reconciled_executed=False))
    check("SM-14 对账未执行回到 READY", r.state.status == "READY", r.state.status)
    # 「可用同一幂等键重派」：从 READY 能再走 S3 派出去
    again = StepMachine.transition(r.state, "S3", StepFacts(
        permissions_required=("L0",), permissions_granted=("L0",),
        concurrency=1, max_concurrent=4))
    check("SM-14 可再次派发（S3 放行）", again.state.status == "DISPATCHED",
          again.state.status)


def sm15() -> None:
    """SM-15 BLOCKED(need_authorization)，用户授权 | ANSWER
    Given: BLOCKED，resume_to=SPEC_LOCKED，阻塞在某个 side_effect.id 上
    When: T22，authorization_effect_id 命中  Then: 回到 resume_to
    """
    locked = TaskMachine.transition(reach_proposed(), "T5", TaskFacts(risk="low")).state
    blocked = TaskMachine.transition(locked, "T9", TaskFacts(
        blocked_reason="need_authorization", resume_to="SPEC_LOCKED",
        blocked_effect_id="e1")).state
    check("SM-15 Given：阻塞在 need_authorization",
          blocked.status == "BLOCKED" and blocked.blocked_reason == "need_authorization",
          f"{blocked.status}/{blocked.blocked_reason}")
    r = TaskMachine.transition(blocked, "T22", TaskFacts(
        authorization_effect_id="e1", blocked_effect_id="e1"))
    check("SM-15 授权命中回到 resume_to", r.state.status == "SPEC_LOCKED", r.state.status)
    # 授权只对当次 side_effect.id 有效：换一个 id 不放行
    try:
        TaskMachine.transition(blocked, "T22", TaskFacts(
            authorization_effect_id="e_other", blocked_effect_id="e1"))
        check("SM-15 别的副作用 id 不认", False, "居然放行了")
    except GuardRejected:
        check("SM-15 授权只对当次 side_effect.id 有效", True)


def sm16() -> None:
    """SM-16 EXECUTING，用户说「算了」| CANCEL_TASK
    Given: EXECUTING 且有两个未完成 Step
    When: 先把每个 Step 走 S15（worker_cancel_returned），再 T24
    Then: Task CANCELLED，closed_reason=cancelled
    ——「Worker cancel() 已返回」在本层落成 worker_cancel_returned 事实；
        纯状态机层没有真 Worker，这个映射据实标注。
    """
    executing = reach_executing()
    open_steps = [step("RUNNING"), step("DISPATCHED")]
    cancelled = []
    for st in open_steps:
        r = StepMachine.transition(st, "S15", StepFacts(
            worker_cancel_returned=True))
        cancelled.append(r.state.status)
    check("SM-16 每个未完成 Step 都被取消", cancelled == ["CANCELLED", "CANCELLED"],
          str(cancelled))
    r = TaskMachine.transition(executing, "T24", TaskFacts(
        inflight_reconciled=True, open_steps_remaining=0))
    check("SM-16 Task 转到 CANCELLED", r.state.status == "CANCELLED", r.state.status)
    check("SM-16 closed_reason=cancelled",
          r.state.closed_reason == "cancelled", r.state.closed_reason)
    # 对照：还有 Step 没取消时 T24 不放行
    try:
        TaskMachine.transition(executing, "T24", TaskFacts(
            inflight_reconciled=True, open_steps_remaining=1))
        check("SM-16 对照：有 Step 未取消时拒绝结单", False, "居然放行了")
    except GuardRejected:
        check("SM-16 对照：有 Step 未取消时拒绝结单", True)


def sm17() -> None:
    """SM-17 VERIFYING，存在 kind=human 的 required 标准 | 自动判定全过
    Given: VERIFYING  When: T17  Then: BLOCKED(need_clarification)，
          resume_to=VERIFYING；不直接 ACCEPTED
    """
    verifying = reach_verifying()
    # 先证明 T18 确实被人判标准堵着（不直接 ACCEPTED 的前半）
    try:
        TaskMachine.transition(verifying, "T18", TaskFacts(
            all_required_passed=True, semantic_without_evidence=0,
            human_required_count=1))
        check("SM-17 有 human 标准不能直接 ACCEPTED", False, "居然 ACCEPTED 了")
    except GuardRejected:
        check("SM-17 有 human 标准不能直接 ACCEPTED（守卫拒绝）", True)
    r = TaskMachine.transition(verifying, "T17", TaskFacts(
        failed_required=True, failure_signature="H1", human_required_count=1))
    check("SM-17 迁到 BLOCKED", r.state.status == "BLOCKED", r.state.status)
    check("SM-17 reason=need_clarification",
          r.state.blocked_reason == "need_clarification", r.state.blocked_reason)
    check("SM-17 resume_to=VERIFYING", r.state.resume_to == "VERIFYING", r.state.resume_to)


def sm18() -> None:
    """SM-18 任意状态收到一个不在迁移表里的目标 | 迁移
    Given: EXECUTING（由 T10 产出）  When: attempt 一个表外目标
    Then: 拒绝并记 kind=illegal_transition，状态不变
    """
    executing = reach_executing()
    before = executing.status
    try:
        TaskMachine.attempt(executing, "DELIVERED", TaskFacts())
        check("SM-18 表外目标被拒", False, "居然迁移了")
    except IllegalTransition as exc:
        check("SM-18 kind=illegal_transition", exc.kind == "illegal_transition", exc.kind)
        check("SM-18 带 allowed 列表", isinstance(exc.allowed, tuple) and len(exc.allowed) > 0,
              str(exc.allowed))
    check("SM-18 状态不变", executing.status == before, executing.status)


SCENARIOS = (
    ("SM-01", sm01), ("SM-02", sm02), ("SM-03", sm03), ("SM-04", sm04),
    ("SM-05", sm05), ("SM-06", sm06), ("SM-07", sm07), ("SM-08", sm08),
    ("SM-09", sm09), ("SM-10", sm10), ("SM-11", sm11), ("SM-12", sm12),
    ("SM-13", sm13), ("SM-14", sm14), ("SM-15", sm15), ("SM-16", sm16),
    ("SM-17", sm17), ("SM-18", sm18),
)


def main() -> int:
    print("[评测集 D 类] 规范 §4.10 SM-01–SM-18 场景化（Given 由状态机产出）")
    for name, fn in SCENARIOS:
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 单条场景崩不拖垮其余 17 条
            check(f"{name} 场景本身没抛异常", False, f"{type(exc).__name__}: {exc}")
    print()
    print("=" * 72)
    if failed:
        print(f"FAILED {len(failed)}/{passed + len(failed)}")
        for item in failed:
            print("  - " + item)
        return 1
    print(f"PASS {passed}/{passed}")
    print("18 条场景全过；其中 1 条（SM-11）依赖注入执行器，其余 17 条只用状态机事实字段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
