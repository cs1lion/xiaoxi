"""Offline evaluator contracts: real temporary storage, fake provider/transport.

No live invocation, .env, production key or user database is used. Fake model
outputs test the evaluation plumbing; they do not measure semantic accuracy.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_memory as target


def client_config(**changes):
    fields = dict(llm_provider='synthetic-compatible', llm_base_url='https://memory.invalid/v1',
                  llm_api_key='OFFLINE_EVAL_SECRET', llm_model='synthetic-model', llm_temperature=.1,
                  llm_max_tokens=800, llm_reasoning_effort='', llm_frequency_penalty=0,
                  llm_presence_penalty=0)
    fields.update(changes)
    return SimpleNamespace(**fields)


def setUpModule():
    """评测夹具是本地撰写的语料（随 /eval 清理被删、从未入 git）；缺失时整组跳过而非报红。"""
    fixture = Path(__file__).resolve().parents[1] / "eval/research/memory_live_cases.json"
    if not fixture.exists():
        raise unittest.SkipTest("缺少本地评测夹具 memory_live_cases.json（曾随 /eval 清理被删，未入 git）；补齐该文件后自动恢复")


class FakeProvider:
    """Only provider JSON is controlled; manager/extractor never get seeded gold."""
    def __init__(self):
        self.calls = []

    async def complete_raw(self, messages, **options):
        self.calls.append((copy.deepcopy(messages), dict(options)))
        source = json.loads(messages[1]['content'])['user_messages'][0]
        text, identifier = source['text'], source['id']
        items = []
        def add(key, value, *, operation='add', certainty='stated'):
            items.append({'kind': 'fact', 'subject': 'user', 'source': certainty,
                          'importance': .8, 'key': key, 'value': value, 'operation': operation,
                          'evidence': {'message_id': identifier, 'quote': text}})
        if '星桥公司' in text:
            add('工作:公司', '用户在星桥公司工作。')
            add('工作:岗位', '用户的岗位是产品设计师。')
        elif '北辰公司' in text:
            add('工作:公司', '用户在北辰公司工作。')
        elif '交互设计师' in text:
            add('工作:岗位', '用户的岗位是交互设计师。')
        elif '银杉公司' in text:
            add('工作:公司', '用户在银杉公司工作。', operation='update')
        elif '青禾公司' in text:
            add('工作:公司', '用户在青禾公司工作。')
        elif '绿茶' in text:
            add('饮食:茶', '用户最喜欢喝绿茶。')
        elif '红茶' in text:
            add('饮食:茶', '用户最喜欢喝红茶。', certainty='inferred')
        elif '椰子' in text:
            add('饮食:点心', '用户喜欢椰子味点心。')
        elif '长篇解释' in text:
            add('偏好:回答长度', '用户偏好简短的回答。')
        elif '文明六' in text:
            add('游戏:兴趣', '用户喜欢玩文明六。')
        return {'text': json.dumps({'memories': items}, ensure_ascii=False), 'finish_reason': 'stop',
                'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140,
                          'completion_tokens_details': {'reasoning_tokens': 2, 'text': 'HIDDEN_REASONING_SENTINEL'},
                          'unexpected_text': 'HIDDEN_REASONING_SENTINEL'},
                'reasoning': 'HIDDEN_REASONING_SENTINEL', 'reasoning_content': 'HIDDEN_REASONING_SENTINEL'}


class PlanTests(unittest.TestCase):
    def test_fixed_cases_have_total_twelve_call_budget_and_one_repeat(self):
        cases = target.load_cases()
        self.assertEqual(len(cases), 12)
        self.assertEqual(sum(case['max_extraction_calls'] for case in cases), 12)
        report = target.make_report(cases)
        self.assertEqual(report['budget']['requested_output_token_bound'], 9600)
        self.assertEqual(report['budget']['repetitions'], 1)
        self.assertEqual(report['budget']['request_timeout_seconds'], 45)
        self.assertTrue(all(case['manual_review'] is None for case in report['cases']))

    def test_default_plan_never_loads_runtime_or_environment_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            with patch.object(target, 'load_runtime', side_effect=AssertionError('forbidden runtime')):
                with patch.object(target, 'live_configuration', side_effect=AssertionError('forbidden credentials')):
                    with patch.dict(os.environ, {'LLM_API_KEY': 'PRODUCTION_SECRET_SENTINEL',
                                                  'MEMORY_EVAL_API_KEY': 'EVAL_SECRET_SENTINEL'}):
                        with redirect_stdout(io.StringIO()):
                            self.assertEqual(target.main(['--output', str(path)]), 0)
            text = path.read_text(encoding='utf-8')
            report = json.loads(text)
            self.assertEqual(report['mode'], 'plan')
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertNotIn('SECRET_SENTINEL', text)
            self.assertTrue(all(case['execution'] == [] for case in report['cases']))

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.json'
            path.write_text('prior evidence', encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(target.main(['--output', str(path)]), 1)
            self.assertEqual(path.read_text(encoding='utf-8'), 'prior evidence')

    def test_mock_blank_and_implicit_production_key_are_refused(self):
        valid = dict(live=True, provider='compatible', base_url='https://memory.invalid/v1',
                     model='model', api_key_env='MEMORY_EVAL_API_KEY')
        with patch.dict(os.environ, {'LLM_API_KEY': 'PRODUCTION_SECRET_SENTINEL'}, clear=True):
            for change in ({'provider': 'mock'}, {'provider': ' '}, {'model': ' '}, {},
                           {'base_url': 'https://name:secret@memory.invalid/v1'},
                           {'base_url': 'https://memory.invalid/v1?key=secret'},
                           {'base_url': 'http://memory.invalid/v1'}):
                with self.subTest(change=change):
                    with self.assertRaises(target.EvaluationError):
                        target.live_configuration(SimpleNamespace(**{**valid, **change}))

    def test_only_explicit_environment_key_is_used(self):
        args = SimpleNamespace(provider='compatible', base_url='https://memory.invalid/v1',
                               model='model', api_key_env='SYNTHETIC_EVAL_KEY')
        with patch.dict(os.environ, {'SYNTHETIC_EVAL_KEY': 'EXPLICIT_TEST_KEY',
                                    'LLM_API_KEY': 'PRODUCTION_SECRET_SENTINEL'}, clear=True):
            self.assertEqual(target.live_configuration(args).llm_api_key, 'EXPLICIT_TEST_KEY')

    def test_blank_configuration_is_refused_even_with_explicit_nonblank_key(self):
        valid = dict(provider='compatible', base_url='https://memory.invalid/v1',
                     model='model', api_key_env='MEMORY_EVAL_API_KEY')
        with patch.dict(os.environ, {'MEMORY_EVAL_API_KEY': 'EXPLICIT_OFFLINE_TEST_KEY'}, clear=True):
            for field in ('provider', 'model', 'base_url'):
                for value in ('', ' ', '\t\n'):
                    with self.subTest(field=field, value=value):
                        with self.assertRaisesRegex(target.EvaluationError, '^live_requires_explicit_provider_url_model$'):
                            target.live_configuration(SimpleNamespace(**{**valid, field: value}))
            with self.assertRaisesRegex(target.EvaluationError, '^live_requires_explicit_provider_url_model$'):
                target.live_configuration(SimpleNamespace(**{**valid, 'provider': ' MOCK '}))
            normalized = target.live_configuration(SimpleNamespace(**{
                **valid, 'provider': ' compatible ', 'model': ' model ',
                'base_url': ' https://memory.invalid/v1/ ',
            }))
            self.assertEqual(normalized.llm_provider, 'compatible')
            self.assertEqual(normalized.llm_model, 'model')
            self.assertEqual(normalized.llm_base_url, 'https://memory.invalid/v1')

    def test_report_redacts_key_and_only_accepts_numeric_usage(self):
        usage = target.numeric_usage({'prompt_tokens': 1, 'total_tokens': float('nan'),
                                      'completion_tokens': True, 'model': 'secret',
                                      'completion_tokens_details': {'reasoning_tokens': 3, 'text': 'hidden'}})
        self.assertEqual(usage, {'prompt_tokens': 1, 'completion_tokens_details': {'reasoning_tokens': 3}})
        for value in ('before OFFLINE_EVAL_SECRET after', 'password=arbitrary',
                      '{"api_key": "different-secret"}', 'Bearer some-token'):
            self.assertEqual(target.safe_report(value, 'OFFLINE_EVAL_SECRET'), '[REDACTED: credential]')


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = target.load_runtime()

    async def asyncSetUp(self):
        import httpx
        self.network_guard = patch.object(httpx.AsyncHTTPTransport, 'handle_async_request',
                                           side_effect=AssertionError('real network is forbidden'))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'offline.json'
        self.report = target.make_report(target.load_cases(), mode='offline_contract_test',
                                         provider='fake', model='synthetic-model')
        self.writer = target.ReportWriter(self.path, secret='OFFLINE_EVAL_SECRET')
        self.writer.save(self.report)

    async def run_fake(self, provider=None):
        provider = provider or FakeProvider()
        result = await target.execute_cases(self.report, self.writer, provider, runtime=self.runtime)
        return result, provider

    async def test_full_real_pipeline_reopens_and_retrieves_actual_rows(self):
        report, provider = await self.run_fake()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(len(provider.calls), 12)
        self.assertEqual(report['budget']['calls_reserved'], 12)
        self.assertEqual(report['budget']['calls_completed'], 12)
        self.assertEqual(report['budget']['usage_totals']['completion_tokens'], 480)
        self.assertTrue(all(case['reopen_rows_equal'] for case in report['cases']))
        cases = {case['id']: case for case in report['cases']}
        direct = cases['direct_employer_and_role']
        self.assertEqual({row['key'] for row in direct['rows_after_reopen']}, {'工作:公司', '工作:岗位'})
        self.assertTrue(all(query['hits'] for query in direct['retrieval_after_reopen']))
        for row in direct['rows_after_reopen']:
            self.assertEqual(row['evidence_state'], 'linked')
            self.assertEqual(row['evidence_message_id'], direct['execution'][0]['saved_user_message']['id'])
        independent = cases['independent_across_turns']['rows_after_reopen']
        self.assertEqual(len(independent), 2)
        self.assertTrue(all(row['status'] == 'active' for row in independent))
        correction = cases['explicit_correction']['rows_after_reopen']
        self.assertEqual({row['status'] for row in correction}, {'active', 'superseded'})
        self.assertEqual(next(row for row in correction if row['status'] == 'active')['decision'], 'update')
        conflict = cases['ambiguous_conflict']
        self.assertEqual({row['status'] for row in conflict['rows_after_reopen']}, {'active', 'conflict'})
        self.assertTrue(all(hit['row']['status'] == 'active' for query in conflict['retrieval'] for hit in query['hits']))
        self.assertFalse(cases['unknown_retrieval']['retrieval_after_reopen'][0]['hits'])

    async def test_source_guards_and_lifecycle_replays_make_no_calls_or_rows(self):
        report, _ = await self.run_fake()
        cases = {case['id']: case for case in report['cases']}
        for identifier in ('third_party_quote', 'hypothesis_and_roleplay',
                           'disable_enable_then_replay_old_message'):
            case = cases[identifier]
            self.assertFalse(any(step['call_reserved'] for step in case['execution']))
            self.assertEqual(case['rows_after_reopen'], [])
        deleted = cases['delete_then_replay_old_message']
        self.assertEqual(deleted['execution'][1]['deleted_rows'], 1)
        self.assertFalse(deleted['execution'][2]['call_reserved'])
        self.assertEqual(deleted['execution'][2]['extraction']['manager_skip'], 'write_state_ineligible')
        self.assertEqual(deleted['rows_after_reopen'], [])

    async def test_expectations_and_assistant_assertions_never_enter_prompts(self):
        self.report['cases'][0]['expected']['unique_marker'] = 'GOLD_EXPECTATION_SENTINEL'
        report, provider = await self.run_fake()
        for messages, options in provider.calls:
            serialized = json.dumps(messages, ensure_ascii=False)
            self.assertNotIn('GOLD_EXPECTATION_SENTINEL', serialized)
            self.assertNotIn('琥珀公司', serialized)
            self.assertEqual(options['max_tokens'], 800)
            self.assertEqual(options['max_attempts'], 1)
            self.assertEqual(options['reasoning_effort'], '')
            inputs = json.loads(messages[1]['content'])
            self.assertEqual(set(inputs), {'user_messages'})
            self.assertTrue(all(item['role'] == 'user' for item in inputs['user_messages']))
        assertion = next(case for case in report['cases'] if case['id'] == 'assistant_assertion_then_continue')
        self.assertEqual(assertion['rows_after_reopen'], [])
        self.assertTrue(all(case['manual_review'] is None for case in report['cases']))
        self.assertNotIn('HIDDEN_REASONING_SENTINEL', self.path.read_text(encoding='utf-8'))

    async def test_explicit_reasoning_effort_is_sent_without_persisting_hidden_reasoning(self):
        args = target.parser().parse_args([
            '--provider', 'compatible', '--base-url', 'https://memory.invalid/v1',
            '--model', 'synthetic-model', '--reasoning-effort', 'low',
        ])
        with patch.dict(os.environ, {'MEMORY_EVAL_API_KEY': 'EXPLICIT_OFFLINE_TEST_KEY'}, clear=True):
            config = target.live_configuration(args)
        self.assertEqual(config.llm_reasoning_effort, 'low')
        self.report = target.make_report(target.load_cases(), mode='offline_contract_test',
                                         provider=config.llm_provider, model=config.llm_model,
                                         reasoning_effort=config.llm_reasoning_effort)
        report, provider = await self.run_fake()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['reasoning_effort'], 'low')
        self.assertEqual(len(provider.calls), 12)
        self.assertTrue(all(options['reasoning_effort'] == 'low' for _, options in provider.calls))
        persisted = self.path.read_text(encoding='utf-8')
        self.assertNotIn('HIDDEN_REASONING_SENTINEL', persisted)
        self.assertNotIn('"reasoning":', persisted)
        self.assertNotIn('"reasoning_content":', persisted)

    async def test_provider_error_stops_run_and_keeps_sanitized_partial_report(self):
        provider = SimpleNamespace(complete_raw=AsyncMock(side_effect=RuntimeError('HTTP body OFFLINE_EVAL_SECRET password=bad')))
        report, _ = await self.run_fake(provider)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(provider.complete_raw.await_count, 1)
        self.assertEqual(report['budget']['calls_reserved'], 1)
        self.assertEqual(report['budget']['calls_completed'], 0)
        persisted = self.path.read_text(encoding='utf-8')
        self.assertNotIn('HTTP body', persisted)
        self.assertNotIn('OFFLINE_EVAL_SECRET', persisted)
        self.assertNotIn('password=bad', persisted)
        self.assertEqual(report['cases'][0]['execution'][0]['extraction']['error_type'], 'RuntimeError')
        self.assertTrue(all(case['status'] == 'planned' for case in report['cases'][1:]))

    async def test_parse_failure_retains_public_raw_usage_and_finish_reason(self):
        provider = SimpleNamespace(complete_raw=AsyncMock(return_value={'text': 'not valid json', 'finish_reason': 'length',
                                                                      'usage': {'completion_tokens': 800}, 'reasoning': 'HIDDEN_REASONING_SENTINEL'}))
        report, _ = await self.run_fake(provider)
        step = report['cases'][0]['execution'][0]
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(step['provider_result']['raw'], 'not valid json')
        self.assertEqual(step['provider_result']['finish_reason'], 'length')
        self.assertEqual(step['provider_result']['usage'], {'completion_tokens': 800})
        self.assertEqual(step['extraction']['parsed'], [])
        self.assertEqual(step['extraction']['error_type'], 'JSONDecodeError')

    async def test_global_and_per_case_budgets_refuse_calls_before_provider(self):
        provider = SimpleNamespace(complete_raw=AsyncMock(return_value={'text': '{"memories":[]}', 'finish_reason': 'stop'}))
        budgeted = target.BudgetedProvider(provider, self.report, self.writer)
        budgeted.begin_case({'max_extraction_calls': 1})
        budgeted.record = {}
        await budgeted.complete_raw([], max_tokens=800, max_attempts=1)
        with self.assertRaises(target.EvaluationError):
            await budgeted.complete_raw([], max_tokens=800, max_attempts=1)
        self.assertEqual(provider.complete_raw.await_count, 1)
        budgeted.begin_case({'max_extraction_calls': 2})
        self.report['budget']['calls_reserved'] = 12
        with self.assertRaises(target.EvaluationError):
            await budgeted.complete_raw([], max_tokens=800, max_attempts=1)
        self.assertEqual(provider.complete_raw.await_count, 1)

    async def test_oversized_provider_output_stops_further_calls(self):
        provider = SimpleNamespace(complete_raw=AsyncMock(return_value={'text': '{"memories":[]}', 'finish_reason': 'stop',
                                                                      'usage': {'completion_tokens': 801}}))
        report, _ = await self.run_fake(provider)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(provider.complete_raw.await_count, 1)
        self.assertEqual(report['cases'][0]['execution'][0]['budget_failure'], 'provider_exceeded_requested_output_tokens')

    async def test_either_output_usage_field_exceeding_limit_stops_calls(self):
        usages = ({'completion_tokens': 40, 'output_tokens': 801},
                  {'completion_tokens': 801, 'output_tokens': 40})
        for index, usage in enumerate(usages):
            with self.subTest(usage=usage):
                report = target.make_report(target.load_cases(), mode='offline_contract_test',
                                            provider='fake', model='synthetic-model')
                writer = target.ReportWriter(Path(self.directory.name) / f'dual-usage-{index}.json')
                provider = SimpleNamespace(complete_raw=AsyncMock(return_value={
                    'text': '{"memories":[]}', 'finish_reason': 'stop', 'usage': usage,
                }))
                result = await target.execute_cases(report, writer, provider, runtime=self.runtime)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(provider.complete_raw.await_count, 1)
                self.assertEqual(result['budget']['calls_reserved'], 1)
                step = result['cases'][0]['execution'][0]
                self.assertEqual(step['provider_result']['usage'], usage)
                self.assertEqual(step['budget_failure'], 'provider_exceeded_requested_output_tokens')
                self.assertTrue(all(case['status'] == 'planned' for case in result['cases'][1:]))

    async def test_timeout_cancels_the_single_attempt_and_preserves_partial(self):
        cancelled = False
        async def pending(*args, **kwargs):
            nonlocal cancelled
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled = True
                raise
        provider = SimpleNamespace(complete_raw=AsyncMock(side_effect=pending))
        with patch.object(target, 'REQUEST_TIMEOUT_SECONDS', .01):
            report, _ = await self.run_fake(provider)
        self.assertTrue(cancelled)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(provider.complete_raw.await_count, 1)
        self.assertEqual(report['cases'][0]['execution'][0]['extraction']['error_type'], 'TimeoutError')

    async def test_actual_http_adapter_attempts_once_and_never_reports_error_body(self):
        import httpx
        calls = []
        def transport(request):
            calls.append(json.loads(request.content))
            return httpx.Response(500, text='provider body OFFLINE_EVAL_SECRET password=bad')
        client = self.runtime.LLM(config=client_config(), request_timeout=45, trust_env=False)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False)
        try:
            with patch('server.llm.asyncio.sleep', AsyncMock()) as sleep:
                report, _ = await self.run_fake(client)
                sleep.assert_not_awaited()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]['max_tokens'], 800)
            self.assertEqual(calls[0]['model'], 'synthetic-model')
            self.assertEqual(report['status'], 'partial')
            self.assertNotIn('provider body', self.path.read_text(encoding='utf-8'))
        finally:
            await client.aclose()

    async def test_http_default_attempt_limit_is_three_and_invalid_limits_refused(self):
        import httpx
        calls = []
        def transport(request):
            calls.append(request)
            return httpx.Response(500, text='password=not-a-report')
        client = self.runtime.LLM(config=client_config())
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False)
        try:
            with patch('server.llm.asyncio.sleep', AsyncMock()) as sleep:
                with self.assertRaisesRegex(RuntimeError, '^HTTP 500$'):
                    await client.complete_raw([{'role': 'user', 'content': 'synthetic'}])
                self.assertEqual(len(calls), 3)
                self.assertEqual(sleep.await_count, 2)
            for attempts in (0, 4, True, '1'):
                with self.assertRaises(ValueError):
                    await client.complete_raw([], max_attempts=attempts)
            self.assertEqual(len(calls), 3)
        finally:
            await client.aclose()


if __name__ == '__main__':
    unittest.main()
