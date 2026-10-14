"""Isolated HTTP integration tests: never import application/live memory DB."""
import asyncio
import tempfile
import sys
import unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.service import AssistantService
from server.assistant.api import create_router

class MockLLM:
    offline = True

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-assistant-api-')
        self.service = AssistantService(Path(self.temp.name), MockLLM())
        app = FastAPI()
        app.include_router(create_router(self.service))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.service.close()
        self.temp.cleanup()

    def test_draft_template_label_and_shared_rules(self):
        captured = []
        class DraftLLM:
            offline = False
            async def complete(self, messages, **kwargs):
                captured.extend(messages)
                return '{"action":"final","text":"本周工作：【实际进展】。"}'
        self.service.llm = DraftLLM()
        context = {'response_policy': self.service.context_broker.build('帮我写周报')['response_policy']}
        result = asyncio.run(self.service._decide({'user_text': '帮我写周报', 'context': context}))
        self.assertTrue(result['text'].startswith('模板（待填写）'))
        self.assertIn('任务存在不等于完成', captured[0]['content'])
        self.assertIn('针对当前问题', captured[0]['content'])

    def test_http_task_reminder_inbox(self):
        task = self.client.post('/api/tasks', json={'title': '周报', 'request_id': 'task-1'}).json()
        self.assertEqual(self.client.get('/api/tasks').json()['items'][0]['id'], task['id'])
        import time
        reminder = self.client.post('/api/reminders', json={'content': '提交周报', 'scheduled_at': time.time(), 'task_id': task['id']}).json()
        self.service.store.scan_due()
        self.assertEqual(len(self.client.get('/api/notifications').json()['items']), 1)
        notice = self.client.get('/api/notifications').json()['items'][0]
        self.assertEqual(self.client.post(f"/api/notifications/{notice['id']}/ack", json={}).status_code, 200)
        response = self.client.patch(f"/api/tasks/{task['id']}", json={'status': 'done', 'version': task['version']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'done')

    def test_origin_guard_and_offline(self):
        self.assertEqual(self.client.post('/api/tasks', json={'title': 'x'}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        result = self.client.post('/api/assistant/run', json={'text': '提醒我'}).json()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.client.get('/api/tasks').json()['items'], [])

    def test_confirmation_api_loop(self):
        decisions = [
            {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '周报'}},
            {'action': 'final', 'text': '已创建周报任务。'},
        ]
        async def model(_):
            return decisions.pop(0)
        self.service.loop.model = model
        result = self.client.post('/api/assistant/run', json={'text': '创建周报任务'}).json()
        self.assertEqual(result['status'], 'waiting_confirmation')
        self.assertEqual(self.service.store.list_tasks(), [])
        path = f"/api/assistant/runs/{result['run_id']}/confirm"
        body = {'confirmation_id': result['confirmation']['id'], 'approve': True}
        done = self.client.post(path, json=body)
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()['receipts'][0]['status'], 'succeeded')
        self.assertEqual(len(self.service.store.list_tasks()), 1)
        self.assertEqual(self.client.post(path, json=body).status_code, 409)

    def test_preferences_api_delete_and_resume_refresh(self):
        evidence = self.client.post('/api/interaction/evidence', json={'text': '技术问题请详细解释'}).json()
        inspect = self.client.get(f"/api/interaction/evidence/{evidence['id']}")
        self.assertEqual(inspect.json()['text'], '技术问题请详细解释')
        self.assertEqual(inspect.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get(f"/api/interaction/evidence/{evidence['id']}", headers={'Origin': 'https://evil.example'}).status_code, 403)
        response = self.client.post('/api/interaction/preferences', json={
            'dimension': 'detail', 'value': 'detailed', 'evidence_ids': [evidence['id']],
            'scope': {'session_id': 's1'}})
        self.assertEqual(response.status_code, 200)
        contexts = []
        async def model(payload):
            contexts.append(payload['context'])
            if len(contexts) == 1:
                return {'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'test'}}
            return {'action': 'final', 'text': '完成'}
        self.service.loop.model = model
        result = self.client.post('/api/assistant/run', json={'text': '创建任务', 'session_id': 's1'}).json()
        self.assertEqual(contexts[0]['response_policy']['length'], 'detailed')
        removed = self.client.post(f"/api/interaction/evidence/{evidence['id']}/delete", json={})
        self.assertEqual(removed.status_code, 200)
        done = self.client.post(f"/api/assistant/runs/{result['run_id']}/confirm", json={
            'confirmation_id': result['confirmation']['id'], 'approve': True})
        self.assertEqual(done.status_code, 200)
        self.assertEqual(contexts[1]['response_policy']['length'], 'short')
        self.assertEqual(self.client.get('/api/interaction/preferences').json()['items'], [])

if __name__ == '__main__':
    unittest.main()
