"""C3 host API: read-only review/propose, host-authorized commit.

    python scripts/test_durable_c3_api.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.assistant.adapters.base import BaseWorkerAdapter, artifact_event  # noqa: E402
from server.assistant.adapters.registry import register_adapter, unregister_adapter  # noqa: E402
from server.assistant.durable_api import create_durable_router, set_host_token  # noqa: E402
from server.assistant.durable_leases import LeaseStore  # noqa: E402
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


ROSTER = {
    "selection": {"max_concurrent_default": 2},
    "dispatch": {"max_steps": 4, "max_prompt_tokens": 80000, "max_retry": 2,
                 "spin_guard": {"max_identical": 3}},
    "agents": [
        {"id": "scribe", "name": "笔录官", "adapter": "manual", "summary": "写文档",
         "strengths": ["写作"], "cost_tier": "cheap", "max_concurrent": 10},
    ],
}


def ready(executive, title: str, summary: str = "已整理三版标题") -> dict:
    commission = executive.open(title, "整理一份交付")
    executive.dispatcher.dispatch(commission["id"], agent_id="scribe")
    executive.dispatcher.start(commission["id"])
    updated, _verdict = executive.dispatcher.submit(commission["id"], {
        "summary": summary,
        "intercepted": "没有",
        "artifacts": [{"kind": "note", "ref": "note:n1", "preview": summary}],
        "wired": True,
    })
    return updated


def ready_file(executive, title: str, path: Path) -> dict:
    commission = executive.open(title, "交付一个真实文件")
    executive.dispatcher.dispatch(commission["id"], agent_id="scribe")
    executive.dispatcher.start(commission["id"])
    updated, _verdict = executive.dispatcher.submit(commission["id"], {
        "summary": "已写出文件",
        "intercepted": "没有",
        "artifacts": [{"kind": "file", "ref": str(path)}],
        "wired": True,
    })
    return updated


def failed_l1(executive, title: str) -> dict:
    commission = executive.open(title, "交一张不合格的卡")
    executive.dispatcher.dispatch(commission["id"], agent_id="scribe")
    executive.dispatcher.start(commission["id"])
    executive.ledger.transition(commission["id"], "auditing")
    return executive.ledger.transition(commission["id"], "ready_to_report",
        audit={"passed": False, "layer": "l1", "created_at": 1.0,
               "findings": [{"rule_id": "required_fields", "severity": "blocker",
                             "detail": "没有 summary"}]},
        delivery={"summary": "", "artifacts": []})


def main() -> int:
    directory = tempfile.mkdtemp(prefix="durable-c3-")
    executive = ExecutiveService(directory, roster=ROSTER)
    leases = LeaseStore(executive.ledger)
    assistant = SimpleNamespace(
        executive=executive,
        _search_notes=lambda query: {"status": "available", "items": []},
        _read_note=lambda note_id, version: {"body": ""},
        _read_thread=lambda thread_id, query="": {"id": thread_id},
    )
    app = FastAPI()
    app.include_router(create_durable_router(assistant, SimpleNamespace(enabled=False), leases))
    app.include_router(create_executive_router(executive))
    set_host_token("test-token")
    headers = {"authorization": "Bearer test-token"}
    lease = leases.get(leases.issue("s_c3", "验收这批交付", "auto")["lease_id"])

    def body(**extra):
        return {"lease_id": lease.lease_id, "session_id": "s_c3", **extra}

    try:
        with TestClient(app) as client:
            print("[durable c3 api]")
            good = ready(executive, "合格交付")
            lease.commissions.add(good["id"])
            review = client.post("/internal/durable/review", headers=headers,
                                 json=body(commission_id=good["id"]))
            review_body = review.json()
            check("review 200", review.status_code == 200, str(review.status_code))
            check("review 推荐 accept", review_body.get("recommendation") == "accept",
                  str(review_body)[:300])
            check("review 带 artifact 验真", bool(review_body.get("artifact_checks")),
                  str(review_body)[:300])
            check("note artifact 不标 verified",
                  review_body.get("artifacts_verified") is False and
                  review_body.get("artifact_unverified_count") == 1,
                  str(review_body)[:300])
            before = executive.ledger.get(good["id"])["status"]
            accept_prop = client.post("/internal/durable/propose", headers=headers,
                                      json=body(commission_id=good["id"], action="accept"))
            accept_body = accept_prop.json()
            check("propose_accept 200", accept_prop.status_code == 200, str(accept_prop.status_code))
            proposal_id = accept_body.get("proposal_id")
            check("propose 不改台账", executive.ledger.get(good["id"])["status"] == before)
            policy = client.post("/internal/durable/authorize", headers=headers, json={
                "proposal_id": proposal_id, "approved": True, "source": "policy",
            })
            check("策略引擎未配置时拒绝授权", policy.status_code == 403, str(policy.status_code))
            authorized = client.post("/internal/durable/authorize", headers=headers, json={
                "proposal_id": proposal_id, "approved": True,
            })
            check("老板授权 proposal", authorized.status_code == 200, str(authorized.status_code))
            authorization_id = authorized.json().get("authorization_id")
            committed = client.post("/internal/durable/accept", headers=headers, json={
                "proposal_id": proposal_id, "authorization_id": authorization_id,
            })
            check("accept 200", committed.status_code == 200, str(committed.status_code))
            check("accept 到 done", executive.ledger.get(good["id"])["status"] == "done")
            duplicate = client.post("/internal/durable/accept", headers=headers, json={
                "proposal_id": proposal_id, "authorization_id": authorization_id,
            })
            check("accept 幂等", duplicate.status_code == 200 and
                  duplicate.json().get("duplicate") is True, str(duplicate.json())[:300])

            bad = failed_l1(executive, "L1 不合格")
            lease.commissions.add(bad["id"])
            bad_review = client.post("/internal/durable/review", headers=headers,
                                     json=body(commission_id=bad["id"])).json()
            check("L1 失败推荐 reject", bad_review.get("can_accept") is False,
                  str(bad_review)[:300])
            bad_prop = client.post("/internal/durable/propose", headers=headers,
                                   json=body(commission_id=bad["id"], action="accept"))
            check("L1 失败不可 propose_accept", bad_prop.status_code == 409,
                  str(bad_prop.status_code))

            reject_ready = ready(executive, "需要打回")
            lease.commissions.add(reject_ready["id"])
            reject_prop = client.post("/internal/durable/propose", headers=headers, json=body(
                commission_id=reject_ready["id"], action="reject",
                suggested_feedback="标题太长，收短一点",
            ))
            reject_body = reject_prop.json()
            check("propose_reject 200", reject_prop.status_code == 200,
                  str(reject_prop.status_code))
            reject_auth = client.post("/internal/durable/authorize", headers=headers, json={
                "proposal_id": reject_body.get("proposal_id"), "approved": True,
            }).json()
            rejected = client.post("/internal/durable/reject", headers=headers, json={
                "proposal_id": reject_body.get("proposal_id"),
                "authorization_id": reject_auth.get("authorization_id"),
                "feedback": "标题太长，收短一点",
            })
            check("reject 200", rejected.status_code == 200, str(rejected.status_code))
            rejected_state = executive.ledger.get(reject_ready["id"])
            check("reject 回到 running", rejected_state["status"] == "running",
                  str(rejected_state.get("status")))
            check("reject 留下老板批注",
                  "标题太长" in str((rejected_state.get("audit") or {}).get("boss_feedback")),
                  str(rejected_state.get("audit"))[:300])

            mismatch = client.post("/internal/durable/reject", headers=headers, json={
                "proposal_id": proposal_id, "authorization_id": authorization_id,
                "feedback": "错误动作",
            })
            check("proposal 动作不匹配被拒", mismatch.status_code == 409, str(mismatch.status_code))

            # A file artifact that existed at audit time and was deleted before
            # commit must not be acceptable.
            artifact_path = Path(directory) / "deliverable.md"
            artifact_path.write_text("delivered", encoding="utf-8")
            file_ready = ready_file(executive, "文件交付", artifact_path)
            lease.commissions.add(file_ready["id"])
            file_review = client.post("/internal/durable/review", headers=headers,
                                      json=body(commission_id=file_ready["id"])).json()
            check("文件交付 review 可验真",
                  file_review.get("can_accept") is True and
                  file_review.get("artifacts_verified") is True, str(file_review)[:300])
            file_prop = client.post("/internal/durable/propose", headers=headers,
                                    json=body(commission_id=file_ready["id"], action="accept")).json()
            file_auth = client.post("/internal/durable/authorize", headers=headers, json={
                "proposal_id": file_prop.get("proposal_id"), "approved": True,
            }).json()
            artifact_path.unlink()
            deleted = client.post("/internal/durable/accept", headers=headers, json={
                "proposal_id": file_prop.get("proposal_id"),
                "authorization_id": file_auth.get("authorization_id"),
            })
            check("文件删除后 accept 409", deleted.status_code == 409, str(deleted.status_code))

            # Findings change while passed/layer/created_at stay the same must
            # invalidate the old proposal evidence.
            mutable = ready(executive, "重审变化", "已按流程完成交付")
            check("重审单初始 ready_to_report", mutable["status"] == "ready_to_report",
                  str(mutable)[:500])
            lease.commissions.add(mutable["id"])
            mutable_prop = client.post("/internal/durable/propose", headers=headers,
                                       json=body(commission_id=mutable["id"], action="accept")).json()
            mutable_auth = client.post("/internal/durable/authorize", headers=headers, json={
                "proposal_id": mutable_prop.get("proposal_id"), "approved": True,
            }).json()
            old_audit = executive.ledger.get(mutable["id"])["audit"]
            executive.ledger.transition(mutable["id"], "running")
            executive.ledger.transition(mutable["id"], "auditing")
            executive.ledger.transition(mutable["id"], "ready_to_report", audit={
                **old_audit,
                "findings": [{"rule_id": "minor_note", "severity": "minor",
                              "detail": "新增提示"}],
            })
            changed = client.post("/internal/durable/accept", headers=headers, json={
                "proposal_id": mutable_prop.get("proposal_id"),
                "authorization_id": mutable_auth.get("authorization_id"),
            })
            check("findings 变化使旧提案失效", changed.status_code == 409 and
                  changed.json().get("detail") == "proposal_evidence_changed",
                  str(changed.json())[:300])

            # Product/workbench entry: existing review/accept/reject buttons now
            # use the same C3 gate. No host token is exposed to the browser.
            ui_ready = ready(executive, "工作台验收", "工作台完成")
            ui_review = client.get(f"/api/executive/commissions/{ui_ready['id']}/review")
            check("工作台 review 200", ui_review.status_code == 200 and
                  ui_review.json().get("can_accept") is True, str(ui_review.status_code))
            ui_accept = client.post(f"/api/executive/commissions/{ui_ready['id']}/accept",
                                    json={}, headers={"origin": "http://testserver"})
            check("工作台 accept 200", ui_accept.status_code == 200, str(ui_accept.status_code))
            check("工作台 accept 到 done",
                  executive.ledger.get(ui_ready["id"])["status"] == "done")
            ui_bad = failed_l1(executive, "工作台 L1 失败")
            ui_denied = client.post(f"/api/executive/commissions/{ui_bad['id']}/accept",
                                    json={}, headers={"origin": "http://testserver"})
            check("工作台 L1 失败 accept 409", ui_denied.status_code == 409,
                  str(ui_denied.status_code))
            ui_reject_ready = ready(executive, "工作台打回", "工作台重做")
            ui_reject = client.post(f"/api/executive/commissions/{ui_reject_ready['id']}/reject",
                                    json={"feedback": "标题太长，收短一点"},
                                    headers={"origin": "http://testserver"})
            check("工作台 reject 200", ui_reject.status_code == 200, str(ui_reject.status_code))
            check("工作台 reject 回到 running",
                  executive.ledger.get(ui_reject_ready["id"])["status"] == "running")
    finally:
        set_host_token(None)

    # The host /run path must actually pick up a rejected (running) order.
    class ReplayAdapter(BaseWorkerAdapter):
        adapter_type = "c3_replay"

        async def prepare_context(self, commission_id, briefing_packet):
            return {"commission_id": commission_id}

        async def execute(self, task_spec):
            yield artifact_event({
                "summary": "打回后已重做",
                "intercepted": "没有",
                "artifacts": [{"kind": "note", "ref": "note:n2"}],
                "wired": True,
            })

        async def cancel(self):
            return None

    register_adapter("c3_replay")(ReplayAdapter)
    replay_dir = tempfile.mkdtemp(prefix="durable-c3-replay-")
    replay_roster = {
        **ROSTER,
        "agents": [{**ROSTER["agents"][0], "adapter": "c3_replay"}],
    }
    try:
        replay_exec = ExecutiveService(replay_dir, roster=replay_roster)
        redo = ready(replay_exec, "打回重做")
        replay_exec.reject(redo["id"], "标题太长，收短一点")
        check("reject 后回到 running", replay_exec.ledger.get(redo["id"])["status"] == "running")
        asyncio.run(replay_exec.run_commission(redo["id"]))
        check("宿主 /run 真的重新执行",
              replay_exec.ledger.get(redo["id"])["status"] == "ready_to_report",
              replay_exec.ledger.get(redo["id"])["status"])
    finally:
        unregister_adapter("c3_replay")

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
