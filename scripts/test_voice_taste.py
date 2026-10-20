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

    def test_reported_format_matches_the_bytes(self):
        """The audition page builds a Blob from `format`, so it must name the
        container the engine really returned. Trimming re-encodes to WAV."""
        from server.config import settings
        engine=Fake();engine.calls=[];app=FastAPI();app.include_router(create_taste_router(engine))
        saved=settings.tts_trim_silence
        try:
            with TestClient(app) as client:
                settings.tts_trim_silence=True
                self.assertEqual(client.post('/api/voice-taste',json={'text':'一句话','style':'natural'},
                    headers={'Origin':'http://testserver'}).json()['format'],'wav')
                settings.tts_trim_silence=False
                self.assertEqual(client.post('/api/voice-taste',json={'text':'一句话','style':'natural'},
                    headers={'Origin':'http://testserver'}).json()['format'],settings.tts_format)
        finally:
            settings.tts_trim_silence=saved
if __name__=='__main__':unittest.main(verbosity=2)
