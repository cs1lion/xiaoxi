"""Provider-response diagnostics through real editor resolvers, entirely offline."""
import asyncio
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.assistant.editor_insights import (
    REPLACEMENT_LABEL, EditorInsightService, InsightDiscussionError,
)
if __package__:
    from .test_editor_insights import BODY, ANCHOR, POINT
else:
    from test_editor_insights import BODY, ANCHOR, POINT


def raw(value, finish_reason='stop', **extra):
    return {'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
            'finish_reason': finish_reason, **extra}


def valid_insight(point=POINT):
    return raw({'action': 'insight', 'anchor': ANCHOR, 'point': point})


class Delegate:
    offline = False

    def __init__(self):
        self.responses = []
        self.requests = []

    async def complete_raw(self, messages, **options):
        self.requests.append({'messages': messages, 'options': options})
        if not self.responses:
            raise AssertionError('unplanned_model_call')
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class OutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.delegate = Delegate()
        self.service = EditorInsightService(self.delegate, clock=lambda: self.now)
        self.addCleanup(self.service.close)

    async def analyze(self, response, session='editor', revision=1):
        self.delegate.responses.append(response)
        self.service.state(session, revision, True)
        return await self.service.analyze(session, revision, '测试稿', BODY, True)

    async def insight(self, session='editor'):
        return (await self.analyze(valid_insight(), session=session))['insight']

    async def test_complete_strict_quiet_is_intentional_silence(self):
        result = await self.analyze(raw({'action': 'quiet'}))
        self.assertEqual(result, {'status': 'quiet', 'reason': 'no_salient_insight', 'checks': 1})
        self.assertEqual(len(self.delegate.requests), 1)
        self.assertEqual(self.service._insights, {})

    async def test_analysis_provider_failures_are_named_and_never_create_insight(self):
        cases = [
            ('empty', raw(''), 'analysis_empty_response'),
            ('whitespace', raw(' \n\t '), 'analysis_empty_response'),
            ('truncated_empty', raw('', 'length'), 'analysis_output_truncated'),
            ('truncated_quiet', raw({'action': 'quiet'}, 'length'), 'analysis_output_truncated'),
            ('truncated_insight', {**valid_insight(), 'finish_reason': 'length'}, 'analysis_output_truncated'),
            ('broken_json', raw('{"action":'), 'analysis_invalid_response'),
            ('fenced_json', raw('```json\n{"action":"quiet"}\n```'), 'analysis_invalid_response'),
            ('non_stop_reason', raw({'action': 'quiet'}, 'content_filter'), 'analysis_invalid_response'),
            ('missing_response_text', {'finish_reason': 'stop'}, 'analysis_invalid_response'),
            ('non_string_text', {'text': None, 'finish_reason': 'stop'}, 'analysis_invalid_response'),
            ('non_dict_response', [], 'analysis_invalid_response'),
            ('null_json', raw(None), 'analysis_invalid_response'),
            ('list_json', raw([]), 'analysis_invalid_response'),
            ('extra_quiet_field', raw({'action': 'quiet', 'text': '不应接受'}), 'analysis_invalid_response'),
            ('fabricated_anchor', raw({'action': 'insight', 'anchor': '不存在的原文句子', 'point': POINT}), 'analysis_invalid_response'),
            ('oversized_point', valid_insight('甲' * 241), 'analysis_invalid_response'),
            ('sensitive_point', valid_insight('api_key=FICTIONAL_MUST_NOT_RETAIN'), 'analysis_invalid_response'),
            ('provider_error', RuntimeError('PRIVATE_PROVIDER_MESSAGE'), 'analysis_unavailable'),
        ]
        for name, response, reason in cases:
            with self.subTest(name=name):
                result = await self.analyze(response, session=name)
                self.assertEqual(result, {'status': 'unavailable', 'reason': reason, 'checks': 1})
                self.assertNotIn('insight_id', self.service._sessions[name])
        self.assertEqual(self.service._insights, {})
        self.assertNotIn('PRIVATE_PROVIDER_MESSAGE', str(result))

    async def test_independent_token_budgets_do_not_raise_visible_character_limits(self):
        result = await self.analyze(valid_insight('甲' * 240))
        self.assertEqual(result['status'], 'insight')
        self.assertEqual(len(result['insight']['point']), 240)
        self.delegate.responses.append(raw({'text': '乙' * 799 + '。'}))
        reply = await self.service.reply(result['insight']['id'], 'editor', 1, '那怎么改？')
        self.assertEqual(len(reply['text']), 800)
        for request in self.delegate.requests:
            self.assertEqual(request['options']['max_tokens'], 6144)
            self.assertEqual(request['options']['max_attempts'], 1)
            self.assertIs(request['options']['json_mode'], True)
        self.assertEqual(self.service.MODEL_TIMEOUT_SECONDS, 45)
        self.assertEqual(self.service.MAX_DISCUSSION_REPLY, 800)

    async def test_medium_reasoning_is_explicit_only_for_exact_step_flash_model(self):
        for index, model in enumerate(('step-3.7-flash', 'step-3.7', 'deepseek-chat',
                                       'STEP-3.7-FLASH', 'step-3.7-flash-custom', '')):
            with self.subTest(model=model):
                self.delegate.model = model
                start = len(self.delegate.requests)
                session = 'reasoning-model-' + str(index)
                insight = await self.insight(session=session)
                self.delegate.responses.append(raw({'text': '仅讨论原文的一条建议。'}))
                await self.service.reply(insight['id'], session, 1, '为什么？')
                calls = self.delegate.requests[start:]
                self.assertEqual(len(calls), 2)
                for position, request in enumerate(calls):
                    options = request['options']
                    if model == 'step-3.7-flash':
                        self.assertEqual(options['reasoning_effort'],
                                         'medium' if position == 0 else 'low')
                    else:
                        self.assertNotIn('reasoning_effort', options)
                    self.assertEqual(options['max_tokens'], 6144)
                    self.assertEqual(options['max_attempts'], 1)
                self.assertEqual(self.service.MODEL_TIMEOUT_SECONDS, 45)

    async def test_replacement_field_is_labelled_by_the_service(self):
        insight = await self.insight()
        self.delegate.responses.append(raw({
            'text': '把第三段的全行业要求收回到你自己的选择。',
            'replacement': '已经够我在早市选现金；别的地方怎么付，那是他们的事。'}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '只替换第三段，以建议稿标出。')
        self.assertTrue(reply['text'].startswith('把第三段的全行业要求收回到你自己的选择。'))
        self.assertIn('建议稿：已经够我在早市选现金', reply['text'])
        self.assertEqual(reply['text'].count('建议稿：'), 1)
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'][-1]['content'],
                         reply['text'])

    async def test_ungrounded_replacement_is_withheld_and_reasoning_is_kept(self):
        insight = await self.insight()
        self.delegate.responses.append(raw({
            'text': '这两项能力目前不能确认，先小范围试印核对。',
            'replacement': '同类工具普遍支持这两项功能，我们初步判断它大概率能满足本次需求。'}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '按常见功能帮我写肯定一点。')
        self.assertIn('目前不能确认', reply['text'])
        self.assertNotIn('大概率', reply['text'])
        self.assertNotIn('普遍支持', reply['text'])
        self.assertNotIn('建议稿：', reply['text'])
        self.assertIn('没有附上替换文字', reply['text'])
        self.assertEqual(self.service._insights[insight['id']]['discussion_checks'], 1)

    async def test_claim_the_draft_already_makes_is_not_treated_as_ungrounded(self):
        insight = await self.insight()
        self.delegate.responses.append(raw({
            'text': '这一段可以直接引用你原文的表述。',
            'replacement': '后文用一个具体例子解释选择，并交代讨论范围与下一步。'}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '只替换第三段。')
        self.assertIn('建议稿：后文用一个具体例子解释选择', reply['text'])

    async def test_replacement_that_carries_its_own_label_is_not_labelled_twice(self):
        insight = await self.insight()
        self.delegate.responses.append(raw({
            'text': '按你指定的范围改这一段。',
            'replacement': '建议稿：后文用一个具体例子解释选择，并交代讨论范围与下一步。'}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '只替换第三段。')
        self.assertEqual(reply['text'].count(REPLACEMENT_LABEL), 1)
        self.assertTrue(reply['text'].endswith(
            REPLACEMENT_LABEL + '后文用一个具体例子解释选择，并交代讨论范围与下一步。'))

    async def test_inline_ungrounded_suggestion_loses_only_its_tail(self):
        insight = await self.insight()
        self.delegate.responses.append(raw(
            {'text': '说明目前不能确认，再给出试印步骤。\n建议稿：同类工具普遍支持，大概率能满足。'}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '那怎么改？')
        self.assertIn('目前不能确认', reply['text'])
        self.assertNotIn('大概率', reply['text'])
        self.assertNotIn('建议稿：', reply['text'])
        self.assertIn('没有附上替换文字', reply['text'])

    async def test_replacement_metadata_shape_is_validated_like_the_answer(self):
        cases = [
            ('non_string_replacement', raw({'text': '先说明方向。', 'replacement': 7})),
            ('oversized_replacement', raw({'text': '先说明方向。', 'replacement': '甲' * 801})),
        ]
        for name, response in cases:
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(response)
                with self.assertRaises(InsightDiscussionError) as caught:
                    await self.service.reply(insight['id'], name, 1, '那怎么改？')
                self.assertEqual(caught.exception.reason, 'insight_discussion_invalid_reply')
                self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])

    async def test_blank_replacement_is_treated_as_no_replacement(self):
        insight = await self.insight()
        self.delegate.responses.append(raw({'text': '目前不能确认，先说清依据。', 'replacement': '  '}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '那把语气写确定一点，行吗？')
        self.assertEqual(reply['text'], '目前不能确认，先说清依据。')
        self.assertNotIn(REPLACEMENT_LABEL, reply['text'])

    async def test_analysis_failures_still_consume_cooldown_and_session_limit(self):
        for revision in range(1, 6):
            self.delegate.responses.append(raw('invalid JSON'))
            self.service.state('editor', revision, True)
            result = await self.service.analyze('editor', revision, '', BODY, True)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['checks'], revision)
            self.service.state('editor', revision + 1, True)
            blocked = await self.service.analyze('editor', revision + 1, '', BODY, True)
            self.assertEqual(blocked['status'], 'cooldown' if revision < 5 else 'limit')
            self.now += 90
        self.assertEqual(len(self.delegate.requests), 5)
        self.assertEqual(self.delegate.responses, [])

    async def test_thinking_never_substitutes_for_missing_response_or_enters_context(self):
        response = raw('', reasoning='{"action":"quiet"}', thinking='可解析但不是正文')
        result = await self.analyze(response, session='empty-thinking')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'analysis_empty_response')
        response = {**valid_insight(), 'reasoning': 'PRIVATE_THINKING', 'thinking': 'PRIVATE_THINKING'}
        result = await self.analyze(response, session='valid-thinking')
        self.assertEqual(result['status'], 'insight')
        self.delegate.responses.append(raw({'text': '建议稿：保留这段，再说明主张。'}, reasoning='PRIVATE_THINKING'))
        await self.service.reply(result['insight']['id'], 'valid-thinking', 1, '那怎么改？')
        payload = json.loads(self.delegate.requests[-1]['messages'][1]['content'])
        retained = self.service._insights[result['insight']['id']]
        self.assertNotIn('PRIVATE_THINKING', json.dumps(payload))
        self.assertNotIn('PRIVATE_THINKING', json.dumps(retained['discussion_history']))

    async def test_discussion_output_diagnostics_consume_budget_without_committing_history(self):
        insight = await self.insight()
        cases = [
            (raw('', 'length'), 'insight_discussion_output_truncated'),
            (raw({'text': '看似完整'}, 'length'), 'insight_discussion_output_truncated'),
            (raw('', reasoning='{"text":"不是回答"}'), 'insight_discussion_empty_response'),
            (raw(' \n '), 'insight_discussion_empty_response'),
            (raw('{"text":'), 'insight_discussion_invalid_reply'),
            (raw({'text': '甲' * 801}), 'insight_discussion_invalid_reply'),
        ]
        for number, (response, reason) in enumerate(cases, 1):
            with self.subTest(reason=reason, call=number):
                self.delegate.responses.append(response)
                with self.assertRaises(InsightDiscussionError) as caught:
                    await self.service.reply(insight['id'], 'editor', 1, '为什么？')
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(caught.exception.status_code, 502)
                state = self.service._insights[insight['id']]
                self.assertEqual(state['discussion_checks'], number)
                self.assertEqual(state['discussion_history'], [])
        with self.assertRaises(InsightDiscussionError) as caught:
            await self.service.reply(insight['id'], 'editor', 1, '再试一次')
        self.assertEqual(caught.exception.reason, 'insight_discussion_limit')
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(len(self.delegate.requests), 7)

    async def test_failed_discussion_response_does_not_erase_successful_history(self):
        insight = await self.insight()
        self.delegate.responses.append(raw({'text': '可以保留原文。'}))
        await self.service.reply(insight['id'], 'editor', 1, '我想保留这段。')
        snapshot = list(self.service._insights[insight['id']]['discussion_history'])
        self.delegate.responses.append(raw({'text': '截断回答'}, 'length'))
        with self.assertRaises(InsightDiscussionError):
            await self.service.reply(insight['id'], 'editor', 1, '为什么？')
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], snapshot)
        self.delegate.responses.append(raw({'text': '建议稿：保留原文，再说明主张。'}))
        await self.service.reply(insight['id'], 'editor', 1, '那怎么改？')
        payload = json.loads(self.delegate.requests[-1]['messages'][1]['content'])
        self.assertEqual(self.delegate.requests[-1]['messages'][2:-1], snapshot)
        self.assertEqual(self.delegate.requests[-1]['messages'][-1],
                         {'role': 'user', 'content': '那怎么改？'})
        self.assertNotIn('截断回答', json.dumps(self.delegate.requests[-1]['messages'], ensure_ascii=False))

    async def test_discussion_invalid_metadata_shape_and_sensitive_text_are_named_failures(self):
        cases = [
            ('non_stop_reason', raw({'text': '内容仍需拒收'}, 'content_filter')),
            ('missing_response_text', {'finish_reason': 'stop'}),
            ('non_string_text', {'text': None, 'finish_reason': 'stop'}),
            ('non_dict_response', []),
            ('extra_json_fields', raw({'text': '内容仍需拒收', 'tool': 'note.update'})),
            ('empty_json_text', raw({'text': ''})),
            ('sensitive_text', raw({'text': 'api_key=FICTIONAL_MUST_NOT_RETAIN'})),
        ]
        for name, response in cases:
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(response)
                with self.assertRaises(InsightDiscussionError) as caught:
                    await self.service.reply(insight['id'], name, 1, '为什么？')
                self.assertEqual(caught.exception.reason, 'insight_discussion_invalid_reply')
                self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])
                self.assertEqual(self.service._insights[insight['id']]['discussion_checks'], 1)

    async def test_self_labelled_answer_is_not_repeated_through_the_field(self):
        suggestion = '这两个月不新增现金，养护轮值已有安排，超出范围先暂停确认。'
        insight = await self.insight()
        self.delegate.responses.append(raw({
            'text': REPLACEMENT_LABEL + suggestion, 'replacement': suggestion}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '只替换第三段。')
        self.assertEqual(reply['text'], REPLACEMENT_LABEL + suggestion)
        self.assertEqual(reply['text'].count(suggestion), 1)
        self.assertEqual(reply['text'].count(REPLACEMENT_LABEL), 1)

    async def test_blank_replacement_field_is_the_same_as_no_replacement(self):
        insight = await self.insight()
        self.delegate.responses.append(raw({'text': '不需要加钱，零投入的表述要改。', 'replacement': ''}))
        reply = await self.service.reply(insight['id'], 'editor', 1, '还需要坚持加钱吗？')
        self.assertEqual(reply['text'], '不需要加钱，零投入的表述要改。')
        self.assertEqual(self.service._insights[insight['id']]['discussion_checks'], 1)

    async def test_label_left_as_a_stub_still_delivers_the_replacement(self):
        suggestion = '这两个月不新增现金，养护轮值已有安排，超出范围先暂停确认。'
        for name, answer in (('bare_label', REPLACEMENT_LABEL),
                             ('labelled_lead_in', '已按你要求替换第三段。' + REPLACEMENT_LABEL)):
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(raw({'text': answer, 'replacement': suggestion}))
                reply = await self.service.reply(insight['id'], name, 1, '只替换第三段。')
                self.assertIn(REPLACEMENT_LABEL + suggestion, reply['text'])
                self.assertEqual(reply['text'].count(REPLACEMENT_LABEL), 1)
                self.assertTrue(reply['text'].strip().endswith(suggestion))

    async def test_inline_lead_in_duplicate_is_not_shown_twice(self):
        suggestion = '这两个月不新增现金，养护轮值已有安排，超出范围先暂停确认。'
        for name, lead in (('suggest_change', '建议改为：'), ('can_change', '可以改为：')):
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(raw({
                    'text': '不需要加钱，但零投入的表述要改。' + lead + suggestion,
                    'replacement': suggestion}))
                reply = await self.service.reply(insight['id'], name, 1, '只替换第三段。')
                self.assertEqual(reply['text'].count(suggestion), 1)
                self.assertNotIn(REPLACEMENT_LABEL, reply['text'])
                self.assertIn(lead + suggestion, reply['text'])

    async def test_unlabelled_duplicate_gains_exactly_one_label_in_place(self):
        suggestion = '这两个月不新增现金，养护轮值已有安排，超出范围先暂停确认。'
        insight = await self.insight(session='unlabelled')
        self.delegate.responses.append(raw({
            'text': '把第三段改成：' + suggestion + '其余部分不动。', 'replacement': suggestion}))
        reply = await self.service.reply(insight['id'], 'unlabelled', 1, '只替换第三段。')
        self.assertEqual(reply['text'].count(suggestion), 1)
        self.assertEqual(reply['text'].count(REPLACEMENT_LABEL), 1)
        self.assertEqual(reply['text'],
                         '把第三段改成：' + REPLACEMENT_LABEL + suggestion + '其余部分不动。')

    async def test_category_premise_and_probability_are_withheld_from_replacements(self):
        cases = [
            ('category_premise', '这类拼页工具通常具备页序自动调整能力，这款理论上可能支持本次需求。'),
            ('probability', '同类软件普遍支持，这款大概率也能满足这次讲义小册子的制作需求。'),
            ('certainty', '这款工具一定能自动安排页序，不必再试印。'),
        ]
        for name, suggestion in cases:
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(raw({'text': '按你的语气写。', 'replacement': suggestion}))
                reply = await self.service.reply(insight['id'], name, 1, '写得肯定一点。')
                self.assertNotIn(suggestion, reply['text'])
                self.assertIn('这次没有附上替换文字', reply['text'])

    async def test_phrase_the_author_just_said_is_not_withheld(self):
        insight = await self.insight(session='author_words')
        self.delegate.responses.append(raw({
            'text': '这一段可以收回。',
            'replacement': '同类工具通常具备这些功能，这款可能支持，但仍需试印核实。'}))
        reply = await self.service.reply(
            insight['id'], 'author_words', 1,
            '就按「同类工具通常具备这些功能，这款可能支持，但仍需试印核实」来写。')
        self.assertIn(REPLACEMENT_LABEL, reply['text'])
        self.assertIn('仍需试印核实', reply['text'])

    async def test_completion_claims_never_reach_the_author(self):
        suggestion = '本学期借用为临时安排，不承诺下学期长期固定开放。'
        cases = [
            ('replaced_paragraph', '已按您要求替换第三段，明确临时安排与期限。', suggestion),
            ('rewritten_paragraph', '已重写第三段，以「' + REPLACEMENT_LABEL + '」开头，说明核实步骤。', suggestion),
            ('already_saved', '已保存修改，并已按你要求替换第二段。', suggestion),
        ]
        for name, answer, replacement in cases:
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(raw({'text': answer, 'replacement': replacement}))
                reply = await self.service.reply(insight['id'], name, 1, '只替换第三段。')
                self.assertNotIn('已按您要求', reply['text'])
                self.assertNotIn('已重写', reply['text'])
                self.assertNotIn('已保存', reply['text'])
                self.assertIn(REPLACEMENT_LABEL + replacement, reply['text'])

    async def test_reply_that_only_claims_completion_is_rejected(self):
        insight = await self.insight(session='only_claim')
        self.delegate.responses.append(raw({'text': '已按您要求替换第三段。'}))
        with self.assertRaises(InsightDiscussionError) as caught:
            await self.service.reply(insight['id'], 'only_claim', 1, '只替换第三段。')
        self.assertEqual(caught.exception.reason, 'insight_discussion_invalid_reply')
        self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])

    async def test_reasoning_before_a_label_survives_a_rewrite_request(self):
        suggestion = '本学期借用为临时安排，不承诺下学期长期固定开放。'
        insight = await self.insight(session='reasoning_first')
        self.delegate.responses.append(raw({
            'text': '问题在归属：居民只交接、物业只借柜子，期限也只有三个月。\n\n' + REPLACEMENT_LABEL,
            'replacement': suggestion}))
        reply = await self.service.reply(insight['id'], 'reasoning_first', 1, '只替换第三段。')
        self.assertTrue(reply['text'].startswith('问题在归属：'))
        self.assertTrue(reply['text'].endswith(REPLACEMENT_LABEL + suggestion))
        self.assertEqual(reply['text'].count(REPLACEMENT_LABEL), 1)

    async def test_requested_length_is_checked_and_reported(self):
        suggestion = '这两个月不新增现金，养护轮值已有安排，超出范围先暂停确认。'
        cases = [
            ('digits_short', '只替换第三段，给100到150字的建议稿。', suggestion),
            ('chinese_long', '篇幅控制在一百到一百五十字。', suggestion * 6),
            ('in_range', '只替换第三段，给20到40字的建议稿。', suggestion),
            ('no_range', '只替换第三段。', suggestion),
            ('reversed_range', '从100字砍到50字。', suggestion),
        ]
        for name, user_text, replacement in cases:
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(raw({'text': '可以这样改。', 'replacement': replacement}))
                reply = await self.service.reply(insight['id'], name, 1, user_text)
                self.assertIn(REPLACEMENT_LABEL + replacement, reply['text'])
                if name in ('digits_short', 'chinese_long'):
                    self.assertIn('你要求的是', reply['text'])
                    self.assertIn('%d 字' % len(replacement), reply['text'])
                else:
                    self.assertNotIn('你要求的是', reply['text'])

    async def test_chinese_numeral_lengths_are_read_correctly(self):
        cases = [('一百五十', 150), ('九十', 90), ('十五', 15), ('九十五', 95),
                 ('两百', 200), ('一百三十', 130), ('一千', None), ('xyz', None)]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(EditorInsightService._chinese_number(text), expected)

    async def test_reply_that_stops_mid_sentence_is_rejected(self):
        broken = ('建议在正文前标注「建议稿：」，并将第三段替换为以下内容（约81字）：'
                  '明确试放期两个月，巡查志愿者核对登记；赔偿暂无约定时先暂停借用，'
                  '由物业确认后续规则。删除原稿中')
        cases = [
            ('answer_only', {'text': broken}),
            ('replacement_only', {'text': '可以这样改。', 'replacement': broken}),
        ]
        for name, resolved in cases:
            with self.subTest(name=name):
                insight = await self.insight(session=name)
                self.delegate.responses.append(raw(resolved))
                with self.assertRaises(InsightDiscussionError) as caught:
                    await self.service.reply(insight['id'], name, 1, '只替换第三段。')
                self.assertEqual(caught.exception.reason, 'insight_discussion_invalid_reply')
                self.assertEqual(self.service._insights[insight['id']]['discussion_history'], [])

    async def test_label_written_inside_brackets_is_not_shown_twice(self):

        suggestion = '九月用水量比八月多近四成，同时新增六名同事、多一台饮水机。'

        insight = await self.insight(session='bracketed_label')

        self.delegate.responses.append(raw({

            'text': '可以这样写。',

            'replacement': '「' + REPLACEMENT_LABEL + suggestion + '」'}))

        reply = await self.service.reply(

            insight['id'], 'bracketed_label', 1, '只替换这一句。')

        self.assertEqual(reply['text'], '可以这样写。' + '\n\n' + REPLACEMENT_LABEL + suggestion)



    async def test_replacement_that_is_only_a_label_is_rejected(self):

        insight = await self.insight(session='label_only')

        self.delegate.responses.append(raw({

            'text': '可以这样写。',

            'replacement': REPLACEMENT_LABEL}))

        with self.assertRaises(InsightDiscussionError) as caught:

            await self.service.reply(

                insight['id'], 'label_only', 1, '只替换这一句。')

        self.assertEqual(caught.exception.reason, 'insight_discussion_invalid_reply')

    async def test_sensitive_draft_remains_local_quiet_without_a_provider_call(self):
        self.service.state('editor', 1, True)
        result = await self.service.analyze('editor', 1, '', BODY + ' api_key=FICTIONAL_PRIVATE', True)
        self.assertEqual(result['status'], 'quiet')
        self.assertEqual(result['reason'], 'sensitive_draft')
        self.assertEqual(self.delegate.requests, [])
        self.assertEqual(self.service._sessions['editor']['checks'], 0)


if __name__ == '__main__':
    unittest.main()
