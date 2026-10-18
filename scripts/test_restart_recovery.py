"""重启收口自检：服务重启后，半路的单必须重新有出路，没派出去的要重新派。

    python scripts/test_restart_recovery.py

验两条：

1. briefing / running / auditing 只有活着的进程能推进。重启后没人持有它们，
   而且它们算在 Worker 的并发槽里，攒够几单就让选将返回 no_worker_available。
   收口把它们转成 BLOCKED 并挂一句请示，老板答一句就能重新推进。
2. drafting 是"接了单还没派出去"，一点活没干。重派一次就是接着做；没将可派
   就转成满载请示，而不是永远躺着。

确定性、零网络、零真实模型调用，数据库一律在临时目录里。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

from server.assistant.dispatcher import in_flight_load  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import (NO_WORKER_QUESTION, RESTART_QUESTION,  # noqa: E402
                                        ExecutiveService)

passed, failed = 0, []

WORKER = {
    "id": "w1", "name": "笔录官", "adapter": "manual", "strengths": ["整理"],
    "weaknesses": [], "cost_tier": "cheap", "max_concurrent": 99,
    "eval_commands": ["required_fields"],
}


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def build(directory, *, max_concurrent: int = 99) -> ExecutiveService:
    base = load_roster()
    base["agents"] = [dict(WORKER, max_concurrent=max_concurrent)]
    return ExecutiveService(Path(directory), roster=base)


def seed_every_state(svc: ExecutiveService) -> dict[str, str]:
    """每种状态各放一单，好逐个核对收口只碰该碰的那几个。"""
    ids: dict[str, str] = {}

    ids["drafting"] = svc.open("草稿单", "还没派")["id"]

    ids["briefing"] = svc.open("组密件单", "派单到一半")["id"]
    svc.dispatch(ids["briefing"], intent="整理")

    ids["running"] = svc.open("在跑单", "跑到一半")["id"]
    svc.dispatch(ids["running"], intent="整理")
    svc.start(ids["running"])

    ids["auditing"] = svc.open("初审单", "初审到一半")["id"]
    svc.dispatch(ids["auditing"], intent="整理")
    svc.start(ids["auditing"])
    svc.ledger.transition(ids["auditing"], "auditing", audit={"passed": False, "layer": "l1"})

    ids["ready"] = svc.open("待验收单", "审完了")["id"]
    svc.dispatch(ids["ready"], intent="整理")
    svc.start(ids["ready"])
    svc.ledger.transition(ids["ready"], "auditing", audit={"passed": True, "layer": "none"})
    svc.ledger.transition(ids["ready"], "ready_to_report",
                          delivery={"summary": "s", "intercepted": "", "artifacts": []})

    ids["blocked"] = svc.open("等着答单", "卡住了")["id"]
    svc.dispatch(ids["blocked"], intent="整理")
    svc.ledger.transition(ids["blocked"], "blocked", blocked_question="选哪版？")

    ids["done"] = svc.open("收工单", "完了")["id"]
    svc.dispatch(ids["done"], intent="整理")
    svc.start(ids["done"])
    svc.ledger.transition(ids["done"], "done")
    return ids


def test_restart_recovery() -> None:
    print("[restart recovery]")
    directory = tempfile.mkdtemp(prefix="restart-recovery-")

    first = build(directory)
    ids = seed_every_state(first)
    for name, commission_id in ids.items():
        print(f"  setup {name:9s} -> {first.ledger.get(commission_id)['status']}")
    first.close()

    # 新进程：重新打开同一个目录，模拟服务重启
    second = build(directory)
    recovered = second.recover_interrupted()

    check("只收口半路的那三单",
          set(recovered["blocked"]) == {ids["briefing"], ids["running"], ids["auditing"]},
          f"实际 {sorted(recovered['blocked'])}")
    for name in ("briefing", "running", "auditing"):
        status = second.ledger.get(ids[name])["status"]
        check(f"{name} 收口到 blocked", status == "blocked", f"实际 {status}")
        check(f"{name} 挂了请示",
              second.ledger.get(ids[name])["blocked_question"] == RESTART_QUESTION)

    resumed = second.ledger.get(ids["drafting"])
    check("drafting 被重新派出", recovered["resumed"] == [ids["drafting"]],
          f"实际 {recovered['resumed']}")
    check("drafting 派到了 running", resumed["status"] == "running", f"实际 {resumed['status']}")
    check("drafting 分到了将", resumed["agent_id"] == "w1", f"实际 {resumed['agent_id']}")

    check("blocked 不动", second.ledger.get(ids["blocked"])["status"] == "blocked")
    check("blocked 原有请示不被覆盖",
          second.ledger.get(ids["blocked"])["blocked_question"] == "选哪版？")
    check("ready_to_report 不动", second.ledger.get(ids["ready"])["status"] == "ready_to_report")
    check("done 不动", second.ledger.get(ids["done"])["status"] == "done")

    # 停车场不算负载：四单 blocked，只有被重派的 drafting 还占着 w1
    load = in_flight_load(second.open_commissions())
    check("blocked 不占并发槽", load == {"w1": 1}, f"实际 {load}")

    check("重复收口是幂等的", second.recover_interrupted() == {"blocked": [], "resumed": []})

    # 收口的目的是"重新有出路"：老板答一句，那句重启请示就该被消费掉。
    # 答完会继续驱动执行体（manual 适配器如实报"没有配置执行体"），所以这里
    # 不断言终态，只断言这单已经离开了"重启收口"这个状态。
    asyncio.run(second.answer(ids["running"], "重跑"))
    question_after = second.ledger.get(ids["running"])["blocked_question"]
    check("收口后能答一句重新推进", question_after != RESTART_QUESTION,
          f"实际 {question_after[:40]}")
    second.close()


def test_drafting_without_worker() -> None:
    print("[drafting, no free worker]")
    directory = tempfile.mkdtemp(prefix="restart-no-worker-")

    # 两个都在 drafting：重派第一个会占满唯一的名额，第二个就撞上满载。
    first = build(directory, max_concurrent=1)
    first.open("草稿甲", "先派的那个")
    first.open("草稿乙", "后派的那个")
    first.close()

    second = build(directory, max_concurrent=1)
    recovered = second.recover_interrupted()

    check("只派出去一单", len(recovered["resumed"]) == 1, f"实际 {recovered}")
    check("另一单转成请示", len(recovered["blocked"]) == 1, f"实际 {recovered}")
    item = second.ledger.get(recovered["blocked"][0])
    check("停在 blocked", item["status"] == "blocked", f"实际 {item['status']}")
    check("请示是满载那句", item["blocked_question"] == NO_WORKER_QUESTION)
    second.close()


def main() -> int:
    test_restart_recovery()
    test_drafting_without_worker()
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
