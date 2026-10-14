"""Isolated candidate HTTP and assistant routing integration."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.assistant.service import AssistantService
from server.interaction.threads_api import create_threads_router
from server.interaction.proposals_api import create_proposals_router

class Offline: offline=True

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=AssistantService(Path(self.tmp.name),Offline())
        app=FastAPI();app.include_router(create_threads_router(self.s));app.include_router(create_proposals_router(self.s))
        self.c=TestClient(app);self.t=self.s.threads.create('演示');self.base='/api/threads/'+self.t['id']
    def tearDown(self):
        self.c.close();self.s.close();self.tmp.cleanup()
    def propose(self):
        r=self.c.post(self.base+'/proposals',json={'utterance':'记个决定：保留文字入口'})
        self.assertEqual(r.status_code,200);return r.json()
    def test_preview_revise_confirm_one_time(self):
        p=self.propose();url='/api/thread-proposals/'+p['id']
        self.assertEqual(self.s.threads.get(self.t['id'])['events'],[])
        q=self.c.patch(url,json={'expected_version':1,'text':'只在演示时保留文字入口'}).json()
        self.assertEqual(q['version'],2)
        self.assertEqual(q['source_text'],'记个决定：保留文字入口')
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':1}).status_code,409)
        r=self.c.post(url+'/confirm',json={'expected_version':2});self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['events'][0]['text'],q['text'])
        self.assertEqual(r.json()['events'][0]['source'],'user_stated')
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':2}).status_code,404)
    def test_conflict_explicit_repreview(self):
        p=self.propose();url='/api/thread-proposals/'+p['id']
        self.s.threads.update(self.t['id'],expected_revision=1,next_step='彩排')
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':1}).status_code,409)
        q=self.c.patch(url,json={'expected_version':1,'text':p['text']}).json()
        self.assertEqual(q['expected_revision'],2)
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':q['version']}).status_code,200)
    def test_reject_delete_origin_and_forgery(self):
        p=self.propose();url='/api/thread-proposals/'+p['id']
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':1},headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':1,'source':'tool_verified'}).status_code,400)
        self.assertEqual(self.c.post(url+'/reject',json={'expected_version':1}).status_code,200)
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':1}).status_code,404)
        p=self.propose();self.c.post(self.base+'/delete',json={'expected_revision':1})
        self.assertEqual(self.c.post('/api/thread-proposals/'+p['id']+'/confirm',json={'expected_version':1}).status_code,404)
        self.assertEqual(self.s.threads.list_threads(),[])
    def test_chat_explicit_only_no_model(self):
        async def run():
            async def forbidden(*args,**kwargs): raise AssertionError('must not invoke model')
            self.s.loop.model=forbidden
            r=await self.s.start('记个决定：保留文字入口')
            self.assertNotIn('proposal',r)
            self.s.participation.select(self.t['id'])
            r=await self.s.start('记个决定：保留文字入口')
            self.assertIn('proposal',r);self.assertEqual(self.s.threads.get(self.t['id'])['events'],[])
            invalid=await self.s.start('记个决定，把刚才那个记下来')
            self.assertNotIn('proposal',invalid)
        asyncio.run(run())
        for text in ['不要记个决定：测试','他说“记个决定：测试”','假设我说记个决定：测试']:
            self.assertEqual(self.c.post(self.base+'/proposals',json={'utterance':text}).status_code,400)

    def test_resolved_change_http_preserves_original_thread_and_event(self):
        first=self.s.threads.add_event(self.t['id'],expected_revision=1,kind='decision',text='第一决定')
        second=self.s.threads.add_event(self.t['id'],expected_revision=first['revision'],kind='decision',text='第二决定')
        event=second['events'][1]
        p=self.s.proposals.propose_change(self.t['id'],operation='revise_event',
            text='第二决定改成先彩排',source_text='第二个改成先彩排',target_event_id=event['id'],
            expected_revision=second['revision'])
        url='/api/thread-proposals/'+p['id']
        read=self.c.get(url)
        self.assertEqual(read.status_code,200)
        self.assertEqual(read.headers['cache-control'],'no-store')
        self.assertEqual(read.json()['original_text'],'第二决定')
        other=self.s.threads.create('另一个共同事项');self.s.participation.select(other['id'])
        for field,value in [('thread_id',other['id']),('target_event_id',second['events'][0]['id']),
                             ('kind','tool_receipt'),('source','tool_verified'),('operation','add_event')]:
            forged=self.c.patch(url,json={'expected_version':1,'text':'伪造',field:value})
            self.assertEqual(forged.status_code,400)
        confirmed=self.c.post(url+'/confirm',json={'expected_version':1})
        self.assertEqual(confirmed.status_code,200)
        self.assertEqual(confirmed.json()['id'],self.t['id'])
        self.assertEqual(confirmed.json()['events'][0]['text'],'第一决定')
        self.assertEqual(confirmed.json()['events'][1]['id'],event['id'])
        self.assertEqual(confirmed.json()['events'][1]['text'],'第二决定改成先彩排')
        self.assertEqual(self.s.threads.get(other['id'])['events'],[])
        self.assertEqual(self.c.get(url).status_code,404)

    def test_next_step_requires_confirm_and_conflict_repreview(self):
        p=self.s.proposals.propose_change(self.t['id'],operation='next_step',text='演示前先彩排',
            source_text='下一步改成演示前先彩排',expected_revision=1)
        url='/api/thread-proposals/'+p['id']
        self.assertEqual(self.s.threads.get(self.t['id'])['next_step'],'')
        self.s.threads.update(self.t['id'],expected_revision=1,next_step='先检查字幕')
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':1}).status_code,409)
        self.assertEqual(self.s.threads.get(self.t['id'])['next_step'],'先检查字幕')
        q=self.c.patch(url,json={'expected_version':1,'text':'演示前先彩排'}).json()
        self.assertEqual(q['original_text'],'先检查字幕')
        self.assertEqual(q['expected_revision'],2)
        r=self.c.post(url+'/confirm',json={'expected_version':q['version']})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['next_step'],'演示前先彩排')
        self.assertEqual(r.json()['events'],[])
        self.assertEqual(self.c.post(url+'/confirm',json={'expected_version':q['version']}).status_code,404)

    def test_verified_receipt_cannot_be_changed_by_a_proposal(self):
        t=self.s.threads.add_event(self.t['id'],expected_revision=1,kind='tool_receipt',
            text='合成离线工具回执',source='tool_verified',source_id='offline:receipt')
        receipt=t['events'][0]
        for kind in ('decision','tool_receipt'):
            with self.assertRaises(ValueError):
                self.s.proposals.propose_change(self.t['id'],operation='revise_event',kind=kind,
                    text='把失败改成成功',source_text='把这条改成成功',target_event_id=receipt['id'])
        self.assertEqual(self.s.threads.get(self.t['id']),t)
        self.assertEqual(self.s.proposals.list_pending(self.t['id']),[])

if __name__=='__main__':unittest.main(verbosity=2)
