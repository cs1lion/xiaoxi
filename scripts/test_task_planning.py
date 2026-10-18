"""结构化任务规划验收（协议 §25）：有界、可验收、可恢复、可修订的串行计划。

四层验证，对应需求的"评估与测试"十条：
  A 单元     计划模型本身：形状校验、预算上限、验收判定、失败分类、序列化往返
  B 集成     AgentLoop 计划驱动：真实执行、确认门、事件流水、checkpoint
  C 纵向     恢复与修订：中断后不重跑、用户改目标后只丢待办、重复事件不双执行
  D 反例     低质量计划必须被拒，而不是变成一段编号文字混进执行链

明确边界：planner 是脚本化的，因此证明的是**计划机制**正确，
不证明真实模型能拆出好计划。后者需按 docs/EVOLUTION_QUALITY_EVAL.md
同类的"机制通过 ≠ 语义合格"方式另做 live 评测。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.assistant.loop import AgentLoop, Tool
from server.assistant.planning import (
    MAX_PLAN_REVISIONS, MAX_PLAN_STEPS, MAX_STEP_ATTEMPTS, MAX_TITLE, Plan, PlanStep,
    check_acceptance, classify_failure, normalize_plan, plan_from_dict, plan_report,
    plan_status_line, record_attempt, revise_plan,
)
from server.assistant.runtime import RuntimeStore


def spec(steps, goal='把这份周报整理成三版并保存', completion='all_steps_done'):
    return {'goal': goal, 'completion': completion, 'steps': steps}


def step(sid, title, **kw):
    base = {'id': sid, 'title': title}
    base.update(kw)
    return base


# ---------- A. 单元：计划模型 ----------

class PlanModelTests(unittest.TestCase):
    def test_simple_goal_stays_one_step(self):
        """简单目标不该被拆成多步——计划层不制造复杂度。"""
        plan, reason = normalize_plan(spec([step('s1', '查一下明天的天气')]), goal='明天天气如何')
        self.assertIsNotNone(plan, reason)
        self.assertEqual(len(plan.steps), 1)

    def test_rejects_missing_steps(self):
        for bad in ({}, {'steps': []}, {'steps': 'x'}, {'goal': ''}):
            plan, reason = normalize_plan(bad, goal='目标')
            self.assertIsNone(plan, f'{bad} 不该被接受')

    def test_rejects_step_without_title(self):
        plan, reason = normalize_plan(spec([{'id': 's1'}]), goal='目标')
        self.assertIsNone(plan)
        self.assertEqual(reason, 'step_title_missing')

    def test_rejects_too_many_steps(self):
        many = [step(f's{i}', f'第{i}步') for i in range(MAX_PLAN_STEPS + 1)]
        plan, reason = normalize_plan(spec(many), goal='目标')
        self.assertIsNone(plan)
        self.assertEqual(reason, 'too_many_steps')

    def test_rejects_unknown_dependency_and_self_dependency(self):
        plan, _ = normalize_plan(spec([step('s1', '一', depends_on=['s9'])]), goal='g')
        self.assertIsNone(plan)
        plan, _ = normalize_plan(spec([step('s1', '一', depends_on=['s1'])]), goal='g')
        self.assertIsNone(plan)

    def test_rejects_duplicate_step_ids(self):
        plan, reason = normalize_plan(spec([step('s1', '一'), step('s1', '二')]), goal='g')
        self.assertIsNone(plan)
        self.assertEqual(reason, 'duplicate_step_id')

    def test_oversized_title_is_truncated_not_rejected(self):
        """标题超长按上限截断（内容字段的既有约定），不是整体拒绝。"""
        plan, reason = normalize_plan(spec([step('s1', 'x' * 200)]), goal='g')
        self.assertIsNotNone(plan, reason)
        self.assertEqual(len(plan.steps[0].title), MAX_TITLE)
        plan, _ = normalize_plan(spec([step('s1', '标题', acceptance=[{}])]), goal='g')
        self.assertIsNotNone(plan)  # 空规则被剔除，不是整体拒绝
        plan, _ = normalize_plan(spec([step('s1', '标题', acceptance=['notdict'])]), goal='g')
        self.assertIsNotNone(plan)

    def test_plan_round_trips_through_json(self):
        """checkpoint 是 JSON：计划必须原样往返，否则恢复后会悄悄变成无计划。"""
        plan, reason = normalize_plan(spec([
            step('s1', '查资料', acceptance=[{'status': 'succeeded'}]),
            step('s2', '写稿', depends_on=['s1'], acceptance=[{'field': 'data.id', 'exists': True}]),
        ]), goal='写一篇调研')
        self.assertIsNotNone(plan, reason)
        restored = plan_from_dict(json.loads(json.dumps(plan.as_dict())))
        self.assertIsNotNone(restored)
        self.assertEqual(restored.as_dict(), plan.as_dict())
        self.assertEqual(restored.next_step().id, 's1')

    def test_unreadable_plan_degrades_to_none(self):
        self.assertIsNone(plan_from_dict(None))
        self.assertIsNone(plan_from_dict({'steps': []}))
        self.assertIsNone(plan_from_dict({'goal': 'g', 'steps': [{'id': 's1'}]}))  # 无 title

    def test_step_attempt_budget_is_enforced(self):
        plan, _ = normalize_plan(spec([step('s1', '一')]), goal='g')
        for _ in range(MAX_STEP_ATTEMPTS):
            plan, status, _why = record_attempt(plan, 's1', receipt={
                'status': 'failed', 'error_code': 'transient'})
            self.assertEqual(status, 'failed')
        self.assertTrue(plan.steps[0].exhausted)
        # 超过上限后不再接受新的 attempt 记录
        plan2, status, _ = record_attempt(plan, 's1', receipt={'status': 'succeeded'})
        self.assertEqual(status, 'failed')
        self.assertEqual(plan2, plan)


class AcceptanceTests(unittest.TestCase):
    """验收优先：worker 说 succeeded 不等于这一步完成。"""

    def setUp(self):
        self.plan, _ = normalize_plan(spec([
            step('s1', '建任务', acceptance=[{'status': 'succeeded'},
                                             {'field': 'data.id', 'exists': True}]),
            step('s2', '凑数', acceptance=[{'field': 'data.n', 'min': 3}]),
            step('s3', '精确值', acceptance=[{'field': 'data.kind', 'equals': 'note'}]),
            step('s4', '不许失败', acceptance=[{'not_status': 'failed'}]),
            step('s5', '没写条件'),
        ]), goal='g')

    def ok(self, sid, **data):
        return check_acceptance(self.plan.step(sid), {'status': 'succeeded', 'data': data})

    def test_all_rules_must_pass(self):
        self.assertEqual(self.ok('s1', id='t1'), (True, 'accepted'))
        self.assertEqual(self.ok('s1'), (False, 'data.id missing'))
        self.assertEqual(self.ok('s2', n=2), (False, 'data.n < 3'))
        self.assertEqual(self.ok('s2', n=5), (True, 'accepted'))
        self.assertEqual(self.ok('s3', kind='note'), (True, 'accepted'))
        self.assertEqual(self.ok('s3', kind='task'), (False, 'data.kind != note'))
        self.assertEqual(self.ok('s4'), (True, 'accepted'))

    def test_failed_receipt_never_passes(self):
        self.assertEqual(check_acceptance(self.plan.step('s5'),
                                          {'status': 'failed', 'error_code': 'x'}),
                         (False, 'default_status'))
        self.assertIsNotNone(check_acceptance(self.plan.step('s1'), None))

    def test_no_rules_falls_back_to_status(self):
        self.assertEqual(self.ok('s5', anything=1), (True, 'default_status'))


class FailureClassificationTests(unittest.TestCase):
    def test_four_classes_are_distinct(self):
        cases = {
            'unknown_tool': 'retryable', 'invalid_arguments': 'retryable',
            'timeout': 'retryable',
            'needs_approval': 'needs_user', 'budget_exceeded': 'needs_user',
            'irreversible': 'needs_user',
            'interrupted': 'uncertain', 'executor_lost': 'uncertain',
            'rejected': 'terminal', 'cancelled': 'terminal',
            'weird_new_code': 'retryable',
        }
        for code, expected in cases.items():
            self.assertEqual(classify_failure({'status': 'failed', 'error_code': code}), expected, code)

    def test_uncertain_is_never_auto_retried(self):
        """结果不确定绝不能自动重放——这是 recover_interrupted 的同一条铁律。"""
        self.assertEqual(classify_failure({'error_code': 'interrupted'}), 'uncertain')

    def test_exhausted_step_becomes_needs_user(self):
        step_obj = PlanStep(id='s1', title='一', attempts=MAX_STEP_ATTEMPTS)
        self.assertEqual(classify_failure({'error_code': 'transient'}, step_obj), 'needs_user')


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.plan, _ = normalize_plan(spec([
            step('s1', '查资料', acceptance=[{'status': 'succeeded'}]),
            step('s2', '写初稿', depends_on=['s1']),
            step('s3', '排版', depends_on=['s2']),
        ]), goal='写调研')

    def test_done_steps_survive_a_goal_change(self):
        plan, _status, _why = record_attempt(self.plan, 's1', receipt={
            'status': 'succeeded', 'data': {'id': 'x'}})
        updated, outcome = revise_plan(plan, goal='改成写一份简短摘要', note='用户缩小范围')
        self.assertEqual(outcome, 'revised')
        self.assertEqual(updated.goal, '改成写一份简短摘要')
        self.assertEqual(updated.step('s1').status, 'done', '已完成的步骤是事实，不因改主意消失')
        self.assertEqual(len(updated.revisions), 1)
        self.assertIn('goal', updated.revisions[0]['changes'])

    def test_only_pending_steps_are_dropped(self):
        plan, _s, _w = record_attempt(self.plan, 's1', receipt={
            'status': 'succeeded', 'data': {'id': 'x'}})
        updated, outcome = revise_plan(plan, drop_steps=['s1', 's3'], note='去掉排版')
        self.assertEqual(outcome, 'revised')
        self.assertEqual(updated.step('s1').status, 'done', '已 done 的不许被丢')
        self.assertEqual(updated.step('s3').status, 'skipped')

    def test_added_steps_append_after_the_last_one(self):
        updated, outcome = revise_plan(self.plan, add_steps=[step('s4', '加一步校对')])
        self.assertEqual(outcome, 'revised')
        self.assertEqual(updated.step('s4').depends_on, ('s3',))

    def test_no_op_revision_is_reported(self):
        updated, outcome = revise_plan(self.plan, note='只是看看')
        self.assertEqual(outcome, 'no_change')
        self.assertEqual(updated, self.plan)

    def test_revision_history_is_bounded(self):
        plan = self.plan
        for i in range(MAX_PLAN_REVISIONS + 3):
            plan, outcome = revise_plan(plan, goal=f'目标{i}', note='改')
        self.assertEqual(outcome, 'revision_limit')
        self.assertLessEqual(len(plan.revisions), MAX_PLAN_REVISIONS)

    def test_revision_cannot_exceed_step_budget(self):
        many = [step(f'x{i}', f'第{i}步') for i in range(MAX_PLAN_STEPS + 2)]
        updated, outcome = revise_plan(self.plan, add_steps=many)
        self.assertEqual(outcome, 'revised')
        self.assertLessEqual(len(updated.steps), MAX_PLAN_STEPS)


class ReportTests(unittest.TestCase):
    def test_report_states_what_ran_what_failed_and_residual_risk(self):
        plan, _ = normalize_plan(spec([
            step('s1', '查资料', acceptance=[{'status': 'succeeded'}]),
            step('s2', '写稿', depends_on=['s1']),
            step('s3', '排版', depends_on=['s2']),
        ]), goal='写调研')
        plan, _s, _w = record_attempt(plan, 's1', receipt={'status': 'succeeded', 'data': {'id': 'n1'}})
        for _ in range(MAX_STEP_ATTEMPTS):
            plan, _s, _w = record_attempt(plan, 's2', receipt={
                'status': 'failed', 'error_code': 'interrupted'})
        report = plan_report(plan)
        self.assertFalse(report['complete'])
        self.assertTrue(report['blocked'])
        self.assertEqual(report['counts']['done'], 1)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertEqual([item['id'] for item in report['completed']], ['s1'])
        self.assertEqual(report['failed'][0]['reason'], 'uncertain')
        self.assertTrue(any('结果不确定' in risk for risk in report['risks']),
                        '不确定的步骤必须在剩余风险里点名，且不声称已重放')
        self.assertTrue(any('还没轮到' in risk for risk in report['risks']))

    def test_status_line_reports_progress_not_internals(self):
        plan, _ = normalize_plan(spec([step('s1', '查资料'), step('s2', '写稿')]), goal='写调研')
        line = plan_status_line(plan)
        self.assertIn('写调研', line)
        self.assertIn('第 1/2 步', line)
        self.assertNotIn('acceptance', line, '状态行不朗读内部字段')


# ---------- B. 集成：AgentLoop 计划驱动 ----------

class ScriptedPlanner:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        return self.payload


class PlanLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-plan-')
        self.runtime = RuntimeStore(str(Path(self.temp.name) / 'runtime.db'))
        self.calls = []

    async def asyncTearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def tool(self, name='task.create', mutation=False, handler=None):
        def default(**kwargs):
            self.calls.append((name, kwargs))
            return {'id': 'task_1', 'title': kwargs.get('title', '')}
        return Tool(name, name, {'title': str}, ('title',), handler or default, mutation)

    def model(self, decisions):
        async def call(_):
            return decisions.pop(0)
        return call

    def loop(self, decisions, planner, tools=None, **kw):
        return AgentLoop(self.model(list(decisions)), tools or [self.tool()],
                         self.runtime, planner=planner, **kw)

    TWO_STEPS = spec([
        step('s1', '建任务', acceptance=[{'status': 'succeeded'}, {'field': 'data.id', 'exists': True}]),
        step('s2', '再建一个', depends_on=['s1'],
             acceptance=[{'status': 'succeeded'}, {'field': 'data.id', 'exists': True}]),
    ])

    async def test_real_steps_execute_not_just_text(self):
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '第一步'}},
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '第二步'}},
            {'action': 'final', 'text': '两件都建好了。'},
        ], ScriptedPlanner(self.TWO_STEPS))
        result = await loop.start('建两个任务')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([title for _n, kw in self.calls for title in kw.values()],
                         ['第一步', '第二步'])
        report = result['plan']
        self.assertEqual(report['counts']['done'], 2)
        self.assertTrue(report['complete'])
        self.assertEqual(report['completed'][0]['evidence'], [0], '成果要能追溯到 observation')

    async def test_acceptance_gates_completion_not_success_status(self):
        """worker 返回 succeeded 但缺 data.id → 这一步不算完成。"""
        def bad(**kwargs):
            return {'title': 'no id here'}
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'x'}},
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'y'}},
            {'action': 'final', 'text': '好了。'},
        ], ScriptedPlanner(self.TWO_STEPS), tools=[self.tool(handler=bad)], max_steps=6)
        result = await loop.start('建两个任务')
        self.assertEqual(result['status'], 'blocked', result)
        self.assertEqual(result['plan']['counts']['done'], 0)
        self.assertEqual(result['plan']['failed'][0]['attempts'], MAX_STEP_ATTEMPTS)

    async def test_simple_goal_is_not_over_decomposed(self):
        """planner 给一步且无验收信息 → 退回自由循环，不当成计划。"""
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '一件'}},
            {'action': 'final', 'text': '建好了。'},
        ], ScriptedPlanner(spec([step('s1', '直接处理')])))
        result = await loop.start('建个任务')
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('plan', result, '平凡计划不该出现')
        run = self.runtime.get(result['run_id'])
        self.assertIsNone(run['state']['plan'])
        self.assertEqual(run['state']['plan_reason'], 'plan_trivial')

    async def test_default_status_acceptance_is_still_trivial(self):
        """只写 {"status":"succeeded"} 的单步计划没有信息量，问候不该变计划。"""
        loop = self.loop([
            {'action': 'final', 'text': '在的。'},
        ], ScriptedPlanner(spec([step('s1', '回复问候',
                                      acceptance=[{'status': 'succeeded'}])])))
        result = await loop.start('在吗')
        self.assertEqual(result['status'], 'completed')
        run = self.runtime.get(result['run_id'])
        self.assertIsNone(run['state']['plan'])
        self.assertEqual(run['state']['plan_reason'], 'plan_trivial')

    async def test_single_step_with_acceptance_is_still_a_plan(self):
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '一件'}},
            {'action': 'final', 'text': '建好了。'},
        ], ScriptedPlanner(spec([step('s1', '建任务',
                                      acceptance=[{'field': 'data.id', 'exists': True}])])))
        result = await loop.start('建个任务')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['plan']['counts']['done'], 1)

    async def test_mutation_still_requires_confirmation_inside_a_plan(self):
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '第一步'}},
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '第二步'}},
            {'action': 'final', 'text': '两件都建好了。'},
        ], ScriptedPlanner(self.TWO_STEPS), tools=[self.tool(mutation=True)])
        result = await loop.start('建两个任务')
        self.assertEqual(result['status'], 'waiting_confirmation')
        self.assertEqual(self.calls, [], '未批准不执行')
        self.assertEqual(result['plan_step'], 's1')
        done = await loop.resume(result['run_id'], result['confirmation']['id'], True)
        self.assertEqual(done['status'], 'waiting_confirmation', '第二步同样要批准')
        self.assertEqual(len(self.calls), 1, '只执行了已批准的那一步')

    async def test_premature_final_does_not_count_as_done(self):
        """还有步骤没跑就说"做完了"：按阻塞交回，不当成完成。"""
        loop = self.loop([{'action': 'final', 'text': '都搞定了。'}],
                         ScriptedPlanner(self.TWO_STEPS), max_steps=6)
        result = await loop.start('建两个任务')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['plan']['counts']['done'], 0)

    async def test_plan_is_persisted_in_the_checkpoint(self):
        loop = self.loop([{'action': 'final', 'text': '好了。'}],
                         ScriptedPlanner(self.TWO_STEPS), max_steps=2, max_tools=0)
        result = await loop.start('建两个任务')
        run = self.runtime.get(result['run_id'])
        stored = run['state']['plan']
        self.assertIsInstance(stored, dict, 'checkpoint 里必须是可 JSON 化的 dict')
        json.dumps(stored)
        self.assertEqual(plan_from_dict(stored).goal, '建两个任务',
                         '计划目标用用户原话，不用 planner 自己写的 goal 字段')

    async def test_broken_planner_degrades_to_free_loop(self):
        def boom(_):
            raise RuntimeError('planner down')
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'x'}},
            {'action': 'final', 'text': '建好了。'},
        ], boom)
        result = await loop.start('建个任务')
        self.assertEqual(result['status'], 'completed')
        run = self.runtime.get(result['run_id'])
        self.assertEqual(run['state']['plan_reason'], 'planner_error:RuntimeError')

    async def test_invalid_plan_shape_degrades_to_free_loop(self):
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'x'}},
            {'action': 'final', 'text': '建好了。'},
        ], ScriptedPlanner({'steps': [{'id': 's1'}]}))   # 缺 title
        result = await loop.start('建个任务')
        self.assertEqual(result['status'], 'completed')
        run = self.runtime.get(result['run_id'])
        self.assertEqual(run['state']['plan_reason'], 'step_title_missing')

    async def test_events_record_the_plan_lifecycle(self):
        loop = self.loop([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'x'}},
            {'action': 'final', 'text': '好了。'},
        ], ScriptedPlanner(spec([step('s1', '建任务',
                                      acceptance=[{'field': 'data.id', 'exists': True}])])))
        result = await loop.start('建个任务')
        kinds = [event['kind'] for event in self.runtime.events(result['run_id'])]
        self.assertIn('plan_step_started', kinds)
        self.assertIn('plan_step_finished', kinds)
        self.assertIn('plan_completed', kinds)


# ---------- C. 纵向：恢复与修订 ----------

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-plan-rec-')
        self.runtime = RuntimeStore(str(Path(self.temp.name) / 'runtime.db'))
        self.calls = []

    async def asyncTearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def tool(self, mutation=False):
        def handler(title):
            self.calls.append(title)
            return {'id': 'task_1', 'title': title}
        return Tool('task.create', 'task.create', {'title': str}, ('title',), handler, mutation)

    def model(self, decisions):
        async def call(_):
            return decisions.pop(0)
        return call

    async def test_interrupted_plan_resumes_without_redoing_finished_steps(self):
        planner = ScriptedPlanner(spec([
            step('s1', '建任务一', acceptance=[{'field': 'data.id', 'exists': True}]),
            step('s2', '建任务二', depends_on=['s1'],
                 acceptance=[{'field': 'data.id', 'exists': True}]),
        ]))
        loop = AgentLoop(self.model([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '一'}},
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '二'}},
            {'action': 'final', 'text': '两件都建好了。'},
        ]), [self.tool(mutation=True)], self.runtime, planner=planner)
        result = await loop.start('建两个任务')
        self.assertEqual(result['status'], 'waiting_confirmation')
        # 批准第一步后手动中断（模拟进程被杀）
        await loop.resume(result['run_id'], result['confirmation']['id'], True)
        run = self.runtime.get(result['run_id'])
        self.assertEqual(run['status'], 'waiting_confirmation')
        self.assertEqual(len(self.calls), 1)

        # 重启：同一 runtime，新 loop（模拟新进程）
        self.runtime.save(run['id'], 'interrupted', run['state'])
        loop2 = AgentLoop(self.model([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '二'}},
            {'action': 'final', 'text': '第二件也建好了。'},
        ]), [self.tool(mutation=True)], self.runtime, planner=planner)
        resumed = await loop2.resume_plan(run['id'])
        self.assertEqual(resumed['status'], 'waiting_confirmation', '第二步仍要批准')
        self.assertEqual(self.calls, ['一'], '已成功的步骤没有被重放')
        done = await loop2.resume(resumed['run_id'], resumed['confirmation']['id'], True)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(self.calls, ['一', '二'])
        self.assertEqual(done['plan']['counts']['done'], 2)

    async def test_resume_refuses_finished_and_waiting_runs(self):
        planner = ScriptedPlanner(spec([step('s1', '建任务',
                                             acceptance=[{'field': 'data.id', 'exists': True}])]))
        loop = AgentLoop(self.model([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'x'}},
            {'action': 'final', 'text': '建好了。'},
        ]), [self.tool()], self.runtime, planner=planner)
        result = await loop.start('建个任务')
        self.assertEqual(result['status'], 'completed')
        with self.assertRaises(ValueError):
            await loop.resume_plan(result['run_id'])

    async def test_repeated_events_do_not_double_execute(self):
        """同一个 mutation 动作重复出现：第二次被 completed_actions 去重。"""
        planner = ScriptedPlanner(spec([
            step('s1', '建任务', acceptance=[{'field': 'data.id', 'exists': True}]),
            step('s2', '重复同一件', depends_on=['s1'],
                 acceptance=[{'not_status': 'failed'}]),
        ]))
        loop = AgentLoop(self.model([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '同一件'}},
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '同一件'}},
            {'action': 'final', 'text': '好了。'},
        ]), [self.tool(mutation=True)], self.runtime, planner=planner)
        result = await loop.start('建任务')
        self.assertEqual(result['status'], 'waiting_confirmation')
        result = await loop.resume(result['run_id'], result['confirmation']['id'], True)
        self.assertEqual(self.calls, ['同一件'], '重复动作没有产生第二次副作用')
        self.assertEqual(result['status'], 'completed')
        receipts = result['receipts']
        self.assertEqual(receipts[1]['status'], 'skipped')
        self.assertEqual(receipts[1]['error_code'], 'already_executed')

    async def test_budget_bounds_the_plan(self):
        """工具预算耗尽时停下并报 blocked，不无限扩张。"""
        planner = ScriptedPlanner(spec([
            step('s1', '一', acceptance=[{'status': 'succeeded'}]),
            step('s2', '二', depends_on=['s1']),
            step('s3', '三', depends_on=['s2']),
        ]))
        loop = AgentLoop(self.model([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'x'}},
        ] * 10), [self.tool()], self.runtime, planner=planner, max_steps=4, max_tools=2)
        result = await loop.start('做三件事')
        self.assertEqual(result['status'], 'blocked', result)
        self.assertLessEqual(result['steps'], 4, '步数不能超过预算')
        self.assertLessEqual(result['plan']['counts']['done'], 2, '工具预算用尽就不该继续跑')
        self.assertGreaterEqual(result['plan']['counts']['pending'], 1, '没跑完的步骤要报出来')
        self.assertTrue(result['plan']['risks'], '没跑完的步骤必须进剩余风险')


# ---------- D. 反例：低质量计划必须被发现 ----------

BAD_PLANS = {
    'no_steps': {'goal': 'g'},
    'empty_steps': {'goal': 'g', 'steps': []},
    'step_without_title': {'goal': 'g', 'steps': [{'id': 's1'}]},
    'too_many_steps': {'goal': 'g', 'steps': [step(f's{i}', f'第{i}步')
                                              for i in range(MAX_PLAN_STEPS + 1)]},
    'dangling_dependency': {'goal': 'g', 'steps': [step('s1', '一', depends_on=['ghost'])]},
    'duplicate_ids': {'goal': 'g', 'steps': [step('s1', '一'), step('s1', '二')]},
    'not_an_object': ['s1', 's2'],
}


class BadPlanRegressionTests(unittest.TestCase):
    def test_every_bad_plan_is_refused_with_a_reason(self):
        for name, payload in BAD_PLANS.items():
            with self.subTest(case=name):
                plan, reason = normalize_plan(payload, goal='把这件事办成')
                self.assertIsNone(plan, f'{name} 被当成有效计划')
                self.assertTrue(reason, '拒绝必须带理由，便于排查')

    async def test_bad_plan_never_enters_the_execution_chain(self):
        """坏计划不能让 AgentLoop 变成"计划驱动"——必须退回自由循环。"""
        temp = tempfile.TemporaryDirectory(prefix='aqi-plan-bad-')
        try:
            runtime = RuntimeStore(str(Path(temp.name) / 'rt.db'))
            calls = []

            def handler(title):
                calls.append(title)
                return {'id': 't', 'title': title}

            async def model(_):
                return {'action': 'tool_call', 'tool': 'task.create',
                        'arguments': {'title': 'x'}}

            decisions = [{'action': 'tool_call', 'tool': 'task.create',
                          'arguments': {'title': 'x'}}, {'action': 'final', 'text': '好了。'}]

            async def scripted(_):
                return decisions.pop(0)

            loop = AgentLoop(scripted,
                             [Tool('task.create', 't', {'title': str}, ('title',), handler, False)],
                             runtime, planner=ScriptedPlanner(BAD_PLANS['dangling_dependency']))
            result = await loop.start('做点事')
            self.assertEqual(result['status'], 'completed')
            run = runtime.get(result['run_id'])
            self.assertIsNone(run['state']['plan'], '坏计划不该进入执行链')
            self.assertEqual(run['state']['plan_reason'], 'unknown_dependency')
            runtime.close()
        finally:
            temp.cleanup()


# ---------- E. API：路由校验与真实 HTTP 链路 ----------

class PlanApiTests(unittest.TestCase):
    """走真实 FastAPI 路由，确认计划能经 HTTP 创建、修订、续跑，且坏输入被拒。"""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from types import SimpleNamespace as NS
        from server.assistant.api import create_router
        from server.assistant.service import AssistantService

        self.temp = tempfile.TemporaryDirectory(prefix='aqi-plan-api-')
        self.service = AssistantService(Path(self.temp.name), NS(offline=True), None)
        self.service.memory_manager = NS(enabled=True)
        app = FastAPI()
        app.include_router(create_router(self.service))
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.service.close()
        self.temp.cleanup()

    def _inject(self):
        async def planner(_payload):
            return {'steps': [{'id': 's1', 'title': '建一个周报任务',
                               'acceptance': [{'field': 'data.id', 'exists': True}]}]}

        async def decide(_payload):
            return {'action': 'tool_call', 'tool': 'task.create',
                    'arguments': {'title': '周报'}}
        self.service.loop.planner = planner
        self.service.loop.model = decide

    def test_plan_run_revise_and_continue_over_http(self):
        self._inject()
        run = self.client.post('/api/assistant/run',
                               json={'text': '建个周报任务', 'session_id': 's1'})
        self.assertEqual(run.status_code, 200, run.text)
        body = run.json()
        self.assertEqual(body['status'], 'waiting_confirmation')
        # 前端要能说"第几步要你确认"，而不是只弹一句不知所云的话
        self.assertIn('plan_status', body)
        self.assertIn('1/1', body['plan_status'])
        run_id = body['run_id']

        state = self.client.get(f'/api/assistant/runs/{run_id}').json()
        self.assertIn('plan', state['state'], 'checkpoint 必须留下计划')

        noop = self.client.post(f'/api/assistant/runs/{run_id}/revise', json={'note': '看看'})
        self.assertEqual(noop.json()['outcome'], 'no_change')

        revised = self.client.post(f'/api/assistant/runs/{run_id}/revise',
                                   json={'goal': '改成建月报',
                                         'add_steps': [{'id': 's2', 'title': '再建月报'}]})
        self.assertEqual(revised.status_code, 200, revised.text)
        self.assertEqual(revised.json()['outcome'], 'revised')
        self.assertEqual(len(revised.json()['plan']['revisions']), 1)

        again = self.client.post(f'/api/assistant/runs/{run_id}/continue')
        self.assertEqual(again.status_code, 200, again.text)

    def test_bad_revision_payloads_are_rejected(self):
        self._inject()
        run = self.client.post('/api/assistant/run',
                               json={'text': '建个周报任务', 'session_id': 's1'}).json()
        run_id = run['run_id']
        for payload in ({'goal': 'x' * 20000}, {'add_steps': ['notdict']},
                        {'add_steps': [{}] * 9}, {'drop_steps': [123]}, {'note': 'y' * 300}):
            with self.subTest(payload=list(payload)[0]):
                response = self.client.post(f'/api/assistant/runs/{run_id}/revise', json=payload)
                self.assertEqual(response.status_code, 400, response.text)

    def test_unknown_run_is_404(self):
        self.assertEqual(self.client.post('/api/assistant/runs/nope/revise',
                                          json={'note': 'x'}).status_code, 404)
        self.assertEqual(self.client.post('/api/assistant/runs/nope/continue').status_code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
