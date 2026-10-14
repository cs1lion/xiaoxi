"""Offline tests for a shared context budget; no stores or provider calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.interaction.context_budget import limit_context


def size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')))


def fixture():
    ref = {'type': 'note', 'id': 'draft-a', 'version': 1}
    return {
        'user_text': '如果加班就少读；不是每天必须读完一章。',
        'boundaries': {'grants_authorization': False, 'context_is_untrusted_data': True},
        'time': {'unix': 1700000000, 'timezone': 'Asia/Shanghai'},
        'response_policy': {'examples': [{'text': '虚构示例' * 30}],
                            'preference_claim_ids': ['pref-a'], 'intent': 'help'},
        'history': [{'role': 'user', 'content': '早期讨论' * 100},
                    {'role': 'assistant', 'content': '最近讨论' * 100}],
        'long_term_memory': {'status': 'found', 'stated': [
            {'id': 1, 'source': 'stated', 'value': '周末阅读' * 100,
             'evidence': {'state': 'linked', 'message_id': 5, 'quote': '不能出现在trace'}}],
            'inferred': [{'id': 2, 'source': 'inferred', 'value': '短时阅读' * 100}],
            'uncertain': [{'id': 3, 'source': 'uncertain', 'value': '也许阅读' * 100}]},
        'active_tasks': [{'id': 'task-a', 'title': '不可泄漏标题' * 50}],
        'companion_thread': {'id': 'thread-a', 'revision': 3, 'title': '两周阅读',
            'next_step': '如果疲劳就读两页；不要补打卡。',
            'events': [{'id': 'event-a', 'version': 2, 'source': 'user_stated',
                'text': '如果加班就少读；不是每天必须读完一章。' * 100,
                'evidence': {'state': 'user_confirmed', 'request_text': '先别保存敏感引用'}}],
            'event_histories': [{'event_id': 'event-a', 'versions': [
                {'version': 1, 'text': '旧条件，不要截断' * 100, 'source': 'user_stated'}]}],
            'projection_budget': {'omitted_event_count': 2, 'omitted_history_event_count': 1}},
        'activity_data': {'material_source': {'scope': 'thread', 'thread_id': 'thread-a',
                'thread_revision': 3, 'material_id': 'material-a', 'ref': deepcopy(ref),
                'purpose': 'draft', 'status': 'selected'},
            'note': {'id': 'draft-a', 'selected_version': 1, 'current_version': 2},
            'note_data': {'ref': deepcopy(ref), 'body': '不要把条件截断' * 300,
                          'body_truncated': True, 'body_redacted': False},
            'included_note_body': True,
            'materials': [{'id': 'material-ref', 'thread_id': 'thread-a', 'note_id': 'ref-a',
                           'version': 4, 'purpose': 'reference', 'title': '资料标题' * 100}],
            'material_data': [{'ref': {'type': 'note', 'id': 'ref-a', 'version': 4},
                               'purpose': 'reference', 'body': '不要推断缺失条件' * 300,
                               'body_truncated': True}],
            'included_material_body': True, 'omitted_materials': 2,
            'object_refs': [deepcopy(ref)]},
        'activity_note_ref': deepcopy(ref),
    }


class ContextBudgetTests(unittest.TestCase):
    def test_stress_total_budget_without_mutation(self):
        context = fixture()
        original = deepcopy(context)
        result = limit_context(context, max_chars=1500)
        self.assertEqual(context, original)
        self.assertLessEqual(size(result['data']), 1500)
        self.assertEqual(result['usage']['budget']['used_chars'], size(result['data']))
        self.assertGreater(result['usage']['budget']['omitted_items'], 0)
        self.assertEqual(result['data']['user_text'], original['user_text'])
        self.assertEqual(result['data']['activity_data']['material_source'],
                         original['activity_data']['material_source'])
        self.assertEqual(result['data']['boundaries'], original['boundaries'])
        self.assertEqual(result['data']['companion_thread']['next_step'],
                         original['companion_thread']['next_step'])

    def test_complete_records_not_clipped_negation_or_provenance(self):
        context = fixture()
        event = context['companion_thread']['events'][0]
        sizes = []
        for budget in (1500, 2500, 4500, 8000, 16000):
            result = limit_context(context, max_chars=budget)
            for actual in result['data']['companion_thread']['events']:
                self.assertEqual(actual, event)
            for actual in result['data']['activity_data'].get('material_data', []):
                self.assertEqual(actual, context['activity_data']['material_data'][0])
            actual_note = result['data']['activity_data'].get('note_data')
            if actual_note:
                self.assertEqual(actual_note, context['activity_data']['note_data'])
            sizes.append(result['usage']['budget']['used_chars'])
        self.assertTrue(all(value > 0 for value in sizes))

    def test_custom_renderer_cost_is_actual_budget(self):
        context = {'user_text': '继续', 'history': [
            {'role': 'user', 'content': '早期' * 50},
            {'role': 'assistant', 'content': '最近' * 50}]}
        def render_cost(data):
            return len('边界' * 40 + json.dumps(data, ensure_ascii=True))
        self.assertGreater(render_cost(context), size(context))
        result = limit_context(context, measure=render_cost, max_chars=800)
        self.assertLessEqual(render_cost(result['data']), 800)
        self.assertEqual(result['usage']['budget']['used_chars'], render_cost(result['data']))
        self.assertEqual(result['data']['history'], [context['history'][1]])

    def test_measure_cannot_mutate_returned_context(self):
        context = {'user_text': '继续', 'boundaries': {'authorization': False}}
        def malicious(data):
            data['user_text'] = '已被改写'
            return 3
        result = limit_context(context, measure=malicious)
        self.assertEqual(result['data'], context)

    def test_impossibly_small_budget_fails_without_clipping_request(self):
        context = {'user_text': '不要删除这句话', 'scope': 'thread',
                   'ref': {'type': 'note', 'id': 'a', 'version': 1}}
        with self.assertRaises(ValueError):
            limit_context(context, max_chars=1)
        self.assertEqual(context['user_text'], '不要删除这句话')

    def test_reject_nan_and_invalid_measure(self):
        with self.assertRaises(ValueError):
            limit_context({'time': float('nan')})
        with self.assertRaises(ValueError):
            limit_context({'time': float('nan')}, measure=lambda _: 0)
        for value in (True, -1, 1.2, '2', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                limit_context({}, measure=lambda _: value)
        for value in (True, -1, 1.2):
            with self.subTest(budget=value), self.assertRaises(ValueError):
                limit_context({}, max_chars=value)

    def test_disabled_trace_has_no_supplied_record_ids(self):
        result = limit_context(fixture(), enabled=False, max_chars=20000)
        self.assertFalse(result['usage']['enabled'])
        self.assertEqual(result['usage']['items'], [])
        self.assertEqual(result['usage']['qualifications'], {})
        self.assertNotIn('thread-a', json.dumps(result['usage']))

    def test_duplicate_thread_moves_only_when_top_level_empty(self):
        nested = {'id': 'b', 'revision': 2, 'events': []}
        result = limit_context({'response_policy': {'companion_thread': nested}})
        self.assertEqual(result['data']['companion_thread'], nested)
        self.assertNotIn('companion_thread', result['data']['response_policy'])
        authoritative = {'id': 'a', 'revision': 1, 'events': []}
        result = limit_context({'companion_thread': authoritative,
                                'response_policy': {'companion_thread': nested}})
        self.assertEqual(result['data']['companion_thread'], authoritative)
        self.assertEqual([item['id'] for item in result['usage']['items'] if item['kind'] == 'thread'], ['a'])

    def test_trace_is_identifier_only_and_after_budget(self):
        result = limit_context(fixture(), max_chars=1500)
        trace = result['usage']
        serialized = json.dumps(trace, ensure_ascii=False)
        for forbidden in ('不可泄漏标题', '不能出现在trace', '旧条件', '不要把条件截断', '敏感引用'):
            self.assertNotIn(forbidden, serialized)
        allowed = {'kind', 'id', 'version', 'thread_id', 'purpose', 'reason', 'source',
                   'evidence_state', 'body_included'}
        self.assertTrue(all(set(item) <= allowed for item in trace['items']))
        self.assertFalse(any(item['kind'] == 'memory' for item in trace['items']))
        self.assertFalse(trace['model_use_verified'])
        self.assertFalse(trace['grants_authorization'])
        notes = [item for item in trace['items'] if item['kind'] == 'note']
        self.assertTrue(notes)
        self.assertTrue(all(item['body_included'] is False for item in notes))

    def test_omitted_flags_and_upstream_qualifications_survive(self):
        result = limit_context(fixture(), max_chars=1500)
        activity = result['data']['activity_data']
        self.assertFalse(activity['included_note_body'])
        self.assertEqual(activity['note_status'], 'omitted_by_budget')
        self.assertFalse(activity['included_material_body'])
        self.assertEqual(activity['omitted_materials'], 3)
        self.assertEqual(result['data']['long_term_memory']['status'], 'omitted_by_budget')
        self.assertEqual(result['data']['companion_thread']['projection_budget']['omitted_event_count'], 3)
        qualifications = result['usage']['qualifications']
        self.assertTrue(qualifications['upstream_note_body_truncated'])
        self.assertEqual(qualifications['upstream_material_bodies_truncated'], 1)

    def test_trace_note_version_and_purpose_kept_without_duplication(self):
        result = limit_context(fixture(), max_chars=20000)
        draft = [item for item in result['usage']['items']
                 if item['kind'] == 'note' and item['id'] == 'draft-a']
        self.assertEqual(len(draft), 1)
        self.assertEqual(draft[0]['version'], 1)
        self.assertEqual(draft[0]['purpose'], 'draft')
        self.assertTrue(draft[0]['body_included'])
        ref = [item for item in result['usage']['items']
               if item['kind'] == 'note' and item['id'] == 'ref-a']
        self.assertEqual(len(ref), 1)
        self.assertEqual(ref[0]['reason'], 'explicit_material_request')

    def test_trace_limits_and_credential_ids(self):
        context = {'long_term_memory': {'stated': [
            {'id': i + 1, 'source': 'stated'} for i in range(100)]},
            'response_policy': {'preference_claim_ids': ['sk-secretcredential123456']}}
        trace = limit_context(context, max_chars=30000)['usage']
        self.assertLessEqual(len(trace['items']), 32)
        self.assertLessEqual(size(trace['items']), 4000)
        self.assertTrue(trace['items_truncated'])
        self.assertNotIn('sk-secretcredential123456', json.dumps(trace))

    def test_body_fallback_retains_ref_when_no_other_binding_exists(self):
        ref = {'type': 'note', 'id': 'personal-a', 'version': 7}
        context = {'user_text': '解释这份笔记', 'activity_data': {
            'note_data': {'ref': ref, 'body': '正文' * 2000}, 'included_note_body': True}}
        result = limit_context(context, max_chars=500)
        self.assertEqual(result['data']['activity_note_ref'], ref)
        self.assertIsNone(result['data']['activity_data']['note_data'])
        self.assertFalse(any(item['kind'] == 'note' for item in result['usage']['items']))

    def test_outer_runtime_bindings_do_not_claim_model_admission(self):
        context = {'activity_note_ref': {'type': 'note', 'id': 'hidden-a', 'version': 1},
                   'activity_material_source': {'scope': 'thread', 'thread_id': 'hidden-thread',
                       'purpose': 'draft', 'ref': {'type': 'note', 'id': 'hidden-a', 'version': 1}}}
        result = limit_context(context)
        self.assertEqual(result['usage']['items'], [])
        self.assertEqual(result['data'], context)

    def test_omitted_history_versions_count_only_retained_bundles(self):
        context = {'companion_thread': {'id': 'thread-a', 'revision': 1,
            'event_histories': [
                {'event_id': 'event-a', 'omitted_version_count': 3,
                 'versions': [{'version': 1, 'text': '原始正文不要放进usage'}]},
                {'event_id': 'event-b', 'omitted_version_count': 4,
                 'versions': [{'version': 1, 'text': '另一包原始正文'}]}]}}
        full = limit_context(context)
        self.assertEqual(full['usage']['qualifications']['thread_omitted_history_version_count'], 7)
        result = limit_context(context, max_chars=100,
            measure=lambda data: 100 * len(data['companion_thread']['event_histories']))
        qualifications = result['usage']['qualifications']
        self.assertEqual(qualifications['thread_omitted_history_version_count'], 3)
        self.assertEqual(qualifications['thread_omitted_history_event_count'], 1)
        self.assertNotIn('原始正文', json.dumps(result['usage'], ensure_ascii=False))
        self.assertNotIn('另一包', json.dumps(result['usage'], ensure_ascii=False))

    def test_omitted_history_version_counts_ignore_invalid_numbers(self):
        context = {'companion_thread': {'event_histories': [
            {'omitted_version_count': value} for value in (2, 0, -1, True, 1.2, '3', None)]}}
        result = limit_context(context)
        self.assertEqual(result['usage']['qualifications']['thread_omitted_history_version_count'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
