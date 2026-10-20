"""C4 workbench authority: shared state, boss confirmation, per-call approval.

The browser routes are not a second ledger path. They read the same
DurableState as the sidecar-facing router, and every write is a same-origin
relay of an explicit boss click.

    python scripts/test_durable_authorization_ui.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.assistant.durable_api import create_durable_router, set_host_token  # noqa: E402
from server.assistant.durable_leases import ProposalStore  # noqa: E402
from server.assistant.durable_state import DurableState  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402
from server.assistant.executive_api import create_executive_router  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


class ApprovalSidecar(BaseHTTPRequestHandler):
    """Only /v1/approvals; enough to prove the host relays per-call grants."""

    def _json(self, value: dict) -> None:
        raw = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path)
        if path.path != "/v1/approvals":
            self.send_response(404)
            self.end_headers()
            return
        self.server.gets.append(parse_qs(path.query))
        self._json({"pending": self.server.pending})

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/v1/approvals":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.posts.append(body)
        tool = str(body.get("tool") or "")
        action_hash = str(body.get("action_hash") or "")
        if body.get("revoke") is True:
            self.server.pending = [
                item for item in self.server.pending
                if not (item["tool"] == tool and
                        (not action_hash or item["action_hash"] == action_hash))
            ]
        else:
            self.server.pending = [
                item for item in self.server.pending
                if not (item["tool"] == tool and
                        (not action_hash or item["action_hash"] == action_hash))
            ]
        self._json({"pending": self.server.pending})

    def log_message(self, *_args) -> None:
        pass


def start_sidecar(pending: list[dict]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApprovalSidecar)
    server.pending = list(pending)
    server.posts = []
    server.gets = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def make_roster(endpoint: str) -> dict:
    return {
        "selection": {"max_concurrent_default": 1},
        "dispatch": {"max_steps": 4, "max_prompt_tokens": 80000, "max_retry": 2,
                     "spin_guard": {"max_identical": 3}},
        "agents": [
            {"id": "scribe", "name": "笔录官", "adapter": "durable", "summary": "写文档",
             "strengths": ["写作"], "weaknesses": ["不碰代码"], "cost_tier": "cheap",
             "max_concurrent": 10, "endpoint": endpoint, "token": "sidecar-token"},
        ],
    }


def ready_to_report(executive, title: str) -> dict:
    commission = executive.open(title, "整理一份交付")
    executive.dispatcher.dispatch(commission["id"], agent_id="scribe")
    executive.dispatcher.start(commission["id"])
    updated, _verdict = executive.dispatcher.submit(commission["id"], {
        "summary": "已整理三版标题",
        "intercepted": "没有",
        "artifacts": [{"kind": "note", "ref": "note:n1", "preview": "三版标题"}],
        "wired": True,
    })
    return updated


def blocked_approval(executive, title: str) -> dict:
    commission = executive.open(title, "需要跑一次 Worker")
    executive.dispatcher.dispatch(commission["id"], agent_id="scribe")
    return executive.dispatcher.block(commission["id"], "工具 run_worker 要老板放行",
                                      kind="approval")


def main() -> int:
    sidecar = start_sidecar([{"tool": "run_worker", "action_hash": "hash-one"}])
    endpoint = f"http://127.0.0.1:{sidecar.server_address[1]}"
    directory = tempfile.mkdtemp(prefix="durable-auth-ui-")
    executive = ExecutiveService(directory, roster=make_roster(endpoint))
    state = DurableState(executive.ledger)
    assistant = SimpleNamespace(
        executive=executive,
        _search_notes=lambda query: {"status": "available", "items": []},
        _read_note=lambda note_id, version: {"body": ""},
        _read_thread=lambda thread_id, query="": {"id": thread_id},
    )
    resumed: list[str] = []

    async def fake_resume(commission_id: str) -> None:
        resumed.append(commission_id)

    executive.resume = fake_resume
    app = FastAPI()
    app.include_router(create_durable_router(
        assistant, SimpleNamespace(enabled=False), runtime_provider=None, state=state))
    app.include_router(create_executive_router(executive, durable_state=state))
    set_host_token("test-token")
    headers = {"authorization": "Bearer test-token"}

    try:
        with TestClient(app) as client:
            print("[durable authorization ui]")
            origin = {"origin": "http://evil.example", "host": "127.0.0.1"}
            cross = client.post("/api/executive/durable/confirmations/nope",
                                json={"approved": True}, headers=origin)
            check("跨源不能批确认单", cross.status_code == 403, str(cross.status_code))

            lease = client.post("/internal/durable/lease", headers=headers, json={
                "session_id": "s_ui", "user_intent": "整理标题", "mode": "confirm",
            }).json()
            dispatch_body = {"lease_id": lease["lease_id"], "session_id": "s_ui",
                             "idempotency_key": "ui-1", "title": "整理标题",
                             "brief": "把七月选题理成三版标题", "intent": "整理标题"}
            first = client.post("/internal/durable/dispatch", headers=headers,
                                json=dispatch_body).json()
            confirmation_id = first.get("confirmation_id")
            check("内部派单先给确认号", bool(confirmation_id), str(first)[:200])

            listed = client.get("/api/executive/durable/confirmations").json()["items"]
            check("工作台能看到待批派单",
                  any(item["confirmation_id"] == confirmation_id for item in listed),
                  str(listed)[:200])
            check("待批卡片带标题与有效期",
                  any(item.get("title") == "整理标题" and item.get("expires_at")
                      for item in listed))

            missing = client.post(
                f"/api/executive/durable/confirmations/{confirmation_id}", json={})
            check("确认单必须带布尔批准", missing.status_code == 400,
                  str(missing.status_code))
            approved = client.post(
                f"/api/executive/durable/confirmations/{confirmation_id}",
                json={"approved": True})
            check("老板点批准", approved.status_code == 200 and
                  approved.json().get("status") == "approved", str(approved.json())[:200])
            left = client.get("/api/executive/durable/confirmations").json()["items"]
            check("批过的不再挂在待办", not any(
                item["confirmation_id"] == confirmation_id for item in left))

            dispatched = client.post("/internal/durable/dispatch", headers=headers, json={
                **dispatch_body, "confirmation_id": confirmation_id})
            commission_id = dispatched.json().get("commission_id")
            check("批准后同一个确认号能真派单",
                  dispatched.status_code == 200 and bool(commission_id),
                  str(dispatched.json())[:300])
            again = client.post("/internal/durable/dispatch", headers=headers, json=dispatch_body)
            check("重复派单仍返回同一单",
                  again.json().get("commission_id") == commission_id and
                  again.json().get("duplicate") is True, str(again.json())[:200])

            # ---- C3 proposal: model proposes, boss authorizes on the card ----
            delivery = ready_to_report(executive, "交付一份验收简报")
            state.leases.record_dispatch(state.leases.get(lease["lease_id"]),
                                         delivery["id"])
            proposed = client.post("/internal/durable/propose", headers=headers, json={
                "lease_id": lease["lease_id"], "session_id": "s_ui",
                "commission_id": delivery["id"], "action": "accept",
            })
            proposal_id = proposed.json().get("proposal_id")
            check("模型只能提议验收",
                  proposed.status_code == 200 and bool(proposal_id) and
                  proposed.json().get("recommendation") == "accept",
                  str(proposed.json())[:200])
            check("提议阶段台账没动",
                  executive.ledger.get(delivery["id"])["status"] == "ready_to_report")

            open_items = client.get("/api/executive/durable/proposals").json()["items"]
            check("工作台列出验收建议",
                  any(item["proposal_id"] == proposal_id and item.get("title") ==
                      "交付一份验收简报" for item in open_items), str(open_items)[:200])
            check("卡上标注成果未全部复验",
                  all(item.get("artifacts_verified") is False for item in open_items
                      if item["proposal_id"] == proposal_id))

            committed = client.post(
                f"/api/executive/durable/proposals/{proposal_id}/commit",
                json={"action": "accept"})
            check("老板授权后真验收",
                  committed.status_code == 200 and
                  committed.json().get("commission", {}).get("status") == "done",
                  str(committed.json())[:300])
            replay = client.post(
                f"/api/executive/durable/proposals/{proposal_id}/commit",
                json={"action": "accept"})
            check("重复授权点击是幂等的",
                  replay.status_code == 200 and replay.json().get("duplicate") is True,
                  str(replay.json())[:200])

            # ---- per-call tool approval ----
            blocked = blocked_approval(executive, "放行一次 Worker 调用")
            pending_rows = client.get("/api/executive/durable/approvals").json()["items"]
            row = next((item for item in pending_rows
                        if item["commission_id"] == blocked["id"]), None)
            check("工作台能看到待放行工具",
                  row is not None and row.get("tool") == "run_worker" and
                  row.get("action_hash") == "hash-one", str(pending_rows)[:300])

            bad_flag = client.post("/api/executive/durable/approvals", json={
                "commission_id": blocked["id"], "tool": "run_worker",
                "action_hash": "hash-one", "approved": "yes",
            })
            check("放行决定必须是布尔", bad_flag.status_code == 400,
                  str(bad_flag.status_code))
            wrong = client.post("/api/executive/durable/approvals", json={
                "commission_id": blocked["id"], "tool": "run_worker",
                "action_hash": "hash-other", "approved": True,
            })
            check("换一个调用哈希不能放行", wrong.status_code == 409,
                  str(wrong.status_code))
            check("错哈希没有打到 sidecar", sidecar.posts == [], str(sidecar.posts))

            allowed = client.post("/api/executive/durable/approvals", json={
                "commission_id": blocked["id"], "tool": "run_worker",
                "action_hash": "hash-one", "approved": True,
            })
            check("按调用放行成功", allowed.status_code == 200 and
                  allowed.json().get("resumed") is True, str(allowed.json())[:300])
            check("放行只送一个精确哈希",
                  sidecar.posts == [{"commission_id": blocked["id"],
                                     "tool": "run_worker",
                                     "action_hash": "hash-one"}], str(sidecar.posts))
            check("放行后恢复执行一次", resumed == [blocked["id"]], str(resumed))
            check("放行后台账回到 running",
                  executive.ledger.get(blocked["id"])["status"] == "running",
                  executive.ledger.get(blocked["id"])["status"])

            # A second live call of the same tool needs its own approval.
            sidecar.pending = [{"tool": "run_worker", "action_hash": "hash-two"}]
            second = blocked_approval(executive, "再放行一次")
            sidecar.posts.clear()
            veto = client.post("/api/executive/durable/approvals", json={
                "commission_id": second["id"], "tool": "run_worker",
                "action_hash": "hash-two", "approved": False,
            })
            check("驳回不放行也不恢复",
                  veto.status_code == 200 and veto.json().get("approved") is False and
                  veto.json().get("resumed") is False, str(veto.json())[:200])
            check("驳回走 revoke",
                  sidecar.posts == [{"commission_id": second["id"],
                                     "tool": "run_worker",
                                     "action_hash": "hash-two", "revoke": True}],
                  str(sidecar.posts))

            # An expired authorization can be re-issued by a new boss click;
            # a live one stays one-time.
            now = [100.0]
            store = ProposalStore(clock=lambda: now[0], proposal_ttl=30,
                                  authorization_ttl=5)
            item = store.create(commission_id="c1", lease_id="l1", session_id="s1",
                                action="accept", evidence_hash="h1")
            first = store.authorize(item.proposal_id).authorization_id
            now[0] += 10
            reissued = store.authorize(item.proposal_id).authorization_id
            check("过期授权可重新签发", reissued != first)

            # ---- no durable state: the surface disappears, not crashes ----
            bare = FastAPI()
            bare.include_router(create_executive_router(executive))
            with TestClient(bare) as solo:
                off = solo.get("/api/executive/durable/confirmations")
            check("没启用 durable 时授权面 503", off.status_code == 503,
                  str(off.status_code))
    finally:
        sidecar.shutdown()
        sidecar.server_close()

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
