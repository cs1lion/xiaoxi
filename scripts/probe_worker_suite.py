"""Live worker suite: run a small set of real commissions and score them.

Manual, costs tokens, needs Codex plus the local gateway:

    .\\.venv\\Scripts\\python.exe scripts\\probe_worker_suite.py

Each case gets its own workspace, so one failure cannot leak into the next.
The suite scores four things the single probe cannot: task variety, the
negative path, latency, and token cost.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_real_worker import build_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

CASES = [
    {
        "id": "create_note",
        "task": "测试：在 out 目录创建 note.txt，内容只有一行 OK",
        "expect": {"status": "ready_to_report", "file": "note.txt", "contains": "OK"},
    },
    {
        "id": "create_json",
        "task": '测试：在 out 目录创建 result.json，内容为 {"status":"ok","count":3}',
        "expect": {"status": "ready_to_report", "file": "result.json", "json": True},
    },
    {
        "id": "transform",
        "task": "测试：读取工作目录的 input.txt，把内容转成大写，写入 out/upper.txt",
        "setup": {"input.txt": "hello worker\n"},
        "expect": {"status": "ready_to_report", "file": "upper.txt", "contains": "HELLO WORKER"},
    },
    {
        "id": "chinese",
        "task": "测试：在 out 目录创建 chinese.txt，写入这句中文：小溪今天很好",
        "expect": {"status": "ready_to_report", "file": "chinese.txt", "contains": "小溪今天很好"},
    },
    {
        "id": "claude_summary",
        "task": "总结：用一句中文概括小溪是什么",
        "expect": {"status": "ready_to_report", "any_file_contains": "语音"},
    },
    {
        "id": "missing_input",
        "task": "测试：读取工作目录里不存在的 secret.txt，把内容原样写入 out/secret_copy.txt",
        "expect": {"status": "blocked"},
    },
]


def check(case: dict, workdir: Path, commission: dict) -> list[str]:
    expect = case["expect"]
    problems = []
    if commission.get("status") != expect.get("status"):
        problems.append(f"status={commission.get('status')} want={expect.get('status')}")
    if "file" in expect:
        path = workdir / "out" / expect["file"]
        if not path.is_file():
            problems.append(f"missing file {expect['file']}")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            if "contains" in expect and expect["contains"] not in text:
                problems.append(f"content miss {expect['contains']!r}")
            if expect.get("json"):
                try:
                    json.loads(text)
                except ValueError:
                    problems.append("invalid json")
    if "any_file_contains" in expect:
        outdir = workdir / "out"
        needle = expect["any_file_contains"]
        hits = []
        if outdir.is_dir():
            for path in outdir.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    if needle in path.read_text(encoding="utf-8", errors="replace"):
                        hits.append(path)
                except OSError:
                    continue
        if not hits:
            problems.append(f"no output file contains {needle!r}")
    return problems


async def run_case(case: dict, base: Path) -> dict:
    workdir = (base / case["id"]).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    for name, text in (case.get("setup") or {}).items():
        target = workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    service = ExecutiveService(workdir / "state", roster=build_roster(workdir))
    started = time.time()
    try:
        result = await service.delegate(case["task"])
        commission = service.commission(result["commission_id"])
    finally:
        service.close()
    problems = check(case, workdir, commission)
    audit = commission.get("audit") or {}
    return {
        "id": case["id"],
        "status": commission.get("status"),
        "problems": problems,
        "elapsed_ms": int((time.time() - started) * 1000),
        "prompt_tokens": int((audit.get("usage") or {}).get("prompt_tokens") or 0),
        "findings": [f.get("rule_id") for f in (audit.get("findings") or [])],
        "delivery": commission.get("delivery") or {},
        "blocked_question": commission.get("blocked_question") or "",
        "workdir": str(workdir),
    }


def ensure_gateway(port: int) -> subprocess.Popen | None:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return None
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_gateway.py"), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.25)
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return process
    process.terminate()
    return None


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("AQI_GATEWAY_URL", "http://127.0.0.1:15721/v1")
    os.environ.setdefault("AQI_GATEWAY_MODEL", "step-5-preview")
    base = (ROOT / "tmp" / f"worker-suite-{time.strftime('%Y%m%d-%H%M%S')}").resolve()
    base.mkdir(parents=True, exist_ok=True)
    wanted = {arg for arg in sys.argv[1:] if not arg.startswith("-")}
    cases = [case for case in CASES if not wanted or case["id"] in wanted]
    port = 15723
    os.environ["AQI_ANTHROPIC_URL"] = f"http://127.0.0.1:{port}"
    gateway = ensure_gateway(port)
    rows = []
    try:
        for case in cases:
            row = await run_case(case, base)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))
            sys.stdout.flush()
    finally:
        if gateway is not None:
            gateway.terminate()
            try:
                gateway.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gateway.kill()
    passed = sum(1 for row in rows if not row["problems"])
    summary = {
        "cases": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "elapsed_ms": sum(row["elapsed_ms"] for row in rows),
        "prompt_tokens": sum(row["prompt_tokens"] for row in rows),
        "rows": rows,
    }
    (base / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("cases", "passed", "failed", "elapsed_ms", "prompt_tokens")},
                     ensure_ascii=False, indent=2))
    print("VERIFIED" if passed == len(rows) else "NOT VERIFIED")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
