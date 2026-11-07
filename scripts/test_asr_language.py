"""Extract actual Recognizer without config/model/network bootstrap."""
import ast
import asyncio
import threading
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
            'decode_wav':lambda data:(np.ones(16000,dtype=np.float32)*.1,16000),'threading':threading}
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

class ModelLoadTests(unittest.IsolatedAsyncioTestCase):
    """ASR 首次模型加载不得阻塞事件循环。

    判据是行为，不是「代码里有没有 to_thread」：_load 打 40ms 延迟桩模拟
    WhisperModel 构造的秒级加载，心跳协程用 sleep(0) 打点（Windows 15.6ms
    定时器精度会盖住被测对象），量加载期间循环还能不能跑。同步对照证明
    这个测量器看得见阻塞。改动前 _ensure 在 async _whisper 里同步执行，
    首句语音会把整个循环冻住。
    """

    def _recognizer(self):
        source=Path(__file__).resolve().parents[1]/'server/speech/asr.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Recognizer')
        settings=SimpleNamespace(asr_language='zh',audio_sample_rate=16000,asr_engine='whisper')
        ns={'Any':object,'np':np,'settings':settings,'Transcript':lambda **kw:kw,
            'trim_silence':lambda a:a,'asyncio':asyncio,'threading':threading}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),cls],type_ignores=[])),str(source),'exec'),ns)
        return ns['Recognizer']()

    async def test_first_model_load_does_not_block_the_loop(self):
        """判据是「40ms 加载窗口内心跳跑了多少次」，不是最长停顿多少毫秒。

        2026-11-05：原判据 `max(gaps) < 10ms` 在 CI 等价全量（172 个脚本串行、
        机器负载高）叠加 Windows 15.6ms 定时器精度时会偶发假红——单独跑绿、
        连跑 5 次全红，与被测代码无关。改成行为计数：异步卸载时事件循环必须
        在加载窗口内持续被调度，同步对照则几乎打不了点。测的仍是「加载有没有
        冻住循环」，但不再依赖墙钟绝对值。
        """
        import sys,time,types
        # _ensure 里那句 `from faster_whisper import WhisperModel` 要能瞬间
        # 完成：注入假模块。它现在跑在工作线程里、不阻塞循环，但真 import
        # 会让这个用例多花几秒。
        fake=types.ModuleType('faster_whisper');fake.WhisperModel=object
        self.addCleanup(sys.modules.pop,'faster_whisper',None)
        sys.modules['faster_whisper']=fake

        r=self._recognizer();loads={"n":0}
        def slow_load():
            loads["n"]+=1
            time.sleep(0.04)
            class FakeModel:
                def transcribe(self,audio,**kwargs):return iter([]),None
            return FakeModel()
        r._load=slow_load

        async_beats={"n":0};stop=asyncio.Event()
        async def heartbeat():
            while not stop.is_set():
                await asyncio.sleep(0)
                async_beats["n"]+=1
        beat=asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        await r.transcribe(np.ones(16000,dtype=np.float32)*.1,16000)
        stop.set();await beat

        self.assertEqual(loads["n"],1,"模型没加载，测试是空转")
        self.assertGreaterEqual(async_beats["n"],10,
            f"40ms 加载窗口内心跳只跑了 {async_beats['n']} 次，事件循环被冻住了")

        # 对照：同样的 40ms 同步加载直接跑在循环上，心跳几乎打不了点。
        sync_beats={"n":0};stop2=asyncio.Event()
        async def heartbeat2():
            while not stop2.is_set():
                await asyncio.sleep(0)
                sync_beats["n"]+=1
        beat2=asyncio.create_task(heartbeat2())
        await asyncio.sleep(0)
        slow_load()
        stop2.set();await beat2
        self.assertLessEqual(sync_beats["n"],3,
            f"对照心跳跑了 {sync_beats['n']} 次，测量器可能没生效")
        self.assertGreater(async_beats["n"],sync_beats["n"]*5,
            "异步卸载与同步对照必须拉得开，否则这个测量器没有判别力")

if __name__=='__main__':unittest.main(verbosity=2)
