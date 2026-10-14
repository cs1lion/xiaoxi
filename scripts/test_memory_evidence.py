"""Offline acceptance of evidence-backed memory writes and lifecycle races.

Run: .venv/Scripts/python.exe -B scripts/test_memory_evidence.py
All databases are in memory or an explicitly created temporary directory. The
application's import-time singleton is isolated before import; dotenv loading
is disabled, and extraction is replaced with controlled candidates. These tests
verify program contracts, not a model's ability to understand arbitrary speech.
"""
from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "mock"
with patch("dotenv.load_dotenv", return_value=False):
    from server.memory.manager import MemoryManager
    from server.memory.store import MemoryStore


def candidate(key="工作:公司", value="用户在甲公司工作", *,
              quote="我在甲公司工作", source="stated", operation="add", **fields):
    item = {
        "kind": "fact", "subject": "user", "key": key, "value": value,
        "source": source, "importance": .8, "confidence": .95,
        "evidence_quote": quote, "operation": operation,
    }
    item.update(fields)
    return item


class ControlledExtractor:
    """Await explicit events so completion order never depends on a sleep."""

    def __init__(self, replies):
        self.replies = replies
        self.started = {text: asyncio.Event() for text in replies}
        self.release = {text: asyncio.Event() for text in replies}

    async def __call__(self, user_text, assistant_text, **kwargs):
        self.started[user_text].set()
        await self.release[user_text].wait()
        return copy.deepcopy(self.replies[user_text])

    async def wait_started(self, text):
        await asyncio.wait_for(self.started[text].wait(), 3)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore(":memory:")
        self.manager = MemoryManager(self.store)
        self.manager.cfg = copy.deepcopy(self.manager.cfg)
        self.manager.retriever.cfg = dict(self.manager.retriever.cfg)
        self.manager.retriever.cfg.update(min_score=0, min_similarity=.01,
                                          max_inject_chars=4000)
        self.session = self.store.start_session("synthetic-lifecycle")
        self.provider_guard = patch("server.llm.llm.complete_raw",
                                    side_effect=AssertionError("Network/model calls are forbidden"))
        self.provider_guard.start()
        self.addCleanup(self.provider_guard.stop)

    async def asyncTearDown(self):
        self.store._conn.close()

    def message(self, text, *, role="user", session=None):
        return self.store.add_message(session or self.session, role, text)

    def active(self, key=None):
        rows = self.store.list_memories(status="active", limit=5000)
        return rows if key is None else [row for row in rows if row.key == key]

    def all_rows(self):
        return self.store.list_memories(status=None, include_history=True, limit=5000)

    async def observe(self, text, items, *, message_id=None, session=None, **kwargs):
        sid = session or self.session
        if message_id is None:
            message_id = self.message(text, session=sid)
        linked_items = copy.deepcopy(items)
        for item in linked_items:
            item.setdefault("evidence_message_id", message_id)
            item.setdefault("evidence_state", "linked")
        with patch("server.memory.manager.extract_memories", AsyncMock(return_value=linked_items)):
            return await self.manager.observe_turn(
                session_id=sid, user_text=text, assistant_text="合成测试回复",
                user_message_id=message_id, **kwargs)

    async def test_message_id_and_linked_evidence_are_real(self):
        text = "我在甲公司工作，岗位是设计师。"
        message_id = self.message(text)
        self.assertIs(type(message_id), int)
        message = self.store.get_message(message_id)
        self.assertEqual(message["session_id"], self.session)
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["text"], text)
        saved = await self.observe(text, [candidate()], message_id=message_id)
        self.assertEqual(len(saved), 1)
        memory = self.active()[0]
        self.assertEqual(memory.evidence_state, "linked")
        self.assertEqual(memory.evidence_message_id, message_id)
        self.assertEqual(memory.evidence_quote, "我在甲公司工作")
        self.assertEqual(memory.decision, "add")
        self.assertEqual(memory.to_dict()["evidence_message_id"], message_id)

    async def test_automatic_write_without_message_id_is_rejected(self):
        fake = AsyncMock(return_value=[candidate()])
        with patch("server.memory.manager.extract_memories", fake):
            saved = await self.manager.observe_turn(
                session_id=self.session, user_text="我在甲公司工作",
                assistant_text="合成测试回复", user_message_id=None)
        self.assertEqual(saved, [])
        self.assertEqual(self.all_rows(), [])

    async def test_wrong_role_session_text_or_missing_message_rejected(self):
        other = self.store.start_session("synthetic-other")
        invalid = [
            (self.message("我在甲公司工作", role="assistant"), "我在甲公司工作"),
            (self.message("我在甲公司工作", role="system"), "我在甲公司工作"),
            (self.message("我在甲公司工作", session=other), "我在甲公司工作"),
            (self.message("我在乙公司工作"), "我在甲公司工作"),
            (9_999_999, "我在甲公司工作"),
            (True, "我在甲公司工作"),
        ]
        for message_id, text in invalid:
            with self.subTest(message_id=message_id, text=text):
                await self.observe(text, [candidate()], message_id=message_id)
                self.assertEqual(self.all_rows(), [])

    async def test_quote_must_come_from_current_user_message(self):
        text = "我在甲公司工作。"
        message_id = self.message(text)
        for quote in ("", "   ", "你在乙公司工作", "我在丙公司工作", None, ["我在甲公司工作"]):
            with self.subTest(quote=quote):
                await self.observe(text, [candidate(quote=quote)], message_id=message_id,
                                   recent_context="用户：我在丙公司工作")
                self.assertEqual(self.all_rows(), [])

    async def test_boolean_id_cannot_alias_real_first_user_message(self):
        self.assertEqual(self.message("我在甲公司工作"), 1)
        saved = await self.observe("我在甲公司工作", [candidate()], message_id=True)
        self.assertEqual(saved, [])
        self.assertEqual(self.all_rows(), [])

    async def test_candidate_cannot_choose_another_evidence_message(self):
        wrong_id = self.message("我在甲公司工作", role="assistant")
        real_id = self.message("我在甲公司工作")
        await self.observe("我在甲公司工作", [candidate(evidence_message_id=wrong_id,
                           evidence_state="linked")], message_id=real_id)
        for memory in self.all_rows():
            self.assertEqual(memory.evidence_message_id, real_id)
            self.assertEqual(self.store.get_message(memory.evidence_message_id)["role"], "user")

    async def test_manager_revalidates_malformed_candidate_fields(self):
        text = "我在甲公司工作"
        message_id = self.message(text)
        malformed = [
            {"kind": "permission"}, {"kind": "self_state"}, {"subject": "admin"}, {"source": "verified"},
            {"operation": "delete"}, {"key": ""}, {"value": ""},
            {"importance": float("nan")}, {"confidence": float("inf")},
            {"importance": True}, {"confidence": False},
            {"importance": "not-a-number"}, {"key": ["工作:公司"]},
        ]
        for fields in malformed:
            with self.subTest(fields=fields):
                saved = await self.observe(text, [candidate(**fields)], message_id=message_id)
                self.assertEqual(saved, [])
                self.assertEqual(self.all_rows(), [])

    async def test_independent_same_topic_keys_survive_same_batch(self):
        text = "我在甲公司工作，岗位是设计师。"
        await self.observe(text, [candidate(), candidate("工作:岗位", "用户的岗位是设计师",
                                                       quote="岗位是设计师")])
        self.assertEqual({row.key for row in self.active()}, {"工作:公司", "工作:岗位"})

    async def test_independent_same_topic_keys_survive_separate_turns(self):
        await self.observe("我在甲公司工作", [candidate()])
        await self.observe("我的岗位是设计师", [candidate("工作:岗位", "用户的岗位是设计师",
                                                    quote="我的岗位是设计师")])
        self.assertEqual({row.key for row in self.active()}, {"工作:公司", "工作:岗位"})

    async def test_lower_source_conflict_cannot_replace_stated(self):
        await self.observe("我在甲公司工作", [candidate()])
        original = self.active()[0]
        for source in ("inferred", "uncertain"):
            await self.observe("我在乙公司工作", [candidate(value="用户在乙公司工作",
                               quote="我在乙公司工作", source=source, operation="update")])
        active = self.active("工作:公司")
        self.assertEqual([(row.id, row.source, row.value) for row in active],
                         [(original.id, "stated", "用户在甲公司工作")])
        conflicts = [row for row in self.all_rows() if row.status == "conflict"]
        self.assertEqual(len(conflicts), 2)
        self.assertEqual({row.source for row in conflicts}, {"inferred", "uncertain"})
        self.assertTrue(all(row.evidence_state == "linked" for row in conflicts))
        self.assertTrue(all(row.decision == "conflict" for row in conflicts))
        self.assertNotIn("用户在乙公司工作", self.manager.context_for("工作 公司"))
        self.assertEqual(len(self.manager.snapshot()), 1)

    async def test_duplicate_cannot_downgrade_source_or_replace_original_evidence(self):
        await self.observe("我在甲公司工作", [candidate()])
        original = self.active()[0]
        await self.observe("我在甲公司工作", [candidate(source="inferred")])
        active = self.active()[0]
        self.assertEqual(active.id, original.id)
        self.assertEqual(active.source, "stated")
        self.assertEqual(active.evidence_message_id, original.evidence_message_id)
        self.assertEqual(len(self.all_rows()), 1)

    async def test_same_value_promotion_links_the_new_session_explicit_evidence(self):
        text = "我在甲公司工作"
        await self.observe(text, [candidate(source="inferred")])
        original = self.active()[0]
        second = self.store.start_session("synthetic-promotion")
        message_id = self.message(text, session=second)
        await self.observe(text, [candidate(source="stated")], message_id=message_id, session=second)
        current = self.active()[0]
        self.assertEqual(current.id, original.id)
        self.assertEqual(current.source, "stated")
        self.assertEqual(current.decision, "noop")
        self.assertEqual(current.evidence_message_id, message_id)
        self.assertEqual(current.evidence_quote, text)
        self.assertEqual(current.evidence_state, "linked")
        self.assertEqual(current.session_id, second)
        self.assertEqual(current.confidence, .95)

    async def test_inference_budget_accumulates_across_turns_but_is_session_scoped(self):
        self.manager.cfg["write"]["max_infer_per_session"] = 2
        for number in range(3):
            text = f"我负责第{number}个项目"
            await self.observe(text, [candidate(f"工作:项目{number}", f"用户负责项目{number}",
                                               quote=text, source="inferred")])
        self.assertEqual(self.store.count_inferences(self.session), 2)
        self.assertEqual(len(self.active()), 2)
        await self.observe("我在甲公司工作", [candidate()])
        self.assertEqual(len(self.active()), 3, "Stated evidence is independent of inference quota")
        second = self.store.start_session("synthetic-inference-next")
        text = "我负责一个新项目"
        await self.observe(text, [candidate("工作:新项目", "用户负责一个新项目", quote=text,
                                           source="inferred")], session=second)
        self.assertEqual(self.store.count_inferences(second), 1)
        self.assertEqual(len(self.active()), 4)

    async def test_stated_change_without_explicit_correction_is_conflict(self):
        await self.observe("我在甲公司工作", [candidate()])
        await self.observe("我在乙公司工作", [candidate(value="用户在乙公司工作",
                                                      quote="我在乙公司工作")])
        self.assertEqual(self.active()[0].value, "用户在甲公司工作")
        self.assertEqual([row.status for row in self.all_rows()].count("conflict"), 1)

    async def test_update_label_without_change_in_quote_stays_conflict(self):
        await self.observe("我在甲公司工作", [candidate()])
        await self.observe("我在乙公司工作", [candidate(value="用户在乙公司工作",
                           quote="我在乙公司工作", operation="update")])
        self.assertEqual(self.active()[0].value, "用户在甲公司工作")
        self.assertEqual([row.status for row in self.all_rows()].count("conflict"), 1)

    async def test_explicit_stated_correction_updates_and_preserves_evidence_chain(self):
        await self.observe("我在甲公司工作", [candidate()])
        original = self.active()[0]
        text = "纠正一下，我已经不在甲公司工作了，现在改为在乙公司工作。"
        message_id = self.message(text)
        await self.observe(text, [candidate(value="用户现在在乙公司工作", quote=text,
                                           operation="update")], message_id=message_id)
        current = self.active()[0]
        self.assertEqual(current.value, "用户现在在乙公司工作")
        self.assertEqual(current.evidence_message_id, message_id)
        self.assertEqual(current.evidence_quote, text)
        self.assertEqual(current.decision, "update")
        self.assertEqual(current.supersedes, original.id)
        self.assertEqual(self.store.get(original.id).status, "superseded")
        self.assertEqual(self.store.get(original.id).evidence_message_id,
                         original.evidence_message_id)

    async def test_manual_write_is_explicit_and_not_fabricated_linked_evidence(self):
        memory = self.manager.remember(kind="fact", subject="user", key="工作:公司",
                                       value="用户在甲公司工作", source="stated", importance=.8)
        self.assertIsNotNone(memory)
        self.assertEqual(memory.evidence_state, "manual")
        self.assertIsNone(memory.evidence_message_id)
        self.assertFalse(memory.evidence_quote)
        corrected = self.manager.correct(memory.id, "用户现在在乙公司工作")
        self.assertEqual(corrected.evidence_state, "manual")
        self.assertIsNone(corrected.evidence_message_id)
        self.assertEqual(corrected.value, "用户现在在乙公司工作")

    async def test_manual_write_cannot_claim_linked_evidence_without_a_message(self):
        memory = self.manager.remember(kind="fact", subject="user", key="工作:公司",
            value="用户在甲公司工作", source="stated", importance=.8,
            evidence_state="linked", evidence_quote="我在甲公司工作")
        if memory is not None:
            self.assertEqual(memory.evidence_state, "manual")
            self.assertIsNone(memory.evidence_message_id)
            self.assertFalse(memory.evidence_quote)

    async def gated_write(self, extractor, text):
        message_id = self.message(text)
        for item in extractor.replies[text]:
            item.setdefault("evidence_message_id", message_id)
            item.setdefault("evidence_state", "linked")
        task = asyncio.create_task(self.manager.observe_turn(
            session_id=self.session, user_text=text, assistant_text="合成测试回复",
            user_message_id=message_id))
        self.addAsyncCleanup(self.finish_task, task)
        await extractor.wait_started(text)
        return task

    @staticmethod
    async def finish_task(task):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def invalidation_case(self, action, *, expect_value=None):
        text = "我在甲公司工作"
        extractor = ControlledExtractor({text: [candidate()]})
        with patch("server.memory.manager.extract_memories", new=extractor):
            task = await self.gated_write(extractor, text)
            action()
            extractor.release[text].set()
            self.assertEqual(await asyncio.wait_for(task, 3), [])
        values = [row.value for row in self.active()]
        self.assertEqual(values, [] if expect_value is None else [expect_value])

    async def test_disable_invalidates_pending_extraction(self):
        await self.invalidation_case(lambda: setattr(self.manager, "enabled", False))

    async def test_disable_then_enable_cannot_revive_pending_extraction(self):
        def toggle():
            self.manager.enabled = False
            self.manager.enabled = True
        await self.invalidation_case(toggle)
        self.assertTrue(self.manager.enabled)
        await self.observe("我在甲公司工作", [candidate()])
        self.assertEqual(len(self.active()), 1)

    async def test_direct_delete_all_invalidates_pending_extraction_even_on_empty_db(self):
        await self.invalidation_case(self.store.delete_all)

    async def test_epoch_captured_at_schedule_rejects_write_after_delete_before_task_starts(self):
        text = "我在甲公司工作"
        message_id = self.message(text)
        scheduled_epoch = self.manager.capture_write_epoch()
        self.store.delete_all()
        item = candidate(evidence_message_id=message_id, evidence_state="linked")
        with patch("server.memory.manager.extract_memories", AsyncMock(return_value=[item])):
            saved = await self.manager.observe_turn(
                session_id=self.session, user_text=text, assistant_text="合成测试回复",
                user_message_id=message_id, write_epoch=scheduled_epoch)
        self.assertEqual(saved, [])
        self.assertEqual(self.all_rows(), [])

    async def test_deleted_memory_cannot_reappear_from_the_same_old_message(self):
        text = "我在甲公司工作"
        message_id = self.message(text)
        await self.observe(text, [candidate()], message_id=message_id)
        self.manager.forget_all()
        self.assertIsNotNone(self.store.get_message(message_id))
        await self.observe(text, [candidate()], message_id=message_id)
        self.assertEqual(self.all_rows(), [])

    async def test_disable_enable_blocks_old_message_but_allows_new_user_message(self):
        text = "我在甲公司工作"
        old_id = self.message(text)
        self.manager.enabled = False
        self.manager.enabled = True
        await self.observe(text, [candidate()], message_id=old_id)
        self.assertEqual(self.all_rows(), [])
        new_id = self.message(text)
        await self.observe(text, [candidate()], message_id=new_id)
        self.assertEqual(self.active()[0].evidence_message_id, new_id)

    async def test_direct_delete_key_invalidates_pending_extraction_even_when_absent(self):
        await self.invalidation_case(lambda: self.store.delete_key("工作:公司"))

    async def test_direct_delete_invalidates_pending_extraction(self):
        memory = self.manager.remember(kind="fact", key="工作:公司", value="用户在旧公司工作")
        await self.invalidation_case(lambda: self.store.delete(memory.id))

    async def test_manager_forget_key_invalidates_pending_extraction(self):
        self.manager.remember(kind="fact", key="工作:公司", value="用户在旧公司工作")
        await self.invalidation_case(lambda: self.manager.forget_key("工作:公司"))

    async def test_manual_correction_invalidates_pending_extraction(self):
        memory = self.manager.remember(kind="fact", key="工作:公司", value="用户在旧公司工作")
        await self.invalidation_case(lambda: self.manager.correct(memory.id, "用户在丙公司工作"),
                                     expect_value="用户在丙公司工作")

    async def test_older_message_finishing_last_cannot_override_newer_fact(self):
        old = "我在甲公司工作"
        new = "纠正一下，我已经改为在乙公司工作。"
        extractor = ControlledExtractor({
            old: [candidate()],
            new: [candidate(value="用户在乙公司工作", quote=new, operation="update")],
        })
        with patch("server.memory.manager.extract_memories", new=extractor):
            old_task = await self.gated_write(extractor, old)
            new_task = await self.gated_write(extractor, new)
            extractor.release[new].set()
            await asyncio.wait_for(new_task, 3)
            self.assertEqual(self.active()[0].value, "用户在乙公司工作")
            extractor.release[old].set()
            await asyncio.wait_for(old_task, 3)
        self.assertEqual(len(self.active()), 1)
        self.assertEqual(self.active()[0].value, "用户在乙公司工作")

    async def test_forget_key_deletes_more_than_a_thousand_history_rows(self):
        for version in range(1025):
            self.store.upsert(kind="fact", key="工作:公司", value=f"合成工作版本 {version}")
        unrelated = self.store.upsert(kind="fact", key="工作:岗位", value="用户的岗位是设计师")
        self.assertEqual(len([row for row in self.all_rows() if row.key == "工作:公司"]), 1025)
        removed = self.manager.forget_key("工作:公司")
        self.assertEqual(removed, 1025)
        self.assertEqual([row.id for row in self.all_rows()], [unrelated])
        self.assertEqual(self.store.history_for_key("工作:公司", limit=5000), [])

    async def test_forget_one_identity_preserves_same_key_other_kind_and_subject(self):
        user = self.manager.remember(kind="fact", subject="user", key="工作:公司",
                                     value="用户在甲公司工作")
        self_memory = self.manager.remember(kind="fact", subject="self", key="工作:公司",
                                            value="角色是甲公司的虚构角色")
        episode = self.manager.remember(kind="episode", subject="user", key="工作:公司",
                                        value="用户曾聊过甲公司")
        corrected = self.manager.correct(user.id, "用户现在在乙公司工作")
        removed = self.manager.forget(corrected.id)
        self.assertEqual(removed, 2)
        self.assertIsNone(self.store.get(user.id))
        self.assertIsNone(self.store.get(corrected.id))
        self.assertEqual({row.id for row in self.all_rows()}, {self_memory.id, episode.id})

    async def test_evidence_survives_temporary_database_reopen(self):
        with tempfile.TemporaryDirectory(prefix="memory-lifecycle-") as directory:
            path = Path(directory) / "isolated.db"
            first = MemoryStore(str(path))
            try:
                manager = MemoryManager(first)
                session = first.start_session("synthetic-reopen")
                text = "我在甲公司工作"
                message_id = first.add_message(session, "user", text)
                item = candidate(evidence_message_id=message_id, evidence_state="linked")
                with patch("server.memory.manager.extract_memories", AsyncMock(return_value=[item])):
                    saved = await manager.observe_turn(session_id=session, user_text=text,
                        assistant_text="合成测试回复", user_message_id=message_id)
                self.assertEqual(len(saved), 1)
                memory_id = saved[0].id
            finally:
                first._conn.close()
            reopened = MemoryStore(str(path))
            try:
                memory = reopened.get(memory_id)
                self.assertEqual(memory.evidence_state, "linked")
                self.assertEqual(memory.evidence_message_id, message_id)
                self.assertEqual(memory.evidence_quote, text)
                message = reopened.get_message(message_id)
                self.assertEqual((message["session_id"], message["role"], message["text"]),
                                 (session, "user", text))
            finally:
                reopened._conn.close()

    async def test_deleted_message_replay_remains_blocked_after_database_reopen(self):
        with tempfile.TemporaryDirectory(prefix="memory-floor-") as directory:
            path = Path(directory) / "isolated.db"
            first = MemoryStore(str(path))
            text = "我在甲公司工作"
            try:
                manager = MemoryManager(first)
                session = first.start_session("synthetic-floor")
                message_id = first.add_message(session, "user", text)
                item = candidate(evidence_message_id=message_id, evidence_state="linked")
                with patch("server.memory.manager.extract_memories", AsyncMock(return_value=[item])):
                    await manager.observe_turn(session_id=session, user_text=text,
                        assistant_text="合成测试回复", user_message_id=message_id)
                manager.forget_all()
            finally:
                first._conn.close()
            reopened = MemoryStore(str(path))
            try:
                manager = MemoryManager(reopened)
                self.assertIsNotNone(reopened.get_message(message_id))
                with patch("server.memory.manager.extract_memories", AsyncMock(return_value=[item])):
                    result = await manager.observe_turn(session_id=session, user_text=text,
                        assistant_text="合成测试回复", user_message_id=message_id)
                self.assertEqual(result, [])
                self.assertEqual(reopened.list_memories(status=None, include_history=True), [])
            finally:
                reopened._conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
