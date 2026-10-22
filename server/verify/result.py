"""Verifier 的结果类型（规范 §5.2 `criteria[{id, kind, passed, evidence}]`）。

**为什么必须有 evidence**：§5.2 的 `VERIFICATION_REPORT` 载荷里，每条 criterion
都带 `evidence`。而 `server/task/model.py` 的 `StepFacts.criterion_results`
只有 `tuple[bool, ...]`——**投影成布尔就会把 evidence 永久丢掉**，第 8 步发事件时
才发现构造不出来。

所以本包返回 `tuple[CriterionResult, ...]`（含 id / kind / passed / evidence），
**由调用方投影成 bool 元组喂 `TaskFacts`**。投影前必须先读 report。
这条顺序在 `VerificationReport` 的 docstring 里写死。
"""
from __future__ import annotations

from dataclasses import dataclass

# §5.2 的 kind 三值（与 §3.1 Criterion.kind 一致）
KIND_DETERMINISTIC = "deterministic"
KIND_SEMANTIC = "semantic"
KIND_HUMAN = "human"
KINDS = (KIND_DETERMINISTIC, KIND_SEMANTIC, KIND_HUMAN)

# 缺口标注：不是失败，是「这一条我没法判，原因如下」。
NOTE_SCHEMA_REF_UNDEFINED = "schema_ref 的语义规范未定义（§3.1 只给了 string 类型）"
NOTE_TS_NOT_PARSED = "ts 语法检查未实现：不引 TypeScript 解析器（会给包带来 node 依赖）"
NOTE_CMD_NOT_EXECUTED = "exit_code_zero 只校验形状、不执行命令（执行属第 8 步之后，有协议门）"
NOTE_CSV_SOURCE_MISSING = "csv_assert 的对比类规则需要原表，但 CheckSpec 没有原表路径"


@dataclass(frozen=True)
class CriterionResult:
    """一条验收标准的判定结果。

    `passed` 是 **三态** `bool | None`：
    - `True`  判过
    - `False` 判不过
    - `None`  **不归我判**（semantic 交 Judge / human 交人），尚未有结论

    用 `False` 表示「等 Judge」是把「未判定」谎报成「失败」。§4.10 SM-17 要求
    human 标准不能自动判过，同一个道理：也不能自动判不过。

    `evidence` 对**已判定**的结果必须非空（判过的标准要可审计）；
    `None` 的结果 `evidence` 可以为空，由 `note` 说明为什么不归我判。
    """

    criterion_id: str
    kind: str
    passed: bool | None
    evidence: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"CriterionResult.kind 必须是 {KINDS} 之一，实际 {self.kind!r}")
        if not self.criterion_id:
            raise ValueError("CriterionResult 缺少 criterion_id")
        if self.passed is not None and not self.evidence.strip():
            raise ValueError(
                f"criterion {self.criterion_id!r} 已判定（passed={self.passed}）"
                f"但没有 evidence：判过的标准必须可审计"
            )

    @property
    def decided(self) -> bool:
        return self.passed is not None

    def to_mapping(self) -> dict:
        """§5.2 `criteria[]` 的形状：id / kind / passed / evidence。"""
        data = {
            "id": self.criterion_id,
            "kind": self.kind,
            "passed": self.passed,
            "evidence": self.evidence,
        }
        if self.note:
            data["note"] = self.note
        return data


@dataclass(frozen=True)
class VerificationReport:
    """一次验收的完整报告。

    **投影顺序很重要**：先读 `results` 的 evidence，再调 `to_bool_tuple()`。
    反过来就会把 evidence 丢掉——`StepFacts.criterion_results` 只有布尔。
    """

    results: tuple[CriterionResult, ...]

    @property
    def decided(self) -> tuple[CriterionResult, ...]:
        return tuple(r for r in self.results if r.decided)

    @property
    def needs_judge(self) -> tuple[CriterionResult, ...]:
        return tuple(r for r in self.results
                     if r.passed is None and r.kind == KIND_SEMANTIC)

    @property
    def needs_human(self) -> tuple[CriterionResult, ...]:
        return tuple(r for r in self.results
                     if r.passed is None and r.kind == KIND_HUMAN)

    @property
    def all_decided(self) -> bool:
        return not self.needs_judge and not self.needs_human

    @property
    def all_passed(self) -> bool:
        """全部已判定且都通过。有未判定的不算全过。"""
        return self.all_decided and all(r.passed is True for r in self.results)

    def to_bool_tuple(self) -> tuple[bool, ...]:
        """投影给 `StepFacts.criterion_results`。

        `passed is None`（等 Judge / 等人工）投影成 `False`——未判定不算通过。
        报告里保留 note 说明原因，所以调用方应先读 report 再投影。
        """
        return tuple(r.passed is True for r in self.results)

    def to_mapping(self) -> dict:
        """§5.2 `VERIFICATION_REPORT` 的载荷形状。"""
        return {
            "passed": self.all_passed,
            "criteria": [r.to_mapping() for r in self.results],
        }
