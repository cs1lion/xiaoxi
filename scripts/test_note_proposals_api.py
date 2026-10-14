"""Isolated note preview HTTP contract; no application or model bootstrap."""

import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

if __package__:
    from .test_note_proposals import NoteStore, NoteProposalService, _PACKAGE, _ROOT
else:
    from test_note_proposals import NoteStore, NoteProposalService, _PACKAGE, _ROOT

_spec = importlib.util.spec_from_file_location(_PACKAGE + '.note_proposals_api',
                                              _ROOT / 'note_proposals_api.py')
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
create_note_proposals_router = _module.create_note_proposals_router


class Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='aqi-note-proposal-http-')
        self.addCleanup(temp.cleanup)
        self.store = NoteStore(Path(temp.name) / 'notes.db')
        self.note = self.store.create('合成标题', '合成原稿第一段。\n第二段。', '合成来源')
        self.now = 1000.0
        self.proposals = NoteProposalService(self.store, clock=lambda: self.now)
        app = FastAPI()
        app.include_router(create_note_proposals_router(SimpleNamespace(note_proposals=self.proposals)))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def propose(self):
        return self.proposals.propose(self.note['id'], 1, '修改标题', '修改正文', '把第一段写短一些。',
                                      thread_id='synthetic-original-thread')

    @staticmethod
    def url(candidate):
        return '/api/note-proposals/' + candidate['id']

    def test_get_list_revise_and_one_time_confirm_preserve_old_snapshot(self):
        candidate = self.propose()
        url = self.url(candidate)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json(), candidate)
        pending = self.client.get('/api/notes/' + self.note['id'] + '/proposals')
        self.assertEqual(pending.json(), {'items': [candidate]})
        self.assertEqual(pending.headers['cache-control'], 'no-store')
        revised = self.client.patch(url, json={'expected_version': 1, 'title': '更短的标题', 'body': '更短的正文'})
        self.assertEqual(revised.status_code, 200)
        self.assertEqual(revised.headers['cache-control'], 'no-store')
        preview = revised.json()
        self.assertEqual(preview['version'], 2)
        self.assertEqual(preview['base_note_version'], 1)
        self.assertEqual(preview['original_body'], self.note['body'])
        self.assertEqual(preview['thread_id'], 'synthetic-original-thread')
        self.assertEqual(self.store.get(self.note['id']), self.note)
        self.assertEqual(self.client.post(url + '/confirm', json={'expected_version': 1}).status_code, 409)
        saved = self.client.post(url + '/confirm', json={'expected_version': 2})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.headers['cache-control'], 'no-store')
        self.assertEqual(saved.json()['version'], 2)
        self.assertEqual(saved.json()['body'], '更短的正文')
        self.assertEqual(self.store.get_version(self.note['id'], 1), self.note)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url + '/confirm', json={'expected_version': 2}).status_code, 404)

    def test_conflict_has_no_implicit_refresh_and_requires_a_new_proposal(self):
        candidate = self.propose()
        url = self.url(candidate)
        changed = self.store.update(self.note['id'], '并发标题', '并发正文', 1)
        self.assertEqual(self.client.post(url + '/confirm', json={'expected_version': 1}).status_code, 409)
        self.assertEqual(self.client.patch(url, json={'expected_version': 1, 'title': '试图覆盖', 'body': '覆盖正文'}).status_code, 409)
        self.assertEqual(self.client.get(url).json(), candidate)
        self.assertEqual(self.store.get(self.note['id']), changed)
        self.assertEqual(len(self.store.history(self.note['id'])), 2)

    def test_unknown_fields_and_wrong_types_cannot_retarget_or_change_provenance(self):
        candidate = self.propose()
        url = self.url(candidate)
        for field, value in [('note_id', 'forged'), ('base_note_version', 2), ('thread_id', 'forged'),
                             ('source_text', '伪造来源'), ('original_body', '伪造原稿'), ('source', 'tool_verified')]:
            with self.subTest(field=field):
                revised = self.client.patch(url, json={'expected_version': 1, 'title': '标题', 'body': '正文', field: value})
                self.assertEqual(revised.status_code, 400)
                self.assertEqual(self.client.post(url + '/confirm', json={'expected_version': 1, field: value}).status_code, 400)
                self.assertEqual(self.client.post(url + '/reject', json={'expected_version': 1, field: value}).status_code, 400)
        for value in [[], None, {'expected_version': True, 'title': '标题', 'body': '正文'},
                      {'expected_version': 1, 'title': [], 'body': '正文'},
                      {'expected_version': 1, 'title': '标题', 'body': 3},
                      {'expected_version': 1, 'title': '标题', 'body': 'x\x00y'},
                      {'expected_version': 1, 'title': '标题', 'body': '字' * 20001},
                      {'expected_version': 1, 'title': '标题'}]:
            with self.subTest(value=value):
                self.assertEqual(self.client.patch(url, json=value).status_code, 400)
        self.assertEqual(self.client.patch(url, content='{invalid json').status_code, 400)
        self.assertEqual(self.proposals.get(candidate['id']), candidate)
        self.assertEqual(self.store.get(self.note['id']), self.note)

    def test_origin_rejection_includes_reads_and_does_not_consume_candidate(self):
        candidate = self.propose()
        url = self.url(candidate)
        for origin in ['https://evil.example', 'https://testserver', 'null', 'http://[']:
            headers = {'Origin': origin}
            self.assertEqual(self.client.get(url, headers=headers).status_code, 403)
            self.assertEqual(self.client.get('/api/notes/' + self.note['id'] + '/proposals', headers=headers).status_code, 403)
            self.assertEqual(self.client.patch(url, headers=headers,
                json={'expected_version': 1, 'title': '标题', 'body': '正文'}).status_code, 403)
            self.assertEqual(self.client.post(url + '/confirm', headers=headers, json={'expected_version': 1}).status_code, 403)
            self.assertEqual(self.client.post(url + '/reject', headers=headers, json={'expected_version': 1}).status_code, 403)
        self.assertEqual(self.client.get(url, headers={'Origin': 'http://testserver'}).status_code, 200)
        self.assertEqual(self.proposals.get(candidate['id']), candidate)
        self.assertEqual(self.store.get(self.note['id']), self.note)

    def test_reject_expire_delete_and_missing_note(self):
        candidate = self.propose()
        rejected = self.client.post(self.url(candidate) + '/reject', json={'expected_version': 1})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.headers['cache-control'], 'no-store')
        self.assertNotIn('original_body', rejected.json())
        self.assertEqual(self.client.get(self.url(candidate)).status_code, 404)
        candidate = self.propose()
        self.now = 1600
        self.assertEqual(self.client.get(self.url(candidate)).status_code, 404)
        candidate = self.propose()
        self.store.delete(self.note['id'], 1)
        self.assertEqual(self.client.get(self.url(candidate)).status_code, 404)
        self.assertEqual(self.client.post(self.url(candidate) + '/confirm', json={'expected_version': 1}).status_code, 404)
        self.assertEqual(self.client.get('/api/notes/' + self.note['id'] + '/proposals').status_code, 404)
        self.assertEqual(self.store.search(), [])

    def test_callback_failure_still_returns_saved_note_and_consumes_token(self):
        def fail(candidate, saved):
            raise RuntimeError('synthetic receipt failure')
        self.proposals._on_confirm = fail
        candidate = self.propose()
        response = self.client.post(self.url(candidate) + '/confirm', json={'expected_version': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version'], 2)
        self.assertEqual(response.json()['body'], candidate['body'])
        self.assertIn('companion_warnings', response.json())
        self.assertEqual(self.client.post(self.url(candidate) + '/confirm', json={'expected_version': 1}).status_code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
