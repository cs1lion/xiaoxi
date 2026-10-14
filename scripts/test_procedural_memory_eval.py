"""Offline contracts for the procedural-memory pilot.

Synthetic provider output exercises the evaluation pipeline, not the usefulness
of learned procedures. All domain writes use temporary stores; no live model,
production database, environment file or credentials are accessed.
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
import eval_procedural_memory as target


class PlanTests(unittest.TestCase):
    def test_default_plan_does_not_load_domain_or_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'plan.json'
            with patch.object(target, 'load_domain', side_effect=AssertionError('domain forbidden')):
                with patch.object(target.common, 'live_configuration', side_effect=AssertionError('key forbidden')):
                    with patch.dict(os.environ, {
                        'LLM_API_KEY': 'PRODUCTION_SECRET_SENTINEL',
                        'PROCEDURAL_MEMORY_EVAL_API_KEY': 'EVAL_SECRET_SENTINEL',
                    }):
                        with redirect_stdout(io.StringIO()):
                            self.assertEqual(target.main(['--output', str(output)]), 0)
            persisted = output.read_text(encoding='utf-8')
            report = json.loads(persisted)
            self.assertEqual(report['mode'], 'plan')
            self.assertEqual(report['budget']['calls_reserved'], 0)
            self.assertNotIn('SECRET_SENTINEL', persisted)

    def test_frozen_plan_keeps_every_condition_and_budget(self):
        cases, _ = target.load_specs()
        report = target.make_report(cases)
        self.assertEqual(len(report['induction']), 3)
        self.assertEqual(len(report['episodes']), 24)
        self.assertEqual(report['budget']['max_actual_http_requests'], 99)
        self.assertEqual(report['budget']['requested_output_token_bound'], 118800)
        self.assertEqual(report['budget']['max_requests_per_episode'], 4)
        for identifier in {episode['target_id'] for episode in report['episodes']}:
            self.assertEqual({episode['group'] for episode in report['episodes']
                              if episode['target_id'] == identifier}, {'N', 'E', 'P', 'G'})
        self.assertTrue(all(episode['semantic_scores'] is None for episode in report['episodes']))
        self.assertFalse(report['mixed_store_retrieval_evaluated'])

    def test_execution_input_is_whitelisted_and_hidden_material_is_refused(self):
        public = {'user_goal': '讨论当前任务', 'note': {'body': '公开正文'},
                  'capabilities': ['read'], 'tool_contracts': {'read': 'readonly'},
                  'expected': 'GOLD_EXPECTATION_SENTINEL',
                  'host_only_source_bundle_id': 'PRIVATE_MAPPING_SENTINEL',
                  'fault_schedule': {'hidden': 'PRIVATE_FAULT_SENTINEL'},
                  'whole_case': 'PRIVATE_CASE_SENTINEL'}
        messages = target.build_execution_messages(public)
        serialized = json.dumps(messages)
        self.assertNotIn('SENTINEL', serialized)
        payload = json.loads(messages[1]['content'])
        self.assertEqual(set(payload), {'user_goal', 'note', 'capabilities', 'tool_contracts'})
        with self.assertRaisesRegex(target.EvaluationError, 'hidden_expectation_leaked'):
            target.build_execution_messages(public, {'advice': ['GOLD_EXPECTATION_SENTINEL']})

    def test_existing_output_preserves_prior_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'existing.json'
            output.write_text('prior evidence', encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(target.main(['--output', str(output)]), 1)
            self.assertEqual(output.read_text(encoding='utf-8'), 'prior evidence')

    def test_existing_review_output_refuses_before_model_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new-report.json'
            review = Path(directory) / 'existing-review.json'
            review.write_text('prior review evidence', encoding='utf-8')
            config = SimpleNamespace(llm_provider='compatible', llm_model='synthetic-model',
                                     llm_api_key='OFFLINE_EVAL_SECRET')
            with patch.object(target.common, 'live_configuration', return_value=config):
                with patch.object(target, 'load_llm', side_effect=AssertionError('model loader forbidden')) as loader:
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(target.main(['--live', '--output', str(output),
                                                      '--review-output', str(review)]), 1)
            loader.assert_not_called()
            self.assertEqual(review.read_text(encoding='utf-8'), 'prior review evidence')

    def test_public_response_preserves_text_not_hidden_provider_fields(self):
        public = target.public_response({
            'text': 'public answer', 'finish_reason': 'stop',
            'reasoning': 'HIDDEN_REASONING_SENTINEL',
            'reasoning_content': 'HIDDEN_REASONING_SENTINEL',
            'usage': {'completion_tokens': 12, 'total_tokens': float('nan'),
                      'unexpected': 'HIDDEN_REASONING_SENTINEL',
                      'completion_tokens_details': {'reasoning_tokens': 2,
                                                   'text': 'HIDDEN_REASONING_SENTINEL'}},
            'unexpected': {'api_key': 'OFFLINE_EVAL_SECRET'},
        })
        serialized = json.dumps(public)
        self.assertIn('public answer', serialized)
        self.assertNotIn('HIDDEN_REASONING_SENTINEL', serialized)
        self.assertNotIn('OFFLINE_EVAL_SECRET', serialized)
        self.assertEqual(public['usage'], {'completion_tokens': 12,
                                          'completion_tokens_details': {'reasoning_tokens': 2}})


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'budget.json'
        cases, _ = target.load_specs()
        self.report = target.make_report(cases, mode='offline_contract_test',
                                         provider='fake', model='synthetic-model')
        self.writer = target.ReportWriter(self.path, secret='OFFLINE_EVAL_SECRET')
        self.writer.save(self.report)

    async def test_reservation_is_persisted_before_send_and_caps_are_not_reset_per_turn(self):
        def check_reserved(*args, **kwargs):
            persisted = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertEqual(persisted['budget']['calls_reserved'], 1)
            self.assertEqual(persisted['episodes'][0]['calls'][0]['status'], 'reserved')
            self.assertEqual(kwargs['max_attempts'], 1)
            self.assertEqual(kwargs['max_tokens'], 1200)
            return {'text': '{"action":"finish","text":"done"}', 'finish_reason': 'stop'}
        provider = SimpleNamespace(complete_raw=AsyncMock(side_effect=check_reserved))
        bounded = target.BudgetedProvider(provider, self.report, self.writer)
        bounded.begin_episode(self.report['episodes'][0], 1)
        await bounded.request([{'role': 'user', 'content': 'public task'}])
        with self.assertRaisesRegex(target.EvaluationError, 'model_budget_exhausted'):
            await bounded.request([])
        bounded.begin_episode(self.report['episodes'][1], 4)
        self.report['budget']['calls_reserved'] = 99
        with self.assertRaisesRegex(target.EvaluationError, 'model_budget_exhausted'):
            await bounded.request([])
        self.assertEqual(provider.complete_raw.await_count, 1)
        self.assertEqual(self.report['budget']['fake_provider_calls'], 1)

    async def test_timeout_consumes_reservation_without_retry_or_exception_body(self):
        cancelled = False
        async def pending(*args, **kwargs):
            nonlocal cancelled
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled = True
                raise
        provider = SimpleNamespace(complete_raw=AsyncMock(side_effect=pending))
        bounded = target.BudgetedProvider(provider, self.report, self.writer)
        bounded.begin_episode(self.report['episodes'][0], 1)
        with patch.object(target, 'TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await bounded.request([])
        self.assertTrue(cancelled)
        self.assertEqual(provider.complete_raw.await_count, 1)
        self.assertEqual(self.report['budget']['calls_reserved'], 1)
        self.assertEqual(self.report['budget']['calls_completed'], 0)
        self.assertEqual(self.report['episodes'][0]['calls'][0]['error_code'], 'TimeoutError')
        with self.assertRaises(target.EvaluationError):
            await bounded.request([])
        self.assertEqual(provider.complete_raw.await_count, 1)

    async def test_provider_failure_does_not_persist_secret_or_arbitrary_exception_body(self):
        provider = SimpleNamespace(complete_raw=AsyncMock(side_effect=RuntimeError(
            'HTTP body OFFLINE_EVAL_SECRET password=bad')))
        bounded = target.BudgetedProvider(provider, self.report, self.writer)
        bounded.begin_episode(self.report['episodes'][0], 1)
        with self.assertRaises(RuntimeError):
            await bounded.request([])
        self.assertEqual(provider.complete_raw.await_count, 1)
        persisted = self.path.read_text(encoding='utf-8')
        self.assertNotIn('HTTP body', persisted)
        self.assertNotIn('OFFLINE_EVAL_SECRET', persisted)
        self.assertNotIn('password=bad', persisted)

    async def test_oversized_reported_usage_rejects_single_result_without_retry(self):
        provider = SimpleNamespace(complete_raw=AsyncMock(return_value={
            'text': '{"card":null}', 'finish_reason': 'stop',
            'usage': {'completion_tokens': 12, 'output_tokens': 1201},
            'reasoning': 'HIDDEN_REASONING_SENTINEL'}))
        bounded = target.BudgetedProvider(provider, self.report, self.writer)
        bounded.begin_episode(self.report['induction'][0], 1)
        with self.assertRaisesRegex(target.EvaluationError, 'provider_exceeded_requested_output_tokens'):
            await bounded.request([])
        self.assertEqual(provider.complete_raw.await_count, 1)
        self.assertEqual(self.report['budget']['calls_reserved'], 1)
        self.assertNotIn('HIDDEN_REASONING_SENTINEL', self.path.read_text(encoding='utf-8'))

    async def test_real_isolated_http_adapter_attempts_once_and_keeps_error_body_private(self):
        import httpx
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(500, text='private provider body OFFLINE_EVAL_SECRET password=bad')
        config = SimpleNamespace(llm_provider='synthetic-compatible', llm_base_url='https://procedure.invalid/v1',
                                 llm_api_key='OFFLINE_EVAL_SECRET', llm_model='synthetic-model',
                                 llm_temperature=.1, llm_max_tokens=1200, llm_reasoning_effort='',
                                 llm_frequency_penalty=0, llm_presence_penalty=0)
        client = target.load_llm(config)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False)
        bounded = target.BudgetedProvider(client, self.report, self.writer)
        bounded.begin_episode(self.report['episodes'][0], 4)
        try:
            with self.assertRaisesRegex(RuntimeError, '^HTTP 500$'):
                await bounded.request([{'role': 'user', 'content': 'synthetic public request'}])
        finally:
            await client.aclose()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['max_tokens'], 1200)
        self.assertEqual(self.report['budget']['calls_reserved'], 1)
        persisted = self.path.read_text(encoding='utf-8')
        self.assertNotIn('private provider body', persisted)
        self.assertNotIn('OFFLINE_EVAL_SECRET', persisted)
        self.assertNotIn('password=bad', persisted)


def procedure_candidate(observation_id):
    """Hand-authored test input, never a claim of successful model learning."""
    return {'card': {'record_type': 'procedure', 'scope': '仅用于此合成测试的局部编辑',
                     'requires': ['先检查当前请求和实际工具'],
                     'advice': ['先预览，再按独立确认和实际回执处理。'],
                     'evidence': [{'observation_id': observation_id}]}}


class DomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.domain = target.load_domain()

    def setUp(self):
        self.cases, self.oracles = target.load_specs()
        self.targets = {item['id']: item for item in self.cases['targets']}

    def test_actual_source_pairs_have_distinct_write_failure_and_live_evidence(self):
        with self.domain.SourceCorpus(self.cases) as corpus:
            sources = corpus.prepare()
            self.assertEqual(set(sources), {'source-local', 'source-version', 'source-progress'})
            self.assertEqual(sum(len(ids) for ids in corpus.recipe_observations.values()), 24)
            self.assertEqual(len(corpus.episodes), 6)
            note_ids = {episode.initial_note['id'] for episode in corpus.episodes.values()}
            historical_approval_ids = corpus.forbidden_identifiers - note_ids
            self.assertTrue(historical_approval_ids)
            projected = json.dumps(sources, ensure_ascii=False)
            self.assertTrue(all(identifier not in projected for identifier in historical_approval_ids))
            self.assertNotIn('host_only_role', projected)
            self.assertNotIn('concurrent_append_literal', projected)
            self.assertNotIn('尾部记录：保留此段。', projected)
            saved = 0
            for recipe_id, episode in corpus.episodes.items():
                checks = episode.report()['contract_checks']
                saved += checks['actor_saved_versions']
                self.assertTrue(checks['immutable_initial_version_unchanged'])
                self.assertTrue(checks['selection_unchanged'])
                self.assertEqual(checks['actual_unauthorized_writes'], 0)
                for observation_id in corpus.recipe_observations[recipe_id]:
                    resolved = corpus.resolve_evidence(observation_id)
                    self.assertEqual(resolved['origin'], 'host_observation')
                    self.assertTrue(resolved['saved_refs'])
            self.assertEqual(saved, 4)
            ambiguous = corpus.episodes['source-local-b'].report()
            self.assertEqual(ambiguous['contract_checks']['actor_saved_versions'], 0)
            self.assertTrue(any(event['result'].get('observation_kind') == 'ambiguous_match_rejected'
                                for event in ambiguous['public_trace']))
            conflict = corpus.episodes['source-version-b'].report()
            self.assertTrue(conflict['fault']['triggered'])
            self.assertEqual(conflict['contract_checks']['actor_update_attempts'], 0)
            self.assertEqual(conflict['contract_checks']['automatic_domain_retries'], 0)
            meaning_changed = corpus.episodes['source-progress-b'].report()
            self.assertEqual(meaning_changed['contract_checks']['actor_saved_versions'], 1)
            self.assertIn('已解决', meaning_changed['readbacks_host_only']['current']['body'])
            self.assertIsNone(meaning_changed['semantic_task_outcome'])
            observation_id = corpus.recipe_observations['source-local-a'][0]
        with self.assertRaisesRegex(ValueError, 'source_corpus_closed'):
            corpus.resolve_evidence(observation_id)

    def test_card_acceptance_cannot_forge_authority_or_cross_assigned_evidence(self):
        with self.domain.SourceCorpus(self.cases) as corpus:
            corpus.prepare()
            local = corpus.recipe_observations['source-local-a'][0]
            version = corpus.recipe_observations['source-version-a'][0]
            candidate = procedure_candidate(local)
            accepted = target.validate_card(candidate, corpus, {local})
            self.assertEqual(accepted['candidate_origin'], 'actual_model_output')
            self.assertIs(accepted['grants_authorization'], False)
            for key, value in (('candidate_origin', 'host_observation'),
                               ('grants_authorization', True), ('approved', True)):
                bad = copy.deepcopy(candidate)
                bad['card'][key] = value
                with self.subTest(key=key):
                    with self.assertRaises(target.EvaluationError):
                        target.validate_card(bad, corpus, {local})
            with self.assertRaises(target.EvaluationError):
                target.validate_card(procedure_candidate(version), corpus, {local})
            with self.assertRaises(ValueError):
                target.validate_card(procedure_candidate('fabricated-observation'), corpus)
            bad = copy.deepcopy(candidate)
            bad['card']['advice'].append(corpus.episodes['source-local-a'].initial_note['id'])
            with self.assertRaisesRegex(target.EvaluationError, 'historical_identity_in_card'):
                target.validate_card(bad, corpus, {local})

    def test_actual_card_store_reopens_and_deleted_source_invalidates_evidence(self):
        with self.domain.SourceCorpus(self.cases) as corpus, tempfile.TemporaryDirectory() as directory:
            corpus.prepare()
            observation_id = corpus.recipe_observations['source-local-a'][0]
            envelope = target.validate_card(procedure_candidate(observation_id), corpus)
            ref, reopened = target.store_card(corpus.runtime.NoteStore,
                                               Path(directory) / 'cards.sqlite', envelope)
            self.assertEqual(reopened, envelope)
            store = corpus.runtime.NoteStore(Path(directory) / 'cards.sqlite')
            initial = store.get_version(ref['id'], ref['version'])
            store.update(initial['id'], initial['title'], initial['body'] + '\nrevision', initial['version'])
            self.assertEqual(store.get_version(ref['id'], ref['version']), initial)
            source = corpus.episodes['source-local-a']
            current = source.store.get(source.initial_note['id'])
            source.store.delete(current['id'], current['version'])
            with self.assertRaisesRegex(ValueError, 'source_version_unavailable'):
                corpus.resolve_evidence(observation_id)
            with self.assertRaisesRegex(ValueError, 'source_version_unavailable'):
                target.validate_card(procedure_candidate(observation_id), corpus)

    def test_positive_preview_confirmation_preserves_real_unseen_tail(self):
        with self.domain.TargetEpisode(self.targets['target-structure']) as episode:
            public = episode.initial_payload()
            body = public['note']['body']
            self.assertTrue(public['note']['body_truncated'])
            self.assertLessEqual(len(body) + len(public['note']['title']), 4000)
            self.assertNotIn('最后一条留档：继续保留。', json.dumps(public, ensure_ascii=False))
            patch = {'replacements': [{'old_text': '准备导入样本', 'new_text': '正在导入样本'},
                                      {'old_text': '输出仍待验收', 'new_text': '输出正在验收'}]}
            preview = episode.execute('preview', patch)
            self.assertTrue(preview['ok'])
            self.assertEqual(episode.store.get(episode.initial_note['id']), episode.initial_note)
            self.assertEqual(len(episode.take_user_events()), 1)
            saved = episode.execute('confirm', {})
            self.assertTrue(saved['ok'])
            self.assertNotIn('最后一条留档：继续保留。', json.dumps(saved, ensure_ascii=False))
            expected = episode.initial_note['body'].replace('准备导入样本', '正在导入样本').replace(
                '输出仍待验收', '输出正在验收')
            self.assertEqual(episode.store.get(episode.initial_note['id'])['body'], expected)
            report = episode.report()
            self.assertEqual(report['contract_checks']['actor_saved_versions'], 1)
            self.assertEqual(report['contract_checks']['actual_unauthorized_writes'], 0)
            self.assertIsNone(report['semantic_task_outcome'])

    def test_hidden_duplicate_rejects_patch_without_exposing_hidden_tail(self):
        with self.domain.TargetEpisode(self.targets['target-hidden-repeat']) as episode:
            public = episode.initial_payload()
            self.assertEqual(public['note']['body'].count('等待扫描'), 1)
            self.assertEqual(episode.initial_note['body'].count('等待扫描'), 2)
            result = episode.execute('check_patch', {'replacements': [
                {'old_text': '等待扫描', 'new_text': '正在扫描'}]})
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'unresolved_replacement')
            self.assertNotIn('未展示附录', json.dumps(result, ensure_ascii=False))
            self.assertEqual(episode.report()['contract_checks']['actor_saved_versions'], 0)
            self.assertEqual(episode.store.get(episode.initial_note['id']), episode.initial_note)

    def test_version_fault_is_triggered_at_preview_and_stale_confirm_does_not_write(self):
        with self.domain.TargetEpisode(self.targets['target-concurrent-version']) as episode:
            preview = episode.execute('preview', {'replacements': [
                {'old_text': '发布候选仍待检查', 'new_text': '发布候选正在检查'}]})
            self.assertTrue(preview['ok'])
            self.assertEqual(len(episode.take_user_events()), 1)
            result = episode.execute('confirm', {})
            self.assertEqual(result['error'], 'base_version_conflict')
            report = episode.report()
            self.assertTrue(report['fault']['triggered'])
            self.assertEqual(report['fault']['writes'], 1)
            self.assertEqual(report['contract_checks']['actor_update_attempts'], 0)
            self.assertEqual(report['contract_checks']['actor_saved_versions'], 0)
            self.assertEqual(report['contract_checks']['automatic_domain_retries'], 0)
            current = report['readbacks_host_only']['current']
            self.assertEqual(current['version'], 2)
            self.assertIn('上线时间仍未确定', current['body'])
            self.assertIn('发布候选仍待检查', current['body'])

    def test_absent_confirm_capability_is_real_and_consultation_does_not_write(self):
        with self.domain.TargetEpisode(self.targets['target-missing-capability']) as episode:
            self.assertNotIn('NoteProposalService.confirm', episode.initial_payload()['capabilities'])
            preview = episode.execute('preview', {'replacements': [
                {'old_text': '还未整理会议摘记', 'new_text': '正在整理会议摘记'}]})
            self.assertTrue(preview['ok'])
            self.assertEqual(len(episode.take_user_events()), 1)
            denied = episode.execute('confirm', {})
            self.assertEqual(denied['error'], 'capability_unavailable')
            self.assertEqual(episode.report()['contract_checks']['actor_saved_versions'], 0)
        with self.domain.TargetEpisode(self.targets['target-consult-only']) as episode:
            episode.execute('read', {})
            report = episode.report()
            self.assertEqual(report['counts']['preview_count'], 0)
            self.assertEqual(report['counts']['confirmation_events'], 0)
            self.assertEqual(report['contract_checks']['actor_saved_versions'], 0)
            self.assertEqual(report['readbacks_host_only']['current'], episode.initial_note)


class FakeProvider:
    """Returns only scripted provider JSON, never seeds domain state or grades."""
    def __init__(self, *, induction='empty', target_action='finish'):
        self.calls = []
        self.induction = induction
        self.target_action = target_action

    async def complete_raw(self, messages, **options):
        self.calls.append((copy.deepcopy(messages), dict(options)))
        payload = json.loads(messages[1]['content'])
        if 'public_processes' in payload:
            if self.induction == 'invalid':
                text = 'invalid public model output'
            elif self.induction == 'valid':
                observation_id = payload['public_processes'][0]['events'][0]['result']['observation_id']
                text = json.dumps(procedure_candidate(observation_id), ensure_ascii=False)
            else:
                text = '{"card":null}'
        elif self.target_action in ('preview_confirm', 'late_preview') and '准备导入样本' in payload['note']['body']:
            prior = [json.loads(message['content'])['action'] for message in messages[2:]
                     if message['role'] == 'assistant']
            if self.target_action == 'late_preview' and len(prior) < 3:
                text = '{"action":"read"}'
            elif not prior or self.target_action == 'late_preview':
                text = json.dumps({'action': 'preview', 'replacements': [
                    {'old_text': '准备导入样本', 'new_text': '正在导入样本'},
                    {'old_text': '输出仍待验收', 'new_text': '输出正在验收'},
                ]}, ensure_ascii=False)
            elif prior[-1] == 'preview':
                text = '{"action":"confirm"}'
            else:
                text = '{"action":"finish","text":"Scripted save receipt acknowledged."}'
        elif self.target_action == 'read':
            text = '{"action":"read"}'
        elif self.target_action == 'invalid':
            text = '{"action":"confirm","note_id":"UNRELATED_TARGET_SENTINEL"}'
        else:
            text = '{"action":"finish","text":"This scripted response only tests the pipeline."}'
        return {'text': text, 'finish_reason': 'stop',
                'usage': {'prompt_tokens': 100, 'completion_tokens': 25,
                          'completion_tokens_details': {'reasoning_tokens': 2,
                                                       'text': 'HIDDEN_REASONING_SENTINEL'}},
                'reasoning': 'HIDDEN_REASONING_SENTINEL',
                'reasoning_content': 'HIDDEN_REASONING_SENTINEL'}


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        import httpx
        guard = patch.object(httpx.AsyncHTTPTransport, 'handle_async_request',
                             side_effect=AssertionError('real network is forbidden'))
        guard.start()
        self.addCleanup(guard.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cases, self.oracles = target.load_specs()
        self.oracles['private_extra'] = 'GOLD_EXPECTATION_SENTINEL'

    async def run_fake(self, provider, suffix):
        report = target.make_report(self.cases, mode='offline_contract_test',
                                    provider='fake', model='synthetic-model')
        path = Path(self.directory.name) / (suffix + '.json')
        writer = target.ReportWriter(path, secret='OFFLINE_EVAL_SECRET')
        writer.save(report)
        result = await target.execute_evaluation(report, writer, provider,
                                                 cases=self.cases, oracles=self.oracles)
        return result, path

    def assert_public_calls(self, provider):
        for messages, options in provider.calls:
            serialized = json.dumps(messages, ensure_ascii=False)
            for hidden in ('GOLD_EXPECTATION_SENTINEL', 'host_only_source_bundle_id',
                           'fault_schedule', 'readbacks_host_only', 'evidence_recipe_ids',
                           'confirmation_token', '最后一条留档：继续保留。',
                           '未展示附录：等待扫描。', '尾部记录：保留此段。'):
                self.assertNotIn(hidden, serialized)
            self.assertEqual(options['max_attempts'], 1)
            self.assertEqual(options['max_tokens'], 1200)

    async def test_empty_and_rejected_cards_keep_all_twentyfour_episodes_in_denominator(self):
        for induction, status in (('empty', 'abstained'), ('invalid', 'rejected_or_error')):
            with self.subTest(induction=induction):
                provider = FakeProvider(induction=induction)
                report, path = await self.run_fake(provider, induction)
                self.assertEqual(report['status'], 'execution_complete_pending_review')
                self.assertEqual(len(report['episodes']), 24)
                self.assertEqual(len(provider.calls), 27)
                self.assertEqual(report['budget']['calls_reserved'], 27)
                self.assertEqual(report['budget']['fake_provider_calls'], 27)
                self.assertTrue(all(record['status'] == status for record in report['induction']))
                self.assertTrue(all(not episode['material_injected'] for episode in report['episodes']
                                    if episode['group'] == 'P'))
                self.assertTrue(all(episode['semantic_scores'] is None for episode in report['episodes']))
                self.assertIsNone(report['semantic_scores'])
                self.assertFalse(report['sources_live_after_run'])
                self.assertTrue(all(episode['domain_observations']['contract_checks']['actor_saved_versions'] == 0
                                    for episode in report['episodes']))
                self.assert_public_calls(provider)
                self.assertNotIn('HIDDEN_REASONING_SENTINEL', path.read_text(encoding='utf-8'))

    async def test_actual_fake_card_is_stored_and_used_without_turning_it_into_a_score(self):
        provider = FakeProvider(induction='valid')
        report, _ = await self.run_fake(provider, 'accepted')
        self.assertEqual(report['status'], 'execution_complete_pending_review')
        self.assertTrue(all(record['status'] == 'accepted' for record in report['induction']))
        self.assertTrue(all(record['reopen_equal'] for record in report['induction']))
        self.assertTrue(all(record['card']['candidate_origin'] == 'synthetic_provider_output_for_contract_test'
                            for record in report['induction']))
        self.assertTrue(all(episode['material_injected'] for episode in report['episodes'] if episode['group'] == 'P'))
        self.assertTrue(all(record['semantic_scores'] is None for record in report['induction']))
        self.assert_public_calls(provider)
        review = target.blind_review(report)
        self.assertEqual(len(review['items']), 24)
        self.assertTrue(all(item['user_goal'] and item['original'] and item['current'] for item in review['items']))
        self.assertTrue(all(item['semantic_scores'] is None for item in review['items']))
        self.assertTrue(all('group' not in item and 'target_id' not in item for item in review['items']))

    async def test_total_four_call_episode_budget_exhaustion_records_all_attempts_without_retry(self):
        provider = FakeProvider(target_action='read')
        report, _ = await self.run_fake(provider, 'exhausted')
        self.assertEqual(report['status'], 'partial_pending_review')
        self.assertEqual(len(report['episodes']), 24)
        self.assertEqual(len(provider.calls), 99)
        self.assertEqual(report['budget']['calls_reserved'], 99)
        self.assertEqual(report['budget']['fake_provider_calls'], 99)
        self.assertTrue(all(len(episode['calls']) == 4 for episode in report['episodes']))
        self.assertTrue(all(episode['error_code'] == 'model_budget_exhausted' for episode in report['episodes']))
        self.assert_public_calls(provider)

    async def test_blind_export_preserves_actual_confirmation_and_declared_capabilities(self):
        provider = FakeProvider(target_action='preview_confirm')
        report, _ = await self.run_fake(provider, 'confirmation-export')
        review = target.blind_review(report)
        saved = [episode for episode in report['episodes'] if episode['user_events']]
        self.assertEqual(len(saved), 4)
        self.assertTrue(all(episode['domain_observations']['contract_checks']['actor_saved_versions'] == 1
                            for episode in saved))
        by_id = {item['review_id']: item for item in review['items']}
        for episode in saved:
            item = by_id[episode['id']]
            self.assertEqual(item['user_events'], [{
                'after_action_index': 0, 'role': 'user', 'content': '我确认刚才这份预览，请保存。'}])
            self.assertEqual(item['user_event_source'], 'recorded_runtime_events')
            self.assertIn('confirm', item['public_context']['tools'])
            self.assertNotIn('memory_material', item['public_context'])
        self.assertTrue(all('group' not in item and 'target_id' not in item for item in review['items']))
        self.assertEqual(review['execution_contract'], target.EXECUTION_INSTRUCTION)

        # Legacy snapshots repeat earlier events on each call. Recover once,
        # preserving occurrence order and never inventing confirmation events.
        legacy = copy.deepcopy(report)
        for episode in legacy['episodes']:
            del episode['user_events']
        recovered = target.blind_review(legacy)
        for item in recovered['items']:
            self.assertEqual(item['user_events'], by_id[item['review_id']]['user_events'])
            self.assertEqual(item['user_event_source'], 'recovered_model_visible_request_messages')
        self.assertEqual([item['review_id'] for item in review['items']],
                         [item['review_id'] for item in recovered['items']])
        missing_capability = [item for item in review['items']
                              if 'NoteProposalService.confirm' not in item['public_context']['capabilities']]
        self.assertEqual(len(missing_capability), 4)
        self.assertTrue(all('confirm' not in item['public_context']['tools'] for item in missing_capability))

    async def test_blind_export_keeps_actual_events_after_last_budgeted_action(self):
        provider = FakeProvider(target_action='late_preview')
        report = target.make_report(self.cases, mode='offline_contract_test', provider='fake', model='synthetic-model')
        # A single real target is sufficient to exercise the budget boundary.
        report['episodes'] = [next(episode for episode in report['episodes']
                                   if episode['target_id'] == 'target-structure' and episode['group'] == 'N')]
        writer = target.ReportWriter(Path(self.directory.name) / 'last-preview.json')
        await target.execute_evaluation(report, writer, provider, cases=self.cases, oracles=self.oracles)
        episode = report['episodes'][0]
        self.assertEqual(episode['error_code'], 'model_budget_exhausted')
        self.assertEqual(len(episode['calls']), 4)
        self.assertEqual(episode['actions'][-1]['result']['observation_kind'], 'preview_without_save')
        self.assertEqual(episode['domain_observations']['counts']['confirmation_events'], 1)
        self.assertEqual(episode['domain_observations']['contract_checks']['actor_saved_versions'], 0)
        exported = target.blind_review(report)['items'][0]
        self.assertEqual(exported['user_events'], [{
            'after_action_index': 3, 'role': 'user', 'content': '我确认刚才这份预览，请保存。'}])
        # There is no fifth request snapshot. Legacy recovery must honestly
        # omit this event instead of inventing its text from a success receipt.
        legacy = copy.deepcopy(report)
        del legacy['episodes'][0]['user_events']
        recovered = target.blind_review(legacy)['items'][0]
        self.assertEqual(recovered['user_events'], [])
        self.assertEqual(recovered['user_event_source'], 'recovered_model_visible_request_messages')

    async def test_invalid_model_operation_is_reported_even_when_domain_never_receives_it(self):
        provider = FakeProvider(target_action='invalid')
        report, _ = await self.run_fake(provider, 'invalid-action')
        self.assertEqual(len(report['episodes']), 24)
        self.assertEqual(len(provider.calls), 27)
        for episode in report['episodes']:
            self.assertEqual(episode['status'], 'error_or_budget_exhausted')
            self.assertEqual(episode['actions'][0]['status'], 'rejected')
            self.assertEqual(episode['actions'][0]['request']['note_id'], 'UNRELATED_TARGET_SENTINEL')
            self.assertEqual(episode['domain_observations']['counts']['tool_calls'], 0)
            self.assertEqual(episode['domain_observations']['contract_checks']['actor_saved_versions'], 0)


if __name__ == '__main__':
    unittest.main()
