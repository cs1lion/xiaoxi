"""Offline V2 induction contracts; scripted candidates are not learned cards."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_procedural_memory_induction as target


def candidate(ids):
    return {'card': {'record_type': 'procedure', 'scope': '合成合同测试，语义未评估',
                     'requires': ['当前资料和能力需重新核对'], 'advice': ['以当前工具结果为准'],
                     'evidence': [{'observation_id': identifier} for identifier in ids]}}


def setUpModule():
    """评测夹具是本地撰写的语料（随 /eval 清理被删、从未入 git）；缺失时整组跳过而非报红。"""
    fixture = Path(__file__).resolve().parents[1] / "eval/research/procedural_memory_eval_cases.json"
    if not fixture.exists():
        raise unittest.SkipTest("缺少本地评测夹具 procedural_memory_eval_cases.json（曾随 /eval 清理被删，未入 git）；补齐该文件后自动恢复")


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.corpus = SimpleNamespace(forbidden_identifiers={'HISTORIC_ID'},
                                      resolve_evidence=lambda identifier: {'actual_id': identifier})

    def test_ambiguous_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        with self.assertRaisesRegex(target.EvaluationError, 'duplicate_model_json_key'):
            target.strict_json('{"card":null,"card":{"requires":[]}}')
        with self.assertRaisesRegex(target.EvaluationError, 'nonfinite_model_json'):
            target.strict_json('{"card":NaN}')
        self.assertEqual(target.strict_json('{"card":null}'), {'card': None})

    def test_source_bound_distinct_evidence_and_exact_fields(self):
        value = candidate(['observed-a', 'observed-b'])
        result = target.validate_card(value, self.corpus, {'observed-a', 'observed-b'})
        self.assertFalse(result['grants_authorization'])
        self.assertEqual(result['content'], value['card'])
        bad = deepcopy(value)
        bad['card']['evidence'][0]['event'] = 'not permitted'
        with self.assertRaisesRegex(target.EvaluationError, 'invalid_card_evidence_pointer'):
            target.validate_card(bad, self.corpus, {'observed-a', 'observed-b'})
        with self.assertRaisesRegex(target.EvaluationError, 'v2_card_evidence_count_or_duplicate'):
            target.validate_card(candidate(['observed-a', 'observed-a']), self.corpus, {'observed-a'})
        with self.assertRaisesRegex(target.EvaluationError, 'evidence_outside_induction_source'):
            target.validate_card(value, self.corpus, {'observed-a'})

    def test_card_limits_include_whitespace_and_do_not_coerce_values(self):
        value = candidate(['a', 'b'])
        for field, replacement, error in [('scope', ' ' * 120 + 'a', 'v2_card_scope_too_long'),
                                          ('advice', [' ' * 100 + 'a'], 'v2_card_items_too_long_or_many'),
                                          ('requires', ['a'] * 5, 'v2_card_items_too_long_or_many')]:
            bad = deepcopy(value)
            bad['card'][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(target.EvaluationError, error):
                target.validate_card(bad, self.corpus, {'a', 'b'})
        bad = deepcopy(value)
        bad['card']['scope'] = 7
        with self.assertRaises(target.EvaluationError):
            target.validate_card(bad, self.corpus, {'a', 'b'})

    def test_plan_does_not_load_credentials_domain_snapshot_or_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'plan'
            with patch.object(target.previous.common, 'live_configuration', side_effect=AssertionError('key forbidden')):
                with patch.object(target.previous, 'load_domain', side_effect=AssertionError('domain forbidden')):
                    with patch.object(target.snapshots, 'create_snapshot', side_effect=AssertionError('copy forbidden')):
                        with redirect_stdout(io.StringIO()):
                            self.assertEqual(target.main(['--run-directory', str(output)]), 0)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['budget']['max_actual_http_requests'], 3)
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertEqual(report['target_episodes'], 0)
            self.assertFalse((output / 'snapshot').exists())

    def test_real_frozen_prepare_loads_all_runtime_files_from_snapshot_without_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'frozen-prepare'
            child = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(target.__file__)),
                                    '--prepare-sources', '--run-directory', str(output)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(child.returncode, 0, child.stdout)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            frozen = (output / 'snapshot').resolve()
            self.assertEqual(report['status'], 'sources_prepared_without_model_calls')
            self.assertEqual(report['target_episodes'], 0)
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertTrue(report['snapshot_verified_after_execution'])
            for path in report['loaded_eval_module_paths'] + report['loaded_domain_module_paths']:
                self.assertTrue(Path(path).is_relative_to(frozen), path)
            self.assertFalse((frozen / '.env').exists())
            self.assertFalse((frozen / 'eval/research/procedural_memory_eval_oracles.json').exists())
            self.assertFalse(report['sources_live_after_run'])


class ScriptedProvider:
    def __init__(self, style):
        self.style, self.calls = style, []

    async def complete_raw(self, messages, **options):
        self.calls.append((deepcopy(messages), deepcopy(options)))
        payload = json.loads(messages[1]['content'])
        processes = payload['public_processes']
        if payload['layout'] == 'reversible_content_tables':
            processes = target.projection.expand_public_bundle(processes)
        ids = sorted(target.previous.observation_ids(processes))[:2]
        value = candidate(ids)
        if self.style == 'null':
            value = {'card': None}
        elif self.style == 'extra':
            value['card']['evidence'][0]['event'] = 'an unrequested field'
        text = '' if self.style == 'empty' else json.dumps(value, ensure_ascii=False)
        return {'text': text, 'finish_reason': 'length' if self.style in ('empty', 'length') else 'stop',
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10},
                'reasoning_content': 'HIDDEN_REASONING_SENTINEL'}


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
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
        await target.execute_induction(report, writer, provider, cases=self.cases)
        return report, writer, provider

    async def test_actual_candidate_save_reopen_and_input_isolation_without_quality_claims(self):
        report, writer, provider = await self.run_scripted('valid')
        self.assertEqual(report['budget']['calls_reserved'], 3)
        self.assertEqual(report['budget']['calls_completed'], 3)
        self.assertEqual(report['budget']['fake_provider_calls'], 3)
        self.assertEqual(report['target_episodes'], 0)
        self.assertEqual(report['status'], 'induction_complete_pending_review')
        self.assertFalse(report['sources_live_after_run'])
        self.assertIsNone(report['semantic_scores'])
        self.assertTrue(all(i['status'] == 'accepted' and i['reopen_equal'] for i in report['induction']))
        self.assertTrue(all(i['card']['candidate_origin'] == 'synthetic_provider_output_for_contract_test'
                            for i in report['induction']))
        self.assertEqual(report['source_projections']['source-local']['layout'], 'reversible_content_tables')
        for messages, options in provider.calls:
            self.assertEqual(options['max_tokens'], 1200)
            self.assertEqual(options['max_attempts'], 1)
            public = json.dumps(messages, ensure_ascii=False)
            for forbidden in ('human_card', 'target_oracle', 'host_only_role', 'fault_schedule',
                              'GOLD_EXPECTATION_SENTINEL', 'source_bundle_id', 'host_only_source_bundle_id',
                              '未展示附录：等待扫描。', '最后一条留档：继续保留。'):
                self.assertNotIn(forbidden, public)
        self.assertNotIn('HIDDEN_REASONING_SENTINEL', writer.path.read_text(encoding='utf-8'))

    async def test_failed_empty_abstained_and_length_outputs_keep_all_sources_without_repair(self):
        for style, status, error in [('extra', 'rejected_or_error', 'invalid_card_evidence_pointer'),
                                     ('empty', 'rejected_or_error', 'empty_model_output'),
                                     ('length', 'rejected_or_error', 'output_token_limit_reached'),
                                     ('null', 'abstained', None)]:
            with self.subTest(style=style):
                report, _, provider = await self.run_scripted(style)
                self.assertEqual(len(report['induction']), 3)
                self.assertEqual(len(provider.calls), 3)
                self.assertTrue(all(i['status'] == status and i.get('error_code') == error
                                    for i in report['induction']))
                self.assertTrue(all(i['card'] is None and i['semantic_scores'] is None for i in report['induction']))

    async def test_three_request_global_guard_and_live_freeze_requirement(self):
        report, writer, provider = await self.run_scripted('null')
        bounded = target.InductionProvider(provider, report, writer)
        bounded.begin_episode({'calls': []}, 1)
        with self.assertRaisesRegex(target.EvaluationError, 'model_budget_exhausted'):
            await bounded.request([])
        self.assertEqual(len(provider.calls), 3)
        report['mode'] = 'live'
        report['budget']['calls_reserved'] = 0
        with self.assertRaisesRegex(target.EvaluationError, 'live_requires_frozen_source_binding'):
            await bounded.request([])
        self.assertEqual(len(provider.calls), 3)


if __name__ == '__main__':
    unittest.main()
