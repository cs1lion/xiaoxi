"""Isolated preference evidence admission checks; no DB/provider/user secrets."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch('dotenv.load_dotenv', return_value=False):
    from server.memory import extractor
    from server.memory.preference import (
        PreferenceValidationError, normalize_preference, preference_change_allowed,
    )


def metadata(target='绿茶', level='like', scope='unspecified'):
    return {'target': target, 'level': level, 'scope': scope}


def normalize(quote='我喜欢喝绿茶。', value='用户喜欢喝绿茶。', raw=None, **changes):
    options = {'key': '饮食:饮品偏好', 'kind': 'fact', 'subject': 'user', 'source': 'stated',
               'message_text': quote}
    options.update(changes)
    return normalize_preference(raw, quote, value, **options)


def parsed(quote='我喜欢喝绿茶。', value='用户喜欢喝绿茶。', *, full=None, **changes):
    item = {'kind': 'fact', 'subject': 'user', 'key': '饮食:饮品偏好', 'value': value,
            'source': 'stated', 'importance': .8, 'operation': 'add',
            'evidence': {'message_id': 41, 'quote': quote}, **changes}
    drops = []
    result = extractor.parse_extraction({'memories': [item]}, drops=drops, evidence_messages=[
        {'id': 41, 'role': 'user', 'text': quote if full is None else full}])
    return result, drops


class PreferenceAdmissionTests(unittest.TestCase):
    def test_real_green_tea_output_derives_long_term_favorite(self):
        fields = normalize('我长期最喜欢喝绿茶。', '用户长期最喜欢喝绿茶。')
        self.assertEqual(fields, {'preference_target': '绿茶', 'preference_level': 'favorite',
                                  'preference_scope': 'long_term'})

    def test_real_red_tea_supplement_preserves_recent_ordinary_like(self):
        quote = '最近红茶也挺喜欢的，我还没决定要不要改掉原来的习惯。'
        self.assertEqual(normalize(quote, '用户最近喜欢红茶。'),
                         {'preference_target': '红茶', 'preference_level': 'like',
                          'preference_scope': 'recent'})
        self.assertFalse(preference_change_allowed(quote, level='like'))

    def test_supported_explicit_structure_is_rederived(self):
        self.assertEqual(normalize(raw=metadata()), normalize())

    def test_unstated_scope_is_unspecified_and_no_timestamp_is_generated(self):
        fields = normalize()
        self.assertEqual(fields['preference_scope'], 'unspecified')
        self.assertNotIn('preference_as_of', fields)
        self.assertNotIn('identity_slot', fields)

    def test_no_longer_like_does_not_invent_a_time_scope(self):
        fields = normalize('我不再喜欢喝绿茶。', '用户不再喜欢喝绿茶。')
        self.assertEqual((fields['preference_level'], fields['preference_scope']),
                         ('dislike', 'unspecified'))
        self.assertTrue(preference_change_allowed('我不再喜欢喝绿茶。', level='dislike'))

    def test_explicit_current_dislike_keeps_current_label(self):
        fields = normalize('我现在不再喜欢喝绿茶。', '用户现在不喜欢喝绿茶。')
        self.assertEqual((fields['preference_target'], fields['preference_level'],
                          fields['preference_scope']), ('绿茶', 'dislike', 'current'))

    def test_object_degree_and_scope_cannot_be_forged(self):
        for changes in ({'target': '红茶'}, {'level': 'favorite'}, {'scope': 'long_term'}):
            with self.subTest(changes=changes), self.assertRaises(PreferenceValidationError):
                normalize(raw=metadata(**changes))

    def test_explicit_shape_enums_and_extra_authority_fields_are_rejected(self):
        bad = [True, 1, '', [], {}, {'target': '绿茶'},
               metadata(level='unknown'), metadata(scope='unknown'), metadata(level=[]),
               metadata(scope={}), metadata(target=1),
               {**metadata(), 'as_of': 123}, {**metadata(), 'status': 'active'},
               {**metadata(), 'identity_slot': 'attacker'}]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(PreferenceValidationError):
                normalize(raw=raw)

    def test_only_stated_user_fact_and_exact_supported_keys_get_member_fields(self):
        for changes in ({'kind': 'todo'}, {'kind': 'episode'}, {'subject': 'self'},
                        {'source': 'inferred'}, {'source': 'uncertain'},
                        {'key': '饮食:随机偏好'}, {'key': '饮食:饮品偏好:绿茶'},
                        {'key': '饮食:饮品喜好'}, {'key': '工作:公司'}):
            with self.subTest(changes=changes):
                self.assertIsNone(normalize(**changes))
                with self.assertRaises(PreferenceValidationError):
                    normalize(raw=metadata(), **changes)

    def test_unsupported_plural_objects_do_not_get_a_single_member_identity(self):
        for quote in ('我喜欢绿茶和红茶。', '我喜欢绿茶或红茶。',
                      '绿茶和红茶我也喜欢。', '我喜欢绿茶、红茶。'):
            with self.subTest(quote=quote):
                self.assertIsNone(normalize(quote, quote.replace('我', '用户')))
                with self.assertRaises(PreferenceValidationError):
                    normalize(quote, '用户喜欢绿茶。', raw=metadata())

    def test_separate_complete_assertions_can_be_selected_individually(self):
        full = '我喜欢喝绿茶，我最近也喜欢喝红茶。'
        self.assertEqual(normalize('我喜欢喝绿茶', message_text=full)['preference_target'], '绿茶')
        self.assertEqual(normalize('我最近也喜欢喝红茶', '用户最近喜欢喝红茶。',
                                   message_text=full)['preference_target'], '红茶')

    def test_unknown_or_negated_assertions_stay_on_the_ordinary_path(self):
        for quote in ('我还没决定是否喜欢绿茶。', '我不是最喜欢绿茶。',
                      '我可能喜欢绿茶。', '我以前喜欢绿茶。', '我平时也喜欢绿茶。'):
            with self.subTest(quote=quote):
                self.assertIsNone(normalize(quote, quote.replace('我', '用户')))

    def test_value_cannot_change_object_degree_or_scope(self):
        for value in ('用户喜欢红茶。', '用户最喜欢绿茶。', '用户长期喜欢绿茶。',
                      '用户不喜欢绿茶。', '用户喜欢绿茶和红茶。'):
            with self.subTest(value=value):
                self.assertIsNone(normalize(value=value))
                with self.assertRaises(PreferenceValidationError):
                    normalize(value=value, raw=metadata())

    def test_cropped_negation_degree_and_time_never_gain_member_permission(self):
        cases = [('喜欢绿茶', '我不喜欢绿茶。', '用户喜欢绿茶。'),
                 ('喜欢绿茶', '我最喜欢绿茶。', '用户喜欢绿茶。'),
                 ('我喜欢绿茶', '最近我喜欢绿茶。', '用户喜欢绿茶。'),
                 ('喜欢绿茶', '我最近喜欢绿茶。', '用户喜欢绿茶。'),
                 ('我喜欢喝绿茶', '我喜欢喝绿茶的味道。', '用户喜欢喝绿茶。')]
        for quote, full, value in cases:
            with self.subTest(full=full):
                self.assertIsNone(normalize(quote, value, message_text=full))
                with self.assertRaises(PreferenceValidationError):
                    normalize(quote, value, raw=metadata(), message_text=full)

    def test_ambiguous_repeated_quote_is_not_linked_to_a_guessed_occurrence(self):
        self.assertIsNone(normalize('我喜欢绿茶', '用户喜欢绿茶。',
                                    message_text='我喜欢绿茶。我喜欢绿茶。'))

    def test_without_full_message_elliptical_preference_is_not_assumed(self):
        self.assertIsNone(normalize('最近红茶也挺喜欢的。', '用户最近喜欢红茶。',
                                    message_text=None))
        self.assertEqual(normalize(message_text=None)['preference_target'], '绿茶')

    def test_quote_roleplay_and_third_party_sources_cannot_grant_members(self):
        for quote in ('假设我喜欢绿茶。', '我朋友说我喜欢绿茶。', '她说我喜欢绿茶。',
                      '引用“我喜欢绿茶”。'):
            with self.subTest(quote=quote):
                self.assertIsNone(normalize(quote, '用户喜欢绿茶。'))

    def test_original_user_noun_is_not_evidence_of_the_speakers_own_preference(self):
        for quote, value in (
            ('用户喜欢红茶。', '用户喜欢红茶。'),
            ('用户长期最喜欢红茶。', '用户长期最喜欢红茶。'),
            ('用户最近红茶也喜欢。', '用户最近喜欢红茶。'),
            ('网站用户也喜欢。', '用户喜欢网站用户。'),
        ):
            with self.subTest(quote=quote):
                self.assertIsNone(normalize(quote, value))
                with self.assertRaises(PreferenceValidationError):
                    normalize(quote, value, raw=metadata(target='红茶'))
                result, drops = parsed(quote, value)
                self.assertEqual(drops, [])
                self.assertFalse(any(name.startswith('preference_') for name in result[0]))
        self.assertEqual(normalize('本人喜欢红茶。', '用户喜欢红茶。')['preference_target'], '红茶')


class PreferenceChangeTests(unittest.TestCase):
    def test_explicit_favorite_replacement_requires_a_local_change(self):
        quote = '我现在最喜欢喝的茶改为红茶。'
        fields = normalize(quote, '用户现在最喜欢喝红茶。')
        self.assertEqual((fields['preference_target'], fields['preference_level'],
                          fields['preference_scope']), ('红茶', 'favorite', 'current'))
        self.assertTrue(preference_change_allowed(quote, level='favorite'))
        self.assertFalse(preference_change_allowed('我现在最喜欢喝红茶。', level='favorite'))

    def test_local_explicit_correction_can_change_favorite(self):
        quote = '纠正一下，我现在最喜欢喝红茶。'
        self.assertEqual(normalize(quote, '用户现在最喜欢喝红茶。')['preference_target'], '红茶')
        self.assertTrue(preference_change_allowed(quote, level='favorite'))

    def test_pending_negated_and_unrelated_changes_cannot_replace(self):
        for quote, level in (
            ('我现在最喜欢红茶，我还没决定要不要改掉原来的习惯。', 'favorite'),
            ('我最喜欢绿茶，不打算改掉习惯。', 'favorite'),
            ('我不再喜欢绿茶，这只是未必真实的想法。', 'dislike'),
            ('我喜欢绿茶，纠正一下工作地址。', 'like'),
            ('我不再加班，我最喜欢红茶。', 'favorite'),
            ('我不喜欢绿茶。', 'dislike'),
            ('我已经不喜欢绿茶。', 'dislike'),
            ('我早就不喜欢绿茶。', 'dislike'),
        ):
            with self.subTest(quote=quote):
                self.assertFalse(preference_change_allowed(quote, level=level))

    def test_complete_message_pending_clause_cannot_be_cropped_out_of_permission(self):
        quote = '我现在最喜欢喝的茶改为红茶'
        self.assertTrue(preference_change_allowed(quote, level='favorite'))
        for suffix in ('我还没决定要不要改习惯。', '我不打算改习惯。', '我不想换原来的习惯。'):
            full = quote + '，' + suffix
            with self.subTest(suffix=suffix):
                # The selected preference member remains attributable, but its
                # destructive replacement permission must see the entire turn.
                self.assertEqual(normalize(quote, '用户现在最喜欢喝红茶。',
                                           message_text=full)['preference_target'], '红茶')
                self.assertFalse(preference_change_allowed(quote, level='favorite', message_text=full))

    def test_full_message_no_change_also_blocks_cropped_dislike_transition(self):
        quote = '我现在不再喜欢喝绿茶'
        self.assertTrue(preference_change_allowed(quote, level='dislike'))
        self.assertFalse(preference_change_allowed(
            quote, level='dislike', message_text=quote + '，我还没有决定是否改习惯。'))
        self.assertFalse(preference_change_allowed(quote, level='dislike', message_text='另一条消息。'))


class StrictParserIntegrationTests(unittest.TestCase):
    def test_actual_old_output_without_structure_gets_only_program_derived_fields(self):
        result, drops = parsed('我长期最喜欢喝绿茶。', '用户长期最喜欢喝绿茶。',
                               preference_as_of=1, identity_slot='model-owned', status='active')
        self.assertEqual(drops, [])
        self.assertEqual(result[0]['preference_target'], '绿茶')
        self.assertEqual(result[0]['preference_scope'], 'long_term')
        for field in ('preference_as_of', 'identity_slot', 'status'):
            self.assertNotIn(field, result[0])

    def test_invalid_explicit_structure_drops_whole_candidate(self):
        for raw in (None, metadata(level='favorite'), {**metadata(), 'as_of': 1}):
            result, drops = parsed(preference=raw)
            self.assertEqual(result, [])
            self.assertEqual(len(drops), 1)
            self.assertIn('preference', drops[0]['key'])

    def test_unrecognized_old_output_keeps_conservative_candidate_without_member_fields(self):
        result, drops = parsed('我喜欢绿茶和红茶。', '用户喜欢绿茶和红茶。')
        self.assertEqual(drops, [])
        self.assertEqual(len(result), 1)
        self.assertFalse(any(name.startswith('preference_') for name in result[0]))

    def test_cut_evidence_is_not_promoted_and_explicit_claim_is_rejected(self):
        result, drops = parsed('喜欢绿茶', '用户喜欢绿茶。', full='我不喜欢绿茶。')
        self.assertEqual(drops, [])
        self.assertFalse(any(name.startswith('preference_') for name in result[0]))
        self.assertEqual(parsed('喜欢绿茶', '用户喜欢绿茶。', full='我不喜欢绿茶。',
                                preference=metadata())[0], [])

    def test_legacy_diagnostic_output_never_acquires_preference_permissions(self):
        result = extractor.parse_extraction({'memories': [
            {'kind': 'fact', 'subject': 'user', 'key': '饮食:饮品偏好',
             'value': '用户喜欢绿茶。', 'source': 'stated', 'importance': .8,
             'preference': metadata()}]})
        self.assertEqual(result[0]['evidence_state'], 'legacy')
        self.assertFalse(any(name.startswith('preference_') for name in result[0]))


if __name__ == '__main__':
    unittest.main()
