"""Persistent clarification continuations: temporary databases and fake models only.

Never imports server.app/config, reads user databases, or calls a model provider.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.loop import AgentLoop, Tool
from server.assistant.runtime import RuntimeStore
from server.assistant.service import AssistantService


class Offline:
    offline = True


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = AssistantService(Path(self.temp.name), Offline())

    async def asyncTearDown(self):
        self.service.close()
        self.temp.cleanup()

    def decisions(self, values, captured=None):
        async def model(payload):
            if captured is not None:
                captured.append(payload)
            return values.pop(0)
        self.service.loop.model = model

    async def test_answer_continues_original_run_then_confirms_exact_action(self):
        captured = []
        self.decisions([
            {'action': 'clarify', 'text': '明天几点提醒你？'},
            {'action': 'tool_call', 'tool': 'reminder.create',
             'arguments': {'content': '提交周报', 'scheduled_at': 4102441200}},
            {'action': 'final', 'text': '提醒已设置。'},
        ], captured)
        first = await self.service.start('明天提醒我提交周报', 'session-a')
        self.assertEqual(first['status'], 'waiting_input')
        self.assertFalse(self.service.should_route('下午三点'))
        self.assertEqual(self.service.waiting_input('session-a')['run_id'], first['run_id'])
        second = await self.service.start('下午三点', 'session-a')
        self.assertEqual(second['run_id'], first['run_id'])
        self.assertEqual(second['status'], 'waiting_confirmation')
        self.assertEqual(self.service.store.list_reminders(), [])
        self.assertEqual(captured[1]['user_text'], '明天提醒我提交周报')
        self.assertEqual(captured[1]['latest_user_text'], '下午三点')
        self.assertEqual([m['content'] for m in captured[1]['input_history']],
                         ['明天提醒我提交周报', '明天几点提醒你？', '下午三点'])
        done = await self.service.resume(second['run_id'], second['confirmation']['id'], True)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(len(self.service.store.list_reminders()), 1)
        self.assertEqual(done['receipts'][0]['data']['scheduled_at'], 4102441200)
        with self.assertRaises(ValueError):
            await self.service.resume(second['run_id'], second['confirmation']['id'], True)

    async def test_cancel_does_not_call_model_or_undo_existing_receipt(self):
        self.decisions([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '周报'}},
            {'action': 'clarify', 'text': '还要安排几点的提醒？'},
        ])
        first = await self.service.start('创建周报任务，再安排提醒', 's')
        waiting = await self.service.resume(first['run_id'], first['confirmation']['id'], True)
        cancelled = await self.service.start('取消这次操作', 's')
        self.assertEqual(cancelled['run_id'], waiting['run_id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        self.assertEqual(cancelled['receipts'][0]['status'], 'succeeded')
        self.assertIsNone(self.service.waiting_input('s'))
        state = self.service.runtime.get(first['run_id'])['state']
        self.assertEqual(state['input_history'][-1]['content'], '取消这次操作')

    async def test_sessions_and_anonymous_calls_never_share_input(self):
        self.decisions([{'action': 'clarify', 'text': '几点？'} for _ in range(4)])
        first = await self.service.start('提醒我', 'a')
        second = await self.service.start('提醒我', 'b')
        self.assertNotEqual(first['run_id'], second['run_id'])
        with self.assertRaisesRegex(ValueError, 'session_mismatch'):
            await self.service.loop.continue_input(first['run_id'], '三点', session_id='b')
        self.assertEqual(self.service.waiting_input('a')['run_id'], first['run_id'])
        anonymous = await self.service.start('提醒我')
        other_anonymous = await self.service.start('下午三点')
        self.assertNotEqual(anonymous['run_id'], other_anonymous['run_id'])
        self.assertIsNone(self.service.waiting_input(''))

    async def test_waiting_query_is_read_only_and_expired_input_cannot_resume(self):
        now = [100.0]
        self.service.runtime.clock = lambda: now[0]
        self.service.loop.input_ttl = 10
        self.decisions([{'action': 'clarify', 'text': '几点？'}])
        first = await self.service.start('提醒我', 'a')
        before = self.service.runtime.get(first['run_id'])
        events = self.service.runtime.events(first['run_id'])
        self.assertIsNotNone(self.service.waiting_input('a'))
        self.assertEqual(before, self.service.runtime.get(first['run_id']))
        self.assertEqual(events, self.service.runtime.events(first['run_id']))
        now[0] = 110.0
        self.assertIsNone(self.service.waiting_input('a'))
        with self.assertRaisesRegex(ValueError, 'input_expired'):
            await self.service.loop.continue_input(first['run_id'], '三点', session_id='a')
        self.assertEqual(self.service.runtime.get(first['run_id'])['status'], 'input_expired')

    async def test_restart_preserves_wait_and_never_repeats_successful_mutation(self):
        action = {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '周报'}}
        self.decisions([action, {'action': 'clarify', 'text': '接下来几点提醒？'}])
        first = await self.service.start('创建周报任务，再安排提醒', 's')
        waiting = await self.service.resume(first['run_id'], first['confirmation']['id'], True)
        self.service.close()
        self.service = AssistantService(Path(self.temp.name), Offline())
        captured = []
        self.decisions([action, {'action': 'final', 'text': '周报任务已存在，继续使用它。'}], captured)
        self.assertEqual(self.service.waiting_input('s')['run_id'], waiting['run_id'])
        done = await self.service.start('暂时不要提醒，只保留任务', 's')
        self.assertEqual(done['run_id'], waiting['run_id'])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        self.assertEqual(done['receipts'][1]['error_code'], 'already_executed')
        self.assertEqual(self.service.runtime.get(done['run_id'])['state']['tool_count'], 1)
        self.assertEqual(captured[0]['observations'][0]['status'], 'succeeded')

    async def test_claim_then_crash_marks_interrupted_and_does_not_restore_wait(self):
        self.decisions([{'action': 'clarify', 'text': '几点？'}])
        first = await self.service.start('提醒我', 's')
        self.service.runtime.claim_input(first['run_id'], 's', '三点')
        self.service.close()
        self.service = AssistantService(Path(self.temp.name), Offline())
        self.assertIsNone(self.service.waiting_input('s'))
        self.assertEqual(self.service.runtime.get(first['run_id'])['status'], 'interrupted')
        self.assertEqual(self.service.store.list_reminders(), [])
        with self.assertRaisesRegex(ValueError, 'run_not_waiting_input'):
            await self.service.loop.continue_input(first['run_id'], '三点', session_id='s')

    async def test_two_concurrent_answers_claim_only_one_run(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def model(payload):
            calls.append(payload['latest_user_text'])
            if len(calls) == 1:
                return {'action': 'clarify', 'text': '几点？'}
            entered.set()
            await release.wait()
            return {'action': 'final', 'text': '收到。'}
        self.service.loop.model = model
        first = await self.service.start('提醒我', 's')
        answering = asyncio.create_task(self.service.start('三点', 's'))
        await entered.wait()
        duplicate = await self.service.start('四点', 's')
        self.assertEqual(duplicate['status'], 'input_busy')
        self.assertEqual(duplicate['run_id'], first['run_id'])
        release.set()
        await answering
        self.assertEqual(calls, ['提醒我', '三点'])
        state = self.service.runtime.get(first['run_id'])['state']
        self.assertNotIn('四点', [m['content'] for m in state['input_history']])

    async def test_refreshes_preferences_without_switching_companion_target(self):
        first_thread = self.service.threads.create('原事项')
        other = self.service.threads.create('其他事项')
        self.service.participation.select(first_thread['id'])
        evidence = self.service.preference_store.add_evidence('技术问题详细解释')
        self.service.preference_store.create_claim('detail', 'detailed', [evidence['id']],
                                                   scope={'activity': 'technical'})
        captured = []
        self.decisions([{'action': 'clarify', 'text': '任务叫什么？'},
                        {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '调试'}},
                        {'action': 'final', 'text': '已保存。'}], captured)
        first = await self.service.start('为Python调试创建任务', 's')
        self.assertEqual(captured[0]['context']['response_policy']['length'], 'detailed')
        self.service.participation.select(other['id'])
        self.service.preference_store.delete_evidence(evidence['id'])
        confirmation = await self.service.start('调试', 's')
        resumed_context = captured[1]['context']
        self.assertEqual(resumed_context['response_policy']['length'], 'short')
        self.assertEqual(resumed_context['companion_thread']['id'], first_thread['id'])
        self.assertNotIn('companion_thread', resumed_context['response_policy'])
        done = await self.service.resume(first['run_id'], confirmation['confirmation']['id'], True)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(len(self.service.threads.get(first_thread['id'])['events']), 1)
        self.assertEqual(self.service.threads.get(other['id'])['events'], [])

    async def test_originally_unselected_run_does_not_inherit_later_selection(self):
        captured = []
        self.decisions([{'action': 'clarify', 'text': '几点？'},
                        {'action': 'final', 'text': '收到。'}], captured)
        await self.service.start('提醒我', 's')
        other = self.service.threads.create('别的事项')
        self.service.participation.select(other['id'])
        await self.service.start('三点', 's')
        self.assertIsNone(captured[1]['context']['companion_thread'])
        self.assertNotIn('companion_thread', captured[1]['context']['response_policy'])

    async def test_followup_gets_decision_budget_but_retains_tool_budget(self):
        self.service.loop.max_steps = 1
        self.decisions([{'action': 'clarify', 'text': '几点？'},
                        {'action': 'final', 'text': '收到。'}])
        first = await self.service.start('提醒我', 's')
        done = await self.service.start('三点', 's')
        self.assertEqual(done['run_id'], first['run_id'])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['steps'], 2)

    async def test_followup_cannot_reset_tool_budget(self):
        self.service.loop.max_tools = 1
        self.decisions([
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '第一项'}},
            {'action': 'clarify', 'text': '第二项叫什么？'},
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '第二项'}},
        ])
        first = await self.service.start('创建一个任务，再创建第二项', 's')
        await self.service.resume(first['run_id'], first['confirmation']['id'], True)
        limited = await self.service.start('第二项', 's')
        self.assertEqual(limited['status'], 'limit_reached')
        self.assertNotIn('confirmation', limited)
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        self.assertEqual(self.service.runtime.get(first['run_id'])['state']['tool_count'], 1)

    async def test_deleted_original_thread_cannot_be_replaced_on_followup(self):
        original = self.service.threads.create('原事项')
        other = self.service.threads.create('其他事项')
        self.service.participation.select(original['id'])
        captured = []
        self.decisions([{'action': 'clarify', 'text': '几点？'},
                        {'action': 'final', 'text': '收到。'}], captured)
        await self.service.start('提醒我', 's')
        self.service.threads.delete(original['id'], expected_revision=original['revision'])
        self.service.participation.select(other['id'])
        await self.service.start('三点', 's')
        self.assertIsNone(captured[1]['context']['companion_thread'])
        self.assertEqual(captured[1]['context']['companion_thread_id'], original['id'])
        self.assertNotIn('companion_thread', captured[1]['context']['response_policy'])

    def test_cancel_syntax_is_explicit_and_whole_utterance(self):
        self.assertTrue(AgentLoop.cancels_input('算了。'))
        for text in ('取消明天的提醒', '他说“取消”', '假设我取消', '取消之后再建一个'):
            self.assertFalse(AgentLoop.cancels_input(text), text)

    async def test_input_round_limit_ends_wait_without_additional_model_call(self):
        self.service.loop.max_input_rounds = 1
        self.decisions([{'action': 'clarify', 'text': '几点？'},
                        {'action': 'clarify', 'text': '哪个时区？'}])
        first = await self.service.start('提醒我', 's')
        await self.service.start('三点', 's')
        done = await self.service.start('北京时间', 's')
        self.assertEqual(done['run_id'], first['run_id'])
        self.assertEqual(done['status'], 'limit_reached')
        self.assertIsNone(self.service.waiting_input('s'))

    async def test_cancelled_model_cannot_return_a_new_confirmation(self):
        entered = asyncio.Event()
        async def resistant_model(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '不应创建'}}
        self.service.loop.model = resistant_model
        turn = asyncio.create_task(self.service.start('创建任务', 's'))
        await entered.wait()
        run_id = self.service.runtime.db.execute('SELECT id FROM agent_runs').fetchone()['id']
        turn.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await turn
        self.assertEqual(self.service.runtime.get(run_id)['status'], 'interrupted')
        self.assertEqual(self.service.store.list_tasks(), [])
        self.assertEqual(self.service.runtime.db.execute('SELECT COUNT(*) FROM agent_confirmations').fetchone()[0], 0)

    async def test_cancel_after_success_keeps_receipt_and_stops_final(self):
        entered = asyncio.Event()
        calls = []
        async def resistant_model(_):
            calls.append(True)
            if len(calls) == 1:
                return {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '已执行'}}
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {'action': 'final', 'text': '不应保存为完成'}
        self.service.loop.model = resistant_model
        first = await self.service.start('创建任务', 's')
        resume = asyncio.create_task(self.service.resume(first['run_id'], first['confirmation']['id'], True))
        await entered.wait()
        resume.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await resume
        run = self.service.runtime.get(first['run_id'])
        self.assertEqual(run['status'], 'interrupted')
        self.assertEqual(run['state']['observations'][0]['status'], 'succeeded')
        self.assertEqual(len(run['state']['completed_actions']), 1)
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        with self.assertRaises(ValueError):
            await self.service.resume(first['run_id'], first['confirmation']['id'], True)

    async def test_cancelled_async_handler_return_preserves_success_without_new_planning(self):
        entered = asyncio.Event()
        effects = []
        async def resistant_handler(title):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                effects.append(title)
                return {'title': title}
        async def model(_):
            return {'action': 'tool_call', 'tool': 'write.test', 'arguments': {'title': '真实副作用'}}
        tool = Tool('write.test', 'Synthetic test', {'title': str}, ('title',), resistant_handler, True)
        loop = AgentLoop(model, [tool], self.service.runtime)
        first = await loop.start('test', {'interaction_session_id': 's'})
        resume = asyncio.create_task(loop.resume(first['run_id'], first['confirmation']['id'], True))
        await entered.wait()
        resume.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await resume
        run = self.service.runtime.get(first['run_id'])
        self.assertEqual(run['status'], 'interrupted')
        self.assertEqual(effects, ['真实副作用'])
        self.assertEqual(run['state']['observations'][0]['status'], 'succeeded')
        self.assertEqual(len(run['state']['completed_actions']), 1)
        self.assertEqual(len(self.service.runtime.events(first['run_id'])), 3)


class RuntimeClaimTests(unittest.TestCase):
    def test_atomic_input_claim_across_database_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.db'
            runtime = RuntimeStore(path)
            run_id = runtime.create({'user_text': '原请求', 'context': {'interaction_session_id': 's'},
                                     'steps': 1, 'observations': [], 'input_history': []})
            state = runtime.get(run_id)['state']
            state['last_clarification'] = '几点？'
            runtime.wait_for_input(run_id, state)
            other = RuntimeStore(path)
            barrier = threading.Barrier(2)
            def claim(store, text):
                barrier.wait()
                try:
                    store.claim_input(run_id, 's', text)
                    return 'claimed'
                except ValueError as exc:
                    return str(exc)
            try:
                with ThreadPoolExecutor(max_workers=2) as workers:
                    one = workers.submit(claim, runtime, '三点')
                    two = workers.submit(claim, other, '四点')
                    results = [one.result(), two.result()]
                self.assertEqual(results.count('claimed'), 1)
                self.assertEqual(results.count('run_not_waiting_input'), 1)
                self.assertEqual(len(runtime.get(run_id)['state']['input_history']), 1)
                self.assertEqual(len(runtime.events(run_id)), 1)
                self.assertEqual(runtime.input_for_session('s')['status'], 'running')
            finally:
                other.close()
                runtime.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
