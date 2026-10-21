"""SQLite snapshots for the local service.

Default mode is a consistent snapshot taken while the service is stopped.  A
live process can keep writing between per-database snapshots, so that mode is
explicitly declared ``best_effort`` in the manifest instead of pretending the
set is a single point in time.

    python scripts/backup_local_databases.py
    python scripts/backup_local_databases.py --allow-live

The live port is checked, not assumed: an app listening on 8000 makes the
default command fail closed and print the exact override.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ("*.db", "durable/*.sqlite")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=2, check=True)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _db_paths(source: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in PATTERNS:
        found.extend(path for path in sorted(source.glob(pattern)) if path.is_file())
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_databases(source: Path, destination: Path, *, allow_live: bool = False,
                     running_check=None, host: str = "127.0.0.1", port: int = 8000) -> dict:
    """Create a snapshot directory or raise without leaving a partial backup."""
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_dir():
        raise RuntimeError(f"source directory does not exist: {source}")
    if destination.exists():
        raise RuntimeError(f"destination already exists: {destination}")
    running = running_check() if running_check is not None else _port_open(host, port)
    if running and not allow_live:
        raise RuntimeError(
            f"service is listening on {host}:{port}; stop it for a consistent snapshot "
            "or pass --allow-live to record a best-effort manifest")

    databases = _db_paths(source)
    if not databases:
        raise RuntimeError(f"no SQLite databases found under {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    started_at = _now()
    rows: list[dict] = []
    try:
        for path in databases:
            relative = path.relative_to(source).as_posix()
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as original:
                with closing(sqlite3.connect(target, timeout=10)) as snapshot:
                    original.backup(snapshot)
                    integrity = snapshot.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise RuntimeError(f"{relative}: {integrity}")
            rows.append({"database": relative, "snapshot": str(target),
                         "integrity": integrity, "sha256": _sha256(target),
                         "bytes": target.stat().st_size})
        manifest = {
            "format": 2,
            "mode": "best_effort" if running else "consistent",
            "source": str(source),
            "started_at": started_at,
            "finished_at": _now(),
            "commit": _commit(),
            "databases": rows,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        staging.rename(destination)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Snapshot local SQLite databases.")
    parser.add_argument("--source", default=str(ROOT / "data"))
    parser.add_argument("--destination", default="")
    parser.add_argument("--allow-live", action="store_true",
                        help="snapshot while the service is running; manifest is best_effort")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    source = Path(args.source).resolve()
    destination = (Path(args.destination).resolve() if args.destination else
                   source / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    try:
        manifest = backup_databases(source, destination, allow_live=args.allow_live,
                                    host=args.host, port=args.port)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
