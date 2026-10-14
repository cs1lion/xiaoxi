"""Exercise actual assistant routes with UI payloads, isolated DB, no listener or LLM."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.assistant.api import create_router
from server.assistant.store import AssistantStore

with tempfile.TemporaryDirectory(prefix='ui-contract-') as directory:
    store = AssistantStore(Path(directory) / 'assistant.db')
    app = FastAPI()
    app.include_router(create_router(SimpleNamespace(store=store)))
    try:
        with TestClient(app) as client:
            response = client.get('/api/preferences/proactivity')
            assert response.status_code == 200, response.text
            for zone in ('UTC', 'Asia/Shanghai', 'America/New_York'):
                response = client.patch('/api/preferences/proactivity', json={'timezone': zone})
                assert response.status_code == 200, response.text
                assert response.json()['timezone'] == zone, response.text
            task = client.post('/api/tasks', json={'title': 'UI contract only', 'request_id': 'ui-1'})
            assert task.status_code == 200, task.text
            task = task.json()
            progressed = client.patch('/api/tasks/' + task['id'], json={'status':'in_progress', 'next_action':'Choose two destinations', 'version':task['version']})
            assert progressed.status_code == 200 and progressed.json()['next_action'] == 'Choose two destinations', progressed.text
            stale = client.patch('/api/tasks/' + task['id'], json={'next_action':'stale overwrite', 'version':task['version']})
            assert stale.status_code == 400, stale.text
            task = progressed.json()
            linked = client.post('/api/reminders', json={'content':task['next_action'], 'task_id':task['id'], 'scheduled_at':2000000000, 'request_id':'linked-ui'})
            assert linked.status_code == 200, linked.text
            done = client.patch('/api/tasks/' + task['id'], json={'status': 'done', 'version': task['version']})
            assert done.status_code == 200 and done.json()['status'] == 'done', done.text
            assert store.list_reminders()[0]['status'] == 'cancelled'
            reopened = client.patch('/api/tasks/' + task['id'], json={'status':'open', 'version':done.json()['version']})
            assert reopened.status_code == 200, reopened.text
            assert store.list_reminders()[0]['status'] == 'cancelled', 'Reopen must not reactivate old reminders'
            reminder = client.post('/api/reminders', json={'content': 'UI reminder only', 'scheduled_at': 2000000000, 'request_id': 'ui-2'})
            assert reminder.status_code == 200, reminder.text
            reminder = reminder.json()
            cancel = client.post('/api/reminders/' + reminder['id'] + '/cancel', json={})
            assert cancel.status_code == 200 and cancel.json()['status'] == 'cancelled', cancel.text
            for name in ('tasks', 'reminders', 'notifications'):
                response = client.get('/api/' + name)
                assert response.status_code == 200 and isinstance(response.json()['items'], list), response.text
            denied = client.post('/api/tasks', json={'title': 'denied'}, headers={'Origin': 'https://untrusted.example'})
            assert denied.status_code == 403, denied.text
        print('PASS: real router + isolated SQLite: preferences, task create/complete, reminder create/cancel, list shapes, origin guard; no production writes')
    finally:
        store.close()
