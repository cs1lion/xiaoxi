"""Archive/rotation safety: snapshot + integrity, refuse rotation while locked,
remove the live SQLite only after an explicit --yes and a verified snapshot.
"""
from __future__ import annotations

import json
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
ARCHIVE = ROOT / "scripts" / "archive_durable.py"
HOLD = ROOT / "agent-runtime" / "hold-lock.ts"

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def run_archive(source: Path, archive: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(ARCHIVE), "--source", str(source), "--archive", str(archive), *extra],
        cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=30,
    )


def hold(directory: Path) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        ["node", "--experimental-strip-types", str(HOLD), str(directory)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    line: queue.Queue[str] = queue.Queue()

    def read() -> None:
        assert process.stdout is not None
        line.put(process.stdout.readline().strip())

    threading.Thread(target=read, daemon=True).start()
    first = line.get(timeout=5)
    if first != "LOCKED":
        process.kill()
        raise RuntimeError(first[:200])
    return process


def main() -> int:
    if shutil.which("node") is None:
        print("SKIP storage archive: node not found")
        return 0
    root = Path(tempfile.mkdtemp(prefix="durable-storage-"))
    source, archive = root / "durable", root / "archive"
    source.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(source / "agent.sqlite")
    try:
        db.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, text TEXT)")
        db.execute("INSERT INTO entries(text) VALUES ('hello')")
        db.commit()
    finally:
        db.close()  # Windows: a live handle blocks rotation's unlink
    try:
        print("[storage archive]")
        snap = run_archive(source, archive)
        snap_body = json.loads(snap.stdout)
        check("在线快照成功", snap.returncode == 0 and snap_body["archived"],
              snap.stderr[:300])
        check("快照 integrity ok", all(item["integrity"] == "ok"
                                       for item in snap_body.get("archived", [])))
        check("快照不动活库", (source / "agent.sqlite").exists())

        dry = run_archive(source, archive, "--rotate")
        check("轮转要求 --yes", dry.returncode == 1 and (source / "agent.sqlite").exists(),
              dry.stdout[:300])

        holder = hold(source)
        try:
            locked = run_archive(source, archive, "--rotate", "--yes")
            check("sidecar 锁存在时拒绝轮转", locked.returncode != 0,
                  locked.stdout[-300:])
            check("拒绝轮转时活库还在", (source / "agent.sqlite").exists())
        finally:
            if holder.stdin is not None:
                holder.stdin.close()
            holder.wait(timeout=5)

        rotated = run_archive(source, archive, "--rotate", "--yes")
        rotated_body = json.loads(rotated.stdout)
        check("离线轮转成功", rotated.returncode == 0 and rotated_body.get("rotated") is True,
              rotated.stderr[:300])
        check("活库被移除", not (source / "agent.sqlite").exists())
        check("轮转前已归档", len(rotated_body.get("archived", [])) >= 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    if failed:
        print(f"=== {passed} passed, {len(failed)} failed ===")
        for item in failed:
            print(f"  - {item}")
        return 1
    print(f"=== {passed} passed, 0 failed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
