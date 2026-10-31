"""Validate opt-in route wiring without loading settings or contacting providers."""
import ast
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# B-1（2026-10-31）：/ws/interpret-stream 现在先过 _ws_handshake_allowed 再进
# serve_*。AST 装载必须把它一起编译进来，否则端点在第一行就 NameError——
# 这正是本文件要防的「接线漂移」本身，替身注入反而会把它盖住。
# 传进来的 ws 是裸 object()：没有 headers → 判据按「无 Origin 的本地客户端」放行，
# 于是这条测试同时钉住「守卫不拦无 Origin 的内部调用」。
_WANTED = {'stream_interpretation_socket', '_ws_handshake_allowed'}


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_only_from_explicit_stream_configuration(self):
        source=Path(__file__).resolve().parents[1]/'server/app.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        fns=[n for n in tree.body if isinstance(n,(ast.AsyncFunctionDef,ast.FunctionDef))
             and n.name in _WANTED]
        self.assertEqual(sorted(n.name for n in fns), sorted(_WANTED),
                         "server/app.py 少了本测试要驱动的函数")
        for fn in fns:
            fn.decorator_list=[]
        captured=[]
        adapter=types.ModuleType('server.speech.step_stream')
        adapter.StepStream=lambda *args,**kw: captured.append((args,kw)) or 'adapter'
        service=types.ModuleType('server.speech.stream_interpretation')
        async def serve(ws,llm,factory):
            self.assertEqual(factory(),'adapter');self.assertEqual(llm,'translator')
        service.serve_stream_interpretation=serve
        settings=types.SimpleNamespace(asr_stream_url='',asr_stream_api_key='',asr_stream_wire_format='pcm',llm_api_key='NEVER_REUSE')
        ns={'__package__':'server','WebSocket':object,'settings':settings,'llm':'translator'}
        with patch.dict(sys.modules,{'server.speech.step_stream':adapter,'server.speech.stream_interpretation':service}):
            exec(compile(ast.Module(body=fns,type_ignores=[]),str(source),'exec'),ns)
            await ns['stream_interpretation_socket'](object())
        self.assertEqual(captured,[(('',''),{'wire_format':'pcm'})])

if __name__=='__main__':unittest.main(verbosity=2)
