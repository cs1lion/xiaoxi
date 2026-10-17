"""协议 §23 动态积木契约（Voice-First, Tail-Block）：python -B scripts/test_aqi_blocks.py

Covers:
1. AqiBlockFilter 字符级截留：口头文本 100% 无标签/JSON 残留；跨 delta 缓冲、
   引号内闭合签不误判、未闭合整体扣留、坏 JSON 与未知 kind 整体丢弃。
2. compose_* 快环直出：消歧/验收/进度/查账只用真实数据，空数据不出积木。
3. stream_respond 真链路：标签前文本实时进 TTS，积木聚合为一条 aqi_blocks
   先于 done 下发；非流式 respond 路径同样剥离。
4. executive_route 命中分支挂载合法 aqi_blocks（派单/消歧/查账/验收/打回）。
5. 便笺查询由服务端组装 note_snippet（真实选定版本正文）。
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import dotenv

os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.object(dotenv, 'load_dotenv', return_value=False):
    from server.dialogue import aqi_blocks as A
    from server.dialogue import orchestrator as O


def feed_all(text: str) -> tuple[str, list[dict]]:
    """整串喂进过滤器，返回 (口头文本, 积木列表)。"""
    parser = A.AqiBlockFilter()
    tokens: list[tuple[str, object]] = []
    for char in text:
        tokens.extend(parser.feed(char))
    tokens.extend(parser.finish())
    spoken = "".join(value for kind, value in tokens if kind == "text")
    blocks = [value for kind, value in tokens if kind == "aqi" and isinstance(value, dict)]
    return spoken, blocks


class AqiBlockFilterTests(unittest.TestCase):
    def test_text_before_tag_stays_clean(self):
        spoken, blocks = feed_all('接单了，这一单我盯着。<aqi-block>{"kind":"note_snippet","text":"他深夜改稿"}</aqi-block>')
        self.assertEqual(spoken, "接单了，这一单我盯着。")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["kind"], "note_snippet")
        self.assertEqual(blocks[0]["text"], "他深夜改稿")
        for banned in ("<aqi-block", "kind", "note_snippet", "{"):
            self.assertNotIn(banned, spoken)

    def test_tag_split_across_deltas(self):
        raw = '前半句<aqi-block>{"kind":"swimlane_strip","lanes":[{"agent_id":"scribe"}]}</aqi-block>后半句'
        spoken, blocks = feed_all(raw)
        self.assertEqual(spoken, "前半句后半句")
        self.assertEqual(blocks[0]["lanes"][0]["agent_id"], "scribe")

    def test_close_tag_inside_json_string_is_not_a_boundary(self):
        raw = '<aqi-block>{"kind":"note_snippet","text":"含</aqi-block>字样"}</aqi-block>尾巴'
        spoken, blocks = feed_all(raw)
        self.assertEqual(spoken, "尾巴")
        self.assertEqual(blocks[0]["text"], "含</aqi-block>字样")

    def test_unclosed_tag_is_withheld_at_eof(self):
        spoken, blocks = feed_all('说到一半<aqi-block>{"kind":"alert_card","title":"x"}')
        self.assertEqual(spoken, "说到一半")
        self.assertEqual(blocks, [])

    def test_malformed_json_is_dropped_whole(self):
        spoken, blocks = feed_all('前<aqi-block>不是json</aqi-block>后')
        self.assertEqual(spoken, "前后")
        self.assertEqual(blocks, [])

    def test_unknown_kind_is_dropped(self):
        spoken, blocks = feed_all('前<aqi-block>{"kind":"nope","text":"x"}</aqi-block>后')
        self.assertEqual(spoken, "前后")
        self.assertEqual(blocks, [])

    def test_schema_invalid_block_is_dropped(self):
        # note_snippet 缺 text：构造器返回 None，整块丢弃，文字不受影响。
        spoken, blocks = feed_all('前<aqi-block>{"kind":"note_snippet"}</aqi-block>后')
        self.assertEqual(spoken, "前后")
        self.assertEqual(blocks, [])

    def test_actions_and_layout_override_survive(self):
        raw = ('<aqi-block>{"kind":"alert_card","title":"熔断了","level":"critical",'
               '"layout":"drawer_ops","actions":[{"label":"去处理","cmd":"ui:drawer:ops"}]}</aqi-block>')
        _, blocks = feed_all(raw)
        self.assertEqual(blocks[0]["level"], "critical")
        self.assertEqual(blocks[0]["layout"], "drawer_ops")
        self.assertEqual(blocks[0]["actions"][0]["cmd"], "ui:drawer:ops")
        self.assertEqual(A.block_layout(blocks[0]), "drawer_ops")

    def test_partial_open_tag_flushes_as_text(self):
        spoken, blocks = feed_all("小于号 < 在这里")
        self.assertEqual(spoken, "小于号 < 在这里")
        self.assertEqual(blocks, [])

    def test_oversized_block_is_dropped(self):
        parser = A.AqiBlockFilter()
        tokens = []
        for char in '<aqi-block>{"kind":"note_snippet","text":"' + "啊" * 9000 + '"}</aqi-block>后':
            tokens.extend(parser.feed(char))
        tokens.extend(parser.finish())
        spoken = "".join(value for kind, value in tokens if kind == "text")
        self.assertEqual(spoken, "后")
        self.assertEqual([v for k, v in tokens if k == "aqi"], [None])

    def test_strip_aqi_blocks_for_non_streaming_path(self):
        self.assertEqual(A.strip_aqi_blocks('a<aqi-block>x</aqi-block>b'), "ab")
        self.assertEqual(A.strip_aqi_blocks('没标签'), '没标签')


class ComposeTests(unittest.TestCase):
    def test_choice_requires_two_real_pending(self):
        one = [{"id": "c1", "title": "《甲》"}]
        self.assertIsNone(A.compose_choice(one))
        two = [{"id": "c1", "title": "《甲》"}, {"id": "c2", "title": "《乙》"}]
        block = A.compose_choice(two)
        self.assertEqual(block["kind"], "choice_pills")
        self.assertEqual([o["cmd"] for o in block["options"]],
                         ["dispatch:accept:c1", "dispatch:accept:c2"])

    def test_accept_card_uses_real_delivery(self):
        block = A.compose_accept_card({"title": "《整理标题》",
                                       "delivery": {"summary": "整理成三版", "intercepted": "拦了一处",
                                                    "artifacts": [{"kind": "稿", "ref": "note:1"}]}})
        self.assertEqual(block["kind"], "spotlight_card")
        self.assertEqual(block["title"], "《整理标题》")
        self.assertEqual(block["artifacts"][0]["ref"], "note:1")

    def test_progress_strip_uses_real_lanes(self):
        block = A.compose_progress_strip([{"agent_id": "scribe", "agent_name": "文书",
                                           "title": "整理", "step_idx": 2, "status": "running"}])
        self.assertEqual(block["kind"], "swimlane_strip")
        lane = block["lanes"][0]
        self.assertEqual((lane["agent_id"], lane["step_idx"], lane["status"]), ("scribe", 2, "running"))

    def test_metric_flame_only_with_real_spans(self):
        self.assertEqual(A.compose_metric_flame({"calls": 0}), [])
        blocks = A.compose_metric_flame({"calls": 42, "total_tokens": 204000,
                                         "success_rate": 0.95, "avg_ttft_ms": 820},
                                        alerts=[{"agent_id": "scribe", "repeats": 3}])
        self.assertEqual([b["kind"] for b in blocks], ["metric_flame", "alert_card"])
        self.assertEqual(blocks[0]["metrics"][0], {"label": "调用", "value": 42})
        self.assertEqual(blocks[1]["level"], "critical")

    def test_message_envelope_always_carries_four_keys(self):
        message = A.aqi_blocks_message([A.note_snippet("正文")])
        self.assertEqual(message["type"], "aqi_blocks")
        self.assertEqual(message["layout"], "stage_holo")
        self.assertIsNone(message["nav"])
        self.assertIsNone(message["gaze"])
        self.assertEqual(len(message["blocks"]), 1)
        self.assertEqual(A.aqi_blocks_message([])["blocks"], [])
        self.assertEqual(A.aqi_blocks_message("坏输入")["blocks"], [])

    def test_block_cap_matches_frontend_deck(self):
        """单条消息的积木数必须等于前端 Holo-Deck 的 MAX_CARDS（web/blocks.js）。

        后端多发的那几张会被前端按"最旧先回收"静默丢掉——没有报错的降级。
        改任一边的数字，这里先红。
        """
        self.assertEqual(A._MAX_BLOCKS, 6)
        many = [A.note_snippet(f"第 {i} 条") for i in range(9)]
        self.assertEqual(len(A.aqi_blocks_message(many)["blocks"]), 6)

    def test_note_snippet_requires_text(self):
        self.assertIsNone(A.note_snippet("   "))
        self.assertIsNone(A.note_snippet(None))

    def test_memory_card_uses_real_stated_record(self):
        block = A.compose_memory_card({"id": 42, "kind": "fact", "source": "stated",
                                       "value": "用户对花生过敏",
                                       "provenance": "【你明确说过的】"})
        self.assertEqual(block["kind"], "note_snippet")
        self.assertEqual(block["ref"], "memory:42")
        self.assertEqual(block["text"], "用户对花生过敏")
        self.assertEqual(block["title"], "你提过的事")
        self.assertEqual(block["actions"], [{"label": "去星图看看", "cmd": "ui:starmap"}])

    def test_memory_card_rejects_speculation(self):
        for source in ("inferred", "uncertain"):
            self.assertIsNone(A.compose_memory_card(
                {"id": 1, "kind": "fact", "source": source, "value": "猜的"}))

    def test_memory_card_requires_positive_int_id_and_text(self):
        self.assertIsNone(A.compose_memory_card(
            {"id": 0, "kind": "fact", "source": "stated", "value": "有正文"}))
        self.assertIsNone(A.compose_memory_card(
            {"id": "42", "kind": "fact", "source": "stated", "value": "有正文"}))
        self.assertIsNone(A.compose_memory_card(
            {"id": 42, "kind": "fact", "source": "stated", "value": "  "}))
        self.assertIsNone(A.compose_memory_card(None))

    def test_memory_card_titles_by_kind(self):
        block = A.compose_memory_card({"id": 9, "kind": "self_state", "source": "stated",
                                       "value": "她最近怕吵"})
        self.assertEqual(block["title"], "她的自记")


class BlockKindContractTests(unittest.TestCase):
    """协议 §23.2 七种积木：构造器的字段约束、异常输入与真实数据铁律。"""

    def test_seven_kinds_all_have_a_builder(self):
        self.assertEqual(len(A.AQI_BLOCK_KINDS), 7)
        self.assertEqual(set(A._BUILDERS), set(A.AQI_BLOCK_KINDS))

    def test_memo_note_requires_title_and_body(self):
        self.assertIsNone(A.memo_note("", "正文"))
        self.assertIsNone(A.memo_note("标题", ""))
        self.assertIsNone(A.memo_note(None, None))
        block = A.memo_note("手头清净", "该收的都收了工。")
        self.assertEqual((block["kind"], block["title"], block["body"]),
                         ("memo_note", "手头清净", "该收的都收了工。"))
        self.assertNotIn("meta", block)

    def test_memo_note_meta_is_capped_and_typed(self):
        meta = {"在跑": 0, "待验收": 0, "近期收工": 3, "备注": "清静",
                "多余一": 1, "多余二": 2, "多余三": 3, "多余四": 4}
        block = A.memo_note("手头清净", "正文", meta=meta)
        self.assertEqual(len(block["meta"]), A._MAX_META_PAIRS)
        self.assertEqual(block["meta"]["在跑"], 0)
        # 坏类型整对丢弃，不让任意 JSON 值溜进小字
        block = A.memo_note("t", "b", meta={"好": 1, "坏": {"nested": 1}, "坏二": [1]})
        self.assertEqual(block["meta"], {"好": 1})

    def test_compose_memo_note_only_when_truly_calm(self):
        # 有单子在跑的时候递"手头清净"是诈骗：三类计数全 0 才出卡。
        self.assertIsNone(A.compose_memo_note(running=1))
        self.assertIsNone(A.compose_memo_note(pending=2))
        self.assertIsNone(A.compose_memo_note(done_recent=1))
        block = A.compose_memo_note(running=0, pending=0, done_recent=0)
        self.assertEqual(block["kind"], "memo_note")
        self.assertEqual(block["title"], "手头清净")
        self.assertEqual(block["meta"], {"在跑": 0, "待验收": 0})

    def test_memo_note_survives_the_stream_filter(self):
        raw = ('说一句。<aqi-block>{"kind":"memo_note","title":"手头清净",'
               '"body":"我就在这儿盯着。","meta":{"在跑":0}}</aqi-block>')
        parser = A.AqiBlockFilter()
        tokens = []
        for char in raw:
            tokens.extend(parser.feed(char))
        tokens.extend(parser.finish())
        spoken = "".join(v for k, v in tokens if k == "text")
        blocks = [v for k, v in tokens if k == "aqi" and isinstance(v, dict)]
        self.assertEqual(spoken, "说一句。")
        self.assertEqual(blocks[0]["kind"], "memo_note")
        self.assertEqual(blocks[0]["body"], "我就在这儿盯着。")

    def test_every_kind_rejects_its_own_empty_payload(self):
        """每个构造器在真实数据不足时返回 None——调用方据此闭嘴，不出占位卡。"""
        self.assertIsNone(A.choice_pills("", []))
        self.assertIsNone(A.choice_pills("问题", []))                  # 无选项
        self.assertIsNone(A.spotlight_card(""))
        self.assertIsNone(A.alert_card(""))
        self.assertIsNone(A.swimlane_strip([]))
        self.assertIsNone(A.swimlane_strip([{"status": "running"}]))   # 无 agent_id
        self.assertIsNone(A.metric_flame([]))
        self.assertIsNone(A.metric_flame([{"label": "调用"}]))         # 无 value
        self.assertIsNone(A.note_snippet(""))

    def test_builders_reject_non_string_and_empty_payloads(self):
        """构造器只认字符串正文；其它类型与空串一律不出卡（不编造内容）。"""
        for bad in (None, 42, [], True, {}, 3.5):
            self.assertIsNone(A.choice_pills(bad, bad), f"choice_pills({bad!r})")
            self.assertIsNone(A.spotlight_card(bad), f"spotlight_card({bad!r})")
            self.assertIsNone(A.alert_card(bad), f"alert_card({bad!r})")
            self.assertIsNone(A.swimlane_strip(bad), f"swimlane_strip({bad!r})")
            self.assertIsNone(A.metric_flame(bad), f"metric_flame({bad!r})")
            self.assertIsNone(A.note_snippet(bad), f"note_snippet({bad!r})")
            self.assertIsNone(A.memo_note(bad, bad), f"memo_note({bad!r})")
        for empty in ("", "   ", "\n\t"):
            self.assertIsNone(A.spotlight_card(empty))
            self.assertIsNone(A.alert_card(empty))
            self.assertIsNone(A.note_snippet(empty))
            self.assertIsNone(A.memo_note(empty, empty))
            self.assertIsNone(A.memo_note("标题", empty))

    def test_non_string_scalars_are_dropped_from_structured_fields(self):
        """options/lanes/metrics 里的坏条目整条丢弃，不拖垮同一块里的好条目。"""
        block = A.choice_pills("选哪单？", [{"label": "甲", "cmd": "say:甲"}, "坏", None,
                                           {"label": "乙"}, {"cmd": "say:乙"}, 7])
        self.assertEqual([o["label"] for o in block["options"]], ["甲"])
        block = A.swimlane_strip([{"agent_id": "scribe", "status": "running"}, {"status": "blocked"}, None, 5])
        self.assertEqual(len(block["lanes"]), 1)
        block = A.metric_flame([{"label": "调用", "value": 3}, {"label": "坏", "value": {"a": 1}},
                                {"value": 9}, None, "x"])
        self.assertEqual(block["metrics"], [{"label": "调用", "value": 3}])

    def test_unknown_kind_in_tag_is_dropped_not_rendered(self):
        self.assertIsNone(A.parse_aqi_block('{"kind":"not_a_kind","text":"x"}'))
        self.assertIsNone(A.parse_aqi_block('{"kind":"note_snippet"}'))   # 缺 text
        self.assertIsNone(A.parse_aqi_block('not json'))
        self.assertIsNone(A.parse_aqi_block('[]'))


class OrchestratorRouteBlockTests(unittest.TestCase):
    """executive_route 命中分支必须挂合法 aqi_blocks（真实台账数据）。"""

    def setUp(self):
        self.orch = O.orchestrator
        self._pending = []
        self._running = []
        self._summary = {}
        self._alerts = []
        self.executive = SimpleNamespace(
            pending_reports=lambda: list(self._pending),
            open_commissions=lambda: list(self._running),
            telemetry_summary=lambda: dict(self._summary),
            spinning_alerts=lambda: list(self._alerts),
            evaluator=SimpleNamespace(roster={"agents": [{"id": "scribe", "name": "文书"}]}),
        )

    def _route(self, text):
        return self.orch.executive_route(text, self.executive)

    def test_clarify_carries_choice_pills(self):
        self._pending = [{"id": "c1", "title": "《甲》"}, {"id": "c2", "title": "《乙》"}]
        route = self._route("好了吗")
        self.assertEqual(route["kind"], "clarify")
        blocks = route["aqi_blocks"]
        self.assertEqual([b["kind"] for b in blocks], ["choice_pills"])
        self.assertEqual(len(blocks[0]["options"]), 2)

    def test_accept_carries_spotlight_card(self):
        self._pending = [{"id": "c1", "title": "《整理标题》", "agent_id": "scribe",
                          "delivery": {"summary": "三版好了", "artifacts": []}}]
        route = self._route("收工")
        self.assertEqual(route["kind"], "accept")
        self.assertEqual([b["kind"] for b in route["aqi_blocks"]], ["spotlight_card"])

    def test_reject_carries_alert_card(self):
        self._pending = [{"id": "c1", "title": "《整理标题》", "agent_id": "scribe",
                          "delivery": {}, "audit": {}}]
        route = self._route("不行，重做")
        self.assertEqual(route["kind"], "reject")
        self.assertEqual([b["kind"] for b in route["aqi_blocks"]], ["alert_card"])

    def test_telemetry_carries_metric_flame(self):
        self._running = [{"id": "c1", "agent_id": "scribe", "status": "running"}]
        self._summary = {"calls": 12, "total_tokens": 900, "success_rate": 0.9, "avg_ttft_ms": 700}
        route = self._route("今天 token 烧了多少")
        self.assertEqual(route["kind"], "telemetry")
        self.assertEqual([b["kind"] for b in route["aqi_blocks"]], ["metric_flame"])

    def test_progress_carries_swimlane(self):
        self._running = [{"id": "c1", "agent_id": "scribe", "title": "整理", "status": "running"}]
        route = self._route("进度怎么样了")
        self.assertEqual(route["kind"], "progress")
        self.assertEqual([b["kind"] for b in route["aqi_blocks"]], ["swimlane_strip"])

    def test_delegation_carries_running_lanes_only(self):
        route = self._route("去把这份稿子整理一下")
        self.assertEqual(route["kind"], "delegation")
        self.assertEqual(route["aqi_blocks"], [])   # 没有在跑的单：不发占位积木
        self._running = [{"id": "c1", "agent_id": "scribe", "status": "running"}]
        route = self._route("去把这份稿子整理一下")
        self.assertEqual([b["kind"] for b in route["aqi_blocks"]], ["swimlane_strip"])

    def test_no_blocks_key_when_no_real_data(self):
        # 没有任何督办时普通对话不走快环，路由返回 None。
        self.assertIsNone(self._route("今天天气不错"))

    def test_note_block_from_real_activity_data(self):
        block = O.Orchestrator._note_block({"activity_data": {"note_data": {
            "body": "便笺正文前半段", "title": "周三的稿", "id": "n1",
            "ref": {"type": "note", "id": "n1", "version": 2}}}})
        self.assertEqual(block["kind"], "note_snippet")
        self.assertEqual(block["ref"], "note:n1")
        self.assertEqual(block["title"], "周三的稿")
        self.assertIsNone(O.Orchestrator._note_block({"activity_data": {"note_data": None}}))
        self.assertIsNone(O.Orchestrator._note_block({}))


class StreamRespondBlockTests(unittest.IsolatedAsyncioTestCase):
    """慢环流式真链路：标签前文本进 TTS，积木聚合一条先于 done。"""

    async def asyncSetUp(self):
        self.orch = O.Orchestrator()
        self.orch.pacing = copy.deepcopy(self.orch.pacing)
        self.orch.pacing["length"].update(
            default="short", short_max_chars=60, medium_max_chars=80, hard_limit_chars=120)
        self.saved: list[tuple] = []
        self.tts_calls: list[str] = []
        old_enabled = O.manager.enabled
        O.manager.enabled = False
        self.addCleanup(setattr, O.manager, "enabled", old_enabled)
        for patcher in (
            patch.object(O, "store_recent", return_value=[]),
            patch.object(O, "store_message", side_effect=lambda *args: self.saved.append(args)),
            patch.object(self.orch, "_spawn_extraction"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def collect(self, deltas):
        async def stream(*args, **kwargs):
            for delta in deltas:
                yield delta
                await asyncio.sleep(0)

        async def synthesize(text, emotion):
            self.tts_calls.append(text)
            return b"audio"

        with patch.object(O.llm, "stream", stream), patch.object(O.tts, "synthesize", synthesize):
            events = [event async for event in self.orch.stream_respond(
                session_id="test", user_text="这一单怎么样了")]
        return events

    async def test_tail_block_split_from_voice(self):
        events = await self.collect([
            "[调侃]接单了，这一单我盯着。",
            "<aqi-blo", "ck>{\"kind\":\"swimlane_strip\",\"lanes\":[{\"agent_id\":\"scribe\"}]}",
            "</aqi-block>",
        ])
        spoken = "".join(e["delta"] for e in events if e["type"] == "text_delta")
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["text"], spoken)
        self.assertEqual(spoken, "接单了，这一单我盯着。")
        for banned in ("<aqi", "kind", "lanes", "swimlane_strip", "{"):
            self.assertNotIn(banned, spoken)
            self.assertNotIn(banned, done["text"])
        self.assertNotIn("aqi", "".join(self.tts_calls))
        self.assertNotIn("{", "".join(self.tts_calls))
        blocks_events = [e for e in events if e["type"] == "aqi_blocks"]
        self.assertEqual(len(blocks_events), 1)
        message = blocks_events[0]
        self.assertEqual(message["layout"], "stage_holo")
        self.assertEqual(message["gaze"], "holo_right")
        self.assertEqual([b["kind"] for b in message["blocks"]], ["swimlane_strip"])
        # 积木先于 done 下发：她一边说，卡片一边到位。
        self.assertLess(events.index(message), events.index(done))
        # 入库文本同样干净。
        self.assertEqual(self.saved[-1][2], spoken)

    async def test_no_valid_block_means_no_message(self):
        events = await self.collect([
            "只说一句话。<aqi-block>{\"kind\":\"note_snippet\"}</aqi-block>",
        ])
        spoken = "".join(e["delta"] for e in events if e["type"] == "text_delta")
        self.assertEqual(spoken, "只说一句话。")
        self.assertEqual([e for e in events if e["type"] == "aqi_blocks"], [])

    async def test_multiple_blocks_aggregate_into_one_message(self):
        events = await self.collect([
            "两枚卡片。<aqi-block>{\"kind\":\"alert_card\",\"title\":\"熔断\"}</aqi-block>"
            "<aqi-block>{\"kind\":\"note_snippet\",\"text\":\"摘录\"}</aqi-block>",
        ])
        messages = [e for e in events if e["type"] == "aqi_blocks"]
        self.assertEqual(len(messages), 1)
        self.assertEqual([b["kind"] for b in messages[0]["blocks"]], ["alert_card", "note_snippet"])

    async def test_block_level_layout_promoted_to_message(self):
        events = await self.collect([
            "开抽屉。<aqi-block>{\"kind\":\"metric_flame\",\"metrics\":[{\"label\":\"调用\",\"value\":3}],"
            "\"layout\":\"drawer_ops\"}</aqi-block>",
        ])
        message = [e for e in events if e["type"] == "aqi_blocks"][0]
        self.assertEqual(message["layout"], "drawer_ops")

    async def test_unclosed_tag_never_speaks_or_renders(self):
        events = await self.collect(["说到一半<aqi-block>{\"kind\":\"alert_card\",\"title\":\"x\"}"])
        spoken = "".join(e["delta"] for e in events if e["type"] == "text_delta")
        self.assertEqual(spoken, "说到一半")
        self.assertEqual([e for e in events if e["type"] == "aqi_blocks"], [])
        self.assertNotIn("aqi", "".join(self.tts_calls))

    async def test_non_streaming_respond_strips_blocks(self):
        async def complete_raw(*args, **kwargs):
            return {"text": "[认真]好，记下了。<aqi-block>{\"kind\":\"note_snippet\",\"text\":\"x\"}</aqi-block>",
                    "finish_reason": "stop", "usage": {}}

        with patch.object(O.llm, "complete_raw", complete_raw):
            result = await self.orch.respond(session_id="test", user_text="记一下")
        self.assertEqual(result.text, "好，记下了。")

    # ---- 闲聊兜底：整轮无积木时，用本轮真实检索到的 stated 记忆补一枚 note_snippet ----

    def _fake_context(self, data):
        return {"messages": [{"role": "system", "content": "s"},
                             {"role": "user", "content": "u"}],
                "usage": {}, "used_ids": [], "data": data}

    _STATED = [{"id": 42, "kind": "fact", "source": "stated",
                "value": "用户对花生过敏", "provenance": "【你明确说过的】"}]

    async def test_memory_card_fills_idle_chat(self):
        data = {"long_term_memory": {"stated": self._STATED, "inferred": [],
                                     "uncertain": [], "status": "found", "enabled": True}}
        with patch.object(self.orch, "_turn_context", return_value=self._fake_context(data)):
            events = await self.collect(["记得呢，你上次说过。"])
        messages = [e for e in events if e["type"] == "aqi_blocks"]
        self.assertEqual(len(messages), 1)
        block = messages[0]["blocks"][0]
        self.assertEqual(block["kind"], "note_snippet")
        self.assertEqual(block["ref"], "memory:42")
        self.assertEqual(block["text"], "用户对花生过敏")
        # 积木仍先于 done：她一边说，卡片一边到位。
        self.assertLess(events.index(messages[0]), events.index(events[-1]))

    async def test_memory_card_not_stacked_with_model_blocks(self):
        data = {"long_term_memory": {"stated": self._STATED, "inferred": [],
                                     "uncertain": [], "status": "found", "enabled": True}}
        with patch.object(self.orch, "_turn_context", return_value=self._fake_context(data)):
            events = await self.collect([
                "两单里挑一个。<aqi-block>{\"kind\":\"choice_pills\",\"question\":\"哪单？\","
                "\"options\":[{\"label\":\"甲\",\"cmd\":\"dispatch:accept:c1\"},"
                "{\"label\":\"乙\",\"cmd\":\"dispatch:accept:c2\"}]}</aqi-block>",
            ])
        message = [e for e in events if e["type"] == "aqi_blocks"][0]
        self.assertEqual([b["kind"] for b in message["blocks"]], ["choice_pills"])

    async def test_explicit_note_block_beats_memory_card(self):
        data = {"activity_data": {"note_data": {"body": "便笺选定版正文", "title": "便笺",
                                                "id": "n1", "ref": {"id": "n1"}}},
                "long_term_memory": {"stated": self._STATED, "inferred": [],
                                     "uncertain": [], "status": "found", "enabled": True}}
        with patch.object(self.orch, "_turn_context", return_value=self._fake_context(data)):
            events = await self.collect(["翻开看看。"])
        message = [e for e in events if e["type"] == "aqi_blocks"][0]
        self.assertEqual([b["kind"] for b in message["blocks"]], ["note_snippet"])
        self.assertEqual(message["blocks"][0]["ref"], "note:n1")

    async def test_idle_chat_without_memory_stays_silent(self):
        data = {"long_term_memory": {"stated": [], "inferred": [], "uncertain": [],
                                     "status": "not_found", "enabled": True}}
        with patch.object(self.orch, "_turn_context", return_value=self._fake_context(data)):
            events = await self.collect(["就随便聊聊。"])
        self.assertEqual([e for e in events if e["type"] == "aqi_blocks"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
