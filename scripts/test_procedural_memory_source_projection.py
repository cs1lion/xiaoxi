"""Offline evidence-preservation checks for deterministic source factoring."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import procedural_memory_source_projection as projection


ROOT = Path(__file__).resolve().parents[1]


def note(body, version=1, **extra):
    return {'ref': {'type': 'note', 'id': 'public-note', 'version': version},
            'title': '公开标题', 'body': body, 'body_chars': len(body),
            'body_truncated': False, 'untrusted': True, **extra}


def fixture():
    body = '只复现一个样本。修复未完成，原因未知。\n[正文已截断；共6000字符]'
    initial = note(body, body_chars=6000, body_truncated=True, current_version=1,
                   selected_version_is_current=True)
    preview, saved = note('改动后的公开正文'), note('改动后的公开正文', version=2)
    return [{'episode_id': 'public-episode', 'user_goal': '只改正文中的指定片段',
             'initial_note': initial, 'events': [
                 {'action': 'read', 'arguments': {}, 'result': {'ok': True, 'note': deepcopy(initial),
                  'observation_id': 'observation-read', 'observation_kind': 'fixed_version_read'}},
                 {'action': 'preview', 'arguments': {'replacements': [
                     {'old_text': '修复未完成', 'new_text': '修复仍未完成'}]},
                  'result': {'ok': False, 'error': 'unresolved_replacement',
                  'observation_id': 'observation-error', 'write_observed': False, 'automatic_retries': 0}},
                 {'action': 'preview', 'arguments': {'replacements': []}, 'result': {
                  'ok': True, 'preview': preview, 'observation_id': 'observation-preview',
                  'confirmation_required': True, 'write_observed': False}},
                 {'actor': 'synthetic_test_user', 'message': {'role': 'user',
                  'content': '我确认刚才这份预览，请保存。'},
                  'provenance': 'fixed_environment_control_not_semantic_approval'},
                 {'action': 'confirm', 'arguments': {}, 'result': {'ok': False,
                  'error': 'base_version_conflict', 'observation_id': 'observation-conflict',
                  'update_attempts': 0, 'write_observed': False}},
                 {'action': 'confirm', 'arguments': {}, 'result': {'ok': True, 'note': saved,
                  'observation_id': 'observation-saved', 'write_observed': True}},
             ]}]


def setUpModule():
    """评测夹具是本地撰写的语料（随 /eval 清理被删、从未入 git）；缺失时整组跳过而非报红。"""
    fixture = Path(__file__).resolve().parents[1] / "eval/research/procedural_memory_eval_cases.json"
    if not fixture.exists():
        raise unittest.SkipTest("缺少本地评测夹具 procedural_memory_eval_cases.json（曾随 /eval 清理被删，未入 git）；补齐该文件后自动恢复")


class SourceProjectionTests(unittest.TestCase):
    def test_independent_error_confirmation_and_version_evidence_stays_in_place(self):
        bundle = fixture()
        original = deepcopy(bundle)
        factored = projection.project_public_bundle(bundle)
        process = factored['processes'][0]
        self.assertEqual(process['user_goal'], '只改正文中的指定片段')
        events = process['events']
        for index in (1, 3, 4):
            self.assertEqual(events[index], original[0]['events'][index])
        self.assertEqual([event.get('result', {}).get('observation_id') for event in events],
                         ['observation-read', 'observation-error', 'observation-preview', None,
                          'observation-conflict', 'observation-saved'])
        self.assertEqual(process['initial_note'], events[0]['result']['note'])
        preview_ref = events[2]['result']['preview'][projection.REFERENCE_KEY]['key']
        saved_ref = events[5]['result']['note'][projection.REFERENCE_KEY]['key']
        self.assertNotEqual(preview_ref, saved_ref)
        self.assertEqual(factored['documents'][preview_ref]['ref']['version'], 1)
        self.assertEqual(factored['documents'][saved_ref]['ref']['version'], 2)
        self.assertEqual(factored['documents'][preview_ref]['body'], factored['documents'][saved_ref]['body'])
        self.assertEqual(projection.expand_public_bundle(factored), original)
        self.assertEqual(bundle, original)

    def test_truncation_marker_survives_without_reconstructing_the_hidden_tail(self):
        factored = projection.project_public_bundle(fixture())
        self.assertIn('[正文已截断；共6000字符]', ''.join(factored['texts'].values()))
        initial = projection.expand_public_bundle(factored)[0]['initial_note']
        self.assertTrue(initial['body_truncated'])
        self.assertEqual(initial['body_chars'], 6000)
        self.assertLess(len(initial['body']), 6000)

    def test_deterministic_content_addresses_ignore_dict_insertion_order(self):
        bundle = fixture()
        first = projection.project_public_bundle(bundle)
        self.assertEqual(first, projection.project_public_bundle(deepcopy(bundle)))
        changed_order = deepcopy(bundle)
        changed_order[0]['initial_note'] = dict(reversed(list(bundle[0]['initial_note'].items())))
        second = projection.project_public_bundle(changed_order)
        self.assertEqual(first['processes'][0]['initial_note'], second['processes'][0]['initial_note'])
        self.assertEqual(set(first['documents']), set(second['documents']))

    def test_reference_like_input_is_escaped_and_separate_expansions_do_not_alias(self):
        bundle = fixture()
        bundle[0]['extra'] = [{projection.REFERENCE_KEY: {'kind': 'document', 'key': 'fake'}},
                              {projection.LITERAL_KEY: [['a', 'literal']]},
                              {projection.REFERENCE_KEY: 'plain', 'another': True}]
        factored = projection.project_public_bundle(bundle)
        untouched = deepcopy(factored)
        restored = projection.expand_public_bundle(factored)
        self.assertEqual(restored, bundle)
        restored[0]['initial_note']['title'] = 'changed only one expanded instance'
        self.assertEqual(restored[0]['events'][0]['result']['note']['title'], '公开标题')
        self.assertEqual(factored, untouched)
        self.assertEqual(projection.expand_public_bundle(factored), bundle)

    def test_direct_id_version_note_and_empty_bundle_roundtrip(self):
        bundle = [{'note': {'id': 'direct', 'version': 2, 'body': '正文', 'other': ['原字段']}}]
        self.assertEqual(projection.expand_public_bundle(projection.project_public_bundle(bundle)), bundle)
        self.assertEqual(projection.expand_public_bundle(projection.project_public_bundle([])), [])

    def test_corrupt_tables_missing_references_and_unknown_layout_fail_closed(self):
        factored = projection.project_public_bundle(fixture())
        for field in ('texts', 'documents'):
            broken = deepcopy(factored)
            first_key = next(iter(broken[field]))
            if field == 'texts':
                broken[field][first_key] += '伪造正文'
            else:
                broken[field][first_key]['title'] = '伪造标题'
            with self.assertRaisesRegex(ValueError, 'content_address_mismatch'):
                projection.expand_public_bundle(broken)
        broken = deepcopy(factored)
        broken['documents'].clear()
        with self.assertRaisesRegex(ValueError, 'missing_document_reference'):
            projection.expand_public_bundle(broken)
        broken = deepcopy(factored)
        broken['schema_version'] = True
        with self.assertRaisesRegex(ValueError, 'invalid_source_projection'):
            projection.expand_public_bundle(broken)

    def test_missing_text_cycles_and_unreferenced_corruption_are_not_ignored(self):
        factored = projection.project_public_bundle(fixture())
        missing_text = deepcopy(factored)
        missing_text['texts'].clear()
        with self.assertRaisesRegex(ValueError, 'missing_text_reference'):
            projection.expand_public_bundle(missing_text)
        cycle = deepcopy(factored)
        document_key = next(iter(cycle['documents']))
        cycle['documents'][document_key]['body'] = {
            projection.REFERENCE_KEY: {'kind': 'document', 'key': document_key}}
        with self.assertRaisesRegex(ValueError, 'cyclic_document_reference'):
            projection.expand_public_bundle(cycle)
        unreferenced = deepcopy(factored)
        unreferenced['documents']['sha256:not-an-actual-digest'] = note('tampered unused document')
        with self.assertRaisesRegex(ValueError, 'document_content_address_mismatch'):
            projection.expand_public_bundle(unreferenced)

    def test_stats_are_measured_json_lengths_and_do_not_assert_small_input_savings(self):
        bundle = [{'user_goal': '纯讨论，不修改。', 'events': []}]
        factored = projection.project_public_bundle(bundle)
        measured = projection.projection_stats(bundle, factored)
        raw_text = json.dumps(bundle, ensure_ascii=False, separators=(',', ':'))
        projected_text = json.dumps(factored, ensure_ascii=False, separators=(',', ':'))
        self.assertEqual(measured['original_json_chars'], len(raw_text))
        self.assertEqual(measured['projected_json_utf8_bytes'], len(projected_text.encode('utf-8')))
        self.assertGreater(measured['json_char_ratio'], 1)
        self.assertFalse(measured['provider_tokens_measured'])
        mismatch = deepcopy(bundle)
        mismatch[0]['user_goal'] = '与投影不一致'
        with self.assertRaisesRegex(ValueError, 'projection_does_not_restore_bundle'):
            projection.projection_stats(mismatch, factored)

    def test_host_fields_non_json_and_cycles_are_rejected_instead_of_deleted(self):
        for field in ('readbacks_host_only', 'host_only_role', 'host_fixture', 'fault_schedule'):
            with self.assertRaisesRegex(ValueError, 'host_only_material'):
                projection.project_public_bundle([{'public': '保留', field: 'PRIVATE_SENTINEL'}])
        for value in ({1: 'coerced key'}, {'value': float('nan')}, {'value': ('tuple',)}):
            with self.assertRaises(ValueError):
                projection.project_public_bundle([value])
        cyclic = {}
        cyclic['value'] = cyclic
        with self.assertRaisesRegex(ValueError, 'cyclic_json'):
            projection.project_public_bundle([cyclic])

    def test_prepared_real_domain_public_bundles_roundtrip(self):
        import procedural_memory_eval_domain as domain
        cases = json.loads((ROOT / 'eval/research/procedural_memory_eval_cases.json').read_text(encoding='utf-8'))
        with domain.SourceCorpus(cases) as corpus:
            bundles = corpus.prepare()
            self.assertEqual(len(bundles), 3)
            for bundle in bundles.values():
                factored = projection.project_public_bundle(bundle)
                restored = projection.expand_public_bundle(factored)
                self.assertEqual(restored, bundle)
                # This compares the actual public serialization, including event order.
                self.assertEqual(json.dumps(restored, ensure_ascii=False), json.dumps(bundle, ensure_ascii=False))
                self.assertNotIn('readbacks_host_only', json.dumps(factored))
            local = projection.projection_stats(bundles['source-local'])
            self.assertLess(local['projected_json_utf8_bytes'], local['original_json_utf8_bytes'])
            self.assertFalse(local['provider_tokens_measured'])


if __name__ == '__main__':
    unittest.main()
