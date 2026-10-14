"""Extract actual Recognizer without config/model/network bootstrap."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np

class LanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_language_override_without_global_mutation(self):
        source=Path(__file__).resolve().parents[1]/'server/speech/asr.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Recognizer')
        settings=SimpleNamespace(asr_language='zh',audio_sample_rate=16000,asr_engine='stepaudio')
        ns={'Any':object,'np':np,'settings':settings,'Transcript':lambda *a: a,'trim_silence':lambda a:a,
            'decode_wav':lambda data:(np.ones(16000,dtype=np.float32)*.1,16000)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),cls],type_ignores=[])),str(source),'exec'),ns)
        r=ns['Recognizer']();seen=[]
        async def backend(audio,duration,language):seen.append((settings.asr_engine,language));return language
        r._stepaudio=backend;r._whisper=backend
        self.assertEqual(await r.transcribe_wav(b'fake',language='en'),'en')
        self.assertEqual(await r.transcribe_wav(b'fake'),'zh')
        settings.asr_engine='whisper'
        self.assertEqual(await r.transcribe_wav(b'fake',language='en'),'en')
        self.assertEqual(settings.asr_language,'zh')
        self.assertEqual(seen,[('stepaudio','en'),('stepaudio','zh'),('whisper','en')])

if __name__=='__main__':unittest.main(verbosity=2)
