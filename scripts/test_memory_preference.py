"""Offline preference contracts through real extraction, writes and retrieval.

Only provider JSON is synthetic. Saved user messages, evidence validation,
manager decisions, SQLite storage, retrieval and reopen are real. Import-time
singletons use RAM; every test uses its own temporary database. No expected
memories are seeded through remember/upsert, and no live provider is called.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
with patch('dotenv.load_dotenv', return_value=False):
    from server.memory.manager import MemoryManager
    from server.memory.store import MemoryStore


def candidate(value, *, key='饮食:偏好', source='stated', operation='add', **extra):
    return {'kind': 'fact', 'subject': 'user', 'key': key, 'value': value,
            'source': source, 'importance': .8, 'operation': operation, **extra}


class PreferencePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        self.network_guard = patch.object(
            httpx.AsyncHTTPTransport, 'handle_async_request',
            side_effect=AssertionError('real network is forbidden'))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.default_provider_guard = patch(
            'server.llm.llm.complete_raw',
            side_effect=AssertionError('default provider is forbidden'))
        self.default_provider_guard.start()
        self.addCleanup(self.default_provider_guard.stop)
        self.directory = tempfile.TemporaryDirectory(prefix='neurosama-preference-test-')
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / 'synthetic.sqlite'
        self.store = MemoryStore(str(self.database))
        self.manager = MemoryManager(self.store)
        self.manager.cfg = copy.deepcopy(self.manager.cfg)
        self.manager.retriever.cfg = dict(self.manager.retriever.cfg)
        self.manager.retriever.cfg['max_inject_chars'] = 4000
        self.session = self.store.start_session('synthetic-preference')

    async def asyncTearDown(self):
        # Every case checks actual persistence, including conflicts/history.
        before = sorted((row.to_dict() for row in self.rows()), key=lambda row: row['id'])
        self.store.close()
        reopened = MemoryStore(str(self.database))
        try:
            after = sorted((row.to_dict() for row in reopened.list_memories(
                status=None, include_history=True, limit=5000)), key=lambda row: row['id'])
            self.assertEqual(before, after)
        finally:
            reopened.close()

    def rows(self, *, active=False):
        return self.store.list_memories(
            status='active' if active else None, include_history=not active, limit=5000)

    def active_targets(self):
        return {row.preference_target for row in self.rows(active=True)}

    def assert_preference(self, row, target, level, scope):
        self.assertEqual((row.preference_target, row.preference_level, row.preference_scope),
                         (target, level, scope))
        self.assertEqual(json.loads(row.identity_slot), [target, scope])
        message = self.store.get_message(row.evidence_message_id)
        self.assertEqual(row.preference_as_of, message['created_at'])
        self.assertEqual(row.evidence_state, 'linked')

    def message(self, text):
        return self.store.add_message(self.session, 'user', text)

    def response(self, text, items, identifier):
        payload = copy.deepcopy(items)
        for item in payload:
            item.setdefault('evidence', {'message_id': identifier, 'quote': text})
        return {'text': json.dumps({'memories': payload}, ensure_ascii=False),
                'finish_reason': 'stop', 'usage': {}}

    async def observe(self, text, items, *, identifier=None, provider=None, epoch=None):
        epoch = self.manager.capture_write_epoch() if epoch is None else epoch
        identifier = self.message(text) if identifier is None else identifier
        provider = provider or AsyncMock(return_value=self.response(text, items, identifier))
        trace = {}
        result = await self.manager.observe_turn(
            session_id=self.session, user_text=text, assistant_text='合成助手回复，不作证据',
            user_message_id=identifier, write_epoch=epoch,
            extraction_provider=SimpleNamespace(complete_raw=provider), trace=trace, max_attempts=1)
        return result, trace, provider, identifier

    def retrieved(self, query):
        return [row for row, _ in self.manager.retriever.search(query, subject='user')]

    async def retire_favorite_chain(self):
        epoch = self.manager.capture_write_epoch()
        original = '我长期最喜欢喝绿茶。'
        _, _, _, identifier = await self.observe(original, [candidate('用户长期最喜欢喝绿茶。')])
        await self.observe('我现在最喜欢喝的茶改为红茶。',
                           [candidate('用户现在最喜欢喝红茶。', operation='update')])
        await self.observe('我现在不再喜欢喝红茶。',
                           [candidate('用户现在不再喜欢喝红茶。', operation='update')])
        self.assertEqual(self.manager.capture_write_epoch(), epoch,
                         'this scenario must not rely on a manual-maintenance write floor')
        active = self.rows(active=True)
        self.assertEqual(len(active), 1)
        self.assertEqual((active[0].preference_target, active[0].preference_level), ('红茶', 'dislike'))
        return original, identifier

    async def test_original_tea_favorite_and_recent_addition_are_both_active(self):
        await self.observe('我长期最喜欢喝绿茶。', [candidate('用户长期最喜欢喝绿茶。')])
        added, trace, provider, _ = await self.observe(
            '最近红茶也挺喜欢的，我还没决定要不要改掉原来的习惯。',
            [candidate('用户最近喜欢红茶。')])
        self.assertEqual(provider.await_count, 1)
        self.assertFalse(trace.get('error_type'))
        self.assertEqual(len(added), 1)
        self.assertEqual(self.active_targets(), {'绿茶', '红茶'})
        by_target = {row.preference_target: row for row in self.rows(active=True)}
        self.assert_preference(by_target['绿茶'], '绿茶', 'favorite', 'long_term')
        self.assert_preference(by_target['红茶'], '红茶', 'like', 'recent')
        self.assertEqual(by_target['红茶'].decision, 'add')
        self.assertTrue(any(row.preference_target == '红茶' for row in self.retrieved('红茶')))
        context, found = self.manager.context_with_status('红茶', subject='user')
        self.assertTrue(found)
        self.assertIn('最近', context)
        self.assertTrue('as_of' in context or '时间' in context or '记录' in context,
                        'relative scope needs an observation anchor in model context')

    async def test_multiple_food_and_game_likes_keep_independent_members(self):
        for text, value, key in (
            ('我喜欢喝绿茶。', '用户喜欢喝绿茶。', '饮食:偏好'),
            ('我喜欢喝红茶。', '用户喜欢喝红茶。', '饮食:偏好'),
            ('我喜欢玩文明六。', '用户喜欢玩文明六。', '游戏:偏好'),
            ('我喜欢玩星露谷。', '用户喜欢玩星露谷。', '游戏:偏好'),
        ):
            await self.observe(text, [candidate(value, key=key)])
        self.assertEqual(self.active_targets(), {'绿茶', '红茶', '文明六', '星露谷'})
        self.assertTrue(all(row.preference_level == 'like' for row in self.rows(active=True)))
        self.assertEqual(len(self.rows()), 4)
        for target in self.active_targets():
            self.assertTrue(any(row.preference_target == target for row in self.retrieved(target)))

    async def test_second_favorite_without_explicit_replacement_is_conflict(self):
        await self.observe('我长期最喜欢喝绿茶。', [candidate('用户长期最喜欢喝绿茶。')])
        await self.observe('我长期最喜欢喝红茶。', [candidate('用户长期最喜欢喝红茶。')])
        self.assertEqual(self.active_targets(), {'绿茶'})
        red = next(row for row in self.rows() if row.preference_target == '红茶')
        self.assertEqual((red.status, red.decision), ('conflict', 'conflict'))
        self.assertFalse(any(row.preference_target == '红茶' for row in self.retrieved('红茶')))

    async def test_explicit_favorite_replacement_retires_only_other_favorite(self):
        await self.observe('我长期最喜欢喝绿茶。', [candidate('用户长期最喜欢喝绿茶。')])
        await self.observe('我长期喜欢喝咖啡。', [candidate('用户长期喜欢喝咖啡。')])
        await self.observe('我最近喜欢喝红茶。', [candidate('用户最近喜欢喝红茶。')])
        await self.observe('我现在最喜欢喝的茶改为红茶。',
                           [candidate('用户现在最喜欢喝红茶。', operation='update')])
        self.assertEqual(self.active_targets(), {'红茶', '咖啡'})
        green = next(row for row in self.rows() if row.preference_target == '绿茶')
        self.assertEqual(green.status, 'superseded')
        current = next(row for row in self.rows(active=True)
                       if row.preference_target == '红茶' and row.preference_scope == 'current')
        self.assert_preference(current, '红茶', 'favorite', 'current')
        self.assertEqual(current.decision, 'update')
        self.assertTrue(any(row.preference_target == '红茶' and row.preference_scope == 'recent'
                            for row in self.rows(active=True)))
        self.assertTrue(any(row.preference_target == '咖啡' for row in self.rows(active=True)))

    async def test_cropped_change_quote_cannot_hide_pending_full_message(self):
        await self.observe('我长期最喜欢喝绿茶。', [candidate('用户长期最喜欢喝绿茶。')])
        green = self.rows(active=True)[0]
        before = green.to_dict()
        quote = '我现在最喜欢喝的茶改为红茶。'
        text = quote + '但我还没决定要不要改掉原来的习惯。'
        identifier = self.message(text)
        saved, trace, provider, _ = await self.observe(
            text, [candidate('用户现在最喜欢喝红茶。', operation='update',
                             evidence={'message_id': identifier, 'quote': quote})],
            identifier=identifier)
        self.assertEqual(provider.await_count, 1)
        self.assertFalse(trace.get('error_type'))
        self.assertEqual(len(saved), 1)
        red = saved[0]
        self.assertEqual((red.status, red.decision), ('conflict', 'conflict'))
        self.assert_preference(red, '红茶', 'favorite', 'current')
        self.assertEqual(red.evidence_message_id, identifier)
        self.assertEqual(red.evidence_quote, quote)
        self.assertEqual(self.store.get_message(red.evidence_message_id)['text'], text)
        self.assertEqual(self.store.get(green.id).to_dict(), before)
        self.assertEqual(self.active_targets(), {'绿茶'})
        self.assertFalse(any(row.preference_target == '红茶' for row in self.retrieved('红茶')))

    async def test_no_longer_liking_green_retires_only_green(self):
        await self.observe('我长期喜欢喝绿茶。', [candidate('用户长期喜欢喝绿茶。')])
        await self.observe('我长期喜欢喝红茶。', [candidate('用户长期喜欢喝红茶。')])
        await self.observe('我喜欢玩文明六。', [candidate('用户喜欢玩文明六。', key='游戏:偏好')])
        await self.observe('我现在不再喜欢喝绿茶。',
                           [candidate('用户现在不再喜欢喝绿茶。', operation='update')])
        green = [row for row in self.rows() if row.preference_target == '绿茶']
        self.assertTrue(any(row.status == 'superseded' and row.preference_level == 'like' for row in green))
        current = next(row for row in green if row.status == 'active')
        self.assert_preference(current, '绿茶', 'dislike', 'current')
        self.assertEqual(self.active_targets(), {'绿茶', '红茶', '文明六'})
        self.assertTrue(all(row.preference_level == 'like' for row in self.rows(active=True)
                            if row.preference_target != '绿茶'))

    async def test_same_target_scope_opposite_without_change_remains_conflict(self):
        await self.observe('我长期喜欢喝绿茶。', [candidate('用户长期喜欢喝绿茶。')])
        await self.observe('我长期不喜欢喝绿茶。', [candidate('用户长期不喜欢喝绿茶。')])
        active = self.rows(active=True)
        self.assertEqual(len(active), 1)
        self.assert_preference(active[0], '绿茶', 'like', 'long_term')
        negative = next(row for row in self.rows() if row.preference_level == 'dislike')
        self.assertEqual(negative.status, 'conflict')
        self.assertTrue(all(row.preference_level == 'like' for row in self.retrieved('绿茶')))

    async def test_same_target_like_at_different_scopes_keeps_relative_anchor(self):
        await self.observe('我长期喜欢喝红茶。', [candidate('用户长期喜欢喝红茶。')])
        await self.observe('我最近喜欢喝红茶。', [candidate('用户最近喜欢喝红茶。')])
        active = self.rows(active=True)
        self.assertEqual(len(active), 2)
        self.assertEqual({row.preference_scope for row in active}, {'long_term', 'recent'})
        for row in active:
            self.assert_preference(row, '红茶', 'like', row.preference_scope)
            exported = row.to_dict()
            self.assertNotIn('valid_until', exported)
            self.assertNotIn('expires_at', exported)

    async def test_inferred_source_cannot_claim_collection_or_retire_stated(self):
        await self.observe('我长期最喜欢喝绿茶。', [candidate('用户长期最喜欢喝绿茶。')])
        await self.observe('我长期喜欢喝红茶。', [candidate(
            '用户长期喜欢喝红茶。', source='inferred',
            preference={'target': '红茶', 'level': 'like', 'scope': 'long_term'})])
        self.assertEqual(self.active_targets(), {'绿茶'})
        self.assertTrue(all(row.source == 'stated' for row in self.rows(active=True)))
        self.assertFalse(any(row.source == 'inferred' for row in self.retrieved('红茶')))

    async def test_model_forged_target_or_unsupported_declaration_is_rejected(self):
        for text, value, metadata in (
            ('我喜欢喝红茶。', '用户喜欢喝红茶。',
             {'target': '绿茶', 'level': 'like', 'scope': 'unspecified'}),
            ('我喝过红茶。', '用户喜欢喝红茶。',
             {'target': '红茶', 'level': 'like', 'scope': 'unspecified'}),
            ('我不是不喜欢喝绿茶。', '用户喜欢喝绿茶。',
             {'target': '绿茶', 'level': 'like', 'scope': 'unspecified'}),
        ):
            with self.subTest(text=text):
                saved, trace, _, _ = await self.observe(text, [candidate(value, preference=metadata)])
                self.assertEqual(saved, [])
                self.assertTrue(trace.get('drops'))
        self.assertEqual(self.rows(), [])

    async def test_unknown_key_cannot_claim_structured_collection_authority(self):
        await self.observe('我长期最喜欢喝绿茶。', [candidate('用户长期最喜欢喝绿茶。')])
        saved, trace, _, _ = await self.observe('我长期喜欢喝红茶。', [candidate(
            '用户长期喜欢喝红茶。', key='饮食:随机标签',
            preference={'target': '红茶', 'level': 'like', 'scope': 'long_term'})])
        self.assertEqual(saved, [])
        self.assertTrue(trace.get('drops'))
        self.assertEqual(self.active_targets(), {'绿茶'})

    async def test_late_preference_extraction_cannot_restore_older_liking(self):
        text = '我长期喜欢喝绿茶。'
        identifier = self.message(text)
        epoch = self.manager.capture_write_epoch()
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            return self.response(text, [candidate('用户长期喜欢喝绿茶。')], identifier)
        provider = AsyncMock(side_effect=delayed)
        task = asyncio.create_task(self.observe(text, [], identifier=identifier,
                                                 provider=provider, epoch=epoch))
        try:
            await asyncio.wait_for(started.wait(), 3)
            await self.observe('我现在不再喜欢喝绿茶。',
                               [candidate('用户现在不再喜欢喝绿茶。', operation='update')])
            release.set()
            await asyncio.wait_for(task, 3)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(all(row.preference_level == 'dislike' for row in self.rows(active=True)))
        self.assertFalse(any(row.preference_level == 'like' for row in self.retrieved('绿茶')))

    async def test_forget_one_slot_keeps_other_member_and_blocks_old_replay(self):
        text = '我长期喜欢喝绿茶。'
        _, _, _, identifier = await self.observe(text, [candidate('用户长期喜欢喝绿茶。')])
        await self.observe('我长期喜欢喝红茶。', [candidate('用户长期喜欢喝红茶。')])
        green = next(row for row in self.rows(active=True) if row.preference_target == '绿茶')
        self.assertEqual(self.manager.forget(green.id), 1)
        provider = AsyncMock(return_value=self.response(text, [candidate('用户长期喜欢喝绿茶。')], identifier))
        saved, trace, _, _ = await self.observe(text, [], identifier=identifier, provider=provider)
        self.assertEqual(saved, [])
        self.assertEqual(trace.get('manager_skip'), 'write_state_ineligible')
        provider.assert_not_awaited()
        self.assertEqual(self.active_targets(), {'红茶'})
        self.assertTrue(all(row.preference_target != '绿茶' for row in self.rows()))
        self.assertTrue(any(row.preference_target == '红茶' for row in self.retrieved('红茶')))

    async def test_manual_correction_is_slot_local_and_clears_automatic_metadata(self):
        await self.observe('我长期喜欢喝绿茶。', [candidate('用户长期喜欢喝绿茶。')])
        await self.observe('我长期喜欢喝红茶。', [candidate('用户长期喜欢喝红茶。')])
        green = next(row for row in self.rows(active=True) if row.preference_target == '绿茶')
        red = next(row for row in self.rows(active=True) if row.preference_target == '红茶')
        corrected = self.manager.correct(green.id, '用户对绿茶的偏好已由本人手动核对。')
        self.assertIsNotNone(corrected)
        self.assertEqual(corrected.identity_slot, green.identity_slot)
        self.assertEqual(corrected.evidence_state, 'manual')
        self.assertIsNone(corrected.evidence_message_id)
        self.assertEqual(corrected.evidence_quote, '')
        self.assertFalse(corrected.preference_target)
        self.assertFalse(corrected.preference_level)
        self.assertFalse(corrected.preference_scope)
        self.assertIsNone(corrected.preference_as_of)
        self.assertEqual(self.store.get(green.id).status, 'superseded')
        self.assertEqual(self.store.get(red.id).status, 'active')
        self.assertEqual(len(self.rows(active=True)), 2)
        self.assertTrue(any(row.id == red.id for row in self.retrieved('红茶')))

    async def check_unchanged_manual_correction(self, padding):
        await self.observe('我长期喜欢喝绿茶。', [candidate('用户长期喜欢喝绿茶。')])
        await self.observe('我长期喜欢喝红茶。', [candidate('用户长期喜欢喝红茶。')])
        green = next(row for row in self.rows(active=True) if row.preference_target == '绿茶')
        red = next(row for row in self.rows(active=True) if row.preference_target == '红茶')
        red_before = red.to_dict()
        corrected = self.manager.correct(green.id, padding + green.value + padding)
        self.assertIsNotNone(corrected)
        self.assertNotEqual(corrected.id, green.id,
                            'an explicit edit must create a manual revision even if text is unchanged')
        self.assertEqual(corrected.value, green.value)
        self.assertEqual(corrected.identity_slot, green.identity_slot)
        self.assertEqual(corrected.evidence_state, 'manual')
        self.assertIsNone(corrected.evidence_message_id)
        self.assertEqual(corrected.evidence_quote, '')
        self.assertFalse(corrected.preference_target)
        self.assertFalse(corrected.preference_level)
        self.assertFalse(corrected.preference_scope)
        self.assertIsNone(corrected.preference_as_of)
        self.assertEqual(self.store.get(green.id).status, 'superseded')
        self.assertEqual(self.store.get(red.id).to_dict(), red_before)
        self.assertEqual(len(self.rows(active=True)), 2)

    async def test_same_value_manual_correction_still_clears_automatic_metadata(self):
        await self.check_unchanged_manual_correction('')

    async def test_whitespace_equivalent_manual_correction_still_clears_automatic_metadata(self):
        await self.check_unchanged_manual_correction(' \t\n ')

    async def test_two_connections_cannot_activate_two_ambiguous_favorites(self):
        other_store = MemoryStore(str(self.database))
        other_manager = MemoryManager(other_store)
        arrived, release = asyncio.Event(), asyncio.Event()
        ready = 0
        async def controlled(messages, **options):
            nonlocal ready
            source = json.loads(messages[1]['content'])['user_messages'][0]
            ready += 1
            if ready == 2:
                arrived.set()
            await release.wait()
            target = '绿茶' if '绿茶' in source['text'] else '红茶'
            return self.response(source['text'], [candidate(f'用户长期最喜欢喝{target}。')], source['id'])
        provider = SimpleNamespace(complete_raw=AsyncMock(side_effect=controlled))
        tasks = []
        try:
            for store, manager, text in (
                (self.store, self.manager, '我长期最喜欢喝绿茶。'),
                (other_store, other_manager, '我长期最喜欢喝红茶。'),
            ):
                epoch = manager.capture_write_epoch()
                identifier = store.add_message(self.session, 'user', text)
                tasks.append(asyncio.create_task(manager.observe_turn(
                    session_id=self.session, user_text=text, assistant_text='合成回复',
                    user_message_id=identifier, write_epoch=epoch,
                    extraction_provider=provider, max_attempts=1)))
            await asyncio.wait_for(arrived.wait(), 3)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
            active = self.rows(active=True)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].preference_level, 'favorite')
            self.assertIn(active[0].preference_target, {'绿茶', '红茶'})
            self.assertEqual(provider.complete_raw.await_count, 2)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            other_store.close()

    async def test_linked_transaction_rechecks_target_slot_and_message_time(self):
        # The accepted row comes from the complete real pipeline. Direct calls
        # below exercise final transaction guards, not an expected-data fixture.
        await self.observe('我长期喜欢喝绿茶。', [candidate('用户长期喜欢喝绿茶。')])
        row = self.rows(active=True)[0]
        original = row.to_dict()
        fields = {key: original[key] for key in (
            'kind', 'subject', 'key', 'value', 'source', 'importance', 'confidence',
            'session_id', 'evidence_message_id', 'evidence_quote', 'evidence_state',
            'identity_slot', 'preference_target', 'preference_level', 'preference_scope')}
        for forged in ({'preference_target': '红茶'},
                       {'identity_slot': json.dumps(['红茶', 'long_term'])}):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                self.store.upsert(**{**fields, **forged})
            self.assertEqual(self.store.get(row.id).to_dict(), original)
        identifier = self.store.upsert(**fields, preference_as_of=row.preference_as_of + 123456789)
        self.assertEqual(identifier, row.id)
        kept = self.store.get(row.id)
        self.assertEqual(kept.preference_as_of,
                         self.store.get_message(kept.evidence_message_id)['created_at'])
        self.assertEqual(len(self.rows(active=True)), 1)

    async def test_deleting_retired_slot_preserves_replacement_and_clears_link(self):
        await self.observe('我长期最喜欢喝绿茶。', [candidate('用户长期最喜欢喝绿茶。')])
        green = self.rows(active=True)[0]
        await self.observe('我现在最喜欢喝的茶改为红茶。',
                           [candidate('用户现在最喜欢喝红茶。', operation='update')])
        red = next(row for row in self.rows(active=True) if row.preference_target == '红茶')
        self.assertEqual(red.supersedes, green.id)
        self.assertEqual(self.manager.forget(green.id), 1)
        self.assertIsNone(self.store.get(green.id))
        kept = self.store.get(red.id)
        self.assertEqual(kept.status, 'active')
        self.assertIsNone(kept.supersedes)
        self.assertEqual(self.active_targets(), {'红茶'})
        self.assertTrue(any(row.id == red.id for row in self.retrieved('红茶')))

    async def test_manager_independently_rejects_null_or_partial_flat_metadata(self):
        for fields in (
            {'preference': None},
            {'preference_target': '绿茶', 'preference_level': 'like'},
            {'preference_scope': 'long_term'},
            {'preference_target': '', 'preference_level': '', 'preference_scope': ''},
        ):
            with self.subTest(fields=fields):
                text = '我长期喜欢喝绿茶。'
                identifier = self.message(text)
                raw = {**candidate('用户长期喜欢喝绿茶。'),
                       'evidence_message_id': identifier, 'evidence_quote': text,
                       'evidence_state': 'linked', **fields}
                extractor = AsyncMock(return_value=[raw])
                with patch('server.memory.manager.extract_memories', extractor):
                    saved = await self.manager.observe_turn(
                        session_id=self.session, user_text=text, assistant_text='合成回复',
                        user_message_id=identifier,
                        write_epoch=self.manager.capture_write_epoch())
                extractor.assert_awaited_once()
                self.assertEqual(saved, [])
                self.assertEqual(self.rows(), [])

    async def test_retired_favorite_cannot_revive_after_replacement_is_disliked(self):
        text, identifier = await self.retire_favorite_chain()
        before = sorted((row.to_dict() for row in self.rows()), key=lambda row: row['id'])
        saved, _, _, _ = await self.observe(
            text, [candidate('用户长期最喜欢喝绿茶。')], identifier=identifier)
        self.assertEqual(saved, [])
        self.assertEqual(before, sorted((row.to_dict() for row in self.rows()), key=lambda row: row['id']))
        self.assertFalse(any(row.preference_target == '绿茶' for row in self.retrieved('绿茶')))
        self.assertEqual(self.active_targets(), {'红茶'})
        fresh, _, _, fresh_id = await self.observe(text, [candidate('用户长期最喜欢喝绿茶。')])
        self.assertGreater(fresh_id, identifier)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].status, 'active')
        self.assert_preference(fresh[0], '绿茶', 'favorite', 'long_term')
        self.assertEqual(self.active_targets(), {'绿茶', '红茶'})

    async def test_retired_favorite_guard_applies_from_second_connection(self):
        text, identifier = await self.retire_favorite_chain()
        other_store = MemoryStore(str(self.database))
        try:
            other_manager = MemoryManager(other_store)
            provider = SimpleNamespace(complete_raw=AsyncMock(return_value=self.response(
                text, [candidate('用户长期最喜欢喝绿茶。')], identifier)))
            saved = await other_manager.observe_turn(
                session_id=self.session, user_text=text, assistant_text='合成回复',
                user_message_id=identifier, write_epoch=other_manager.capture_write_epoch(),
                extraction_provider=provider, max_attempts=1)
            self.assertEqual(saved, [])
            active = other_store.list_memories(status='active', limit=5000)
            self.assertEqual(len(active), 1)
            self.assertEqual((active[0].preference_target, active[0].preference_level), ('红茶', 'dislike'))
        finally:
            other_store.close()
        self.assertEqual(self.active_targets(), {'红茶'})

    async def test_retired_favorite_guard_survives_reopen_without_blocking_older_independent_like(self):
        # This unrelated message was accepted before all favorite changes but its
        # model reply arrives later. A family-wide floor would wrongly discard it.
        milk = '我长期喜欢喝牛奶。'
        milk_id = self.message(milk)
        milk_epoch = self.manager.capture_write_epoch()
        text, identifier = await self.retire_favorite_chain()
        self.store.close()
        self.store = MemoryStore(str(self.database))
        self.manager = MemoryManager(self.store)
        saved, _, _, _ = await self.observe(
            text, [candidate('用户长期最喜欢喝绿茶。')], identifier=identifier)
        self.assertEqual(saved, [])
        independent, _, _, _ = await self.observe(
            milk, [candidate('用户长期喜欢喝牛奶。')], identifier=milk_id, epoch=milk_epoch)
        self.assertEqual(len(independent), 1)
        self.assert_preference(independent[0], '牛奶', 'like', 'long_term')
        self.assertEqual(self.active_targets(), {'红茶', '牛奶'})
        self.assertFalse(any(row.preference_target == '绿茶' for row in self.rows(active=True)))
        self.assertTrue(any(row.preference_target == '牛奶' for row in self.retrieved('牛奶')))

    async def test_retired_target_blocks_older_opposite_at_other_scope_after_reopen(self):
        epoch = self.manager.capture_write_epoch()
        old_negative = '我最近不再喜欢喝绿茶。'
        old_id = self.message(old_negative)
        independent = '我长期喜欢喝牛奶。'
        independent_id = self.message(independent)
        await self.observe('纠正，我现在最喜欢喝绿茶。',
                           [candidate('用户现在最喜欢喝绿茶。', operation='update')])
        await self.observe('纠正，我现在最喜欢喝红茶。',
                           [candidate('用户现在最喜欢喝红茶。', operation='update')])
        self.assertEqual(self.manager.capture_write_epoch(), epoch,
                         'automatic changes must not depend on a manual write floor')
        green = next(row for row in self.rows() if row.preference_target == '绿茶')
        self.assertEqual(green.status, 'superseded')
        self.assertEqual(green.preference_scope, 'current')
        self.assertGreater(green.retired_by_message_id, old_id)
        self.assertEqual(self.active_targets(), {'红茶'})

        # The incoming negative is "recent", while the retired positive is
        # "current". A slot-only retirement guard would admit this stale result.
        for reopened in (False, True):
            with self.subTest(reopened=reopened):
                if reopened:
                    self.store.close()
                    self.store = MemoryStore(str(self.database))
                    self.manager = MemoryManager(self.store)
                before = sorted((row.to_dict() for row in self.rows()), key=lambda row: row['id'])
                saved, _, provider, _ = await self.observe(
                    old_negative, [candidate('用户最近不再喜欢喝绿茶。', operation='update')],
                    identifier=old_id, epoch=epoch)
                self.assertEqual(provider.await_count, 1)
                self.assertEqual(saved, [])
                self.assertEqual(before, sorted((row.to_dict() for row in self.rows()),
                                                key=lambda row: row['id']))
                self.assertFalse(any(row.preference_target == '绿茶'
                                     for row in self.retrieved('绿茶')))

        accepted, _, _, _ = await self.observe(
            independent, [candidate('用户长期喜欢喝牛奶。')],
            identifier=independent_id, epoch=epoch)
        self.assertEqual(len(accepted), 1)
        self.assert_preference(accepted[0], '牛奶', 'like', 'long_term')
        self.assertEqual(self.active_targets(), {'红茶', '牛奶'})
        fresh, _, _, fresh_id = await self.observe(
            old_negative, [candidate('用户最近不再喜欢喝绿茶。', operation='update')])
        self.assertGreater(fresh_id, green.retired_by_message_id)
        self.assertEqual(len(fresh), 1)
        self.assert_preference(fresh[0], '绿茶', 'dislike', 'recent')
        self.assertEqual(self.active_targets(), {'红茶', '牛奶', '绿茶'})


if __name__ == '__main__':
    unittest.main()
