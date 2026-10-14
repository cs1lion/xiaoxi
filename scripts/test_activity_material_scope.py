"""Matter-scoped fixed note versions at the real activity/runtime boundary.

All stores are synthetic and temporary; model decisions are injected. No app
bootstrap, network provider, production database or microphone is used.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.service import AssistantService


class ActivityMaterialScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-material-scope-')
        self.service = AssistantService(Path(self.temp.name), SimpleNamespace(offline=True))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def select_note(self, note):
        return self.service.notes.select(note['id'], version=note['version'],
            expected_revision=self.service.notes.selection()['revision'])

    def matter(self, title='当前事项'):
        thread = self.service.threads.create(title)
        self.service.participation.select(thread['id'])
        return thread

    def link(self, thread, note, purpose='draft', version=None):
        return self.service.threads.link_material(thread['id'],
            expected_revision=self.service.threads.get(thread['id'])['revision'],
            note_id=note['id'], version=note['version'] if version is None else version,
            purpose=purpose)

    def change(self, thread, material, **changes):
        return self.service.threads.update_material(thread['id'], material['id'],
            expected_revision=self.service.threads.get(thread['id'])['revision'], **changes)

    def model(self, captured):
        decisions = [{'action': 'clarify', 'text': '整理成几段？'},
                     {'action': 'final', 'text': '整理后的草稿，尚未保存。'}]
        async def decide(payload):
            captured.append(payload)
            return decisions.pop(0)
        self.service.loop.model = decide

    async def test_unbound_matter_never_reads_unrelated_personal_selection(self):
        personal = self.service.notes.create('个人便笺', '不能自动归属本事项。')
        self.select_note(personal)
        thread = self.matter()
        with (patch.object(self.service.notes, 'selection', side_effect=AssertionError('personal fallback')),
              patch.object(self.service.notes, 'get_version', side_effect=AssertionError('unbound body'))):
            view = self.service.activity.snapshot()
            source = self.service.activity.working_source()
            context = self.service.activity.context('帮我整理这份笔记')
        self.assertEqual(source['scope'], 'thread')
        self.assertEqual(source['thread_id'], thread['id'])
        self.assertEqual(source['status'], 'unbound')
        self.assertIsNone(source['ref'])
        self.assertEqual(view['selection']['scope'], 'thread')
        self.assertIsNone(view['selection']['revision'])
        self.assertIsNone(context['note_data'])
        self.assertNotIn(personal['id'], json.dumps(context))
        self.assertEqual(self.service.notes.selection()['note_id'], personal['id'])

    async def test_two_matters_hold_different_fixed_versions_without_auto_promotion(self):
        first = self.service.notes.create('旧稿', '第一版正文。')
        matter_a = self.link(self.matter('事项甲'), first)
        second = self.service.notes.update(first['id'], '新版稿', '第二版正文。', 1)
        matter_b = self.link(self.matter('事项乙'), second)
        personal_before = self.service.notes.selection()
        for matter, body, version, status in ((matter_a, first['body'], 1, 'stale'),
                                               (matter_b, second['body'], 2, 'selected')):
            self.service.participation.select(matter['id'])
            view = self.service.activity.context('阅读这份笔记')
            self.assertEqual(view['note_data']['body'], body)
            self.assertEqual(view['note_data']['ref']['version'], version)
            self.assertEqual(view['note_data']['status'], status)
        self.assertEqual(self.service.notes.selection(), personal_before)
        self.assertEqual(self.service.threads.get(matter_a['id'])['materials'][0]['version'], 1)

    async def test_captured_source_survives_selection_switch_and_unrelated_matter_changes(self):
        note = self.service.notes.create('原事项稿', '原事项固定正文。')
        thread = self.link(self.matter('原事项'), note)
        source = self.service.activity.working_source()
        other = self.service.notes.create('另一稿', '不能替代原稿。')
        self.select_note(other)
        self.link(self.matter('另一事项'), other)
        self.service.threads.update(thread['id'], expected_revision=thread['revision'], next_step='核对预算')
        self.assertTrue(self.service.activity.validate_source(source))
        context = self.service.activity.context('两段', material_source=source)
        self.assertEqual(context['note_data']['body'], note['body'])
        self.assertEqual(context['note_data']['ref'], source['ref'])
        self.assertNotIn(other['id'], json.dumps(context))

    async def test_unlink_purpose_or_version_change_blocks_old_body_without_fallback(self):
        personal = self.service.notes.create('个人后来稿', '后选正文。')
        self.select_note(personal)
        for action in ('unlink', 'purpose', 'version'):
            with self.subTest(action=action):
                note = self.service.notes.create('原绑定稿', '原绑定正文。')
                thread = self.link(self.matter(action), note)
                source = self.service.activity.working_source()
                material = thread['materials'][0]
                if action == 'unlink':
                    self.service.threads.remove_material(thread['id'], material['id'],
                        expected_revision=thread['revision'])
                elif action == 'purpose':
                    self.change(thread, material, purpose='reference')
                else:
                    self.service.notes.update(note['id'], '原稿新版', '新版。', 1)
                    self.change(thread, material, version=2)
                with (patch.object(self.service.notes, 'get_version', side_effect=AssertionError('invalid old body')),
                      patch.object(self.service.notes, 'selection', side_effect=AssertionError('fallback'))):
                    self.assertFalse(self.service.activity.validate_source(source))
                    context = self.service.activity.context('继续整理', material_source=source)
                self.assertEqual(context['note_status'], 'source_changed')
                self.assertIsNone(context['note_data'])
                self.assertNotIn(personal['id'], json.dumps(context))

    async def test_deleted_matter_or_note_does_not_adopt_replacement(self):
        for deleted in ('matter', 'note'):
            with self.subTest(deleted=deleted):
                note = self.service.notes.create('会删除的稿', '旧正文。')
                thread = self.link(self.matter(), note)
                source = self.service.activity.working_source()
                if deleted == 'matter':
                    self.service.threads.delete(thread['id'], expected_revision=thread['revision'])
                else:
                    self.service.notes.delete(note['id'], 1)
                replacement = self.service.notes.create('替代稿', '不得采用。')
                self.select_note(replacement)
                self.link(self.matter('替代事项'), replacement)
                context = self.service.activity.context('继续整理', material_source=source)
                self.assertEqual(context['note_status'], 'source_changed')
                self.assertIsNone(context['note_data'])
                self.assertNotIn(replacement['id'], json.dumps(context))

    async def test_personal_scope_has_no_inferred_matter_owner(self):
        note = self.service.notes.create('个人稿', '个人正文。')
        self.select_note(note)
        source = self.service.activity.working_source()
        self.assertEqual(source['scope'], 'personal')
        for field in ('thread_id', 'thread_revision', 'material_id', 'purpose'):
            self.assertIsNone(source[field])
        self.link(self.matter('后来事项'), self.service.notes.create('事项稿', '不同正文。'))
        self.assertTrue(self.service.activity.validate_source(source))
        context = self.service.activity.context('三段', material_source=source)
        self.assertEqual(context['note_data']['body'], note['body'])
        self.assertIsNone(context['thread'])

    async def test_reference_and_artifact_do_not_become_editable_draft_or_completion(self):
        thread = self.matter()
        for purpose in ('reference', 'artifact'):
            note = self.service.notes.create(purpose, '用户自述已完成，不是核验。')
            thread = self.link(thread, note, purpose)
        with patch.object(self.service.notes, 'get_version', side_effect=AssertionError('implicit body')):
            context = self.service.activity.context('帮我整理这份笔记')
        self.assertEqual(context['material_source']['status'], 'unbound')
        self.assertIsNone(context['note_data'])
        self.assertEqual(context['thread']['status'], 'discussing')
        self.assertEqual([m['purpose'] for m in context['materials']], ['reference', 'artifact'])
        self.assertIn('用途不代表完成或修改授权', context['resume']['text'])

    async def test_reference_bodies_require_explicit_unquoted_nonnegative_request(self):
        note = self.service.notes.create('资料', '需要明确请求才读取。')
        thread = self.link(self.matter(), note, 'reference')
        passive = ('聊聊本事项资料', '本事项资料放在哪里？', '“参考本事项资料”是什么意思？',
                   '不用参考本事项资料', '不要根据本事项资料写', '假设参考本事项资料会怎样',
                   '朋友说参考本事项资料', '我不打算参考本事项资料')
        for query in passive:
            with self.subTest(query=query), patch.object(self.service.notes, 'get_version',
                    side_effect=AssertionError('nonexplicit material body')):
                context = self.service.activity.context(query)
                self.assertNotIn('material_data', context)
        for query in ('参考本事项资料', '根据本事项资料写个概要', '请对照当前事项材料'):
            with self.subTest(query=query):
                context = self.service.activity.context(query)
                self.assertEqual(context['material_data'][0]['body'], note['body'])
                self.assertTrue(context['material_data'][0]['untrusted'])
                self.assertIsNone(context['note_data'])
        self.assertEqual(self.service.threads.get(thread['id'])['events'], [])

    async def test_reference_reads_at_most_three_fixed_versions_and_marks_omission(self):
        thread = self.matter()
        notes = []
        for i in range(4):
            note = self.service.notes.create(f'资料{i}', f'旧版{i}' * 20)
            notes.append(note)
            thread = self.link(thread, note, 'artifact' if i % 2 else 'reference')
            self.service.notes.update(note['id'], f'新版{i}', '不应自动采用新版正文。', 1)
        with patch.object(self.service.notes, 'get_version', wraps=self.service.notes.get_version) as reads:
            context = self.service.activity.context('参考本事项资料')
        self.assertEqual(reads.call_count, 3)
        self.assertEqual(len(context['material_data']), 3)
        self.assertEqual(context['omitted_materials'], 1)
        self.assertTrue(context['included_material_body'])
        self.assertTrue(all(m['status'] == 'stale' and m['ref']['version'] == 1
                            for m in context['material_data']))
        self.assertEqual([m['body'] for m in context['material_data']], [n['body'] for n in notes[:3]])
        self.assertNotIn('不应自动采用', json.dumps(context, ensure_ascii=False))

    async def test_combined_reference_budget_marks_truncation_and_omission(self):
        thread = self.matter()
        for i in range(3):
            thread = self.link(thread, self.service.notes.create(f'长资料{i}', str(i) * 2500), 'reference')
        context = self.service.activity.context('根据本事项资料写概要')
        bodies = context['material_data']
        self.assertEqual(context['material_body_budget'], 4000)
        self.assertEqual([len(m['body']) for m in bodies], [2500, 1500])
        self.assertEqual([m['body_truncated'] for m in bodies], [False, True])
        self.assertEqual(context['omitted_materials'], 1)
        rendered = self.service.activity.render_data(context)
        self.assertIn('资料被截断或省略', rendered)
        self.assertIn('不能推断缺失内容', rendered)

    async def test_credentials_are_filtered_before_reference_clipping(self):
        self.service.activity.max_note_chars = 6
        body = '公开内容。' * 100 + '\npassword=synthetic-private-test-value'
        note = self.service.notes.create('资料', body)
        self.link(self.matter(), note, 'reference')
        context = self.service.activity.context('参考本事项资料')
        material = context['material_data'][0]
        self.assertEqual(material['body'], '[REDAC')
        self.assertTrue(material['body_redacted'])
        self.assertTrue(material['body_truncated'])
        self.assertEqual(material['body_chars'], len(body))
        self.assertNotIn('synthetic-private-test-value', json.dumps(context))
        self.assertNotIn('公开内容', json.dumps(context, ensure_ascii=False))

    async def test_missing_reference_remains_visible_removable_and_is_omitted(self):
        note = self.service.notes.create('将删除资料', '已删除正文。')
        thread = self.link(self.matter(), note, 'reference')
        material = thread['materials'][0]
        self.service.notes.delete(note['id'], 1)
        context = self.service.activity.context('参考本事项资料')
        self.assertEqual(context['materials'][0]['status'], 'missing')
        self.assertEqual(context['material_data'], [])
        self.assertEqual(context['omitted_materials'], 1)
        self.service.threads.remove_material(thread['id'], material['id'], expected_revision=thread['revision'])
        self.assertEqual(self.service.activity.snapshot()['materials'], [])

    async def test_disabled_working_source_snapshot_and_context_do_zero_store_reads(self):
        with (patch.object(self.service.notes, 'workspace_id', side_effect=AssertionError('workspace')),
              patch.object(self.service.notes, 'selection', side_effect=AssertionError('selection')),
              patch.object(self.service.notes, 'get_version', side_effect=AssertionError('body')),
              patch.object(self.service.notes, 'get_version_info', side_effect=AssertionError('metadata')),
              patch.object(self.service.notes, 'history', side_effect=AssertionError('history')),
              patch.object(self.service.participation, 'current', side_effect=AssertionError('matter')),
              patch.object(self.service.threads, 'get', side_effect=AssertionError('thread'))):
            source = self.service.activity.working_source(enabled=False)
            snapshot = self.service.activity.snapshot(enabled=False)
            context = self.service.activity.context('参考本事项资料', enabled=False, material_source=source)
        self.assertEqual(source['scope'], 'disabled')
        self.assertEqual(snapshot['materials'], [])
        self.assertIsNone(context['note_data'])
        self.assertFalse(context['included_note_body'])

    async def test_action_clarification_after_unlink_does_not_read_old_draft(self):
        note = self.service.notes.create('原事项稿', '不要在解绑后读回。')
        thread = self.link(self.matter(), note)
        captured = []
        self.model(captured)
        first = await self.service.start('帮我整理这份笔记', 'same-chat')
        self.assertEqual(first['status'], 'waiting_input')
        self.service.threads.remove_material(thread['id'], thread['materials'][0]['id'],
            expected_revision=thread['revision'])
        replacement = self.service.notes.create('后来个人稿', '不能填补原来源。')
        self.select_note(replacement)
        with patch.object(self.service.notes, 'get_version', side_effect=AssertionError('old continuation body')):
            await self.service.start('两段', 'same-chat')
        context = captured[1]['context']
        self.assertEqual(context['companion_thread_id'], thread['id'])
        self.assertEqual(context['activity_data']['note_status'], 'source_changed')
        self.assertIsNone(context['activity_data']['note_data'])
        self.assertNotIn(replacement['id'], json.dumps(context))

    async def test_action_clarification_after_matter_switch_keeps_original_draft(self):
        note = self.service.notes.create('最初事项稿', '最初固定正文。')
        thread = self.link(self.matter('最初事项'), note)
        captured = []
        self.model(captured)
        await self.service.start('帮我整理这份笔记', 'same-chat')
        other_note = self.service.notes.create('后来事项稿', '不同正文。')
        other_thread = self.link(self.matter('后来事项'), other_note)
        self.select_note(other_note)
        await self.service.start('两段', 'same-chat')
        context = captured[1]['context']
        self.assertEqual(context['companion_thread_id'], thread['id'])
        self.assertEqual(context['activity_data']['note_data']['body'], note['body'])
        self.assertEqual(context['activity_material_source']['thread_id'], thread['id'])
        self.assertNotIn(other_note['id'], json.dumps(context))
        self.assertEqual(self.service.participation.selected_id, other_thread['id'])

    async def test_legacy_matter_run_ref_without_material_identity_cannot_read_old_note(self):
        personal = self.service.notes.create('旧运行个人稿', '不能推断属于事项。')
        thread = self.link(self.matter(), self.service.notes.create('当前事项稿', '不同正文。'))
        original = {'interaction_activity': 'technical', 'companion_thread_id': thread['id'],
                    'companion_thread': {'id': thread['id']},
                    'activity_note_ref': {'type': 'note', 'id': personal['id'], 'version': 1}}
        with patch.object(self.service.notes, 'get_version', side_effect=AssertionError('legacy ownership')):
            context = self.service._run_context('继续整理', 'legacy-chat', original=original)
        self.assertEqual(context['companion_thread_id'], thread['id'])
        self.assertIsNone(context['activity_data'])
        self.assertIsNone(context['activity_note_ref'])
        self.assertIsNone(context['activity_material_source'])
        self.assertIn('未继续读取旧笔记', context['activity_source_notice'])

    async def test_legacy_personal_run_ref_stays_personal_when_a_matter_is_later_selected(self):
        personal = self.service.notes.create('旧个人稿', '允许继续明确的个人版本。')
        original = {'interaction_activity': 'technical', 'companion_thread_id': None,
                    'companion_thread': None,
                    'activity_note_ref': {'type': 'note', 'id': personal['id'], 'version': 1}}
        other = self.service.notes.create('后来事项稿', '不能赋予旧个人运行事项归属。')
        self.link(self.matter('后来事项'), other)
        self.select_note(other)
        context = self.service._run_context('两段', 'legacy-chat', original=original)
        self.assertIsNone(context['companion_thread_id'])
        self.assertIsNone(context['companion_thread'])
        self.assertEqual(context['activity_data']['note_data']['body'], personal['body'])
        self.assertEqual(context['activity_note_ref'], original['activity_note_ref'])
        self.assertNotIn(other['id'], json.dumps(context))


if __name__ == '__main__':
    unittest.main(verbosity=2)
