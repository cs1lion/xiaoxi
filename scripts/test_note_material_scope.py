"""Captured draft sources with temporary notes and injected model adapters only."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.note_conversation import NoteConversation
from server.assistant.note_proposals import NoteProposalConflict, NoteProposalService
from server.assistant.notes import NoteStore
from server.interaction.activity import ActivityFacade
from server.interaction.participation import Participation
from server.interaction.threads import ThreadStore


class NoteMaterialScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='aqi-note-material-scope-')
        self.addCleanup(temp.cleanup)
        self.notes = NoteStore(Path(temp.name) / 'notes.db')
        self.personal = self.notes.create('个人稿 A', '个人稿只读自身。')
        self.draft = self.notes.create('事项稿 B', '开头：说明目标。\n结尾：补充对比。')
        self.notes.select(self.personal['id'], version=1, expected_revision=1)
        self.participation = SimpleNamespace(selected_id='thread-B')
        self.source = self.thread_source(self.draft)
        self.materials = {'material-B': deepcopy(self.source)}
        self.provider_calls = 0
        self.validation_calls = []
        self.model_calls = []
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '补充对比', 'new_text': '展示前后对比'}]}
        self.proposals = NoteProposalService(self.notes, source_validator=self.validate)
        self.conversation = NoteConversation(self.notes, self.proposals, self.participation,
            self.resolve, source_provider=self.provide, source_validator=self.validate)

    @staticmethod
    def thread_source(note, *, thread='thread-B', material='material-B'):
        return {'scope': 'thread', 'thread_id': thread, 'thread_revision': 3,
                'material_id': material, 'ref': {'type': 'note', 'id': note['id'], 'version': note['version']},
                'purpose': 'draft', 'status': 'selected'}

    def personal_source(self, note):
        return {'scope': 'personal', 'thread_id': None, 'thread_revision': None, 'material_id': None,
                'ref': {'type': 'note', 'id': note['id'], 'version': note['version']},
                'purpose': None, 'status': 'selected'}

    def provide(self, *, enabled=True):
        self.assertTrue(enabled)
        self.provider_calls += 1
        return self.source  # The implementation must detach this shared object.

    def validate(self, source):
        self.validation_calls.append(deepcopy(source))
        if source['scope'] == 'personal':
            return True
        live = self.materials.get(source['material_id'])
        return bool(live and all(live[key] == source[key]
                    for key in ('thread_id', 'material_id', 'ref', 'purpose')))

    async def resolve(self, payload):
        self.model_calls.append(deepcopy(payload))
        return self.answer

    async def edit(self, text='修改这份笔记，把结尾改清楚', *, session='scope-session', enabled=True):
        return await self.conversation.handle(text, session, enabled=enabled)

    def propose(self, **kwargs):
        args = {'note_id': self.draft['id'], 'version': 1, 'title': self.draft['title'],
                'body': '开头：说明目标。\n结尾：展示前后对比。', 'source_text': '修改这份笔记，把结尾改清楚',
                'source_context': self.source}
        args.update(kwargs)
        return self.proposals.propose(**args)

    def wire_real_activity(self):
        threads = ThreadStore(Path(self.notes.path).parent / 'threads.db')
        self.addCleanup(threads.close)
        participation = Participation(threads)
        activity = ActivityFacade(self.notes, participation)
        self.proposals = NoteProposalService(self.notes, source_validator=activity.validate_source,
                                            source_guard=activity.source_guard)
        self.conversation = NoteConversation(self.notes, self.proposals, participation, self.resolve,
            source_provider=activity.working_source, source_validator=activity.validate_source)
        thread = threads.create('真实接线事项')
        participation.select(thread['id'])
        return threads, participation, activity, thread

    async def test_thread_without_draft_never_reads_global_body_or_calls_model(self):
        for purpose in (None, 'reference', 'artifact'):
            with self.subTest(existing_material_purpose=purpose):
                self.source = {'scope': 'thread', 'thread_id': 'thread-B', 'thread_revision': 3,
                               'material_id': None, 'ref': None, 'purpose': None, 'status': 'unbound'}
                self.materials = {} if purpose is None else {'other-material': {'purpose': purpose}}
                with (patch.object(self.notes, 'selection', side_effect=AssertionError('global selection')),
                     patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
                     patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
                    result = await self.edit()
                self.assertNotIn('note_proposal', result)
                self.assertIn('当前事项还没有关联草稿', result['text'])
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.validation_calls, [])

    async def test_real_activity_without_draft_never_reads_personal_body(self):
        threads, _, _, thread = self.wire_real_activity()
        for purpose in ('reference', 'artifact'):
            with self.subTest(purpose=purpose):
                current = threads.get(thread['id'])
                linked = threads.link_material(thread['id'], expected_revision=current['revision'],
                    note_id=self.draft['id'], version=1, purpose=purpose)
                with (patch.object(self.notes, 'selection', side_effect=AssertionError('global selection')),
                      patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
                      patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
                    result = await self.edit()
                self.assertIn('当前事项还没有关联草稿', result['text'])
                material = linked['materials'][0]
                threads.remove_material(thread['id'], material['id'], expected_revision=linked['revision'])
        self.assertEqual(self.model_calls, [])

    async def test_real_activity_capture_survives_ui_switch_and_stops_after_unlink(self):
        threads, participation, activity, thread = self.wire_real_activity()
        linked = threads.link_material(thread['id'], expected_revision=thread['revision'],
            note_id=self.draft['id'], version=1, purpose='draft')
        original = activity.working_source()
        other = threads.create('另一事项')
        async def resolve(payload):
            self.model_calls.append(deepcopy(payload))
            participation.select(other['id'])
            return self.answer
        self.conversation.resolver = resolve
        candidate = (await self.edit())['note_proposal']
        self.assertEqual(candidate['source_context'], original)
        self.assertEqual(self.proposals.get(candidate['id'])['note_id'], self.draft['id'])
        material = linked['materials'][0]
        threads.remove_material(thread['id'], material['id'], expected_revision=linked['revision'])
        with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
              patch.object(self.notes, 'get_version', side_effect=AssertionError('body read')),
              patch.object(self.notes, 'update', side_effect=AssertionError('note write'))):
            with self.assertRaisesRegex(NoteProposalConflict, '^note source changed$'):
                self.proposals.confirm(candidate['id'], 1)
        self.assertEqual(self.notes.get(self.draft['id']), self.draft)
        self.assertTrue(self.proposals.reject(candidate['id'], 1)['rejected'])

    async def test_legacy_conversation_without_provider_stays_personal(self):
        conversation = NoteConversation(self.notes, self.proposals, self.participation, self.resolve)
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '个人稿只读自身', 'new_text': '个人稿已有修改建议'}]}
        candidate = (await conversation.handle('修改这份笔记', 'legacy'))['note_proposal']
        self.assertEqual(candidate['note_id'], self.personal['id'])
        self.assertIsNone(candidate['thread_id'])
        self.assertIsNone(candidate['source_context'])
        self.assertEqual(self.provider_calls, 0)
        self.assertEqual(self.validation_calls, [])

    async def test_thread_uses_its_draft_without_reading_personal_selection(self):
        with patch.object(self.notes, 'selection', side_effect=AssertionError('global selection')):
            candidate = (await self.edit())['note_proposal']
        self.assertEqual(candidate['note_id'], self.draft['id'])
        self.assertEqual(candidate['original_body'], self.draft['body'])
        self.assertEqual(self.model_calls[0]['note']['ref'], self.source['ref'])
        self.assertNotIn(self.personal['body'], str(self.model_calls))
        self.assertEqual(candidate['source_context'], self.source)

    async def test_personal_scope_preserves_global_note_flow(self):
        self.source = self.personal_source(self.personal)
        self.answer = {'action': 'edit', 'replacements': [
            {'old_text': '个人稿只读自身', 'new_text': '个人稿已有修改建议'}]}
        candidate = (await self.edit())['note_proposal']
        self.assertEqual(candidate['note_id'], self.personal['id'])
        self.assertEqual(candidate['scope'], 'personal')
        self.assertIsNone(candidate['thread_id'])
        self.assertIsNone(candidate['material_id'])

    async def test_routing_does_not_capture_source_or_read_global_selection(self):
        with patch.object(self.notes, 'selection', side_effect=AssertionError('global selection')):
            self.assertTrue(self.conversation.should_route('结尾改具体一点', 'new'))
            self.assertTrue(self.conversation.should_route('修改这份笔记', 'new'))
            self.assertEqual(self.provider_calls, 0)
            await self.edit('结尾改具体一点')
        self.assertEqual(self.provider_calls, 1)

    async def test_selection_change_while_awaiting_keeps_original_source(self):
        started, resume = asyncio.Event(), asyncio.Event()
        original = deepcopy(self.source)
        async def resolve(payload):
            started.set()
            await resume.wait()
            return self.answer
        self.conversation.resolver = resolve
        task = asyncio.create_task(self.edit())
        await started.wait()
        other = self.thread_source(self.personal, thread='thread-A', material='material-A')
        self.materials['material-A'] = deepcopy(other)
        self.source = other
        self.participation.selected_id = 'thread-A'
        self.notes.select(self.draft['id'], version=1, expected_revision=2)
        resume.set()
        candidate = (await task)['note_proposal']
        self.assertEqual(candidate['source_context'], original)
        self.assertEqual(candidate['thread_id'], 'thread-B')
        self.assertEqual(candidate['note_id'], self.draft['id'])
        self.assertEqual(self.provider_calls, 1)
        self.assertTrue(all(source == original for source in self.validation_calls))

    async def test_clarification_keeps_original_source_across_selection_change(self):
        original = deepcopy(self.source)
        self.answer = {'action': 'clarify'}
        await self.edit()
        self.source = self.thread_source(self.personal, thread='thread-A', material='material-A')
        self.materials['material-A'] = deepcopy(self.source)
        self.answer = {'action': 'edit', 'replacements': [{'old_text': '补充对比', 'new_text': '展示对比'}]}
        candidate = (await self.edit('把最后的对比写具体'))['note_proposal']
        self.assertEqual(candidate['source_context'], original)
        self.assertEqual(candidate['note_id'], self.draft['id'])
        self.assertEqual(self.provider_calls, 1)
        self.assertNotIn('scope-session', str(candidate))

    async def test_revoked_clarification_stops_before_body_and_model(self):
        self.answer = {'action': 'clarify'}
        await self.edit()
        self.materials.clear()
        with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
             patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
            result = await self.edit('把对比改得更短')
        self.assertNotIn('note_proposal', result)
        self.assertIn('草稿关联已变化', result['text'])
        self.assertIsNone(self.conversation._state('scope-session'))
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(self.provider_calls, 1)

    async def test_revoked_source_after_await_cannot_create_candidate_or_clarification(self):
        for action in ('edit', 'clarify', 'ignore'):
            with self.subTest(action=action):
                self.materials['material-B'] = deepcopy(self.source)
                async def resolve(payload):
                    self.materials.clear()
                    return self.answer if action == 'edit' else {'action': action}
                self.conversation.resolver = resolve
                result = await self.edit()
                self.assertNotIn('note_proposal', result)
                self.assertIn('草稿关联已变化', result['text'])
                self.assertIsNone(self.conversation._state('scope-session'))
        self.assertEqual(self.proposals._pending, {})
        self.assertEqual(self.notes.get(self.draft['id']), self.draft)

    async def test_noneditable_and_unavailable_sources_are_read_free(self):
        variants = [dict(self.source, purpose='reference'), dict(self.source, purpose='artifact'),
                    dict(self.source, status='missing'), dict(self.source, status='stale'),
                    dict(self.source, scope='disabled', status='disabled'),
                    dict(self.source, material_id=None), dict(self.source, thread_revision=True)]
        for source in variants:
            with self.subTest(source=source):
                self.source = source
                with (patch.object(self.notes, 'selection', side_effect=AssertionError('global selection')),
                     patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
                     patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
                    self.assertNotIn('note_proposal', await self.edit())
        self.assertEqual(self.model_calls, [])

    async def test_disabled_memory_calls_no_provider_validator_note_or_model(self):
        with (patch.object(self.notes, 'selection', side_effect=AssertionError('selection read')),
             patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
             patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
            self.assertTrue(self.conversation.should_route('结尾改清楚', enabled=False))
            result = await self.edit(enabled=False)
        self.assertNotIn('note_proposal', result)
        self.assertEqual(self.provider_calls, 0)
        self.assertEqual(self.validation_calls, [])
        self.assertEqual(self.model_calls, [])

    async def test_provider_exception_fails_closed_before_note_or_model(self):
        def fail(**kwargs):
            raise RuntimeError('synthetic source failure')
        self.conversation.source_provider = fail
        with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
             patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
            self.assertNotIn('note_proposal', await self.edit())
        self.assertEqual(self.model_calls, [])

    async def test_conversation_validator_requires_true_and_fails_closed_on_errors(self):
        for result in (False, None, 1, 'true', {}, {'valid': True}, RuntimeError('synthetic')):
            with self.subTest(result=result):
                def validate(source):
                    if isinstance(result, Exception):
                        raise result
                    return result
                self.conversation.source_validator = validate
                with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
                     patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
                    self.assertNotIn('note_proposal', await self.edit())
        self.assertEqual(self.model_calls, [])

    async def test_resolver_and_validator_cannot_mutate_authoritative_ref(self):
        original = deepcopy(self.source)
        def validate(source):
            self.assertEqual(source, original)
            source['ref']['id'] = self.personal['id']
            source['thread_id'] = 'forged'
            return True
        async def resolve(payload):
            payload['note']['ref']['id'] = self.personal['id']
            payload['note']['ref']['version'] = 99
            return self.answer
        self.conversation.source_validator = validate
        self.proposals._source_validator = validate
        self.conversation.resolver = resolve
        candidate = (await self.edit())['note_proposal']
        self.assertEqual(candidate['source_context'], original)
        self.assertEqual(candidate['note_id'], self.draft['id'])
        self.assertEqual(self.source, original)

    async def test_cancelled_resolution_still_creates_no_candidate(self):
        started, resume = asyncio.Event(), asyncio.Event()
        async def resolve(payload):
            started.set()
            await resume.wait()
            return self.answer
        self.conversation.resolver = resolve
        task = asyncio.create_task(self.edit())
        await started.wait()
        self.conversation.abandon('scope-session')
        resume.set()
        self.assertEqual((await task)['status'], 'superseded')
        self.assertEqual(self.proposals._pending, {})

    def test_revoked_removed_replaced_or_reclassified_candidate_is_read_write_blocked(self):
        for change in ('removed', 'replaced', 'reference', 'artifact'):
            with self.subTest(change=change):
                self.materials['material-B'] = deepcopy(self.source)
                candidate = self.propose()
                if change == 'removed':
                    self.materials.clear()
                elif change == 'replaced':
                    self.materials['material-B']['ref'] = self.personal_source(self.personal)['ref']
                else:
                    self.materials['material-B']['purpose'] = change
                with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
                     patch.object(self.notes, 'get_version', side_effect=AssertionError('body read')),
                     patch.object(self.notes, 'update', side_effect=AssertionError('note write'))):
                    actions = [lambda: self.proposals.get(candidate['id']),
                               lambda: self.proposals.list_pending(self.draft['id']),
                               lambda: self.proposals.revise(candidate['id'], 1, '新标题', '新正文'),
                               lambda: self.proposals.confirm(candidate['id'], 1)]
                    for action in actions:
                        with self.assertRaisesRegex(NoteProposalConflict, '^note source changed$'):
                            action()
                    rejected = self.proposals.reject(candidate['id'], 1)
                    self.assertTrue(rejected['rejected'])
        self.assertEqual(self.notes.get(self.draft['id']), self.draft)

    def test_invalid_source_validator_rejects_proposal_before_body_reads(self):
        for result in (False, None, 1, 'yes', {}, RuntimeError('synthetic')):
            with self.subTest(result=result):
                def validate(source):
                    if isinstance(result, Exception):
                        raise result
                    return result
                self.proposals._source_validator = validate
                with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
                     patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
                    with self.assertRaisesRegex(NoteProposalConflict, '^note source changed$'):
                        self.propose()
        self.assertEqual(self.proposals._pending, {})

    def test_newer_thread_revision_or_ui_selection_does_not_revoke_original(self):
        candidate = self.propose()
        self.materials['material-B']['thread_revision'] = 99
        self.source = self.personal_source(self.personal)
        self.participation.selected_id = None
        self.assertEqual(self.proposals.get(candidate['id'])['source_context']['thread_revision'], 3)
        saved = self.proposals.confirm(candidate['id'], 1)
        self.assertEqual(saved['id'], self.draft['id'])
        self.assertEqual(saved['version'], 2)

    def test_source_context_is_detached_on_propose_get_list_and_revise(self):
        original = deepcopy(self.source)
        candidate = self.propose()
        self.source['ref']['id'] = self.personal['id']
        candidate['source_context']['ref']['id'] = 'forged'
        token = candidate['id']
        self.assertEqual(self.proposals.get(token)['source_context'], original)
        returned = self.proposals.get(token)
        returned['source_context']['purpose'] = 'reference'
        returned = self.proposals.list_pending(self.draft['id'])
        returned[0]['source_context']['ref']['version'] = 99
        revised = self.proposals.revise(token, 1, '修订标题', '修订正文')
        revised['source_context']['thread_id'] = 'forged'
        self.assertEqual(self.proposals.get(token)['source_context'], original)

    def test_confirmation_receipt_uses_original_source_and_saved_real_version(self):
        selection, materials = self.notes.selection(), deepcopy(self.materials)
        receipt = []
        original = deepcopy(self.source)
        def on_confirm(candidate, saved):
            receipt.append((deepcopy(candidate['source_context']), dict(saved)))
            candidate['source_context']['ref']['id'] = 'forged'
            saved['body'] = 'forged'
            return []
        self.proposals._on_confirm = on_confirm
        candidate = self.propose()
        self.source = self.personal_source(self.personal)
        saved = self.proposals.confirm(candidate['id'], 1)
        self.assertEqual(receipt[0][0], original)
        self.assertEqual(receipt[0][1]['version'], 2)
        self.assertEqual(saved['body'], candidate['body'])
        self.assertEqual(self.notes.selection(), selection)
        self.assertEqual(self.materials, materials)
        self.assertEqual(self.materials['material-B']['ref']['version'], 1)
        self.assertEqual(self.notes.get_version(self.draft['id'], 1), self.draft)

    def test_saved_receipt_failure_retains_success_and_does_not_repeat_write(self):
        def fail(candidate, saved):
            raise RuntimeError('synthetic receipt failure')
        self.proposals._on_confirm = fail
        candidate = self.propose()
        with patch.object(self.notes, 'update', wraps=self.notes.update) as update:
            saved = self.proposals.confirm(candidate['id'], 1)
            self.assertEqual(update.call_count, 1)
            with self.assertRaises(KeyError):
                self.proposals.confirm(candidate['id'], 1)
        self.assertEqual(saved['version'], 2)
        self.assertIn('已保存', saved['companion_warnings'][0])

    def test_confirmation_guard_covers_note_check_write_and_releases_before_receipt(self):
        candidate = self.propose()
        original = deepcopy(candidate['source_context'])
        guarded, order = False, []
        @contextmanager
        def guard(source):
            nonlocal guarded
            self.assertEqual(source, original)
            source['ref']['id'] = 'forged'
            guarded = True
            order.append('guard-enter')
            try:
                yield
            finally:
                guarded = False
                order.append('guard-exit')
        def fail_nested_validator(source):
            raise AssertionError('nested source validation')
        get_note, update_note = self.notes.get, self.notes.update
        def checked_get(note_id):
            self.assertTrue(guarded)
            order.append('base-check')
            return get_note(note_id)
        def checked_update(*args):
            self.assertTrue(guarded)
            order.append('note-write')
            return update_note(*args)
        def receipt(proposal, saved):
            self.assertFalse(guarded)
            self.assertEqual(proposal['source_context'], original)
            self.assertEqual(saved['version'], 2)
            order.append('receipt')
        self.proposals._source_validator = fail_nested_validator
        self.proposals._source_guard = guard
        self.proposals._on_confirm = receipt
        with (patch.object(self.notes, 'get', side_effect=checked_get),
              patch.object(self.notes, 'update', side_effect=checked_update)):
            saved = self.proposals.confirm(candidate['id'], 1)
        self.assertEqual(saved['version'], 2)
        self.assertEqual(order, ['guard-enter', 'base-check', 'note-write', 'guard-exit', 'receipt'])
        self.assertEqual(candidate['source_context'], original)

    def test_confirmation_guard_failure_is_conflict_before_note_read_or_write(self):
        for error in (ValueError('draft_source_changed'), RuntimeError('synthetic guard failure')):
            with self.subTest(error=error):
                candidate = self.propose()
                @contextmanager
                def guard(source):
                    raise error
                    yield  # Mark this deliberately failing adapter as a contextmanager.
                self.proposals._source_guard = guard
                with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
                      patch.object(self.notes, 'update', side_effect=AssertionError('note write'))):
                    with self.assertRaisesRegex(NoteProposalConflict, '^note source changed$'):
                        self.proposals.confirm(candidate['id'], 1)
                self.assertTrue(self.proposals.reject(candidate['id'], 1)['rejected'])
        self.assertEqual(self.notes.get(self.draft['id']), self.draft)

    async def test_real_guard_serializes_other_connection_unlink_until_note_save_finishes(self):
        threads, _, _, thread = self.wire_real_activity()
        linked = threads.link_material(thread['id'], expected_revision=thread['revision'],
            note_id=self.draft['id'], version=1, purpose='draft')
        candidate = (await self.edit())['note_proposal']
        material = linked['materials'][0]
        other = ThreadStore(Path(self.notes.path).parent / 'threads.db')
        self.addCleanup(other.close)
        update_entered, release_update = threading.Event(), threading.Event()
        unlink_started, unlink_done = threading.Event(), threading.Event()
        # Signal the second connection's actual SQLite lock attempt, so the
        # assertion cannot pass merely because its worker has not run yet.
        other._db.set_trace_callback(lambda statement: unlink_started.set()
                                    if statement.upper() == 'BEGIN IMMEDIATE' else None)
        saved_results, errors = [], []
        update_note = self.notes.update
        def hold_update(*args):
            update_entered.set()
            if not release_update.wait(4):
                raise AssertionError('confirmation release timed out')
            return update_note(*args)
        def save():
            try:
                saved_results.append(self.proposals.confirm(candidate['id'], 1))
            except Exception as error:
                errors.append(error)
        def unlink():
            try:
                other.remove_material(thread['id'], material['id'], expected_revision=linked['revision'])
            except Exception as error:
                errors.append(error)
            finally:
                unlink_done.set()
        saving, unlinking = threading.Thread(target=save), threading.Thread(target=unlink)
        with patch.object(self.notes, 'update', side_effect=hold_update) as update:
            saving.start()
            try:
                self.assertTrue(update_entered.wait(3))
                unlinking.start()
                self.assertTrue(unlink_started.wait(3))
                self.assertFalse(unlink_done.wait(0.15), 'unlink bypassed guarded save')
            finally:
                release_update.set()
                saving.join(timeout=4)
                if unlinking.ident is not None:
                    unlinking.join(timeout=4)
            self.assertEqual(update.call_count, 1)
        self.assertFalse(saving.is_alive())
        self.assertFalse(unlinking.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(saved_results), 1)
        self.assertEqual(saved_results[0]['version'], 2)
        self.assertEqual(self.notes.get(self.draft['id'])['body'], candidate['body'])
        self.assertEqual(other.get(thread['id'])['materials'], [])
        self.assertEqual(self.notes.get_version(self.draft['id'], 1), self.draft)

    async def test_real_guard_other_connection_unlinks_first_and_confirmation_reads_writes_nothing(self):
        threads, _, _, thread = self.wire_real_activity()
        linked = threads.link_material(thread['id'], expected_revision=thread['revision'],
            note_id=self.draft['id'], version=1, purpose='draft')
        candidate = (await self.edit())['note_proposal']
        other = ThreadStore(Path(self.notes.path).parent / 'threads.db')
        self.addCleanup(other.close)
        other.remove_material(thread['id'], linked['materials'][0]['id'], expected_revision=linked['revision'])
        with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
              patch.object(self.notes, 'get_version', side_effect=AssertionError('body read')),
              patch.object(self.notes, 'update', side_effect=AssertionError('note write'))):
            with self.assertRaisesRegex(NoteProposalConflict, '^note source changed$'):
                self.proposals.confirm(candidate['id'], 1)
        self.assertEqual(self.notes.get(self.draft['id']), self.draft)
        self.assertTrue(self.proposals.reject(candidate['id'], 1)['rejected'])

    async def test_real_guard_releases_registry_transaction_before_receipt(self):
        threads, _, _, thread = self.wire_real_activity()
        threads.link_material(thread['id'], expected_revision=thread['revision'],
            note_id=self.draft['id'], version=1, purpose='draft')
        candidate = (await self.edit())['note_proposal']
        receipts = []
        def receipt(proposal, saved):
            self.assertFalse(threads._db.in_transaction)
            current = threads.get(thread['id'])
            receipts.append(threads.add_event(thread['id'], expected_revision=current['revision'],
                kind='tool_receipt', text='已保存版本2', source='tool_verified', source_id=proposal['id'],
                object_refs=[{'type': 'note', 'id': saved['id'], 'version': saved['version']}]))
        self.proposals._on_confirm = receipt
        saved = self.proposals.confirm(candidate['id'], 1)
        self.assertEqual(saved['version'], 2)
        self.assertNotIn('companion_warnings', saved)
        self.assertEqual(len(receipts), 1)
        current = threads.get(thread['id'])
        self.assertEqual(current['materials'][0]['version'], 1)
        self.assertEqual(current['events'][0]['object_refs'],
                         [{'type': 'note', 'id': self.draft['id'], 'version': 2}])

    def test_note_base_cas_conflicts_independently_of_valid_source(self):
        candidate = self.propose()
        changed = self.notes.update(self.draft['id'], self.draft['title'], '其他已保存正文。', 1)
        self.assertEqual(self.proposals.get(candidate['id'])['base_note_version'], 1)
        for action in (lambda: self.proposals.revise(candidate['id'], 1, '改标题', '改正文'),
                       lambda: self.proposals.confirm(candidate['id'], 1)):
            with self.assertRaisesRegex(NoteProposalConflict, '^note base version conflict$'):
                action()
        self.assertEqual(self.notes.get(self.draft['id']), changed)
        self.assertTrue(self.proposals.reject(candidate['id'], 1)['rejected'])

    def test_scoped_scalar_compatibility_and_malformed_binding_shapes(self):
        candidate = self.propose(source_context=None, scope='thread', thread_id='thread-B',
                                 thread_revision=3, material_id='material-B', purpose='draft')
        self.assertEqual(candidate['source_context'], self.source)
        invalid = [dict(source_context=None, material_id='material-B'),
                   dict(source_context=None, thread_revision=3),
                   dict(source_context=None, scope='thread', thread_id='thread-B'),
                   dict(source_context=None, scope='personal', thread_id='thread-B'),
                   dict(source_context=None, scope='disabled'),
                   dict(thread_id='thread-A'), dict(scope='personal'), dict(material_id='other'),
                   dict(source_context=dict(self.source, status='stale')),
                   dict(source_context=dict(self.source, thread_revision=True)),
                   dict(source_context=dict(self.source, session_id='private')),
                   dict(source_context=dict(self.source, ref={'type': 'note', 'id': self.personal['id'], 'version': 1})),
                   dict(source_context=dict(self.source, ref={**self.source['ref'], 'session': 'private'}))]
        with (patch.object(self.notes, 'get', side_effect=AssertionError('body read')),
             patch.object(self.notes, 'get_version', side_effect=AssertionError('body read'))):
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.propose(**kwargs)

    def test_constructor_rejects_noncallable_boundaries(self):
        for value in (False, 1, {}, 'callable'):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    NoteProposalService(self.notes, source_validator=value)
                with self.assertRaises(TypeError):
                    NoteProposalService(self.notes, source_guard=value)
                with self.assertRaises(TypeError):
                    NoteConversation(self.notes, self.proposals, self.participation, self.resolve,
                                     source_provider=value)
                with self.assertRaises(TypeError):
                    NoteConversation(self.notes, self.proposals, self.participation, self.resolve,
                                     source_validator=value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
