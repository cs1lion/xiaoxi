"""Temporary DBs only; no application bootstrap, live data or cloud providers."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.service import AssistantService
from server.interaction.threads_api import create_threads_router

class Offline:
    offline = True

class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = AssistantService(Path(self.temp.name), Offline())
        app = FastAPI(); app.include_router(create_threads_router(self.service))
        self.client = TestClient(app)
        self.thread = self.service.threads.create('演示保底')
        self.id = self.thread['id']

    def tearDown(self):
        self.client.close(); self.service.close(); self.temp.cleanup()

    def restart(self):
        self.client.close()
        self.service.close()
        self.service = AssistantService(Path(self.temp.name), Offline())
        app = FastAPI(); app.include_router(create_threads_router(self.service))
        self.client = TestClient(app)

    def test_api_selection_and_context_survive_restart_and_unlink(self):
        self.assertEqual(self.client.post('/api/threads/selection',
            json={'thread_id': self.id}).json(), {'thread_id': self.id})
        self.restart()
        self.assertEqual(self.client.get('/api/threads/selection').json(), {'thread_id': self.id})
        policy = self.service.context_broker.build('继续昨天那个')['response_policy']
        self.assertEqual(policy['companion_thread']['id'], self.id)
        self.assertEqual(self.service.participation.current(), self.thread)
        self.assertEqual(self.client.post('/api/threads/selection',
            json={'thread_id': None}).json(), {'thread_id': None})
        self.restart()
        self.assertEqual(self.client.get('/api/threads/selection').json(), {'thread_id': None})
        self.assertNotIn('companion_thread', self.service.context_broker.build('继续')['response_policy'])
        self.assertEqual(self.service.threads.get(self.id), self.thread)

    def test_api_explicit_sources_conflicts_and_delete(self):
        base = '/api/threads/' + self.id
        self.assertEqual(self.client.post('/api/threads/selection',json={'thread_id': self.id}).status_code,200)
        r = self.client.post(base+'/events',json={'expected_revision':1,'kind':'decision','text':'保留文字入口'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['events'][0]['source'],'user_stated')
        self.assertEqual(self.client.patch(base,json={'expected_revision':1,'status':'done'}).status_code,409)
        self.assertEqual(self.client.post(base+'/events',json={'expected_revision':2,'kind':'tool_receipt','text':'成功'}).status_code,400)
        self.assertEqual(self.client.post(base+'/events',json={'expected_revision':2,'kind':'progress','text':'成功','source':'tool_verified'}).status_code,400)
        self.assertEqual(self.client.post(base+'/delete',json={'expected_revision':2},headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.post(base+'/delete',json={'expected_revision':2}).status_code,200)
        self.assertEqual(self.client.get(base).status_code,404)
        self.assertIsNone(self.client.get('/api/threads/selection').json()['thread_id'])

    def test_broker_provenance_and_disable(self):
        self.service.participation.select(self.id)
        self.service.threads.add_event(self.id,expected_revision=1,kind='progress',text='我说演示成功了')
        policy = self.service.context_broker.build('昨天那件事')['response_policy']
        evidence = policy['companion_thread']['events'][0]
        self.assertEqual(evidence['source'],'user_stated')
        self.assertFalse(policy['tool_authorization'])
        self.assertNotIn('companion_thread',self.service.context_broker.build('昨天那件事',enabled=False)['response_policy'])
        self.assertEqual(self.service.context_broker.build('先听我说')['response_policy']['participation_act'],'listen')
        self.service.threads.delete(self.id,expected_revision=2)
        self.assertNotIn('companion_thread',self.service.context_broker.build('继续')['response_policy'])

    def test_receipts_no_generated_prose_no_duplicate_no_resurrection(self):
        self.service.participation.select(self.id)
        result={'run_id':'run1','text':'我们成功拯救世界了','receipts':[{'tool':'task.create','status':'succeeded','data':{'private':'not copied'}}]}
        self.service.participation.receipts(self.id,result)
        self.service.participation.receipts(self.id,result)
        thread=self.service.threads.get(self.id)
        self.assertEqual(len(thread['events']),1)
        self.assertNotIn('private',str(thread)); self.assertNotIn('拯救',str(thread))
        self.assertEqual(thread['events'][0]['source'],'tool_verified')
        self.service.threads.delete(self.id,expected_revision=thread['revision'])
        self.assertIsNone(self.service.participation.selected_id)
        self.assertTrue(self.service.participation.receipts(self.id,result))
        self.assertIsNone(self.service.threads.get_selection())
        with self.assertRaises(KeyError): self.service.threads.get(self.id)

    def test_confirmation_stays_bound_to_original_thread(self):
        async def scenario():
            self.service.participation.select(self.id)
            decisions=iter([{'action':'tool_call','tool':'task.create','arguments':{'title':'彩排'}}, {'action':'final','text':'保存了'}])
            async def decide(_): return next(decisions)
            self.service.loop.model=decide
            result=await self.service.start('帮我创建任务：彩排','session-a')
            self.assertEqual(result['status'],'waiting_confirmation')
            self.assertEqual(self.service.threads.get(self.id)['events'],[])
            other=self.service.threads.create('其他项目');self.service.participation.select(other['id'])
            done=await self.service.resume(result['run_id'],result['confirmation']['id'],True)
            self.assertEqual(done['receipts'][0]['status'],'succeeded')
            self.assertEqual(len(self.service.threads.get(self.id)['events']),1)
            self.assertEqual(self.service.threads.get(other['id'])['events'],[])
        asyncio.run(scenario())

    def test_delete_while_waiting_does_not_recreate(self):
        async def scenario():
            self.service.participation.select(self.id)
            decisions=iter([{'action':'tool_call','tool':'task.create','arguments':{'title':'彩排'}}, {'action':'final','text':'保存了'}])
            async def decide(_): return next(decisions)
            self.service.loop.model=decide
            result=await self.service.start('帮我创建任务：彩排','session-a')
            self.service.threads.delete(self.id,expected_revision=1)
            await self.service.resume(result['run_id'],result['confirmation']['id'],True)
            self.assertEqual(self.service.threads.list_threads(),[])
            # Deleting companion notes isn't permission to delete/cancel a task.
            self.assertEqual(len(self.service.store.list_tasks()),1)
        asyncio.run(scenario())

if __name__=='__main__': unittest.main(verbosity=2)
