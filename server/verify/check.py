"""三种 check 的执行器（规范 §3.1 :175-181）。

**只做三种**，另外四种明确不做、理由写死在这里：

| type | 本模块 | 理由 |
|---|---|---|
| `file_exists_nonempty` | **做** | `path` + `min_bytes`，规范写全了 |
| `syntax_ok` | **做** | `path` + `lang`（python/ts/json/yaml），规范写全了 |
| `csv_assert` | **做** | `path` + `rules`，`csv_rules.RULES` 已有 6 条且已实现 |
| `schema_valid` | **不做** | `schema_ref` 的语义规范**一个字都没写**（全篇只在 :176 的类型定义出现一次）。它可能是文件路径、内注册表名、或 format 的同义词——三个选项三个不同实现，选哪个都是猜 |
| `exit_code_zero` | **不做** | 「只校验形状不执行」`lint.py` 已做；执行属第 8 步之后，有 `[PROTOCOL_REQUEST]` 门 |
| `rubric` | **不做** | 语义 Judge，属第 8 步之后 |
| `ask_user` | **不做** | 人工判定，属第 8 步之后 |

`SKIPPED_CHECKS` 常量把后四种连同原因固定下来，调用方与测试都能读到——
**不是悄悄不实现，是显式标出来**。

路径判定不许自己抄
------------------
`server/contract/lint.py:45-46` 与 `server/task/paths.py:13-14` 已经有**逐字相同**的
两份 `_ABS_PATH` / `_TRAVERSAL`（有意复制，注释里承认）。**再抄一份就是第三份**，
将来规则一改就漏一处、而漏的那处不会报错只会放行。

所以本模块**从 `server.task` import**（纯函数依赖：`paths.py` 只 import `re`，零 IO）。
方向是 `verify → task`，与「task 不 import verify、靠注入」不冲突。

边界
----
- **允许读文件**（三种 check 都要读）：本包是这条链上第一个真读文件的模块。
- **禁止**开 socket、连库、subprocess。这条由 `scripts/test_verify.py` 的 IO 断言机械检查。
"""
from __future__ import annotations

import ast
import csv
import json
from pathlib import Path
from typing import Any, Callable

from ..contract.csv_rules import apply_rule, read_rows_from_bytes
from ..task import path_in_contract_scope
from .result import KIND_DETERMINISTIC, CriterionResult

# 读文件端口：path -> bytes。抛 OSError 表示读不到。
Reader = Callable[[str], bytes]


def default_reader(path: str) -> bytes:
    """默认真读文件。测试可注入假 reader 避免落盘。"""
    return Path(path).read_bytes()


# 明确不做的四种 + 原因（写死在这里，调用方与测试都能读）。
SKIPPED_CHECKS: dict[str, str] = {
    "schema_valid": "schema_ref 语义未定义（规范全篇只在 :176 的类型定义出现一次），"
                    "可能是文件路径 / 内注册表名 / format 同义词，三个选项三个实现，选哪个都是猜",
    "exit_code_zero": "「只校验形状不执行」lint.py 已做；执行属第 8 步之后，有 [PROTOCOL_REQUEST] 门",
    "rubric": "语义 Judge，属第 8 步之后",
    "ask_user": "人工判定，属第 8 步之后",
}

ALL_CHECK_TYPES = (
    "file_exists_nonempty", "schema_valid", "syntax_ok", "exit_code_zero",
    "csv_assert", "rubric", "ask_user",
)
DETERMINISTIC_EXECUTORS = ("file_exists_nonempty", "syntax_ok", "csv_assert")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _missing_file_result(criterion_id: str, path: str, exc: OSError) -> CriterionResult:
    return CriterionResult(
        criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
        evidence=f"读不到文件 {path!r}：{exc.__class__.__name__}: {exc}",
    )


# ---------- 1. file_exists_nonempty ----------


def check_file_exists_nonempty(criterion_id: str, check: Any,
                               reader: Reader) -> CriterionResult:
    """文件存在、非空，且不小于 `min_bytes`（给了的话）。"""
    path = _text(getattr(check, "path", ""))
    if not path:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence="check.path 为空，无从判定",
        )
    try:
        data = reader(path)
    except OSError as exc:
        return _missing_file_result(criterion_id, path, exc)
    size = len(data)
    min_bytes = getattr(check, "min_bytes", None)
    if size == 0:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence=f"{path!r} 存在但为空（0 字节）",
        )
    if isinstance(min_bytes, int) and not isinstance(min_bytes, bool) and size < min_bytes:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence=f"{path!r} 只有 {size} 字节，低于 min_bytes {min_bytes}",
        )
    suffix = f"（≥ min_bytes {min_bytes}）" if isinstance(min_bytes, int) else ""
    return CriterionResult(
        criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=True,
        evidence=f"{path!r} 存在，{size} 字节{suffix}",
    )


# ---------- 2. syntax_ok ----------


def check_syntax_ok(criterion_id: str, check: Any, reader: Reader) -> CriterionResult:
    """按 `lang` 做语法解析：python 用 ast、json 用 json、yaml 用 PyYAML、ts 用 tsc 不可用。

    **ts 返回 `passed=None`**：没有 TypeScript 解析器，引一个会给这个包带来 node 依赖。
    这是缺口，显式标注，不猜。
    """
    path = _text(getattr(check, "path", ""))
    lang = _text(getattr(check, "lang", ""))
    if not path:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence="check.path 为空，无从判定",
        )
    if lang == "ts":
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=None,
            evidence=f"{path!r} 的 ts 语法检查未实现",
            note="ts 语法检查未实现：不引 TypeScript 解析器（会给包带来 node 依赖）",
        )
    try:
        data = reader(path)
    except OSError as exc:
        return _missing_file_result(criterion_id, path, exc)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence=f"{path!r} 不是 UTF-8：{exc}",
        )

    if lang == "python":
        try:
            ast.parse(text, filename=path)
        except SyntaxError as exc:
            return CriterionResult(
                criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
                evidence=f"{path!r} Python 语法错误（第 {exc.lineno} 行）：{exc.msg}",
            )
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=True,
            evidence=f"{path!r} 通过 ast.parse",
        )
    if lang == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return CriterionResult(
                criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
                evidence=f"{path!r} JSON 语法错误：{exc}",
            )
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=True,
            evidence=f"{path!r} 通过 json.loads",
        )
    if lang == "yaml":
        try:
            import yaml
        except ImportError:
            return CriterionResult(
                criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=None,
                evidence=f"{path!r} 的 yaml 检查需要 PyYAML，当前环境没有",
                note="PyYAML 未安装",
            )
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return CriterionResult(
                criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
                evidence=f"{path!r} YAML 语法错误：{exc}",
            )
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=True,
            evidence=f"{path!r} 通过 yaml.safe_load",
        )
    return CriterionResult(
        criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
        evidence=f"lang {lang!r} 不是 python/ts/json/yaml（契约校验应已拦住）",
    )


# ---------- 3. csv_assert ----------


# 需要原表才能判的三条规则（CheckSpec 没有原表路径）
_SOURCE_DEPENDENT_RULES = frozenset({
    "header_equals_source", "row_count_gte_source", "changed_rows_column_is_today",
})


def check_csv_assert(criterion_id: str, check: Any, reader: Reader,
                     source_rows: Any = None) -> CriterionResult:
    """按 `rules` 逐条跑 `contract/csv_rules.py` 的六条纯函数。

    **对比类规则需要原表**，而 `CheckSpec` 只有 `path`、没有原表路径（规范缺口）。
    调用方可以通过 `source_rows` 注入已解析的原表；没注入时这三条让整条结果
    返回 `passed=None` 并标注缺口——不猜、不假装通过。
    """
    path = _text(getattr(check, "path", ""))
    rules = tuple(getattr(check, "rules", ()) or ())
    if not path:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence="check.path 为空，无从判定",
        )
    if not rules:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence="csv_assert 没有声明任何规则",
        )
    try:
        data = reader(path)
    except OSError as exc:
        return _missing_file_result(criterion_id, path, exc)
    try:
        rows = read_rows_from_bytes(data)
    except (UnicodeDecodeError, csv.Error) as exc:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence=f"{path!r} 不是合法 CSV：{exc}",
        )

    passed_parts: list[str] = []
    failed_parts: list[str] = []
    skipped: list[str] = []
    for rule in rules:
        name = _text(getattr(rule, "name", ""))
        args = getattr(rule, "args", {}) or {}
        if name in _SOURCE_DEPENDENT_RULES and source_rows is None:
            skipped.append(f"{name}（缺原表）")
            continue
        result = apply_rule(name, rows, source_rows=source_rows, args=args)
        if result.ok:
            passed_parts.append(f"{name}: {result.detail}" if result.detail else name)
        else:
            failed_parts.append(f"{name}: {result.detail}")

    if failed_parts:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=False,
            evidence=f"{path!r} 有 {len(failed_parts)} 条规则未过：" + "；".join(failed_parts),
        )
    if skipped:
        return CriterionResult(
            criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=None,
            evidence=(f"{path!r} 通过 {len(passed_parts)} 条，"
                      f"另有 {len(skipped)} 条需要原表无法判：" + "；".join(skipped)),
            note="csv_assert 的对比类规则需要原表，但 CheckSpec 没有原表路径",
        )
    return CriterionResult(
        criterion_id=criterion_id, kind=KIND_DETERMINISTIC, passed=True,
        evidence=f"{path!r} {len(passed_parts)} 条规则全过：" + "；".join(passed_parts),
    )
