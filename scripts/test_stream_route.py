"""Validate opt-in route wiring without loading settings or contacting providers."""
import ast
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_only_from_explicit_stream_configuration(self):
        source=Path(__file__).resolve().parents[1]/'server/app.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='stream_interpretation_socket')
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
            exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns)
            await ns['stream_interpretation_socket'](object())
        self.assertEqual(captured,[(('',''),{'wire_format':'pcm'})])

if __name__=='__main__':unittest.main(verbosity=2)
