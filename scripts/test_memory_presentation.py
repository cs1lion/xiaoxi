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


if __name__ == '__main__':
    unittest.main(verbosity=2)
