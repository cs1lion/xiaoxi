"""Isolated notes persistence, API and conflict tests. Never imports application singletons."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.notes import NoteStore
from server.assistant.api import create_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

class NotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-notes-')
        self.path = Path(self.temp.name)/'notes.db'
        self.store = NoteStore(self.path)
        app = FastAPI()
        app.include_router(create_router(SimpleNamespace(notes=self.store)))
        self.client = TestClient(app)
    def tearDown(self):
        self.client.close()
        self.temp.cleanup()
    def test_persistence_and_literal_search(self):
        note = self.store.create('周末计划', '查两个目的地，预算 100% 确认', '用户粘贴')
        reopened = NoteStore(self.path)
        self.assertEqual(reopened.get(note['id'])['source'], '用户粘贴')
        self.assertEqual(len(reopened.search('目的地')), 1)
        self.assertEqual(len(reopened.search('%')), 1)
        self.assertEqual(reopened.search('_'), [])
    def test_conflict_and_delete(self):
        note = self.store.create('标题', '原文')
        updated = self.store.update(note['id'], '标题', '新内容', 1)
        with self.assertRaises(ValueError): self.store.update(note['id'], '标题', '覆盖', 1)
        with self.assertRaises(ValueError): self.store.delete(note['id'], 1)
        self.assertEqual(self.store.get(note['id'])['body'], '新内容')
        self.store.delete(note['id'], updated['version'])
        self.assertEqual(self.store.search('新内容'), [])
        with self.assertRaises(KeyError): self.store.get(note['id'])
    def test_validation(self):
        for title, body in [('', 'a'), ('x', ''), ('x', 'a'*20001)]:
            with self.assertRaises(ValueError): self.store.create(title, body)
    def test_api_and_origin(self):
        denied = self.client.post('/api/notes', json={'title':'x','body':'y'}, headers={'Origin':'https://other.example'})
        self.assertEqual(denied.status_code, 403)
        created = self.client.post('/api/notes',json={'title':'计划','body':'原文'})
        self.assertEqual(created.status_code,200)
        note = created.json()
        self.assertEqual(self.client.get('/api/notes', params={'query':'原文'}).json()['items'][0]['id'],note['id'])
        read = self.client.get('/api/notes/'+note['id'])
        self.assertEqual(read.headers['cache-control'],'no-store')
        self.assertEqual(self.client.patch('/api/notes/'+note['id'],json={'title':'计划','body':'修改','version':1}).status_code,200)
        self.assertEqual(self.client.post('/api/notes/'+note['id']+'/delete',json={'version':1}).status_code,400)
        self.assertEqual(self.client.post('/api/notes/'+note['id']+'/delete',json={'version':2}).status_code,200)
        self.assertEqual(self.client.get('/api/notes/'+note['id']).status_code,404)

if __name__=='__main__': unittest.main(verbosity=2)
