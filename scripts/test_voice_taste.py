import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.speech.taste_api import create_taste_router

class Fake:
    engine='stepaudio'
    calls=[]
    def profile(self,_):return {'voice':'fixed','instruction':'default'}
    async def synthesize_profile(self,text,profile):self.calls.append((text,profile));return b'fake-audio'

class TestTaste(unittest.TestCase):
    def test_contract(self):
        engine=Fake();engine.calls=[];app=FastAPI();app.include_router(create_taste_router(engine))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/voice-taste').json()['voice'],'fixed')
            for style in ('natural','serious','light','gentle'):
                response=client.post('/api/voice-taste',json={'text':'同一句话','style':style},headers={'Origin':'http://testserver'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['voice'],'fixed')
            self.assertEqual(len(engine.calls),4)
            self.assertEqual(len({p['instruction'] for _,p in engine.calls}),4)
            self.assertEqual(engine.profile('base')['instruction'],'default')
            for body in ({'text':'x','style':'bad'},{'text':'x'*241,'style':'natural'},{'text':'x','style':'natural','voice':'override'}):
                self.assertEqual(client.post('/api/voice-taste',json=body,headers={'Origin':'http://testserver'}).status_code,400)
            self.assertEqual(client.post('/api/voice-taste',json={'text':'x','style':'natural'},headers={'Origin':'https://other.example'}).status_code,403)
            self.assertEqual(client.post('/api/voice-taste',json={'text':'x','style':'natural'}).status_code,403)
            self.assertEqual(len(engine.calls),4)
if __name__=='__main__':unittest.main(verbosity=2)
