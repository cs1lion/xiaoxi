"""Offline diagnostic bounds and citation contracts; no learning quality claims."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_procedural_memory_diagnostics as target


def scalar_event(identifier, **fields):
    return {'actor': 'domain_tool', 'action': 'confirm', 'arguments': {},
            'result': {'observation_id': identifier, **fields}}


def synthetic_lesson(failure_id='failed', read_id='read'):
    return {'lesson': {'record_type': 'failure_lesson', 'scope': '合成合同测试，语义未评估',
                       'claims': [
        {'kind': 'observed_failure', 'text': '本次确认返回版本冲突',
         'evidence': [{'observation_id': failure_id, 'pointer': '/result/error',
                       'value': 'base_version_conflict'}]},
        {'kind': 'stop_condition', 'text': '遇到该结果先停止声称保存成功',
         'evidence': [{'observation_id': failure_id, 'pointer': '/result/write_observed',
                       'value': False}]},
        {'kind': 'uncertainty', 'text': '此读取仍是固定旧版本，恢复尚未证明',
         'evidence': [{'observation_id': read_id, 'pointer': '/result/note/selected_version_is_current',
                       'value': False}]},
    ]}}


def simple_corpus():
    return SimpleNamespace(forbidden_identifiers={'HISTORICAL_NOTE_ID', 'HISTORICAL_PROPOSAL_ID'},
                           resolve_evidence=lambda identifier: {'actual_observation': identifier})


def simple_catalog():
    return {'failed': scalar_event('failed', ok=False, error='base_version_conflict',
                                   write_observed=False, update_attempts=0),
            'read': scalar_event('read', ok=True, note={'selected_version_is_current': False})}


class CitationContractTests(unittest.TestCase):
    def setUp(self):
        self.corpus, self.catalog = simple_corpus(), simple_catalog()

    def test_exact_scalar_pointer_supports_escaped_keys_and_array_indices(self):
        event = scalar_event('nested', ok=False, nested={'a/b': {'~key': [None, '原话', 3.5]}})
        self.assertIsNone(target.pointer_value(event, '/result/nested/a~1b/~0key/0'))
        self.assertEqual(target.pointer_value(event, '/result/nested/a~1b/~0key/1'), '原话')
        self.assertEqual(target.pointer_value(event, '/result/nested/a~1b/~0key/2'), 3.5)
        for pointer in ('/result', '', '/arguments', '/result/nested/a~2b',
                        '/result/nested/a~1b/~0key/-1', '/result/nested/a~1b/~0key/01',
                        '/result/nested/a~1b/~0key/3', '/result/nested/a~1b/~0key/١'):
            with self.subTest(pointer=pointer), self.assertRaises(target.EvaluationError):
                target.pointer_value(event, pointer)
        with self.assertRaisesRegex(target.EvaluationError, 'public_evidence_value_not_scalar'):
            target.pointer_value(event, '/result/nested')

    def test_scalar_value_comparison_preserves_types_and_exact_text(self):
        for replacement in (0, 'false', None):
            bad = synthetic_lesson()
            bad['lesson']['claims'][1]['evidence'][0]['value'] = replacement
            with self.subTest(value=replacement), self.assertRaisesRegex(
                    target.EvaluationError, 'public_evidence_value_mismatch'):
                target.validate_lesson(bad, self.corpus, self.catalog)
        bad = synthetic_lesson()
        bad['lesson']['claims'][0]['evidence'][0]['value'] = 'base_version_conflict '
        with self.assertRaisesRegex(target.EvaluationError, 'public_evidence_value_mismatch'):
            target.validate_lesson(bad, self.corpus, self.catalog)
        for original, replacement in ((0, False), (1, True), (1, 1.0)):
            catalog = simple_catalog()
            catalog['failed']['result']['counter'] = original
            bad = synthetic_lesson()
            bad['lesson']['claims'][0]['evidence'] = [
                {'observation_id': 'failed', 'pointer': '/result/counter', 'value': replacement}]
            with self.subTest(original=original, replacement=replacement), self.assertRaisesRegex(
                    target.EvaluationError, 'public_evidence_value_mismatch'):
                target.validate_lesson(bad, self.corpus, catalog)

    def test_every_claim_needs_its_own_source_bound_exact_citations(self):
        for claim_index in range(3):
            bad = synthetic_lesson()
            bad['lesson']['claims'][claim_index]['evidence'] = []
            with self.subTest(claim=claim_index), self.assertRaisesRegex(
                    target.EvaluationError, 'invalid_lesson_claim'):
                target.validate_lesson(bad, self.corpus, self.catalog)
        bad = synthetic_lesson()
        bad['lesson']['claims'][1]['evidence'][0]['observation_id'] = 'another-source-real-id'
        with self.assertRaisesRegex(target.EvaluationError, 'evidence_outside_public_failure_source'):
            target.validate_lesson(bad, self.corpus, self.catalog)
        bad = synthetic_lesson()
        link = bad['lesson']['claims'][0]['evidence'][0]
        bad['lesson']['claims'][0]['evidence'].append(deepcopy(link))
        with self.assertRaisesRegex(target.EvaluationError, 'duplicate_claim_evidence'):
            target.validate_lesson(bad, self.corpus, self.catalog)
        bad = synthetic_lesson()
        bad['lesson']['claims'][0]['evidence'][0]['reason'] = 'an unrequested field'
        with self.assertRaisesRegex(target.EvaluationError, 'invalid_claim_evidence'):
            target.validate_lesson(bad, self.corpus, self.catalog)

    def test_observed_failure_requires_a_real_failed_tool_result(self):
        bad = synthetic_lesson()
        bad['lesson']['claims'][0]['evidence'] = [
            {'observation_id': 'read', 'pointer': '/result/ok', 'value': True}]
        with self.assertRaisesRegex(target.EvaluationError, 'missing_actual_failure_anchor'):
            target.validate_lesson(bad, self.corpus, self.catalog)
        for actor in ('assistant', 'synthetic_test_user'):
            event = scalar_event('invented-failure', ok=False)
            event['actor'] = actor
            with self.subTest(actor=actor), self.assertRaisesRegex(
                    target.EvaluationError, 'invalid_public_observation_catalog'):
                target.public_observations([{'events': [event]}])
        event = scalar_event('duplicated', ok=False)
        with self.assertRaisesRegex(target.EvaluationError, 'invalid_public_observation_catalog'):
            target.public_observations([{'events': [event, deepcopy(event)]}])

    def test_historical_identity_cannot_be_quoted_as_a_lesson_or_citation(self):
        for location in ('scope', 'text', 'value'):
            bad = synthetic_lesson()
            if location == 'scope':
                bad['lesson']['scope'] = 'HISTORICAL_NOTE_ID'
            elif location == 'text':
                bad['lesson']['claims'][1]['text'] = 'HISTORICAL_PROPOSAL_ID'
            else:
                bad['lesson']['claims'][0]['evidence'][0]['value'] = 'HISTORICAL_NOTE_ID'
            with self.subTest(location=location), self.assertRaisesRegex(
                    target.EvaluationError, 'historical_identity_in_lesson'):
                target.validate_lesson(bad, self.corpus, self.catalog)

    def test_true_quotes_do_not_promote_an_unobserved_recovery_to_semantic_truth(self):
        value = synthetic_lesson()
        value['lesson']['claims'][1]['text'] = '遇到版本冲突应重新preview获取最新base'
        candidate = target.validate_lesson(value, self.corpus, self.catalog,
                                           origin='synthetic_provider_output_for_contract_test')
        self.assertIsNone(candidate['semantic_review'])
        self.assertFalse(candidate['grants_authorization'])
        self.assertEqual(candidate['validation_scope'], 'structure_and_exact_public_citations_only')
        self.assertEqual(candidate['candidate_origin'], 'synthetic_provider_output_for_contract_test')
        self.assertEqual(len(candidate['host_evidence']), 3)
        with self.assertRaisesRegex(target.EvaluationError, 'invalid_host_candidate_origin'):
            target.validate_lesson(value, self.corpus, self.catalog, origin='verified_correct')

    def test_claim_kinds_and_size_are_bounded_without_coercion(self):
        for field, replacement in [('scope', 'x' * 101), ('scope', 12)]:
            bad = synthetic_lesson()
            bad['lesson'][field] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(target.EvaluationError):
                target.validate_lesson(bad, self.corpus, self.catalog)
        for field, replacement in [('text', 'x' * 101), ('text', 12), ('kind', 'procedure')]:
            bad = synthetic_lesson()
            bad['lesson']['claims'][1][field] = replacement
            with self.subTest(field=field), self.assertRaises(target.EvaluationError):
                target.validate_lesson(bad, self.corpus, self.catalog)
        bad = synthetic_lesson()
        bad['lesson']['claims'][2]['kind'] = 'stop_condition'
        with self.assertRaisesRegex(target.EvaluationError, 'missing_or_duplicate_claim_kind'):
            target.validate_lesson(bad, self.corpus, self.catalog)


class CompletionContractTests(unittest.TestCase):
    def test_empty_length_parse_and_stop_are_distinct_public_observations(self):
        cases = [('', 'length', True, False, False),
                 ('{"ok":true}', 'length', False, True, False),
                 ('{"ok":', 'length', False, False, False),
                 ('{"ok":true}', 'stop', False, True, True),
                 ('   ', 'stop', True, False, False),
                 ('{"x":1,"x":2}', 'stop', False, False, False),
                 ('{"x":NaN}', 'stop', False, False, False),
                 ('[]', 'stop', False, False, False),
                 ('null', 'stop', False, False, False)]
        for raw, finish, empty, valid, normal in cases:
            with self.subTest(raw=raw, finish=finish):
                observed = target.completion_observation({'raw': raw, 'finish_reason': finish})
                self.assertEqual(observed['empty'], empty)
                self.assertEqual(observed['json_object_valid'], valid)
                self.assertEqual(observed['normal_stop_and_json'], normal)


class PlanningAndSnapshotTests(unittest.TestCase):
    def test_plan_does_not_read_credentials_load_domain_copy_source_or_use_network(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'plan'
            with patch.object(target.previous.common, 'live_configuration', side_effect=AssertionError('key forbidden')), \
                    patch.object(target.previous, 'load_domain', side_effect=AssertionError('domain forbidden')), \
                    patch.object(target.previous, 'load_llm', side_effect=AssertionError('provider forbidden')), \
                    patch.object(target.snapshots, 'create_snapshot', side_effect=AssertionError('copy forbidden')), \
                    patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('network forbidden')), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(target.main(['--run-directory', str(output)]), 0)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'planned')
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertEqual(report['budget']['requested_output_token_bound'], 18000)
            self.assertEqual(report['budget']['max_actual_http_requests'], 7)
            self.assertEqual(report['target_episodes'], 0)
            self.assertFalse((output / 'snapshot').exists())

    def test_real_frozen_prepare_loads_only_frozen_runtime_without_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'frozen-prepare'
            child = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(target.__file__)),
                                    '--prepare-sources', '--run-directory', str(output)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            frozen = (output / 'snapshot').resolve()
            self.assertEqual(report['status'], 'prepared_without_model_calls')
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertEqual(report['budget']['output_tokens_reserved'], 0)
            self.assertTrue(report['snapshot_verified_after_execution'])
            self.assertFalse(report['sources_live_after_run'])
            paths = report['loaded_eval_module_paths'] + report['loaded_domain_module_paths']
            self.assertTrue(paths)
            self.assertEqual(Path(report['execution_script_path']),
                             frozen / 'scripts/eval_procedural_memory_diagnostics.py')
            for path in paths:
                self.assertTrue(Path(path).is_relative_to(frozen), path)
            self.assertNotIn('loaded_llm_module_path', report)
            self.assertFalse((frozen / '.env').exists())
            self.assertFalse((frozen / 'eval/research/procedural_memory_eval_oracles.json').exists())
            self.assertEqual(set(report['source_execution']), {'source-version'})
            self.assertEqual(len(report['source_execution']['source-version']), 1)
            self.assertEqual(report['lesson']['status'], 'prepared_without_model_call')
            self.assertTrue(all(item['status'] == 'prepared_without_model_call' for item in report['diagnostics']))


class ScriptedProvider:
    def __init__(self, style='valid'):
        self.style, self.calls = style, []

    async def complete_raw(self, messages, **options):
        self.calls.append((deepcopy(messages), deepcopy(options)))
        if len(self.calls) <= 6:
            value, finish = {'control': True}, 'stop'
        else:
            payload = json.loads(messages[1]['content'])
            catalog = target.public_observations(payload['public_processes'])
            failed = next(identifier for identifier, event in catalog.items() if event['result'].get('ok') is False)
            read = next(identifier for identifier, event in catalog.items()
                        if event['result'].get('note', {}).get('selected_version_is_current') is False)
            value, finish = synthetic_lesson(failed, read), 'stop'
            if self.style == 'abstain':
                value = {'lesson': None}
            elif self.style == 'length':
                finish = 'length'
            elif self.style == 'extra':
                value['lesson']['claims'][0]['evidence'][0]['invented'] = 'field'
            elif self.style == 'error':
                raise RuntimeError('scripted_failure')
        text = json.dumps(value, ensure_ascii=False)
        if len(self.calls) == 7 and self.style == 'empty':
            text, finish = '', 'length'
        elif len(self.calls) == 7 and self.style == 'invalid_json':
            text = '{"lesson":'
        return {'text': text, 'finish_reason': finish,
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20},
                'reasoning_content': 'HIDDEN_DIAGNOSTIC_REASONING_SENTINEL',
                'reasoning': 'HIDDEN_DIAGNOSTIC_REASONING_SENTINEL'}


class PipelineContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        import httpx
        guard = patch.object(httpx.AsyncHTTPTransport, 'handle_async_request',
                             side_effect=AssertionError('real network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.inputs = target.load_inputs()
        self.writer_serial = 0

    def report_writer(self, label):
        config = SimpleNamespace(llm_provider='fake', llm_model='synthetic-model', llm_reasoning_effort='low')
        report = target.make_report(self.inputs, 'offline_contract_test', config)
        self.writer_serial += 1
        writer = target.previous.ReportWriter(Path(self.directory.name) / f'{label}-{self.writer_serial}.json')
        return report, writer

    async def run_scripted(self, style='valid'):
        report, writer = self.report_writer(style)
        provider = ScriptedProvider(style)
        await target.execute(report, writer, provider, self.inputs)
        return report, writer, provider

    async def test_seven_slots_are_single_attempt_same_message_budget_pairs(self):
        report, writer, provider = await self.run_scripted()
        self.assertEqual(report['status'], 'complete_pending_review')
        self.assertEqual(report['budget']['calls_reserved'], 7)
        self.assertEqual(report['budget']['calls_completed'], 7)
        self.assertEqual(report['budget']['fake_provider_calls'], 7)
        self.assertEqual(report['budget']['output_tokens_reserved'], 18000)
        self.assertEqual(report['target_episodes'], 0)
        self.assertIsNone(report['semantic_scores'])
        for index, limits in enumerate(target.OUTPUT_ORDERS):
            left, right = provider.calls[index * 2:index * 2 + 2]
            self.assertEqual(left[0], right[0])
            self.assertEqual(left[0], self.inputs['inputs'][index]['messages'])
            self.assertEqual((left[1]['max_tokens'], right[1]['max_tokens']), limits)
            left_options, right_options = deepcopy(left[1]), deepcopy(right[1])
            left_options.pop('max_tokens')
            right_options.pop('max_tokens')
            self.assertEqual(left_options, right_options)
        for messages, options in provider.calls:
            self.assertEqual(options['max_attempts'], 1)
            self.assertEqual(options['temperature'], .1)
            self.assertTrue(options['json_mode'])
            self.assertEqual(options['model'], 'synthetic-model')
            self.assertEqual(options['reasoning_effort'], 'low')
            self.assertNotIn('GOLD_EXPECTATION_SENTINEL', json.dumps(messages))
        self.assertNotIn('HIDDEN_DIAGNOSTIC_REASONING_SENTINEL', writer.path.read_text(encoding='utf-8'))
        self.assertIsNone(report['lesson']['candidate']['semantic_review'])

    async def test_all_failures_abstention_and_truncation_keep_exact_seven_calls_without_repairs(self):
        expected = [('empty', 'rejected_or_error', 'empty_model_output'),
                    ('length', 'rejected_or_error', 'lesson_not_normally_terminated'),
                    ('invalid_json', 'rejected_or_error', 'invalid_model_json'),
                    ('extra', 'rejected_or_error', 'invalid_claim_evidence'),
                    ('error', 'rejected_or_error', 'RuntimeError'),
                    ('abstain', 'abstained', None)]
        for style, status, error in expected:
            with self.subTest(style=style):
                report, writer, provider = await self.run_scripted(style)
                self.assertEqual(len(provider.calls), 7)
                self.assertEqual(report['budget']['calls_reserved'], 7)
                self.assertEqual(report['lesson']['status'], status)
                self.assertEqual(report['lesson'].get('error_code'), error)
                self.assertIsNone(report['lesson']['candidate'])
                self.assertIsNone(report['lesson']['semantic_review'])
                self.assertFalse(report['sources_live_after_run'])
                self.assertNotIn('HIDDEN_DIAGNOSTIC_REASONING_SENTINEL', writer.path.read_text(encoding='utf-8'))

    async def test_global_and_per_record_bounds_consume_exception_slots(self):
        report, writer = self.report_writer('bounds')
        class FailingProvider:
            calls = 0
            async def complete_raw(inner, messages, **options):
                inner.calls += 1
                self.assertEqual(options['max_attempts'], 1)
                raise RuntimeError('offline transport failure')
        provider = FailingProvider()
        bounded = target.ProbeProvider(provider, report, writer)
        records = report['diagnostics'] + [report['lesson']]
        for record in records:
            with self.assertRaises(RuntimeError):
                await bounded.request(record, self.inputs['inputs'][0]['messages'])
            with self.assertRaisesRegex(target.EvaluationError, 'diagnostic_budget_exhausted'):
                await bounded.request(record, self.inputs['inputs'][0]['messages'])
        self.assertEqual(provider.calls, 7)
        self.assertEqual(report['budget']['calls_reserved'], 7)
        self.assertEqual(report['budget']['calls_completed'], 0)
        self.assertEqual(report['budget']['output_tokens_reserved'], 18000)
        with self.assertRaisesRegex(target.EvaluationError, 'diagnostic_budget_exhausted'):
            await bounded.request({'output_tokens': 1200, 'calls': []}, [])
        self.assertEqual(provider.calls, 7)

    async def test_token_bound_bad_limits_and_live_requires_binding_before_provider(self):
        for reserved, limit, mode, error in [
            (18000, 1200, 'offline_contract_test', 'diagnostic_budget_exhausted'),
            (17000, 1200, 'offline_contract_test', 'diagnostic_budget_exhausted'),
            (0, True, 'offline_contract_test', 'diagnostic_budget_exhausted'),
            (0, 2400, 'offline_contract_test', 'diagnostic_budget_exhausted'),
            (0, 1200, 'live', 'live_requires_frozen_source_binding')]:
            report, writer = self.report_writer('limits')
            report['mode'] = mode
            report['budget']['output_tokens_reserved'] = reserved
            provider = ScriptedProvider()
            bounded = target.ProbeProvider(provider, report, writer)
            with self.subTest(reserved=reserved, limit=limit, mode=mode), self.assertRaisesRegex(target.EvaluationError, error):
                await bounded.request({'output_tokens': limit, 'calls': []}, [])
            self.assertFalse(provider.calls)
            self.assertEqual(report['budget']['calls_reserved'], 0)

    async def test_actual_temporary_store_reopens_fresh_source_then_closes_resolver(self):
        domain = target.previous.load_domain()
        original_type, original_store = domain.SourceCorpus, target.previous.store_card
        corpora, saved_paths, saved_readbacks = [], [], []
        def capture_corpus(cases):
            corpus = original_type(cases)
            corpora.append(corpus)
            return corpus
        def capture_store(store_type, path, envelope):
            self.assertTrue(Path(path).parent.exists())
            reference, reread = original_store(store_type, path, envelope)
            reopened = store_type(str(path)).get_version(reference['id'], reference['version'])
            self.assertEqual(json.loads(reopened['body']), envelope)
            saved_paths.append(Path(path))
            saved_readbacks.append(reread)
            return reference, reread
        with patch.object(domain, 'SourceCorpus', side_effect=capture_corpus), \
                patch.object(target.previous, 'store_card', side_effect=capture_store):
            first, _, _ = await self.run_scripted('valid')
            second, _, _ = await self.run_scripted('valid')
        self.assertEqual(len(corpora), 2)
        self.assertEqual(len(saved_paths), 2)
        for report, corpus, path, reread in zip((first, second), corpora, saved_paths, saved_readbacks):
            lesson = report['lesson']
            self.assertEqual(lesson['status'], 'accepted_structurally_pending_review')
            self.assertTrue(lesson['reopen_equal'])
            self.assertEqual(reread, lesson['candidate'])
            self.assertFalse(path.exists())
            self.assertFalse(report['sources_live_after_run'])
            observation = lesson['candidate']['host_evidence'][0]['citation']['observation_id']
            with self.assertRaisesRegex(ValueError, 'source_corpus_closed'):
                corpus.resolve_evidence(observation)
            self.assertEqual(set(corpus.recipe_observations), {'source-version-b'})
            for check in corpus.preparation_checks.values():
                self.assertTrue(check['actual_recipe_materialized'])
        first_ids = set(corpora[0].catalog)
        self.assertTrue(first_ids.isdisjoint(corpora[1].catalog))
        lesson_public = json.dumps(first['lesson']['input_messages'], ensure_ascii=False)
        for secret in ('host_only_role', 'fault_schedule', 'source_bundle_id', 'target_oracle',
                       'source-version-b', 'HIDDEN_DIAGNOSTIC_REASONING_SENTINEL'):
            self.assertNotIn(secret, lesson_public)


if __name__ == '__main__':
    unittest.main()
