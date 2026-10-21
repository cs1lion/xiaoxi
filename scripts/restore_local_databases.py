"""Rehearse a restore from a backup manifest without touching the live data dir.

    python scripts/restore_local_databases.py data/backups/<stamp> --target tmp/restored

The target must be empty unless --force is given.  Every snapshot is copied
through sqlite3's backup API and integrity-checked again; the command prints a
small JSON report so a restore drill has real evidence instead of "backup
files exist".
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def load_manifest(snapshot_dir: Path) -> tuple[dict, list[dict]]:
    path = snapshot_dir / "manifest.json"
    if not path.is_file():
        raise RuntimeError(f"manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        # Legacy backups wrote a bare list; keep the restore path usable.
        return {"format": 1, "mode": "legacy_unknown"}, data
    if not isinstance(data, dict) or not isinstance(data.get("databases"), list):
        raise RuntimeError("manifest has no databases list")
    return data, data["databases"]


def _safe_relative(value: str) -> Path:
    relative = Path(str(value).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RuntimeError(f"unsafe database path in manifest: {value!r}")
    return relative


def restore_databases(snapshot_dir: Path, target: Path, *, force: bool = False) -> dict:
    snapshot_dir = Path(snapshot_dir).resolve()
    target = Path(target).resolve()
    manifest, databases = load_manifest(snapshot_dir)
    if target.exists() and any(target.iterdir()) and not force:
        raise RuntimeError(f"target is not empty: {target} (pass --force to overwrite)")
    target.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for item in databases:
        if not isinstance(item, dict):
            raise RuntimeError("manifest entry is not an object")
        relative = _safe_relative(item.get("database") or item.get("snapshot") or "")
        source = snapshot_dir / relative
        if not source.is_file():
            raise RuntimeError(f"snapshot missing: {source}")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)) as original:
            with closing(sqlite3.connect(destination, timeout=10)) as restored:
                original.backup(restored)
                integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"{relative}: {integrity}")
                tables = [row[0] for row in restored.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        rows.append({"database": relative.as_posix(), "integrity": integrity,
                     "tables": tables, "target": str(destination)})
    return {"ok": True, "mode": manifest.get("mode", "unknown"),
            "snapshot": str(snapshot_dir), "target": str(target),
            "commit": manifest.get("commit", "unknown"), "databases": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse restoring a local backup.")
    parser.add_argument("snapshot_dir")
    parser.add_argument("--target", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = restore_databases(Path(args.snapshot_dir), Path(args.target), force=args.force)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
