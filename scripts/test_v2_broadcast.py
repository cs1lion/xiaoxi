# -*- coding: utf-8 -*-
"""W1（issue #25）验收测试：§25 事件协议 v2 的线上广播层。

票面三条验收对应的测试块：

A. 信封语义：连接级 seq 单调、同一逻辑事件跨连接共享 id、未知/禁用事件名不炸。
B. 真实事件流：T2 沙盒工跑一单全链，台账订阅 diff 出六类委托事件（task 生命周期）。
C. 五命令校验表：validate_command 的合法/非法逐条。
D. 命令动作：V2CommandActions 的幂等重放、显式错误回执、取消真链路。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402
from server.assistant.dispatcher import roster_agents  # noqa: E402
from server.contract import events_v2 as v2  # noqa: E402
from server.broadcast import (CommissionEventBridge, RequestLedger,  # noqa: E402
                                       V2BroadcastHub, V2CommandActions,
                                       validate_command)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def _sink(bucket: list[dict]):
    def sink(envelope: dict) -> bool:
        bucket.append(envelope)
        return True
    return sink


def _sandbox_service() -> ExecutiveService:
    """与 T2 验收同一手法：roster 只留 sandbox，executable 换当前解释器、
    工作目录指临时根，测试不依赖 PATH 与 cwd。"""
    roster = load_roster()
    entry = next(dict(item) for item in roster_agents(roster)
                 if item.get("id") == "sandbox")
    entry["executable"] = sys.executable
    entry["args"] = [str(ROOT / "scripts" / "worker_shims" / "sandbox_worker.py")]
    entry["env"] = {**(entry.get("env") or {}),
                    "AQI_WORKSPACE_ROOT": tempfile.mkdtemp(prefix="w1-ws-")}
    roster["agents"] = [entry]
    return ExecutiveService(Path(tempfile.mkdtemp(prefix="w1-exec-")), roster=roster)


def test_hub_envelope_semantics() -> None:
    print("[A hub envelope semantics]")
    hub = V2BroadcastHub()
    a: list[dict] = []
    b: list[dict] = []
    hub.attach("a", _sink(a))
    hub.attach("b", _sink(b))
    for to in ("drafting", "briefing", "running"):
        hub.publish(v2.TASK_STATE_CHANGE, {"from": "", "to": to, "attempt": 0},
                    task_id="t1")
    hub.publish(v2.AGENT_PRESENCE, {"state": "idle"}, session_id="s1")
    check("每连接 seq 从 1 起严格单调", [e["seq"] for e in a] == [1, 2, 3, 4]
          and [e["seq"] for e in b] == [1, 2, 3, 4],
          f"a={[e['seq'] for e in a]} b={[e['seq'] for e in b]}")
    check("同一逻辑事件跨连接共享 id",
          [e["id"] for e in a] == [e["id"] for e in b])
    check("信封带 v/id/ts/type 与 payload",
          all(set(("v", "id", "ts", "type", "payload")) <= set(e) for e in a + b)
          and a[0]["v"] == v2.ENVELOPE_VERSION)
    check("task 作用域字段只在有事时出现",
          a[0].get("task_id") == "t1" and "task_id" not in a[3]
          and a[3].get("session_id") == "s1")
    late: list[dict] = []
    hub.attach("late", _sink(late))
    hub.publish(v2.AGENT_PRESENCE, {"state": "listening"}, session_id="s1")
    check("新连接独立起算 seq（连接级不是全局）",
          late and late[0]["seq"] == 1 and a[-1]["seq"] == 5,
          f"late={late[-1]['seq'] if late else '-'} a={a[-1]['seq']}")
    hub.attach("bad", lambda env: (_ for _ in ()).throw(RuntimeError("sink down")))
    before = len(a)
    hub.publish(v2.AGENT_PRESENCE, {"state": "thinking"}, session_id="s1")
    check("坏 sink 不连累其它连接", len(a) == before + 1)
    count = len(a)
    hub.publish("TASK_SNAPSHOT", {"x": 1})
    hub.publish("NOT_AN_EVENT", {})
    check("未知/禁用事件名不炸也不发", len(a) == count)


def test_bridge_real_chain() -> None:
    print("[B bridge on real sandbox chain]")
    service = _sandbox_service()
    hub = V2BroadcastHub()
    bridge = CommissionEventBridge(hub)
    got: list[dict] = []
    hub.attach("ui", _sink(got))
    service.ledger.subscribe(bridge.on_commission)
    result = asyncio.run(service.delegate(
        "沙盒打样：受控生成并校验一个小文件",
        acceptance=[{"kind": "artifact_json", "name": "verify_report.json",
                     "expect": {"status": "verified"}},
                    {"kind": "artifact_max_bytes", "name": "verify_report.json",
                     "bytes": 4096}]))
    cid = result["commission_id"]
    service.accept(cid)
    types = [e["type"] for e in got]
    check("真子进程全链走通到 done",
          result["status"] in ("ready_to_report", "done")
          and service.commission(cid)["status"] == "done",
          f"{result['status']}")
    tos = [e["payload"]["to"] for e in got if e["type"] == v2.TASK_STATE_CHANGE]
    chain = ["drafting", "briefing", "running", "auditing", "ready_to_report", "done"]
    it = iter(chain)
    ordered = all(status in it for status in tos)
    check("TASK_STATE_CHANGE 覆盖任务生命周期（有序子序列）",
          ordered and all(s in tos for s in chain), str(tos))
    contracts = [e for e in got if e["type"] == v2.CONTRACT_PROPOSED]
    check("CONTRACT_PROPOSED 在 briefing 发出且对得上单号",
          contracts and all(e["payload"].get("contract_id") == cid
                            for e in contracts) and contracts[0]["payload"]["version"] == 1,
          str([e["payload"] for e in contracts][:1]))
    check("契约载荷不编造 risk（缺就留空）",
          all("risk" not in e["payload"] or not e["payload"]["risk"]
              for e in contracts))
    reports = [e for e in got if e["type"] == v2.VERIFICATION_REPORT]
    check("VERIFICATION_REPORT 初审落判且判据来自 eval_commands",
          reports and reports[0]["payload"]["passed"] is True
          and bool(reports[0]["payload"]["criteria"]),
          str(reports[:1]))
    deliveries = [e for e in got if e["type"] == v2.DELIVERY_EFFECT]
    check("DELIVERY_EFFECT 交付入库带幂等键",
          deliveries and deliveries[0]["payload"]["idempotency_key"] == f"{cid}:archive",
          str(deliveries[:1]))
    check("全程无阻塞故无 ASK_USER", v2.ASK_USER not in types)
    task_scoped = [e for e in got if e["type"] != v2.AGENT_PRESENCE]
    check("task 作用域事件都带正确 task_id",
          all(e.get("task_id") == cid for e in task_scoped))
    seqs = [e["seq"] for e in got]
    check("整条链上 seq 连接级单调", seqs == list(range(1, len(seqs) + 1)), str(seqs))


def test_validate_command_table() -> None:
    print("[C validate_command table]")
    good_confirm = {"type": "CONFIRM_CONTRACT", "task_id": "t", "contract_id": "t",
                    "version": 1, "confirmed_slots": ["goal"], "request_id": "r"}
    check("合法 CONFIRM_CONTRACT 过检", validate_command(good_confirm) == "")
    check("缺必填字段被拒",
          validate_command({**good_confirm, "version": None}) != "")
    check("version 非整数被拒",
          validate_command({**good_confirm, "version": True}) != ""
          and validate_command({**good_confirm, "version": "1"}) != "")
    check("confirmed_slots 非字符串数组被拒",
          validate_command({**good_confirm, "confirmed_slots": [1]}) != ""
          and validate_command({**good_confirm, "confirmed_slots": "goal"}) != "")
    good_answer = {"type": "ANSWER", "task_id": "t", "answer": "选 A",
                   "request_id": "r"}
    check("合法 ANSWER 过检；answer 非字符串被拒",
          validate_command(good_answer) == ""
          and validate_command({**good_answer, "answer": 3}) != "")
    good_cancel = {"type": "CANCEL_TASK", "task_id": "t", "request_id": "r"}
    check("CANCEL reason 闭集外被拒",
          validate_command({**good_cancel, "reason": "because"}) != ""
          and validate_command({**good_cancel, "reason": "wrong_task"}) == "")
    check("APPROVE 缺 effect_id 被拒；非字符串被拒",
          validate_command({"type": "APPROVE_SIDE_EFFECT", "task_id": "t",
                            "request_id": "r"}) != ""
          and validate_command({"type": "APPROVE_SIDE_EFFECT", "task_id": "t",
                                "effect_id": 7, "request_id": "r"}) != "")
    check("RETRY 缺 step_id 被拒",
          validate_command({"type": "RETRY_STEP", "task_id": "t",
                            "request_id": "r"}) != "")
    check("未知命令名被拒", validate_command({"type": "SUMMON"}) != "")
    check("option_index 布尔混入整数被拒",
          validate_command({**good_answer, "option_index": True}) != ""
          and validate_command({**good_answer, "option_index": 0}) == "")


async def _drive_command_actions() -> None:
    print("[D command actions]")
    service = _sandbox_service()
    actions = V2CommandActions(service)
    replies: list[tuple[str, str]] = []

    async def reply(code: str, message: str) -> None:
        replies.append((code, message))

    await actions.handle({"type": "CONFIRM_CONTRACT"}, reply)
    check("非法命令有显式回执不静默",
          replies and replies[0][0] == "v2_command_invalid", str(replies))

    opened = service.open("W1 取消打样", "给 CANCEL_TASK 用的一单")
    cid = opened["id"]
    cancel = {"type": "CANCEL_TASK", "task_id": cid, "request_id": "req-cancel-1",
              "reason": "wrong_task"}
    replies.clear()
    await actions.handle(cancel, reply)
    cancelled = service.commission(cid)
    check("合法取消真生效（done + audit.cancelled + 原因入档）",
          cancelled["status"] == "done"
          and (cancelled.get("audit") or {}).get("cancelled") is True
          and (cancelled.get("audit") or {}).get("cancel_reason") == "wrong_task"
          and not replies, str(cancelled.get("audit")))
    await actions.handle(dict(cancel), reply)
    check("同 request_id 重放不二次副作用不回包",
          not replies and service.commission(cid)["status"] == "done")

    ledger = RequestLedger(capacity=2)
    check("RequestLedger 首见登记重见拒绝",
          ledger.claim("a") and not ledger.claim("a") and ledger.claim("b"))

    replies.clear()
    await actions.handle({"type": "CANCEL_TASK", "task_id": "no-such-id",
                          "request_id": "req-404"}, reply)
    check("未知单号有错误回执（不静默落空）",
          any(code.startswith("v2_") for code, _ in replies), str(replies))

    replies.clear()
    await actions.handle({"type": "ANSWER", "task_id": cid, "answer": "x",
                          "request_id": "req-ans-1"}, reply)
    check("非阻塞单作答被显式回绝",
          any(code == "v2_command_rejected" and "不在等回答" in msg
              for code, msg in replies), str(replies))

    done_chain = _sandbox_service()
    result = await done_chain.delegate(
        "沙盒打样：给契约版本核对用",
        acceptance=[{"kind": "artifact_json", "name": "verify_report.json",
                     "expect": {"status": "verified"}}])
    done_chain.accept(result["commission_id"])
    done_actions = V2CommandActions(done_chain)
    done_replies: list[tuple[str, str]] = []

    async def done_reply(code: str, message: str) -> None:
        done_replies.append((code, message))

    await done_actions.handle(
        {"type": "CONFIRM_CONTRACT", "task_id": result["commission_id"],
         "contract_id": result["commission_id"], "version": 99,
         "confirmed_slots": ["goal"], "request_id": "req-ver-1"}, done_reply)
    check("契约版本不一致被显式回绝",
          any("版本不一致" in msg for _, msg in done_replies), str(done_replies))
    done_replies.clear()
    await done_actions.handle(
        {"type": "CONFIRM_CONTRACT", "task_id": result["commission_id"],
         "contract_id": "other", "version": 1,
         "confirmed_slots": ["goal"], "request_id": "req-ver-2"}, done_reply)
    check("contract_id 对不上被显式回绝",
          any("contract_id" in msg for _, msg in done_replies), str(done_replies))
    done_replies.clear()
    await done_actions.handle(
        {"type": "CONFIRM_CONTRACT", "task_id": result["commission_id"],
         "contract_id": result["commission_id"], "version": 1,
         "confirmed_slots": ["goal"], "request_id": "req-ver-3"}, done_reply)
    check("已收口的单再确认被回绝",
          any("已收口" in msg for _, msg in done_replies), str(done_replies))


def main() -> int:
    test_hub_envelope_semantics()
    test_bridge_real_chain()
    test_validate_command_table()
    asyncio.run(_drive_command_actions())
    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
