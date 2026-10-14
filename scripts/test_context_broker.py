"""Scene isolation and deletion propagation, fake facts excluded by design."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.interaction.broker import ContextBroker
from server.interaction.preferences import PreferenceStore


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.store = PreferenceStore()
        self.broker = ContextBroker(self.store)

    def tearDown(self):
        self.store.close()

    def claim(self, dimension, value, scope=None):
        evidence = self.store.add_evidence('用户明确选择此偏好')
        claim = self.store.create_claim(dimension, value, [evidence['id']], scope=scope)
        return evidence, claim

    def test_draft_grounding_separate_from_plain_chat(self):
        draft = self.broker.build('帮我写周报')['response_policy']
        self.assertIn('任务存在不等于完成', draft['draft_grounding'])
        self.assertIn('模板（待填写）', self.broker.render({'response_policy': draft}))
        self.assertNotIn('draft_grounding', self.broker.build('你好')['response_policy'])

    def test_mixed_emotion_and_action(self):
        self.claim('support_style', 'listen')
        self.assertEqual(self.broker.build('烦死了，帮我写周报')['response_policy']['intent'], 'help')
        self.assertEqual(self.broker.build('只想吐槽，别给建议')['response_policy']['intent'], 'listen')

    def test_scope_does_not_leak(self):
        self.claim('detail', 'detailed', {'activity': 'technical'})
        self.assertEqual(self.broker.build('代码报错')['response_policy']['length'], 'detailed')
        self.assertEqual(self.broker.build('你好')['response_policy']['length'], 'short')

    def test_specific_scope_overrides_global(self):
        self.claim('detail', 'detailed')
        self.claim('detail', 'short', {'activity': 'technical'})
        self.assertEqual(self.broker.build('代码报错')['response_policy']['length'], 'short')
        self.assertEqual(self.broker.build('你好')['response_policy']['length'], 'detailed')

    def test_current_request_overrides_scoped(self):
        self.claim('detail', 'short', {'activity': 'technical'})
        self.assertEqual(self.broker.build('代码原理详细解释')['response_policy']['length'], 'detailed')

    def test_file_script_requests_without_language_name_are_technical(self):
        requests = (
            '帮我写个批量重命名文件的脚本',
            '给我一个自动整理文件夹的脚本',
            '请生成合并CSV文件的脚本',
            '批量重命名文件的脚本该怎么写？',
            '把这个批量处理日志的脚本改一下',
            '帮我用PowerShell批量重命名文件',
            '代码报错',
            'Python里的列表和元组有什么区别？',
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(self.broker.build(text)['scene']['activity'], 'technical')

    def test_creative_script_requests_do_not_select_technical_scope(self):
        _, claim = self.claim('detail', 'detailed', {'activity': 'technical'})
        requests = (
            '帮我写个电影剧本',
            '请写一份演讲脚本',
            '帮我写一个介绍Python的视频脚本',
            '帮我写一段Python教学视频的脚本',
            '帮我写个代码教学视频脚本',
            '写一个给文件起漂亮名字的视频脚本',
        )
        for text in requests:
            with self.subTest(text=text):
                bundle = self.broker.build(text)
                self.assertEqual(bundle['scene']['activity'], 'writing')
                self.assertEqual(bundle['response_policy']['length'], 'short')
                self.assertNotIn(claim['id'], bundle['response_policy']['preference_claim_ids'])

    def test_ambiguous_declined_or_reported_technical_words_do_not_select_scope(self):
        _, claim = self.claim('detail', 'detailed', {'activity': 'technical'})
        requests = (
            '帮我写个脚本',
            '今天不想写代码，陪我聊聊',
            '不需要Python代码，只想聊电影',
            '请不要写代码',
            '不要写批量重命名文件的脚本',
            '同事说帮我写个批量重命名文件的脚本',
            '他说：帮我写Python脚本',
            '我同事说帮我写个重命名文件的脚本',
            '朋友说帮他写代码，再做一个重命名文件的脚本',
            '我引用一下别人的话：帮我写个批量重命名文件的脚本',
            '只是在转述：请生成合并文件的脚本',
            '这是别人说的：帮我写Python脚本',
            '原话是：帮我写个重命名文件的脚本',
            '她说“代码报错”，我只是转述',
            '“帮我写个批量重命名文件的脚本”是朋友的原话',
            '『请生成合并CSV文件的脚本』是别人说的',
            '我今天很happy',
        )
        for text in requests:
            with self.subTest(text=text):
                bundle = self.broker.build(text)
                self.assertEqual(bundle['scene']['activity'], 'chat')
                self.assertEqual(bundle['response_policy']['length'], 'short')
                self.assertNotIn(claim['id'], bundle['response_policy']['preference_claim_ids'])

    def test_current_technical_request_after_declining_or_quoting_still_applies(self):
        requests = (
            '不要写代码，解释一下为什么报错',
            '我不想写代码，帮我分析日志报错',
            '不想写代码但我想理解API',
            '朋友说“帮我写演讲脚本”，但我需要你检查代码',
            '他说我该休息，但现在我想请你写个重命名文件的脚本',
            '请检查电影剧本里的代码是否正确',
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(self.broker.build(text)['scene']['activity'], 'technical')

    def test_detected_technical_scope_changes_actual_policy_without_expanding_work_scope(self):
        _, technical = self.claim('detail', 'detailed', {'activity': 'technical'})
        _, work = self.claim('question_style', 'minimal', {'activity': 'work'})
        bundle = self.broker.build('帮我写个批量重命名文件的脚本', 'current')
        policy = bundle['response_policy']
        self.assertEqual(policy['length'], 'detailed')
        self.assertEqual(policy['preference_claim_ids'], [technical['id']])
        self.assertNotIn(work['id'], policy['preference_claim_ids'])
        self.assertEqual(policy['questions'], 'normal')
        self.assertIn('本轮需要展开', self.broker.render(bundle))
        self.assertFalse(bundle['tool_authorization'])
        self.assertEqual(self.broker.build('帮我写个批量重命名文件的脚本，说短点')
                         ['response_policy']['length'], 'short')

    def test_explicit_activity_has_priority_over_automatic_detection(self):
        _, technical = self.claim('detail', 'detailed', {'activity': 'technical'})
        bundle = self.broker.build('帮我写个批量重命名文件的脚本', activity='writing')
        self.assertEqual(bundle['scene']['activity'], 'writing')
        self.assertEqual(bundle['scene']['source'], 'explicit_context')
        self.assertNotIn(technical['id'], bundle['response_policy']['preference_claim_ids'])
        self.assertEqual(self.broker.build('帮我写演讲脚本', activity='technical')
                         ['response_policy']['length'], 'detailed')
        with self.assertRaises(ValueError):
            self.broker.build('代码报错', activity='unknown')

    def test_evidence_deletion_immediate(self):
        evidence, _ = self.claim('detail', 'detailed')
        self.assertEqual(self.broker.build('你好')['response_policy']['length'], 'detailed')
        self.store.delete_evidence(evidence['id'])
        self.assertEqual(self.broker.build('你好')['response_policy']['length'], 'short')

    def test_session_isolation(self):
        self.claim('detail', 'detailed', {'session_id': 'a'})
        self.assertEqual(self.broker.build('你好', 'a')['response_policy']['length'], 'detailed')
        self.assertEqual(self.broker.build('你好', 'b')['response_policy']['length'], 'short')

    def test_disabled_zero_read(self):
        class Forbidden:
            def list_claims(self, **kwargs):
                raise AssertionError('must not read')
        self.assertFalse(ContextBroker(Forbidden()).build('你好', enabled=False)['response_policy']['preference_claim_ids'])

    def test_raw_evidence_never_in_bundle(self):
        evidence = self.store.add_evidence('私人原文不得传给行为通道')
        self.store.create_claim('detail', 'detailed', [evidence['id']])
        result = self.broker.build('你好')
        self.assertNotIn('私人原文', str(result))
        self.assertFalse(result['tool_authorization'])

    def test_conflict_not_used(self):
        self.claim('detail', 'detailed')
        self.claim('detail', 'short')
        result = self.broker.build('你好')
        self.assertFalse(result['response_policy']['preference_claim_ids'])

if __name__ == '__main__':
    unittest.main()
