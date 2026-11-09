"""§六 playback_done 播放回执的离线契约：python scripts/test_playback_done.py

沿 test_ws_origin / test_voice_turns 的 AST 隔离装载，执行 server/app.py 里
真实的 Connection 方法，不 bootstrap 真 app、不联网、不落库。

批准口径（2026-11-09 Role 0，最小集）：
  - 客户端只发 turn_id + status（completed|stopped|failed），服务端不回包；
  - 只认当前轮或最近一次 done 的轮次，陈旧/未知 turn_id 静默忽略；
  - completed / stopped 才把该轮记为播放已确认，并把 last_spoke_at
    从「交付结束」推进到「播放结束」；
  - failed 只记录，不确认、不推进空闲基准，也不当用户拒绝或情绪信号；
  - 同一轮重复上报只取第一次终态。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


class PlaybackDoneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.env = load_ws()
        self.conn = self.env["Connection"](FakeSocket())

    def turn(self, turn_id: str = "t1", *, live: bool = True):
        turn = self.env["VoiceTurn"](turn_id)
        turn.live = live
        return turn

    async def delivered(self, turn_id: str = "t1"):
        """走 finish(done) 把轮次登记为「已交付」，与生产路径一致。"""
        turn = self.turn(turn_id)
        self.conn.turn = turn
        await self.conn.finish(turn, {"type": "done", "delivery": {"playback_confirmed": False}})
        return turn

    async def test_done_registers_delivered_turn_but_keeps_contract_field_false(self):
        turn = await self.delivered()
        self.assertEqual(self.conn.delivered_turn_id, turn.turn_id)
        # done.delivery.playback_confirmed 仍是 False：播放结果只能走 playback_done。
        self.assertIsNone(self.conn.playback_confirmed_turn_id)

    async def test_completed_confirms_and_advances_idle_baseline(self):
        turn = await self.delivered()
        self.conn.last_spoke_at = 100.0
        accepted = self.conn.note_playback(
            {"type": "playback_done", "turn_id": turn.turn_id, "status": "completed"})
        self.assertTrue(accepted)
        self.assertEqual(self.conn.playback_confirmed_turn_id, turn.turn_id)
        self.assertGreater(self.conn.last_spoke_at, 100.0)
        self.assertEqual(self.conn.playback_report,
                         {"turn_id": turn.turn_id, "status": "completed"})

    async def test_stopped_on_cancelled_current_turn_confirms(self):
        # barge_in 取消后 finish 不会再发 done，但该轮仍算「当前轮」。
        turn = self.turn("t1", live=False)
        self.conn.turn = turn
        self.conn.last_spoke_at = 100.0
        accepted = self.conn.note_playback(
            {"type": "playback_done", "turn_id": turn.turn_id, "status": "stopped"})
        self.assertTrue(accepted)
        self.assertEqual(self.conn.playback_confirmed_turn_id, turn.turn_id)
        self.assertGreater(self.conn.last_spoke_at, 100.0)

    async def test_live_current_turn_is_not_accepted_before_done(self):
        self.conn.turn = self.turn("t1", live=True)
        self.assertFalse(self.conn.note_playback(
            {"type": "playback_done", "turn_id": "t1", "status": "completed"}))
        self.assertIsNone(self.conn.playback_confirmed_turn_id)

    async def test_stale_or_unknown_turn_is_ignored(self):
        await self.delivered("t1")
        self.conn.turn = self.turn("t2", live=True)
        self.conn.last_spoke_at = 100.0
        self.assertFalse(self.conn.note_playback(
            {"type": "playback_done", "turn_id": "t0", "status": "completed"}))
        self.assertIsNone(self.conn.playback_confirmed_turn_id)
        self.assertEqual(self.conn.last_spoke_at, 100.0)

    async def test_failed_is_recorded_but_not_confirmed_and_not_a_rejection(self):
        await self.delivered("t1")
        self.conn.last_spoke_at = 100.0
        accepted = self.conn.note_playback(
            {"type": "playback_done", "turn_id": "t1", "status": "failed"})
        self.assertTrue(accepted)
        self.assertIsNone(self.conn.playback_confirmed_turn_id)
        self.assertEqual(self.conn.last_spoke_at, 100.0)
        self.assertEqual(self.conn.playback_report,
                         {"turn_id": "t1", "status": "failed"})

    async def test_duplicate_report_keeps_first_terminal_status(self):
        await self.delivered("t1")
        self.conn.last_spoke_at = 100.0
        self.assertTrue(self.conn.note_playback(
            {"type": "playback_done", "turn_id": "t1", "status": "completed"}))
        self.assertFalse(self.conn.note_playback(
            {"type": "playback_done", "turn_id": "t1", "status": "failed"}))
        self.assertEqual(self.conn.playback_report["status"], "completed")
        self.assertEqual(self.conn.playback_confirmed_turn_id, "t1")

    async def test_invalid_shape_is_ignored(self):
        await self.delivered("t1")
        for event in (
            {"type": "playback_done", "status": "completed"},
            {"type": "playback_done", "turn_id": "t1"},
            {"type": "playback_done", "turn_id": "t1", "status": "done"},
            {"type": "playback_done", "turn_id": 42, "status": "completed"},
            {"type": "playback_done", "turn_id": "", "status": "completed"},
        ):
            self.assertFalse(self.conn.note_playback(event), event)


if __name__ == "__main__":
    unittest.main()
