"""Verifier 主入口（规范 §9.1：确定性规则 + 语义 Judge 分离）。

职责
----
把一组 `Criterion` 逐条判定，返回 `VerificationReport`。

**只做三种确定性 check**（`file_exists_nonempty` / `syntax_ok` / `csv_assert`），
另外四种明确不做，原因写死在 `check.SKIPPED_CHECKS` 里——**不是悄悄不实现，是显式标出来**。

**不做的**：
- **不做重试**。重试是 TaskMachine 的事（T16/T17 与 `budget.max_retries`）。
  `evaluator.MAX_RETRY = 2` 与规范 `Budget.max_retries = 3` 不是一个数——
  引错常量会让重试次数静默少一次。本包**不定义、不引用任何重试常量**。
- **不跑模型**。`rubric` 属语义 Judge，第 8 步之后。
- **不执行命令**。`exit_code_zero` 的执行属第 8 步之后，有 `[PROTOCOL_REQUEST]` 门。

投影顺序（第 8 步最容易踩的坑）
------------------------------
```
report = verify(criteria)            # 1. 拿到含 evidence 的报告
for r in report.results: ...         # 2. 先读 evidence（要落盘 / 发事件）
bools = report.to_bool_tuple()       # 3. 再投影给 StepFacts.criterion_results
```
`StepFacts.criterion_results` 只有 `tuple[bool, ...]`，**投影会丢 evidence**。
顺序反了，第 8 步发 `VERIFICATION_REPORT` 时才发现构造不出来。

边界
----
- 允许读文件（本包是这条链上第一个真读文件的模块）
- 禁止开 socket、连库、subprocess（由 `scripts/test_verify.py` 的 IO 断言机械检查）
"""
from __future__ import annotations

from typing import Any, Iterable

from .check import (
    DETERMINISTIC_EXECUTORS,
    SKIPPED_CHECKS,
    Reader,
    check_csv_assert,
    check_file_exists_nonempty,
    check_syntax_ok,
    default_reader,
)
from .result import (
    KIND_DETERMINISTIC, KIND_HUMAN, KIND_SEMANTIC,
    CriterionResult, VerificationReport,
)

_EXECUTOR_MAP = {
    "file_exists_nonempty": check_file_exists_nonempty,
    "syntax_ok": check_syntax_ok,
    "csv_assert": check_csv_assert,
}


def _criterion_id(criterion: Any, index: int) -> str:
    """取 criterion 的 id；没有就按序号生成一个并标注（不静默跳过）。"""
    value = getattr(criterion, "id", "") or getattr(criterion, "criterion_id", "")
    return str(value) if value else f"criterion[{index}]"


def _criterion_kind(criterion: Any) -> str:
    kind = str(getattr(criterion, "kind", "") or "")
    if kind in (KIND_DETERMINISTIC, KIND_SEMANTIC, KIND_HUMAN):
        return kind
    return KIND_DETERMINISTIC


def verify(criteria: Iterable[Any], *, reader: Reader | None = None,
           source_rows: Any = None) -> VerificationReport:
    """逐条判定。`criteria` 是 §3.1 的 Criterion（鸭子类型：读 id/kind/check 三个属性）。

    `reader` 默认真读文件；测试可注入假 reader。
    `source_rows` 给 `csv_assert` 的对比类规则用（`CheckSpec` 本身没有原表路径）。
    """
    read = reader or default_reader
    results: list[CriterionResult] = []
    for index, criterion in enumerate(criteria or ()):
        cid = _criterion_id(criterion, index)
        kind = _criterion_kind(criterion)
        check = getattr(criterion, "check", None)
        if check is None:
            results.append(CriterionResult(
                criterion_id=cid, kind=kind, passed=False,
                evidence="criterion 没有 check（lint L1 应已拦住）",
            ))
            continue
        check_type = str(getattr(check, "type", "") or "")
        if check_type in SKIPPED_CHECKS:
            results.append(CriterionResult(
                criterion_id=cid, kind=kind, passed=None,
                evidence=f"{check_type} 未实现：{SKIPPED_CHECKS[check_type]}",
                note=SKIPPED_CHECKS[check_type],
            ))
        elif check_type in _EXECUTOR_MAP:
            executor = _EXECUTOR_MAP[check_type]
            if check_type == "csv_assert":
                results.append(executor(cid, check, read, source_rows=source_rows))
            else:
                results.append(executor(cid, check, read))
        else:
            results.append(CriterionResult(
                criterion_id=cid, kind=kind, passed=False,
                evidence=f"未知的 check.type {check_type!r}（契约校验应已拦住）",
            ))
    return VerificationReport(results=tuple(results))


def verify_one(criterion: Any, *, reader: Reader | None = None,
               source_rows: Any = None) -> CriterionResult:
    """判定单条（便于逐个调试）。"""
    report = verify((criterion,), reader=reader, source_rows=source_rows)
    return report.results[0]


def required_of(criteria: Iterable[Any]) -> tuple[Any, ...]:
    """过滤出 required 的 criterion（`required` 默认 true，§3.1）。"""
    return tuple(c for c in (criteria or ()) if getattr(c, "required", True) is not False)
