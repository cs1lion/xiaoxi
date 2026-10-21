"""契约 v2（规范 §0–§3.7）。

只做「契约能不能成立」的判断：结构、完备度、风险、置信度、验收标准 lint，
以及 §3.7 的内置 CsvRule 规则库。

不做「契约怎么被执行」——状态机、Verifier、事件总线、权限等级、评测集
都在规范的 §4–§9，而那几节在文档里不存在（见 docs/CONTRACT_V2_SCOPE.md）。

边界纪律：本包不 import 任何现有业务模块，不读库、不发网络、不跑命令，
因此可以单独跑测试，也不会把契约层和台账层耦合起来。
"""
from __future__ import annotations

from .confidence import (
    FILL_ASK, FILL_CONFIRM, FILL_SILENT, SilentFillPolicy, resolve_conflicting_memories,
)
from .gate import (
    Draft, GateOutcome, Gap, GateResult, collect_gaps, effective_start_policy, evaluate,
    freeze, gate, highlighted_slots,
)
from .lint import LintProblem, LintResult, lint_acceptance
from .model import (
    ArtifactSpec, Budget, CheckSpec, ContractShapeError, Criterion, CsvRule, InputRef,
    RiskAssessment, SideEffect, Slot, SlotSource, TaskContract, parse_contract,
)
from .risk import compute_risk, confirmation_policy, risk_of_side_effect

__all__ = [
    # model (§3.1)
    "ArtifactSpec", "Budget", "CheckSpec", "ContractShapeError", "Criterion", "CsvRule",
    "InputRef", "RiskAssessment", "SideEffect", "Slot", "SlotSource", "TaskContract",
    "parse_contract",
    # gate (§3.2 / §3.3)
    "Draft", "GateOutcome", "Gap", "GateResult", "collect_gaps",
    "effective_start_policy", "evaluate", "freeze", "gate", "highlighted_slots",
    # risk (§3.4)
    "compute_risk", "confirmation_policy", "risk_of_side_effect",
    # confidence (§3.5)
    "FILL_ASK", "FILL_CONFIRM", "FILL_SILENT", "SilentFillPolicy",
    "resolve_conflicting_memories",
    # lint (§3.7)
    "LintProblem", "LintResult", "lint_acceptance",
]
