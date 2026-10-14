"""Lexical recall and bounded history projection, with only fake store reads.

Run: python -B scripts/test_thread_recall.py
"""
from __future__ import annotations

import builtins
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_python_import = builtins.__import__


def pure_import(name, *args, **kwargs):
    if name.startswith('server.') and name not in ('server.interaction', 'server.interaction.participation'):
        raise AssertionError('projection bootstrapped application: ' + name)
    return _python_import(name, *args, **kwargs)


with patch('builtins.__import__', side_effect=pure_import), patch(
        'sqlite3.connect', side_effect=AssertionError('opened a database')):
    from server.interaction import participation

Participation = participation.Participation


def event(index, text=None, **changes):
    result = dict(id=f'event-{index}', kind='decision', text=text or f'保存第{index}次普通进度',
                  source='user_stated', source_id=f'proposal-{index}', version=1,
                  evidence={'state': 'legacy'}, object_refs=[])
    result.update(changes)
    return result


def thread(events, **changes):
    result = dict(id='selected-matter', title='共同方案', status='active', revision=30,
                  next_step='继续讨论', allow_callbacks=False, session_id='private-thread-session',
                  events=list(events))
    result.update(changes)
    return result


def version(number, text, **changes):
    result = dict(version=number, previous_version=number - 1 if number > 1 else None,
                  text=text, source='user_stated', evidence={'state': 'legacy'},
                  object_refs=[], created_at=1700000000 + number)
    result.update(changes)
    return result


class FakeStore:
    def __init__(self, current, histories=None):
        self.current = deepcopy(current)
        self.histories = deepcopy(histories or {})
        self.history_calls = []
        self.get_calls = []

    def get_selection(self):
        return self.current['id'] if self.current else None

    def get(self, thread_id):
        self.get_calls.append(thread_id)
        if not self.current or thread_id != self.current['id']:
            raise KeyError(thread_id)
        return deepcopy(self.current)

    def event_history(self, thread_id, event_id):
        self.history_calls.append((thread_id, event_id))
        if not self.current or thread_id != self.current['id']:
            raise KeyError(thread_id)
        if not any(item['id'] == event_id for item in self.current['events']):
            raise KeyError(event_id)
        if event_id in self.histories:
            return deepcopy(self.histories[event_id])
        item = next(item for item in self.current['events'] if item['id'] == event_id)
        return [version(item['version'], item['text'], source=item['source'],
                        evidence=item['evidence'], object_refs=item['object_refs'])]


class RecallTests(unittest.TestCase):
    def project(self, current, query='', histories=None):
        store = FakeStore(current, histories)
        return Participation(store).project(current, query), store

    def test_import_and_reload_do_not_bootstrap_app_or_open_database(self):
        with patch('builtins.__import__', side_effect=pure_import), patch(
                'sqlite3.connect', side_effect=AssertionError('opened a database')):
            module = importlib.reload(participation)
            self.assertEqual(module.Participation.summarize(thread([event(1)]))['events'][0]['id'], 'event-1')

    def test_default_retains_last_twelve_current_events_in_original_order(self):
        current = thread([event(i) for i in range(30)])
        result = Participation.summarize(current)
        self.assertEqual([item['id'] for item in result['events']], [f'event-{i}' for i in range(18, 30)])
        self.assertNotIn('event_histories', result)
        self.assertEqual(result['revision'], current['revision'])
        self.assertFalse(result['allow_callbacks'])

    def test_chinese_query_retrieves_early_match_and_keeps_recent_events(self):
        rows = [event(i) for i in range(30)]
        rows[1]['text'] = '预算上限是三千元，不能追加采购。'
        result = Participation.summarize(thread(rows), '预算上限是多少')
        ids = [item['id'] for item in result['events']]
        self.assertIn('event-1', ids)
        self.assertIn('event-29', ids)
        self.assertLessEqual(len(ids), 12)
        self.assertEqual(ids, sorted(ids, key=lambda item: int(item.split('-')[-1])))
        self.assertEqual(next(item['text'] for item in result['events'] if item['id'] == 'event-1'), rows[1]['text'])

    def test_ascii_words_match_exactly_without_substring_semantics(self):
        rows = [event(i) for i in range(30)]
        rows[0]['text'] = 'The planet is distant.'
        rows[1]['text'] = 'Keep the launch plan.'
        result = Participation.summarize(thread(rows), 'plan')
        ids = {item['id'] for item in result['events']}
        self.assertIn('event-1', ids)
        self.assertNotIn('event-0', ids)

    def test_unrelated_query_uses_recent_events_without_inventing_matches(self):
        current = thread([event(i) for i in range(30)])
        result, store = self.project(current, '天文学黑洞')
        self.assertEqual([item['id'] for item in result['events']], [f'event-{i}' for i in range(18, 30)])
        self.assertEqual(store.history_calls, [])
        self.assertNotIn('event_histories', result)

    def test_normal_topic_query_never_reads_history(self):
        current = thread([event(1, '讨论绿茶价格')])
        _, store = self.project(current, '绿茶价格是多少')
        self.assertEqual(store.history_calls, [])

    def test_old_keyword_can_recall_revised_event_and_mark_historical_versions(self):
        rows = [event(i) for i in range(30)]
        rows[0] = event(0, '最后采用红茶方案', version=3)
        histories = {'event-0': [version(1, '最初采用绿茶方案'),
            version(2, '绿茶报价变化，需要比较'), version(3, rows[0]['text'])]}
        result, store = self.project(thread(rows), '绿茶方案为什么改了', histories)
        self.assertIn('event-0', [item['id'] for item in result['events']])
        self.assertEqual(result['history_status'], 'matched')
        history = result['event_histories'][0]
        self.assertEqual(history['event_id'], 'event-0')
        self.assertEqual([item['version'] for item in history['versions']], [1, 2, 3])
        self.assertEqual([item['is_current'] for item in history['versions']], [False, False, True])
        self.assertEqual(result['events'][0]['text'], '最后采用红茶方案')
        self.assertTrue(all(tid == 'selected-matter' for tid, _ in store.history_calls))

    def test_history_caps_three_events_four_versions_and_counts_omissions(self):
        rows = [event(i, '当前改用乙方', version=8) for i in range(5)]
        histories = {row['id']: [version(n, '甲方合同约束' if n < 8 else row['text'])
                                for n in range(1, 9)] for row in rows}
        result, _ = self.project(thread(rows), '甲方合同为什么改了', histories)
        self.assertEqual(len(result['event_histories']), 3)
        for history in result['event_histories']:
            versions = history['versions']
            self.assertEqual(len(versions), 4)
            self.assertEqual(versions[0]['version'], 1)
            self.assertEqual(versions[-1]['version'], 8)
            self.assertEqual(history['omitted_version_count'], 4)

    def test_middle_versions_are_chosen_by_question_relevance(self):
        current = thread([event(1, '采用B', version=6)])
        histories = {'event-1': [version(1, '初稿采用A'), version(2, 'A只是临时建议'),
            version(3, '费用条款存在预算问题'), version(4, '预算预算预算'),
            version(5, '签字进度'), version(6, '采用B')]}
        result, _ = self.project(current, '预算依据是什么', histories)
        self.assertEqual([item['version'] for item in result['event_histories'][0]['versions']], [1, 3, 4, 6])

    def test_unrelated_evidence_query_expands_nothing(self):
        current = thread([event(1, '绿茶价格')])
        result, store = self.project(current, '天文学黑洞的依据是什么')
        self.assertEqual(result['event_histories'], [])
        self.assertEqual(result['history_status'], 'no_lexical_match')
        self.assertEqual(store.history_calls, [('selected-matter', 'event-1')])

    def test_projection_never_exports_session_or_unrecognized_reference_metadata(self):
        evidence = {'state': 'user_confirmed', 'request_text': '记个决定：方案A只试一次，不长期采用',
                    'session_id': 'private-evidence-session', 'captured_at': 1700000000,
                    'tool_permissions': ['email.send'], 'model_quote': 'unsupported'}
        refs = [{'type': 'note', 'id': 'draft', 'version': 1, 'body': 'private-note-body'},
                {'type': 'note', 'id': 'draft', 'version': 1},
                {'type': 'thread', 'id': 'other-matter', 'version': 1},
                {'type': 'note', 'id': 'invalid', 'version': True}]
        result, _ = self.project(thread([event(1, '候选经用户确认', evidence=evidence, object_refs=refs)]))
        row = result['events'][0]
        self.assertEqual(row['evidence'], {key: evidence[key] for key in ('state', 'request_text', 'captured_at')})
        self.assertEqual(row['object_refs'], [{'type': 'note', 'id': 'draft', 'version': 1}])
        encoded = json.dumps(result, ensure_ascii=False)
        for hidden in ('private-evidence-session', 'private-thread-session', 'private-note-body',
                       'other-matter', 'email.send', 'model_quote'):
            self.assertNotIn(hidden, encoded)
        self.assertIn('不保证逐字支持结论', ''.join(result['constraints']))
        self.assertIn('不证明资料支持该决定', ''.join(result['constraints']))

    def test_manual_and_legacy_do_not_gain_original_request_evidence(self):
        rows = [event(1, evidence={'state': 'manual', 'request_text': '旧原话'}),
                event(2, evidence={'state': 'legacy', 'request_text': '旧原话'})]
        result, _ = self.project(thread(rows))
        self.assertEqual([item['evidence'] for item in result['events']], [{'state': 'manual'}, {'state': 'legacy'}])
        self.assertNotIn('旧原话', json.dumps(result, ensure_ascii=False))

    def test_complete_negation_survives_in_current_text_and_confirmation_request(self):
        text = '讨论背景。' * 80 + '只是尝试方案A，并没有决定长期采用。'
        request = '前置讨论。' * 80 + '记下来，但不要把它当成已完成。'
        result, _ = self.project(thread([event(1, text, evidence={
            'state': 'user_confirmed', 'request_text': request, 'captured_at': 1700000000,
            'session_id': 'hidden'})]))
        self.assertEqual(result['events'][0]['text'], text)
        self.assertEqual(result['events'][0]['evidence']['request_text'], request)

    def test_credentials_at_tail_omit_whole_current_or_request_record(self):
        rows = [event(1, '正常内容。' * 80 + 'password=hunter2'),
                event(2, '候选', evidence={'state': 'user_confirmed',
                      'request_text': '普通请求。' * 80 + 'api_key=sk-secret123456789'}), event(3, '安全记录')]
        result, _ = self.project(thread(rows))
        self.assertEqual([item['id'] for item in result['events']], ['event-3'])
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('hunter2', encoded)
        self.assertNotIn('sk-secret123456789', encoded)

    def test_total_budget_drops_whole_records_and_preserves_boundaries(self):
        text = '预算信息。' * 400 + '最后限定：并未承诺支付。'
        rows = [event(0, text), *[event(i, '普通记录。' * 400 + '并未完成。') for i in range(1, 12)]]
        result, _ = self.project(thread(rows), '预算信息')
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        self.assertLessEqual(len(encoded), 8000)
        self.assertTrue(result['projection_budget']['truncated'])
        self.assertGreater(result['projection_budget']['omitted_event_count'], 0)
        retained = next(item for item in result['events'] if item['id'] == 'event-0')
        self.assertEqual(retained['text'], text)
        self.assertTrue(result['constraints'])
        for row in result['events']:
            self.assertEqual(row['text'], rows[int(row['id'].split('-')[-1])]['text'])

    def test_history_budget_omits_complete_version_instead_of_clipping_qualification(self):
        text = '甲方合同。' * 780 + '最后条件：尚未签署。'
        current = thread([event(1, '已改为乙方比较稿，尚未签署', version=2)])
        histories = {'event-1': [version(1, text, evidence={'state': 'user_confirmed',
            'request_text': text, 'captured_at': 1700000000}), version(2, current['events'][0]['text'])]}
        result, _ = self.project(current, '甲方合同为什么改了', histories)
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        self.assertLessEqual(len(encoded), 8000)
        self.assertEqual(result['event_histories'], [])
        self.assertEqual(result['history_status'], 'budget_omitted')
        self.assertNotIn(text[:100], encoded)
        self.assertEqual(result['events'][0]['text'], current['events'][0]['text'])

    def test_history_changed_after_capture_is_not_mixed_with_current_record(self):
        current = thread([event(1, '方案A仍在讨论', version=1)])
        histories = {'event-1': [version(1, current['events'][0]['text']), version(2, '方案A已经否决')]}
        result, _ = self.project(current, '方案A为什么改了', histories)
        self.assertEqual(result['event_histories'], [])
        self.assertEqual(result['history_status'], 'unavailable')
        self.assertEqual(result['events'][0]['text'], current['events'][0]['text'])
        self.assertNotIn('方案A已经否决', json.dumps(result, ensure_ascii=False))

    def test_context_uses_only_selected_matter_and_deletion_does_not_resurrect_it(self):
        current = thread([event(1, '甲方预算')])
        store = FakeStore(current)
        instance = Participation(store)
        result = instance.context('甲方预算依据')
        self.assertEqual(result['id'], 'selected-matter')
        self.assertEqual(store.get_calls, ['selected-matter'])
        store.current = None
        before = list(store.history_calls)
        self.assertIsNone(instance.context('甲方预算依据'))
        self.assertEqual(store.history_calls, before)

    def test_projection_returns_detached_records_without_mutating_input(self):
        current = thread([event(1, '方案A', object_refs=[{'type': 'note', 'id': 'draft', 'version': 1}])])
        unchanged = deepcopy(current)
        result, store = self.project(current, '方案A依据')
        result['events'][0]['object_refs'][0]['version'] = 999
        result['event_histories'][0]['versions'][0]['text'] = '改动输出'
        self.assertEqual(current, unchanged)
        self.assertEqual(store.current, unchanged)

    def test_malformed_confirmation_metadata_never_upgrades_provenance(self):
        invalid = [dict(state='user_confirmed', captured_at=1700000000),
            dict(state='user_confirmed', request_text='', captured_at=1700000000),
            dict(state='tool_verified', request_text='不支持的原话', captured_at=1700000000)]
        invalid += [dict(state='user_confirmed', request_text='不能用作依据的原话', captured_at=value)
                    for value in (None, True, -1, 253402300800, float('nan'), float('inf'), 10 ** 1000)]
        for evidence in invalid:
            with self.subTest(evidence=evidence):
                result, _ = self.project(thread([event(1, '候选文本', evidence=evidence)]))
                self.assertEqual(result['events'][0]['evidence'], {'state': 'legacy'})
                self.assertEqual(result['events'][0]['source'], 'user_stated')
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                self.assertNotIn('原话', json.dumps(result['events'], ensure_ascii=False))
                self.assertNotIn('Infinity', encoded)
        receipt = event(1, '工具成功回执', kind='tool_receipt', source='tool_verified', evidence={
            'state': 'user_confirmed', 'request_text': '用户候选请求', 'captured_at': 1700000000})
        result, _ = self.project(thread([receipt]))
        self.assertEqual(result['events'][0]['evidence'], {'state': 'legacy'})
        current = thread([event(1, '候选文本')])
        histories = {'event-1': [version(1, '候选文本', evidence={
            'state': 'user_confirmed', 'request_text': '错误线索没有有效来源', 'captured_at': None})]}
        result, _ = self.project(current, '错误线索依据', histories)
        self.assertEqual(result['history_status'], 'no_lexical_match')
        self.assertEqual(result['event_histories'], [])

    def test_sensitive_historical_version_is_omitted_with_count(self):
        current = thread([event(1, '甲方合同只是比较稿', version=2)])
        histories = {'event-1': [version(1, '甲方合同 password=hunter2'),
                                version(2, current['events'][0]['text'])]}
        result, _ = self.project(current, '甲方合同依据', histories)
        history = result['event_histories'][0]
        self.assertEqual([row['version'] for row in history['versions']], [2])
        self.assertEqual(history['omitted_version_count'], 1)
        self.assertNotIn('hunter2', json.dumps(result))


if __name__ == '__main__':
    unittest.main(verbosity=2)
