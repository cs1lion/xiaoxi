"""Synthetic draft observations; no app bootstrap, personal stores or providers."""
import asyncio
import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.editor_insights import (
    EditorInsightService, InsightConflict, InsightDiscussionError, InsightSpeechError,
)


BODY = '第一段介绍读者的具体处境，并说明这份稿子准备解决的问题。\n这里的结论埋在铺垫后面。\n' + '后文用一个具体例子解释选择，并交代讨论范围与下一步。' * 3
ANCHOR = '这里的结论埋在铺垫后面。'
POINT = '这段先交代了过程，读者却还不知道你的主张；把结论放到这段开头，会更容易理解后面的例子。'


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def synthesize(self, text, emotion):
        self.calls.append((text, emotion))
        return b'synthetic-audio'


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.allowed = True
        self.payloads = []
        self.service = EditorInsightService(SimpleNamespace(offline=False), enabled=lambda: self.allowed,
                                            clock=lambda: self.now)
        self.addCleanup(self.service.close)

        async def resolver(payload):
            self.payloads.append(payload)
            return {'action': 'insight', 'anchor': ANCHOR, 'point': POINT}

        self.service.resolver = resolver
        self.service.state('editor', 1, True)
        self.engine = FakeEngine()

    async def analyze(self, body=BODY, revision=1, session='editor', **kwargs):
        return await self.service.analyze(session, revision, kwargs.get('title', ''), body, True)

    async def insight(self):
        return (await self.analyze())['insight']

    async def test_anchored_draft_is_ephemeral_and_audio_only_after_click(self):
        insight = await self.insight()
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(insight['anchor'], ANCHOR)
        self.assertEqual(insight['point'], POINT)
        self.assertEqual(insight['draft_revision'], 1)
        draft = self.payloads[0]['draft']
        self.assertFalse(draft['saved'])
        self.assertTrue(draft['untrusted'])
        self.assertNotIn('note', self.payloads[0])
        audio = await self.service.speak(insight['id'], 'editor', 1, self.engine)
        self.assertEqual(audio['text'], POINT)
        self.assertEqual(base64.b64decode(audio['audio']), b'synthetic-audio')
        self.assertEqual(self.engine.calls, [(POINT, 'base')])
        self.assertFalse(any(key in self.service.__dict__ for key in ('notes', 'store', 'memory_manager')))

    async def test_explicit_state_enable_and_matching_revision_required(self):
        self.assertEqual((await self.analyze(session='unknown'))['reason'], 'editor_not_enabled')
        self.service.state('editor', 2, False)
        self.assertEqual((await self.analyze(revision=2))['reason'], 'editor_not_enabled')
        self.service.state('editor', 3, True)
        self.assertEqual((await self.analyze(revision=2))['status'], 'superseded')
        self.assertEqual(self.payloads, [])

    async def test_revisions_never_go_back_and_duplicate_state_is_idempotent(self):
        insight = await self.insight()
        self.service.state('editor', 1, True)
        self.assertIn(insight['id'], self.service._insights)
        self.service.state('editor', 3, True)
        old = self.service.state('editor', 2, False)
        self.assertEqual(old['status'], 'superseded')
        self.assertTrue(self.service._sessions['editor']['enabled'])
        with self.assertRaises(KeyError):
            await self.service.speak(insight['id'], 'editor', 1, self.engine)

    async def test_memory_disabled_and_offline_skip_model(self):
        self.allowed = False
        self.assertEqual((await self.analyze())['reason'], 'memory_disabled')
        self.assertEqual(self.payloads, [])
        self.allowed = True
        self.service.state('editor', 2, True)
        self.service.llm.offline = True
        self.assertEqual((await self.analyze(revision=2))['reason'], 'model_unavailable')
        self.assertEqual(self.payloads, [])

    async def test_full_credential_check_precedes_4000_character_clipping(self):
        for body in (BODY + 'password=do-not-send-this-value', '甲' * 4500 + ' api_key=tail-secret'):
            result = await self.analyze(body)
            self.assertEqual(result['status'], 'quiet')
            self.assertEqual(result['reason'], 'sensitive_draft')
        self.assertEqual(self.payloads, [])
        self.assertEqual(self.service._sessions['editor']['checks'], 0)

    async def test_long_draft_payload_is_bounded_and_marks_partial_view(self):
        body = BODY + '甲' * 5000
        await self.analyze(body)
        draft = self.payloads[0]['draft']
        self.assertEqual(len(draft['body']), 4000)
        self.assertTrue(draft['body_truncated'])
        self.assertEqual(draft['body_chars'], len(body))

    async def test_anchor_repeated_in_invisible_tail_is_not_supported(self):
        result = await self.analyze(BODY + '甲' * 5000 + ANCHOR)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'analysis_invalid_response')
        self.assertEqual(self.service._insights, {})

    async def test_ambiguous_overlapping_anchor_is_rejected(self):
        async def resolver(_):
            return {'action': 'insight', 'anchor': 'aaaaa', 'point': POINT}
        self.service.resolver = resolver
        result = await self.analyze('aaaaaa' + '乙' * 80)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'analysis_invalid_response')

    async def test_invisible_or_fabricated_anchor_is_rejected(self):
        for index, anchor in enumerate(('原文没有这句话。', '尾部的特有观点。')):
            session = 'unsupported-anchor-' + str(index)
            self.service.state(session, 1, True)
            self.service.resolver = lambda payload, anchor=anchor: self.value(
                {'action': 'insight', 'anchor': anchor, 'point': POINT})
            result = await self.analyze(BODY + '甲' * 4100 + '尾部的特有观点。', session=session)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['reason'], 'analysis_invalid_response')

    @staticmethod
    async def value(value):
        return value

    async def test_unknown_shape_and_sensitive_output_are_unavailable(self):
        values = [None, [], {'action': 'quiet', 'point': 'extra'},
                  {'action': 'insight', 'anchor': ANCHOR, 'point': POINT, 'tool': 'note.update'},
                  {'action': 'insight', 'anchor': ANCHOR, 'point': 'x' * 241},
                  {'action': 'insight', 'anchor': ANCHOR, 'point': 'password=not-an-observation'}]
        for index, value in enumerate(values):
            session = 'shape-' + str(index)
            self.service.state(session, 1, True)
            self.service.resolver = lambda _, value=value: self.value(value)
            result = await self.analyze(session=session)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['reason'], 'analysis_invalid_response')
        self.assertEqual(self.service._insights, {})

    async def test_unavailable_failures_consume_cooldown_and_max_checks(self):
        async def bad(_):
            raise RuntimeError('synthetic-provider-failure')
        self.service.resolver = bad
        for revision in range(1, 6):
            self.service.state('editor', revision, True)
            result = await self.analyze(revision=revision)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['reason'], 'analysis_unavailable')
            self.assertEqual(result['checks'], revision)
            self.service.state('editor', revision + 1, True)
            blocked = await self.analyze(revision=revision + 1)
            self.assertEqual(blocked['status'], 'cooldown' if revision < 5 else 'limit')
            self.now += 90
        self.assertEqual((await self.analyze(revision=6))['status'], 'limit')

    async def test_same_revision_cannot_be_reused_for_other_content(self):
        await self.analyze()
        result = await self.analyze(BODY + '新字')
        self.assertEqual(result['reason'], 'draft_revision_content_changed')

    async def test_same_draft_is_not_rechecked_after_cooldown(self):
        await self.analyze()
        self.now += 91
        self.assertEqual((await self.analyze())['reason'], 'draft_already_checked')
        self.assertEqual(len(self.payloads), 1)

    async def test_superseded_generation_cannot_reappear_if_provider_ignores_cancel(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def stubborn(_):
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()
            return {'action': 'insight', 'anchor': ANCHOR, 'point': POINT}
        self.service.resolver = stubborn
        pending = asyncio.create_task(self.analyze())
        await started.wait()
        self.service.state('editor', 2, True)
        finish.set()
        self.assertEqual((await pending)['status'], 'superseded')
        self.assertEqual(self.service._insights, {})

    async def test_disable_cancels_generation_without_error_or_candidate(self):
        started = asyncio.Event()
        async def blocking(_):
            started.set()
            await asyncio.Event().wait()
        self.service.resolver = blocking
        pending = asyncio.create_task(self.analyze())
        await started.wait()
        self.service.state('editor', 2, False)
        self.assertEqual((await pending)['status'], 'superseded')

    async def test_sessions_do_not_share_points_or_budgets(self):
        insight = await self.insight()
        self.service.state('other', 1, True)
        with self.assertRaises(InsightConflict):
            await self.service.speak(insight['id'], 'other', 1, self.engine)
        other = await self.analyze(session='other')
        self.assertEqual(other['checks'], 1)
        self.assertNotEqual(other['insight']['id'], insight['id'])

    async def test_dismissal_revokes_and_suppresses_same_anchor_in_next_draft(self):
        insight = await self.insight()
        self.assertEqual(self.service.dismiss(insight['id'], 'editor', 1)['status'], 'dismissed')
        self.now += 91
        self.service.state('editor', 2, True)
        result = await self.analyze(BODY + '改了一句。', revision=2)
        self.assertEqual(result['reason'], 'dismissed_anchor')
        self.assertEqual(self.payloads[-1]['dismissed_anchors'], [ANCHOR])

    async def test_repeated_click_singleflight_and_cached_audio(self):
        insight = await self.insight()
        started, finish = asyncio.Event(), asyncio.Event()
        async def audio(text, emotion):
            self.engine.calls.append((text, emotion))
            started.set()
            await finish.wait()
            return b'synthetic-audio'
        self.engine.synthesize = audio
        first = asyncio.create_task(self.service.speak(insight['id'], 'editor', 1, self.engine))
        await started.wait()
        second = asyncio.create_task(self.service.speak(insight['id'], 'editor', 1, self.engine))
        finish.set()
        self.assertEqual(await first, await second)
        await self.service.speak(insight['id'], 'editor', 1, self.engine)
        self.assertEqual(len(self.engine.calls), 1)

    async def test_invalidation_during_audio_discards_late_uncancellable_result(self):
        insight = await self.insight()
        started, finish = asyncio.Event(), asyncio.Event()
        async def audio(text, emotion):
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()
            return b'late-audio'
        self.engine.synthesize = audio
        pending = asyncio.create_task(self.service.speak(insight['id'], 'editor', 1, self.engine))
        await started.wait()
        self.service.state('editor', 2, True)
        with self.assertRaises(InsightConflict):
            await pending
        finish.set()
        await asyncio.sleep(0)
        self.assertEqual(self.service._insights, {})

    async def test_revoke_before_singleflight_task_starts_is_conflict_not_caller_cancel(self):
        insight = await self.insight()
        speech = asyncio.create_task(self.service.speak(insight['id'], 'editor', 1, self.engine))
        # Run the HTTP wrapper until it creates its provider task, then revoke
        # before the provider task receives its first execution slot.
        await asyncio.sleep(0)
        self.service.state('editor', 2, False)
        with self.assertRaises(InsightConflict):
            await speech
        self.assertEqual(self.engine.calls, [])

    async def test_analysis_timeout_consumes_budget_without_reviving_provider_result(self):
        self.service.MODEL_TIMEOUT_SECONDS = .01
        stopped = asyncio.Event()
        async def blocking(_):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.service.resolver = blocking
        result = await self.analyze()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'analysis_timeout')
        self.assertEqual(result['checks'], 1)
        await stopped.wait()

    async def test_ttl_and_disabled_policy_prevent_audio_calls(self):
        insight = await self.insight()
        self.allowed = False
        with self.assertRaises(InsightConflict):
            await self.service.speak(insight['id'], 'editor', 1, self.engine)
        self.allowed = True
        self.now += 301
        with self.assertRaises(KeyError):
            await self.service.speak(insight['id'], 'editor', 1, self.engine)
        self.assertEqual(self.engine.calls, [])

    async def test_failed_audio_does_not_claim_success_or_remove_readable_point(self):
        insight = await self.insight()
        async def failed(text, emotion):
            raise RuntimeError('synthetic-sensitive-provider-detail')
        self.engine.synthesize = failed
        with self.assertRaises(InsightSpeechError) as error:
            await self.service.speak(insight['id'], 'editor', 1, self.engine)
        self.assertEqual(str(error.exception), 'insight_speech_failed')
        self.assertIn(insight['id'], self.service._insights)
        self.assertIsNone(self.service._insights[insight['id']]['audio'])

    async def test_timeout_cancels_provider_and_returns_generic_error(self):
        insight = await self.insight()
        self.service.SPEECH_TIMEOUT_SECONDS = .01
        cancelled = asyncio.Event()
        async def blocked(text, emotion):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.engine.synthesize = blocked
        with self.assertRaises(InsightSpeechError) as error:
            await self.service.speak(insight['id'], 'editor', 1, self.engine)
        self.assertEqual(error.exception.status_code, 504)
        await cancelled.wait()

    async def test_default_resolver_has_one_attempt_and_strict_json(self):
        calls = []
        async def complete_raw(messages, **options):
            calls.append((messages, options))
            return {'text': '{"action":"quiet"}', 'finish_reason': 'stop'}
        self.service.llm.complete_raw = complete_raw
        self.service.resolver = self.service._resolve
        self.assertEqual((await self.analyze())['status'], 'quiet')
        self.assertEqual(calls[0][1]['max_attempts'], 1)
        self.assertEqual(calls[0][1]['max_tokens'], 6144)
        self.assertTrue(calls[0][1]['json_mode'])

    async def test_capacity_is_bounded_and_evicted_token_cannot_speak(self):
        insight = await self.insight()
        for index in range(self.service.CAPACITY):
            self.service.state('new-' + str(index), 1, True)
        self.assertEqual(len(self.service._sessions), self.service.CAPACITY)
        with self.assertRaises(KeyError):
            await self.service.speak(insight['id'], 'editor', 1, self.engine)

    async def test_invalid_types_sizes_and_boolean_revisions_rejected(self):
        for revision in (True, -1, 1.2, '1'):
            with self.assertRaises(ValueError):
                self.service.state('editor', revision, True)
        for title, body, enabled in [('x' * 161, BODY, True), ('', 'short', True),
                                      ('', 'x' * 20001, True), ('', BODY, 1), ('', ' ' * 100, True)]:
            with self.assertRaises(ValueError):
                await self.service.analyze('editor', 1, title, body, enabled)

    async def test_context_validates_current_scope_without_exposing_draft_or_mutation(self):
        insight = await self.insight()
        public = self.service.context(insight['id'], 'editor', 1)
        self.assertEqual(public, insight)
        self.assertNotIn('draft', public)
        self.assertNotIn('discussion_history', public)
        public['point'] = 'forged view'
        self.assertEqual(self.service.context(insight['id'], 'editor', 1)['point'], POINT)
        with self.assertRaises(InsightConflict):
            self.service.context(insight['id'], 'other', 1)
        self.service.state('editor', 2, True)
        with self.assertRaises(KeyError):
            self.service.context(insight['id'], 'editor', 1)

    async def test_discussion_uses_original_visible_draft_and_scoped_history_only(self):
        body = BODY + '乙' * 5000 + '末尾没有提供给模型的内容。'
        insight = (await self.analyze(body, title='论证草稿'))['insight']
        payloads = []
        async def discuss(payload):
            payloads.append(payload)
            return {'text': '可以保留这段；若这是叙事稿，我之前把结论提前的建议未必适合。'}
        self.service.discussion_resolver = discuss
        first = await self.service.reply(insight['id'], 'editor', 1, '我想保留这段。')
        second = await self.service.reply(insight['id'], 'editor', 1, '那怎么让读者更容易理解？')
        self.assertEqual(set(first), {'text', 'insight_id', 'draft_revision'})
        self.assertEqual(second['insight_id'], insight['id'])
        draft = payloads[0]['draft']
        self.assertEqual(draft['title'], '论证草稿')
        self.assertEqual(draft['body'], body[:4000])
        self.assertTrue(draft['body_truncated'])
        self.assertTrue(draft['untrusted'])
        self.assertFalse(draft['saved'])
        self.assertEqual(payloads[0]['insight'], {'anchor': ANCHOR, 'point': POINT})
        self.assertEqual(payloads[0]['history'], [])
        self.assertEqual(payloads[1]['history'], [
            {'role': 'user', 'content': '我想保留这段。'},
            {'role': 'assistant', 'content': first['text']},
        ])
        self.assertEqual(payloads[0]['boundaries']['tool_permissions'], [])
        self.assertFalse(payloads[0]['boundaries']['save_or_edit_draft'])
        self.assertFalse(payloads[0]['boundaries']['long_term_memory'])
        self.assertEqual(self.engine.calls, [])

    async def test_default_discussion_request_uses_scoped_payload_without_execution(self):
        insight = await self.insight()
        calls = []
        async def complete_raw(messages, **options):
            calls.append((messages, options))
            return {'text': '{"text":"建议稿：先用一句话说明读者的问题，再保留你的叙事顺序。"}',
                    'finish_reason': 'stop'}
        self.service.llm.complete_raw = complete_raw
        result = await self.service.reply(insight['id'], 'editor', 1, '我不同意提前结论，你能换一种改法吗？')
        self.assertIn('建议稿', result['text'])
        messages, options = calls[0]
        self.assertEqual(options['max_attempts'], 1)
        self.assertEqual(options['max_tokens'], 6144)
        self.assertTrue(options['json_mode'])
        self.assertEqual([message['role'] for message in messages], ['system', 'user', 'user'])
        payload = json.loads(messages[1]['content'])
        self.assertEqual(messages[-1]['content'], '我不同意提前结论，你能换一种改法吗？')
        self.assertEqual(set(payload), {'draft', 'insight', 'boundaries'})
        self.assertEqual(payload['insight'], {'anchor': ANCHOR, 'point': POINT})
        self.assertEqual(payload['draft']['body'], BODY)
        self.assertFalse(payload['boundaries']['save_or_edit_draft'])
        self.assertFalse(payload['boundaries']['long_term_memory'])
        self.assertEqual(payload['boundaries']['tool_permissions'], [])

    async def test_discussion_input_sensitive_and_invalid_values_never_reach_model(self):
        insight = await self.insight()
        calls = []
        async def discuss(payload):
            calls.append(payload)
            return {'text': '合成回答。'}
        self.service.discussion_resolver = discuss
        for text in (None, [], {}, True, '', '  ', 'x' * 1001, '甲\x00乙', 'password=keep-private'):
            with self.assertRaises(ValueError):
                await self.service.reply(insight['id'], 'editor', 1, text)
        self.assertEqual(calls, [])
        self.assertEqual(self.service._insights[insight['id']]['discussion_checks'], 0)

    async def test_discussion_invalid_outputs_do_not_commit_history(self):
        insight = await self.insight()
        outputs = [None, {'text': ''}, {'text': 'x' * 801}, {'text': 'api_key=private'},
                   {'text': '甲\x00乙'}, {'text': '合成回答', 'tool': 'note.update'}]
        for output in outputs:
            self.service.discussion_resolver = lambda _, output=output: self.value(output)
            with self.assertRaises(InsightDiscussionError) as exc:
                await self.service.reply(insight['id'], 'editor', 1, '为什么？')
            self.assertEqual(str(exc.exception), 'insight_discussion_invalid_reply')
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])
        with self.assertRaises(InsightDiscussionError) as exc:
            await self.service.reply(insight['id'], 'editor', 1, '再试一次')
        self.assertEqual(exc.exception.status_code, 429)

    async def test_discussion_budget_and_history_are_bounded_per_insight(self):
        insight = await self.insight()
        payloads = []
        async def discuss(payload):
            payloads.append(payload)
            return {'text': '回答' + payload['user_text']}
        self.service.discussion_resolver = discuss
        for index in range(6):
            await self.service.reply(insight['id'], 'editor', 1, '问题' + str(index))
        self.assertEqual(len(payloads[-1]['history']), 6)
        history = self.service._insights[insight['id']]['discussion_history']
        self.assertEqual(len(history), 6)
        self.assertEqual(history[0]['content'], '问题3')
        with self.assertRaises(InsightDiscussionError) as exc:
            await self.service.reply(insight['id'], 'editor', 1, '第七轮')
        self.assertEqual(str(exc.exception), 'insight_discussion_limit')
        self.assertEqual(len(payloads), 6)
        self.service.state('other', 1, True)
        other = (await self.analyze(session='other'))['insight']
        await self.service.reply(other['id'], 'other', 1, '独立问题')
        self.assertEqual(payloads[-1]['history'], [])

    async def test_discussion_payload_mutation_does_not_change_retained_context(self):
        insight = await self.insight()
        async def mutate(payload):
            payload['draft']['body'] = 'forged'
            payload['insight']['point'] = 'forged'
            payload['history'].append({'role': 'assistant', 'content': 'forged'})
            return {'text': '合成回答。'}
        self.service.discussion_resolver = mutate
        await self.service.reply(insight['id'], 'editor', 1, '为什么？')
        retained = self.service._insights[insight['id']]
        self.assertEqual(retained['draft']['body'], BODY)
        self.assertEqual(retained['public']['point'], POINT)
        self.assertEqual(len(retained['discussion_history']), 2)

    async def test_analysis_resolver_cannot_replace_original_draft_retained_for_discussion(self):
        async def mutate(payload):
            payload['draft']['title'] = 'forged title'
            payload['draft']['body'] = 'forged draft'
            return {'action': 'insight', 'anchor': ANCHOR, 'point': POINT}
        self.service.resolver = mutate
        insight = (await self.analyze(title='原始标题'))['insight']
        payloads = []
        async def discuss(payload):
            payloads.append(payload)
            return {'text': '合成回答。'}
        self.service.discussion_resolver = discuss
        await self.service.reply(insight['id'], 'editor', 1, '为什么？')
        self.assertEqual(payloads[0]['draft']['title'], '原始标题')
        self.assertEqual(payloads[0]['draft']['body'], BODY)

    async def test_discussion_mismatched_expired_and_disabled_scope_never_calls_provider(self):
        insight = await self.insight()
        calls = []
        async def discuss(payload):
            calls.append(payload)
            return {'text': '合成回答。'}
        self.service.discussion_resolver = discuss
        for session, revision in (('other', 1), ('editor', 2)):
            with self.assertRaises(InsightConflict):
                await self.service.reply(insight['id'], session, revision, '为什么？')
        self.allowed = False
        with self.assertRaises(InsightConflict):
            await self.service.reply(insight['id'], 'editor', 1, '为什么？')
        self.allowed = True
        self.now += 301
        with self.assertRaises(KeyError):
            await self.service.reply(insight['id'], 'editor', 1, '为什么？')
        self.assertEqual(calls, [])

    async def test_discussion_revoke_discards_provider_that_ignores_cancellation(self):
        insight = await self.insight()
        retained = self.service._insights[insight['id']]
        started, finish = asyncio.Event(), asyncio.Event()
        async def stubborn(_):
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()
            return {'text': '迟到回答。'}
        self.service.discussion_resolver = stubborn
        pending = asyncio.create_task(self.service.reply(insight['id'], 'editor', 1, '为什么？'))
        await started.wait()
        self.service.state('editor', 2, True)
        finish.set()
        with self.assertRaises(InsightConflict):
            await pending
        self.assertEqual(retained['discussion_history'], [])
        self.assertNotIn(insight['id'], self.service._insights)

    async def test_discussion_caller_cancellation_cannot_commit_swallowed_cancel_result(self):
        insight = await self.insight()
        started, finish, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def stubborn(_):
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()
            settled.set()
            return {'text': '迟到回答。'}
        self.service.discussion_resolver = stubborn
        pending = asyncio.create_task(self.service.reply(insight['id'], 'editor', 1, '为什么？'))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        finish.set()
        await settled.wait()
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])

    async def test_overlapping_discussion_requests_supersede_without_committing_old_history(self):
        insight = await self.insight()
        started, finish = asyncio.Event(), asyncio.Event()
        payloads = []
        async def discuss(payload):
            payloads.append(payload)
            if payload['user_text'] == '旧问题':
                started.set()
                try:
                    await finish.wait()
                except asyncio.CancelledError:
                    await finish.wait()
                return {'text': '旧回答。'}
            return {'text': '新回答。'}
        self.service.discussion_resolver = discuss
        old = asyncio.create_task(self.service.reply(insight['id'], 'editor', 1, '旧问题'))
        await started.wait()
        new = await self.service.reply(insight['id'], 'editor', 1, '新问题')
        finish.set()
        with self.assertRaises(InsightConflict):
            await old
        self.assertEqual(new['text'], '新回答。')
        self.assertEqual(payloads[1]['history'], [])
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [
            {'role': 'user', 'content': '新问题'}, {'role': 'assistant', 'content': '新回答。'},
        ])

    async def test_external_turn_predicate_prevents_late_discussion_delivery_and_history(self):
        insight = await self.insight()
        active = True
        started, finish = asyncio.Event(), asyncio.Event()
        async def discuss(_):
            started.set()
            await finish.wait()
            return {'text': '迟到回答。'}
        self.service.discussion_resolver = discuss
        pending = asyncio.create_task(self.service.reply(insight['id'], 'editor', 1, '为什么？',
                                                        is_current=lambda: active))
        await started.wait()
        active = False
        finish.set()
        with self.assertRaises(InsightConflict):
            await pending
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])

    async def test_discussion_timeout_and_failure_never_commit_or_leak_provider_error(self):
        insight = await self.insight()
        self.service.MODEL_TIMEOUT_SECONDS = .01
        finish, settled = asyncio.Event(), asyncio.Event()
        async def stubborn(_):
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()
            settled.set()
            return {'text': '超时回答。'}
        self.service.discussion_resolver = stubborn
        with self.assertRaises(InsightDiscussionError) as exc:
            await self.service.reply(insight['id'], 'editor', 1, '为什么？')
        self.assertEqual(exc.exception.status_code, 504)
        finish.set()
        await settled.wait()
        async def failed(_):
            raise RuntimeError('private synthetic provider detail')
        self.service.discussion_resolver = failed
        with self.assertRaises(InsightDiscussionError) as exc:
            await self.service.reply(insight['id'], 'editor', 1, '再解释一次')
        self.assertEqual(str(exc.exception), 'insight_discussion_failed')
        retained = self.service._insights[insight['id']]
        self.assertEqual(retained['discussion_history'], [])
        self.assertEqual(retained['discussion_checks'], 2)

    async def test_provider_cancellation_is_explicit_failure_without_cancelling_current_caller(self):
        insight = await self.insight()
        async def cancelled(_):
            raise asyncio.CancelledError()
        self.service.discussion_resolver = cancelled
        with self.assertRaises(InsightDiscussionError) as exc:
            await self.service.reply(insight['id'], 'editor', 1, '为什么？')
        self.assertEqual(str(exc.exception), 'insight_discussion_failed')
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])

    async def test_close_cancels_pending_discussion_without_retaining_context(self):
        insight = await self.insight()
        retained = self.service._insights[insight['id']]
        started, stopped = asyncio.Event(), asyncio.Event()
        async def blocked(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.service.discussion_resolver = blocked
        pending = asyncio.create_task(self.service.reply(insight['id'], 'editor', 1, '为什么？'))
        await started.wait()
        self.service.close()
        with self.assertRaises(InsightConflict):
            await pending
        await stopped.wait()
        self.assertEqual(retained['discussion_history'], [])
        self.assertEqual(self.service._insights, {})
        self.assertEqual(self.service._sessions, {})

    async def test_feedback_is_idempotent_revisable_local_and_does_not_edit_draft(self):
        insight = await self.insight()
        first = self.service.feedback(insight['id'], 'editor', 1, 'missed')
        self.assertEqual(first, {'status': 'feedback', 'value': 'missed',
                                 'stats': {'useful': 0, 'missed': 1, 'total': 1}})
        self.assertEqual(self.service.feedback(insight['id'], 'editor', 1, 'missed'), first)
        self.assertEqual(self.service._sessions['editor']['dismissed_anchors'], [ANCHOR])
        changed = self.service.feedback(insight['id'], 'editor', 1, 'useful')
        self.assertEqual(changed['stats'], {'useful': 1, 'missed': 0, 'total': 1})
        self.assertEqual(self.service._sessions['editor']['dismissed_anchors'], [])
        self.assertEqual(self.service._insights[insight['id']]['draft']['body'], BODY)
        self.service.state('other', 1, True)
        other = (await self.analyze(session='other'))['insight']
        other_feedback = self.service.feedback(other['id'], 'other', 1, 'missed')
        self.assertEqual(other_feedback['stats'], {'useful': 0, 'missed': 1, 'total': 1})
        self.service.state('editor', 2, False)
        with self.assertRaises(KeyError):
            self.service.feedback(insight['id'], 'editor', 1, 'useful')
        self.now += self.service.SESSION_TTL_SECONDS + 1
        self.service.status()
        self.assertEqual(self.service._sessions, {})

    async def test_missed_feedback_suppresses_repeated_quote_only_in_current_editor_session(self):
        insight = await self.insight()
        self.service.feedback(insight['id'], 'editor', 1, 'missed')
        self.now += 91
        self.service.state('editor', 2, True)
        result = await self.analyze(BODY + '新的句子。', revision=2)
        self.assertEqual(result['reason'], 'dismissed_anchor')
        self.assertEqual(self.payloads[-1]['dismissed_anchors'], [ANCHOR])
        self.service.state('other', 1, True)
        self.assertEqual((await self.analyze(session='other'))['status'], 'insight')

    async def test_feedback_illegal_expired_and_wrong_draft_rejected(self):
        insight = await self.insight()
        for value in ('yes', '', None, {}, True):
            with self.assertRaises(ValueError):
                self.service.feedback(insight['id'], 'editor', 1, value)
        with self.assertRaises(InsightConflict):
            self.service.feedback(insight['id'], 'editor', 2, 'useful')
        self.assertEqual(self.service._sessions['editor']['feedback'], {})
        self.now += 301
        with self.assertRaises(KeyError):
            self.service.feedback(insight['id'], 'editor', 1, 'useful')


if __name__ == '__main__':
    unittest.main()
