"""In-process history routes, private module imports and temporary DBs only."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


_ROOT = Path(__file__).resolve().parents[1] / 'server/interaction'
_PACKAGE = '_standalone_thread_history_api'
_package = types.ModuleType(_PACKAGE)
_package.__path__ = [str(_ROOT)]
sys.modules[_PACKAGE] = _package
for _name in ('threads', 'threads_api'):
    _spec = importlib.util.spec_from_file_location(
        _PACKAGE + '.' + _name, _ROOT / (_name + '.py'))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
ThreadStore = sys.modules[_PACKAGE + '.threads'].ThreadStore
RevisionConflict = sys.modules[_PACKAGE + '.threads'].RevisionConflict
create_threads_router = sys.modules[_PACKAGE + '.threads_api'].create_threads_router


class ThreadHistoryAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ThreadStore(Path(self.temp.name) / 'history.sqlite')
        self.addCleanup(self.store.close)
        self.thread = self.store.create('Synthetic history test')
        self.id = self.thread['id']
        self.base = '/api/threads/' + self.id
        self.client = self.make_client(self.store)
        self.addCleanup(self.client.close)

    @staticmethod
    def make_client(store):
        app = FastAPI()
        app.include_router(create_threads_router(types.SimpleNamespace(threads=store)))
        return TestClient(app)

    def add_manual(self):
        response = self.client.post(self.base + '/events', json={
            'expected_revision': 1, 'kind': 'decision', 'text': 'Keep the text entry'})
        self.assertEqual(response.status_code, 200)
        return response.json()['events'][0]

    def history_path(self, event_id):
        return self.base + '/events/' + event_id + '/history'

    def test_manual_revision_history_keeps_original_and_previous_version(self):
        event = self.add_manual()
        revised = self.client.patch(self.base + '/events/' + event['id'], json={
            'expected_revision': 2, 'text': 'Keep text entry and keyboard navigation'})
        self.assertEqual(revised.status_code, 200)
        response = self.client.get(self.history_path(event['id']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        first, second = response.json()['items']
        self.assertEqual((first['version'], first['previous_version']), (1, None))
        self.assertEqual((second['version'], second['previous_version']), (2, 1))
        self.assertEqual(first['text'], 'Keep the text entry')
        self.assertEqual(second['text'], 'Keep text entry and keyboard navigation')
        for version in (first, second):
            self.assertEqual(version['source'], 'user_stated')
            self.assertEqual(version['evidence']['state'], 'manual')
            self.assertNotIn('request_text', version['evidence'])

    def test_confirmed_request_and_fixed_note_reference_survive_restart(self):
        evidence = {'state': 'user_confirmed', 'request_text': '记个决定：保留文字入口',
                    'session_id': 'synthetic-origin-session', 'captured_at': 1000}
        refs = [{'type': 'note', 'id': 'synthetic-note', 'version': 2}]
        saved = self.store.add_event(self.id, expected_revision=1, kind='decision',
            text='保留文字入口和键盘操作', source_id='proposal:synthetic',
            evidence=evidence, object_refs=refs)
        event = saved['events'][0]
        self.client.close()
        self.store.close()
        reopened = ThreadStore(Path(self.temp.name) / 'history.sqlite')
        self.addCleanup(reopened.close)
        client = self.make_client(reopened)
        self.addCleanup(client.close)
        response = client.get(self.history_path(event['id']))
        self.assertEqual(response.status_code, 200)
        version, = response.json()['items']
        self.assertEqual(version['evidence'], evidence)
        self.assertEqual(version['object_refs'], refs)
        self.assertEqual(version['text'], '保留文字入口和键盘操作')
        self.assertNotEqual(version['created_at'], evidence['captured_at'])

    def test_history_read_does_not_change_selection_or_record_revision(self):
        event = self.add_manual()
        self.store.select_thread(self.id)
        before = self.store.get(self.id)
        for _ in range(2):
            response = self.client.get(self.history_path(event['id']))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.store.get(self.id), before)
        self.assertEqual(self.store.get_selection(), self.id)

    def test_history_cannot_read_another_thread_or_missing_event(self):
        event = self.add_manual()
        other = self.store.create('Another synthetic thread')
        paths = [self.base + '/events/missing/history',
                 '/api/threads/' + other['id'] + '/events/' + event['id'] + '/history',
                 '/api/threads/missing/events/' + event['id'] + '/history']
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()['detail'], 'thread_or_event_not_found')

    def test_deleted_thread_history_is_unavailable_after_restart(self):
        event = self.add_manual()
        self.store.delete(self.id, expected_revision=2)
        self.assertEqual(self.client.get(self.history_path(event['id'])).status_code, 404)
        self.client.close()
        self.store.close()
        reopened = ThreadStore(Path(self.temp.name) / 'history.sqlite')
        self.addCleanup(reopened.close)
        client = self.make_client(reopened)
        self.addCleanup(client.close)
        self.assertEqual(client.get(self.history_path(event['id'])).status_code, 404)

    def test_public_writes_cannot_forge_evidence_or_object_references(self):
        payload = {'expected_revision': 1, 'kind': 'decision', 'text': 'Synthetic'}
        for field, value in [('source', 'tool_verified'),
                             ('evidence', {'state': 'user_confirmed', 'request_text': 'Fake'}),
                             ('object_refs', [{'type': 'note', 'id': 'fake', 'version': 1}])]:
            with self.subTest(field=field):
                response = self.client.post(self.base + '/events', json={**payload, field: value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['detail'], 'unknown_fields')
        event = self.add_manual()
        response = self.client.patch(self.base + '/events/' + event['id'], json={
            'expected_revision': 2, 'text': 'Synthetic', 'evidence': {'state': 'tool_verified'}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.get(self.id)['revision'], 2)

    def test_history_uses_existing_validation_and_conflict_error_mapping(self):
        event = self.add_manual()
        for error, status in ((ValueError('synthetic_invalid'), 400),
                              (RevisionConflict('synthetic_conflict'), 409)):
            with self.subTest(status=status), patch.object(self.store, 'event_history', side_effect=error):
                response = self.client.get(self.history_path(event['id']))
                self.assertEqual(response.status_code, status)


if __name__ == '__main__':
    unittest.main(verbosity=2)
