"""Offline quality-runner checks through the real editor service contract.

Model delegates are injected; no resolver replacement, network, audio, app
bootstrap, or production-store access is needed.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import eval_editor_quality as runner
from server.assistant.editor_insights import (
    DISCUSSION_RULES, INSIGHT_RULES, EditorInsightService,
)


BODY = ('我们先说明为什么要改变现有流程，并列出试点的适用范围。\n'
        '试点结果尚不能代表所有用户。\n'
        '试点只覆盖两个小组，尚未记录其他团队的实际使用情况。'
        '建议先确认试点目标、采样方式和观察周期，再决定是否扩大范围。')
ANCHOR = '试点结果尚不能代表所有用户。'
POINT = '扩大的条件还不够明确。可补充如何判断试点结果足以支持扩大范围，帮助读者理解下一步。'


def fixture(case_id='sample', discussion=()):
    return {
        'id': case_id, 'genre': '方案', 'title': '试点说明', 'body': BODY,
        'author_intent': 'AUTHOR_INTENT_SENTINEL_DO_NOT_SEND',
        'expectation': {'mode': 'insight', 'rubric': ['RUBRIC_SENTINEL_DO_NOT_SEND']},
        'discussion': list(discussion),
    }


def response(value, usage=None, finish_reason='stop'):
    return {'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
            'usage': usage or {}, 'finish_reason': finish_reason,
            'reasoning': 'PRIVATE_REASONING_MUST_NOT_BE_RECORDED'}


def insight_response(**kwargs):
    return response({'action': 'insight', 'anchor': ANCHOR, 'point': POINT}, **kwargs)


class ScriptedModel:
    offline = False
    model = 'isolated-test-model'
    provider = 'isolated-test-provider'

    def __init__(self, *values):
        self.values = list(values)
        self.requests = []
        self.closed = 0
        self.settings = SimpleNamespace(llm_frequency_penalty=0, llm_presence_penalty=0,
                                        llm_reasoning_effort='')
        self.request_timeout = 25

    async def complete_raw(self, messages, **options):
        self.requests.append({'messages': deepcopy(messages), 'options': dict(options)})
        if not self.values:
            raise AssertionError('unexpected_delegate_call')
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def aclose(self):
        self.closed += 1


class BlockingModel(ScriptedModel):
    def __init__(self, *values):
        super().__init__(*values)
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def complete_raw(self, messages, **options):
        if self.values:
            return await super().complete_raw(messages, **options)
        self.requests.append({'messages': deepcopy(messages), 'options': dict(options)})
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class CancellationResistantModel(BlockingModel):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def complete_raw(self, messages, **options):
        self.requests.append({'messages': deepcopy(messages), 'options': dict(options)})
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        self.finished.set()
        return response({'action': 'quiet'})


class QualityEvalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='editor-quality-isolated-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / 'results.json'
        self.cases_path = self.root / 'cases.json'

    def write_cases(self, *cases):
        self.cases_path.write_text(json.dumps({'schema_version': 1, 'cases': cases},
                                             ensure_ascii=False), encoding='utf-8')

    def metadata(self, cases, limit=15):
        return {'request_limit': limit,
                'planned_request_upper_bound': sum(1 + len(c.get('discussion', [])) for c in cases)}

    async def run_fixture(self, cases, delegate, limit=15):
        recorder = runner.RecordingLLM(delegate, limit)
        with redirect_stdout(io.StringIO()):
            report = await runner.run_cases(cases, recorder, self.output, self.metadata(cases, limit))
        return report, recorder

    async def test_default_dry_run_never_constructs_model_or_writes_output(self):
        self.write_cases(fixture())
        self.output.write_text('existing baseline', encoding='utf-8')
        with patch.object(runner, 'LLM', side_effect=AssertionError('model construction forbidden')) as factory:
            with redirect_stdout(io.StringIO()):
                code = await runner.main(['--cases', str(self.cases_path), '--output', str(self.output)])
        self.assertEqual(code, 0)
        factory.assert_not_called()
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing baseline')

    async def test_excessive_plan_rejected_before_model_construction(self):
        self.write_cases(fixture('first'), fixture('second'))
        with patch.object(runner, 'LLM', side_effect=AssertionError('model construction forbidden')) as factory:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                await runner.main(['--live', '--cases', str(self.cases_path), '--max-calls', '1'])
        self.assertEqual(caught.exception.code, 2)
        factory.assert_not_called()

    async def test_existing_live_report_preserved_before_supplier_call(self):
        self.write_cases(fixture())
        self.output.write_text('existing baseline', encoding='utf-8')
        delegate = ScriptedModel(insight_response())
        with patch.object(runner, 'LLM', return_value=delegate):
            with redirect_stdout(io.StringIO()), self.assertRaises(FileExistsError):
                await runner.main(['--live', '--cases', str(self.cases_path), '--output', str(self.output)])
        self.assertEqual(delegate.requests, [])
        self.assertEqual(delegate.closed, 1)
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'existing baseline')

    async def test_live_mode_refuses_offline_delegate_without_creating_report(self):
        self.write_cases(fixture())
        delegate = ScriptedModel()
        delegate.offline = True
        with patch.object(runner, 'LLM', return_value=delegate):
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'live_model_required'):
                await runner.main(['--live', '--cases', str(self.cases_path), '--output', str(self.output)])
        self.assertEqual(delegate.requests, [])
        self.assertEqual(delegate.closed, 1)
        self.assertFalse(self.output.exists())

    async def test_live_cli_uses_injected_delegate_and_preserves_reviewable_baseline(self):
        self.write_cases(fixture())
        delegate = ScriptedModel(insight_response(usage={'total_tokens': 7}))
        with patch.object(runner, 'LLM', return_value=delegate):
            with redirect_stdout(io.StringIO()):
                code = await runner.main(['--live', '--cases', str(self.cases_path), '--output', str(self.output)])
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(code, 0)
        self.assertEqual(saved['run_status'], 'completed')
        self.assertEqual(saved['mode'], 'live')
        self.assertEqual(saved['model'], 'isolated-test-model')
        self.assertEqual(saved['summary']['request_count'], 1)
        self.assertEqual(saved['summary']['usage_reported_only']['total_tokens'], 7)
        self.assertEqual(delegate.closed, 1)

    async def test_runtime_budget_never_dispatches_extra_supplier_request(self):
        report, recorder = await self.run_fixture([fixture()], ScriptedModel(insight_response()), limit=1)
        with self.assertRaisesRegex(RuntimeError, 'eval_request_budget_exhausted'):
            await recorder.complete_raw([], json_mode=True, max_attempts=1)
        self.assertEqual(len(recorder.delegate.requests), 1)
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(report['summary']['request_count'], 1)

    async def test_real_service_builds_prompts_without_review_expectations(self):
        turns = [{'id': 'disagree', 'user_text': '我想保留现有范围说明。'},
                 {'id': 'revise', 'user_text': '那怎么改？'}]
        cases = [fixture(discussion=turns)]
        delegate = ScriptedModel(insight_response(), response({'text': '可以保留，再明确扩大范围的条件。'}),
                                 response({'text': '建议稿：满足既定观察条件后再扩大范围。'}))
        report, _ = await self.run_fixture(cases, delegate)
        self.assertEqual([r['messages'][0]['content'] for r in delegate.requests],
                         [INSIGHT_RULES, DISCUSSION_RULES, DISCUSSION_RULES])
        all_sent = json.dumps(delegate.requests, ensure_ascii=False)
        self.assertNotIn('AUTHOR_INTENT_SENTINEL_DO_NOT_SEND', all_sent)
        self.assertNotIn('RUBRIC_SENTINEL_DO_NOT_SEND', all_sent)
        initial = json.loads(delegate.requests[0]['messages'][1]['content'])
        self.assertEqual(initial['draft']['body'], BODY)
        self.assertFalse(initial['draft']['saved'])
        self.assertTrue(initial['draft']['untrusted'])
        continuation = json.loads(delegate.requests[-1]['messages'][1]['content'])
        self.assertEqual(set(continuation), {'draft', 'insight', 'boundaries'})
        self.assertEqual(delegate.requests[-1]['messages'][2:-1], [
            {'role': 'user', 'content': turns[0]['user_text']},
            {'role': 'assistant', 'content': '可以保留，再明确扩大范围的条件。'},
        ])
        self.assertEqual(delegate.requests[-1]['messages'][-1],
                         {'role': 'user', 'content': turns[1]['user_text']})
        self.assertEqual(continuation['boundaries']['tool_permissions'], [])
        self.assertFalse(continuation['boundaries']['save_or_edit_draft'])
        self.assertEqual(report['cases'][0]['expectation'], cases[0]['expectation'])
        self.assertEqual(report['summary']['analysis_outcomes'], {'insight': 1})
        self.assertEqual(report['summary']['discussion_outcomes'], {'reply': 2})
        for request in delegate.requests:
            self.assertIs(request['options']['json_mode'], True)
            self.assertEqual(request['options']['max_attempts'], 1)

    async def test_accepted_quiet_separate_from_invalid_json_schema_quote_and_point(self):
        cases = [fixture('quiet'), fixture('json'), fixture('schema'), fixture('quote'), fixture('point')]
        delegate = ScriptedModel(
            response({'action': 'quiet'}), response('not JSON'), response({'action': 'quiet', 'extra': 1}),
            response({'action': 'insight', 'anchor': '不在原文中的引文', 'point': POINT}),
            response({'action': 'insight', 'anchor': ANCHOR, 'point': '甲' * 241}),
        )
        report, _ = await self.run_fixture(cases, delegate)
        self.assertEqual([c['analysis']['outcome'] for c in report['cases']],
                         ['model_quiet', 'invalid_json', 'invalid_schema', 'invalid_anchor', 'invalid_point'])
        self.assertEqual([c['analysis']['result']['status'] for c in report['cases']],
                         ['quiet', 'unavailable', 'unavailable', 'unavailable', 'unavailable'])
        self.assertEqual([c['analysis']['result']['reason'] for c in report['cases']],
                         ['no_salient_insight', *(['analysis_invalid_response'] * 4)])
        self.assertEqual(report['summary']['analysis_outcomes']['model_quiet'], 1)
        self.assertEqual(report['summary']['request_count'], 5)

    async def test_silence_skips_planned_discussion_without_supplier_call(self):
        cases = [fixture(discussion=[{'id': 'ask', 'user_text': '为什么？'}])]
        delegate = ScriptedModel(response({'action': 'quiet'}))
        report, _ = await self.run_fixture(cases, delegate)
        self.assertEqual(len(delegate.requests), 1)
        self.assertEqual(report['cases'][0]['discussion'][0]['outcome'], 'skipped_no_insight')

    async def test_repeated_and_beyond_visible_quotes_are_invalid_anchors(self):
        repeated = fixture('repeated')
        repeated['body'] = BODY + '\n' + ANCHOR
        hidden = fixture('hidden')
        hidden['body'] = '甲' * 4001 + '\n' + ANCHOR
        report, _ = await self.run_fixture([repeated, hidden],
                                           ScriptedModel(insight_response(), insight_response()))
        self.assertEqual([c['analysis']['outcome'] for c in report['cases']],
                         ['invalid_anchor', 'invalid_anchor'])

    async def test_provider_failure_never_counts_as_successful_quiet_or_logs_message(self):
        report, _ = await self.run_fixture([fixture()], ScriptedModel(RuntimeError('PRIVATE_PROVIDER_BODY')))
        self.assertEqual(report['cases'][0]['analysis']['outcome'], 'analysis_unavailable')
        self.assertEqual(report['cases'][0]['analysis']['result']['status'], 'unavailable')
        self.assertEqual(report['calls'][0]['status'], 'error')
        self.assertEqual(report['calls'][0]['error_type'], 'RuntimeError')
        self.assertNotIn('PRIVATE_PROVIDER_BODY', self.output.read_text(encoding='utf-8'))

    async def test_discussion_protocol_error_retains_model_output_for_review(self):
        cases = [fixture(discussion=[{'id': 'ask', 'user_text': '为什么？'}])]
        report, _ = await self.run_fixture(cases, ScriptedModel(insight_response(), response('broken reply')))
        turn = report['cases'][0]['discussion'][0]
        self.assertEqual(turn['outcome'], 'discussion_error')
        self.assertEqual(turn['reason'], 'insight_discussion_invalid_reply')
        self.assertEqual(report['calls'][1]['text'], 'broken reply')

    async def test_only_provider_reported_usage_is_counted_and_reasoning_excluded(self):
        delegate = ScriptedModel(insight_response(
            usage={'prompt_tokens': 21, 'completion_tokens': 8, 'total_tokens': 29,
                   'cached_tokens': 9, 'negative': -1}, finish_reason='length'))
        report, _ = await self.run_fixture([fixture()], delegate)
        self.assertEqual(report['summary']['usage_reported_only'],
                         {'prompt_tokens': 21, 'completion_tokens': 8, 'total_tokens': 29})
        self.assertEqual(report['summary']['calls_with_usage'], 1)
        self.assertEqual(report['summary']['finish_reason_length_calls'], 1)
        self.assertEqual(report['cases'][0]['analysis']['outcome'], 'analysis_output_truncated')
        self.assertEqual(report['cases'][0]['analysis']['result']['status'], 'unavailable')
        self.assertEqual(report['cases'][0]['analysis']['result']['reason'], 'analysis_output_truncated')
        self.assertNotIn('PRIVATE_REASONING_MUST_NOT_BE_RECORDED', self.output.read_text(encoding='utf-8'))
        self.assertEqual(runner.safe_usage({'prompt_tokens': True, 'completion_tokens': -1,
                                          'total_tokens': '3'}), {})

    async def test_response_credential_redacted_in_persistent_report(self):
        secret = 'api_key=FICTIONAL_DO_NOT_PERSIST_VALUE'
        report, _ = await self.run_fixture([fixture()], ScriptedModel(response(secret)))
        self.assertEqual(report['cases'][0]['analysis']['outcome'], 'sensitive_response')
        self.assertTrue(report['calls'][0]['response_redacted'])
        self.assertNotIn(secret, self.output.read_text(encoding='utf-8'))

    async def test_empty_response_with_thinking_is_failure_not_intentional_silence(self):
        raw = response('', usage={'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 6})
        raw['reasoning'] = json.dumps({'action': 'quiet'})
        report, _ = await self.run_fixture([fixture()], ScriptedModel(raw))
        self.assertEqual(report['cases'][0]['analysis']['outcome'], 'analysis_empty_response')
        self.assertEqual(report['cases'][0]['analysis']['result']['status'], 'unavailable')
        self.assertEqual(report['summary']['analysis_protocol_accepted'], 0)
        self.assertEqual(report['summary']['usage_reported_only']['total_tokens'], 6)
        self.assertNotIn('reasoning', report['calls'][0])

    async def test_length_completion_with_valid_quiet_is_not_credited_as_silence(self):
        report, _ = await self.run_fixture([fixture()], ScriptedModel(
            response({'action': 'quiet'}, finish_reason='length')))
        self.assertEqual(report['cases'][0]['analysis']['outcome'], 'analysis_output_truncated')
        self.assertEqual(report['summary']['analysis_protocol_accepted'], 0)
        self.assertEqual(report['cases'][0]['analysis']['result']['status'], 'unavailable')

    async def test_malformed_provider_response_reaches_service_diagnostics_without_recording_opaque_data(self):
        candidates = [
            ['PRIVATE_OPAQUE_PROVIDER_DATA'],
            {'finish_reason': 'stop', 'reasoning': 'PRIVATE_OPAQUE_PROVIDER_DATA'},
            {'text': None, 'finish_reason': 'stop'},
            {'text': {'private': 'PRIVATE_OPAQUE_PROVIDER_DATA'}, 'finish_reason': 'stop'},
        ]
        for raw_response in candidates:
            with self.subTest(response_type=type(raw_response).__name__, raw_response=raw_response):
                report, _ = await self.run_fixture([fixture()], ScriptedModel(raw_response))
                analysis = report['cases'][0]['analysis']
                self.assertEqual(analysis['outcome'], 'analysis_invalid_response')
                self.assertEqual(analysis['result']['status'], 'unavailable')
                self.assertEqual(analysis['result']['reason'], 'analysis_invalid_response')
                self.assertTrue(report['calls'][0]['invalid_response_shape'])
                self.assertEqual(report['summary']['analysis_protocol_accepted'], 0)
                self.assertNotIn('PRIVATE_OPAQUE_PROVIDER_DATA', self.output.read_text(encoding='utf-8'))

    async def test_recorder_forwards_model_for_production_reasoning_options(self):
        cases = [fixture(discussion=[{'id': 'ask', 'user_text': '为什么？'}])]
        for model in ('step-3.7-flash', 'other-model'):
            with self.subTest(model=model):
                delegate = ScriptedModel(insight_response(), response({'text': '原文还没有说明推广的条件。'}))
                delegate.model = model
                report, recorder = await self.run_fixture(cases, delegate)
                self.assertEqual(recorder.model, model)
                self.assertEqual(report['summary']['request_count'], 2)
                for request, recorded in zip(delegate.requests, report['calls']):
                    options = request['options']
                    if model == 'step-3.7-flash':
                        # Analysis keeps medium; discussion drops to low so a
                        # multi-turn reply cannot spend the whole budget on
                        # thinking. Other providers get no forced option.
                        self.assertEqual(options['reasoning_effort'],
                                         'medium' if recorded['stage'] == 'analyze' else 'low')
                    else:
                        self.assertNotIn('reasoning_effort', options)
                    self.assertEqual(options['max_tokens'], 6144)
                    self.assertEqual(options['max_attempts'], 1)
                    self.assertEqual(recorded['request'], options)

    async def test_transient_atomic_replace_lock_retries_local_write_without_model_replay(self):
        delegate = ScriptedModel(insight_response())
        recorder = runner.RecordingLLM(delegate, 15)
        cases = [fixture()]
        real_replace = Path.replace
        locks_remaining = 2
        locks_seen = 0

        def replace_with_transient_lock(temporary, target):
            nonlocal locks_remaining, locks_seen
            # Lock the report only after its one provider request has returned.
            if recorder.calls and locks_remaining:
                locks_remaining -= 1
                locks_seen += 1
                raise PermissionError('simulated Windows file handle lock')
            return real_replace(temporary, target)

        with patch.object(Path, 'replace', replace_with_transient_lock):
            with patch.object(runner.time, 'sleep') as sleeper:
                with redirect_stdout(io.StringIO()):
                    report = await runner.run_cases(cases, recorder, self.output, self.metadata(cases))
        self.assertEqual(locks_seen, 2)
        self.assertEqual(sleeper.call_args_list, [call(0.1), call(0.1)])
        self.assertEqual(len(delegate.requests), 1)
        self.assertEqual(report['summary']['request_count'], 1)
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(saved['run_status'], 'completed')
        self.assertEqual(saved['cases'][0]['analysis']['outcome'], 'insight')
        self.assertFalse(self.output.with_suffix('.tmp').exists())

    def test_persistent_atomic_replace_lock_is_bounded_and_preserves_old_result_and_full_temp(self):
        prior = '{"prior_baseline":{"complete":true}}\n'
        self.output.write_text(prior, encoding='utf-8')
        report = {**self.metadata([]), 'run_status': 'completed', 'completed_at': 'offline-fixture',
                  'calls': [], 'cases': []}
        with patch.object(Path, 'replace', side_effect=PermissionError('simulated Windows file handle lock')) as replacer:
            with patch.object(runner.time, 'sleep') as sleeper:
                with self.assertRaises(PermissionError):
                    runner.save_report(self.output, report)
        self.assertEqual(replacer.call_count, 6)
        self.assertEqual(sleeper.call_args_list, [call(0.1)] * 5)
        self.assertEqual(self.output.read_text(encoding='utf-8'), prior)
        saved_temporary = json.loads(self.output.with_suffix('.tmp').read_text(encoding='utf-8'))
        self.assertEqual(saved_temporary, report)
        self.assertEqual(saved_temporary['run_status'], 'completed')
        self.assertEqual(saved_temporary['completed_at'], 'offline-fixture')

    async def test_timeout_separate_from_quiet_and_cancelled_call_persisted(self):
        delegate = BlockingModel()
        with patch.object(EditorInsightService, 'MODEL_TIMEOUT_SECONDS', 0.01):
            report, _ = await self.run_fixture([fixture()], delegate)
        await asyncio.wait_for(delegate.cancelled.wait(), 1)
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(report['cases'][0]['analysis']['outcome'], 'analysis_timeout')
        self.assertEqual(report['cases'][0]['analysis']['result']['status'], 'unavailable')
        self.assertEqual(saved['calls'][0]['status'], 'cancelled')
        self.assertEqual(saved['summary']['request_count'], 1)
        self.assertEqual(saved['summary']['analysis_outcomes'], {'analysis_timeout': 1})
        self.assertEqual(saved['summary']['unreported_usage_calls'], 1)

    async def test_external_cancellation_retains_spent_calls_and_prior_usage(self):
        delegate = BlockingModel(insight_response(usage={'total_tokens': 17}))
        cases = [fixture('completed'), fixture('cancelled')]
        recorder = runner.RecordingLLM(delegate, 15)
        with redirect_stdout(io.StringIO()):
            task = asyncio.create_task(runner.run_cases(cases, recorder, self.output, self.metadata(cases)))
            await asyncio.wait_for(delegate.started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await asyncio.wait_for(delegate.cancelled.wait(), 1)
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(saved['summary']['request_count'], 2)
        self.assertEqual(saved['summary']['usage_reported_only']['total_tokens'], 17)
        self.assertEqual(saved['calls'][-1]['status'], 'cancelled')
        self.assertEqual(saved['cases'][-1]['analysis']['outcome'], 'evaluation_incomplete')
        self.assertEqual(saved['run_status'], 'cancelled')
        self.assertNotIn('completed_at', saved)

    async def test_external_discussion_cancellation_preserves_partial_baseline(self):
        delegate = BlockingModel(insight_response(usage={'total_tokens': 11}))
        cases = [fixture(discussion=[{'id': 'ask', 'user_text': '为什么？'}])]
        recorder = runner.RecordingLLM(delegate, 15)
        with redirect_stdout(io.StringIO()):
            task = asyncio.create_task(runner.run_cases(cases, recorder, self.output, self.metadata(cases)))
            await asyncio.wait_for(delegate.started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await asyncio.wait_for(delegate.cancelled.wait(), 1)
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(saved['run_status'], 'cancelled')
        self.assertEqual(saved['summary']['request_count'], 2)
        self.assertEqual(saved['summary']['usage_reported_only']['total_tokens'], 11)
        self.assertEqual(saved['calls'][-1]['status'], 'cancelled')
        self.assertEqual(saved['cases'][0]['analysis']['outcome'], 'insight')
        self.assertEqual(saved['cases'][0]['discussion'][0]['outcome'], 'evaluation_incomplete')

    async def test_cancellation_resistant_provider_cannot_block_final_report(self):
        delegate = CancellationResistantModel()
        try:
            with patch.object(EditorInsightService, 'MODEL_TIMEOUT_SECONDS', 0.01):
                report, _ = await asyncio.wait_for(self.run_fixture([fixture()], delegate), 1)
            saved = json.loads(self.output.read_text(encoding='utf-8'))
            self.assertEqual(report['run_status'], 'completed')
            self.assertEqual(saved['calls'][0]['status'], 'response_unavailable_at_shutdown')
            self.assertEqual(saved['summary']['unreported_usage_calls'], 1)
            self.assertEqual(saved['cases'][0]['analysis']['outcome'], 'analysis_timeout')
        finally:
            delegate.release.set()
            await asyncio.wait_for(delegate.finished.wait(), 1)

    def test_unsafe_fixture_rejected_before_any_request_plan(self):
        case = fixture()
        case['body'] += ' api_key=FICTIONAL_UNSAFE_FIXTURE'
        self.write_cases(case)
        with self.assertRaisesRegex(ValueError, 'fixture_contains_sensitive_data'):
            runner.load_cases(self.cases_path)

    async def test_invalid_discussion_ids_rejected_before_live_model_construction(self):
        plans = [
            [{'id': invalid_id, 'user_text': '为什么？'}]
            for invalid_id in ('', 'analyze', 'Why', 'two words', 'a' * 61)
        ] + [[{'id': 'same', 'user_text': '为什么？'},
              {'id': 'same', 'user_text': '那怎么改？'}]]
        with patch.object(runner, 'LLM', side_effect=AssertionError('model construction forbidden')) as factory:
            for plan in plans:
                with self.subTest(ids=[turn['id'] for turn in plan]):
                    self.write_cases(fixture(discussion=plan))
                    with self.assertRaisesRegex(ValueError, 'invalid_discussion_turn'):
                        await runner.main(['--live', '--cases', str(self.cases_path),
                                           '--output', str(self.output)])
            factory.assert_not_called()
        self.assertFalse(self.output.exists())
        valid = fixture(discussion=[{'id': 'a' * 60, 'user_text': '为什么？'},
                                    {'id': 'keep-this_2', 'user_text': '那怎么改？'}])
        self.write_cases(valid)
        self.assertEqual(runner.load_cases(self.cases_path)[0]['discussion'], valid['discussion'])

    async def test_untrimmed_draft_limit_rejected_before_live_model_construction(self):
        with patch.object(runner, 'LLM', side_effect=AssertionError('model construction forbidden')) as factory:
            for body in ('甲' * 80 + ' ' * 19921, ' ' * 19921 + '甲' * 80, '甲' * 79 + ' ' * 500):
                with self.subTest(raw_chars=len(body), trimmed_chars=len(body.strip())):
                    case = fixture()
                    case['body'] = body
                    self.write_cases(case)
                    with self.assertRaisesRegex(ValueError, 'invalid_draft_length'):
                        await runner.main(['--live', '--cases', str(self.cases_path),
                                           '--output', str(self.output)])
            factory.assert_not_called()
        self.assertFalse(self.output.exists())
        for body in ('甲' * 80, '甲' * 80 + ' ' * 19920, '甲' * 20000):
            with self.subTest(valid_raw_chars=len(body), valid_trimmed_chars=len(body.strip())):
                case = fixture()
                case['body'] = body
                self.write_cases(case)
                self.assertEqual(runner.load_cases(self.cases_path)[0]['body'], body)


if __name__ == '__main__':
    unittest.main()
