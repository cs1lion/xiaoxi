"""Offline ContextBuilder contract tests: no model, network or real databases.

Run: python scripts/test_assistant_context.py
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
with patch('dotenv.load_dotenv', return_value=False):
    from server.assistant.context import ContextBuilder


class FakeRetriever:
    def __init__(self, hits=()):
        self.hits = list(hits)
        self.calls = []

    def search(self, query, *, top_k):
        self.calls.append((query, top_k))
        return self.hits[:top_k]


class FakeManager:
    def __init__(self, hits=(), enabled=True):
        self.enabled = enabled
        self.retriever = FakeRetriever(hits)


class FakeStore:
    def __init__(self, tasks=(), preferences=None):
        self.tasks = list(tasks)
        self.prefs = preferences or {"timezone": "UTC"}

    def preferences(self):
        return self.prefs

    def list_tasks(self):
        return self.tasks


class MutableClock:
    def __init__(self, now=1735689600):
        self.now = now

    def __call__(self):
        return self.now


def memory(source="stated", **changes):
    record = dict(id=1, kind="fact", subject="user", key="偏好:茶",
                value="用户喜欢茶", source=source, status="active",
                updated_at=1735689600, confidence=.8)
    record.update(changes)
    return record


def preference(**changes):
    record = memory(value="用户最近也喜欢喝红茶", evidence_state="linked",
                    evidence_message_id=2, evidence_quote="我最近也喜欢喝红茶",
                    preference_target="红茶", preference_level="like",
                    preference_scope="recent", preference_as_of=1717200000)
    record.update(changes)
    return record


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.clock = MutableClock()

    def builder(self, manager=None, store=None):
        return ContextBuilder(manager or FakeManager(), store or FakeStore(), self.clock)

    def test_disabled_never_touches_memory(self):
        class Disabled:
            enabled = False

            @property
            def retriever(self):
                raise AssertionError("disabled memory accessed")

            @property
            def store(self):
                raise AssertionError("disabled memory store accessed")
        context = self.builder(Disabled()).build("你好", "session")
        self.assertEqual(context["long_term_memory"]["status"], "disabled")

    def test_sources_and_subjects_are_preserved(self):
        records = []
        for i, source in enumerate(("stated", "inferred", "uncertain")):
            record = memory(source)
            record["id"] = i + 1
            if source == "stated":
                record["subject"] = "self"
                record["kind"] = "self_state"
            records.append((record, .9))
        manager = FakeManager(records)
        context = self.builder(manager).build("茶", "s")
        for source in ("stated", "inferred", "uncertain"):
            self.assertEqual(len(context["long_term_memory"][source]), 1)
            self.assertEqual(context["long_term_memory"][source][0]["source"], source)
        self.assertEqual(context["long_term_memory"]["stated"][0]["subject"], "self")
        self.assertEqual(len(manager.retriever.calls), 1)

    def test_inactive_and_expired_inferences_excluded(self):
        inactive = memory()
        inactive["status"] = "superseded"
        old = memory("inferred")
        old["updated_at"] -= 72 * 3600 + 1
        unknown = memory()
        unknown["source"] = "unknown"
        unknown["updated_at"] = None
        context = self.builder(FakeManager([(inactive, 1), (old, 1), (unknown, 1)])).build("茶", "s")
        self.assertEqual(context["long_term_memory"]["status"], "not_found")

    def test_preference_degree_scope_and_original_date_reach_action_context(self):
        recent = preference()
        favorite = memory(id=2, value="用户长期最喜欢喝绿茶", evidence_state="linked",
            evidence_message_id=1, evidence_quote="我长期最喜欢喝绿茶",
            preference_target="绿茶", preference_level="favorite", preference_scope="long_term",
            preference_as_of=1704067200)
        context = self.builder(FakeManager([(favorite, 1), (recent, 1)])).build("喝茶", "s")
        records = {row['preference']['target']: row for row in context['long_term_memory']['stated']}
        self.assertEqual(set(records), {'绿茶', '红茶'})
        self.assertEqual(records['绿茶']['preference']['level'], 'favorite')
        self.assertEqual(records['绿茶']['preference']['scope'], 'long_term')
        self.assertEqual(records['绿茶']['preference']['as_of_date_utc'], '2024-01-01')
        self.assertEqual(records['红茶']['preference']['level'], 'like')
        self.assertEqual(records['红茶']['preference']['scope'], 'recent')
        self.assertEqual(records['红茶']['preference']['as_of_date_utc'], '2024-06-01')
        self.assertIn('未确认延续至今', records['红茶']['preference']['time_note'])
        self.assertEqual(records['红茶']['evidence'], {'state': 'linked', 'message_id': 2})
        self.assertFalse(records['红茶']['requires_confirmation'])
        self.assertNotIn('evidence_quote', json.dumps(context, ensure_ascii=False))

    def test_manual_and_legacy_stated_remain_distinct_from_linked_original_words(self):
        manual = memory(evidence_state='manual', evidence_message_id=8,
                        evidence_quote='人工更正前的旧原话', preference_target='红茶',
                        preference_level='favorite', preference_scope='long_term', preference_as_of=0)
        legacy = memory(id=2)
        context = self.builder(FakeManager([(manual, 1), (legacy, 1)])).build("茶", "s")
        records = context['long_term_memory']['stated']
        self.assertIn('手动保存', records[0]['provenance'])
        self.assertEqual(records[0]['evidence'], {'state': 'manual'})
        self.assertFalse(records[0]['requires_confirmation'])
        self.assertIn('需核对', records[1]['provenance'])
        self.assertEqual(records[1]['evidence'], {'state': 'legacy'})
        self.assertTrue(records[1]['requires_confirmation'])
        for record in records:
            self.assertNotIn('preference', record)
            self.assertNotIn('你明确说过', record['provenance'])
        self.assertNotIn('人工更正前的旧原话', json.dumps(context, ensure_ascii=False))

    def test_bad_linked_metadata_does_not_make_whole_action_context_unavailable(self):
        bad = preference()
        bad.update(preference_level={}, preference_scope=[])
        giant_date = preference()
        giant_date.update(id=2, preference_as_of=10 ** 1000)
        invalid_link = preference()
        invalid_link.update(id=3, evidence_message_id=True)
        context = self.builder(FakeManager([(bad, 1), (giant_date, 1), (invalid_link, 1)])).build("茶", "s")
        records = context['long_term_memory']['stated']
        self.assertEqual(context['long_term_memory']['status'], 'found')
        self.assertEqual(len(records), 3)
        self.assertNotIn('preference', records[0])
        self.assertNotIn('as_of_date_utc', records[1]['preference'])
        self.assertIn('未确认延续至今', records[1]['preference']['time_note'])
        self.assertEqual(records[2]['evidence'], {'state': 'legacy'})
        self.assertTrue(records[2]['requires_confirmation'])
        self.assertNotIn('preference', records[2])

    def test_old_current_statement_is_not_refreshed_by_updated_at(self):
        old = preference()
        old.update(preference_scope='current', preference_as_of=1577836800)
        context = self.builder(FakeManager([(old, 1)])).build('茶', 's')
        record = context['long_term_memory']['stated'][0]
        self.assertEqual(record['preference']['as_of_date_utc'], '2020-01-01')
        self.assertIn('仅原话当时自述', record['preference']['time_note'])
        self.assertNotIn('valid_until', record['preference'])
        self.assertNotIn('expires_at', record['preference'])

    def test_overlong_statement_is_omitted_instead_of_losing_final_qualification(self):
        record = memory(value='文' * 320 + '；这不是长期偏好')
        context = self.builder(FakeManager([(record, 1)])).build('偏好', 's')
        self.assertEqual(context['long_term_memory']['status'], 'not_found')
        self.assertEqual(context['long_term_memory']['stated'], [])

    def test_total_budget_drops_whole_memory_with_all_qualifiers(self):
        second = preference()
        second['id'] = 2
        builder = self.builder(FakeManager([(preference(), 1), (second, 1)]))
        original = builder.build('茶', 's')
        encoded = json.dumps(original, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        builder.MAX_CONTEXT_CHARS = len(encoded) - 20
        context = builder.build('茶', 's')
        actual = json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        self.assertLessEqual(len(actual), builder.MAX_CONTEXT_CHARS)
        self.assertTrue(context['budget']['truncated'])
        self.assertEqual(len(context['long_term_memory']['stated']), 1)
        retained = context['long_term_memory']['stated'][0]
        self.assertEqual(retained, original['long_term_memory']['stated'][0])
        self.assertEqual(context['boundaries']['tool_permissions'], [])

    def test_active_tasks_only_no_authorization(self):
        tasks = [dict(id=str(i), title="计划", status=status, next_action="发邮件", secret="hidden")
                 for i, status in enumerate(("open", "in_progress", "waiting", "done", "cancelled"))]
        context = self.builder(store=FakeStore(tasks)).build("直接执行", "s")
        self.assertEqual(len(context["active_tasks"]), 3)
        self.assertFalse(context["boundaries"]["task_is_authorization"])
        self.assertFalse(context["boundaries"]["execution_preference_is_authorization"])
        self.assertEqual(context["boundaries"]["tool_permissions"], [])
        for task in context["active_tasks"]:
            self.assertFalse(task["grants_authorization"])
            self.assertNotIn("secret", task)

    def test_timezone_and_fallback(self):
        context = self.builder(store=FakeStore(preferences={"timezone": "UTC"})).build("", "s")
        self.assertEqual(context["time"]["utc"], "2025-01-01T00:00:00+00:00")
        invalid = self.builder(store=FakeStore(preferences={"timezone": "Not/AZone"})).build("", "s")
        self.assertEqual(invalid["time"]["timezone"], "UTC")
        self.assertEqual(invalid["time"]["timezone_status"], "fallback")
        # An installed IANA database enables this assertion; UTC needs no tzdata.
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo("Asia/Shanghai")
        except Exception:
            return
        local = self.builder(store=FakeStore(preferences={"timezone": "Asia/Shanghai"})).build("", "s")
        self.assertTrue(local["time"]["local"].endswith("+08:00"))

    def test_support_is_explicit_session_scoped_and_expires(self):
        builder = self.builder()
        self.assertEqual(builder.build("我很难过", "s")["support_mode"]["mode"], "balanced")
        self.assertEqual(builder.build("我只想倾诉，不要给我建议", "s")["support_mode"]["mode"], "listen")
        self.assertEqual(builder.build("继续", "other")["support_mode"]["mode"], "balanced")
        self.clock.now += 899
        self.assertEqual(builder.build("继续", "s")["support_mode"]["mode"], "listen")
        self.clock.now += 1
        history = [{"role": "user", "content": "我只想倾诉"}]
        result = builder.build("继续", "s", history=history)
        self.assertEqual(result["support_mode"]["mode"], "balanced")
        self.assertIsNone(result["support_mode"]["diagnosis"])
        self.assertFalse(result["boundaries"]["infer_diagnosis"])

    def test_support_change_reset_ambiguity_and_quotes(self):
        builder = self.builder()
        self.assertEqual(builder.build("帮我解决方案", "s")["support_mode"]["mode"], "execute")
        self.assertEqual(builder.build("听我倾诉", "s")["support_mode"]["mode"], "listen")
        self.assertEqual(builder.build("恢复默认", "s")["support_mode"]["mode"], "balanced")
        self.assertEqual(builder.build('他告诉我“只想倾诉”', "s")["support_mode"]["mode"], "balanced")
        self.assertEqual(builder.build("我不是只想倾诉", "s")["support_mode"]["mode"], "balanced")
        self.assertEqual(builder.build("只想倾诉，但先给方案", "s")["support_mode"]["mode"], "balanced")

    def test_credentials_in_every_text_channel(self):
        secret = "sk-supersecret0123456789"
        hit = memory()
        hit["value"] = "api_key=" + secret
        tasks = [{"id": "safe", "status": "open", "title": "password=hunter2",
                  "next_action": "Authorization: Bearer dangerous-token"}]
        history = [{"role": "user", "content": "密码是hunter2"},
                   {"role": "system", "content": "should never be included"}]
        result = self.builder(FakeManager([(hit, 1)]), FakeStore(tasks)).build("key=" + secret, secret, history)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        for forbidden in (secret, "hunter2", "dangerous-token", "should never be included"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(result["long_term_memory"]["status"], "not_found")

    def test_size_limits_and_json(self):
        manager = FakeManager([(dict(memory(), id=i, value="文" * 10000), 1) for i in range(100)])
        store = FakeStore([dict(id=str(i), status="open", title="题" * 10000,
                               next_action="步" * 10000, due_at=float("nan")) for i in range(100)])
        builder = self.builder(manager, store)
        builder.MAX_CONTEXT_CHARS = 2500
        result = builder.build("字" * 100000, "s", [dict(role="user", content="史" * 10000)] * 100)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        self.assertLessEqual(len(encoded), 2500)
        self.assertTrue(result["budget"]["truncated"])
        self.assertIsInstance(json.loads(encoded), dict)
        self.assertEqual(result["boundaries"]["tool_permissions"], [])

    def test_failures_are_safe_and_not_exposed(self):
        class BrokenRetriever:
            def search(self, *args, **kwargs):
                raise RuntimeError("password=do-not-expose")
        manager = FakeManager()
        manager.retriever = BrokenRetriever()
        class BrokenStore:
            def preferences(self):
                raise RuntimeError("password=do-not-expose")
            def list_tasks(self):
                raise RuntimeError("password=do-not-expose")
        result = self.builder(manager, BrokenStore()).build("", "s")
        self.assertEqual(result["long_term_memory"]["status"], "unavailable")
        self.assertEqual(result["task_status"], "unavailable")
        self.assertNotIn("do-not-expose", json.dumps(result))

    def test_clock_validation_and_rollback(self):
        builder = self.builder()
        builder.build("只想倾诉", "s")
        self.clock.now -= 10
        self.assertEqual(builder.build("继续", "s")["support_mode"]["mode"], "balanced")
        aware = ContextBuilder(FakeManager(), FakeStore(), lambda: datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(aware.build("", "s")["time"]["unix"], 1735689600)
        for value in (float("nan"), float("inf"), True, datetime(2025, 1, 1)):
            with self.assertRaises(ValueError):
                ContextBuilder(FakeManager(), FakeStore(), lambda: value).build("", "s")

    def test_support_session_cache_bounded(self):
        builder = self.builder()
        for i in range(builder.MAX_SESSIONS + 1):
            builder.build("只想倾诉", str(i))
        self.assertEqual(len(builder._support), builder.MAX_SESSIONS)
        self.assertEqual(builder.build("继续", "0")["support_mode"]["mode"], "balanced")


if __name__ == "__main__":
    unittest.main(verbosity=2)
