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


def test_switch_agent_inherits_workspace_root() -> None:
    """switch_agents 把 cli_subproc 切成 durable 时，写盘根不能丢。

    cli_subproc 的写盘根在 env.AQI_WORKSPACE_ROOT；durable 适配器只认顶层
    workspace_root。不继承这一项，sidecar 的 write_file 会拒绝一切写入——
    切换后能跑、能交文字，但拿不到真实文件成果。
    """
    print("[durable adapter: switch_agents keeps the workspace root]")
    base = load_roster()
    base["agents"] = [{
        "id": "dsh", "name": "真机 DSH", "adapter": "cli_subproc",
        "strengths": ["整理"], "weaknesses": [], "cost_tier": "medium",
        "max_concurrent": 1, "eval_commands": ["required_fields"],
        "env": {"AQI_WORKSPACE_ROOT": "data/commissions"},
    }]
    service = ExecutiveService(Path(tempfile.mkdtemp(prefix="durable-switch-")), roster=base)
    bound = service.bind_durable({"endpoint": "http://127.0.0.1:9", "token": "t"},
                                 apply_to=["dsh"])
    entry = service.evaluator.roster["agents"][0]
    check("switch_agents 点名后被绑定", bound == 1, str(bound))
    check("adapter 切成 durable", entry.get("adapter") == "durable", str(entry.get("adapter")))
    check("写盘根从 AQI_WORKSPACE_ROOT 继承", entry.get("workspace_root") == "data/commissions",
          str(entry.get("workspace_root")))
    service.close()


def test_reload_keeps_runtime_binding() -> None:
    """热重载整份替换花名册时，运行时注入的端点不能丢。

    YAML 里没有 endpoint/token；reload 后若整份替换，durable 单会在下一次派单
    时变成「没配 endpoint」。用 loader 返回一份没有端点的同构花名册来复现。
    """
    print("[durable adapter: hot reload keeps the binding]")
    startup = load_roster()
    startup["agents"] = [{key: value for key, value in WORKER.items()
                          if key not in ("endpoint", "token")}]
    fresh = load_roster()
    fresh["agents"] = [{key: value for key, value in WORKER.items()
                        if key not in ("endpoint", "token")}]
    service = ExecutiveService(Path(tempfile.mkdtemp(prefix="durable-reload-")),
                               roster=startup, roster_loader=lambda: fresh)
    service.bind_durable({"endpoint": "http://127.0.0.1:9", "token": "t"})
    changed = service.reload_roster()
    entry = service.evaluator.roster["agents"][0]
    check("热重载确实换了花名册", changed, str(changed))
    check("端点没有被 YAML 覆盖掉", entry.get("endpoint") == "http://127.0.0.1:9", str(entry))
    check("令牌还在", entry.get("token") == "t", str(entry))
    service.close()


def test_lookup_reads_run_without_creating() -> None:
    print("[durable adapter: lookup]")
    FakeSidecar.events = list(GOOD_EVENTS)
    FakeSidecar.runs = []
    server, endpoint = serve()
    try:
        from server.assistant.adapters import create_adapter
        adapter = create_adapter("durable", {**WORKER, "endpoint": endpoint})
        found = adapter.lookup("c_missing")
        check("内核里没有这一单时返回 None", found is None, str(found))
        check("lookup 不建单", FakeSidecar.runs == [], str(FakeSidecar.runs))
    finally:
        server.shutdown()


def test_recovery_adopts_finished_durable_run() -> None:
    """重启时内核已经干完的单，采纳成果，不判"重跑吧"。

    这是对账唯一的合法动作：只采纳内核已终态的成果，永不替内核把没跑完的单
    判成功，也不自动 done。
    """
    print("[durable adapter: restart reconciliation]")

    class FinishedSidecar(FakeSidecar):
        seen_attempts: list[str] = []

        def do_GET(self) -> None:
            if self.path.startswith("/v1/lookup"):
                from urllib.parse import parse_qs, urlparse
                attempt = (parse_qs(urlparse(self.path).query).get("attempt") or [""])[0]
                FinishedSidecar.seen_attempts.append(attempt)
                # 只认 @1：这一单重做过一次，内核里没有 @0 的任务。
                # 查询若不带 attempt（默认 0），这里就如实返回"查不到"，
                # 对账会退化成 BLOCKED——正是要防的那个回归。
                if attempt == "1":
                    self._json({"found": True, "task_id": 7, "conversation_id": "conv-1"})
                else:
                    self._json({"found": False})
                return
            if self.path.startswith("/v1/runs/7"):
                self._json({"status": "terminal",
                            "outcome": {"status": "completed", "result": {"artifact": {
                                "summary": "内核干完的三版标题",
                                "intercepted": "没有需要老板拍板的事",
                                "artifacts": [{"kind": "note", "ref": "note:n_1"}],
                                "wired": True}}}})
                return
            self._json({"error": "not_found"}, 404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FinishedSidecar)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        service = build(endpoint)
        commission = service.open("理三版标题", "把七月选题理成三版标题")
        service.dispatch(commission["id"], intent="整理标题")
        service.start(commission["id"])
        # 模拟"这一单重做过一次"：台账 retry_count 必须变成 1，内核里对应的
        # 幂等键也随之变成 @1。进程死在半路，台账停在 running。
        service.ledger.transition(commission["id"], "blocked",
                                  blocked_question="上一版没过初审，重做一次")
        service.ledger.transition(commission["id"], "running",
                                  blocked_question="", retry_delta=1)
        check("测试前提：这一单是第 1 次重做", service.commission(commission["id"])["retry_count"] == 1,
              str(service.commission(commission["id"]).get("retry_count")))
        from server.assistant.adapters import create_adapter
        adapter = create_adapter("durable", {**WORKER, "endpoint": endpoint})
        recovered = service.recover_interrupted(durable_lookup=adapter.lookup)
        item = service.commission(commission["id"])
        check("对账查询带上了 attempt", FinishedSidecar.seen_attempts == ["1"],
              str(FinishedSidecar.seen_attempts))
        check("已跑完的单被采纳而不是打回重跑", commission["id"] in recovered["adopted"],
              str(recovered))
        check("成果进了台账", item["status"] == "ready_to_report", str(item["status"]))
        check("交付摘要来自内核", (item.get("delivery") or {}).get("summary") == "内核干完的三版标题",
              str(item.get("delivery")))
        service.close()
    finally:
        httpd.shutdown()


def main() -> int:
    test_bind_durable()
    test_switch_agent_inherits_workspace_root()
    test_reload_keeps_runtime_binding()
    test_lookup_reads_run_without_creating()
    test_recovery_adopts_finished_durable_run()
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
