"""Response policy wiring tests; explicitly isolate all memory singletons."""
import os
os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.config import settings
settings.db_path = ':memory:'
from server.dialogue.orchestrator import Orchestrator, manager, llm
from server.persona import build_messages


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_respond_uses_dynamic_examples(self):
        captured = []
        async def complete(messages, **kwargs):
            captured.extend(messages)
            return {'text': '[base]我听着。', 'finish_reason': 'stop', 'usage': {}}
        old_enabled = manager.enabled
        manager.enabled = False
        try:
            orch = Orchestrator()
            with patch.object(llm, 'complete_raw', complete):
                result = await orch.respond(session_id='fusion-test', user_text='我只想吐槽，不要给建议')
            self.assertEqual(result.text, '我听着。')
            self.assertEqual(orch.session_state('fusion-test')['response_policy']['intent'], 'listen')
            self.assertTrue(any('authored' in m['content'] or '虚构表达素材' in m['content'] for m in captured))
            # No static role-play dialogues are masquerading as actual history.
            self.assertEqual(sum(m['role'] == 'user' for m in captured), 1)
        finally:
            manager.enabled = old_enabled

    async def test_memory_disabled_never_searches_preferences(self):
        old_enabled = manager.enabled
        manager.enabled = False
        try:
            with patch.object(manager.retriever, 'search', side_effect=AssertionError('disabled read')):
                strategy = Orchestrator()._response_strategy('你好', 'test')
            self.assertIn('"preference_evidence_ids":[]', strategy)
        finally:
            manager.enabled = old_enabled

    async def test_detail_preference_survives_postprocess(self):
        orch = Orchestrator()
        text = '这是具体的解释。' * 20
        detailed = orch._postprocess(text, user_text='列表与元组区别', response_policy={'length': 'detailed'})
        ordinary = orch._postprocess(text, user_text='列表与元组区别')
        self.assertGreater(len(detailed), len(ordinary))
        self.assertLessEqual(len(detailed), orch.pacing['length']['hard_limit_chars'])

    async def test_legacy_build_messages_keeps_compatibility(self):
        legacy = build_messages(user_text='你好')
        dynamic = build_messages(user_text='你好', response_strategy='style only')
        self.assertGreater(len(legacy), len(dynamic))
        self.assertEqual(dynamic[-1], {'role': 'user', 'content': '你好'})

if __name__ == '__main__':
    unittest.main()
