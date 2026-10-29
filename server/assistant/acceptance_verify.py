"""把 evaluator 的 acceptance 规则桥接到 server/verify 的确定性 Verifier（M2，issue #61）。

分工
----
evaluator 自有的六条 acceptance 规则全部照跑，本模块对可对应的两种 kind
**追加**一道独立判定：

- ``artifact_exists`` / ``artifact_name`` -> ``file_exists_nonempty``
- ``artifact_json``（带 name）-> ``syntax_ok``（lang=json）

``csv_assert`` 在 evaluator 没有对应规则，不映射；``artifact_contains`` /
``artifact_regex`` / ``artifact_max_bytes`` 留在 evaluator 原实现——不造第二份
真相源。verify 只做三种确定性 check（check.py:62-66），本桥接只用其中两种。

路径从声明取，不从存在取
------------------------
evaluator 的 ``_artifact_files`` 只收 ``Path.is_file()`` 为真的文件
（evaluator.py:184-199），「文件缺失」恰好是 verify 最有价值的场景。所以桥接
自己遍历交付卡的 ``artifacts`` 声明项取 ``ref``，交给 verify 的 reader 去读；
读不到，由 verify 产出「读不到文件」证据，而不是桥接自己下结论。只认
``kind=="file"`` 的声明，口径同 evaluator.py:126——note 一类的非文件 ref 不当
文件读（M2 复审 MAJOR-1 的修法）。

渲染契约（三态 passed）
----------------------
- ``passed=False`` -> blocker finding。rule_id 两种形状：问题型（坏 name、
  无可达 ref）是 ``verify:<check_type>``；判定结果型是
  ``verify:<check_type>[i]``，i 是声明项序号，多文件时区分是哪一件。
  detail 取 evidence。
- ``passed=True``  -> 无声
- ``passed=None``  -> **绝不进 findings**：evaluator.review 的 ``passed = not
  findings``，任何 finding 都判不过；把「未判定」谎报成「不合格」正是 verify
  包三态设计要防的事（result.py:33-40）。note 随 ``render_results`` 的
  notes 列表返回。

当前 evaluator 钩子只消费 findings，notes 没有消费方——这是已知缺口，不是
静默丢：渲染函数的返回值里带着，scripts/test_verify.py 直接断言它。且映射面内
None 不可达：``file_exists_nonempty`` 与 ``syntax_ok(json)`` 不返回 None
（只有 lang=ts 或缺 PyYAML 才 None）。

不猜的两种形状
--------------
- ``artifact_json`` 未指定 name 时，evaluator 自有语义是「至少一个成果是合法
  JSON」，per-criterion 判定表达不了，桥接跳过（evaluator 原实现覆盖这种语义）。
- ``name`` 带路径分隔符或 ``..``：复用 evaluator 的 ``_acceptance_name`` 判定
  拒绝，不在这里抄第三份路径规则。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..contract.model import CheckSpec, Criterion
from ..verify.check import default_reader
from ..verify.result import KIND_DETERMINISTIC, CriterionResult
from ..verify.verifier import verify

from .evaluator import _acceptance_name, _finding

# 桥接覆盖的 kind -> verify 的 check.type。未列出的 kind 留 evaluator 原实现。
_MAPPED_CHECKS = {
    "artifact_exists": "file_exists_nonempty",
    "artifact_name": "file_exists_nonempty",
    "artifact_json": "syntax_ok",
}


def _declared_refs(artifact: dict[str, Any], name: str) -> list[str]:
    """交付卡声明的 ref（不要求文件已存在）。name 为空 = 全部声明项。

    只认 ``kind=="file"`` 的声明：file_exists_nonempty 与 syntax_ok 都是文件
    内容检查，口径对齐 evaluator 的 artifacts_exist（evaluator.py:126 只对
    file 查存在）。note 一类的非文件 ref（如 "note:x@1"）交给 verify 的 reader
    只会读不到文件——那是误打回，不是判定。
    """
    refs: list[str] = []
    for item in artifact.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "file":
            continue
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            continue
        if name and Path(ref).name != name:
            continue
        refs.append(ref.strip())
    return refs


def _criteria_for_rule(rule: dict[str, Any],
                       artifact: dict[str, Any]) -> tuple[list[Criterion], list[dict[str, Any]]]:
    """一条 acceptance 规则 -> verify 的 Criterion 列表；坏输入产 blocker finding。"""
    kind = str(rule.get("kind") or "")
    check_type = _MAPPED_CHECKS[kind]
    name, problem = _acceptance_name(rule)
    if problem:
        return [], [_finding(f"verify:{check_type}", "blocker", problem)]
    if check_type == "syntax_ok" and not name:
        return [], []  # 未指定 name 的 artifact_json：至少一个的语义表达不了，跳过
    refs = _declared_refs(artifact, name)
    if not refs:
        detail = (f"没有名为 {name} 的成果声明" if name
                  else "交付卡没有声明任何可达 ref")
        return [], [_finding(f"verify:{check_type}", "blocker", detail)]
    criteria: list[Criterion] = []
    for index, ref in enumerate(refs):
        if check_type == "syntax_ok":
            check = CheckSpec(type="syntax_ok", path=ref, lang="json")
        else:
            check = CheckSpec(type="file_exists_nonempty", path=ref)
        criteria.append(Criterion(
            id=f"verify:{check_type}[{index}]",
            text=f"{kind} -> {check_type}: {ref}",
            kind=KIND_DETERMINISTIC,
            check=check,
        ))
    return criteria, []


def render_results(results: list[CriterionResult]) -> tuple[list[dict[str, Any]], list[str]]:
    """verify 判定 -> (findings, notes)。False 才进 findings；None 只进 notes。"""
    findings: list[dict[str, Any]] = []
    notes: list[str] = []
    for result in results:
        if result.passed is False:
            findings.append(_finding(result.criterion_id, "blocker", result.evidence))
        elif result.passed is None:
            notes.append(f"{result.criterion_id} 未判定：{result.note or result.evidence}")
    return findings, notes


def verify_findings(rules: Any, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """evaluator._check_acceptance 末尾调用：返回要追加的 findings。

    notes 今天没有消费方（evaluator 的 L1 检查只收 findings）；渲染契约由
    render_results 的返回值与 scripts/test_verify.py 直接断言。

    findings 多时桥接证据排在 evaluator 自有 findings 之后，会先被 review 的
    MAX_FINDINGS 截断砍掉；不影响 passed（有任何 finding 即不过），但最乱的
    时候第二道判定的记录会先消失（M2 复审 MINOR-6，已知取舍）。
    """
    if not isinstance(rules, (list, tuple)):
        return []
    findings: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("kind") not in _MAPPED_CHECKS:
            continue
        criteria, problems = _criteria_for_rule(rule, artifact or {})
        findings.extend(problems)
        if not criteria:
            continue
        report = verify(criteria, reader=default_reader)
        rendered, notes = render_results(list(report.results))
        findings.extend(rendered)
        if notes:
            # 映射面内 None 不可达；真出现时不静默——L1 钩子只收 findings，
            # 没有 notes 通道，先告警留痕（M2 复审 MINOR-3）。
            logging.getLogger(__name__).warning(
                "acceptance verify 有未判定项（不进 findings，不会打回）：%s",
                "；".join(notes))
    return findings
