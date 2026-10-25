"""mine_eval_candidates 的离线自检：自建同 schema 临时 sqlite，不起服务、
不碰生产库、不进 run_gates。验命中 / 不命中 / 去重 / 脱敏 / 空库 exit 0 /
只读不改源。"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mine_eval_candidates as target


# 与 server/memory/store.py:80-88 一致的 messages 表（自建同 schema 临时库）。
MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'text',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);
"""

WIDE_SINCE = 0.0
WIDE_UNTIL = 1e12
EPOCH = 1_700_000_000.0


class MineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.db = self.base / 'memory.db'
        connection = sqlite3.connect(self.db)
        try:
            connection.executescript(MESSAGES_SCHEMA)
            connection.commit()
        finally:
            connection.close()

    def add(self, session_id, role, text, created_at=EPOCH):
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "INSERT INTO messages (session_id, role, text, mode, created_at) "
                "VALUES (?, ?, ?, 'text', ?)", (session_id, role, text, created_at))
            connection.commit()
        finally:
            connection.close()

    def mine(self):
        return target.mine(self.db, since=WIDE_SINCE, until=WIDE_UNTIL)

    def heuristics_of(self, report):
        return {candidate['heuristic'] for candidate in report['candidates']}

    def test_each_heuristic_is_nominated_with_suggestions(self):
        self.add('s1', 'user', '那个呢？')
        self.add('s1', 'assistant', '好的，我在听。')
        self.add('s2', 'user', '你记错了，不是这样')
        self.add('s2', 'assistant', '抱歉，我更正一下。')
        self.add('s3', 'user', '我最近很难过')
        self.add('s3', 'assistant', '想聊聊发生了什么吗。')
        self.add('s4', 'user', '今天天气不错')
        self.add('s4', 'assistant', '你之前说过你喜欢咖啡。')
        report = self.mine()
        self.assertEqual(self.heuristics_of(report), {
            'coreference_or_ellipsis', 'fact_challenge',
            'negative_emotion_boundary', 'suspected_hallucination_surface'})
        by_key = {candidate['heuristic']: candidate for candidate in report['candidates']}
        expected = {
            'coreference_or_ellipsis': ('offline', 'retrieval_disambiguation'),
            'suspected_hallucination_surface': ('live', 'hallucination_guard'),
            'fact_challenge': ('offline', 'correction_semantics'),
            'negative_emotion_boundary': ('live', 'emotion_boundary'),
        }
        for key, (layer, origin) in expected.items():
            candidate = by_key[key]
            self.assertEqual(candidate['suggested_layer'], layer)
            self.assertEqual(candidate['suggested_origin'], origin)
            self.assertEqual(set(candidate), {
                'heuristic', 'session_id', 'created_at', 'user_text',
                'assistant_text', 'suggested_layer', 'suggested_origin'})
            self.assertTrue(candidate['session_id'])
            self.assertIsInstance(candidate['created_at'], str)

    def test_benign_turns_are_not_nominated(self):
        self.add('b1', 'user', '我今天去公园散步了')
        self.add('b1', 'assistant', '好的，知道了，注意安全。')
        report = self.mine()
        self.assertEqual(report['candidates'], [])
        self.assertEqual(set(report['counts'].values()), {0})

    def test_identical_turns_are_deduped(self):
        for session in ('d1', 'd2', 'd3'):
            self.add(session, 'user', '你记错了，不是这样')
            self.add(session, 'assistant', '抱歉。')
        report = self.mine()
        self.assertEqual(report['counts']['fact_challenge'], 1)
        self.assertEqual(len(report['candidates']), 1)

    def test_credentials_and_pii_are_redacted(self):
        secret = ('你记错了，我的 api_key: sk-abcdef1234567890 '
                  '邮箱 foo.bar@example.com 手机 13800138000')
        self.add('r1', 'user', secret)
        self.add('r1', 'assistant', '好的。')
        report = self.mine()
        self.assertEqual(len(report['candidates']), 1)
        text = report['candidates'][0]['user_text']
        self.assertIn('[REDACTED:credential]', text)
        self.assertIn('[REDACTED:pii]', text)
        for needle in ('sk-abcdef1234567890', 'foo.bar@example.com', '13800138000'):
            self.assertNotIn(needle, text)

    def test_empty_database_writes_zero_candidates_and_exits_zero(self):
        output = self.base / 'out.json'
        with redirect_stdout(io.StringIO()):
            code = target.main(['--db', str(self.db), '--output', str(output), '--days', '365000'])
        self.assertEqual(code, 0)
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['candidates'], [])
        self.assertEqual(set(report['counts'].values()), {0})

    def test_missing_database_prints_and_exits_zero(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = target.main(['--db', str(self.base / 'absent.db')])
        self.assertEqual(code, 0)
        self.assertIn('database_missing', buffer.getvalue())

    def test_read_only_mining_never_mutates_source(self):
        self.add('x1', 'user', '你记错了，不是这样')
        self.add('x1', 'assistant', '抱歉。')
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.mine()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertFalse((self.base / 'memory.db-wal').exists())
        self.assertFalse((self.base / 'memory.db-journal').exists())


if __name__ == '__main__':
    unittest.main()
