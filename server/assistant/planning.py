"""结构化任务计划（纯逻辑，零 IO、零模型调用）。

为什么值得单独存在：`AgentLoop` 已经是有界、可检查点、可恢复、可审批的
decide/act 循环，但它**没有计划对象**——"下一步做什么"只活在模型的上下文里，
于是无法回答"这一步的验收条件是什么""哪几步还有效""实际产出了什么"。

本模块把计划变成**可解释、可检查、可持久化的数据**，但它不自建执行引擎：
计划存进 `AgentLoop` 运行态（`agent_runs.checkpoint`），因此自动继承已有的
检查点/恢复、单次 CAS 审批、事件流水、取消与预算边界。不造平行计划系统。

设计约束（对应需求）：
  * 有界——步骤数、每步重试、计划修订数全部有上限，不会无限扩张。
  * 验收优先——步骤完成不看 worker 返回 success，而看 acceptance 条件。
  * 失败分类——可重试 / 需用户决策 / 结果不确定 / 终局，四类分开处理。
  * 串行优先——MVP 只支持线性依赖（depends_on 指向前一步），不引入 DAG。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# ---- 预算：所有上限都在这里，改一个数就够 ----
MAX_PLAN_STEPS = 6          # 一个计划最多几步（对应 AgentLoop.max_steps 的量级）
MAX_STEP_ATTEMPTS = 2       # 单步最大尝试次数（含首次）
MAX_PLAN_REVISIONS = 8      # 计划变更记录上限，防止用户反复改目标把历史撑爆
MAX_TITLE = 80
MAX_INPUT = 400
MAX_EXPECTED = 400
MAX_ACCEPTANCE = 6
MAX_DEPENDS = 4
MAX_REASON = 200

STEP_STATES = ('pending', 'running', 'done', 'failed', 'skipped', 'blocked')
TERMINAL_STEPS = ('done', 'skipped')
# 只有这几种失败值得重试；其余要么该问用户，要么结果不确定不能重放。
RETRYABLE_CODES = ('unknown_tool', 'invalid_arguments', 'transient', 'timeout')
NEEDS_USER_CODES = ('needs_approval', 'ambiguous_goal', 'budget_exceeded', 'irreversible')
UNCERTAIN_CODES = ('interrupted', 'executor_lost', 'uncertain')
TERMINAL_CODES = ('rejected', 'cancelled', 'unsupported')


def _text(value: Any, limit: int, *, allow_empty: bool = False) -> str | None:
    if not isinstance(value, str):
        return None if not allow_empty else ''
    stripped = value.strip()
    if not stripped and not allow_empty:
        return None
    return stripped[:limit]


@dataclass(frozen=True)
class PlanStep:
    """计划里的一步。`acceptance` 是数据化的验收条件，不是自然语言描述。"""

    id: str
    title: str
    expected: str = ''
    step_input: str = ''
    acceptance: tuple[dict[str, Any], ...] = ()
    executor: str = ''            # 期望的执行器/工具名；空表示由模型自选
    depends_on: tuple[str, ...] = ()
    status: str = 'pending'
    attempts: int = 0
    evidence: tuple[int, ...] = ()   # 指向 run observations 的下标
    result: str = ''
    failure: str = ''               # 失败分类码

    def validate(self) -> str | None:
        if not self.id or len(self.id) > 64:
            return 'step_id_invalid'
        if not self.title:
            return 'step_title_missing'
        if self.status not in STEP_STATES:
            return 'step_status_invalid'
        if self.attempts < 0 or self.attempts > MAX_STEP_ATTEMPTS:
            return 'step_attempts_invalid'
        if len(self.depends_on) > MAX_DEPENDS:
            return 'step_depends_too_many'
        if self.id in self.depends_on:
            return 'step_depends_on_self'
        if len(self.acceptance) > MAX_ACCEPTANCE:
            return 'step_acceptance_too_many'
        for rule in self.acceptance:
            if not isinstance(rule, dict) or not rule:
                return 'acceptance_rule_invalid'
        return None

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_STEP_ATTEMPTS


@dataclass(frozen=True)
class Plan:
    """一个可追踪的工作计划。整个对象可 JSON 序列化，直接存进 checkpoint。"""

    goal: str
    steps: tuple[PlanStep, ...] = ()
    revisions: tuple[dict[str, Any], ...] = ()
    completion: str = 'all_steps_done'

    def validate(self) -> str | None:
        if not self.goal:
            return 'plan_goal_missing'
        if not 1 <= len(self.steps) <= MAX_PLAN_STEPS:
            return 'plan_step_count'
        if len(self.revisions) > MAX_PLAN_REVISIONS:
            return 'plan_revisions_overflow'
        seen: set[str] = set()
        for step in self.steps:
            reason = step.validate()
            if reason:
                return reason
            if step.id in seen:
                return 'duplicate_step_id'
            seen.add(step.id)
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in seen:
                    return 'unknown_dependency'
        if self.completion not in ('all_steps_done', 'first_success'):
            return 'plan_completion_invalid'
        return None

    # ---- 查询 ----

    def step(self, step_id: str) -> PlanStep | None:
        return next((item for item in self.steps if item.id == step_id), None)

    @property
    def done_ids(self) -> set[str]:
        return {item.id for item in self.steps if item.status == 'done'}

    def ready_steps(self) -> list[PlanStep]:
        """依赖已满足、还没结束、且还有重试余量的步骤（按原顺序）。

        重试余量用完的失败步骤**不算 ready**——它卡住了，后继也无从推进，
        于是 `next_step()` 为 None 而计划未完成，`is_blocked` 才能如实报"需要人决定"。
        """
        finished = self.done_ids | {item.id for item in self.steps if item.status == 'skipped'}
        ready = []
        for step in self.steps:
            if step.status in TERMINAL_STEPS:
                continue
            if step.status == 'failed' and step.exhausted:
                continue
            if any(dep not in finished for dep in step.depends_on):
                continue
            ready.append(step)
        return ready

    def next_step(self) -> PlanStep | None:
        ready = self.ready_steps()
        return ready[0] if ready else None

    @property
    def is_complete(self) -> bool:
        if self.completion == 'first_success':
            return any(item.status == 'done' for item in self.steps)
        return all(item.status in TERMINAL_STEPS for item in self.steps)

    @property
    def is_blocked(self) -> bool:
        """没有可推进的步骤，但计划还没完成——需要用户决策。"""
        return not self.is_complete and self.next_step() is None

    def counts(self) -> dict[str, int]:
        result = {state: 0 for state in STEP_STATES}
        for step in self.steps:
            result[step.status] += 1
        return result

    # ---- 序列化：checkpoint 是 JSON，计划必须能原样往返 ----

    def as_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的 plain dict（存进 agent_runs.checkpoint）。"""
        return {
            'goal': self.goal,
            'completion': self.completion,
            'revisions': [dict(item) for item in self.revisions],
            'steps': [{
                'id': step.id, 'title': step.title, 'expected': step.expected,
                'input': step.step_input, 'acceptance': [dict(r) for r in step.acceptance],
                'executor': step.executor, 'depends_on': list(step.depends_on),
                'status': step.status, 'attempts': step.attempts,
                'evidence': list(step.evidence), 'result': step.result, 'failure': step.failure,
            } for step in self.steps],
        }


def plan_from_dict(value: Any) -> Plan | None:
    """从 checkpoint 还原 Plan；形状不符返回 None（调用方按"无计划"处理）。"""
    if not isinstance(value, dict):
        return None
    raw_steps = value.get('steps')
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    steps = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            return None
        steps.append(PlanStep(
            id=str(raw.get('id') or ''), title=str(raw.get('title') or ''),
            expected=str(raw.get('expected') or ''),
            step_input=str(raw.get('input') or ''),
            acceptance=tuple(dict(r) for r in (raw.get('acceptance') or []) if isinstance(r, dict)),
            executor=str(raw.get('executor') or ''),
            depends_on=tuple(str(item) for item in (raw.get('depends_on') or [])),
            status=str(raw.get('status') or 'pending'),
            attempts=int(raw.get('attempts') or 0),
            evidence=tuple(int(item) for item in (raw.get('evidence') or [])
                           if isinstance(item, int)),
            result=str(raw.get('result') or ''), failure=str(raw.get('failure') or '')))
    plan = Plan(goal=str(value.get('goal') or ''),
                steps=tuple(steps),
                revisions=tuple(dict(item) for item in (value.get('revisions') or [])
                                if isinstance(item, dict)),
                completion=str(value.get('completion') or 'all_steps_done'))
    return plan if plan.validate() is None else None


def normalize_plan(payload: Any, *, goal: str, max_steps: int = MAX_PLAN_STEPS
                   ) -> tuple[Plan | None, str | None]:
    """把模型提出的计划规格收敛成可执行 Plan；(None, reason) 或 (plan, None)。

    只接受显式形状：步骤必须有 id/title，依赖必须指向已声明的步骤，
    验收条件必须是数据。缺一项就整体拒绝——不让"看起来像计划的一段文字"混进来。
    """
    if not isinstance(payload, dict):
        return None, 'payload_not_object'
    clean_goal = _text(goal, 400)
    if not clean_goal:
        return None, 'goal_missing'
    raw_steps = payload.get('steps')
    if not isinstance(raw_steps, list) or not raw_steps:
        return None, 'steps_missing'
    limit = max(1, min(int(max_steps), MAX_PLAN_STEPS))
    if len(raw_steps) > limit:
        return None, 'too_many_steps'
    steps: list[PlanStep] = []
    for index, raw in enumerate(raw_steps[:limit]):
        if not isinstance(raw, dict):
            return None, 'step_not_object'
        step_id = _text(raw.get('id'), 64) or f's{index + 1}'
        title = _text(raw.get('title'), MAX_TITLE)
        if not title:
            return None, 'step_title_missing'
        raw_rules = raw.get('acceptance')
        rules: list[dict[str, Any]] = []
        if isinstance(raw_rules, list):
            for rule in raw_rules[:MAX_ACCEPTANCE]:
                if isinstance(rule, dict) and rule:
                    rules.append(dict(rule))
        raw_deps = raw.get('depends_on')
        deps = tuple(_text(item, 64) for item in
                     (raw_deps if isinstance(raw_deps, list) else [])[:MAX_DEPENDS]
                     if _text(item, 64))
        steps.append(PlanStep(
            id=step_id, title=title,
            expected=_text(raw.get('expected'), MAX_EXPECTED) or '',
            step_input=_text(raw.get('input'), MAX_INPUT) or '',
            acceptance=tuple(rules),
            executor=_text(raw.get('executor'), 64) or '',
            depends_on=deps))
    plan = Plan(goal=clean_goal, steps=tuple(steps),
                completion=payload.get('completion')
                if payload.get('completion') in ('all_steps_done', 'first_success')
                else 'all_steps_done')
    reason = plan.validate()
    if reason:
        return None, reason
    return plan, None


def classify_failure(receipt: dict[str, Any] | None, step: PlanStep | None = None) -> str:
    """把一次失败归类，决定下一步是重试、问用户、还是标记不确定。

    结果不确定（interrupted / executor_lost）**绝不自动重放**——那是
    `RuntimeStore.recover_interrupted` 的同一条哲学。
    """
    code = ''
    if isinstance(receipt, dict):
        code = str(receipt.get('error_code') or receipt.get('failure') or '').strip()
    if code in UNCERTAIN_CODES:
        return 'uncertain'
    if code in NEEDS_USER_CODES:
        return 'needs_user'
    if code in TERMINAL_CODES:
        return 'terminal'
    # 可重试的错误码 + 重试余量已经用完 = 再试也不会成功，只能问人。
    if step is not None and step.exhausted:
        return 'needs_user'
    if code in RETRYABLE_CODES:
        return 'retryable'
    return 'retryable'


def check_acceptance(step: PlanStep, receipt: dict[str, Any] | None) -> tuple[bool, str]:
    """按步骤的 acceptance 条件判定一次执行结果是否可用。

    验收优先：worker 返回 succeeded 不等于这一步完成。条件全部通过才判 done。
    支持的规则形状（数据化，便于测试与审计）：
      {"status": "succeeded"}                   receipts 的 status 必须相等
      {"field": "data.id", "exists": true}      取值路径必须存在且非空
      {"field": "data.n", "min": 1}             数值下限
      {"not_status": "failed"}                  状态不得为该值
    """
    if not isinstance(receipt, dict):
        return False, 'no_receipt'
    if not step.acceptance:
        # 没写验收条件时，退回最小判据：worker 自己说成功。
        return (receipt.get('status') == 'succeeded'), 'default_status'
    for rule in step.acceptance:
        if 'status' in rule and receipt.get('status') != rule['status']:
            return False, f"status!={rule['status']}"
        if 'not_status' in rule and receipt.get('status') == rule['not_status']:
            return False, f"status=={rule['not_status']}"
        path = rule.get('field')
        if isinstance(path, str) and path:
            value: Any = receipt
            for part in path.split('.'):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    value = None
                    break
            if rule.get('exists') and (value is None or value == '' or value == [] or value == {}):
                return False, f'{path} missing'
            if 'min' in rule:
                try:
                    if float(value) < float(rule['min']):
                        return False, f'{path} < {rule["min"]}'
                except (TypeError, ValueError):
                    return False, f'{path} not numeric'
            if 'equals' in rule and value != rule['equals']:
                return False, f'{path} != {rule["equals"]}'
    return True, 'accepted'


def record_attempt(plan: Plan, step_id: str, *, receipt: dict[str, Any] | None,
                   observation_index: int | None = None) -> tuple[Plan, str, str]:
    """把一次执行结果记到步骤上，返回 (新计划, 步骤状态, 说明)。

    纯函数：不改入参。验收通过 → done；不通过且还有余量 → failed(retryable)；
    不通过且已耗尽 → failed(其分类)。已结束的步骤不会被再次改写。
    """
    step = plan.step(step_id)
    if step is None or step.status in TERMINAL_STEPS:
        return plan, step.status if step else 'missing', 'no_change'
    if step.exhausted:
        # 尝试次数已用完：不再接受新的执行结果，交回 needs_user。
        return plan, 'failed', 'attempts_exhausted'
    accepted, why = check_acceptance(step, receipt)
    evidence = step.evidence + ((observation_index,) if observation_index is not None else ())
    if accepted:
        updated = replace(step, status='done', attempts=step.attempts + 1,
                          evidence=evidence, result=why, failure='')
        return _replace_step(plan, updated), 'done', why
    failure = classify_failure(receipt, step)
    updated = replace(step, status='failed', attempts=step.attempts + 1,
                      evidence=evidence, result='', failure=failure)
    return _replace_step(plan, updated), 'failed', f'{failure}:{why}'


def _replace_step(plan: Plan, step: PlanStep) -> Plan:
    return replace(plan, steps=tuple(step if item.id == step.id else item
                                     for item in plan.steps))


def revise_plan(plan: Plan, *, goal: str | None = None, add_steps: list[dict] | None = None,
                drop_steps: list[str] | None = None, note: str = '') -> tuple[Plan, str]:
    """用户改了目标/否决了结果时更新计划，并留下变更记录。

    只丢弃**尚未开始**的步骤；已 done 的步骤保留（它的成果是事实，不因为
    用户改主意而消失），但在 summary 里标注"可能已不适用"。绝不盲目重跑全计划。
    """
    if plan is None:
        raise ValueError('plan_required')
    revisions = list(plan.revisions)
    if len(revisions) >= MAX_PLAN_REVISIONS:
        return plan, 'revision_limit'
    changes: list[str] = []
    steps = list(plan.steps)
    if goal:
        clean = _text(goal, 400)
        if clean and clean != plan.goal:
            changes.append('goal')
            plan = replace(plan, goal=clean)
    if drop_steps:
        dropped = {str(item) for item in drop_steps}
        kept = []
        for step in steps:
            if step.id in dropped and step.status in ('pending', 'failed', 'blocked'):
                kept.append(replace(step, status='skipped', failure='dropped_by_user'))
                changes.append(f'drop:{step.id}')
            else:
                kept.append(step)
        steps = kept
    if add_steps:
        existing = {step.id for step in steps}
        last = steps[-1].id if steps else ''
        for index, raw in enumerate(add_steps[:MAX_PLAN_STEPS]):
            if not isinstance(raw, dict):
                continue
            step_id = _text(raw.get('id'), 64) or f'r{len(revisions) + 1}_{index}'
            if step_id in existing:
                continue
            title = _text(raw.get('title'), MAX_TITLE)
            if not title:
                continue
            steps.append(PlanStep(id=step_id, title=title,
                                  expected=_text(raw.get('expected'), MAX_EXPECTED) or '',
                                  step_input=_text(raw.get('input'), MAX_INPUT) or '',
                                  executor=_text(raw.get('executor'), 64) or '',
                                  depends_on=(last,) if last else ()))
            existing.add(step_id)
            changes.append(f'add:{step_id}')
            last = step_id
    if not changes:
        return plan, 'no_change'
    updated = replace(plan, steps=tuple(steps[:MAX_PLAN_STEPS]),
                      revisions=tuple(revisions + [{'note': note[:MAX_REASON],
                                                    'changes': changes}]))
    reason = updated.validate()
    if reason:
        return plan, f'invalid_after_revise:{reason}'
    return updated, 'revised'


def plan_report(plan: Plan) -> dict[str, Any]:
    """最终回报：实际跑了哪些、产出了什么、哪些失败/未执行、剩余风险。"""
    counts = plan.counts()
    done = [step for step in plan.steps if step.status == 'done']
    failed = [step for step in plan.steps if step.status == 'failed']
    skipped = [step for step in plan.steps if step.status == 'skipped']
    # running/pending/blocked 都算"还没跑完"：把 running 漏掉会让最终回报说谎
    # （步骤卡在 running 时，报告既不说它没跑，也不把它列进剩余风险）。
    pending = [step for step in plan.steps if step.status in ('pending', 'blocked', 'running')]
    risks: list[str] = []
    for step in pending:
        if step.status == 'running':
            risks.append(f'「{step.title}」还没跑完就被预算或中断叫停，结果未知。')
        else:
            risks.append(f'「{step.title}」还没轮到。')
    for step in failed:
        if step.failure == 'uncertain':
            risks.append(f'「{step.title}」结果不确定，未自动重放——需要人工确认后再继续。')
        elif step.failure == 'needs_user':
            risks.append(f'「{step.title}」需要你决策后才能继续。')
        else:
            risks.append(f'「{step.title}」失败（{step.failure or "unknown"}）。')
    for step in skipped:
        risks.append(f'「{step.title}」未执行。')
    return {
        'goal': plan.goal,
        'status_line': plan_status_line(plan),
        'complete': plan.is_complete,
        'blocked': plan.is_blocked,
        'counts': counts,
        'completed': [{'id': s.id, 'title': s.title, 'result': s.result,
                       'evidence': list(s.evidence)} for s in done],
        'failed': [{'id': s.id, 'title': s.title, 'reason': s.failure,
                    'attempts': s.attempts} for s in failed],
        'skipped': [{'id': s.id, 'title': s.title} for s in skipped],
        'pending': [{'id': s.id, 'title': s.title} for s in pending],
        'risks': risks,
        'revisions': list(plan.revisions),
    }


def plan_status_line(plan: Plan) -> str:
    """给语音/卡片用的一行状态：不朗读内部步骤，只报进度与下一步。"""
    counts = plan.counts()
    total = len(plan.steps)
    done = counts['done'] + counts['skipped']
    nxt = plan.next_step()
    if plan.is_complete:
        return f'「{plan.goal}」这件事办完了，{total} 步都走完。'
    if nxt is not None:
        return f'「{plan.goal}」推进到第 {done + 1}/{total} 步：{nxt.title}。'
    blocked = next((s for s in plan.steps if s.status == 'failed'), None)
    if blocked is not None:
        return f'「{plan.goal}」卡在「{blocked.title}」：{blocked.failure or "需要你定一下"}。'
    return f'「{plan.goal}」停在第 {done}/{total} 步，等你决定下一步。'


__all__ = [
    'MAX_PLAN_STEPS', 'MAX_STEP_ATTEMPTS', 'MAX_PLAN_REVISIONS', 'Plan', 'PlanStep',
    'check_acceptance', 'classify_failure', 'normalize_plan', 'plan_from_dict', 'plan_report',
    'plan_status_line', 'record_attempt', 'revise_plan',
]
