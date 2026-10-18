"""Shadow 模式自检：sidecar 照跑，结果只落日志，正主路径不受影响。

    python scripts/test_durable_shadow.py

确定性、零网络（只连本机临时端口）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

from server.assistant.durable_shadow import DurableShadow  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

passed, failed = 0, []

WORKER = {
    "id": "w1", "name": "笔录官", "adapter": "manual", "strengths": ["整理"],
    "weaknesses": [], "cost_tier": "cheap", "max_concurrent": 2,
    "eval_commands": ["required_fields"],
}

EVENTS = [
    {"type": "progress", "step_idx": 1, "text": "接单"},
    {"type": "artifact", "artifact": {"summary": "影子交差", "intercepted": "无",
                                      "artifacts": [{"kind": "note", "ref": "note:n_1"}],
                                      "wired": True}},
]


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


class FakeSidecar(BaseHTTPRequestHandler):
    runs: list[dict] = []

    def log_message(self, *args) -> None:
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        FakeSidecar.runs.append(json.loads(self.rfile.read(length) or b"{}"))
        self._json({"task_id": 3, "conversation_id": "conv", "ok": True})

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in EVENTS:
            self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n")
            self.wfile.flush()


def serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSidecar)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_shadow_writes_log() -> None:
    print("[shadow: event log]")
    FakeSidecar.runs = []
    server, endpoint = serve()
    try:
        log_path = Path(tempfile.mkdtemp(prefix="shadow-log-")) / "shadow.jsonl"
        shadow = DurableShadow(endpoint=endpoint, token="t", log_path=log_path)
        asyncio.run(shadow.run({"id": "c_shadow", "title": "整理", "brief": "整理标题", "agent_id": "w1"}))

        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
        check("日志记下了两条事件", len(rows) == 2, str(rows)[:200])
        check("两条都是事件行", all("event" in row for row in rows))
        check("第一条是进度", rows[0]["event"]["type"] == "progress")
        check("第二条是交差", rows[1]["event"]["type"] == "artifact")
        check("密件按 commission_id 递进去", FakeSidecar.runs[0]["commission_id"] == "c_shadow")
        check("影子带上了 objective", FakeSidecar.runs[0]["packet"]["objective"] == "整理标题")
    finally:
        server.shutdown()


def test_delegate_triggers_shadow() -> None:
    print("[shadow: wired into delegate]")
    service = ExecutiveService(Path(tempfile.mkdtemp(prefix="shadow-service-")),
                               roster={**load_roster(), "agents": [dict(WORKER)]})
    seen: list[str] = []

    async def recorder(commission: dict) -> None:
        seen.append(str(commission.get("id")))

    service.shadow = recorder

    async def go() -> None:
        await service.delegate("把七月选题理成三版标题")
        if service._shadow_tasks:
            await asyncio.gather(*service._shadow_tasks)

    asyncio.run(go())
    check("派单会顺手起一个影子", len(seen) == 1, str(seen))
    check("影子拿到的是同一单", seen[0].startswith("c_"), str(seen))
    check("影子不影响正主状态", service.commissions() != [])
    service.close()


def main() -> int:
    test_shadow_writes_log()
    test_delegate_triggers_shadow()
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
