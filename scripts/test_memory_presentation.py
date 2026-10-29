"""Shared memory projection contracts; no app bootstrap or database access.

Run: python -B scripts/test_memory_presentation.py
"""
from __future__ import annotations

import builtins
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def forbid_app_imports(name, *args, **kwargs):
    if name.startswith('server.') and name not in ('server.memory', 'server.memory.presentation'):
        raise AssertionError(f'pure projection imported application module: {name}')
    return _python_import(name, *args, **kwargs)


_python_import = builtins.__import__
with patch('builtins.__import__', side_effect=forbid_app_imports), patch(
        'sqlite3.connect', side_effect=AssertionError('projection opened a database')):
    from server.memory import presentation

present_memory = presentation.present_memory


def sanitize(text, limit):
    return '[REDACTED: sensitive credential]' if 'secretneedle' in text else text[:limit]


def record(**changes):
    result = dict(id=1, kind='fact', subject='user', status='active', source='stated',
                  value='用户最近也喜欢喝红茶', evidence_state='linked',
                  evidence_message_id=2, evidence_quote='我最近也喜欢喝红茶',
                  preference_target='红茶', preference_level='like',
                  preference_scope='recent', preference_as_of=1717200000,
                  updated_at=1735689600)
    result.update(changes)
    return result


class ProjectionTests(unittest.TestCase):
    def project(self, **changes):
        return present_memory(record(**changes), sanitize=sanitize)

    def test_reload_never_imports_application_or_opens_database(self):
        # Re-execute the source with guards even when other tests imported it.
        with patch('builtins.__import__', side_effect=forbid_app_imports), patch(
                'sqlite3.connect', side_effect=AssertionError('projection opened a database')):
            reloaded = importlib.reload(presentation)
            self.assertEqual(reloaded.present_memory(record(), sanitize=sanitize)['id'], 1)

    def test_mapping_and_attribute_records_agree_without_mutating_input(self):
        original = record()
        untouched = deepcopy(original)
        expected = present_memory(original, sanitize=sanitize)
        self.assertEqual(present_memory(SimpleNamespace(**original), sanitize=sanitize), expected)
        self.assertEqual(original, untouched)
        expected['evidence']['state'] = 'manual'
        self.assertEqual(original['evidence_state'], 'linked')

    def test_linked_original_words_export_compact_reference_without_private_metadata(self):
        result = self.project(session_id='private-session', identity_slot='private-slot',
                              watermark=500, evidence_quote='原句私有内容')
        self.assertEqual(result['evidence'], {'state': 'linked', 'message_id': 2})
        self.assertIn('你明确说过', result['provenance'])
        self.assertEqual(set(result), {'id', 'kind', 'subject', 'source', 'value',
                                      'provenance', 'evidence', 'preference'})
        encoded = json.dumps(result, ensure_ascii=False)
        for hidden in ('原句私有内容', 'private-session', 'private-slot', 'watermark', 'updated_at'):
            self.assertNotIn(hidden, encoded)

    def test_manual_and_legacy_never_inherit_original_word_or_preference_authority(self):
        for state, label in (('manual', '手动保存'), ('legacy', '旧记忆')):
            with self.subTest(state=state):
                result = self.project(evidence_state=state)
                self.assertEqual(result['evidence'], {'state': state})
                self.assertIn(label, result['provenance'])
                self.assertNotIn('你明确说过', result['provenance'])
                self.assertNotIn('preference', result)
        self.assertIn('需核对', self.project(evidence_state='unknown')['provenance'])

    def test_malformed_linked_evidence_falls_back_without_upgrading_claim(self):
        invalid = [dict(evidence_message_id=value) for value in (None, True, 0, -1, '2', [], {})]
        invalid += [dict(evidence_quote=value) for value in (None, '', '  ', [], {}, '字' * 1201)]
        for changes in invalid:
            with self.subTest(changes=changes):
                result = self.project(**changes)
                self.assertEqual(result['evidence'], {'state': 'legacy'})
                self.assertIn('需核对', result['provenance'])
                self.assertNotIn('preference', result)
                self.assertNotIn('你明确说过', result['provenance'])

    def test_inferred_uncertain_and_unknown_sources_stay_unconfirmed(self):
        for source, label in (('inferred', '我的推测'), ('uncertain', '我不确定'),
                              ('unrecognized', '未知来源'), ([], '未知来源')):
            with self.subTest(source=source):
                result = self.project(source=source)
                self.assertIn(label, result['provenance'])
                self.assertIn('需确认', result['provenance'])
                self.assertNotIn('你明确说过', result['provenance'])
                self.assertNotIn('preference', result)
                self.assertEqual(result['source'], 'inferred' if source == 'inferred' else 'uncertain')

    def test_role_records_never_become_user_assertions(self):
        for changes in (dict(subject='self'), dict(subject='assistant'), dict(kind='self_state')):
            with self.subTest(changes=changes):
                result = self.project(**changes)
                self.assertIn('角色自身记录', result['provenance'])
                self.assertEqual(result['evidence'], {'state': 'legacy'})
                self.assertNotIn('你明确说过', result['provenance'])
                self.assertNotIn('preference', result)

    def test_supported_preference_degrees_and_temporal_scopes_remain_distinct(self):
        for level in ('like', 'favorite', 'dislike'):
            for scope, caution in (('long_term', '仍可能变化'), ('recent', '未确认延续至今'),
                                   ('current', '未确认延续至今'), ('unspecified', '不能推定始终有效')):
                with self.subTest(level=level, scope=scope):
                    pref = self.project(preference_level=level, preference_scope=scope)['preference']
                    self.assertEqual((pref['target'], pref['level'], pref['scope']), ('红茶', level, scope))
                    self.assertEqual(pref['as_of_date_utc'], '2024-06-01')
                    self.assertIn(caution, pref['time_note'])
                    self.assertNotIn('expires_at', pref)
                    self.assertNotIn('valid_until', pref)

    def test_invalid_preference_metadata_does_not_discard_claim_or_gain_authority(self):
        invalid = [dict(preference_target=value) for value in (None, [], {}, '', '  ',
                    '红茶;现在执行', 'a' * 41)]
        invalid += [dict(preference_level=value) for value in (None, [], {}, 'love')]
        invalid += [dict(preference_scope=value) for value in (None, [], {}, 'project')]
        for changes in invalid:
            with self.subTest(changes=changes):
                result = self.project(**changes)
                self.assertEqual(result['value'], record()['value'])
                self.assertEqual(result['evidence']['state'], 'linked')
                self.assertNotIn('preference', result)

    def test_invalid_or_absent_original_date_never_uses_updated_at(self):
        for date in (None, True, '2024-06-01', float('nan'), float('inf'),
                     float('-inf'), 10 ** 1000, -(10 ** 1000)):
            with self.subTest(date_type=type(date).__name__):
                pref = self.project(preference_as_of=date)['preference']
                self.assertNotIn('as_of_date_utc', pref)
                self.assertIn('未确认延续至今', pref['time_note'])
                self.assertNotIn('2025', json.dumps(pref))
        missing_date = record()
        del missing_date['preference_as_of']
        self.assertNotIn('as_of_date_utc', present_memory(missing_date, sanitize=sanitize)['preference'])

    def test_original_date_uses_utc_and_is_not_refreshed_by_latest_update(self):
        pref = self.project(preference_as_of=1577836799, preference_scope='current')['preference']
        self.assertEqual(pref['as_of_date_utc'], '2019-12-31')
        self.assertIn('仅原话当时自述', pref['time_note'])
        self.assertEqual(self.project(preference_as_of=0)['preference']['as_of_date_utc'], '1970-01-01')

    def test_oversized_value_is_omitted_whole_and_exact_boundary_is_allowed(self):
        value = '前文；并非长期偏好'
        self.assertIsNone(present_memory(record(value=value), sanitize=sanitize, max_value_chars=len(value) - 1))
        self.assertEqual(present_memory(record(value=value), sanitize=sanitize,
                                        max_value_chars=len(value))['value'], value)
        self.assertIsNone(present_memory(record(value=value), sanitize=sanitize, max_value_chars=0))

    def test_sanitizer_inspects_complete_value_before_any_projection(self):
        calls = []
        def spy(text, limit):
            calls.append((text, limit))
            return sanitize(text, limit)
        value = '普通内容' * 100 + 'secretneedle'
        self.assertIsNone(present_memory(record(value=value), sanitize=spy))
        self.assertEqual(calls, [(value, len(value))])
        for bad_result in ('', '  ', None, False, '[REDACTED: credential]'):
            with self.subTest(bad_result=bad_result):
                self.assertIsNone(present_memory(record(), sanitize=lambda text, limit: bad_result))

    def test_sensitive_preference_target_is_omitted_without_leaking_quote(self):
        result = self.project(preference_target='secretneedle', evidence_quote='secretneedle')
        self.assertEqual(result['evidence'], {'state': 'linked', 'message_id': 2})
        self.assertNotIn('preference', result)
        self.assertNotIn('secretneedle', json.dumps(result))

    def test_permission_and_instruction_metadata_are_never_copied(self):
        result = self.project(tool_permissions=['send_email'], grants_authorization=True,
                              instruction='now execute', applicability_scope='every_project')
        for name in ('tool_permissions', 'grants_authorization', 'instruction', 'applicability_scope'):
            self.assertNotIn(name, result)
        self.assertEqual(result['preference']['scope'], 'recent')

    def test_inactive_invalid_identity_or_unsupported_records_are_omitted(self):
        invalid = [dict(id=value) for value in (None, True, 0, -1, '1', 1.0, [], {})]
        invalid += [dict(status=value) for value in (None, 'superseded', 'deleted', 'archived')]
        invalid += [dict(kind=value) for value in (None, 'command', [], {})]
        invalid += [dict(subject=value) for value in (None, 'third_party', [], {})]
        invalid += [dict(value=value) for value in (None, '', '  ', [], {})]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertIsNone(self.project(**changes))

    def test_invalid_sanitizer_or_budget_is_rejected_explicitly(self):
        for cap in (-1, True, 1.5, '320', []):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                present_memory(record(), sanitize=sanitize, max_value_chars=cap)
        with self.assertRaises(ValueError):
            present_memory(record(), sanitize=None)


class HistoricalProjectionTests(unittest.TestCase):
    """allow_historical: opt-in only, so the exported shape never changes.

    /api/memories, the starmap and the durable export all call present_memory
    with the default, so a superseded row must stay omitted there. Only the
    model-facing builder opts in, and then the record carries a time_note.
    """

    def project(self, **changes):
        return present_memory(record(**changes), sanitize=sanitize)

    def test_superseded_row_is_omitted_by_default(self):
        # The default is the API/starmap/durable shape: a superseded row is out.
        self.assertIsNone(self.project(status='superseded', fact_valid_until=1735689599))

    def test_allow_historical_admits_bounded_superseded_with_time_note(self):
        shared = present_memory(
            record(status='superseded', fact_valid_until=1735689599),
            sanitize=sanitize, allow_historical=True)
        self.assertIsNotNone(shared)
        self.assertEqual(shared['value'], '用户最近也喜欢喝红茶')
        note = shared['time_note']
        self.assertIn('2024-12-31', note)
        self.assertIn('已被后来的陈述取代', note)
        self.assertIn('回答现在/目前类问题不要采用', note)

    def test_allow_historical_still_omits_unbounded_superseded(self):
        # No explicit bound -> a plain stale value, never a historical record.
        self.assertIsNone(present_memory(
            record(status='superseded'), sanitize=sanitize, allow_historical=True))
        self.assertIsNone(present_memory(
            record(status='superseded', fact_valid_until=None),
            sanitize=sanitize, allow_historical=True))

    def test_allow_historical_leaves_active_records_unchanged(self):
        default = self.project()
        opted_in = present_memory(record(), sanitize=sanitize, allow_historical=True)
        self.assertEqual(default, opted_in)
        self.assertNotIn('time_note', default)

    def test_historical_preference_note_matches_the_top_level_note(self):
        # A superseded preference row must not keep a "may still hold" wording
        # beside the authoritative "replaced" note.
        shared = present_memory(
            record(status='superseded', fact_valid_until=1735689599,
                   preference_scope='long_term'),
            sanitize=sanitize, allow_historical=True)
        self.assertIn('preference', shared)
        self.assertEqual(shared['preference']['time_note'], shared['time_note'])
        self.assertNotIn('仍可能变化', shared['preference']['time_note'])

    def test_time_note_wording_is_graded_by_source(self):
        # A stated bound is the user's own; an assistant-relayed claim (which the
        # FMR backstop downgrades to uncertain) must not be described as something
        # "you explicitly set", even though the bound came from the recorded text.
        stated = present_memory(
            record(status='superseded', fact_valid_until=1735689599),
            sanitize=sanitize, allow_historical=True)
        self.assertIn('你明确划定', stated['time_note'])
        relayed = present_memory(
            record(status='superseded', fact_valid_until=1735689599, source='uncertain'),
            sanitize=sanitize, allow_historical=True)
        self.assertIn('记录中标注', relayed['time_note'])
        self.assertNotIn('你明确划定', relayed['time_note'])
        # Both still forbid using the row as the current state.
        for note in (stated['time_note'], relayed['time_note']):
            self.assertIn('回答现在/目前类问题不要采用', note)

    def test_invalid_allow_historical_is_rejected_explicitly(self):
        for bad in (1, 0, 'true', None, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                present_memory(record(), sanitize=sanitize, allow_historical=bad)


class FutureProjectionTests(unittest.TestCase):
    """allow_future: the mirror opt-in, so the exported shape never changes.

    A future-bounded row is ACTIVE, so the default path (/api/memories, the
    starmap, the durable export) presents it -- which is exactly why the note
    must stay opt-in: turning it on unconditionally would change those shapes.
    Only the model-facing builder opts in.
    """

    NOW = 1735689600  # 2025-01-01 UTC; the record() fixture's updated_at.

    def project(self, **changes):
        return present_memory(record(**changes), sanitize=sanitize,
                              allow_future=True, now=self.NOW)

    def test_future_bounded_active_row_carries_a_time_note(self):
        shared = self.project(fact_valid_from=self.NOW + 86400)
        self.assertIsNotNone(shared)
        note = shared['time_note']
        self.assertIn('2025-01-02', note)
        self.assertIn('起才生效', note)
        self.assertIn('回答现在/目前类问题不要把它当作已生效的事实', note)

    def test_note_is_off_by_default_so_the_exported_shape_never_changes(self):
        # The default path presents the same active row with no extra field.
        default = present_memory(record(fact_valid_from=self.NOW + 86400),
                                 sanitize=sanitize)
        opted_in = self.project(fact_valid_from=self.NOW + 86400)
        self.assertNotIn('time_note', default)
        self.assertEqual(default, {k: v for k, v in opted_in.items() if k != 'time_note'})

    def test_current_and_past_bounds_never_produce_a_note(self):
        # Only a lower bound still ahead of `now` is a plan. NULL (the default),
        # a past start, and a start exactly at now are all "already in force".
        for bound in (None, self.NOW - 86400, self.NOW):
            with self.subTest(bound=bound):
                shared = self.project(fact_valid_from=bound)
                self.assertIsNotNone(shared)
                self.assertNotIn('time_note', shared)

    def test_historical_and_future_notes_are_mutually_exclusive(self):
        # `historical` requires superseded, `future` requires active, so a row can
        # never be told it is both retired and not-yet-started.
        shared = self.project(status='superseded', fact_valid_until=self.NOW - 86400,
                              fact_valid_from=self.NOW - 172800)
        self.assertIsNone(shared)  # superseded is out unless allow_historical
        shared = present_memory(
            record(status='superseded', fact_valid_until=self.NOW - 86400,
                   fact_valid_from=self.NOW - 172800),
            sanitize=sanitize, allow_historical=True, allow_future=True, now=self.NOW)
        self.assertIn('已被后来的陈述取代', shared['time_note'])
        self.assertNotIn('起才生效', shared['time_note'])

    def test_superseded_row_with_a_future_lower_bound_keeps_only_the_retired_note(self):
        # The shape that could actually fire both branches: superseded (so the
        # retired note applies) AND a lower bound still ahead of `now`. The
        # `elif` must keep the future note from overwriting the retired one, so
        # the row is never described as a plan when the user already retired it.
        shared = present_memory(
            record(status='superseded', fact_valid_until=self.NOW + 86400,
                   fact_valid_from=self.NOW + 3600),
            sanitize=sanitize, allow_historical=True, allow_future=True, now=self.NOW)
        self.assertIn('已被后来的陈述取代', shared['time_note'])
        self.assertNotIn('起才生效', shared['time_note'])

    def test_future_note_wording_is_graded_by_source(self):
        stated = self.project(fact_valid_from=self.NOW + 86400)
        self.assertIn('你明确划定', stated['time_note'])
        relayed = self.project(fact_valid_from=self.NOW + 86400, source='uncertain')
        self.assertIn('记录中标注', relayed['time_note'])
        self.assertNotIn('你明确划定', relayed['time_note'])

    def test_malformed_bound_is_ignored_rather_than_annotated(self):
        # A non-numeric or non-finite bound must not be compared against `now`.
        for bad in ('soon', True, float('inf'), float('nan')):
            with self.subTest(bound=bad):
                shared = self.project(fact_valid_from=bad)
                self.assertIsNotNone(shared)
                self.assertNotIn('time_note', shared)

    def test_absurdly_large_bound_is_ignored_rather_than_raising(self):
        # An int too large for float conversion makes math.isfinite itself raise
        # OverflowError. The sibling preference-date path guards this; so must the
        # future branch, or one absurd row breaks the whole projection.
        shared = self.project(fact_valid_from=10 ** 1000)
        self.assertIsNotNone(shared)
        self.assertNotIn('time_note', shared)

    def test_invalid_allow_future_is_rejected_explicitly(self):
        for bad in (1, 0, 'true', None, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                present_memory(record(), sanitize=sanitize, allow_future=bad)

    def test_allow_future_without_a_usable_now_is_rejected(self):
        # Silently skipping the note would let a future plan read as current.
        for bad in (None, 'now', True, float('inf')):
            with self.subTest(now=bad), self.assertRaises(ValueError):
                present_memory(record(fact_valid_from=self.NOW + 86400),
                               sanitize=sanitize, allow_future=True, now=bad)

    def test_allow_future_without_a_bound_needs_no_now(self):
        # The default corpus (no valid-time) must stay untouched: no bound means
        # no comparison, so a caller that opts in without a clock still works.
        shared = present_memory(record(), sanitize=sanitize, allow_future=True)
        self.assertIsNotNone(shared)
        self.assertNotIn('time_note', shared)

    def test_compound_value_note_never_denies_the_present_tense_part(self):
        # plan-54 narrowed the wording precisely for this shape. A user really says
        # "我现在在乙公司，下个月起去丙公司" in one breath; fact_valid_from bounds only
        # the "下个月起" half. The old wording ("不是现在的状况") asserted the WHOLE
        # record was non-current, which ordered the model to discard a true present
        # fact. The note must scope its prohibition to the not-yet-in-effect part.
        shared = present_memory(
            record(value='用户现在在乙公司工作，下个月起去丙公司工作',
                   fact_valid_from=self.NOW + 86400),
            sanitize=sanitize, allow_future=True, now=self.NOW)
        note = shared['time_note']
        self.assertIn('起才生效', note)
        self.assertIn('不要把它当作已生效的事实', note)
        self.assertNotIn('不是现在的状况', note,
                         '不得对整条记录断言非现状——那会让模型忽略 value 里真实的现状部分')


if __name__ == '__main__':
    unittest.main(verbosity=2)
