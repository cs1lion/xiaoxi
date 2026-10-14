"""Offline source-message contract checks; no user database or provider calls."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Importing config must not read the user's private .env during this suite.
with patch('dotenv.load_dotenv', return_value=False):
    from server.memory import extractor


def message(identifier=41, text='我在甲公司工作，岗位是设计师。', **changes):
    return {'id': identifier, 'session_id': 'isolated-extraction', 'role': 'user',
            'text': text, 'mode': 'text', 'created_at': 1000.0, **changes}


def candidate(**changes):
    return {'kind': 'fact', 'subject': 'user', 'key': '工作:公司',
            'value': '用户在甲公司工作。', 'source': 'stated', 'importance': 0.8,
            'operation': 'add', 'evidence': {'message_id': 41, 'quote': '我在甲公司工作'},
            **changes}


def parse(*items, records=None):
    return extractor.parse_extraction({'memories': list(items)},
                                      evidence_messages=[message()] if records is None else records)


class ExtractionContractTests(unittest.TestCase):
    def test_linked_candidate_retains_exact_quote_and_operation(self):
        quote = ' 我在甲公司工作\n'
        result = parse(candidate(evidence={'message_id': 41, 'quote': quote}),
                       records=[message(text=f'背景。{quote}岗位是设计师。')])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['evidence_message_id'], 41)
        self.assertEqual(result[0]['evidence_quote'], quote)
        self.assertEqual(result[0]['evidence_state'], 'linked')
        self.assertEqual(result[0]['operation'], 'add')
        self.assertEqual(result[0]['decision'], 'add')
        self.assertEqual(result[0]['source'], 'stated')

    def test_company_and_job_are_independent_attributes(self):
        company = candidate()
        job = candidate(key='工作:岗位', value='用户的岗位是设计师。',
                        evidence={'message_id': 41, 'quote': '岗位是设计师'})
        result = parse(company, job)
        self.assertEqual([item['key'] for item in result], ['工作:公司', '工作:岗位'])
        self.assertTrue(all(item['evidence_state'] == 'linked' for item in result))

    def test_same_key_different_values_reach_manager_without_auto_merge(self):
        record = message(text='我以前在甲公司工作，现在改为在乙公司工作。')
        old = candidate(evidence={'message_id': 41, 'quote': '在甲公司工作'})
        new = candidate(value='用户现在在乙公司工作。', operation='update',
                        evidence={'message_id': 41, 'quote': '现在改为在乙公司工作'})
        result = parse(old, new, old, records=[record])
        self.assertEqual(len(result), 2)
        self.assertEqual([item['operation'] for item in result], ['add', 'update'])
        self.assertEqual([item['key'] for item in result], ['工作:公司', '工作:公司'])
        self.assertNotEqual(result[0]['value'], result[1]['value'])

    def test_kinds_do_not_replace_each_other_by_topic(self):
        fact = candidate()
        episode = candidate(kind='episode', key='工作:讨论', value='用户提到了自己的公司。')
        todo = candidate(kind='todo', key='工作:待办', value='用户计划检查工作安排。')
        self.assertEqual([item['kind'] for item in parse(fact, episode, todo)],
                         ['fact', 'episode', 'todo'])

    def test_missing_evidence_or_operation_is_rejected(self):
        for field in ('evidence', 'operation'):
            item = candidate()
            del item[field]
            with self.subTest(field=field):
                self.assertEqual(parse(item), [])
        for value in ('delete', '', None, 1, {}):
            self.assertEqual(parse(candidate(operation=value)), [])

    def test_unknown_or_missing_source_is_never_promoted(self):
        for source in (None, '', 'verified', 'STATED', 1, []):
            with self.subTest(source=source):
                self.assertEqual(parse(candidate(source=source)), [])
        missing = candidate()
        del missing['source']
        self.assertEqual(parse(missing), [])
        self.assertEqual(extractor.parse_extraction({'memories': [missing]}), [])

    def test_known_source_preserves_uncertainty(self):
        for source, confidence in (('stated', 0.95), ('inferred', 0.6), ('uncertain', 0.35)):
            result = parse(candidate(source=source))
            self.assertEqual(result[0]['source'], source)
            self.assertEqual(result[0]['confidence'], confidence)
            self.assertEqual(result[0]['evidence_state'], 'linked')

    def test_only_explicit_user_subject_is_accepted_automatically(self):
        for subject in ('self', 'other', None, {}, 1):
            self.assertEqual(parse(candidate(subject=subject)), [])
        missing = candidate()
        del missing['subject']
        self.assertEqual(parse(missing), [])

    def test_self_state_is_rejected_automatically_but_available_for_legacy_diagnosis(self):
        drops = []
        item = candidate(kind='self_state')
        result = extractor.parse_extraction({'memories': [item]},
                                            evidence_messages=[message()], drops=drops)
        self.assertEqual(result, [])
        self.assertEqual(len(drops), 1)
        self.assertIn('角色自身状态', drops[0]['key'])
        for subject in ('user', 'self'):
            legacy = extractor.parse_extraction({'memories': [candidate(kind='self_state', subject=subject)]})
            self.assertEqual(len(legacy), 1)
            self.assertEqual(legacy[0]['kind'], 'self_state')
            self.assertEqual(legacy[0]['subject'], subject)
            self.assertEqual(legacy[0]['evidence_state'], 'legacy')

    def test_wrong_role_and_nonexistent_source_message_are_rejected(self):
        self.assertEqual(parse(candidate(), records=[message(role='assistant')]), [])
        self.assertEqual(parse(candidate(), records=[message(role='system')]), [])
        self.assertEqual(parse(candidate(), records=[message(identifier=99)]), [])
        self.assertEqual(parse(candidate(), records=[]), [])

    def test_message_ids_are_real_integers_and_unambiguous(self):
        for identifier in ('41', True, 0, -1, 41.0, None):
            item = candidate(evidence={'message_id': identifier, 'quote': '我在甲公司工作'})
            self.assertEqual(parse(item), [])
            self.assertEqual(parse(candidate(), records=[message(identifier=identifier)]), [])
        self.assertEqual(parse(candidate(), records=[message(), message()]), [])
        self.assertEqual(parse(candidate(), records=[message(), message(role='assistant')]), [])

    def test_quote_must_be_exact_nonempty_bounded_original_span(self):
        for quote in ('用户在甲公司工作', '我在甲公司岗位是设计师', '', ' \n\t ', None, 41):
            self.assertEqual(parse(candidate(evidence={'message_id': 41, 'quote': quote})), [])
        quote = 'x' * (extractor.MAX_EVIDENCE_QUOTE_CHARS + 1)
        self.assertEqual(parse(candidate(evidence={'message_id': 41, 'quote': quote}),
                               records=[message(text=quote)]), [])
        # A word-boundary or paraphrase matcher must not silently replace exact attribution.
        self.assertEqual(parse(candidate(evidence={'message_id': 41, 'quote': '我在 甲公司工作'})), [])

    def test_invalid_candidate_shape_and_bounds_are_rejected(self):
        for value in (None, 12, '', ' ', 'x' * 121):
            self.assertEqual(parse(candidate(value=value)), [])
        for key in ('工作', ':公司', '工作:', 'x' * 201, 41):
            self.assertEqual(parse(candidate(key=key)), [])
        for importance in ('bad', None, float('nan'), float('inf'), -1):
            self.assertEqual(parse(candidate(importance=importance)), [])
        for payload in (None, {'memories': {}}, {'memories': 'bad'}, [None, 'bad', 1]):
            self.assertEqual(extractor.parse_extraction(payload, evidence_messages=[message()]), [])

    def test_each_kind_budget_counts_distinct_candidates(self):
        items = [candidate(key=f'工作:属性{index}', value=f'用户记录属性{index}。')
                 for index in range(5)]
        items.insert(1, items[0])
        result = parse(*items, candidate(kind='todo', key='计划:事项', value='用户计划再讨论此事。'))
        self.assertEqual(len([item for item in result if item['kind'] == 'fact']), 4)
        self.assertEqual(len([item for item in result if item['kind'] == 'todo']), 1)

    def test_quote_third_person_hypothesis_and_roleplay_are_conservatively_excluded(self):
        for text in ('他说我在甲公司工作', '我朋友在甲公司工作', '假设我在甲公司工作',
                     '假如我在甲公司工作', '角色扮演：我在甲公司工作',
                     '如果我在甲公司工作', '请扮演我，我在甲公司工作',
                     '我妈妈说我在甲公司工作', '她喜欢说我在甲公司工作',
                     '这句台词是我在甲公司工作', '引用“我在甲公司工作”',
                     "引用'我在甲公司工作'", '引用‘我在甲公司工作’'):
            self.assertEqual(parse(candidate(), records=[message(text=text)]), [])

    def test_public_evidence_guard_covers_common_english_markers(self):
        for text in ('He says I work at A.', 'My friend works at A.', 'If I work at A.',
                     'Suppose I work at A.', 'Pretend I work at A.', 'Role-play: I work at A.',
                     "Quote 'I work at A.'"):
            self.assertFalse(extractor.is_direct_user_evidence(text), text)
        for text in ('我在甲公司工作。', 'I work at A.', "I'm a designer and I'm working at A.",
                     'I’m a designer.'):
            self.assertTrue(extractor.is_direct_user_evidence(text), text)
        for text in ('', ' \n ', None, 41):
            self.assertFalse(extractor.is_direct_user_evidence(text))

    def test_legacy_diagnostic_parse_never_claims_a_link(self):
        old = candidate()
        del old['operation']
        del old['evidence']
        result = extractor.parse_extraction({'memories': [old]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['evidence_state'], 'legacy')
        self.assertIsNone(result[0]['evidence_message_id'])
        self.assertEqual(result[0]['evidence_quote'], '')
        # Even model-supplied IDs have no provenance without caller-supplied records.
        self.assertEqual(extractor.parse_extraction({'memories': [candidate()]})[0]['evidence_state'], 'legacy')

    def test_attribution_does_not_claim_semantic_entailment(self):
        item = candidate(value='用户喜欢喝咖啡。')
        result = parse(item)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['evidence_quote'], '我在甲公司工作')
        # Linked means an exact user quote exists; truth validation is a separate concern.

    def test_update_flag_is_candidate_metadata_not_conflict_resolution(self):
        result = parse(candidate(operation='update'))
        self.assertEqual(result[0]['operation'], 'update')
        self.assertNotIn('supersedes', result[0])
        self.assertNotIn('status', result[0])

    def test_drop_diagnostics_explain_rejected_evidence(self):
        drops = []
        result = extractor.parse_extraction({'memories': [candidate(evidence={})]},
                                            evidence_messages=[message()], drops=drops)
        self.assertEqual(result, [])
        self.assertEqual(len(drops), 1)
        self.assertIn('证据', drops[0]['key'])


class AutomaticExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_uses_only_saved_user_id_role_and_original_text(self):
        model = AsyncMock(return_value={'text': json.dumps({'memories': [candidate()]})})
        records = [message(), message(identifier=42, role='assistant', text='AI_FAKE_SHARED_EPISODE')]
        with patch.object(extractor.llm, 'complete_raw', model):
            result = await extractor.extract_memories('UNSAVED_USER_TEXT', 'ASSISTANT_TEXT',
                                                     recent_context='NO_ID_HISTORY', evidence_messages=records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['evidence_state'], 'linked')
        prompt = model.call_args.args[0]
        self.assertEqual(json.loads(prompt[1]['content']), {'user_messages': [
            {'id': 41, 'role': 'user', 'text': message()['text']}]})
        self.assertNotIn('ASSISTANT_TEXT', str(prompt))
        self.assertNotIn('UNSAVED_USER_TEXT', str(prompt))
        self.assertNotIn('NO_ID_HISTORY', str(prompt))
        self.assertNotIn('AI_FAKE_SHARED_EPISODE', str(prompt))
        self.assertIn('工作:公司', prompt[0]['content'])
        self.assertIn('工作:岗位', prompt[0]['content'])
        self.assertIn('"kind": "fact|episode|todo"', prompt[0]['content'])

    async def test_no_saved_user_message_skips_model_and_marks_trace(self):
        model = AsyncMock(side_effect=AssertionError('no model call is permitted'))
        for records in (None, [], [message(role='assistant')], [message(identifier=True)],
                        [message(text='假设我在甲公司工作')]):
            trace = {}
            with patch.object(extractor.llm, 'complete_raw', model):
                self.assertEqual(await extractor.extract_memories('我在甲公司工作', '',
                                                                  evidence_messages=records, trace=trace), [])
            self.assertEqual(trace['error'], 'no_linkable_user_messages')
        model.assert_not_awaited()

    async def test_automatic_result_requires_strict_evidence(self):
        for item in (candidate(evidence={'message_id': 99, 'quote': '我在甲公司工作'}),
                     candidate(evidence=None), candidate(subject='self'), candidate(source='unknown'),
                     candidate(kind='self_state')):
            model = AsyncMock(return_value={'text': json.dumps({'memories': [item]})})
            trace = {}
            with patch.object(extractor.llm, 'complete_raw', model):
                result = await extractor.extract_memories('', '', evidence_messages=[message()], trace=trace)
            self.assertEqual(result, [])
            self.assertEqual(trace['dropped'], 1)

    async def test_provider_failure_is_recorded_without_candidates(self):
        trace = {}
        with patch.object(extractor.llm, 'complete_raw', AsyncMock(side_effect=RuntimeError('isolated failure'))):
            result = await extractor.extract_memories('', '', evidence_messages=[message()], trace=trace)
        self.assertEqual(result, [])
        self.assertIn('isolated failure', trace['error'])

    async def test_contract_suite_has_no_live_database_or_provider(self):
        self.assertEqual(extractor.settings.db_path, ':memory:')
        self.assertEqual(extractor.settings.llm_provider, 'mock')
        self.assertEqual(extractor.settings.llm_api_key, '')
        self.assertTrue(extractor.llm.offline)


if __name__ == '__main__':
    unittest.main()
