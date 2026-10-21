"""契约 v2 · §3.7 内置 CsvRule 规则库（M1 最小集）。

规范列出六条规则（原文）：

> 内置 CsvRule 规则库（M1 最小集）：header_equals_source、row_count_gte_source、
> no_empty_cells（可指定列）、column_values_in_set、changed_rows_column_is_today、
> unique_key（可指定列）。规则库可以扩展，但每条规则必须是纯函数、无网络、
> 可在 1 秒内跑完。

纯函数怎么落地
--------------
"纯函数"意味着**不碰文件、不碰时钟、不碰网络**。所以这里的规则都接
"已解析好的行"，而不是路径：

- `rows`：目标 CSV 的行，`list[list[str]]`，第一行是表头
- `source_rows`：原表（对比类规则用）
- `today`：当天的日期字符串（`changed_rows_column_is_today` 用），由调用方给

读文件的活交给调用方（`read_rows()` 只是个便利函数，可被替换）。
这样规则本身可单测、可复现、结果与时钟无关。

**注意：本模块不执行验收。** 契约里的 `csv_assert` 只在 lint 阶段校验形状
（§3.7 L2），真正跑规则属于 §4.9 交付与 Verifier，已冻结
（见 docs/CONTRACT_V2_SCOPE.md §二.4）。这里提供的是**库**，
供将来 Verifier 接上时直接用，且现在就能用它自己的单测证明正确。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Sequence

Rows = Sequence[Sequence[str]]


@dataclass(frozen=True)
class RuleResult:
    """单条规则的判定结果。`detail` 会进验收报告，所以要说清"哪一行哪一列"。"""

    ok: bool
    detail: str = ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize(rows: Rows) -> list[list[str]]:
    """把行规整成 list[list[str]]，容忍短行（补空串）。"""
    out: list[list[str]] = []
    for row in rows or ():
        if row is None:
            out.append([])
            continue
        out.append(["" if cell is None else str(cell) for cell in row])
    return out


def _header(rows: list[list[str]]) -> list[str]:
    return [cell.strip() for cell in rows[0]] if rows else []


def _column_index(header: list[str], column: str) -> int:
    """列名 -> 下标。找不到抛 KeyError（由 apply_rule 转成失败结果）。"""
    target = column.strip()
    for index, name in enumerate(header):
        if name == target:
            return index
    raise KeyError(column)


# ---------- 六条规则 ----------


def header_equals_source(rows: Rows, source_rows: Rows | None, args: dict) -> RuleResult:
    """表头与原表一致。

    规范 §3.6 的 C2 用它：「表头与原表一致，行数不少于原表」。
    """
    if source_rows is None:
        return RuleResult(False, "header_equals_source 需要 source_rows")
    target = _header(_normalize(rows))
    source = _header(_normalize(source_rows))
    if not source:
        return RuleResult(False, "原表没有表头，无法比对")
    if target == source:
        return RuleResult(True, f"表头一致（{len(target)} 列）")
    if len(target) != len(source):
        return RuleResult(
            False, f"列数不一致：目标 {len(target)} 列，原表 {len(source)} 列")
    diff = [f"第{i + 1}列 {source[i]!r}->{target[i]!r}"
            for i in range(len(source)) if target[i] != source[i]]
    return RuleResult(False, "表头不一致：" + "；".join(diff))


def row_count_gte_source(rows: Rows, source_rows: Rows | None,
                         args: dict) -> RuleResult:
    """行数不少于原表（不含表头）。"""
    if source_rows is None:
        return RuleResult(False, "row_count_gte_source 需要 source_rows")
    target_rows = _normalize(rows)
    source_count = max(0, len(_normalize(source_rows)) - 1)
    target_count = max(0, len(target_rows) - 1)
    # 末尾全是空行的不算数据行——很多导出工具留空行。
    while target_count > 0 and not any(
            cell.strip() for cell in target_rows[target_count]):
        target_count -= 1
    if target_count >= source_count:
        return RuleResult(True, f"目标 {target_count} 行 >= 原表 {source_count} 行")
    return RuleResult(False, f"目标 {target_count} 行 < 原表 {source_count} 行")


def no_empty_cells(rows: Rows, source_rows: Rows | None, args: dict) -> RuleResult:
    """指定列不得有空单元格。`args.columns` 省略时检查全部列。"""
    normalized = _normalize(rows)
    if not normalized:
        return RuleResult(False, "目标表为空")
    header = _header(normalized)
    columns = args.get("columns")
    if columns is None:
        indexes = list(range(len(header)))
        names = header
    else:
        if not isinstance(columns, list) or not columns:
            return RuleResult(False, "no_empty_cells 的 columns 必须是非空数组")
        indexes, names = [], []
        for column in columns:
            try:
                indexes.append(_column_index(header, str(column)))
                names.append(str(column).strip())
            except KeyError:
                return RuleResult(False, f"找不到列 {column!r}")
    problems = []
    for row_index in range(1, len(normalized)):
        row = normalized[row_index]
        for position, column_index in enumerate(indexes):
            cell = row[column_index] if column_index < len(row) else ""
            if not cell.strip():
                problems.append(f"第{row_index}行「{names[position]}」为空")
                if len(problems) >= 5:
                    break
        if len(problems) >= 5:
            break
    if problems:
        return RuleResult(False, "；".join(problems))
    return RuleResult(True, f"{len(normalized) - 1} 行 {len(indexes)} 列无空格")


def column_values_in_set(rows: Rows, source_rows: Rows | None,
                         args: dict) -> RuleResult:
    """指定列的值必须落在给定集合内。`args.column` + `args.values`。"""
    column = _text(args.get("column"))
    if not column:
        return RuleResult(False, "column_values_in_set 缺少 column")
    allowed = args.get("values")
    if not isinstance(allowed, list) or not allowed:
        return RuleResult(False, "column_values_in_set 的 values 必须是非空数组")
    allowed_set = {str(item).strip() for item in allowed}
    normalized = _normalize(rows)
    if not normalized:
        return RuleResult(False, "目标表为空")
    try:
        index = _column_index(_header(normalized), column)
    except KeyError:
        return RuleResult(False, f"找不到列 {column!r}")
    bad = []
    for row_index in range(1, len(normalized)):
        row = normalized[row_index]
        cell = (row[index] if index < len(row) else "").strip()
        if not cell:
            continue  # 空单元格由 no_empty_cells 负责，这里不重复报
        if cell not in allowed_set:
            bad.append(f"第{row_index}行 {cell!r}")
            if len(bad) >= 5:
                break
    if bad:
        return RuleResult(False, f"「{column}」出现集合外的值：" + "；".join(bad))
    return RuleResult(True, f"「{column}」全部落在 {len(allowed_set)} 个允许值内")


def changed_rows_column_is_today(rows: Rows, source_rows: Rows | None,
                                 args: dict) -> RuleResult:
    """被改动的行，指定列的值必须是今天。

    `args.column` 指定列；`args.today` 是今天的日期字符串（**必填**，
    不从时钟取，保证纯函数）。行是否"被改动"通过与 `source_rows` 逐列比对判断；
    没有 `source_rows` 时退化为检查该列的**全部非空值**是否为今天。
    """
    column = _text(args.get("column"))
    if not column:
        return RuleResult(False, "changed_rows_column_is_today 缺少 column")
    today = _text(args.get("today"))
    if not today:
        return RuleResult(False, "changed_rows_column_is_today 缺少 today（不从时钟取）")
    normalized = _normalize(rows)
    if not normalized:
        return RuleResult(False, "目标表为空")
    header = _header(normalized)
    try:
        index = _column_index(header, column)
    except KeyError:
        return RuleResult(False, f"找不到列 {column!r}")

    source = _normalize(source_rows) if source_rows is not None else None
    source_header = _header(source) if source else []
    key_column = _text(args.get("key_column"))
    key_index = None
    if key_column and key_column in header:
        key_index = header.index(key_column)

    bad = []
    checked = 0
    for row_index in range(1, len(normalized)):
        row = normalized[row_index]
        value = (row[index] if index < len(row) else "").strip()
        changed = True
        if source:
            changed = _row_changed(header, row, source, source_header,
                                   source_index=row_index - 1, key_index=key_index)
        if not changed:
            continue
        checked += 1
        if value != today:
            bad.append(f"第{row_index}行 {value or '(空)'}")
            if len(bad) >= 5:
                break
    if bad:
        return RuleResult(False, f"改动行「{column}」不是 {today}：" + "；".join(bad))
    if checked == 0:
        return RuleResult(False, f"没有检测到改动行，无法验证「{column}」是否为今天")
    return RuleResult(True, f"{checked} 个改动行的「{column}」都是 {today}")


def _row_changed(header: list[str], row: list[str], source: list[list[str]],
                 source_header: list[str], *, source_index: int,
                 key_index: int | None) -> bool:
    """判断目标行相对原表是否改动。

    有 key 列时按 key 找原行（顺序可能变），否则按同下标对齐。
    """
    if not source:
        return True
    if key_index is not None and key_index < len(row):
        key = row[key_index].strip()
        for candidate in source[1:]:
            if key_index < len(candidate) and candidate[key_index].strip() == key:
                return _cells_differ(header, row, source_header, candidate)
        return True  # 原表里没有这个 key = 新增行 = 改动
    if source_index < 0 or source_index + 1 >= len(source):
        return True
    return _cells_differ(header, row, source_header, source[source_index + 1])


def _cells_differ(header: list[str], row: list[str], source_header: list[str],
                  source_row: list[str]) -> bool:
    for position, name in enumerate(header):
        if name not in source_header:
            continue
        source_position = source_header.index(name)
        left = row[position].strip() if position < len(row) else ""
        right = (source_row[source_position].strip()
                 if source_position < len(source_row) else "")
        if left != right:
            return True
    return False


def unique_key(rows: Rows, source_rows: Rows | None, args: dict) -> RuleResult:
    """指定列的值必须唯一。`args.columns` 单列或数组（组合键）。"""
    columns = args.get("columns")
    if isinstance(columns, str):
        columns = [columns]
    if not isinstance(columns, list) or not columns:
        return RuleResult(False, "unique_key 的 columns 必须是非空数组")
    normalized = _normalize(rows)
    if not normalized:
        return RuleResult(False, "目标表为空")
    header = _header(normalized)
    indexes = []
    for column in columns:
        try:
            indexes.append(_column_index(header, str(column)))
        except KeyError:
            return RuleResult(False, f"找不到列 {column!r}")
    seen: dict[tuple, int] = {}
    duplicates = []
    for row_index in range(1, len(normalized)):
        row = normalized[row_index]
        if not any((row[index] if index < len(row) else "").strip() for index in indexes):
            continue  # 全空的行不参与唯一性（那是 no_empty_cells 的事）
        key = tuple((row[index] if index < len(row) else "").strip() for index in indexes)
        if key in seen:
            duplicates.append(f"「{key}」出现在第{seen[key]}行与第{row_index}行")
            if len(duplicates) >= 5:
                break
        else:
            seen[key] = row_index
    if duplicates:
        return RuleResult(False, "唯一性冲突：" + "；".join(duplicates))
    return RuleResult(True, f"{len(seen)} 个键唯一")


# ---------- 分发 ----------

RULES = {
    "header_equals_source": header_equals_source,
    "row_count_gte_source": row_count_gte_source,
    "no_empty_cells": no_empty_cells,
    "column_values_in_set": column_values_in_set,
    "changed_rows_column_is_today": changed_rows_column_is_today,
    "unique_key": unique_key,
}


def apply_rule(name: str, rows: Rows, *, source_rows: Rows | None = None,
               args: dict | None = None) -> RuleResult:
    """按名字调一条规则。未知规则名直接失败（不静默跳过）。"""
    handler = RULES.get(_text(name))
    if handler is None:
        return RuleResult(False, f"未知规则 {name!r}，可用：{sorted(RULES)}")
    try:
        return handler(rows, source_rows, args if isinstance(args, dict) else {})
    except Exception as exc:  # noqa: BLE001 - 规则库不该把调用方炸掉
        return RuleResult(False, f"规则 {name} 执行异常：{type(exc).__name__}: {exc}")


def read_rows(text: str) -> list[list[str]]:
    """把 CSV 文本解析成行。**便利函数**，不碰文件系统——路径读取是调用方的事，
    这样规则库整体保持"无 IO"（见模块头）。"""
    if not isinstance(text, str) or not text.strip():
        return []
    return [row for row in csv.reader(io.StringIO(text))]


def read_rows_from_bytes(payload: bytes) -> list[list[str]]:
    """同上，接字节。UTF-8 BOM 会被剥掉（Excel 导出的 CSV 常带 BOM）。"""
    if not isinstance(payload, (bytes, bytearray)):
        return []
    return read_rows(bytes(payload).decode("utf-8-sig", errors="replace"))
