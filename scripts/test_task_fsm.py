# -*- coding: utf-8 -*-
"""Task/Step 双层状态机独立验收（规范 §4.1–§4.8，M1 第 1、2 步）。

    $env:PYTHONIOENCODING="utf-8"
    & "D:/project/neurosama/.venv/Scripts/python.exe" scripts/test_task_fsm.py

覆盖范围：

  A  状态集（§4.2）：13 个 Task 态、11 个 Step 态、终态集合、BLOCKED 取值域
  B  Task 迁移表（§4.4 T1–T24）：每条边一个允许用例 + 一个拒绝用例，拒绝必须带明确错误类型
  C  Step 迁移表（§4.5 S1–S15）：同上
  D  全组合穷举：13x13 + 11x11 个 (state, target) 与 edges() 逐一对照，
     不在表里的必须抛 IllegalTransition，在表里的绝不允许抛 IllegalTransition
  E  属性测试（§4.10 末）：随机序列 + 随机规则 + 随机 facts，断言不产生非法状态、
     终态无出边、BLOCKED 必带 resume_to
  F  §4.10 的 SM-01 至 SM-18 逐条
  G  失败分类与签名（§4.6）
  H  重试与确认策略（§4.7）
  I  派发后不得重放（§4.8）
  J  台账投影（§4.3）
  K  边界：零业务 import、零 IO、无 {"type": ...} 字面量、状态对象不可变

写测试的人 ≠ 写实现的人：本文件只读 server/task 的公开行为，不 patch 实现内部。

各节覆盖的是**不同类**的不变量，不可互相替代，因此都不能被收窄：

| 节 | 覆盖的不变量 | 已知盲区 |
|---|---|---|
| D | 迁移表层面：edges() 里有什么边、表外组合一律拒绝、逐终态无出边 | 无 |
| B/C | 逐边守卫：每条边的允许条件与拒绝理由 | 无 |
| E | 行为层面：随机游走不会走进非法状态、终态无出边、BLOCKED 必带 resume_to | **取消类边** |

**D 节那 3 条「终态」断言不是冗余，不要收窄。** 变异实验证实它们各自抓不同的失败模式
（把 T24 通配改成只放回 DELIVERED 一个终态，只红这 3 条）：
  - 「边里不含终态作源」抓 edges() 集合里混进终态出发的边
  - 「allows(x->y) 为真」抓 edges() 与 allows() **不一致**（改坏 allows() 时只有它红）
  - 「终态 x 无出边」抓逐终态视角（rules_from 与 edges 双查）
三条一起才覆盖「表错」与「两个 API 打架」两种病。

**E 节对取消类边的变异系统性不敏感，这是已知盲区。** 原因在长链游走
（搜 `forward = [r for r in rules if r.target != "CANCELLED"]`，就在本文件里）：
为了不被 T24 这条通配边在第一步就吸走，游走主动降权了取消边
（`forward = [r for r in rules if r.target != "CANCELLED"]`，只在 forward 为空时才选取消边）。
于是「取消边的守卫或目标被改坏」这类变异，E 节几乎不会实际走到那条边，
`not in edges()` 自然不触发。这不是缺陷（那行是有理由的工程折衷），但它是盲区：
改坏取消边只能靠 D 节那 3 条和 B/C 的逐边守卫发现，E 节帮不上。
（注意：E 节**有** `edges()` 检查，搜 `not in _task_edges()` / `not in _step_edges()`
能找到三处。走不到取消边是游走降权的后果，不是没有检查——想「补一行 edges()
检查」是无用功，要补的是取消边本身被走到的机会，或者承认这条只能由 D 节守。
这里不写行号：注释在文件头、被引用的代码在下方，改一次注释行号就漂，留搜索串更稳。）
所以 D 节那 3 条同时还是这条盲区唯一的守军，收窄它们会直接打开这个缺口。
"""
from __future__ import annotations

import ast
import itertools
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 实现缺失与实现损坏是两种不同的失败，处置不同：
#   实现未开始  -> 明确说"还没写"，不算测试失败，但也不冒充通过
#   实现有错    -> 指出具体文件和错误，让施工方直接去修那一行
# 两者都不静默跳过，也不把 import 错误说成断言失败。
try:
    from server.task import (  # noqa: E402
        BLOCKED_REASONS, DEFAULT_DESTINATION, FAILURE_CLASSES, LEDGER_STATUSES, REPLAY_REPLAN,
        REPLAY_SAME_KEY, REPLAY_UNKNOWN, RESUME_TARGETS, RETRYABLE, START_POLICIES, STEP_STATES,
        STEP_TERMINAL, TASK_STATES, TASK_TERMINAL, DispatchEvidence, Failure, GuardRejected,
        IllegalTransition, LedgerProjection, RetryDecision, SideEffectDecl, StateValueError,
        EffectRecord, StepFacts, StepMachine, StepState, TaskFacts, TaskMachine, TaskState,
    TransitionError,
        blocked_kind_of, build_signature, can_auto_start, classify, destination_for,
        normalize_error_text, project_task, replay_decision, requires_reconciliation,
        step_retry_decision, task_retry_decision, unknown_exits,
    )
    IMPL_STATUS = "ok"
except ModuleNotFoundError as exc:
    missing = exc.name or ""
    if missing == "server" or missing == "server.task" or missing.startswith("server.task."):
        print("=" * 72)
        print(f"[实现未开始] server/task 包不存在（{exc.name}）。")
        print("本套件验的是规范 §4.1–§4.8 的状态机行为，实现就位前无法判定。")
        print("这不等于通过——是「无实现可验」，处置：等施工方交付 server/task/ 后重跑。")
        print("=" * 72)
        sys.exit(2)
    raise
except (ImportError, AttributeError, NameError, SyntaxError) as exc:
    import traceback
    print("=" * 72)
    print("[实现损坏] server/task 能定位但导入失败——实现本身有错，不是测试的问题。")
    print(f"错误: {type(exc).__name__}: {exc}")
    print("最后两帧（指出该改哪个文件哪一行）:")
    for line in traceback.format_exc().strip().split("\n")[-4:]:
        print("  " + line)
    print("=" * 72)
    sys.exit(3)

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


# ---------- 构造助手 ----------

def task(state: str, **kw) -> TaskState:
    return TaskState(status=state, **kw)


def step(state: str, **kw) -> StepState:
    return StepState(status=state, **kw)


def eff(**kw) -> SideEffectDecl:
    base = dict(id="e1", type="overwrite_file", target="out/a.txt",
                target_class="local", reversible=True, idempotency_key="k1")
    base.update(kw)
    return SideEffectDecl(**base)


def rec(key: str, status: str = "done", **kw) -> "EffectRecord":
    """构造一条 effect_log 记录（§4.4 T20 / §5.2 DELIVERY_EFFECT）。

    status 是闭集 done | failed | replayed，只有 done 算成功。
    非法 status 在 EffectRecord 构造时就抛 ValueError，不在守卫里兜。
    """
    return EffectRecord(
        idempotency_key=key,
        status=status,
        effect_id=kw.get("effect_id", f"e_{key}:{status}"),
        type=kw.get("type", "overwrite_file"),
        target_class=kw.get("target_class", "local"),
        at=kw.get("at", 1761000000000),
    )


def expect_illegal(label: str, fn) -> None:
    try:
        fn()
    except IllegalTransition as exc:
        check(f"{label} 抛 IllegalTransition", exc.kind == "illegal_transition")
    except TransitionError as exc:  # GuardRejected 是 TransitionError 的子类
        check(f"{label} 抛 IllegalTransition", False, f"实际 {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - 测试要区分「完全没抛」
        check(f"{label} 抛 IllegalTransition", False, f"实际 {type(exc).__name__}: {exc}")
    else:
        check(f"{label} 抛 IllegalTransition", False, "没有抛异常")


def expect_guard(label: str, fn, *, contains: str = "") -> None:
    try:
        fn()
    except GuardRejected as exc:
        ok = bool(exc.reasons) and all(r.strip() for r in exc.reasons)
        if contains:
            ok = ok and any(contains in r for r in exc.reasons)
        check(f"{label} 抛 GuardRejected", ok,
              "" if ok else f"reasons={exc.reasons!r}")
    except TransitionError as exc:
        check(f"{label} 抛 GuardRejected", False, f"实际 {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        check(f"{label} 抛 GuardRejected", False, f"实际 {type(exc).__name__}: {exc}")
    else:
        check(f"{label} 抛 GuardRejected", False, "没有抛异常")


def expect_ok(label: str, machine, state, rule_id: str, facts, *, target: str = ""):
    try:
        result = machine.transition(state, rule_id, facts)
    except TransitionError as exc:
        check(label, False, f"{type(exc).__name__}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"{type(exc).__name__}: {exc}")
        return None
    ok = result.rule_id == rule_id and result.source == state.status
    if target:
        ok = ok and result.target == target and result.state.status == target
    check(label, ok, "" if ok else f"result={result}")
    return result


# ---------- A 状态集 ----------

def test_a_states() -> None:
    print("[A] 状态集（§4.2）")
    check("Task 状态 13 个", len(TASK_STATES) == 13, str(len(TASK_STATES)))
    check("Task 状态无重复", len(set(TASK_STATES)) == 13)
    for name in ("CLARIFYING", "PROPOSED", "SPEC_LOCKED", "DISPATCHED", "EXECUTING", "VERIFYING",
                 "BLOCKED", "ACCEPTED", "DELIVERING", "DELIVERED", "CANCELLED", "FAILED", "TIMEOUT"):
        check(f"Task 状态含 {name}", name in TASK_STATES)
    check("Task 终态恰为四个", TASK_TERMINAL == frozenset(
        {"DELIVERED", "CANCELLED", "FAILED", "TIMEOUT"}), str(sorted(TASK_TERMINAL)))
    check("Step 状态 11 个", len(STEP_STATES) == 11, str(len(STEP_STATES)))
    check("Step 状态无重复", len(set(STEP_STATES)) == 11)
    for name in ("CREATED", "READY", "DISPATCHED", "RUNNING", "OBSERVED", "VERIFIED",
                 "REJECTED", "FAILED", "UNKNOWN", "SKIPPED", "CANCELLED"):
        check(f"Step 状态含 {name}", name in STEP_STATES)
    check("Step 终态恰为四个", STEP_TERMINAL == frozenset(
        {"VERIFIED", "FAILED", "SKIPPED", "CANCELLED"}), str(sorted(STEP_TERMINAL)))
    check("BLOCKED 原因五值", set(BLOCKED_REASONS) == {
        "need_clarification", "need_approval", "need_authorization",
        "retry_exhausted", "ambiguous_result"}, str(BLOCKED_REASONS))
    check("resume_to 取值域含 DISPATCHED（§4.4 T11 要求）",
          "DISPATCHED" in RESUME_TARGETS, str(RESUME_TARGETS))
    check("台账七态", set(LEDGER_STATUSES) == {
        "drafting", "briefing", "running", "blocked", "auditing", "ready_to_report", "done"},
        str(LEDGER_STATUSES))
    # 两层共有 CANCELLED / DISPATCHED / FAILED 三个同名状态（规范原表如此），
    # 所以判据是「两套集合不同」，不是「无交集」。
    check("Task 与 Step 状态集不是同一套",
          set(TASK_STATES) != set(STEP_STATES) and len(set(TASK_STATES) - set(STEP_STATES)) == 10,
          str(sorted(set(TASK_STATES) - set(STEP_STATES))))


# ---------- B Task 迁移表（T1–T24）----------

def test_b_task_edges() -> None:
    print("[B] Task 迁移表逐边（§4.4）")
    M = TaskMachine

    # T1 CLARIFYING -> CLARIFYING
    r = expect_ok("T1 允许：还有缺口且轮数未满", M, task("CLARIFYING"), "T1",
                  TaskFacts(rounds_used=1, max_rounds=3, gaps_changed=True), target="CLARIFYING")
    expect_guard("T1 拒绝：轮数耗尽", lambda: M.transition(
        task("CLARIFYING"), "T1", TaskFacts(rounds_used=3, gaps_changed=True)), contains="3")
    expect_guard("T1 拒绝：缺口没变", lambda: M.transition(
        task("CLARIFYING"), "T1", TaskFacts(rounds_used=0, gaps_changed=False)))

    # T2 CLARIFYING -> PROPOSED
    expect_ok("T2 允许：GateResult ready", M, task("CLARIFYING"), "T2",
              TaskFacts(gate_ready=True), target="PROPOSED")
    expect_ok("T2 允许：保守补齐且全高亮", M, task("CLARIFYING"), "T2",
              TaskFacts(conservative_filled=True, gap_slots_all_highlighted=True),
              target="PROPOSED")
    expect_guard("T2 拒绝：既非 ready 也非保守高亮", lambda: M.transition(
        task("CLARIFYING"), "T2", TaskFacts(conservative_filled=True,
                                            gap_slots_all_highlighted=False)))

    # T3 CLARIFYING -> CANCELLED 无守卫
    expect_ok("T3 允许：用户放弃", M, task("CLARIFYING"), "T3", TaskFacts(), target="CANCELLED")

    # T4 PROPOSED -> CLARIFYING
    expect_ok("T4 允许：version 递增", M, task("PROPOSED"), "T4",
              TaskFacts(contract_version_incremented=True), target="CLARIFYING")
    expect_guard("T4 拒绝：version 未递增", lambda: M.transition(
        task("PROPOSED"), "T4", TaskFacts(contract_version_incremented=False)))

    # T5 PROPOSED -> SPEC_LOCKED（显式确认）
    expect_ok("T5 允许：low 风险显式确认", M, task("PROPOSED"), "T5",
              TaskFacts(risk="low", contract_id_matches=True, contract_version_matches=True),
              target="SPEC_LOCKED")
    res = expect_ok("T5 允许：high 风险高亮槽位全确认", M, task("PROPOSED"), "T5",
                    TaskFacts(risk="high", highlighted_slots=("a", "b"),
                              confirmed_slots=("a", "b")), target="SPEC_LOCKED")
    if res is not None:
        check("T5 记 confirmed_by=user", res.state.confirmed_by == "user", res.state.confirmed_by)
    expect_guard("T5 拒绝：contract_id 不一致", lambda: M.transition(
        task("PROPOSED"), "T5", TaskFacts(contract_id_matches=False)))
    expect_guard("T5 拒绝：version 不一致", lambda: M.transition(
        task("PROPOSED"), "T5", TaskFacts(contract_version_matches=False)))
    expect_guard("T5 拒绝：high 风险缺高亮槽位", lambda: M.transition(
        task("PROPOSED"), "T5", TaskFacts(risk="high", highlighted_slots=("a", "b"),
                                          confirmed_slots=("a",))), contains="b")

    # T6 PROPOSED -> SPEC_LOCKED（倒计时）
    expect_ok("T6 允许：low 无副作用无高亮且策略已批", M, task("PROPOSED"), "T6",
              TaskFacts(risk="low", start_policy_approved=True), target="SPEC_LOCKED")
    res = expect_ok("T6 允许：副作用本地可逆", M, task("PROPOSED"), "T6",
                    TaskFacts(risk="low", start_policy_approved=True,
                              side_effects=(eff(),)), target="SPEC_LOCKED")
    if res is not None:
        check("T6 记 confirmed_by=countdown", res.state.confirmed_by == "countdown",
              res.state.confirmed_by)
    expect_guard("T6 拒绝：medium 风险", lambda: M.transition(
        task("PROPOSED"), "T6", TaskFacts(risk="medium", start_policy_approved=True)))
    expect_guard("T6 拒绝：不可逆副作用", lambda: M.transition(
        task("PROPOSED"), "T6", TaskFacts(
            risk="low", start_policy_approved=True,
            side_effects=(eff(reversible=False),))), contains="不可逆")
    expect_guard("T6 拒绝：非本地副作用", lambda: M.transition(
        task("PROPOSED"), "T6", TaskFacts(
            risk="low", start_policy_approved=True,
            side_effects=(eff(target_class="third_party"),))))
    expect_guard("T6 拒绝：有高亮槽位", lambda: M.transition(
        task("PROPOSED"), "T6", TaskFacts(
            risk="low", start_policy_approved=True, highlighted_slots=("x",))))
    expect_guard("T6 拒绝：确认门策略未批", lambda: M.transition(
        task("PROPOSED"), "T6", TaskFacts(risk="low", start_policy_approved=False)))

    # T7 PROPOSED -> CANCELLED
    expect_ok("T7 允许：卡片超时取消", M, task("PROPOSED"), "T7", TaskFacts(), target="CANCELLED")

    # T8 SPEC_LOCKED -> DISPATCHED
    expect_ok("T8 允许：权限齐、有将、预算够", M, task("SPEC_LOCKED"), "T8",
              TaskFacts(permissions_required=("write",), permissions_granted=("write",)),
              target="DISPATCHED")
    expect_guard("T8 拒绝：缺权限", lambda: M.transition(
        task("SPEC_LOCKED"), "T8", TaskFacts(
            permissions_required=("write",), permissions_granted=())), contains="write")
    expect_guard("T8 拒绝：无可用 Worker", lambda: M.transition(
        task("SPEC_LOCKED"), "T8", TaskFacts(worker_available=False)))
    expect_guard("T8 拒绝：预算耗尽", lambda: M.transition(
        task("SPEC_LOCKED"), "T8", TaskFacts(budget_exhausted=True)))
    expect_guard("T8 拒绝：契约已不是冻结版", lambda: M.transition(
        task("SPEC_LOCKED"), "T8", TaskFacts(frozen_version_current=False)))

    # T9 SPEC_LOCKED -> BLOCKED
    res = expect_ok("T9 允许：无适配器需授权", M, task("SPEC_LOCKED"), "T9",
                    TaskFacts(blocked_reason="need_authorization", resume_to="SPEC_LOCKED"),
                    target="BLOCKED")
    if res is not None:
        check("T9 保留 resume_to", res.state.resume_to == "SPEC_LOCKED", res.state.resume_to)
    expect_guard("T9 拒绝：原因不合法", lambda: M.transition(
        task("SPEC_LOCKED"), "T9", TaskFacts(blocked_reason="retry_exhausted",
                                            resume_to="SPEC_LOCKED")))
    expect_guard("T9 拒绝：resume_to 不是 SPEC_LOCKED", lambda: M.transition(
        task("SPEC_LOCKED"), "T9", TaskFacts(blocked_reason="need_authorization",
                                            resume_to="EXECUTING")))

    # T10 DISPATCHED -> EXECUTING
    expect_ok("T10 允许：至少一条回执", M, task("DISPATCHED"), "T10",
              TaskFacts(dispatch_receipts=1), target="EXECUTING")
    expect_guard("T10 拒绝：零回执", lambda: M.transition(
        task("DISPATCHED"), "T10", TaskFacts(dispatch_receipts=0)))

    # T11 DISPATCHED -> BLOCKED
    res = expect_ok("T11 允许：全无回执且超时", M, task("DISPATCHED"), "T11",
                    TaskFacts(dispatch_receipts=0, ack_timed_out=True), target="BLOCKED")
    if res is not None:
        check("T11 写 ambiguous_result", res.state.blocked_reason == "ambiguous_result",
              res.state.blocked_reason)
        check("T11 resume_to=DISPATCHED", res.state.resume_to == "DISPATCHED",
              res.state.resume_to)
    expect_guard("T11 拒绝：已有回执", lambda: M.transition(
        task("DISPATCHED"), "T11", TaskFacts(dispatch_receipts=1, ack_timed_out=True)),
        contains="1")
    expect_guard("T11 拒绝：未超时", lambda: M.transition(
        task("DISPATCHED"), "T11", TaskFacts(dispatch_receipts=0, ack_timed_out=False)))

    # T12 EXECUTING -> VERIFYING
    expect_ok("T12 允许：全部 Step 终态、无未声明副作用", M, task("EXECUTING"), "T12",
              TaskFacts(step_states=("VERIFIED", "SKIPPED")), target="VERIFYING")
    expect_guard("T12 拒绝：还有 Step 在跑", lambda: M.transition(
        task("EXECUTING"), "T12", TaskFacts(step_states=("VERIFIED", "RUNNING"))),
        contains="RUNNING")
    expect_guard("T12 拒绝：有 Step UNKNOWN（SM-07）", lambda: M.transition(
        task("EXECUTING"), "T12", TaskFacts(step_states=("VERIFIED", "UNKNOWN"))),
        contains="UNKNOWN")
    expect_guard("T12 拒绝：有 Step DISPATCHED", lambda: M.transition(
        task("EXECUTING"), "T12", TaskFacts(step_states=("DISPATCHED",))))
    expect_guard("T12 拒绝：未声明副作用", lambda: M.transition(
        task("EXECUTING"), "T12", TaskFacts(
            step_states=("VERIFIED",), undeclared_effects=("send_message",))),
        contains="send_message")

    # T13 EXECUTING -> BLOCKED
    res = expect_ok("T13 允许：Step 重试耗尽", M, task("EXECUTING"), "T13",
                    TaskFacts(blocked_reason="retry_exhausted"), target="BLOCKED")
    if res is not None:
        check("T13 resume_to=EXECUTING", res.state.resume_to == "EXECUTING", res.state.resume_to)
    expect_ok("T13 允许：需授权", M, task("EXECUTING"), "T13",
              TaskFacts(blocked_reason="need_authorization"), target="BLOCKED")
    expect_guard("T13 拒绝：原因不合法", lambda: M.transition(
        task("EXECUTING"), "T13", TaskFacts(blocked_reason="need_clarification")))

    # T14 EXECUTING -> FAILED
    expect_ok("T14 允许：未声明副作用（SM-09）", M, task("EXECUTING"), "T14",
              TaskFacts(undeclared_effects=("send_message",)), target="FAILED")
    expect_ok("T14 允许：预算硬耗尽", M, task("EXECUTING"), "T14",
              TaskFacts(budget_hard_exhausted=True), target="FAILED")
    expect_guard("T14 拒绝：都没发生", lambda: M.transition(
        task("EXECUTING"), "T14", TaskFacts()))

    # T15 EXECUTING -> TIMEOUT
    expect_ok("T15 允许：已对账在飞副作用", M, task("EXECUTING"), "T15",
              TaskFacts(inflight_reconciled=True), target="TIMEOUT")
    expect_guard("T15 拒绝：还有在飞副作用", lambda: M.transition(
        task("EXECUTING"), "T15", TaskFacts(inflight_reconciled=False)))

    # T16 VERIFYING -> EXECUTING
    res = expect_ok("T16 允许：首次打回", M, task("VERIFYING"), "T16",
                    TaskFacts(failed_required=True, failure_signature="S1",
                              max_retries=3), target="EXECUTING")
    if res is not None:
        check("T16 attempt 递增到 1", res.state.attempt == 1, str(res.state.attempt))
        check("T16 记下签名", res.state.last_failure_signature == "S1",
              res.state.last_failure_signature)
    expect_guard("T16 拒绝：没有失败的 required", lambda: M.transition(
        task("VERIFYING"), "T16", TaskFacts(failed_required=False)))
    expect_guard("T16 拒绝：签名重复（SM-05）", lambda: M.transition(
        task("VERIFYING", attempt=1, last_failure_signature="S1"), "T16",
        TaskFacts(failed_required=True, failure_signature="S1")), contains="S1")
    expect_guard("T16 拒绝：次数耗尽", lambda: M.transition(
        task("VERIFYING", attempt=3), "T16",
        TaskFacts(failed_required=True, failure_signature="S9", max_retries=3)),
        contains="3")

    # T17 VERIFYING -> BLOCKED
    res = expect_ok("T17 允许：次数耗尽转阻塞", M, task("VERIFYING", attempt=3), "T17",
                    TaskFacts(failed_required=True, failure_signature="S9", max_retries=3),
                    target="BLOCKED")
    if res is not None:
        check("T17 reason=retry_exhausted", res.state.blocked_reason == "retry_exhausted",
              res.state.blocked_reason)
        check("T17 resume_to=VERIFYING", res.state.resume_to == "VERIFYING", res.state.resume_to)
    expect_ok("T17 允许：签名重复直接阻塞（SM-05）", M,
              task("VERIFYING", attempt=1, last_failure_signature="S1"), "T17",
              TaskFacts(failed_required=True, failure_signature="S1"), target="BLOCKED")
    expect_ok("T17 允许：有 human 标准（SM-17）", M, task("VERIFYING"), "T17",
              TaskFacts(failed_required=True, failure_signature="S1",
                        human_required_count=1), target="BLOCKED")
    # §4.4 T17 分派（6b23691 补）：human 标准 -> need_clarification；
    # 重试耗尽/签名重复 -> retry_exhausted；两者并存取 need_clarification。
    r_human = M.transition(task("VERIFYING"), "T17", TaskFacts(
        failed_required=True, failure_signature="S1", human_required_count=1))
    check("T17 reason=need_clarification（有 human 标准）",
          r_human.state.blocked_reason == "need_clarification",
          r_human.state.blocked_reason)
    check("T17 resume_to=VERIFYING（human 标准）",
          r_human.state.resume_to == "VERIFYING", r_human.state.resume_to)
    r_both = M.transition(task("VERIFYING", attempt=3), "T17", TaskFacts(
        failed_required=True, failure_signature="S1", max_retries=3,
        human_required_count=1))
    check("T17 并存时取 need_clarification（不取 retry_exhausted）",
          r_both.state.blocked_reason == "need_clarification",
          r_both.state.blocked_reason)
    expect_guard("T17 拒绝：还能自动重试", lambda: M.transition(
        task("VERIFYING"), "T17", TaskFacts(failed_required=True, failure_signature="S1")),
        contains="3")

    # T18 VERIFYING -> ACCEPTED
    expect_ok("T18 允许：required 全过、semantic 有证据", M, task("VERIFYING"), "T18",
              TaskFacts(all_required_passed=True, semantic_without_evidence=0),
              target="ACCEPTED")
    expect_guard("T18 拒绝：required 未全过", lambda: M.transition(
        task("VERIFYING"), "T18", TaskFacts(all_required_passed=False)))
    expect_guard("T18 拒绝：semantic 缺证据", lambda: M.transition(
        task("VERIFYING"), "T18", TaskFacts(all_required_passed=True,
                                           semantic_without_evidence=2)), contains="2")
    expect_guard("T18 拒绝：有 human 标准未判（SM-17）", lambda: M.transition(
        task("VERIFYING"), "T18", TaskFacts(all_required_passed=True,
                                           human_required_count=1)))

    # T19 ACCEPTED -> DELIVERING
    expect_ok("T19 允许：low 无副作用直接交付", M, task("ACCEPTED"), "T19",
              TaskFacts(risk="low"), target="DELIVERING")
    expect_ok("T19 允许：high 有副作用但已 APPROVE", M, task("ACCEPTED"), "T19",
              TaskFacts(risk="high", side_effects=(eff(),), approve_side_effect=True),
              target="DELIVERING")
    expect_guard("T19 拒绝：high 未批准（SM-10）", lambda: M.transition(
        task("ACCEPTED"), "T19", TaskFacts(risk="high", side_effects=(eff(),))),
        contains="APPROVE_SIDE_EFFECT")

    # T20 DELIVERING -> DELIVERED
    # 只数 status=done 的记录。三种失败必须报三种不同文案——排查时分得清是哪一种。
    expect_ok("T20 允许：恰好一条 done", M, task("DELIVERING"), "T20",
              TaskFacts(declared_effect_keys=("k1",), effect_records=(rec("k1"),)),
              target="DELIVERED")
    # 改动前会误拒的正向场景：1 条 done + 1 条 failed 是正常重试，不是重复执行
    expect_ok("T20 允许：一条 done + 一条 failed（正常重试，只数 done）", M,
              task("DELIVERING"), "T20",
              TaskFacts(declared_effect_keys=("k1",),
                        effect_records=(rec("k1", "failed"), rec("k1", "done"))),
              target="DELIVERED")
    # 失败情形 1：声明了但记录里根本没这个键
    expect_guard("T20 拒绝(1/3)：完全没有这个键的记录", lambda: M.transition(
        task("DELIVERING"), "T20", TaskFacts(declared_effect_keys=("k1",),
                                            effect_records=())), contains="没有任何记录")
    expect_guard("T20 拒绝(1/3)：只有别的 key", lambda: M.transition(
        task("DELIVERING"), "T20", TaskFacts(declared_effect_keys=("k1",),
                                            effect_records=(rec("k2"),))), contains="没有任何记录")
    # 失败情形 2：有记录但一条 done 都没有
    expect_guard("T20 拒绝(2/3)：只有一条 failed（0 条成功）", lambda: M.transition(
        task("DELIVERING"), "T20", TaskFacts(
            declared_effect_keys=("k1",),
            effect_records=(rec("k1", "failed"),))), contains="没有成功的")
    expect_guard("T20 拒绝(2/3)：只有 replayed（没真执行过）", lambda: M.transition(
        task("DELIVERING"), "T20", TaskFacts(
            declared_effect_keys=("k1",),
            effect_records=(rec("k1", "replayed"),))), contains="没有成功的")
    expect_guard("T20 拒绝(2/3)：两条 failed 也不报成「重复执行」", lambda: M.transition(
        task("DELIVERING"), "T20", TaskFacts(
            declared_effect_keys=("k1",),
            effect_records=(rec("k1", "failed"), rec("k1", "failed")))), contains="没有成功的")
    # EffectRecord 自身的构造时校验：status 是闭集，拼错必须在构造时炸，
    # 不能静默落进「不是 done」的分支（那会让 T20 永远拒绝且没人知道是拼错了）
    for bad_status in ("DONE", "Done", "succes", "success", "ok", ""):
        try:
            EffectRecord(idempotency_key="k1", status=bad_status, effect_id="e1")
        except ValueError:
            check(f"EffectRecord 拒绝非法 status {bad_status!r}", True)
        else:
            check(f"EffectRecord 拒绝非法 status {bad_status!r}", False, "没有抛 ValueError")
    for good_status in ("done", "failed", "replayed"):
        try:
            EffectRecord(idempotency_key="k1", status=good_status, effect_id="e1")
        except ValueError as exc:
            check(f"EffectRecord 接受合法 status {good_status!r}", False, str(exc))
        else:
            check(f"EffectRecord 接受合法 status {good_status!r}", True)
    try:
        EffectRecord(idempotency_key="", status="done", effect_id="e1")
    except ValueError:
        check("EffectRecord 拒绝空 idempotency_key", True)
    else:
        check("EffectRecord 拒绝空 idempotency_key", False, "没有抛")
    # effect_id 也补上：否则这条会因为「缺 effect_id」先炸，而不是因为 status 非法，
    # 就成了「因为错的原因通过」。补上后它只测 status。
    try:
        EffectRecord.from_mapping({"idempotency_key": "k1", "status": "bogus",
                                   "effect_id": "e1"})
    except ValueError:
        check("from_mapping 也校 status", True)
    else:
        check("from_mapping 也校 status", False, "没有抛")
    ok_rec = EffectRecord.from_mapping({"idempotency_key": "k1", "status": "done",
                                        "effect_id": "e1", "type": "overwrite_file",
                                        "target_class": "local", "at": 1761000000000})
    check("from_mapping 正常构造", ok_rec.idempotency_key == "k1" and ok_rec.status == "done")

    # 失败情形 3：重复 done
    expect_guard("T20 拒绝(3/3)：两条 done（重复执行，SM-11）", lambda: M.transition(
        task("DELIVERING"), "T20", TaskFacts(declared_effect_keys=("k1",),
                                            effect_records=(rec("k1"), rec("k1")))),
        contains="疑似重复执行")

    # T21 DELIVERING -> BLOCKED
    res = expect_ok("T21 允许：目标拒绝要重授权", M, task("DELIVERING"), "T21",
                    TaskFacts(blocked_reason="need_approval"), target="BLOCKED")
    if res is not None:
        check("T21 resume_to=DELIVERING", res.state.resume_to == "DELIVERING",
              res.state.resume_to)
    expect_guard("T21 拒绝：原因不合法", lambda: M.transition(
        task("DELIVERING"), "T21", TaskFacts(blocked_reason="need_clarification")))

    # T22 BLOCKED -> resume_to
    res = expect_ok("T22 允许：非空回答回到 resume_to", M,
                    task("BLOCKED", resume_to="VERIFYING", blocked_reason="retry_exhausted"),
                    "T22", TaskFacts(answer_text="再试一次"), target="VERIFYING")
    if res is not None:
        check("T22 清空 blocked 字段",
              res.state.resume_to == "" and res.state.blocked_reason == "",
              f"{res.state.resume_to!r}/{res.state.blocked_reason!r}")
    expect_ok("T22 允许：授权命中当次 side_effect（SM-15）", M,
              task("BLOCKED", resume_to="DELIVERING", blocked_reason="need_approval"),
              "T22", TaskFacts(authorization_effect_id="e1", blocked_effect_id="e1"),
              target="DELIVERING")
    expect_guard("T22 拒绝：空回答且未授权", lambda: M.transition(
        task("BLOCKED", resume_to="VERIFYING"), "T22", TaskFacts(answer_text="   ")))
    expect_guard("T22 拒绝：授权对象不匹配", lambda: M.transition(
        task("BLOCKED", resume_to="DELIVERING"), "T22",
        TaskFacts(authorization_effect_id="e2", blocked_effect_id="e1")))

    # T23 BLOCKED -> CANCELLED
    res = expect_ok("T23 允许：阻塞中取消", M, task("BLOCKED", resume_to="EXECUTING"), "T23",
                    TaskFacts(), target="CANCELLED")
    if res is not None:
        check("T23 记 closed_reason=cancelled", res.state.closed_reason == "cancelled",
              res.state.closed_reason)

    # T24 任意非终态 -> CANCELLED
    for src in ("CLARIFYING", "PROPOSED", "SPEC_LOCKED", "DISPATCHED", "EXECUTING",
                "VERIFYING", "BLOCKED", "ACCEPTED", "DELIVERING"):
        expect_ok(f"T24 允许：{src} 取消", M, task(src), "T24",
                  TaskFacts(inflight_reconciled=True, open_steps_remaining=0),
                  target="CANCELLED")
    expect_guard("T24 拒绝：还有在飞副作用", lambda: M.transition(
        task("EXECUTING"), "T24", TaskFacts(inflight_reconciled=False)))
    expect_guard("T24 拒绝：还有 Step 未取消", lambda: M.transition(
        task("EXECUTING"), "T24", TaskFacts(open_steps_remaining=2)), contains="2")
    for src in sorted(TASK_TERMINAL):
        expect_illegal(f"T24 拒绝：终态 {src} 没有出边", lambda s=src: M.transition(
            task(s), "T24", TaskFacts()))


# ---------- C Step 迁移表（S1–S15）----------

def test_c_step_edges() -> None:
    print("[C] Step 迁移表逐边（§4.5）")
    M = StepMachine

    # S1 CREATED -> READY
    expect_ok("S1 允许：无条件依赖", M, step("CREATED"), "S1", StepFacts(), target="READY")
    expect_ok("S1 允许：依赖全 VERIFIED", M, step("CREATED"), "S1",
              StepFacts(dep_states=("VERIFIED", "VERIFIED")), target="READY")
    expect_guard("S1 拒绝：依赖未全 VERIFIED", lambda: M.transition(
        step("CREATED"), "S1", StepFacts(dep_states=("VERIFIED", "RUNNING"))),
        contains="RUNNING")

    # S2 CREATED -> SKIPPED
    expect_ok("S2 允许：依赖 FAILED", M, step("CREATED"), "S2",
              StepFacts(dep_states=("FAILED",)), target="SKIPPED")
    expect_ok("S2 允许：依赖 SKIPPED", M, step("CREATED"), "S2",
              StepFacts(dep_states=("SKIPPED",)), target="SKIPPED")
    expect_ok("S2 允许：条件不成立", M, step("CREATED"), "S2",
              StepFacts(condition_holds=False), target="SKIPPED")
    expect_guard("S2 拒绝：Task 已终态", lambda: M.transition(
        step("CREATED"), "S2", StepFacts(task_terminal=True, condition_holds=False)))
    expect_guard("S2 拒绝：依赖正常且条件成立", lambda: M.transition(
        step("CREATED"), "S2", StepFacts(dep_states=("VERIFIED",))))

    # S3 READY -> DISPATCHED
    expect_ok("S3 允许：权限齐且并发未满", M, step("READY"), "S3",
              StepFacts(permissions_required=("write",), permissions_granted=("write",),
                        concurrency=1, max_concurrent=4), target="DISPATCHED")
    expect_guard("S3 拒绝：缺权限", lambda: M.transition(
        step("READY"), "S3", StepFacts(permissions_required=("write",))), contains="write")
    expect_guard("S3 拒绝：并发已满", lambda: M.transition(
        step("READY"), "S3", StepFacts(concurrency=4, max_concurrent=4)), contains="4")

    # S4 DISPATCHED -> RUNNING
    expect_ok("S4 允许：有执行回执", M, step("DISPATCHED"), "S4",
              StepFacts(ack_receipt="pid:4242"), target="RUNNING")
    expect_guard("S4 拒绝：无回执", lambda: M.transition(
        step("DISPATCHED"), "S4", StepFacts(ack_receipt="  ")))

    # S5 DISPATCHED -> UNKNOWN
    expect_ok("S5 允许：已写 receipt 且超时", M, step("DISPATCHED"), "S5",
              StepFacts(dispatch_recorded=True, ack_timed_out=True), target="UNKNOWN")
    expect_guard("S5 拒绝：没有 dispatch_receipt", lambda: M.transition(
        step("DISPATCHED"), "S5", StepFacts(dispatch_recorded=False, ack_timed_out=True)))
    expect_guard("S5 拒绝：未超时", lambda: M.transition(
        step("DISPATCHED"), "S5", StepFacts(dispatch_recorded=True, ack_timed_out=False)))

    # S6 RUNNING -> OBSERVED
    expect_ok("S6 允许：产出物在范围内", M, step("RUNNING"), "S6",
              StepFacts(artifact_path="out/report.md"), target="OBSERVED")
    expect_guard("S6 拒绝：无产出物路径", lambda: M.transition(
        step("RUNNING"), "S6", StepFacts(artifact_path="")))
    expect_guard("S6 拒绝：路径上跳（SM-08）", lambda: M.transition(
        step("RUNNING"), "S6", StepFacts(artifact_path="../../etc/passwd")),
        contains="..")
    expect_guard("S6 拒绝：绝对路径", lambda: M.transition(
        step("RUNNING"), "S6", StepFacts(artifact_path="C:/Windows/system32")))

    # S7 RUNNING -> FAILED
    expect_ok("S7 允许：不可重试错误", M, step("RUNNING"), "S7",
              StepFacts(error_retryable=False), target="FAILED")
    expect_ok("S7 允许：重试耗尽", M, step("RUNNING", retries=2), "S7",
              StepFacts(max_step_retries=2), target="FAILED")
    expect_guard("S7 拒绝：可重试且未耗尽", lambda: M.transition(
        step("RUNNING"), "S7", StepFacts(error_retryable=True, max_step_retries=2)),
        contains="2")

    # S8 RUNNING -> UNKNOWN
    expect_ok("S8 允许：墙钟超限", M, step("RUNNING"), "S8",
              StepFacts(dispatch_recorded=True, wall_elapsed_s=901, max_wall_s=900),
              target="UNKNOWN")
    expect_ok("S8 允许：连接中断（SM-12）", M, step("RUNNING"), "S8",
              StepFacts(dispatch_recorded=True, connection_lost=True), target="UNKNOWN")
    expect_guard("S8 拒绝：没有 dispatch_receipt", lambda: M.transition(
        step("RUNNING"), "S8", StepFacts(dispatch_recorded=False, connection_lost=True)))
    expect_guard("S8 拒绝：未超时且未断", lambda: M.transition(
        step("RUNNING"), "S8", StepFacts(dispatch_recorded=True,
                                          wall_elapsed_s=10, max_wall_s=900)))

    # S9 OBSERVED -> VERIFIED
    expect_ok("S9 允许：断言全过", M, step("OBSERVED"), "S9",
              StepFacts(criterion_results=(True, True)), target="VERIFIED")
    expect_guard("S9 拒绝：没有断言结果", lambda: M.transition(
        step("OBSERVED"), "S9", StepFacts(criterion_results=())))
    expect_guard("S9 拒绝：有断言未过", lambda: M.transition(
        step("OBSERVED"), "S9", StepFacts(criterion_results=(True, False))), contains="1")

    # S10 OBSERVED -> REJECTED
    expect_ok("S10 允许：存在失败断言", M, step("OBSERVED"), "S10",
              StepFacts(criterion_results=(True, False)), target="REJECTED")
    expect_guard("S10 拒绝：全部通过", lambda: M.transition(
        step("OBSERVED"), "S10", StepFacts(criterion_results=(True, True))))
    expect_guard("S10 拒绝：没有断言结果", lambda: M.transition(
        step("OBSERVED"), "S10", StepFacts(criterion_results=())))

    # S11 REJECTED -> READY
    res = expect_ok("S11 允许：可重做且签名不同", M, step("REJECTED"), "S11",
                    StepFacts(failure_signature="S2", max_step_retries=2), target="READY")
    if res is not None:
        check("S11 retries 递增到 1", res.state.retries == 1, str(res.state.retries))
        check("S11 记下新签名", res.state.last_failure_signature == "S2",
              res.state.last_failure_signature)
    expect_guard("S11 拒绝：签名重复", lambda: M.transition(
        step("REJECTED", retries=1, last_failure_signature="S1"), "S11",
        StepFacts(failure_signature="S1")), contains="S1")
    expect_guard("S11 拒绝：重试耗尽", lambda: M.transition(
        step("REJECTED", retries=2), "S11",
        StepFacts(failure_signature="S3", max_step_retries=2)), contains="2")

    # S12 REJECTED -> FAILED
    expect_ok("S12 允许：重试耗尽", M, step("REJECTED", retries=2), "S12",
              StepFacts(max_step_retries=2), target="FAILED")
    expect_ok("S12 允许：签名重复", M, step("REJECTED", retries=1,
                                          last_failure_signature="S1"), "S12",
              StepFacts(failure_signature="S1"), target="FAILED")
    expect_guard("S12 拒绝：还能重做", lambda: M.transition(
        step("REJECTED"), "S12", StepFacts(failure_signature="S2", max_step_retries=2)),
        contains="2")

    # S13 UNKNOWN -> OBSERVED
    expect_ok("S13 允许：对账确认已执行（SM-13）", M, step("UNKNOWN"), "S13",
              StepFacts(reconciliation_evidence="文件哈希已变", reconciled_executed=True),
              target="OBSERVED")
    expect_guard("S13 拒绝：没有对账证据", lambda: M.transition(
        step("UNKNOWN"), "S13", StepFacts(reconciliation_evidence="  ",
                                          reconciled_executed=True)))
    expect_guard("S13 拒绝：结论是未执行", lambda: M.transition(
        step("UNKNOWN"), "S13", StepFacts(reconciliation_evidence="查过，没写",
                                          reconciled_executed=False)))

    # S14 UNKNOWN -> READY
    expect_ok("S14 允许：对账确认未执行（SM-14）", M, step("UNKNOWN"), "S14",
              StepFacts(reconciliation_evidence="工作区快照一致", reconciled_executed=False),
              target="READY")
    expect_guard("S14 拒绝：没有对账证据", lambda: M.transition(
        step("UNKNOWN"), "S14", StepFacts(reconciliation_evidence="")))
    expect_guard("S14 拒绝：结论是已执行", lambda: M.transition(
        step("UNKNOWN"), "S14", StepFacts(reconciliation_evidence="已写",
                                          reconciled_executed=True)))

    # S15 任意非终态 -> CANCELLED
    for src in ("CREATED", "READY", "DISPATCHED", "RUNNING", "OBSERVED", "REJECTED", "UNKNOWN"):
        expect_ok(f"S15 允许：{src} 取消", M, step(src), "S15",
                  StepFacts(worker_cancel_returned=True), target="CANCELLED")
    expect_guard("S15 拒绝：cancel() 未返回", lambda: M.transition(
        step("RUNNING"), "S15", StepFacts(worker_cancel_returned=False)))
    for src in sorted(STEP_TERMINAL):
        expect_illegal(f"S15 拒绝：终态 {src} 没有出边", lambda s=src: M.transition(
            step(s), "S15", StepFacts(worker_cancel_returned=True)))


# ---------- D 全组合穷举 ----------

def _task_edges() -> set:
    return set(TaskMachine.edges())


def _step_edges() -> set:
    return set(StepMachine.edges())


def test_d_exhaustive() -> None:
    print("[D] 全组合穷举：表外一律拒绝")
    t_edges = _task_edges()
    check("Task 边数 > 0", len(t_edges) > 0, str(len(t_edges)))
    check("Task 边里不含终态作源",
          not any(src in TASK_TERMINAL for src, _ in t_edges),
          str([e for e in t_edges if e[0] in TASK_TERMINAL]))
    for src, dst in sorted(t_edges):
        check(f"allows({src}->{dst}) 为真", TaskMachine.allows(src, dst))
    # 表外：13x13 全组合里不在 edges 的必须抛 IllegalTransition
    outside = 0
    for src, dst in itertools.product(TASK_STATES, TASK_STATES):
        if (src, dst) in t_edges:
            continue
        outside += 1
        try:
            TaskMachine.attempt(task(src), dst, TaskFacts())
        except IllegalTransition:
            pass
        except TransitionError as exc:
            check(f"表外 {src}->{dst} 是 IllegalTransition", False,
                  f"实际 {type(exc).__name__}")
        else:
            check(f"表外 {src}->{dst} 是 IllegalTransition", False, "没有抛异常")
    check("Task 表外组合数 = 169 - 边数", outside == 169 - len(t_edges),
          f"{outside} vs {169 - len(t_edges)}")
    check("Task 表外组合一律 IllegalTransition", outside == 169 - len(t_edges))

    s_edges = _step_edges()
    check("Step 边数 > 0", len(s_edges) > 0, str(len(s_edges)))
    check("Step 边里不含终态作源",
          not any(src in STEP_TERMINAL for src, _ in s_edges),
          str([e for e in s_edges if e[0] in STEP_TERMINAL]))
    for src, dst in sorted(s_edges):
        check(f"allows({src}->{dst}) 为真", StepMachine.allows(src, dst))
    outside = 0
    for src, dst in itertools.product(STEP_STATES, STEP_STATES):
        if (src, dst) in s_edges:
            continue
        outside += 1
        try:
            StepMachine.attempt(step(src), dst, StepFacts())
        except IllegalTransition:
            pass
        except TransitionError as exc:
            check(f"表外 {src}->{dst} 是 IllegalTransition", False,
                  f"实际 {type(exc).__name__}")
        else:
            check(f"表外 {src}->{dst} 是 IllegalTransition", False, "没有抛异常")
    check("Step 表外组合数 = 121 - 边数", outside == 121 - len(s_edges),
          f"{outside} vs {121 - len(s_edges)}")
    check("Step 表外组合一律 IllegalTransition", outside == 121 - len(s_edges))

    # 终态没有任何出边
    for src in sorted(TASK_TERMINAL):
        check(f"Task 终态 {src} 无出边",
              TaskMachine.rules_from(src) == () and not any(e[0] == src for e in t_edges))
    for src in sorted(STEP_TERMINAL):
        check(f"Step 终态 {src} 无出边",
              StepMachine.rules_from(src) == () and not any(e[0] == src for e in s_edges))

    # 规则 id 完整
    t_ids = [r.rule_id for r in TaskMachine.rules_from("CLARIFYING")]
    all_t = set()
    for src in TASK_STATES:
        all_t.update(r.rule_id for r in TaskMachine.rules_from(src))
    check("Task 规则 T1–T24 恰好 24 条", all_t == {f"T{i}" for i in range(1, 25)},
          str(sorted(all_t)))
    all_s = set()
    for src in STEP_STATES:
        all_s.update(r.rule_id for r in StepMachine.rules_from(src))
    check("Step 规则 S1–S15 恰好 15 条", all_s == {f"S{i}" for i in range(1, 16)},
          str(sorted(all_s)))
    t_all = [r for src in TASK_STATES for r in TaskMachine.rules_from(src)]
    s_all = [r for src in STEP_STATES for r in StepMachine.rules_from(src)]
    # 通配规则（T24/S15）会出现在每个非终态下，是**同一条**规则对象，不是重复定义。
    # 判据：按 id 分组后每组里的规则内容（id/source/target/guard_text）必须完全一致。
    def unique_rules(rules):
        by_id = {}
        for rule in rules:
            key = (rule.rule_id, rule.source, rule.target, rule.guard_text)
            by_id.setdefault(rule.rule_id, set()).add(key)
        return all(len(keys) == 1 for keys in by_id.values())
    check("Task 规则 id 无重复定义", unique_rules(t_all), f"{len({r.rule_id for r in t_all})} ids")
    check("Step 规则 id 无重复定义", unique_rules(s_all), f"{len({r.rule_id for r in s_all})} ids")


# ---------- E 属性测试 ----------

TASK_PASSING_FACTS = {}


def _task_rule_passes(rule_id: str) -> TaskFacts:
    """每一条 Task 规则一个能通过的 facts。表序即规则契约。"""
    return {
        "T1": TaskFacts(rounds_used=0, max_rounds=3, gaps_changed=True),
        "T2": TaskFacts(gate_ready=True),
        "T3": TaskFacts(),
        "T4": TaskFacts(contract_version_incremented=True),
        "T5": TaskFacts(contract_id_matches=True, contract_version_matches=True),
        "T6": TaskFacts(risk="low", start_policy_approved=True),
        "T7": TaskFacts(),
        "T8": TaskFacts(),
        "T9": TaskFacts(blocked_reason="need_authorization", resume_to="SPEC_LOCKED"),
        "T10": TaskFacts(dispatch_receipts=1),
        "T11": TaskFacts(dispatch_receipts=0, ack_timed_out=True),
        "T12": TaskFacts(step_states=("VERIFIED",)),
        "T13": TaskFacts(blocked_reason="retry_exhausted"),
        "T14": TaskFacts(budget_hard_exhausted=True),
        "T15": TaskFacts(inflight_reconciled=True),
        "T16": TaskFacts(failed_required=True, failure_signature="S1", max_retries=3),
        "T17": TaskFacts(failed_required=True, failure_signature="S1"),
        "T18": TaskFacts(all_required_passed=True),
        "T19": TaskFacts(risk="low"),
        "T20": TaskFacts(declared_effect_keys=(), effect_records=()),
        "T21": TaskFacts(blocked_reason="need_approval"),
        "T22": TaskFacts(answer_text="好"),
        "T23": TaskFacts(),
        "T24": TaskFacts(),
    }[rule_id]


STEP_PASSING_FACTS = {
    "S1": StepFacts(),
    "S2": StepFacts(condition_holds=False),
    "S3": StepFacts(),
    "S4": StepFacts(ack_receipt="pid:1"),
    "S5": StepFacts(dispatch_recorded=True, ack_timed_out=True),
    "S6": StepFacts(artifact_path="out/x.md"),
    "S7": StepFacts(error_retryable=False),
    "S8": StepFacts(dispatch_recorded=True, connection_lost=True),
    "S9": StepFacts(criterion_results=(True,)),
    "S10": StepFacts(criterion_results=(False,)),
    "S11": StepFacts(failure_signature="S2"),
    "S12": StepFacts(failure_signature="S1"),
    "S13": StepFacts(reconciliation_evidence="e", reconciled_executed=True),
    "S14": StepFacts(reconciliation_evidence="e", reconciled_executed=False),
    "S15": StepFacts(worker_cancel_returned=True),
}


def _source_states(machine, states, terminal, rule) -> list:
    if rule.source == "*":
        return [s for s in states if s not in terminal]
    return [rule.source]


def _rules_by_state(machine, states, terminal) -> dict:
    out = {}
    for src in states:
        out[src] = machine.rules_from(src)
    return out


def test_e_property() -> None:
    print("[E] 属性测试（§4.10 末：随机序列）")
    rng = random.Random(20261022)

    task_rules = {}
    for src in TASK_STATES:
        for rule in TaskMachine.rules_from(src):
            task_rules[rule.rule_id] = rule
    step_rules = {}
    for src in STEP_STATES:
        for rule in StepMachine.rules_from(src):
            step_rules[rule.rule_id] = rule

    check("Task 规则表 24 条", len(task_rules) == 24, str(len(task_rules)))
    check("Step 规则表 15 条", len(step_rules) == 15, str(len(step_rules)))

    # 1) 随机「规则 id + facts」序列：只要有一步抛异常，捕获并按规范分类断言
    illegal_hits = 0
    guard_hits = 0
    ok_hits = 0
    terminal_violation = 0
    blocked_without_resume = 0
    for _ in range(2000):
        state = TaskState()
        for _ in range(rng.randint(1, 12)):
            rule_id = f"T{rng.randint(1, 24)}"
            rule = task_rules[rule_id]
            facts = (_task_rule_passes(rule_id) if rng.random() < 0.5 else TaskFacts())
            try:
                result = TaskMachine.transition(state, rule_id, facts)
            except IllegalTransition:
                illegal_hits += 1
                continue  # 状态不变
            except GuardRejected:
                guard_hits += 1
                continue  # 状态不变
            ok_hits += 1
            new = result.state
            # 属性 (1)：绝不进入未列出的 (from,to)
            if (state.status, new.status) not in _task_edges():
                terminal_violation += 1
            # 属性 (2)：终态没有出边
            if state.status in TASK_TERMINAL:
                terminal_violation += 1
            # 属性 (3)：BLOCKED 必带 resume_to
            if new.status == "BLOCKED" and not new.resume_to:
                blocked_without_resume += 1
            state = new
    check("随机序列 no 未列出迁移", terminal_violation == 0, str(terminal_violation))
    check("随机序列 BLOCKED 必带 resume_to", blocked_without_resume == 0,
          str(blocked_without_resume))
    check("随机序列确实走到过成功迁移", ok_hits > 0, str(ok_hits))
    check("随机序列确实触发过守卫拒绝", guard_hits > 0, str(guard_hits))

    check("随机序列确实触发过非法迁移", illegal_hits > 0, str(illegal_hits))

    # 2) 只挑「按当前状态合法」的规则 + 通过型 facts，长链状态机
    state = TaskState()
    steps = 0
    invalid = 0
    for _ in range(5000):
        rules = TaskMachine.rules_from(state.status)
        if not rules:
            break
        # 优先走非取消边，否则随机游走第一步就被 T24 吸走（T24 是通配边，永远可选）
        forward = [r for r in rules if r.target != "CANCELLED"]
        rule = rng.choice(forward or rules)
        facts = _task_rule_passes(rule.rule_id)
        try:
            result = TaskMachine.transition(state, rule.rule_id, facts)
        except GuardRejected:
            continue
        new = result.state
        if (state.status, new.status) not in _task_edges():
            invalid += 1
        if new.status == "BLOCKED" and not new.resume_to:
            invalid += 1
        state = new
        steps += 1
    check("长链随机游走无非法边", invalid == 0, str(invalid))
    check("长链随机游走至少 8 步且走到终态",
          steps >= 8 and state.status in TASK_TERMINAL, f"{steps}/{state.status}")

    # 3) Step 侧同样两条
    bad = 0
    s_ok = 0
    s_blocked = 0
    for _ in range(2000):
        st = StepState()
        for _ in range(rng.randint(1, 10)):
            rule_id = f"S{rng.randint(1, 15)}"
            facts = STEP_PASSING_FACTS[rule_id]
            try:
                result = StepMachine.transition(st, rule_id, facts)
            except TransitionError:
                continue
            if (st.status, result.state.status) not in _step_edges():
                bad += 1
            st = result.state
            s_ok += 1
    check("Step 随机序列无非法边", bad == 0, str(bad))
    check("Step 随机序列确实发生迁移", s_ok > 0, str(s_ok))

    # 4) 每次成功的 transition 都记录了 trail 与事件描述符
    st = TaskState()
    r = TaskMachine.transition(st, "T2", TaskFacts(gate_ready=True))
    check("成功迁移写 trail", r.state.trail == ("T2",), str(r.state.trail))
    check("T2 事件是 CONTRACT_PROPOSED", "CONTRACT_PROPOSED" in r.events, str(r.events))
    check("transition 不改原 state", st.status == "CLARIFYING" and st.trail == (),
          f"{st.status}/{st.trail}")


# ---------- F §4.10 验收用例 ----------

def test_f_acceptance_cases() -> None:
    print("[F] §4.10 验收用例 SM-01–SM-18")
    M = TaskMachine

    # SM-01 low / 无副作用 / 无高亮 -> 确认后 SPEC_LOCKED，带 confirmed_by=user
    r = expect_ok("SM-01 用户确认 low 任务", M, task("PROPOSED"), "T5",
                  TaskFacts(risk="low"), target="SPEC_LOCKED")
    if r is not None:
        check("SM-01 confirmed_by=user", r.state.confirmed_by == "user", r.state.confirmed_by)

    # SM-02 medium 卡倒计时结束 -> 不迁移，仍 PROPOSED
    st = task("PROPOSED")
    expect_guard("SM-02 medium 倒计时不迁移", lambda: M.transition(
        st, "T6", TaskFacts(risk="medium", start_policy_approved=True)))
    check("SM-02 状态仍 PROPOSED", st.status == "PROPOSED", st.status)

    # SM-03 high 两个高亮只确认一个 -> 拒绝并返回缺失清单
    try:
        M.transition(task("PROPOSED"), "T5", TaskFacts(
            risk="high", highlighted_slots=("a", "b"), confirmed_slots=("a",)))
    except GuardRejected as exc:
        msg = " ".join(exc.reasons)
        check("SM-03 报出缺失槽位 b", "b" in msg, msg)
    else:
        check("SM-03 报出缺失槽位 b", False, "没有拒绝")

    # SM-04 version 不一致 -> CONTRACT_STALE 语义（守卫拒绝）
    expect_guard("SM-04 旧 version 被拒", lambda: M.transition(
        task("PROPOSED"), "T5", TaskFacts(contract_version_matches=False)))

    # SM-05 第二次同签名 -> 直接 BLOCKED，不消耗第 3 次
    st = task("VERIFYING", attempt=1, last_failure_signature="S1")
    r = expect_ok("SM-05 同签名直接阻塞", M, st, "T17",
                  TaskFacts(failed_required=True, failure_signature="S1",
                            max_retries=3), target="BLOCKED")
    if r is not None:
        check("SM-05 不消耗次数（attempt 仍 1）", r.state.attempt == 1, str(r.state.attempt))

    # SM-06 S1 -> S2 -> S3 三次都用尽后才 BLOCKED
    st = task("VERIFYING")
    for i, sig in enumerate(("S1", "S2", "S3"), start=1):
        r = M.transition(st, "T16", TaskFacts(failed_required=True,
                                             failure_signature=sig, max_retries=3))
        check(f"SM-06 第 {i} 次打回仍可重试", r.state.attempt == i and
              r.state.status == "EXECUTING", f"{r.state.attempt}/{r.state.status}")
        st = TaskState(status="VERIFYING", attempt=r.state.attempt,
                       last_failure_signature=r.state.last_failure_signature)
    expect_guard("SM-06 第四次才阻塞", lambda: M.transition(
        st, "T16", TaskFacts(failed_required=True, failure_signature="S4",
                             max_retries=3)), contains="3")
    r = M.transition(st, "T17", TaskFacts(failed_required=True, failure_signature="S4",
                                         max_retries=3))
    check("SM-06 转向 BLOCKED(retry_exhausted)",
          r.state.status == "BLOCKED" and r.state.blocked_reason == "retry_exhausted",
          f"{r.state.status}/{r.state.blocked_reason}")

    # SM-07 有 Step UNKNOWN 时 EXECUTING->VERIFYING 必须拒
    expect_guard("SM-07 UNKNOWN 未解时拒绝验收", lambda: M.transition(
        task("EXECUTING"), "T12", TaskFacts(step_states=("VERIFIED", "UNKNOWN"))),
        contains="UNKNOWN")

    # SM-08 产出物路径越界 -> OBSERVED 被拒，E_CRITERION（越界）
    expect_guard("SM-08 越界路径被拒", lambda: StepMachine.transition(
        step("RUNNING"), "S6", StepFacts(artifact_path="../../etc/passwd")))
    f = classify("E_CRITERION", {"criterion": "c1"}, free_text="路径越界 ../../etc/passwd")
    check("SM-08 归类 E_CRITERION", f.failure_class == "E_CRITERION", f.failure_class)
    check("SM-08 E_CRITERION 按 §4.6 可自动重试", f.retryable is True, str(f.retryable))

    # SM-09 只声明 overwrite_file 却出现 send_message -> EXECUTING->FAILED
    r = expect_ok("SM-09 未声明副作用直接 FAILED", M, task("EXECUTING"), "T14",
                  TaskFacts(undeclared_effects=("send_message",)), target="FAILED")
    if r is not None:
        check("SM-09 目标就是 FAILED", r.state.status == "FAILED", r.state.status)

    # SM-10 high 未 APPROVE_SIDE_EFFECT 不能进 DELIVERING
    expect_guard("SM-10 未批准不得交付", lambda: M.transition(
        task("ACCEPTED"), "T19", TaskFacts(risk="high", side_effects=(eff(),))))

    # SM-11 同一 idempotency_key 触发两次 -> 第二次不应产生第二个 done 记录
    expect_guard("SM-11 两条 done 记录被拒", lambda: M.transition(
        task("DELIVERING"), "T20", TaskFacts(declared_effect_keys=("k1",),
                                            effect_records=(rec("k1"), rec("k1")))))
    expect_ok("SM-11 一条 done + 一条 replayed 仍放行（重放不阻碍 T20）", M,
              task("DELIVERING"), "T20",
              TaskFacts(declared_effect_keys=("k1",),
                        effect_records=(rec("k1"), rec("k1", "replayed"))),
              target="DELIVERED")

    # SM-12 RUNNING 断开 -> 立刻重试应被拒；先 UNKNOWN 再对账
    expect_illegal("SM-12 RUNNING 不能直接回 READY", lambda: StepMachine.attempt(
        step("RUNNING"), "READY", StepFacts(connection_lost=True)))
    r = expect_ok("SM-12 断连先转 UNKNOWN", StepMachine, step("RUNNING"), "S8",
                  StepFacts(dispatch_recorded=True, connection_lost=True), target="UNKNOWN")
    check("SM-12 UNKNOWN 不是终态", r is not None and "UNKNOWN" not in STEP_TERMINAL)

    # SM-13 UNKNOWN 对账发现已写 -> OBSERVED，不重跑
    expect_ok("SM-13 对账已执行转 OBSERVED", StepMachine, step("UNKNOWN"), "S13",
              StepFacts(reconciliation_evidence="哈希变了", reconciled_executed=True),
              target="OBSERVED")
    expect_guard("SM-13 已执行时不能回 READY（S14 守卫拒绝）", lambda: StepMachine.attempt(
        step("UNKNOWN"), "READY", StepFacts(reconciliation_evidence="哈希变了",
                                            reconciled_executed=True)))

    # SM-14 UNKNOWN 对账未执行 -> READY
    expect_ok("SM-14 对账未执行回 READY", StepMachine, step("UNKNOWN"), "S14",
              StepFacts(reconciliation_evidence="快照一致", reconciled_executed=False),
              target="READY")

    # SM-15 BLOCKED(need_authorization) 授权 -> 回 resume_to，且只对当次 side_effect 有效
    r = expect_ok("SM-15 授权命中回 resume_to", M,
                  task("BLOCKED", resume_to="DELIVERING", blocked_reason="need_approval"),
                  "T22",
                  TaskFacts(authorization_effect_id="e1", blocked_effect_id="e1"),
                  target="DELIVERING")
    expect_guard("SM-15 别的副作用不认", lambda: M.transition(
        task("BLOCKED", resume_to="DELIVERING"), "T22",
        TaskFacts(authorization_effect_id="e9", blocked_effect_id="e1")))

    # SM-16 EXECUTING 取消 -> Step 全 CANCELLED，Task CANCELLED
    expect_guard("SM-16 有 Step 未取消时不能结单", lambda: M.transition(
        task("EXECUTING"), "T24", TaskFacts(open_steps_remaining=2)))
    expect_ok("SM-16 Step 先取消", StepMachine, step("RUNNING"), "S15",
              StepFacts(worker_cancel_returned=True), target="CANCELLED")
    r = expect_ok("SM-16 Task 再取消", M, task("EXECUTING"), "T24",
                  TaskFacts(open_steps_remaining=0), target="CANCELLED")
    if r is not None:
        check("SM-16 closed_reason=cancelled", r.state.closed_reason == "cancelled",
              r.state.closed_reason)

    # SM-17（2026-10-22 修订）：human required -> BLOCKED(need_clarification) 等 ANSWER，
    # resume_to=VERIFYING；不是「停在 VERIFYING」（非终态悬挂 = 丢进黑洞）
    expect_guard("SM-17 有 human 标准不能 ACCEPTED", lambda: M.transition(
        task("VERIFYING"), "T18", TaskFacts(all_required_passed=True,
                                           human_required_count=1)))
    r = M.transition(task("VERIFYING"), "T17", TaskFacts(
        failed_required=True, failure_signature="H1", human_required_count=1))
    check("SM-17 转向 BLOCKED 等答复", r.state.status == "BLOCKED", r.state.status)
    check("SM-17 reason=need_clarification",
          r.state.blocked_reason == "need_clarification", r.state.blocked_reason)
    check("SM-17 resume_to=VERIFYING", r.state.resume_to == "VERIFYING", r.state.resume_to)
    check("SM-17 不直接 ACCEPTED", r.state.status != "ACCEPTED")

    # SM-18 任意状态收到表外目标 -> 拒绝并记 kind=illegal_transition，状态不变
    st = task("EXECUTING")
    try:
        M.attempt(st, "DELIVERED", TaskFacts())
    except IllegalTransition as exc:
        check("SM-18 kind=illegal_transition", exc.kind == "illegal_transition", exc.kind)
        check("SM-18 带 allowed 列表", isinstance(exc.allowed, tuple) and len(exc.allowed) > 0,
              str(exc.allowed))
    else:
        check("SM-18 kind=illegal_transition", False, "没有抛异常")
    check("SM-18 状态不变", st.status == "EXECUTING", st.status)


# ---------- G 失败分类与签名 ----------

def test_g_failures() -> None:
    print("[G] 失败分类与签名（§4.6）")
    check("八类失败类别齐全", set(FAILURE_CLASSES) == {
        "E_SPEC", "E_INPUT", "E_TOOL", "E_CRITERION", "E_BUDGET",
        "E_PERMISSION", "E_EFFECT", "E_AMBIGUOUS"}, str(FAILURE_CLASSES))
    check("只有 E_TOOL/E_CRITERION 可自动重试",
          set(RETRYABLE) == {"E_TOOL", "E_CRITERION"}, str(RETRYABLE))
    expected_dest = {"E_SPEC": "PROPOSED", "E_INPUT": "BLOCKED", "E_TOOL": "REJECTED",
                     "E_CRITERION": "EXECUTING", "E_BUDGET": "FAILED",
                     "E_PERMISSION": "BLOCKED", "E_EFFECT": "FAILED",
                     "E_AMBIGUOUS": "UNKNOWN"}
    for cls, dest in expected_dest.items():
        check(f"{cls} 默认去向 {dest}", DEFAULT_DESTINATION.get(cls) == dest,
              str(DEFAULT_DESTINATION.get(cls)))
        check(f"destination_for({cls}) == {dest}", destination_for(cls) == dest)

    # 签名归一化：同一种错误的不同表面写法必须同签名
    a = build_signature("E_TOOL", {"step": "s1", "tool": "write_file"},
                        free_text="Permission denied: C:/tmp/a/b.txt 2026-10-22 10:00:00")
    b = build_signature("E_TOOL", {"step": "s1", "tool": "write_file"},
                        free_text="permission denied: D:/other/b.txt 2026-10-23 11:22:33")
    check("同种错误同签名（路径/时间戳被归一）", a == b, f"{a} vs {b}")
    c = build_signature("E_TOOL", {"step": "s1", "tool": "write_file"},
                        free_text="disk full")
    check("不同错误不同签名", a != c)
    d = build_signature("E_TOOL", {"tool": "write_file", "step": "s1"}, free_text="disk full")
    check("签名与参数顺序无关", c == d, f"{c} vs {d}")
    e = build_signature("E_CRITERION", {"criterion": "c1"}, free_text="disk full")
    check("不同类别签名不同", c != e)

    check("normalize 折叠空白", normalize_error_text("  A   B  ") == "a b",
          repr(normalize_error_text("  A   B  ")))
    check("normalize 去数字", "<n>" in normalize_error_text("exit code 137"),
          normalize_error_text("exit code 137"))
    check("normalize 去时间戳", "<ts>" in normalize_error_text("at 2026-10-22T10:00:00Z"),
          normalize_error_text("at 2026-10-22T10:00:00Z"))
    check("normalize 去 uuid",
          "<id>" in normalize_error_text("id 3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
          normalize_error_text("id 3f2504e0-4f89-11d3-9a0c-0305e82c3301"))
    check("normalize 保留路径 basename",
          "b.txt" in normalize_error_text("C:\\tmp\\a\\b.txt"),
          normalize_error_text("C:\\tmp\\a\\b.txt"))
    check("normalize 幂等", normalize_error_text(normalize_error_text("A  1 2")) ==
          normalize_error_text("A  1 2"))

    f = classify("E_TOOL", {"step": "s1", "tool": "write_file"}, free_text="permission denied")
    check("classify 返回 Failure", isinstance(f, Failure))
    check("E_TOOL 可重试", f.retryable is True)
    check("to_feedback 三键", set(f.to_feedback()) == {"class", "signature", "evidence"},
          str(sorted(f.to_feedback())))
    check("to_feedback.class 值正确", f.to_feedback()["class"] == "E_TOOL",
          str(f.to_feedback().get("class")))
    j = f.to_feedback()
    check("to_feedback 可直接 JSON 序列化", isinstance(j["signature"], str) and
          isinstance(j["evidence"], str))
    check("E_EFFECT 不可重试", classify("E_EFFECT").retryable is False)
    check("E_AMBIGUOUS 不可直接重试", classify("E_AMBIGUOUS").retryable is False)
    try:
        classify("E_UNKNOWN")
    except ValueError:
        check("未知类别抛 ValueError", True)
    else:
        check("未知类别抛 ValueError", False, "没有抛")


# ---------- H 重试与确认策略 ----------

def test_h_retry() -> None:
    print("[H] 重试与确认策略（§4.7）")
    d = task_retry_decision(attempt=0, max_retries=3, failure_signature="S1",
                            last_failure_signature="", has_failed_required=True,
                            human_required_count=0)
    check("首次打回可重试", d.action == "retry" and d.attempt_after == 1,
          f"{d.action}/{d.attempt_after}")
    d = task_retry_decision(attempt=1, max_retries=3, failure_signature="S1",
                            last_failure_signature="S1", has_failed_required=True,
                            human_required_count=0)
    check("同签名不重试", d.action == "block" and "签名" in d.reason,
          f"{d.action}/{d.reason}")
    check("同签名不消耗次数", d.attempt_after == 1, str(d.attempt_after))
    d = task_retry_decision(attempt=3, max_retries=3, failure_signature="S9",
                            last_failure_signature="S8", has_failed_required=True,
                            human_required_count=0)
    check("次数耗尽转阻塞", d.action == "block" and "3" in d.reason, d.reason)
    d = task_retry_decision(attempt=0, max_retries=3, failure_signature="S1",
                            last_failure_signature="", has_failed_required=False,
                            human_required_count=0)
    check("无失败 required 不重试", d.action == "block", d.reason)
    d = task_retry_decision(attempt=0, max_retries=3, failure_signature="S1",
                            last_failure_signature="", has_failed_required=True,
                            human_required_count=2)
    check("有 human 标准不重试", d.action == "block" and "human" in d.reason.lower(), d.reason)

    d = step_retry_decision(retries=0, max_step_retries=2, failure_signature="S1",
                            last_failure_signature="", retryable=True)
    check("Step 首次可重做", d.action == "retry" and d.attempt_after == 1,
          f"{d.action}/{d.attempt_after}")
    d = step_retry_decision(retries=1, max_step_retries=2, failure_signature="S1",
                            last_failure_signature="S1", retryable=True)
    check("Step 同签名不重做", d.action == "block" and d.attempt_after == 1,
          f"{d.action}/{d.attempt_after}")
    d = step_retry_decision(retries=2, max_step_retries=2, failure_signature="S9",
                            last_failure_signature="S1", retryable=True)
    check("Step 次数耗尽", d.action == "block" and "2" in d.reason, d.reason)
    d = step_retry_decision(retries=0, max_step_retries=2, failure_signature="S1",
                            last_failure_signature="", retryable=False)
    check("Step 不可重试错误直接阻塞", d.action == "block", d.reason)

    # 两个数的口径不能混（§4.4 末「重试次数口径」）
    d = task_retry_decision(attempt=0, max_retries=3, failure_signature="A",
                            last_failure_signature="", has_failed_required=True,
                            human_required_count=0)
    check("Task 重试数的是验收打回次数", d.attempt_after == 1)
    d2 = step_retry_decision(retries=0, max_step_retries=2, failure_signature="A",
                             last_failure_signature="", retryable=True)
    check("Step 重试与 Task 重试是两个口径", d2.attempt_after == 1 and d2.action == "retry")

    # 确认策略
    check("默认策略是始终显式确认", START_POLICIES[0] == "always_explicit", str(START_POLICIES))
    check("默认不允许自动开工（§4.7 推荐 a）",
          can_auto_start(risk="low") is False)
    check("low_risk_countdown 允许 low 无副作用无高亮",
          can_auto_start(risk="low", policy="low_risk_countdown") is True)
    check("low_risk_countdown 拒绝 medium",
          can_auto_start(risk="medium", policy="low_risk_countdown") is False)
    check("low_risk_countdown 拒绝有副作用",
          can_auto_start(risk="low", side_effects=(eff(),),
                         policy="low_risk_countdown") is False)
    check("low_risk_countdown 拒绝高亮槽位",
          can_auto_start(risk="low", highlighted_slots=("a",),
                         policy="low_risk_countdown") is False)
    check("countdown_hint_only 不自动开工",
          can_auto_start(risk="low", policy="countdown_hint_only") is False)
    try:
        can_auto_start(risk="low", policy="whatever")
    except ValueError:
        check("未知策略抛 ValueError", True)
    else:
        check("未知策略抛 ValueError", False, "没有抛")


# ---------- I 派发后不得重放 ----------

def test_i_replay() -> None:
    print("[I] 派发后不得重放（§4.8）")
    check("无 receipt 可重派",
          replay_decision(DispatchEvidence(has_receipt=False)) == REPLAY_REPLAN)
    check("有 receipt + 幂等 + 远端去重确认 -> 同键重放",
          replay_decision(DispatchEvidence(has_receipt=True, idempotent=True,
                                           remote_dedup_confirmed=True)) == REPLAY_SAME_KEY)
    check("有 receipt 但不幂等 -> UNKNOWN",
          replay_decision(DispatchEvidence(has_receipt=True)) == REPLAY_UNKNOWN)
    check("有 receipt + 幂等但远端未确认 -> UNKNOWN",
          replay_decision(DispatchEvidence(has_receipt=True, idempotent=True)) == REPLAY_UNKNOWN)
    check("requires_reconciliation 与 UNKNOWN 一致",
          requires_reconciliation(DispatchEvidence(has_receipt=True)) is True and
          requires_reconciliation(DispatchEvidence(has_receipt=False)) is False)
    check("UNKNOWN 只有两个安全出口", tuple(unknown_exits()) == ("OBSERVED", "READY"),
          str(unknown_exits()))
    check("出口就是 §4.5 的 S13/S14",
          set(unknown_exits()) == {"OBSERVED", "READY"})
    # 三种证据的穷举：只有一种情况允许重放
    combos = []
    for receipt in (False, True):
        for idem in (False, True):
            for dedup in (False, True):
                combos.append(((receipt, idem, dedup),
                               replay_decision(DispatchEvidence(receipt, idem, dedup))))
    replay_ok = [c for c, d in combos if d == REPLAY_SAME_KEY]
    replan_ok = [c for c, d in combos if d == REPLAY_REPLAN]
    check("8 种证据组合里只有 1 种允许重放", len(replay_ok) == 1, str(replay_ok))
    check("只要没有 receipt 就允许重新规划（另两个标志无关）",
          all(c[0] is False for c in replan_ok) and len(replan_ok) == 4, str(replan_ok))
    check("其余 3 种一律 UNKNOWN", len(combos) - len(replay_ok) - len(replan_ok) == 3,
          str([c for c, d in combos if d == REPLAY_UNKNOWN]))


# ---------- J 台账投影 ----------

def test_j_projection() -> None:
    print("[J] 台账投影（§4.3）")
    table = {
        "CLARIFYING": (False, ""),
        "PROPOSED": (True, "drafting"),
        "SPEC_LOCKED": (True, "briefing"),
        "DISPATCHED": (True, "briefing"),
        "EXECUTING": (True, "running"),
        "VERIFYING": (True, "auditing"),
        "ACCEPTED": (True, "ready_to_report"),
        "DELIVERING": (True, "ready_to_report"),
        "DELIVERED": (True, "done"),
    }
    for status, (has_row, ledger) in table.items():
        p = project_task(task(status))
        check(f"{status} 投影为 {ledger or '无行'}",
              p.has_row == has_row and p.status == ledger, f"{p.has_row}/{p.status}")
        if p.has_row:
            check(f"{status} 投影是合法台账状态", p.status in LEDGER_STATUSES, p.status)

    p = project_task(task("BLOCKED", blocked_reason="need_clarification"))
    check("BLOCKED(need_clarification) -> blocked/clarification",
          p.status == "blocked" and p.blocked_kind == "clarification",
          f"{p.status}/{p.blocked_kind}")
    for reason in ("need_approval", "need_authorization", "retry_exhausted", "ambiguous_result"):
        p = project_task(task("BLOCKED", blocked_reason=reason))
        check(f"BLOCKED({reason}) -> blocked/approval",
              p.status == "blocked" and p.blocked_kind == "approval",
              f"{p.status}/{p.blocked_kind}")

    p = project_task(task("CANCELLED"))
    check("CANCELLED 落 done + audit_kind=cancelled",
          p.status == "done" and p.audit_kind == "cancelled", f"{p.status}/{p.audit_kind}")
    p = project_task(task("DELIVERED"))
    check("DELIVERED 落 done 且无 audit_kind", p.status == "done" and p.audit_kind == "",
          f"{p.status}/{p.audit_kind}")
    p = project_task(task("FAILED"))
    check("FAILED 默认落 blocked(approval)+audit=failed",
          p.status == "blocked" and p.blocked_kind == "approval" and p.audit_kind == "failed",
          f"{p.status}/{p.blocked_kind}/{p.audit_kind}")
    p = project_task(task("FAILED"), failed_to="done")
    check("FAILED 可选落 done", p.status == "done" and p.audit_kind == "failed",
          f"{p.status}/{p.audit_kind}")
    p = project_task(task("TIMEOUT"))
    check("TIMEOUT 落 blocked + audit=timeout",
          p.status == "blocked" and p.audit_kind == "timeout",
          f"{p.status}/{p.audit_kind}")

    try:
        project_task(task("FAILED"), failed_to="running")
    except ValueError:
        check("failed_to 非法值抛 ValueError", True)
    else:
        check("failed_to 非法值抛 ValueError", False, "没有抛")

    # 取值域外的输入：规范未定义（§6.6 只定义了五个合法 reason 的映射）。
    # 台账层只接受 clarification/approval（threads.py:694），所以必须二选一。
    # 取 approval 的依据是失败方向不对称：多要一次点头 < 吞掉真实的授权需求。
    # 待拍：规范未定义此情形，第一条锁的是「当前判断」，不是规范原文。
    check("blocked_kind_of 未知原因 fail-safe 到 approval（规范未定义，见注释）",
          blocked_kind_of("nonsense") == "approval", blocked_kind_of("nonsense"))
    check("blocked_kind_of 未知原因不返回空串（否则台账写库会 ValueError）",
          blocked_kind_of("nonsense") in ("clarification", "approval"))
    check("台账状态集不含 v2 状态值",
          not (set(TASK_STATES) & set(LEDGER_STATUSES)),
          str(sorted(set(TASK_STATES) & set(LEDGER_STATUSES))))
    check("ready_to_report 不等于终态（§4.3 铁律）",
          "ready_to_report" not in ("done",))
    for status in TASK_STATES:
        p = project_task(task(status))
        if p.status:
            check(f"{status} 投影 status 合法", p.status in LEDGER_STATUSES, p.status)


# ---------- K 边界 ----------

def test_k_boundaries() -> None:
    print("[K] 边界断言")
    pkg = ROOT / "server" / "task"
    check("server/task 目录存在", pkg.is_dir())
    for name in ("__init__.py", "states.py", "errors.py", "paths.py", "model.py", "machine.py",
                 "failures.py", "retry.py", "replay.py", "projection.py"):
        check(f"文件存在 {name}", (pkg / name).is_file())

    business = []
    event_literals = []
    io_calls = []
    for path in sorted(pkg.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "server" or alias.name.startswith("server."):
                        business.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "server" or
                                    node.module.startswith("server.")):
                    business.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and key.value == "type":
                        event_literals.append(f"{path.name}:{node.lineno}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("socket", "sqlite3", "requests", "urllib", "http"):
                        io_calls.append(f"{path.name}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module in (
                    "socket", "sqlite3", "requests", "urllib", "http"):
                io_calls.append(f"{path.name}:{node.module}")
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", "") or getattr(func, "attr", "")
                if name in ("open", "print", "input"):
                    io_calls.append(f"{path.name}:{name}()")
    check("零业务 import", business == [], str(business))
    check("无 type 键字典字面量（防协议误报）", event_literals == [], str(event_literals))
    check("零 IO / 零打印", io_calls == [], str(io_calls))

    # 状态对象不可变
    st = TaskState()
    try:
        st.status = "DELIVERED"  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - dataclasses.FrozenInstanceError
        check("TaskState 不可变", True)
    else:
        check("TaskState 不可变", False, "赋值成功")
    sp = StepState()
    try:
        sp.status = "VERIFIED"  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        check("StepState 不可变", True)
    else:
        check("StepState 不可变", False, "赋值成功")

    # 错误类型继承关系：调用方能靠 ValueError 统一兜住
    check("IllegalTransition 是 ValueError", issubclass(IllegalTransition, ValueError))
    check("GuardRejected 是 ValueError", issubclass(GuardRejected, ValueError))
    check("两者共同基类 TransitionError", issubclass(IllegalTransition, TransitionError) and
          issubclass(GuardRejected, TransitionError))
    check("StateValueError 是 ValueError", issubclass(StateValueError, ValueError))

    # 不 import server.contract / server.assistant / server.interaction
    for forbidden in ("server.contract", "server.assistant", "server.interaction",
                      "server.dialogue", "server.memory"):
        hits = [b for b in business if forbidden in b]
        check(f"不 import {forbidden}", hits == [], str(hits))


def main() -> int:
    test_a_states()
    test_b_task_edges()
    test_c_step_edges()
    test_d_exhaustive()
    test_e_property()
    test_f_acceptance_cases()
    test_g_failures()
    test_h_retry()
    test_i_replay()
    test_j_projection()
    test_k_boundaries()

    print()
    print("=" * 72)
    if failed:
        print(f"FAILED {len(failed)}/{passed + len(failed)}")
        for item in failed:
            print("  - " + item)
        return 1
    print(f"PASS {passed}/{passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
