"""Offline agent-loop guardrail and checkpoint tests."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.loop import AgentLoop, Tool
from server.assistant.runtime import RuntimeStore


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = RuntimeStore()
        self.calls = []

    async def asyncTearDown(self):
        self.runtime.close()

    def tool(self, mutation=True):
        def handler(title):
            self.calls.append(title)
            return {'id': 'task_1', 'title': title}
        return Tool('task.create', 'Create task', {'title': str}, ('title',), handler, mutation)

    def model(self, decisions):
        async def call(_):
            return decisions.pop(0)
        return call

    async def test_confirmation_before_mutation(self):
        loop = AgentLoop(self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': '周报'}},
                                    {'action': 'final', 'text': '已创建。'}]), [self.tool()], self.runtime)
        result = await loop.start('创建周报任务')
        self.assertEqual(result['status'], 'waiting_confirmation')
        self.assertEqual(self.calls, [])
        done = await loop.resume(result['run_id'], result['confirmation']['id'], True)
        self.assertEqual(self.calls, ['周报'])
        self.assertEqual(done['receipts'][0]['status'], 'succeeded')
        with self.assertRaises(ValueError):
            await loop.resume(result['run_id'], result['confirmation']['id'], True)

    async def test_reject_no_side_effect(self):
        loop = AgentLoop(self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'test'}}]),
                         [self.tool()], self.runtime)
        result = await loop.start('test')
        done = await loop.resume(result['run_id'], result['confirmation']['id'], False)
        self.assertEqual(done['status'], 'cancelled')
        self.assertFalse(self.calls)

    async def test_invalid_tool_bounded(self):
        async def model(_):
            return {'action': 'tool_call', 'tool': 'shell', 'arguments': {'command': 'bad'}}
        result = await AgentLoop(model, [self.tool()], self.runtime, max_steps=2).start('test')
        self.assertEqual(result['status'], 'limit_reached')
        self.assertEqual(result['steps'], 2)
        self.assertFalse(self.calls)

    async def test_invalid_argument_type(self):
        loop = AgentLoop(self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': True}},
                                    {'action': 'clarify', 'text': '任务叫什么？'}]), [self.tool()], self.runtime)
        result = await loop.start('test')
        self.assertEqual(result['receipts'][0]['error_code'], 'invalid_arguments')
        self.assertFalse(self.calls)

    async def test_timeout(self):
        async def model(_):
            await asyncio.sleep(1)
        result = await AgentLoop(model, [], self.runtime, timeout=.01).start('test')
        self.assertEqual(result['status'], 'timed_out')

    async def test_confirmation_expires(self):
        now = [0.0]
        runtime = RuntimeStore(clock=lambda: now[0])
        loop = AgentLoop(self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'test'}}]),
                         [self.tool()], runtime)
        result = await loop.start('test')
        now[0] = 601
        with self.assertRaisesRegex(ValueError, 'expired'):
            await loop.resume(result['run_id'], result['confirmation']['id'], True)
        self.assertFalse(self.calls)
        runtime.close()

    async def test_checkpoint_reopen(self):
        # data/ 被 gitignore，全新检出时不存在。先建出来再在里面开临时目录，
        # 否则这个用例会取决于前面有没有测试先把 data/ 建出来——时红时绿。
        data_dir = Path(__file__).resolve().parents[1] / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=data_dir) as directory:
            path = Path(directory) / 'runtime.db'
            runtime = RuntimeStore(path)
            loop = AgentLoop(self.model([{'action': 'tool_call', 'tool': 'task.create', 'arguments': {'title': 'test'}}]),
                             [self.tool()], runtime)
            result = await loop.start('test')
            runtime.close()
            runtime = RuntimeStore(path)
            loop = AgentLoop(self.model([{'action': 'final', 'text': '完成'}]), [self.tool()], runtime)
            done = await loop.resume(result['run_id'], result['confirmation']['id'], True)
            self.assertEqual(done['status'], 'completed')
            runtime.close()

    async def test_no_replay_after_crash(self):
        run_id = self.runtime.create({'pending_action': None})
        self.assertEqual(self.runtime.recover_interrupted(), 1)
        self.assertEqual(self.runtime.get(run_id)['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
