"""Read-only markdown projection contracts; isolated in-memory SQLite only.

Run: python -B scripts/test_memory_snapshot.py
Covers `server/memory/snapshot.py` (the human-auditable projection) and the
`scripts/export_memory_snapshot.py` CLI that writes it. Never touches the
application's real memories.
"""
from __future__ import annotations

import atexit
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# The export CLI opens the DEFAULT store (MemoryStore() -> settings.db_path, which
# server.config freezes from DB_PATH at import time). Point it at a scratch file so
# the test and the CLI share one database and the real store is never touched.
_SCRATCH = tempfile.mkdtemp(prefix="xiaoxi-snapshot-")
atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
os.environ['DB_PATH'] = str(Path(_SCRATCH) / "memory.db")
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''

with patch('dotenv.load_dotenv', return_value=False):
    from server.memory.snapshot import render_memory_markdown
    from server.memory.store import MemoryStore


def epoch(y, mo, d):
    return datetime(y, mo, d, tzinfo=timezone.utc).timestamp()


def fake(**changes):
    base = dict(id=1, kind='fact', subject='user', key='工作:公司', value='用户在甲公司工作。',
                source='stated', status='active', supersedes=None, importance=0.8,
                confidence=0.95, session_id='s', created_at=epoch(2024, 1, 1),
                updated_at=epoch(2024, 6, 1), last_used_at=None, use_count=3,
                fact_valid_from=None, fact_valid_until=None, disabled=False)
    base.update(changes)
    return SimpleNamespace(**base)


class RenderTests(unittest.TestCase):
    def test_deterministic_and_diff_friendly_without_timestamp(self):
        rows = [fake(id=2, key='b:二', value='第二句。'), fake(id=1, key='a:一', value='第一句。')]
        first = render_memory_markdown(rows)
        second = render_memory_markdown(list(reversed(rows)))
        self.assertEqual(first, second)                 # input order must not matter
        self.assertNotIn('生成于', first)               # no timestamp -> stable body
        self.assertIn('第一句。', first)
        self.assertLess(first.index('第一句。'), first.index('第二句。'))  # sorted by key

    def test_generated_at_adds_a_header_stamp_only(self):
        body = render_memory_markdown([fake()])
        stamped = render_memory_markdown([fake()], generated_at=epoch(2026, 6, 15))
        self.assertNotIn('生成于', body)
        self.assertIn('生成于 2026-06-15', stamped)
        # The stamp is the only difference: the body stays diff-friendly.
        self.assertEqual(stamped.replace(
            [line for line in stamped.splitlines() if line.startswith('> 生成于')][0],
            [line for line in body.splitlines() if line.startswith('>')][0]), body)

    def test_fact_valid_time_bounds_are_surfaced(self):
        # The projection is the human-auditable surface; to_dict() excludes these
        # fields, so the snapshot is where a reviewer can still see them.
        document = render_memory_markdown([
            fake(fact_valid_from=epoch(2024, 3, 1), fact_valid_until=epoch(2025, 12, 31))])
        self.assertIn('有效时间 自 2024-03-01 至 2025-12-31', document)

    def test_unbounded_fact_shows_no_valid_time(self):
        self.assertNotIn('有效时间', render_memory_markdown([fake()]))

    def test_status_is_reported_not_hardcoded_active(self):
        # A superseded row must not be labelled "active"; the summary counts the
        # real statuses and the row carries its own.
        document = render_memory_markdown([
            fake(id=1, status='superseded'), fake(id=2, key='k:二', status='active')])
        self.assertIn('共 2 条记忆（active 1 · superseded 1）', document)
        self.assertIn('superseded', document)
        self.assertNotIn('共 2 条 active 记忆', document)

    def test_disabled_row_is_flagged_never_hidden(self):
        document = render_memory_markdown([fake(disabled=True)])
        self.assertIn('用户在甲公司工作。', document)
        self.assertIn('已停用', document)

    def test_empty_store_renders_a_placeholder(self):
        document = render_memory_markdown([])
        self.assertIn('（无记忆）', document)
        self.assertIn('小溪', document)

    def test_tier_note_surfaces_the_confidence_signal(self):
        for source, note in (('stated', 'stated · 陈述，保守不过期'),
                             ('inferred', 'inferred · 推断，72h 衰减'),
                             ('uncertain', 'uncertain · 不确定，72h 衰减')):
            self.assertIn(note, render_memory_markdown([fake(source=source)]))


class ExportCliTests(unittest.TestCase):
    """The documented command in docs/TESTING.md must actually work."""

    def setUp(self):
        # One database file per test: Windows refuses to unlink a SQLite file
        # while a connection is open, and a shared file would leak rows between
        # tests. `server.config` froze settings.db_path at import, so patch the
        # store's own resolution point instead of the environment.
        self._dir = tempfile.mkdtemp(prefix="xiaoxi-snapshot-case-")
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self.database = str(Path(self._dir) / "memory.db")
        self.store = MemoryStore(self.database)
        self.store.upsert(kind='fact', value='用户在甲公司工作', key='工作:公司')
        self.store.upsert(kind='fact', value='用户最近在学Python', key='学习:语言')

    def tearDown(self):
        self.store._conn.close()

    def load_cli(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "export_memory_snapshot_cli", ROOT / "scripts" / "export_memory_snapshot.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cli_writes_the_documented_file_and_is_regenerable(self):
        cli = self.load_cli()
        cli.MemoryStore = lambda *a, **k: MemoryStore(self.database)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "memory-snapshot.md"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = cli.main(["--out", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            first = out.read_text(encoding="utf-8")
            self.assertIn('用户在甲公司工作', first)
            self.assertIn('用户最近在学Python', first)
            # Regenerating from an unchanged store reproduces the same body.
            with redirect_stdout(io.StringIO()):
                cli.main(["--out", str(out)])
            self.assertEqual(out.read_text(encoding="utf-8"), first)

    def test_cli_prints_to_stdout_when_out_is_omitted(self):
        cli = self.load_cli()
        cli.MemoryStore = lambda *a, **k: MemoryStore(self.database)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main([])
        self.assertEqual(code, 0)
        self.assertIn('小溪', buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
