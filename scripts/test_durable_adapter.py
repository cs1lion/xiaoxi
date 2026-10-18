"""durable 适配器离线自检：用一个假 sidecar 验三条契约。

    python scripts/test_durable_adapter.py

1. 派单密件按内部契约递进去（commission_id / agent_id / packet 都在）。
2. sidecar 的 SSE 事件被 1:1 映射成适配器五种事件，artifact 能进初审。
3. sidecar 不可用时派单转 BLOCKED 并明示"执行内核不可用"，不假装执行。

确定性、零网络（只连本机 127.0.0.1 的临时端口）、零真实模型调用。
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

from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

passed, failed = 0, []

WORKER = {
    "id": "d1", "name": "durable 工", "adapter": "durable",
    "strengths": ["整理"], "weaknesses": [], "cost_tier": "cheap", "max_concurrent": 2,
    "eval_commands": ["required_fields", "artifacts_exist"],
    "token": "test-token",
}

GOOD_EVENTS = [
    {"type": "progress", "step_idx": 1, "text": "接单"},
    {"type": "token", "model": "deepseek-chat", "prompt_tokens": 12,
     "completion_tokens": 7, "ttft_ms": 40, "duration_ms": 90, "status_code": 200},
    {"type": "progress", "step_idx": 2, "text": "收尾"},
    {"type": "artifact", "artifact": {"summary": "三版标题已经理好",
                                              "intercepted": "没有需要老板拍板的事",
                                              "artifacts": [{"kind": "note", "ref": "note:n_1", "preview": "三版"}],
                                              "wired": True}},
    # 用量在交差之后才补齐：适配器必须继续读到流尾，否则这条会丢。
    {"type": "token", "model": "deepseek-chat", "prompt_tokens": 20,
     "completion_tokens": 10, "ttft_ms": 30, "duration_ms": 80, "status_code": 200},
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
    events: list[dict] = []
    runs: list[dict] = []
    cancelled: list[str] = []
    fail: bool = False

    def log_message(self, *args) -> None:  # noqa: D401 - keep the test quiet
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
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v1/runs":
            if FakeSidecar.fail:
                self._json({"error": "unavailable"}, 503)
                return
            FakeSidecar.runs.append({"body": body, "authorization": self.headers.get("authorization")})
            self._json({"task_id": 7, "conversation_id": "conv-1"})
            return
        if self.path.endswith("/cancel"):
            FakeSidecar.cancelled.append(self.path)
            self._json({"result": "terminal"})
            return
        self._json({"error": "not_found"}, 404)

    def do_GET(self) -> None:
        if not self.path.startswith("/v1/events"):
            self._json({"error": "not_found"}, 404)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in FakeSidecar.events:
            self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n")
            self.wfile.flush()


def serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSidecar)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def build(endpoint: str) -> ExecutiveService:
    base = load_roster()
    base["agents"] = [dict(WORKER, endpoint=endpoint)]
    return ExecutiveService(Path(tempfile.mkdtemp(prefix="durable-adapter-")), roster=base)


def run_one(service: ExecutiveService) -> dict:
    commission = service.open("理三版标题", "把七月选题理成三版标题")
    service.dispatch(commission["id"], intent="整理标题")
    service.start(commission["id"])
    return asyncio.run(service.run_commission(commission["id"]))


def test_happy_path() -> None:
    print("[durable adapter: happy path]")
    FakeSidecar.events = list(GOOD_EVENTS)
    FakeSidecar.runs = []
    FakeSidecar.cancelled = []
    FakeSidecar.fail = False
    server, endpoint = serve()
    try:
        service = build(endpoint)
        result = run_one(service)
        item = result["commission"]

        check("artifact 进初审并通过", item["status"] == "ready_to_report", str(item["status"]))
        check("交付摘要是真实产物", (item.get("delivery") or {}).get("summary") == "三版标题已经理好",
              str(item.get("delivery")))
        check("进度步数落到台账", int(item["step_idx"]) >= 2, str(item["step_idx"]))

        sent = FakeSidecar.runs[0]
        check("密件按内部契约递进去",
              sent["body"]["commission_id"] == item["id"] and sent["body"]["agent_id"] == "d1")
        check("密件带上了 packet", isinstance(sent["body"]["packet"], dict)
              and "objective" in sent["body"]["packet"])
        check(" token 走 Authorization", sent["authorization"] == "Bearer test-token")
        check("跑完会收尾取消（幂等）", len(FakeSidecar.cancelled) == 1, str(FakeSidecar.cancelled))
        usage = service.commission_usage(item["id"])
        check("token 事件落成 span", int(usage.get("calls") or 0) >= 1, str(usage))
        service.close()
    finally:
        server.shutdown()


def test_sidecar_unavailable() -> None:
    print("[durable adapter: sidecar unavailable]")
    FakeSidecar.events = []
    FakeSidecar.runs = []
    FakeSidecar.cancelled = []
    FakeSidecar.fail = True
    server, endpoint = serve()
    try:
        service = build(endpoint)
        result = run_one(service)
        item = result["commission"]
        check("内核不可用转 BLOCKED", item["status"] == "blocked", str(item["status"]))
        check("请示写明执行内核不可用",
              "执行内核" in (item.get("blocked_question") or ""), str(item.get("blocked_question")))
        service.close()
    finally:
        server.shutdown()


def test_missing_endpoint() -> None:
    print("[durable adapter: misconfigured worker]")
    base = load_roster()
    base["agents"] = [{key: value for key, value in WORKER.items() if key != "endpoint"}]
    service = ExecutiveService(Path(tempfile.mkdtemp(prefix="durable-adapter-")), roster=base)
    result = run_one(service)
    item = result["commission"]
    check("没配 endpoint 不假装执行", item["status"] == "blocked", str(item["status"]))
    check("请示点名执行体没起来",
          "执行体没起来" in (item.get("blocked_question") or ""), str(item.get("blocked_question")))
    service.close()


def test_bind_durable() -> None:
    print("[durable adapter: bind endpoint after the supervisor is up]")
    base = load_roster()
    base["agents"] = [{key: value for key, value in WORKER.items() if key != "endpoint"}]
    service = ExecutiveService(Path(tempfile.mkdtemp(prefix="durable-bind-")), roster=base)
    bound = service.bind_durable({"endpoint": "http://127.0.0.1:9", "token": "t"})
    check("绑定到一个 Worker", bound == 1, str(bound))
    entry = service.evaluator.roster["agents"][0]
    check("端点写进了花名册", entry.get("endpoint") == "http://127.0.0.1:9", str(entry))
    check("令牌写进了花名册", entry.get("token") == "t", str(entry))
    service.close()


def main() -> int:
    test_bind_durable()
    test_happy_path()
    test_sidecar_unavailable()
    test_missing_endpoint()
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
