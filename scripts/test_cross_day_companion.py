"""跨天语音陪伴验收：三天式场景（Day1 共建 → Day2 恢复 → Day3 续做与修正）。

复用 AssistantService + 临时库 + 真实确认路由 + reopen() 重建服务，
不碰真实数据库、不调模型（模型边界由注入的 resolver 承担）。

本测试只证明**管线与状态机**正确：事项恢复、版本血缘、决定/问题/进展的区分、
以及自然语言「接着昨天的继续」能否走对路。它**不能**证明真实模型的语义能力——
resolver 是脚本化的，真实 LLM 的输出质量需另做离线评测（与 docs/EVOLUTION_QUALITY_EVAL.md
同类的「机制通过 ≠ 语义合格」声明）。
"""
from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from server.assistant.service import AssistantService
from server.interaction.proposals_api import create_proposals_router
from server.interaction.threads_api import create_threads_router


class CrossDayCompanionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aqi-cross-day-')
        self.directory = Path(self.temp.name)
        self.manager = SimpleNamespace(enabled=True)
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True), None)
        self.service.memory_manager = self.manager
        self.thread = self.service.threads.create('周六出行计划')
        self.tid = self.thread['id']
        self.service.participation.select(self.tid)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    # ---------- 基础设施：与 test_thread_continuity.py 同一套真实确认路由 ----------

    def reopen(self):
        """模拟「程序重启 / 新建会话」：同一目录重建服务，不继承任何内存态。"""
        self.service.close()
        self.service = AssistantService(self.directory, SimpleNamespace(offline=True), None)
        self.service.memory_manager = self.manager

    def confirm_http(self, candidate):
        app = FastAPI()
        app.include_router(create_threads_router(self.service))
        app.include_router(create_proposals_router(self.service))
        with TestClient(app) as client:
            response = client.post('/api/thread-proposals/' + candidate['id'] + '/confirm',
                                   json={'expected_version': candidate['version']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def scripted_resolver(self, script):
        """把「模型层」换成脚本：按用户原话返回预定 action，验证的是状态机不是语义。"""
        async def resolve(payload):
            return script(payload)
        self.service.thread_conversation.resolver = resolve
        return resolve

    def live_events(self):
        return self.service.threads.get(self.tid)['events']

    def decisions(self):
        return [e for e in self.live_events() if e['kind'] == 'decision']

    def questions(self):
        return [e for e in self.live_events() if e['kind'] == 'question']

    def link_draft(self, note, purpose='draft'):
        """把可持久化成果挂成本事项的资料——决定才会带上它的精确版本引用。"""
        live = self.service.threads.get(self.tid)
        self.thread = self.service.threads.link_material(
            self.tid, expected_revision=live['revision'], note_id=note['id'],
            version=note['version'], purpose=purpose)
        return self.thread

    def advance_draft(self, note):
        """推进到新版本：旧版本仍可追溯，当前版本随决定一起更新。"""
        live = self.service.threads.get(self.tid)
        material = next(item for item in live['materials'] if item['note_id'] == note['id'])
        self.thread = self.service.threads.update_material(
            self.tid, material['id'], expected_revision=live['revision'], version=note['version'])
        return self.thread

    async def record(self, utterance, session):
        result = await self.service.start(utterance, session)
        if result.get('proposal'):
            self.confirm_http(result['proposal'])
        return result

    # ---------- Day 1：一起完成一件事 ----------

    async def test_day1_builds_a_real_matter_with_decisions_questions_and_versions(self):
        # 可持久化成果（便笺草案）先于决定存在，决定才会带上它的版本引用
        note = self.service.notes.create('出行草案', '先查天气，再定交通。')
        self.link_draft(note)
        await self.record('记个决定：周六骑共享单车沿河走', 'day1-chat')
        bike = self.decisions()[0]

        # 未决定的问题：必须留在 question，不能被提升成 decision
        await self.record('记个问题：中午在哪吃还没定', 'day1-chat')
        self.assertEqual([e['text'] for e in self.questions()], ['中午在哪吃还没定'])
        self.assertEqual(len(self.decisions()), 1)

        await self.record('记个进展：查完了天气，周六有雨', 'day1-chat')

        # 变更条件 → 修订原决定（同一 event 追加版本，不是新增一条平行决定）
        note2 = self.service.notes.update(note['id'], '雨天出行草案', '周六预报有雨，沿河拍照暂缓。', 1)
        self.advance_draft(note2)
        self.scripted_resolver(lambda payload: {
            'action': 'revise_event', 'kind': 'decision', 'target_event_id': bike['id'],
            'text': '周六改坐地铁，因预报有雨，沿河拍照暂缓'})
        await self.record('把共享单车改成地铁，因为周六有雨', 'day1-chat')

        self.scripted_resolver(lambda payload: {
            'action': 'next_step', 'text': '确认中午吃饭的点，然后订地铁票'})
        await self.record('下一步先订地铁票', 'day1-chat')

        thread = self.service.threads.get(self.tid)
        self.assertEqual(thread['next_step'], '确认中午吃饭的点，然后订地铁票')
        # 当前视图只有一条 decision，且是最新文本
        self.assertEqual([e['text'] for e in self.decisions()],
                         ['周六改坐地铁，因预报有雨，沿河拍照暂缓'])
        history = self.service.threads.event_history(self.tid, bike['id'])
        self.assertEqual([v['version'] for v in history], [1, 2])
        self.assertEqual(history[0]['text'], '周六骑共享单车沿河走')
        self.assertEqual(history[1]['text'], '周六改坐地铁，因预报有雨，沿河拍照暂缓')
        self.assertEqual([v['object_refs'][0]['version'] for v in history], [1, 2])

    # ---------- Day 2：恢复昨天的工作 ----------

    async def test_day2_natural_continuation_restores_the_matter(self):
        await self._build_day1()
        # 大量后续进展：绝不能把早先的决定/问题挤出可恢复范围
        for i in range(20):
            live = self.service.threads.get(self.tid)
            self.service.threads.add_event(self.tid, expected_revision=live['revision'],
                                           kind='progress', text=f'核对了第 {i} 个备选地点')
        self.reopen()
        self.assertEqual(self.service.participation.selected_id, self.tid)

        result = await self.service.start('接着昨天的继续', 'day2-chat')
        text = result['text']

        self.assertIn('周六出行计划', text, '恢复时必须点明是哪个事项')
        self.assertIn('周六改坐地铁', text, '恢复时必须给出最终有效决定')
        self.assertNotIn('周六骑共享单车沿河走', text,
                         '已被修订的旧决定不能当成当前决定复述')
        self.assertIn('中午在哪吃还没定', text, '未决定的问题必须被带回')
        self.assertIn('待确认', text, '未决定项要标注为待确认，不能说成已决定')
        self.assertIn('确认中午吃饭的点', text, '下一步必须被带回')
        # 跨天恢复必须说清"当前是哪一稿"——Day1 已把草案推进到 v2 并据此改了决定
        self.assertIn('雨天出行草案', text, '恢复时要点名关联的成果资料')
        self.assertIn('当前版本 2', text, '必须讲明白聊的是当前这一版')

    async def test_day2_routes_the_natural_phrasings(self):
        await self._build_day1()
        self.reopen()
        # 走服务层的真实路由门（thread_conversation 与 _resume_request 的合取），
        # 而不是单个子模块的判定——用户说的是给人听的那一句。
        for phrase in ('接着昨天的继续', '继续昨天的工作', '接着昨天的', '继续昨天那件事',
                       '我们继续吧', '继续推进'):
            with self.subTest(phrase=phrase):
                self.assertTrue(self.service.should_route(phrase, 'day2-chat'),
                                f'「{phrase}」应被识别为继续共同事项')

    async def test_day2_explicit_forms_route_but_ordinary_questions_do_not(self):
        """词法门只收显式接续/修正/下一步指令；普通提问和日常说法仍走闲聊。"""
        await self._build_day1()
        self.reopen()
        for phrase in ('下一步做什么？', '下一步是什么', '下一步该做什么',
                       '改天再聊', '改天吧', '继续加油', '继续努力',
                       '他说“改成步行”', '要不周六步行吧，我觉得走路挺好'):
            with self.subTest(phrase=phrase):
                self.assertFalse(self.service.should_route(phrase, 'day2-chat'),
                                 f'「{phrase}」不是对事项的指令，不能被劫走')
        for phrase in ('下一步先订地铁票', '下一步：订地铁票', '改步行',
                       '地铁也不行了，改步行', '设置下一步'):
            with self.subTest(phrase=phrase):
                self.assertTrue(self.service.should_route(phrase, 'day2-chat'),
                                f'「{phrase}」是对当前事项的显式指令')

    async def test_day2_restores_even_without_a_prior_explicit_selection(self):
        """跨天恢复不能依赖「昨天恰好选过那个事项」——新会话/清空选择也要能接上。"""
        await self._build_day1()
        self.reopen()
        self.service.participation.select(None)          # 模拟全新会话：没有任何显式选择
        result = await self.service.start('接着昨天的继续', 'day2-chat')
        self.assertIn('周六出行计划', result['text'], '没有显式选择时也要按最近推进的事项恢复')

    async def test_day2_pinned_draft_is_not_silently_switched_to_the_newer_version(self):
        """稿子推进到 v2、但事项仍钉在 v1：恢复时必须讲明白，不能悄悄换版。"""
        await self._build_day1()
        # 把关联资料退回到 v1（用户明确要按旧稿继续），同时稿子本身已有 v2
        live = self.service.threads.get(self.tid)
        material = live['materials'][0]
        older = self.service.notes.get_version(material['note_id'], 1)
        self.thread = self.service.threads.update_material(
            self.tid, material['id'], expected_revision=live['revision'], version=older['version'])
        self.reopen()
        result = await self.service.start('接着昨天的继续', 'day2-chat')
        self.assertIn('是版本 1', result['text'], '必须讲明白事项钉在版本 1')
        self.assertIn('已经是版本 2', result['text'], '必须说明稿子已有版本 2')
        self.assertIn('没有自动切过去', result['text'], '必须声明没有擅自切换版本')
        # 快照里的精确版本也要对得上
        self.assertEqual(result['activity']['note']['selected_version'], 1)
        self.assertEqual(result['activity']['note']['current_version'], 2)

    async def test_day2_correction_updates_state_with_new_evidence(self):
        await self._build_day1()
        self.reopen()
        await self.service.start('接着昨天的继续', 'day2-chat')
        decision = self.decisions()[0]
        self.scripted_resolver(lambda payload: {
            'action': 'revise_event', 'kind': 'decision', 'target_event_id': decision['id'],
            'text': '周六改成步行，地铁也暂缓'})
        await self.record('地铁也不行了，改步行', 'day2-chat')
        self.assertEqual([e['text'] for e in self.decisions()], ['周六改成步行，地铁也暂缓'])

    # ---------- Day 3：继续、打断与修正 ----------

    async def test_day3_keeps_decision_lineage_and_uses_latest_context(self):
        await self._build_day1()
        self.reopen()
        await self.service.start('接着昨天的继续', 'day2-chat')

        decision = self.decisions()[0]
        self.scripted_resolver(lambda payload: {
            'action': 'revise_event', 'kind': 'decision', 'target_event_id': decision['id'],
            'text': '周六改成步行，地铁也暂缓'})
        await self.record('地铁也不行了，改步行', 'day3-chat')

        history = self.service.threads.event_history(self.tid, decision['id'])
        self.assertEqual([v['version'] for v in history], [1, 2, 3])
        self.assertEqual([v['text'] for v in history],
                         ['周六骑共享单车沿河走',
                          '周六改坐地铁，因预报有雨，沿河拍照暂缓',
                          '周六改成步行，地铁也暂缓'])
        self.assertEqual([e['text'] for e in self.decisions()], ['周六改成步行，地铁也暂缓'])

        # 续做时用的是最新有效上下文
        view = self.service.participation.project(self.service.threads.get(self.tid), '现在怎么走的')
        rendered = '\n'.join(e['text'] for e in view['events'])
        self.assertIn('周六改成步行', rendered)
        self.assertNotIn('周六骑共享单车', rendered)

        # 用户不必重讲已可靠保存的信息：再次接着继续，直接带出最新状态
        result = await self.service.start('接着昨天的继续', 'day3-chat')
        self.assertIn('周六改成步行', result['text'])
        self.assertNotIn('周六骑共享单车沿河走', result['text'])

    async def test_day3_stale_decision_is_never_presented_as_current(self):
        await self._build_day1()
        self.reopen()
        await self.service.start('接着昨天的继续', 'day2-chat')
        decision = self.decisions()[0]
        self.scripted_resolver(lambda payload: {
            'action': 'revise_event', 'kind': 'decision', 'target_event_id': decision['id'],
            'text': '周六改成步行，地铁也暂缓'})
        await self.record('改步行', 'day3-chat')
        # 普通闲聊路径注入的事项上下文同样只能看到当前版本
        bundle = self.service.context_broker.build('那我们周六怎么走', 'day3-chat')
        view = bundle['response_policy'].get('companion_thread')
        self.assertIsNotNone(view)
        texts = [e['text'] for e in view['events']]
        self.assertIn('周六改成步行，地铁也暂缓', texts)
        self.assertNotIn('周六骑共享单车沿河走', texts)

    async def test_day3_discussed_is_not_promoted_to_decided(self):
        """讨论/提议 ≠ 决定：没有显式「记个决定」就不落 decision。

        注意这里不断言 status——那句提议可能路由到别处（闲聊/澄清），
        真正的铁律是"台账里没有多出任何决定"。
        """
        await self._build_day1()
        self.reopen()
        self.scripted_resolver(lambda payload: {'action': 'ignore'})
        await self.service.start('要不周六步行吧，我觉得走路挺好', 'day3-chat')
        self.assertEqual([e['text'] for e in self.decisions()],
                         ['周六改坐地铁，因预报有雨，沿河拍照暂缓'])
        self.assertEqual([e['text'] for e in self.questions()], ['中午在哪吃还没定'])

    async def test_day3_spoken_line_and_carrying_cards_agree(self):
        """口述、注入上下文与随路积木必须三方一致，且都只认最新决定。

        这条链路此前没有任何测试把 text_delta / companion_thread / aqi_blocks
        放在一起比对——两边各测各的，就会出现"她嘴里说新版、卡片显示旧版"的分裂。
        模型层脚本化，所以验的是"喂给模型的上下文是新的"和"积木不带旧决定"。
        """
        await self._build_day1()
        self.reopen()
        await self.service.start('接着昨天的继续', 'day2-chat')
        decision = self.decisions()[0]
        self.scripted_resolver(lambda payload: {
            'action': 'revise_event', 'kind': 'decision', 'target_event_id': decision['id'],
            'text': '周六改成步行，地铁也暂缓'})
        await self.record('地铁也不行了，改步行', 'day3-chat')

        seen = await self._run_one_turn('那我们周六怎么走')
        latest = self.decisions()[-1]['text']
        # 没有注入上下文时 json.dumps(None) 会让下面的"不能包含"全部空转，先钉住
        # 真实形状：这一轮确实带着当前事项和最新决定。
        self.assertIsNotNone(seen['thread'], '这一轮必须注入当前共同事项')
        self.assertEqual(seen['thread']['title'], '周六出行计划')
        self.assertIn(latest, [e['text'] for e in seen['thread']['events']])
        # (1) 喂给模型的上下文带的是最新决定
        injected = json.dumps(seen['thread'], ensure_ascii=False)
        self.assertIn('周六改成步行', injected, '注入回答的上下文必须是最新决定')
        self.assertNotIn('周六骑共享单车', injected, '上下文不能带已作废的旧决定')
        self.assertNotIn('周六改坐地铁', injected, '上下文不能带被再次修订的中间决定')
        # (2) 随路积木同样只认最新决定
        self.assertTrue(seen['blocks'], '这一轮应当产出随路积木')
        self.assertEqual(seen['block_event']['source'], 'chat', '慢环积木来源应为 chat')
        self.assertEqual(seen['block_event']['layout'], 'stage_holo', '默认落在主舞台')
        self.assertEqual([b['text'] for b in seen['blocks'] if b['kind'] == 'note_snippet'],
                         [latest], '积木正文必须与台账当前决定逐字一致')
        rendered = json.dumps(seen['blocks'], ensure_ascii=False)
        self.assertIn('周六改成步行', rendered, '积木内容应来自最新决定')
        self.assertNotIn('周六骑共享单车', rendered, '积木不能带已作废的旧决定')
        self.assertNotIn('周六改坐地铁', rendered, '积木不能带被再次修订的中间决定')
        # (3) 标签 JSON 绝不进语音：嘴里说的那一句里没有积木原文
        self.assertIn('周六', seen['spoken'], '口述要回应这次周六出行')
        self.assertNotIn('周六骑共享单车', seen['spoken'], '口述不能复述旧决定')
        self.assertNotIn('周六改坐地铁', seen['spoken'], '口述不能复述中间决定')
        for banned in ('aqi-block', '"kind"', '{'):
            self.assertNotIn(banned, seen['spoken'], '积木标签不能漏进口述')

    async def _run_one_turn(self, utterance):
        """跑一轮真实慢环对话，回收口述 / 注入上下文 / 随路积木。"""
        # llm.stream 逐段吐字符串（不是事件 dict）——orchestrator 自己分句、派发。
        async def stream(*args, **kwargs):
            yield '那就周六步行。'
            yield ('<aqi-block>{"kind":"note_snippet","text":"周六改成步行，地铁也暂缓",'
                   '"ref":"note:1"}</aqi-block>')

        async def synthesize(text, emotion):
            return b'audio'

        captured = {}
        from unittest.mock import patch
        from server.dialogue import orchestrator as O
        # app.py wires the global orchestrator to the live service. The test must
        # do the same, or the spy sees a broker with no selected matter.
        original_context = O.orchestrator._turn_context

        def spy_context(user_text, session_id, recent):
            data = original_context(user_text, session_id, recent)
            captured['data'] = data
            return data

        with (patch.object(O.orchestrator, 'context_broker', self.service.context_broker),
              patch.object(O.orchestrator, 'activity_facade', self.service.activity, create=True),
              patch.object(O.llm, 'stream', stream),
              patch.object(O.tts, 'synthesize', synthesize),
              patch.object(O.orchestrator, '_turn_context', spy_context)):
            events = [event async for event in O.orchestrator.stream_respond(
                session_id='day3-chat', user_text=utterance)]
        captured_turn = captured.get('data') or {}
        injected = captured_turn.get('data') or {}
        return {
            'spoken': ''.join(e['delta'] for e in events if e['type'] == 'text_delta'),
            'blocks': [b for e in events if e['type'] == 'aqi_blocks' for b in e['blocks']],
            'block_event': next((e for e in events if e['type'] == 'aqi_blocks'), None),
            'data': captured_turn,
            # _turn_context returns {'messages', 'usage', 'used_ids', 'data'};
            # the companion thread lives inside the inner data payload.
            'thread': injected.get('companion_thread'),
        }

    # ---------- 共用 fixture ----------

    async def _build_day1(self):
        note = self.service.notes.create('出行草案', '先查天气，再定交通。')
        self.link_draft(note)
        await self.record('记个决定：周六骑共享单车沿河走', 'day1-chat')
        await self.record('记个问题：中午在哪吃还没定', 'day1-chat')
        await self.record('记个进展：查完了天气，周六有雨', 'day1-chat')
        note2 = self.service.notes.update(note['id'], '雨天出行草案', '周六预报有雨，沿河拍照暂缓。', 1)
        self.advance_draft(note2)
        bike = self.decisions()[0]
        self.scripted_resolver(lambda payload: {
            'action': 'revise_event', 'kind': 'decision', 'target_event_id': bike['id'],
            'text': '周六改坐地铁，因预报有雨，沿河拍照暂缓'})
        await self.record('把共享单车改成地铁，因为周六有雨', 'day1-chat')
        self.scripted_resolver(lambda payload: {
            'action': 'next_step', 'text': '确认中午吃饭的点，然后订地铁票'})
        await self.record('下一步先订地铁票', 'day1-chat')


if __name__ == '__main__':
    unittest.main(verbosity=2)
