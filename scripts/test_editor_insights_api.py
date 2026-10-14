"""HTTP boundaries for draft observations with synthetic model and audio."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.editor_insights import EditorInsightService
from server.assistant.editor_insights_api import create_editor_insights_router
if __package__:
    from .test_editor_insights import BODY, ANCHOR, POINT, FakeEngine
else:
    from test_editor_insights import BODY, ANCHOR, POINT, FakeEngine


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.service = EditorInsightService(SimpleNamespace(offline=False), clock=lambda: self.now)
        async def resolve(payload):
            return {'action': 'insight', 'anchor': ANCHOR, 'point': POINT}
        self.service.resolver = resolve
        self.discussion_payloads = []
        async def discuss(payload):
            self.discussion_payloads.append(payload)
            return {'text': '可以保留原文，我的观点是在提醒读者可能还不清楚主张。'}
        self.service.discussion_resolver = discuss
        self.engine = FakeEngine()
        app = FastAPI()
        app.include_router(create_editor_insights_router(self.service, self.engine))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                                        headers={'Origin': 'http://testserver'})

    async def asyncTearDown(self):
        await self.client.aclose()
        self.service.close()

    async def insight(self):
        await self.client.post('/api/editor-insights/state', json={'session_id': 'editor', 'draft_revision': 1, 'enabled': True})
        response = await self.client.post('/api/editor-insights/analyze', json={
            'session_id': 'editor', 'draft_revision': 1, 'enabled': True, 'title': '', 'body': BODY})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        return response.json()['insight']

    async def test_status_and_click_only_speech_exact_contract(self):
        response = await self.client.get('/api/editor-insights/status')
        self.assertEqual(response.json(), {'available': True, 'reason': '', 'pause_ms': 12000,
                                           'cooldown_ms': 90000, 'max_checks': 5})
        insight = await self.insight()
        self.assertEqual(self.engine.calls, [])
        audio = await self.client.post('/api/editor-insights/' + insight['id'] + '/speak',
                                       json={'session_id': 'editor', 'draft_revision': 1})
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(set(audio.json()), {'id', 'draft_revision', 'text', 'audio', 'format'})
        self.assertEqual(audio.json()['text'], POINT)
        self.assertEqual(audio.headers['cache-control'], 'no-store')
        self.assertEqual(self.engine.calls, [(POINT, 'base')])

    async def test_cross_origin_scheme_host_and_missing_origin_rejected(self):
        for origin in ('http://elsewhere', 'https://testserver', 'null', ''):
            response = await self.client.post('/api/editor-insights/state', headers={'Origin': origin},
                                               json={'session_id': 'editor', 'draft_revision': 1, 'enabled': True})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.service._sessions, {})

    async def test_unknown_fields_and_forged_speech_text_rejected(self):
        insight = await self.insight()
        response = await self.client.post('/api/editor-insights/' + insight['id'] + '/speak',
                                           json={'session_id': 'editor', 'draft_revision': 1, 'text': 'forged'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.engine.calls, [])
        response = await self.client.post('/api/editor-insights/state',
                                           json={'session_id': 'editor', 'draft_revision': True, 'enabled': True})
        self.assertEqual(response.status_code, 400)

    async def test_session_revision_mismatch_and_revocation_cannot_speak(self):
        insight = await self.insight()
        path = '/api/editor-insights/' + insight['id'] + '/speak'
        self.assertEqual((await self.client.post(path, json={'session_id': 'other', 'draft_revision': 1})).status_code, 409)
        self.assertEqual((await self.client.post(path, json={'session_id': 'editor', 'draft_revision': 2})).status_code, 409)
        await self.client.post('/api/editor-insights/state', json={'session_id': 'editor', 'draft_revision': 2, 'enabled': False})
        self.assertEqual((await self.client.post(path, json={'session_id': 'editor', 'draft_revision': 1})).status_code, 404)
        self.assertEqual(self.engine.calls, [])

    async def test_dismiss_exact_fields_and_expired_audio_are_404(self):
        insight = await self.insight()
        response = await self.client.post('/api/editor-insights/' + insight['id'] + '/dismiss',
                                           json={'session_id': 'editor', 'draft_revision': 1})
        self.assertEqual(response.json(), {'status': 'dismissed'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.now += 301
        response = await self.client.post('/api/editor-insights/' + insight['id'] + '/speak',
                                           json={'session_id': 'editor', 'draft_revision': 1})
        self.assertEqual(response.status_code, 404)

    async def test_audio_failure_is_generic_and_point_remains_available(self):
        insight = await self.insight()
        async def failed(text, emotion):
            raise RuntimeError('private synthetic backend detail')
        self.engine.synthesize = failed
        response = await self.client.post('/api/editor-insights/' + insight['id'] + '/speak',
                                           json={'session_id': 'editor', 'draft_revision': 1})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {'detail': 'insight_speech_failed'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn(insight['id'], self.service._insights)

    async def test_oversized_request_and_non_json_rejected(self):
        response = await self.client.post('/api/editor-insights/analyze', content='x' * 130001)
        self.assertEqual(response.status_code, 413)
        response = await self.client.post('/api/editor-insights/state', content='{')
        self.assertEqual(response.status_code, 400)

    async def test_discuss_and_feedback_contract_are_no_store_and_do_not_synthesize(self):
        insight = await self.insight()
        path = '/api/editor-insights/' + insight['id']
        discuss = await self.client.post(path + '/discuss', json={
            'session_id': 'editor', 'draft_revision': 1, 'text': '我想保留这段。'})
        self.assertEqual(discuss.status_code, 200)
        self.assertEqual(discuss.headers['cache-control'], 'no-store')
        self.assertEqual(set(discuss.json()), {'text', 'insight_id', 'draft_revision'})
        self.assertEqual(discuss.json()['insight_id'], insight['id'])
        self.assertEqual(self.discussion_payloads[0]['draft']['body'], BODY)
        feedback = await self.client.post(path + '/feedback', json={
            'session_id': 'editor', 'draft_revision': 1, 'value': 'useful'})
        self.assertEqual(feedback.status_code, 200)
        self.assertEqual(feedback.headers['cache-control'], 'no-store')
        self.assertEqual(feedback.json(), {'status': 'feedback', 'value': 'useful',
                                           'stats': {'useful': 1, 'missed': 0, 'total': 1}})
        self.assertEqual(self.engine.calls, [])

    async def test_discuss_and_feedback_require_same_origin_and_exact_fields(self):
        insight = await self.insight()
        path = '/api/editor-insights/' + insight['id']
        valid = {'session_id': 'editor', 'draft_revision': 1}
        for route, addition in (('/discuss', {'text': '为什么？'}), ('/feedback', {'value': 'missed'})):
            for origin in ('https://testserver', 'http://elsewhere', ''):
                response = await self.client.post(path + route, headers={'Origin': origin},
                                                   json={**valid, **addition})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers['cache-control'], 'no-store')
            for payload in ({**valid, **addition, 'body': 'forged draft'}, valid):
                response = await self.client.post(path + route, json=payload)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.discussion_payloads, [])
        self.assertEqual(self.service._sessions['editor']['feedback'], {})

    async def test_discussion_sensitive_input_limits_and_generic_failure_do_not_fall_back(self):
        insight = await self.insight()
        path = '/api/editor-insights/' + insight['id'] + '/discuss'
        valid = {'session_id': 'editor', 'draft_revision': 1}
        for text in ('password=not-for-provider', 'x' * 1001, ' ', True):
            response = await self.client.post(path, json={**valid, 'text': text})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.discussion_payloads, [])
        async def failed(_):
            raise RuntimeError('private synthetic backend detail')
        self.service.discussion_resolver = failed
        response = await self.client.post(path, json={**valid, 'text': '为什么？'})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {'detail': 'insight_discussion_failed'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])
        self.assertEqual(self.engine.calls, [])

    async def test_discussion_limit_expiry_and_different_draft(self):
        insight = await self.insight()
        path = '/api/editor-insights/' + insight['id']
        valid = {'session_id': 'editor', 'draft_revision': 1}
        mismatch = await self.client.post(path + '/discuss', json={**valid, 'draft_revision': 2, 'text': '为什么？'})
        self.assertEqual(mismatch.status_code, 409)
        for _ in range(6):
            response = await self.client.post(path + '/discuss', json={**valid, 'text': '为什么？'})
            self.assertEqual(response.status_code, 200)
        response = await self.client.post(path + '/discuss', json={**valid, 'text': '再问一次'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {'detail': 'insight_discussion_limit'})
        self.now += 301
        for route, addition in (('/discuss', {'text': '为什么？'}), ('/feedback', {'value': 'useful'})):
            response = await self.client.post(path + route, json={**valid, **addition})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_feedback_amendment_invalid_value_and_oversized_discussion(self):
        insight = await self.insight()
        path = '/api/editor-insights/' + insight['id']
        valid = {'session_id': 'editor', 'draft_revision': 1}
        for value in ('missed', 'missed', 'useful'):
            response = await self.client.post(path + '/feedback', json={**valid, 'value': value})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['stats']['total'], 1)
        self.assertEqual(response.json()['stats'], {'useful': 1, 'missed': 0, 'total': 1})
        response = await self.client.post(path + '/feedback', json={**valid, 'value': 'train_forever'})
        self.assertEqual(response.status_code, 400)
        response = await self.client.post(path + '/discuss', content='x' * 8001)
        self.assertEqual(response.status_code, 413)

    async def test_real_analysis_resolver_exposes_failures_separately_from_quiet(self):
        responses = [
            ({'text': '{"action":"quiet"}', 'finish_reason': 'stop'}, 'quiet', 'no_salient_insight'),
            ({'text': '', 'finish_reason': 'stop'}, 'unavailable', 'analysis_empty_response'),
            ({'text': '{"action":"quiet"}', 'finish_reason': 'length'}, 'unavailable', 'analysis_output_truncated'),
            ({'text': 'PRIVATE_PROVIDER_BROKEN_RESPONSE', 'finish_reason': 'stop'}, 'unavailable', 'analysis_invalid_response'),
        ]
        calls = []
        async def complete_raw(messages, **options):
            calls.append((messages, options))
            return current
        self.service.llm.complete_raw = complete_raw
        self.service.resolver = self.service._resolve
        for number, (current, status, reason) in enumerate(responses):
            with self.subTest(reason=reason):
                session = 'api-output-' + str(number)
                await self.client.post('/api/editor-insights/state', json={
                    'session_id': session, 'draft_revision': 1, 'enabled': True})
                response = await self.client.post('/api/editor-insights/analyze', json={
                    'session_id': session, 'draft_revision': 1, 'enabled': True, 'title': '', 'body': BODY})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.json(), {'status': status, 'reason': reason, 'checks': 1})
                self.assertNotIn('PRIVATE_PROVIDER_BROKEN_RESPONSE', response.text)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(options['max_tokens'] == 6144 and options['max_attempts'] == 1
                            for _, options in calls))
        self.assertEqual(self.service._insights, {})
        self.assertEqual(self.engine.calls, [])

    async def test_real_discussion_resolver_returns_named_output_errors_without_history(self):
        insight = await self.insight()
        path = '/api/editor-insights/' + insight['id'] + '/discuss'
        responses = [
            ({'text': '{"text":"可解析的截断输出"}', 'finish_reason': 'length'}, 'insight_discussion_output_truncated'),
            ({'text': '', 'finish_reason': 'stop', 'reasoning': '{"text":"不能替代回答"}'}, 'insight_discussion_empty_response'),
            ({'text': 'PRIVATE_PROVIDER_BROKEN_RESPONSE', 'finish_reason': 'stop'}, 'insight_discussion_invalid_reply'),
        ]
        calls = []
        async def complete_raw(messages, **options):
            calls.append((messages, options))
            return current
        self.service.llm.complete_raw = complete_raw
        self.service.discussion_resolver = self.service._resolve_discussion
        for number, (current, reason) in enumerate(responses, 1):
            with self.subTest(reason=reason):
                response = await self.client.post(path, json={
                    'session_id': 'editor', 'draft_revision': 1, 'text': '为什么？'})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json(), {'detail': reason})
                self.assertEqual(response.headers['cache-control'], 'no-store')
                state = self.service._insights[insight['id']]
                self.assertEqual(state['discussion_history'], [])
                self.assertEqual(state['discussion_checks'], number)
                self.assertNotIn('PRIVATE_PROVIDER_BROKEN_RESPONSE', response.text)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(options['max_tokens'] == 6144 and options['max_attempts'] == 1
                            for _, options in calls))
        self.assertEqual(self.engine.calls, [])


if __name__ == '__main__':
    unittest.main()
