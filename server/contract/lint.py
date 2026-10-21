"""契约 v2 · §3.7 验收标准的编写规范与 lint。

七条规则（逐字对应规范）：

| 编号 | 规则 |
|---|---|
| L1 | 每条标准必须有 kind，并且 check 的 type 与 kind 匹配 |
| L2 | deterministic 必须带可执行的 check；其中所有路径和命令必须能在契约范围内解析 |
| L3 | semantic 必须带 rubric，至少 1 条，每条是可判真假的陈述，并声明需要引用的证据；「内容准确」「质量高」这类无证据陈述一律拒绝 |
| L4 | 至少 1 条 required 的 deterministic 标准；纯语义任务（如写作）例外，但必须有至少 1 条 human 标准 |
| L5 | 标准不得引用 Worker 的自述，如「Worker 报告已完成」 |
| L6 | required 标准不超过 8 条，避免验收成本失控 |
| L7 | semantic 标准的数量不得超过 deterministic 的两倍，防止验收退化成全靠模型感觉 |

L2 里「可执行」与「能在契约范围内解析」分两半：

- **「可在契约范围内解析」= 纯字符串判定，属于这里。** 路径必须是工作区相对路径
  （不是 `../` 上跳、不是绝对路径、不带盘符），命令不能是明显的破坏性外部命令，
  `cwd` 同样不能越界。这些**不需要执行**就能判，早期实现漏掉了，导致
  `path="../../etc/passwd"` 与 `cmd="rm -rf /"` 都能过 lint。
- **「可执行」= 真的跑一遍，不属于这里。** 那要副作用与沙箱，属 §4.9 交付与
  Verifier，冻结（见 docs/CONTRACT_V2_SCOPE.md §二.4）。
"""
from __future__ import annotations

import re

from .model import DETERMINISTIC_CHECKS, HUMAN_CHECKS, SEMANTIC_CHECKS, Criterion

MAX_REQUIRED_CRITERIA = 8

# L3：无证据陈述。规范点名的是「内容准确」「质量高」两类，说「这类一律拒绝」。
# 早期把「合理/合适/优秀/清晰/完整」也做成子串扫描，误杀了
# 「每个结论都引用了数据来源，表述清晰」与「报告完整覆盖契约里列出的 5 个竞品」——
# 两句都带证据和数字，正是规范「好」示例的形态。
# 现在改成**整句匹配**：整条 rubric 就是一句空话才拒，不做子串扫描。
VAGUE_CLAIMS = (
    "内容准确", "质量高", "质量好", "效果好", "看起来不错", "看着不错",
    "内容准确无误", "质量过关", "质量不错", "写得好", "做得不错",
)

# L2：路径必须在契约范围内。
# 规范说「所有路径和命令必须能在契约范围内解析」——「契约范围内」= 工作区相对。
# 绝对路径、盘符、../ 上跳都在范围外，纯字符串即可判定。
_ABS_PATH = re.compile(r'^(?:[A-Za-z]:[\\/]|[\\/]{1,2}[^\\/])')
_TRAVERSAL = re.compile(r'(?:^|[\\/])\.\.(?:[\\/]|$)')

# L2：明显越界的命令。不是完备黑名单——真正的命令白名单属于 §7 权限，冻结。
# 这里只拦「一看就知道不该出现在验收标准里」的那几类。
_DESTRUCTIVE_CMD = re.compile(
    r'\brm\s+-[a-zA-Z]*[rf]|\bdel\s+/[sq]|\brmdir\s+/s|\bformat\s+[a-zA-Z]:'
    r'|\bmkfs\b|\bdd\s+if=|\bshutdown\b|\breboot\b|\bcurl\b.*\|\s*(?:sh|bash)'
    r'|:()\s*\{|\bchmod\s+-R\s+777\s+/', re.I)

# L5：不得引用 Worker 自述。
SELF_REPORT_MARKERS = (
    "worker 报告", "worker报告", "worker 自述", "worker自述",
    "worker 声称", "worker声称", "worker 说", "worker说",
    "执行者报告", "执行者自述", "执行者声称",
    "agent 报告", "agent报告", "agent 自述", "agent 声称",
    "已完成（据", "据 worker",
)

# 「可判真假的陈述」的最低字符数。规范没给形式化判据，这里只拦 1 个字的空条目
# （如「好」「行」），不当成语义判据——真正的语义判定属 §4 的 Judge，冻结。
MIN_RUBRIC_CHARS = 2


class LintProblem:
    """一条 lint 问题。`rule` 是 L1..L7，方便按规则统计与测试。"""

    __slots__ = ("rule", "criterion_id", "message")

    def __init__(self, rule: str, criterion_id: str, message: str) -> None:
        self.rule = rule
        self.criterion_id = criterion_id
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"LintProblem({self.rule}, {self.criterion_id}, {self.message!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LintProblem):
            return NotImplemented
        return (self.rule, self.criterion_id, self.message) == (
            other.rule, other.criterion_id, other.message)

    def __str__(self) -> str:
        return f"[{self.rule}] {self.criterion_id}: {self.message}"


class LintResult:
    """lint 结果。`ok` 只在没有任何问题时为真。"""

    __slots__ = ("problems",)

    def __init__(self, problems: list[LintProblem]) -> None:
        self.problems = tuple(problems)

    @property
    def ok(self) -> bool:
        return not self.problems

    def rules(self) -> set[str]:
        return {problem.rule for problem in self.problems}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"LintResult(ok={self.ok}, problems={len(self.problems)})"


def _criterion_label(criterion: Criterion) -> str:
    return criterion.id or "<no-id>"


def _check_l1(criterion: Criterion, problems: list[LintProblem]) -> None:
    kind = criterion.kind
    check_type = criterion.check.type
    expected = {
        "deterministic": DETERMINISTIC_CHECKS,
        "semantic": SEMANTIC_CHECKS,
        "human": HUMAN_CHECKS,
    }.get(kind)
    if expected is None:
        problems.append(LintProblem("L1", _criterion_label(criterion),
                                    f"未知 kind {kind!r}"))
        return
    if check_type not in expected:
        problems.append(LintProblem(
            "L1", _criterion_label(criterion),
            f"kind={kind} 应当配 {expected} 中的 check.type，实际 {check_type!r}"))


def _path_in_contract_scope(path: str) -> str:
    """返回空串表示在范围内，否则返回原因。

    「契约范围内」= 工作区相对路径。规范没给形式化定义，但 §3.1 的
    `ArtifactSpec.location` 写的是「工作区内的相对路径」，据此推出：
    绝对路径、盘符、`..` 上跳都在范围外。
    """
    text = (path or "").strip()
    if not text:
        return ""
    if _ABS_PATH.match(text):
        return f"绝对路径不在契约范围内：{text!r}"
    if _TRAVERSAL.search(text):
        return f"路径含 .. 上跳，可能在契约范围外：{text!r}"
    return ""


def _check_l2(criterion: Criterion, problems: list[LintProblem]) -> None:
    if criterion.kind != "deterministic":
        return
    check = criterion.check
    label = _criterion_label(criterion)
    if check.type == "file_exists_nonempty":
        if not check.path:
            problems.append(LintProblem("L2", label, "file_exists_nonempty 缺 path"))
        else:
            out_of_scope = _path_in_contract_scope(check.path)
            if out_of_scope:
                problems.append(LintProblem("L2", label, out_of_scope))
    elif check.type == "schema_valid":
        if not check.path:
            problems.append(LintProblem("L2", label, "schema_valid 缺 path"))
        else:
            out_of_scope = _path_in_contract_scope(check.path)
            if out_of_scope:
                problems.append(LintProblem("L2", label, out_of_scope))
        if not check.schema_ref:
            problems.append(LintProblem("L2", label, "schema_valid 缺 schema_ref"))
    elif check.type == "syntax_ok":
        if not check.path:
            problems.append(LintProblem("L2", label, "syntax_ok 缺 path"))
        else:
            out_of_scope = _path_in_contract_scope(check.path)
            if out_of_scope:
                problems.append(LintProblem("L2", label, out_of_scope))
    elif check.type == "exit_code_zero":
        # 「可在契约范围内解析」这一半：形状 + 越界，纯字符串判定。
        # 「可执行」那一半要真跑，属 §4.9，冻结。
        if not check.cmd:
            problems.append(LintProblem("L2", label, "exit_code_zero 缺 cmd"))
        elif _DESTRUCTIVE_CMD.search(check.cmd):
            problems.append(LintProblem(
                "L2", label, f"命令有破坏性，不应出现在验收标准里：{check.cmd[:60]!r}"))
        if not check.cwd:
            problems.append(LintProblem("L2", label, "exit_code_zero 缺 cwd"))
        else:
            out_of_scope = _path_in_contract_scope(check.cwd)
            if out_of_scope:
                problems.append(LintProblem("L2", label, f"cwd {out_of_scope}"))
        if check.timeout_s is None or check.timeout_s <= 0:
            problems.append(LintProblem("L2", label, "exit_code_zero 的 timeout_s 必须为正"))
    elif check.type == "csv_assert":
        if not check.path:
            problems.append(LintProblem("L2", label, "csv_assert 缺 path"))
        else:
            out_of_scope = _path_in_contract_scope(check.path)
            if out_of_scope:
                problems.append(LintProblem("L2", label, out_of_scope))
        if not check.rules:
            problems.append(LintProblem("L2", label, "csv_assert 至少要 1 条规则"))
        else:
            # 规则名必须可解析——§3.7 L2 的「可解析」直接适用，而 csv_rules.RULES
            # 就在同一个包里，接上零成本。早期漏了这条，任意规则名都能过 lint。
            from .csv_rules import RULES

            for index, rule in enumerate(check.rules):
                if rule.name not in RULES:
                    problems.append(LintProblem(
                        "L2", label,
                        f"rules[{index}] 的 {rule.name!r} 不是已知规则，可用：{sorted(RULES)}"))


def _check_l3(criterion: Criterion, problems: list[LintProblem]) -> None:
    if criterion.kind != "semantic":
        return
    label = _criterion_label(criterion)
    rubric = criterion.check.rubric
    if not rubric:
        problems.append(LintProblem("L3", label, "semantic 必须带 rubric，至少 1 条"))
        return
    if criterion.check.evidence_required is not True:
        problems.append(LintProblem("L3", label, "semantic 必须声明 evidence_required"))
    for index, entry in enumerate(rubric):
        text = entry.strip()
        if len(text) < MIN_RUBRIC_CHARS:
            problems.append(LintProblem(
                "L3", label, f"rubric[{index}] 过短，不是可判真假的陈述: {text!r}"))
            continue
        # 整句就是空话才拒；句子里有其它内容（尤其是带数字/引用/核对对象）就放行。
        bare = text.strip("。．.！!？? 　")
        if bare in VAGUE_CLAIMS:
            problems.append(LintProblem(
                "L3", label,
                f"rubric[{index}] 是无证据陈述「{bare}」，须改成可核对的条件"))


def _check_l4(criteria: tuple[Criterion, ...], problems: list[LintProblem]) -> None:
    required = [item for item in criteria if item.required]
    has_required_deterministic = any(
        item.kind == "deterministic" for item in required)
    if has_required_deterministic:
        return
    # 例外：纯语义任务必须有至少 1 条 human 标准。
    has_human = any(item.kind == "human" for item in required)
    if has_human:
        return
    problems.append(LintProblem(
        "L4", "<contract>",
        "至少要有 1 条 required 的 deterministic 标准；纯语义任务则至少 1 条 human 标准"))


def _check_l5(criterion: Criterion, problems: list[LintProblem]) -> None:
    label = _criterion_label(criterion)
    haystack = f"{criterion.text} {' '.join(criterion.check.rubric)}".lower()
    for marker in SELF_REPORT_MARKERS:
        if marker.lower() in haystack:
            problems.append(LintProblem(
                "L5", label, f"不得引用 Worker 自述（命中「{marker}」）"))
            return


def _check_l6(criteria: tuple[Criterion, ...], problems: list[LintProblem]) -> None:
    required = [item for item in criteria if item.required]
    if len(required) > MAX_REQUIRED_CRITERIA:
        problems.append(LintProblem(
            "L6", "<contract>",
            f"required 标准 {len(required)} 条，超过上限 {MAX_REQUIRED_CRITERIA}"))


def _check_l7(criteria: tuple[Criterion, ...], problems: list[LintProblem]) -> None:
    deterministic = sum(1 for item in criteria if item.kind == "deterministic")
    semantic = sum(1 for item in criteria if item.kind == "semantic")
    if semantic > deterministic * 2:
        problems.append(LintProblem(
            "L7", "<contract>",
            f"semantic {semantic} 条 > deterministic {deterministic} 条的两倍"))


def lint_acceptance(criteria: tuple[Criterion, ...] | list[Criterion]) -> LintResult:
    """对验收标准跑 L1–L7。

    注意 L4/L6/L7 是**契约级**规则（看整组），L1/L2/L3/L5 是**标准级**规则。
    单条标准能过前四关，整组仍可能因为 L4/L6/L7 不合格。
    """
    items = tuple(criteria)
    problems: list[LintProblem] = []
    for criterion in items:
        _check_l1(criterion, problems)
        _check_l2(criterion, problems)
        _check_l3(criterion, problems)
        _check_l5(criterion, problems)
    _check_l4(items, problems)
    _check_l6(items, problems)
    _check_l7(items, problems)
    return LintResult(problems)
