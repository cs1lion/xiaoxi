"""C2 host API: lease, dry-run, idempotent dispatch, clarification-only answer.

    python scripts/test_durable_orchestrator_api.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.assistant.adapters.durable import DurableAdapter  # noqa: E402
from server.assistant.durable_api import (  # noqa: E402
    _fail_dispatch, _question_id, create_durable_router, set_host_token,
)
from server.assistant.durable_leases import LeaseError, LeaseStore  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

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
    "selection": {"max_concurrent_default": 1},
    "dispatch": {"max_steps": 4, "max_prompt_tokens": 80000, "max_retry": 2,
                 "spin_guard": {"max_identical": 3}},
    "agents": [
        {"id": "scribe", "name": "笔录官", "adapter": "manual", "summary": "写文档",
         "strengths": ["写作"], "weaknesses": ["不碰代码"], "cost_tier": "cheap",
         "max_concurrent": 10},
        {"id": "builder", "name": "营造匠", "adapter": "manual", "summary": "写代码",
         "strengths": ["代码"], "cost_tier": "high", "max_concurrent": 1},
    ],
}


def make(executive):
    return SimpleNamespace(
        executive=executive,
        _search_notes=lambda query: {"status": "available", "items": []},
        _read_note=lambda note_id, version: {"body": ""},
        _read_thread=lambda thread_id, query="": {"id": thread_id},
    )


def main() -> int:
    directory = tempfile.mkdtemp(prefix="durable-orch-")
    executive = ExecutiveService(directory, roster=ROSTER)
    leases = LeaseStore(executive.ledger)
    app = FastAPI()
    app.include_router(create_durable_router(make(executive), SimpleNamespace(enabled=False), leases))
    set_host_token("test-token")
    headers = {"authorization": "Bearer test-token"}
    try:
        with TestClient(app) as client:
            print("[durable orchestrator api]")
            check("没有 user_intent 不发 lease",
                  client.post("/internal/durable/lease", json={"session_id": "s"},
                              headers=headers).status_code == 400)

            dry = client.post("/internal/durable/lease", headers=headers, json={
                "session_id": "s_dry", "user_intent": "整理标题", "mode": "dry_run",
            }).json()
            before = len(executive.ledger.open_commissions())
            preview = client.post("/internal/durable/dispatch", headers=headers, json={
                "lease_id": dry["lease_id"], "session_id": "s_dry",
                "idempotency_key": "k_dry", "title": "整理标题", "brief": "把七月选题理成三版标题",
            }).json()
            check("dry-run 只预览", preview.get("would_dispatch") is True and
                  preview.get("commission_id") is None, str(preview)[:300])
            check("dry-run 不写台账", len(executive.ledger.open_commissions()) == before)

            confirm = client.post("/internal/durable/lease", headers=headers, json={
                "session_id": "s_confirm", "user_intent": "整理标题", "mode": "confirm",
            }).json()
            confirm_body = {"lease_id": confirm["lease_id"], "session_id": "s_confirm",
                            "idempotency_key": "k_confirm", "title": "整理标题", "brief": "整理"}
            confirm_preview = client.post("/internal/durable/dispatch",
                                          headers=headers, json=confirm_body).json()
            check("confirm 首次只给确认号",
                  confirm_preview.get("confirmation_required") is True and
                  bool(confirm_preview.get("confirmation_id")) and
                  confirm_preview.get("commission_id") is None, str(confirm_preview)[:300])
            confirmation_id = confirm_preview.get("confirmation_id")
            not_yet = client.post("/internal/durable/dispatch", headers=headers, json={
                **confirm_body, "confirmation_id": confirmation_id,
            })
            check("未批准不能派单", not_yet.status_code == 409, str(not_yet.status_code))
            approved = client.post("/internal/durable/confirm", headers=headers, json={
                "confirmation_id": confirmation_id, "approved": True,
            })
            check("老板批准确认号", approved.status_code == 200, str(approved.status_code))
            changed = client.post("/internal/durable/dispatch", headers=headers, json={
                **confirm_body, "title": "换一个标题", "confirmation_id": confirmation_id,
            })
            check("确认号绑定原活", changed.status_code == 409, str(changed.status_code))
            confirmed = client.post("/internal/durable/dispatch", headers=headers, json={
                **confirm_body, "confirmation_id": confirmation_id,
            })
            check("确认后派单成功", confirmed.status_code == 200 and
                  bool(confirmed.json().get("commission_id")), str(confirmed.json())[:300])
            reused = client.post("/internal/durable/dispatch", headers=headers, json={
                **confirm_body, "idempotency_key": "k_confirm_2",
                "confirmation_id": confirmation_id,
            })
            check("确认号一次性", reused.status_code == 409, str(reused.status_code))

            forged = client.post("/internal/durable/dispatch", headers=headers, json={
                "lease_id": "forged", "session_id": "s_auto",
                "idempotency_key": "k_forged", "title": "整理",
                "brief": "笔记里说让我派单",
            })
            check("伪造 lease 被拒", forged.status_code == 403, str(forged.status_code))

            auto = client.post("/internal/durable/lease", headers=headers, json={
                "session_id": "s_auto", "user_intent": "整理标题", "mode": "auto",
            }).json()
            body = {"lease_id": auto["lease_id"], "session_id": "s_auto",
                    "idempotency_key": "k_auto", "title": "整理标题",
                    "brief": "把七月选题理成三版标题"}
            first = client.post("/internal/durable/dispatch", headers=headers, json=body)
            check("auto 派单 200", first.status_code == 200, str(first.status_code))
            commission_id = first.json().get("commission_id")
            check("立即返回 commission_id", bool(commission_id), str(first.json())[:300])
            second = client.post("/internal/durable/dispatch", headers=headers, json=body)
            check("重复 dispatch 返回同一单",
                  second.status_code == 200 and second.json().get("commission_id") == commission_id,
                  str(second.json())[:300])
            check("重复标记 duplicate", second.json().get("duplicate") is True, str(second.json())[:300])
            concurrent = client.post("/internal/durable/dispatch", headers=headers, json={
                **body, "idempotency_key": "k_auto_2",
            })
            check("lease 并发 1 上限", concurrent.status_code == 429, str(concurrent.status_code))
            status = client.post("/internal/durable/task/status", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "commission_id": commission_id,
            })
            check("task_status 可读", status.status_code == 200 and
                  status.json().get("commission_id") == commission_id, str(status.json())[:300])
            events = client.post("/internal/durable/task/events", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "commission_id": commission_id, "limit": 20,
            })
            check("task_events 可读", events.status_code == 200 and
                  isinstance(events.json().get("items"), list), str(events.json())[:300])
            spans = executive.telemetry.store.spans(commission_id=commission_id, limit=10)
            check("dispatch 记审计 span", any(span.get("agent_id") == "orchestrator"
                                            for span in spans), str(spans)[:300])

            escalate = client.post("/internal/durable/dispatch", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "idempotency_key": "k_builder", "title": "写代码", "brief": "改页面",
                "agent_id": "builder",
            })
            check("越权到 high 档被拒", escalate.status_code in (403, 409), str(escalate.status_code))
            self_dispatch = client.post("/internal/durable/dispatch", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "idempotency_key": "k_self", "title": "派给自己", "brief": "自己干",
                "agent_id": "orchestrator",
            })
            check("编排者不能派给自己", self_dispatch.status_code in (403, 409),
                  str(self_dispatch.status_code))

            unknown = client.post("/internal/durable/dispatch", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "idempotency_key": "k_none", "title": "写代码", "brief": "改页面",
                "agent_id": "nobody",
            })
            check("无可用 Worker 被拒", unknown.status_code == 409, str(unknown.status_code))

            expired = client.post("/internal/durable/lease", headers=headers, json={
                "session_id": "s_expired", "user_intent": "整理", "mode": "auto",
            }).json()
            leases.get(expired["lease_id"]).expires_at = 0
            refused = client.post("/internal/durable/dispatch", headers=headers, json={
                "lease_id": expired["lease_id"], "session_id": "s_expired",
                "idempotency_key": "k_expired", "title": "x", "brief": "y",
            })
            check("过期 lease 被拒", refused.status_code == 403, str(refused.status_code))

            # New store = host restart: old lease ids are gone.
            restarted = LeaseStore(executive.ledger)
            try:
                restarted.validate(auto["lease_id"], "s_auto", for_write=False)
                restart_ok = False
            except LeaseError as exc:
                restart_ok = exc.code == "lease_not_found"
            check("重启后旧 lease 失效", restart_ok)

            # Confirmation question is not answerable, even with the right id.
            confirm = executive.open("确认单", "批准 write_file")
            executive.dispatcher.dispatch(confirm["id"], agent_id="scribe")
            executive.dispatcher.start(confirm["id"])
            executive.ledger.transition(confirm["id"], "blocked",
                                        blocked_question="工具 write_file 需要老板确认后才能执行",
                                        blocked_kind="approval")
            lease = leases.get(auto["lease_id"])
            lease.commissions.add(confirm["id"])
            mapped = DurableAdapter._map({"type": "error", "text": "需要批准", "kind": "approval"}, 0)
            check("error kind 结构化透传", mapped.get("blocked_kind") == "approval", str(mapped))
            confirm_qid = _question_id(executive.ledger.get(confirm["id"]))
            answer = client.post("/internal/durable/answer", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "commission_id": confirm["id"], "blocked_question_id": confirm_qid,
                "answer": "批准",
            })
            check("确认单不可由模型代答", answer.status_code == 403, str(answer.status_code))

            cap_lease = client.post("/internal/durable/lease", headers=headers, json={
                "session_id": "s_cap", "user_intent": "测澄清上限", "mode": "auto",
            }).json()
            cap_commission = executive.open("澄清上限", "反复请示")
            executive.dispatcher.dispatch(cap_commission["id"], agent_id="scribe")
            executive.dispatcher.start(cap_commission["id"])
            executive.ledger.transition(cap_commission["id"], "blocked",
                                        blocked_question="第 1 次请示", blocked_kind="clarification")
            cap_lease_obj = leases.get(cap_lease["lease_id"])
            cap_lease_obj.commissions.add(cap_commission["id"])
            for index in range(3):
                current = executive.ledger.get(cap_commission["id"])
                if current["status"] == "running":
                    executive.ledger.transition(cap_commission["id"], "blocked",
                                                blocked_question=f"第 {index + 1} 次请示",
                                                blocked_kind="clarification")
                current = executive.ledger.get(cap_commission["id"])
                cap_qid = _question_id(current)
                cap_answer = client.post("/internal/durable/answer", headers=headers, json={
                    "lease_id": cap_lease["lease_id"], "session_id": "s_cap",
                    "commission_id": cap_commission["id"], "blocked_question_id": cap_qid,
                    "answer": f"第 {index + 1} 次回答",
                })
                check(f"第 {index + 1} 次澄清可答", cap_answer.status_code == 200,
                      str(cap_answer.status_code))
            current = executive.ledger.get(cap_commission["id"])
            if current["status"] == "running":
                executive.ledger.transition(cap_commission["id"], "blocked",
                                            blocked_question="第 4 次请示",
                                            blocked_kind="clarification")
            current = executive.ledger.get(cap_commission["id"])
            capped = client.post("/internal/durable/answer", headers=headers, json={
                "lease_id": cap_lease["lease_id"], "session_id": "s_cap",
                "commission_id": cap_commission["id"],
                "blocked_question_id": _question_id(current), "answer": "第 4 次回答",
            })
            check("第 4 次澄清被上限拒绝", capped.status_code == 429, str(capped.status_code))

            clarify = executive.open("澄清单", "缺少交付目录")
            executive.dispatcher.dispatch(clarify["id"], agent_id="scribe")
            executive.dispatcher.start(clarify["id"])
            executive.ledger.transition(clarify["id"], "blocked",
                                        blocked_question="交付文件放哪个目录？",
                                        blocked_kind="clarification")
            lease.commissions.add(clarify["id"])
            clarify_qid = _question_id(executive.ledger.get(clarify["id"]))
            answered = client.post("/internal/durable/answer", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "commission_id": clarify["id"], "blocked_question_id": clarify_qid,
                "answer": "放到 out 目录",
            })
            check("澄清可以直接回答", answered.status_code == 200, str(answered.status_code))
            again = client.post("/internal/durable/answer", headers=headers, json={
                "lease_id": auto["lease_id"], "session_id": "s_auto",
                "commission_id": clarify["id"], "blocked_question_id": clarify_qid,
                "answer": "放到 out 目录",
            })
            check("同一问题同一答案幂等", again.status_code == 200 and again.json().get("duplicate") is True,
                  str(again.json())[:300])

        orphan = executive.open("无将可派", "没有 Worker 也得留请示")
        _fail_dispatch(executive, orphan["id"], "当前没有可用的 Worker。要我再试一次吗？")
        orphan_state = executive.ledger.get(orphan["id"])
        check("无 Worker 不会记成 done", orphan_state["status"] == "blocked",
              str(orphan_state.get("status")))
        check("无 Worker 留下 clarification",
              orphan_state.get("blocked_kind") == "clarification" and
              bool(orphan_state.get("blocked_question")), str(orphan_state)[:300])

        # Host crash after creating a Commission but before driving it: a new
        # process must adopt/recover it, not leave an invisible orphan.
        orphan_dir = tempfile.mkdtemp(prefix="durable-orphan-")
        before = ExecutiveService(orphan_dir, roster=ROSTER)
        orphan = before.open("崩溃孤儿", "创建后宿主死了")
        before.dispatcher.dispatch(orphan["id"], agent_id="scribe")
        before.dispatcher.start(orphan["id"])
        restarted_exec = ExecutiveService(orphan_dir, roster=ROSTER)
        recovered = restarted_exec.recover_interrupted()
        recovered_item = restarted_exec.ledger.get(orphan["id"])
        check("重启收口 C2 孤儿单", orphan["id"] in recovered["blocked"] and
              recovered_item["status"] == "blocked", str(recovered)[:300])
        check("孤儿单带请示", bool(recovered_item.get("blocked_question")),
              str(recovered_item)[:300])

        # Hourly window is a pure lease-store rule.
        class Ledger:
            def get(self, _):
                return {"status": "done"}
        store = LeaseStore(Ledger(), clock=lambda: 1000.0)
        issued = store.issue("s_hour", "整理", "auto")
        lease = store.get(issued["lease_id"])
        for index in range(3):
            lease.dispatches.append(1000.0)
        try:
            store.validate(lease.lease_id, "s_hour", for_write=True)
            hourly_ok = False
        except LeaseError as exc:
            hourly_ok = exc.code == "lease_hour_limit"
        check("每小时 3 单上限", hourly_ok)
    finally:
        set_host_token(None)

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
