"""Backup/restore contract: fail closed on live writes, and prove a round trip."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.backup_local_databases import backup_databases  # noqa: E402
from scripts.restore_local_databases import load_manifest, restore_databases  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def make_db(path: Path, table: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"CREATE TABLE {table} (value TEXT NOT NULL)")
        db.execute(f"INSERT INTO {table} VALUES (?)", (value,))
        db.commit()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data"
        make_db(source / "assistant.db", "tasks", "task-one")
        make_db(source / "threads.db", "threads", "thread-one")
        make_db(source / "durable" / "agent.sqlite", "runs", "run-one")
        snapshot = root / "backup"

        manifest = backup_databases(source, snapshot, running_check=lambda: False)
        check("stopped service produces a consistent manifest",
              manifest["mode"] == "consistent" and manifest["format"] == 2)
        check("all three database families are included",
              [row["database"] for row in manifest["databases"]]
              == ["assistant.db", "threads.db", "durable/agent.sqlite"],
              str([row["database"] for row in manifest["databases"]]))
        check("manifest and snapshots are in one directory",
              (snapshot / "manifest.json").is_file()
              and all((snapshot / row["database"]).is_file() for row in manifest["databases"]))
        check("each snapshot has an integrity result and hash",
              all(row["integrity"] == "ok" and len(row["sha256"]) == 64
                  for row in manifest["databases"]))

        restored = root / "restored"
        report = restore_databases(snapshot, restored)
        check("restore reports every database",
              report["ok"] and len(report["databases"]) == 3, str(report))
        with closing(sqlite3.connect(restored / "assistant.db")) as db:
            check("restored task row survives",
                  db.execute("SELECT value FROM tasks").fetchone()[0] == "task-one")
        check("restored tables are listed", report["databases"][0]["tables"] == ["tasks"],
              str(report["databases"][0]))

        try:
            restore_databases(snapshot, restored)
        except RuntimeError as exc:
            check("non-empty target refuses without --force", "not empty" in str(exc))
        else:
            check("non-empty target refuses without --force", False)

        refused = root / "refused"
        try:
            backup_databases(source, refused, running_check=lambda: True)
        except RuntimeError as exc:
            check("live service fails closed by default", "allow-live" in str(exc), str(exc))
        else:
            check("live service fails closed by default", False)
        check("refused live backup leaves no destination", not refused.exists())

        live = root / "live"
        live_manifest = backup_databases(source, live, allow_live=True, running_check=lambda: True)
        check("explicit live mode is labelled best_effort",
              live_manifest["mode"] == "best_effort")

        legacy = root / "legacy"
        legacy.mkdir()
        legacy_manifest = [{"database": "assistant.db", "snapshot": "assistant.db",
                            "integrity": "ok"}]
        (legacy / "manifest.json").write_text(json.dumps(legacy_manifest), encoding="utf-8")
        (legacy / "assistant.db").write_bytes((source / "assistant.db").read_bytes())
        meta, rows = load_manifest(legacy)
        check("legacy list manifest remains readable",
              meta["mode"] == "legacy_unknown" and rows[0]["database"] == "assistant.db")

        bad = root / "bad"
        bad.mkdir()
        (bad / "manifest.json").write_text(json.dumps(
            [{"database": "../escape.db", "snapshot": "../escape.db", "integrity": "ok"}]),
            encoding="utf-8")
        try:
            restore_databases(bad, root / "never")
        except RuntimeError as exc:
            check("path traversal is rejected", "unsafe" in str(exc), str(exc))
        else:
            check("path traversal is rejected", False)

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
