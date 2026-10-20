"""Read-only storage growth report for data/durable and its archives.

    python scripts/storage_doctor.py
    python scripts/storage_doctor.py --source data/durable --warn-mb 512 --fail-over

It does not open the live SQLite (the sidecar owns it). It reports file sizes,
archive/backup counts, and the newest archive timestamp. `--fail-over` makes a
warning an exit-1 signal for cron/ops.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def files(directory: Path, pattern: str) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob(pattern) if path.is_file())


def human(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return f"{value} B"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(ROOT / "data" / "durable"))
    parser.add_argument("--warn-mb", type=float, default=512.0)
    parser.add_argument("--fail-over", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    archive = source / "archive"
    backups = source.parent / "backups"
    live = [source / "agent.sqlite", source / "agent.sqlite-wal", source / "agent.sqlite-shm"]
    live_bytes = sum(size(path) for path in live)
    archives = files(archive, "*.sqlite")
    backup_dbs = sorted(
        path for path in backups.rglob("*")
        if path.is_file() and path.suffix in (".db", ".sqlite")
    ) if backups.exists() else []
    newest = None
    if archives:
        newest = datetime.fromtimestamp(archives[-1].stat().st_mtime, timezone.utc).isoformat()
    warning = live_bytes > max(0.0, args.warn_mb) * 1024 * 1024
    report = {
        "source": str(source),
        "live_bytes": live_bytes,
        "live_human": human(live_bytes),
        "live_files": [{"path": str(path), "bytes": size(path)} for path in live if path.exists()],
        "archive_count": len(archives),
        "archive_bytes": sum(size(path) for path in archives),
        "newest_archive": newest,
        "backup_count": len(backup_dbs),
        "backup_bytes": sum(size(path) for path in backup_dbs),
        "warn_mb": args.warn_mb,
        "warning": warning,
        "note": "live SQLite is owned by the sidecar; this report never opens it",
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"source: {source}")
        print(f"live:   {report['live_human']} across {len(report['live_files'])} file(s)")
        print(f"archive:{report['archive_count']} file(s), {human(report['archive_bytes'])}, newest {newest or '-'}")
        print(f"backup: {report['backup_count']} file(s), {human(report['backup_bytes'])}")
        if warning:
            print(f"WARN: live durable storage exceeds {args.warn_mb} MiB; plan an offline rotation")
    return 1 if warning and args.fail_over else 0


if __name__ == "__main__":
    sys.exit(main())
