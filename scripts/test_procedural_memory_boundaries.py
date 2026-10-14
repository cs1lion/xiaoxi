"""Offline boundary contracts; a passed structure never means a supported claim."""
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
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_procedural_memory_boundaries as target


def citation(event, field):
    leaves = dict(target.scalar_paths(event['result']))
    path = next(candidate for candidate in leaves if candidate.rsplit('/', 1)[-1] == field)
    return {'observation_id': event['result']['observation_id'], 'field': field, 'value': leaves[path]}


def grounded_lesson(catalog, *, data_text='公开完整正文可核对目标文本是否唯一', conflation=False):
    reads = [event for event in catalog.values() if event.get('action') == 'read']
    previews = [event for event in catalog.values() if event.get('action') == 'preview']
    failures = [event for event in catalog.values() if event['result'].get('ok') is False]
    data_event = reads[0] if reads else previews[0]
    data_field = 'body' if conflation else 'body_truncated'
    if data_field not in {path.rsplit('/', 1)[-1] for path, _ in target.scalar_paths(data_event['result'])}:
        data_field = 'body'
    capability_event = failures[0] if failures else previews[0]
    authorization_event = previews[0] if previews else failures[0]
    return {'lesson': {'record_type': 'failure_lesson', 'scope': '合成合同测试，语义未评估', 'claims': [
        {'kind': 'data_sufficiency', 'text': data_text,
         'evidence': [citation(data_event, data_field)]},
        {'kind': 'capability_boundary', 'text': '当前工具能否完成下一步需以回执为准',
         'evidence': [citation(capability_event, 'ok' if capability_event['result'].get('ok') is True
                               else 'error')]},
        {'kind': 'authorization_boundary', 'text': '确认是否针对当前提案需单独核对',
         'evidence': [citation(authorization_event, 'confirmation_required'
                               if authorization_event['result'].get('confirmation_required') is not None
                               else 'error')]},
    ]}}


def simple_corpus():
    return SimpleNamespace(forbidden_identifiers={'HISTORICAL_NOTE_ID'},
                           resolve_evidence=lambda identifier: {'actual_observation': identifier})


def simple_catalog():
    read = {'actor': 'domain_tool', 'action': 'read', 'arguments': {},
            'result': {'observation_id': 'read', 'ok': True, 'note': {'body': '等待复核。', 'body_truncated': False}}}
    preview = {'actor': 'domain_tool', 'action': 'preview', 'arguments': {},
               'result': {'observation_id': 'preview', 'ok': True, 'confirmation_required': True}}
    failure = {'actor': 'domain_tool', 'action': 'confirm', 'arguments': {},
               'result': {'observation_id': 'failure', 'ok': False, 'error': 'capability_unavailable',
                          'write_observed': False, 'update_attempts': 0, 'automatic_retries': 0}}
    return {'read': read, 'preview': preview, 'failure': failure}


def setUpModule():
    """评测夹具是本地撰写的语料（随 /eval 清理被删、从未入 git）；缺失时整组跳过而非报红。"""
    fixture = Path(__file__).resolve().parents[1] / "eval/research/procedural_memory_boundary_cases_v1.json"
    if not fixture.exists():
        raise unittest.SkipTest("缺少本地评测夹具 procedural_memory_boundary_cases_v1.json（曾随 /eval 清理被删，未入 git）；补齐该文件后自动恢复")


class SpecificationTests(unittest.TestCase):
    def test_fixed_boundary_specification_and_role_mapping(self):
        cases = target.load_sources()
        self.assertEqual([b['id'] for b in cases['source_bundles']], ['boundary-truncated',
            'boundary-concurrent-unique', 'boundary-concurrent-ambiguous', 'boundary-missing-confirm'])
        for bundle in cases['source_bundles']:
            self.assertEqual(len(bundle['recipes']), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spec.json'
            payload = json.loads(target.CASE_PATH.read_text(encoding='utf-8-sig'))
            payload['source_bundles'].pop()
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(target.EvaluationError, 'invalid_fixed_boundary_specification'):
                target.load_sources(path)
            payload = json.loads(target.CASE_PATH.read_text(encoding='utf-8-sig'))
            payload['source_bundles'][1]['recipes'][0]['host_only_role'] = 'missing_confirm_capability'
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(target.EvaluationError, 'invalid_boundary_recipe_role'):
                target.load_sources(path)


class ClaimContractTests(unittest.TestCase):
    def setUp(self):
        self.corpus, self.catalog = simple_corpus(), simple_catalog()

    def test_each_kind_accepts_only_its_own_core_field(self):
        for kind, field, error in [
                ('data_sufficiency', 'error', 'claim_missing_kind_grounded_citation'),
                ('capability_boundary', 'body', 'claim_missing_kind_grounded_citation'),
                ('authorization_boundary', 'update_attempts', 'claim_missing_kind_grounded_citation'),
                ('authorization_boundary', 'body', 'claim_missing_kind_grounded_citation'),
                ('data_sufficiency', 'missing', 'public_evidence_field_missing'),
                ('data_sufficiency', 'note/body', 'invalid_public_evidence_field')]:
            value = grounded_lesson(self.catalog)
            claim = next(item for item in value['lesson']['claims'] if item['kind'] == kind)
            event = self.catalog['read' if field == 'body' else 'failure']
            claim['evidence'] = ([{'observation_id': event['result']['observation_id'],
                                   'field': field, 'value': 'x'}]
                                  if field in ('missing', 'note/body') else [citation(event, field)])
            with self.subTest(kind=kind, field=field), self.assertRaisesRegex(
                    target.EvaluationError, error):
                target.validate_lesson(value, self.corpus, self.catalog)

    def test_field_names_and_values_are_resolved_inside_the_cited_event(self):
        result = {'ok': True, 'note': {'body': 'x', 'body_truncated': True},
                  'nested': {'flag': True, 'items': [None, '原话', 3.5]}}
        self.assertEqual(target.field_matches(result, 'body_truncated', True), ['/note/body_truncated'])
        self.assertEqual(target.field_matches(result, 'body', 'x'), ['/note/body'])
        self.assertEqual(target.field_matches(result, 'flag', True), ['/nested/flag'])
        for field, value, error in [('missing', True, 'public_evidence_field_missing'),
                                    ('body', 'x ', 'public_evidence_value_mismatch'),
                                    ('body_truncated', 1, 'public_evidence_value_mismatch'),
                                    ('body_truncated', 'true', 'public_evidence_value_mismatch'),
                                    ('note/body', 'x', 'invalid_public_evidence_field'),
                                    ('body~', 'x', 'invalid_public_evidence_field'),
                                    ('', 'x', 'invalid_public_evidence_field')]:
            with self.subTest(field=field, value=value), self.assertRaisesRegex(target.EvaluationError, error):
                target.field_matches(result, field, value)
        self.assertEqual(list(target.scalar_paths({'a': [1, {'b': None}]})),
                         [('/a/0', 1), ('/a/1/b', None)])

    def test_three_distinct_kinds_each_with_their_own_exact_citation(self):
        value = grounded_lesson(self.catalog)
        result = target.validate_lesson(value, self.corpus, self.catalog)
        self.assertEqual([item['claim_kind'] for item in result['host_evidence']],
                         ['data_sufficiency', 'capability_boundary', 'authorization_boundary'])
        self.assertTrue(all(item['matched_public_paths'] for item in result['host_evidence']))
        self.assertTrue(all(item['matched_public_paths'][0].startswith('/')
                            for item in result['host_evidence']))
        self.assertTrue(all(item['kind_grounding'] for item in result['host_evidence']))
        self.assertIsNone(result['semantic_review'])
        self.assertFalse(result['grants_authorization'])
        # An extra non-core citation is allowed while the claim keeps a core one.
        extra = deepcopy(value)
        extra['lesson']['claims'][0]['evidence'].append(
            {'observation_id': 'preview', 'field': 'ok', 'value': True})
        accepted = target.validate_lesson(extra, self.corpus, self.catalog)
        self.assertEqual([item['kind_grounding'] for item in accepted['host_evidence']],
                         [True, False, True, True])
        for mutation, error in [('drop_kind', 'invalid_lesson_structure'),
                                ('duplicate_kind', 'claim_missing_kind_grounded_citation'),
                                ('wrong_kind', 'invalid_lesson_claim'),
                                ('duplicate_kind_valid_field', 'missing_or_duplicate_claim_kind'),
                                ('all_extra_citations', 'claim_missing_kind_grounded_citation'),
                                ('empty_evidence', 'invalid_lesson_claim'),
                                ('duplicate_field', 'duplicate_claim_evidence'),
                                ('foreign_source', 'evidence_outside_boundary_source'),
                                ('wrong_value', 'public_evidence_value_mismatch'),
                                ('wrong_type', 'public_evidence_value_mismatch'),
                                ('identity', 'historical_identity_in_lesson')]:
            bad = grounded_lesson(self.catalog)
            if mutation == 'drop_kind':
                bad['lesson']['claims'] = bad['lesson']['claims'][:2]
            elif mutation == 'duplicate_kind':
                bad['lesson']['claims'][2]['kind'] = 'capability_boundary'
            elif mutation == 'wrong_kind':
                bad['lesson']['claims'][2]['kind'] = 'unclassified_boundary'
            elif mutation == 'duplicate_kind_valid_field':
                bad['lesson']['claims'][2]['kind'] = 'capability_boundary'
                bad['lesson']['claims'][2]['evidence'] = [
                    {'observation_id': 'failure', 'field': 'ok', 'value': False}]
            elif mutation == 'all_extra_citations':
                bad['lesson']['claims'][1]['evidence'] = [
                    {'observation_id': 'read', 'field': 'body', 'value': '等待复核。'}]
            elif mutation == 'empty_evidence':
                bad['lesson']['claims'][1]['evidence'] = []
            elif mutation == 'duplicate_field':
                bad['lesson']['claims'][1]['evidence'].append(deepcopy(bad['lesson']['claims'][1]['evidence'][0]))
            elif mutation == 'foreign_source':
                bad['lesson']['claims'][1]['evidence'][0]['observation_id'] = 'another-source-real-id'
            elif mutation == 'wrong_value':
                bad['lesson']['claims'][1]['evidence'][0]['value'] = 'capability_unavailable '
            elif mutation == 'wrong_type':
                bad['lesson']['claims'][0]['evidence'][0]['value'] = 'false'
            else:
                bad['lesson']['scope'] = 'HISTORICAL_NOTE_ID'
            with self.subTest(mutation=mutation), self.assertRaisesRegex(target.EvaluationError, error):
                target.validate_lesson(bad, self.corpus, self.catalog)

    def test_conflation_passes_structure_but_never_becomes_semantic_truth(self):
        # A complete, uniquely matching body cited while the claim says applicability is unknowable.
        value = grounded_lesson(self.catalog, conflation=True,
                                data_text='无法判断替换是否仍适用于当前正文')
        result = target.validate_lesson(value, self.corpus, self.catalog)
        self.assertEqual(result['validation_scope'],
                         'structure_and_kind_grounded_public_citations_only')
        self.assertIsNone(result['semantic_review'])
        with self.assertRaisesRegex(target.EvaluationError, 'invalid_host_candidate_origin'):
            target.validate_lesson(value, self.corpus, self.catalog, origin='semantically_verified')

    def test_public_observation_catalog_rejects_non_tool_and_duplicate_ids(self):
        event = {'actor': 'assistant', 'action': 'confirm', 'arguments': {},
                 'result': {'observation_id': 'invented', 'ok': False}}
        with self.assertRaisesRegex(target.EvaluationError, 'invalid_public_observation_catalog'):
            target.public_observations([{'events': [event]}])
        tool = {'actor': 'domain_tool', 'action': 'confirm', 'arguments': {},
                'result': {'observation_id': 'twice', 'ok': False}}
        with self.assertRaisesRegex(target.EvaluationError, 'invalid_public_observation_catalog'):
            target.public_observations([{'events': [tool, deepcopy(tool)]}])


class ScriptedProvider:
    def __init__(self, style):
        self.style, self.calls = style, []

    async def complete_raw(self, messages, **options):
        self.calls.append((deepcopy(messages), deepcopy(options)))
        payload = json.loads(messages[1]['content'])
        catalog = target.public_observations(payload['public_processes'])
        if self.style == 'empty':
            return {'text': '', 'finish_reason': 'length', 'usage': {'prompt_tokens': 5, 'completion_tokens': 5},
                    'reasoning_content': 'HIDDEN_REASONING_SENTINEL'}
        if self.style == 'conflated':
            value = grounded_lesson(catalog, conflation=True, data_text='无法判断替换是否仍适用')
        elif self.style == 'null':
            value = {'lesson': None}
        elif self.style == 'cross_kind':
            value = grounded_lesson(catalog)
            failure = next(event for event in catalog.values() if event['result'].get('ok') is False)
            value['lesson']['claims'][0]['evidence'] = [citation(failure, 'error')]
        else:
            value = grounded_lesson(catalog)
        return {'text': json.dumps(value, ensure_ascii=False), 'finish_reason': 'stop',
                'usage': {'prompt_tokens': 11, 'completion_tokens': 12},
                'reasoning_content': 'HIDDEN_REASONING_SENTINEL'}


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        guard = patch.object(httpx.AsyncHTTPTransport, 'handle_async_request',
                             side_effect=AssertionError('real network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cases = target.load_sources()

    async def run_scripted(self, style):
        config = SimpleNamespace(llm_provider='fake', llm_model='synthetic-model', llm_reasoning_effort='')
        report = target.make_report(self.cases, 'offline_contract_test', config)
        writer = target.previous.ReportWriter(Path(self.directory.name) / (style + '.json'))
        provider = ScriptedProvider(style)
        await target.execute(report, writer, provider, self.cases)
        return report, writer, provider

    async def test_grounded_candidates_are_saved_and_reopened_without_quality_claims(self):
        report, writer, provider = await self.run_scripted('grounded')
        self.assertEqual(report['budget']['calls_reserved'], 4)
        self.assertEqual(report['budget']['calls_completed'], 4)
        self.assertEqual(report['budget']['fake_provider_calls'], 4)
        self.assertEqual(report['budget']['output_tokens_reserved'], 24000)
        self.assertEqual(report['pipeline_version'], 'boundaries_v4')
        self.assertEqual(report['budget']['max_output_tokens_per_request'], 6000)
        self.assertEqual(report['status'], 'complete_pending_review')
        self.assertFalse(report['sources_live_after_run'])
        self.assertIsNone(report['semantic_scores'])
        self.assertIsNone(report['manual_review'])
        self.assertTrue(all(item['status'] == 'accepted_structurally_pending_review' and item['reopen_equal']
                            for item in report['induction']))
        self.assertTrue(all(item['lesson']['semantic_review'] is None for item in report['induction']))
        self.assertTrue(all(item['lesson']['candidate_origin'] == 'synthetic_provider_output_for_contract_test'
                            for item in report['induction']))
        for messages, options in provider.calls:
            self.assertEqual(options['max_tokens'], 6000)
            self.assertEqual(options['max_attempts'], 1)
            self.assertNotIn('HIDDEN_REASONING_SENTINEL', json.dumps(messages, ensure_ascii=False))

    async def test_empty_cross_kind_and_null_outputs_are_recorded_without_retry(self):
        for style, status, error in [('empty', 'rejected_or_error', 'empty_model_output'),
                                     ('cross_kind', 'rejected_or_error', 'claim_missing_kind_grounded_citation'),
                                     ('null', 'abstained', None)]:
            report, writer, provider = await self.run_scripted(style)
            with self.subTest(style=style):
                self.assertEqual(len(provider.calls), 4)
                self.assertTrue(all(item['status'] == status for item in report['induction']))
                if error:
                    self.assertTrue(all(item['error_code'] == error for item in report['induction']))
                self.assertTrue(all(len(item['calls']) == 1 for item in report['induction']))
                self.assertFalse(report['sources_live_after_run'])

    async def test_conflated_candidate_is_accepted_structurally_but_never_scored(self):
        report, writer, provider = await self.run_scripted('conflated')
        self.assertEqual(report['status'], 'complete_pending_review')
        for item in report['induction']:
            self.assertEqual(item['status'], 'accepted_structurally_pending_review')
            self.assertIsNone(item['semantic_review'])
            self.assertIsNone(item['lesson']['semantic_review'])
            self.assertEqual(item['lesson']['validation_scope'],
                             'structure_and_kind_grounded_public_citations_only')


class EntryPointTests(unittest.TestCase):
    def test_plan_does_not_load_domain_credentials_or_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'plan'
            with patch.object(target.previous.common, 'live_configuration', side_effect=AssertionError('key forbidden')):
                with patch.object(target.previous, 'load_domain', side_effect=AssertionError('domain forbidden')):
                    with patch.object(target.snapshots, 'create_snapshot', side_effect=AssertionError('copy forbidden')):
                        with redirect_stdout(io.StringIO()):
                            self.assertEqual(target.main(['--run-directory', str(output)]), 0)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['budget']['max_actual_http_requests'], 4)
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertEqual(report['target_episodes'], 0)
            self.assertFalse((output / 'snapshot').exists())

    def test_real_frozen_prepare_runs_all_boundary_recipes_without_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'frozen-prepare'
            child = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(target.__file__)),
                                    '--prepare-sources', '--run-directory', str(output)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=120)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            frozen = (output / 'snapshot').resolve()
            self.assertEqual(report['status'], 'sources_prepared_without_model_calls')
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertTrue(report['snapshot_verified_after_execution'])
            self.assertEqual(len(report['induction']), 4)
            self.assertTrue(all(item['status'] == 'source_prepared_without_model_call' for item in report['induction']))
            for path in report['loaded_eval_module_paths'] + report['loaded_domain_module_paths']:
                self.assertTrue(Path(path).is_relative_to(frozen), path)
            self.assertFalse((frozen / '.env').exists())
            self.assertFalse((frozen / 'eval/research/procedural_memory_eval_oracles.json').exists())
            self.assertFalse((frozen / 'eval/research/procedural_memory_diagnostic_inputs_v1.json').exists())
            self.assertFalse(report['sources_live_after_run'])


if __name__ == '__main__':
    unittest.main()
