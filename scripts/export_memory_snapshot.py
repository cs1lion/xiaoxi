#!/usr/bin/env python
"""Render the read-only markdown memory snapshot from SQLite.

SQLite stays the only source of truth; this writes a derived, human-auditable
projection (grep-able, git-diff-able) and never touches the store. Delete the
output and regenerate it at any time.

Run: .venv/Scripts/python.exe -B scripts/export_memory_snapshot.py [--out PATH]
     [--subject user] [--include-history] [--limit N]

``--out`` omitted prints to stdout. ``DB_PATH`` selects the database; the
default is the real one, so pass DB_PATH=:memory: to experiment safely.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with patch('dotenv.load_dotenv', return_value=False):
    from server.memory.snapshot import render_memory_markdown
    from server.memory.store import MemoryStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="", help="输出路径；省略则打印到标准输出")
    parser.add_argument("--subject", default="", help="只导出一个 subject")
    parser.add_argument("--include-history", action="store_true",
                        help="包含 superseded / conflict 行（默认只导 active）")
    parser.add_argument("--limit", type=int, default=5000, help="最多导出多少条")
    args = parser.parse_args(argv)

    store = MemoryStore()
    try:
        memories = store.list_memories(
            subject=args.subject or None,
            include_history=args.include_history,
            limit=max(1, args.limit),
        )
        document = render_memory_markdown(memories, generated_at=None)
    finally:
        store._conn.close()

    if args.out:
        path = Path(args.out)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")
        print(f"已写出 {len(memories)} 条记忆到 {path}")
    else:
        print(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
