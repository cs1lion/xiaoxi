"""Real in-process materials routes/facade; guarded temporary stores only."""
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.object(dotenv, 'load_dotenv', return_value=False), patch.dict(os.environ, {
        'DB_PATH': ':memory:', 'OPENAI_API_KEY': '', 'DEEPSEEK_API_KEY': '',
        'STEP_API_KEY': '', 'MEMORY_EVAL_API_KEY': ''}), patch.object(
        sqlite3, 'connect', side_effect=AssertionError('Imports must not open a database')):
    from server.assistant.service import AssistantService
    from server.interaction.threads_api import create_threads_router


class ThreadMaterialsAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-thread-material-api-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        original_connect = sqlite3.connect

        def guarded_connect(path, *args, **kwargs):
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(self.directory):
                raise AssertionError('Test attempted to open a non-temporary database')
            return original_connect(path, *args, **kwargs)

        self.guard = patch.object(sqlite3, 'connect', side_effect=guarded_connect)
        self.guard.start(); self.addCleanup(self.guard.stop)
        network_guard = patch.object(socket, 'create_connection', side_effect=AssertionError('No network'))
        network_guard.start(); self.addCleanup(network_guard.stop)
        env_guard = patch.object(dotenv, 'load_dotenv', side_effect=AssertionError('No .env reads'))
        env_guard.start(); self.addCleanup(env_guard.stop)
        self.service = self.make_service()
        self.addCleanup(self.service.close)
        self.thread = self.service.threads.create('Synthetic matter')
        self.id = self.thread['id']; self.base = '/api/threads/' + self.id + '/materials'
        self.note = self.service.notes.create('Version one', 'Synthetic original body')
        self.note2 = self.service.notes.update(self.note['id'], 'Version two', 'Synthetic revised body', 1)
        self.other_note = self.service.notes.create('Other material', 'A different body')
        self.service.notes.select(self.other_note['id'], version=1,
            expected_revision=self.service.notes.selection()['revision'])
        self.selection = self.service.notes.selection()
        self.client = self.make_client(self.service)
        self.addCleanup(self.client.close)

    @staticmethod
    def make_client(service):
        app = FastAPI(); app.include_router(create_threads_router(service))
        return TestClient(app)

    def make_service(self):
        service = AssistantService.__new__(AssistantService)
        try:
            service.__init__(self.directory, SimpleNamespace(offline=True))
        except BaseException:
            for name in ('threads', 'preference_store', 'runtime', 'store'):
                store = getattr(service, name, None)
                if store is not None:
                    store.close()
            raise
        return service

    def link(self, *, revision=None, note_id=None, version=1, purpose='reference'):
        if revision is None: revision = self.service.threads.get(self.id)['revision']
        return self.client.post(self.base, json={'expected_revision':revision,
            'note_id':note_id or self.note['id'], 'version':version, 'purpose':purpose})

    def saved_link(self, **kwargs):
        response = self.link(**kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['items'][-1]

    def test_empty_list_is_no_store_and_does_not_read_note_body(self):
        before = self.service.threads.get(self.id)
        with patch.object(self.service.notes, 'get_version', side_effect=AssertionError('No body reads')):
            response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json(), {'thread_id':self.id, 'revision':1, 'items':[]})
        self.assertEqual(self.service.threads.get(self.id), before)

    def test_link_records_exact_old_version_purpose_and_explicit_matter(self):
        link = self.saved_link(purpose='draft')
        self.assertEqual((link['thread_id'], link['note_id'], link['version'], link['purpose']),
                         (self.id, self.note['id'], 1, 'draft'))
        self.assertEqual((link['status'], link['title'], link['current_version']),
                         ('stale', 'Version one', 2))
        response = self.client.get(self.base)
        self.assertEqual(response.json()['revision'], 2)
        self.assertEqual(self.service.notes.selection(), self.selection)
        self.assertIsNone(self.service.participation.selected_id)
        self.assertEqual(self.service.threads.get(self.id)['events'], [])

    def test_edit_version_and_purpose_does_not_update_note_or_complete_matter(self):
        link = self.saved_link(purpose='draft')
        response = self.client.patch(self.base + '/' + link['id'], json={
            'expected_revision':2, 'version':2, 'purpose':'artifact'})
        self.assertEqual(response.status_code, 200, response.text)
        updated, = response.json()['items']
        self.assertEqual((updated['version'], updated['purpose'], updated['status']), (2, 'artifact', 'selected'))
        self.assertEqual(self.service.threads.get(self.id)['status'], 'discussing')
        self.assertEqual(self.service.notes.get_version(self.note['id'], 1), self.note)
        self.assertEqual(self.service.notes.get(self.note['id']), self.note2)
        self.assertEqual(self.service.notes.selection(), self.selection)

    def test_same_fixed_version_cannot_be_relinked_with_different_purpose(self):
        link = self.saved_link()
        before = self.client.get(self.base).json()
        response = self.link(purpose='artifact')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.base).json(), before)
        self.assertEqual(self.client.get(self.base).json()['items'][0]['id'], link['id'])

    def test_one_current_draft_requires_explicit_change_of_previous_use(self):
        link = self.saved_link(purpose='draft')
        response = self.link(note_id=self.other_note['id'], purpose='draft')
        self.assertEqual(response.status_code, 400)
        changed = self.client.patch(self.base + '/' + link['id'], json={
            'expected_revision':2, 'purpose':'reference'})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.saved_link(note_id=self.other_note['id'], purpose='draft')
        self.assertEqual([item['purpose'] for item in self.client.get(self.base).json()['items']].count('draft'), 1)

    def test_revision_conflicts_for_all_writes_do_not_mutate_materials(self):
        link = self.saved_link()
        before = self.client.get(self.base).json()
        responses = [self.link(revision=1, note_id=self.other_note['id']),
            self.client.patch(self.base + '/' + link['id'], json={'expected_revision':1, 'purpose':'artifact'}),
            self.client.post(self.base + '/' + link['id'] + '/remove', json={'expected_revision':1})]
        for response in responses: self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get(self.base).json(), before)

    def test_links_are_isolated_to_named_matter_and_missing_ids_are_404(self):
        link = self.saved_link()
        other = self.service.threads.create('Other matter')
        base = '/api/threads/' + other['id'] + '/materials'
        self.assertEqual(self.client.get(base).json()['items'], [])
        self.assertEqual(self.client.patch(base + '/' + link['id'], json={
            'expected_revision':1, 'purpose':'artifact'}).status_code, 404)
        self.assertEqual(self.client.post(base + '/' + link['id'] + '/remove', json={
            'expected_revision':1}).status_code, 404)
        self.assertEqual(self.client.get('/api/threads/missing/materials').status_code, 404)

    def test_restart_persists_link_and_unlink_preserves_note_versions_and_selection(self):
        link = self.saved_link()
        self.client.close(); self.service.close()
        reopened = self.make_service()
        self.addCleanup(reopened.close)
        client = self.make_client(reopened); self.addCleanup(client.close)
        self.assertEqual(client.get(self.base).json()['items'][0]['id'], link['id'])
        response = client.post(self.base + '/' + link['id'] + '/remove', json={'expected_revision':2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['items'], [])
        self.assertEqual(reopened.notes.get_version(self.note['id'], 1), self.note)
        self.assertEqual(reopened.notes.get_version(self.note['id'], 2), self.note2)
        self.assertEqual(reopened.notes.selection(), self.selection)

    def test_deleted_note_link_reports_missing_and_remains_removable(self):
        link = self.saved_link()
        self.service.notes.delete(self.note['id'], 2)
        response = self.client.get(self.base)
        missing, = response.json()['items']
        self.assertEqual(missing['id'], link['id'])
        self.assertEqual(missing['status'], 'missing')
        self.assertNotIn('body', missing)
        self.assertEqual(self.client.post(self.base + '/' + link['id'] + '/remove',
            json={'expected_revision':2}).status_code, 200)

    def test_existing_version_is_required_and_client_cannot_change_note_identity(self):
        before = self.service.threads.get(self.id)
        self.assertEqual(self.link(version=99).status_code, 404)
        self.assertEqual(self.link(note_id='missing').status_code, 404)
        self.assertEqual(self.service.threads.get(self.id), before)
        link = self.saved_link()
        response = self.client.patch(self.base + '/' + link['id'], json={
            'expected_revision':2, 'note_id':self.other_note['id'], 'purpose':'artifact'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.base).json()['items'][0]['note_id'], self.note['id'])

    def test_routes_reject_forged_fields_bad_values_and_cross_origin_writes(self):
        payload = {'expected_revision':1, 'note_id':self.note['id'], 'version':1, 'purpose':'reference'}
        for field, value in [('source','tool_verified'), ('thread_id','other'), ('evidence',{'state':'manual'})]:
            with self.subTest(field=field):
                self.assertEqual(self.client.post(self.base, json={**payload,field:value}).status_code, 400)
        for field, value in [('purpose','inferred'), ('version',True), ('expected_revision',True)]:
            with self.subTest(field=field):
                self.assertEqual(self.client.post(self.base, json={**payload,field:value}).status_code, 400)
        self.assertEqual(self.client.post(self.base, json=payload,
            headers={'Origin':'https://outside.test'}).status_code, 403)
        self.assertEqual(self.client.get(self.base).json()['items'], [])

    def test_twelve_link_limit_preserves_existing_associations(self):
        for index in range(12):
            note = self.service.notes.create('Material ' + str(index), 'Synthetic body')
            self.saved_link(note_id=note['id'])
        before = self.client.get(self.base).json()
        self.assertEqual(self.link(note_id=self.other_note['id']).status_code, 400)
        self.assertEqual(self.client.get(self.base).json(), before)

    def test_explicit_management_is_available_with_model_memory_disabled(self):
        self.service.memory_manager = SimpleNamespace(enabled=False)
        with patch.object(self.service.notes, 'get_version', side_effect=AssertionError('No note body read')):
            link = self.saved_link()
            response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'][0]['id'], link['id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
