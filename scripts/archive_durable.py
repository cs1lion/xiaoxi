"""Archive the durable SQLite, and optionally rotate it while the sidecar is down.

Default mode is an online snapshot into `data/durable/archive/`; the live database
is untouched. `--rotate --yes` additionally removes the live SQLite (and its WAL/
SHM) after a verified snapshot, so the next sidecar starts from a fresh file. The
rotation refuses while the sidecar holds the directory lock: it asks the Node
`hold-lock.ts` helper to take the same proper-lockfile lock with retries=0.

    python scripts/archive_durable.py
    python scripts/archive_durable.py --rotate --yes
    python scripts/archive_durable.py --prune-days 180

Rotation discards resumable runs in the old database. Python's ledger will treat
them as missing on the next reconciliation and ask the boss; it never invents a
result. That is why rotation requires --yes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "durable"
HOLD_LOCK = ROOT / "agent-runtime" / "hold-lock.ts"


def snapshot(path: Path, archive: Path, stamp: str) -> dict[str, str]:
    target = archive / f"{path.stem}-{stamp}.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    copy = sqlite3.connect(target, timeout=10)
    try:
        original.backup(copy)
        integrity = copy.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"{path.name}: {integrity}")
    finally:
        # Windows keeps the source locked until both handles close. `with
        # sqlite3.connect()` commits but does not close, so be explicit.
        copy.close()
        original.close()
    return {"source": str(path), "archive": str(target), "integrity": integrity}


def prune(archive: Path, days: int) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    removed: list[str] = []
    if not archive.exists():
        return removed
    for path in sorted(archive.glob("*.sqlite")):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed.append(str(path))
    return removed


def hold_lock(directory: Path) -> subprocess.Popen[str]:
    """Take the same proper-lockfile lock as the sidecar, with no retries."""
    if not HOLD_LOCK.exists():
        raise RuntimeError(f"missing lock helper: {HOLD_LOCK}")
    if shutil.which("node") is None:
        raise RuntimeError("node not found")
    process = subprocess.Popen(
        ["node", "--experimental-strip-types", str(HOLD_LOCK), str(directory)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    line: queue.Queue[str] = queue.Queue()

    def read_line() -> None:
        assert process.stdout is not None
        line.put(process.stdout.readline().strip())

    threading.Thread(target=read_line, daemon=True).start()
    try:
        first = line.get(timeout=5)
    except queue.Empty:
        process.kill()
        raise RuntimeError("lock helper did not answer")
    if first != "LOCKED":
        process.kill()
        raise RuntimeError(f"sidecar still holds the lock: {first[:200]}")
    return process


def release_lock(process: subprocess.Popen[str]) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)
    except Exception:
        process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--archive", default="")
    parser.add_argument("--rotate", action="store_true",
                        help="remove the live SQLite after a verified snapshot; requires --yes")
    parser.add_argument("--yes", action="store_true", help="confirm destructive rotation")
    parser.add_argument("--prune-days", type=int, default=0,
                        help="delete archives older than this many days; 0 keeps everything")
    args = parser.parse_args()

    source = Path(args.source)
    archive = Path(args.archive) if args.archive else source / "archive"
    if not source.exists():
        print(json.dumps({"archived": [], "reason": "no durable directory"}, indent=2))
        return 0
    databases = sorted(source.glob("*.sqlite"))
    if args.rotate and not args.yes:
        print(json.dumps({
            "rotate": False, "reason": "confirmation_required",
            "would_archive": [str(path) for path in databases],
            "would_remove": [str(path) for path in databases],
        }, indent=2))
        return 1
    lock = None
    if args.rotate:
        lock = hold_lock(source)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archived = []
    try:
        for path in databases:
            archived.append(snapshot(path, archive, stamp))
        removed = []
        if args.rotate:
            for path in databases:
                for suffix in ("", "-wal", "-shm"):
                    candidate = Path(str(path) + suffix)
                    if candidate.exists():
                        candidate.unlink()
                        removed.append(str(candidate))
        pruned = prune(archive, args.prune_days) if args.prune_days > 0 else []
        print(json.dumps({"archived": archived, "removed": removed,
                          "pruned": pruned, "rotated": args.rotate}, indent=2))
    finally:
        if lock is not None:
            release_lock(lock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
