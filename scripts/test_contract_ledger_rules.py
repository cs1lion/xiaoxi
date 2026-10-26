# -*- coding: utf-8 -*-
"""台账验收规则 -> 契约缺口 的桥接测试（离线，无需 API Key）。

    .\.venv\Scripts\python.exe scripts\test_contract_ledger_rules.py

为什么需要它
------------
契约层此前对老板写的验收规则是**瞎的**：`_acceptance_criteria()` 用
`Criterion(text=..., required=...)` 构造，而 `Criterion` 还必填 `id`/`kind`/`check`
-> 每次 TypeError 被 `except Exception: continue` 吞掉 -> 恒返回空。
结果：老板写了三条能核对的规则，密件里仍报「G4 缺验收标准」，
且「有规则」与「没规则」的缺口清单完全相同。

规范没定义两套词表怎么映射（台账 `artifact_*` 六个 kind vs 契约 `CheckSpec`
七个 type），所以**不假装能转换**：只如实记「有规则，但契约词表读不懂」，
逐条点名 kind。缺的是映射，不是标准——这句话必须能在数据里看到。

四条断言
--------
1. 有台账规则时不得再报「缺 acceptance」；改成点名 ledger_rule:<kind>
2. 有规则与没规则的缺口清单必须不同
3. 真没写规则时仍要老实报缺 acceptance（修桥不能把空单说成有单）
4. 被 _clean_acceptance 降级的占位 kind 不算老板写的规则

不接进 run_gates 的理由
----------------------
它依赖契约层与台账两侧的常量表一致（`_LEDGER_RULE_KINDS` vs
`evaluator._ACCEPTANCE_RULES`），这条一致性本测试第 5 组直接锁死。
跑法与 `core` 同族。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.dispatcher import (  # noqa: E402
    Dispatcher, _contract_gaps,
)
from server.assistant.evaluator import _ACCEPTANCE_RULES, load_roster  # noqa: E402
from server.interaction.threads import CommissionStore  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def gap_map(gaps) -> dict[str, list[str]]:
    return {item["check"]: list(item.get("missing") or []) for item in gaps}


# 台账真实词表里的规则（kind 取自 evaluator._ACCEPTANCE_RULES）
LEDGER_RULES = [
    {"kind": "artifact_exists", "name": "report.md"},
    {"kind": "artifact_contains", "name": "report.md", "text": "Q3"},
    {"kind": "artifact_max_bytes", "name": "report.md", "bytes": 5000},
]
COMMISSION = {"id": "c_t", "title": "写一份季度报告", "brief": "整理Q3数据并生成文件"}


def main() -> int:
    print("[1 有规则时不说「缺验收标准」]")
    gaps = gap_map(_contract_gaps(COMMISSION, LEDGER_RULES))
    missing = gaps.get("G4", [])
    check("G4 存在（验收这条边仍要报）", "G4" in gaps, str(sorted(gaps)))
    check("不再谎报缺 acceptance", "acceptance" not in missing, str(missing))
    check("逐条点名台账规则",
          all(f"ledger_rule:{r['kind']}" in missing for r in LEDGER_RULES), str(missing))

    print("[2 有规则 vs 没规则必须不同]")
    none_gaps = gap_map(_contract_gaps(COMMISSION, None))
    check("两份清单不同", gaps.get("G4") != none_gaps.get("G4"),
          f"有={gaps.get('G4')} 无={none_gaps.get('G4')}")
    check("没规则时老实报缺 acceptance", "acceptance" in none_gaps.get("G4", []),
          str(none_gaps.get("G4")))

    print("[3 未知 kind 要区别对待]")
    weird = gap_map(_contract_gaps(COMMISSION, [{"kind": "sentiment_is_good"}]))
    check("未知 kind 标成 unknown_rule",
          any(item.startswith("unknown_rule:") for item in weird.get("G4", [])),
          str(weird.get("G4")))
    check("未知 kind 不冒充台账规则",
          not any(item.startswith("ledger_rule:") for item in weird.get("G4", [])),
          str(weird.get("G4")))

    print("[4 降级占位 kind 不算老板写的规则]")
    # _clean_acceptance 在超长/超量时换成这些 kind，那是我们自己的降级
    degraded = gap_map(_contract_gaps(
        COMMISSION, [{"kind": "acceptance_rule_overflow"}, {"kind": "invalid"}]))
    check("降级占位不报成有规则",
          "acceptance" in degraded.get("G4", []), str(degraded.get("G4")))

    print("[5 词表常量与 evaluator 保持同步]")
    from server.assistant.dispatcher import _LEDGER_RULE_KINDS
    check("dispatcher 的台账词表 == evaluator 的注册表",
          set(_LEDGER_RULE_KINDS) == set(_ACCEPTANCE_RULES),
          f"dispatcher={sorted(_LEDGER_RULE_KINDS)} evaluator={sorted(_ACCEPTANCE_RULES)}")

    print("[6 真链路：dispatch 落库的密件里带着点名结果]")
    agents = load_roster().get("agents") or []
    check("roster 有可用 agent", bool(agents))
    if agents:
        store = CommissionStore(":memory:")
        dispatcher = Dispatcher(commissions=store)
        cid = store.create(COMMISSION["title"], COMMISSION["brief"],
                           agent_id=str(agents[0]["id"]))["id"]
        row, packet = dispatcher.dispatch(cid, acceptance=LEDGER_RULES)
        check("状态进 briefing", row["status"] == "briefing", row["status"])
        check("初审侧仍收到 3 条规则（不许被桥接改动削掉）",
              len(packet.acceptance) == 3, str(len(packet.acceptance)))
        stored = (row.get("packet") or {}).get("contract_gaps") or []
        stored_g4 = [item for item in stored if item.get("check") == "G4"]
        check("落库缺口点名了台账规则",
              bool(stored_g4) and all(
                  any(str(m).startswith("ledger_rule:") for m in item.get("missing") or [])
                  for item in stored_g4), str(stored_g4))
        # 契约层坏掉不能把派单带崩
        cid2 = store.create("脏输入", "", agent_id=str(agents[0]["id"]))["id"]
        try:
            _row2, packet2 = dispatcher.dispatch(cid2, acceptance=[{"no": 1}, "not-a-dict", None])
            check("脏验收规则不抛异常", isinstance(packet2.contract_gaps, tuple))
        except Exception as exc:  # noqa: BLE001
            check(f"脏验收规则不抛异常（实际 {type(exc).__name__}）", False)

    print()
    print("=" * 72)
    if failed:
        print(f"FAILED {len(failed)}/{passed + len(failed)}")
        for item in failed:
            print("  - " + item)
        return 1
    print(f"PASS {passed}/{passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
