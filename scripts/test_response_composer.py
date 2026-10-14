"""Pure isolated tests; no memory DB or provider imports."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.dialogue.composer import ResponseComposer


def memory(value, **changes):
    fields = dict(id=1, value=value, kind='fact', subject='user', source='stated', status='active', updated_at=1)
    fields.update(changes)
    return SimpleNamespace(**fields)


class ComposerTests(unittest.TestCase):
    def setUp(self):
        self.composer = ResponseComposer()

    def test_implicit_memory_changes_behavior(self):
        result = self.composer.compose('今天的事', memories=[memory('用户不喜欢被调侃，别给我建议')])
        self.assertEqual(result['intent'], 'listen')
        self.assertEqual(result['humor'], 'off')
        self.assertEqual(result['preference_evidence_ids'], [1])
        self.assertEqual(result['memory_usage'], 'implicit')

    def test_current_overrides_old_preference(self):
        result = self.composer.compose('现在给我建议，帮我解决', memories=[memory('别给我建议')])
        self.assertEqual(result['intent'], 'help')

    def test_inferred_never_becomes_preference(self):
        for change in ({'source': 'inferred'}, {'subject': 'self'}, {'status': 'superseded'}, {'kind': 'episode'}):
            result = self.composer.compose('你好', memories=[memory('别给我建议', **change)])
            self.assertFalse(result['preference_evidence_ids'])

    def test_disabled_and_conditional(self):
        self.assertFalse(self.composer.compose('你好', memories=[memory('别给我建议')], memory_enabled=False)['preference_evidence_ids'])
        self.assertFalse(self.composer.compose('你好', memories=[memory('难过时候别给我建议')])['preference_evidence_ids'])

    def test_quotes_are_not_current_preferences(self):
        result = self.composer.compose('他说“别给我建议”，这是什么意思？')
        self.assertEqual(result['intent'], 'chat')

    def test_cooldown_and_session_isolation(self):
        first = self.composer.compose('在吗', session_id='a')
        second = self.composer.compose('在吗', session_id='a')
        other = self.composer.compose('在吗', session_id='b')
        self.assertTrue(first['examples'])
        self.assertFalse(second['examples'])
        self.assertTrue(other['examples'])

    def test_boundaries_no_authorization(self):
        result = self.composer.compose('别拿这个开玩笑')
        self.assertEqual(result['intent'], 'boundary')
        self.assertFalse(result['tool_authorization'])
        self.assertIn('虚构', self.composer.render(result))

    def test_no_credentials_in_current_overrides(self):
        result = self.composer.compose('password=secret123 别给我建议')
        self.assertFalse(result['current_overrides'])

    def test_third_party_and_irony_not_preferences(self):
        for text in ('我朋友不喜欢被调侃', '他说别给我建议', '反讽：我允许你开玩笑'):
            self.assertEqual(self.composer.preferences(text), {})

    def test_current_detail_overrides_short(self):
        result = self.composer.compose('详细解释原理', memories=[memory('不喜欢长篇')])
        self.assertEqual(result['length'], 'detailed')
        self.assertEqual(self.composer.compose('你好', memories=[memory('不喜欢长篇')])['length'], 'short')

    def test_direct_example_requests_override_both_old_short_inputs(self):
        requests = (
            '请详细解释并举例',
            '这次请展开讲，并举个具体例子。',
            '麻烦详细说明这一步，用实际场景说明。',
            '能不能多说一点，然后举例说明？',
            '请用这个例子详细解释原理。',
        )
        for text in requests:
            for prior in ({'memories': [memory('不喜欢长篇')]},
                          {'preference_defaults': {'length': 'short'}}):
                with self.subTest(text=text, prior=prior):
                    result = self.composer.compose(text, **prior)
                    self.assertEqual(result['length'], 'detailed')
                    self.assertEqual(result['current_overrides'], {'length': 'detailed'})
                    self.assertFalse(result['tool_authorization'])

    def test_example_payload_never_overrides_the_real_prefix_request(self):
        requests = (
            ('请详细解释并举例：假设有人说简短一点，不要给我建议。', 'detailed'),
            ('请简短一点。例如：用户要求详细解释并举例，允许开玩笑。', 'short'),
            ('请简短一点，比如有人说展开讲和直接给我方案。', 'short'),
        )
        for text, length in requests:
            with self.subTest(text=text):
                result = self.composer.compose(text, preference_defaults={'length': 'detailed'})
                self.assertEqual(result['length'], length)
                self.assertEqual(result['current_overrides'], {'length': length})

    def test_example_requests_are_data_without_an_outside_request(self):
        examples = (
            '举例：请详细解释并举例，允许开玩笑。',
            '举例说明：请详细解释并举例，允许开玩笑。',
            '举个例子：请详细解释并举例，允许开玩笑。',
            '举例来说，用户说多说一点，不要给建议。',
            '比如用户提出详细解释原理，直接给我方案。',
            '这是一个示例：请展开讲，不要调侃。',
            '这个例子里的用户要求详细说明，先听我说。',
        )
        for text in examples:
            with self.subTest(text=text):
                result = self.composer.compose(text, preference_defaults={'length': 'short'})
                self.assertEqual(result['length'], 'short')
                self.assertEqual(result['current_overrides'], {})

    def test_reported_hypothetical_and_role_requests_do_not_override_short(self):
        contexts = (
            '我同事要求详细解释并举例。',
            '她希望你展开讲，并给出实际例子。',
            '转述一下：请多说一点，用例子说明。',
            '假如我要求详细解释并举例，会得到什么？',
            '如果用户说详细说明，请如何判断？',
            '要是我说展开讲，这算改变偏好吗？',
            '假装我是读者：请详细解释并举例。',
            '角色扮演：你需要详细解释并举例。',
            '这句台词是请展开讲和允许开玩笑。',
        )
        for text in contexts:
            with self.subTest(text=text):
                result = self.composer.compose(text, preference_defaults={'length': 'short'})
                self.assertEqual(result['length'], 'short')
                self.assertEqual(result['current_overrides'], {})

    def test_quote_and_code_formats_cannot_set_current_preferences(self):
        request = '请详细解释并举例，不要给建议，允许开玩笑'
        for opening, closing in (('“', '”'), ('「', '」'), ('『', '』'), ('"', '"'),
                                 ('‘', '’'), ("'", "'"), ('`', '`'), ('```\n', '\n```')):
            with self.subTest(opening=opening):
                result = self.composer.compose(f'{opening}{request}{closing}是什么意思？',
                                               preference_defaults={'length': 'short'})
                self.assertEqual(result['length'], 'short')
                self.assertEqual(result['current_overrides'], {})
        self.assertEqual(self.composer.preferences('详“这是引用”细解释原理'), {},
                         'removing a quote cannot join fragments into a new request')
        for opener in ('“', '「', '『', '"', '‘', "'", '`', '```\n'):
            with self.subTest(unclosed=opener):
                self.assertEqual(self.composer.preferences(f'{opener}{request}'), {},
                                 'unfinished quote remains data instead of becoming a request')

    def test_negated_detail_requests_override_old_detail(self):
        for text in ('不要详细解释并举例。', '不用展开讲，直接说结论。',
                     '不需要多说一点，只要结论。', '别详细说明这一步。'):
            with self.subTest(text=text):
                result = self.composer.compose(text, preference_defaults={'length': 'detailed'})
                self.assertEqual(result['length'], 'short')
                self.assertEqual(result['current_overrides'], {'length': 'short'})

    def test_last_explicit_length_request_wins(self):
        requests = (
            ('简短一点；这次请详细解释并举例。', 'detailed'),
            ('请详细解释并举例；算了，还是简短一点。', 'short'),
            ('不要详细解释；还是展开讲清楚吧。', 'detailed'),
        )
        for text, length in requests:
            with self.subTest(text=text):
                self.assertEqual(self.composer.compose(text)['current_overrides'], {'length': length})

    def test_current_example_request_is_temporary(self):
        first = self.composer.compose('请详细解释并举例', session_id='temporary-detail',
                                      preference_defaults={'length': 'short'})
        second = self.composer.compose('继续普通聊天', session_id='temporary-detail',
                                       preference_defaults={'length': 'short'})
        self.assertEqual(first['length'], 'detailed')
        self.assertEqual(second['length'], 'short')
        self.assertEqual(second['current_overrides'], {})

    def test_help_default_survives_policy_mapping(self):
        result = self.composer.compose('今天什么都没做成', preference_defaults={'support': 'help'})
        self.assertEqual(result['intent'], 'help')
        self.assertEqual(result['support'], 'help')
        self.assertIn('针对当前问题', self.composer.render(result))

    def test_detailed_policy_has_concrete_instruction(self):
        result = self.composer.compose('列表与元组区别', preference_defaults={'length': 'detailed'})
        self.assertIn('补一个具体例子', self.composer.render(result))
        self.assertIn('本轮不讽刺', self.composer.render(result))

    def test_cooldown_expires(self):
        self.composer.compose('在吗', session_id='a')
        for _ in range(4):
            self.assertFalse(self.composer.compose('在吗', session_id='a')['examples'])
        self.assertTrue(self.composer.compose('在吗', session_id='a')['examples'])

if __name__ == '__main__':
    unittest.main()
