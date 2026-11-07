# -*- coding: utf-8 -*-
"""决策 trail 校验：每条决策必须有「证据」和「验证方式」，缺一条即红。

**为什么需要它**：2026-11-06 盘点发现，仓库里 4 份 `docs/DECISION_TRAIL_*.md`
表头互不统一（`20261017:8` 有「证据」列但没有「验证方式」列），唯一符合
pstack 格式的结构化 TSV 在 `tmp/pixel-world-decisions.tsv`（被 gitignore）。
于是「show me your work」只剩散落在 Markdown 里的叙述，机器无法判断某条决策
到底有没有客观验收方式——这正是三阶段流程里最容易被 AI 用漂亮文字糊过去的一环。

**口径**：append-only JSONL，每条记录必填 `evidence` 与 `verification`。
- `evidence` 至少一条带 `file:line` 或 7+ 位 commit hash（防止「AGENTS.md」这种
  无锚引用被当成证据）；**不检查 evidence 里的路径是否存在**——`.agents/` 下的
  pstack bundle 被 gitignore，别人 clone 后本来就没有，检查会让门禁随环境飘。
- `verification` 至少一条是可复跑命令；其中引用的 `scripts/*.py` 必须真实存在
  （防「写一条看起来很专业的假命令」）。
- `status=done` 必须写 `result`；`pending` / `blocked` 允许没有。
- 记录数下限与模板完整性检查：**防真空绿**。空文件、只有一条假记录、或把
  开工模板删了，都必须红，而不是「0 条全通过」。

**只读**：不写 trail、不写模板、不跑 verification 里的命令（只解析字符串）、
不调用 git。支持 `--trail` 指向临时副本，供 `test_check_process_trail.py`
用 `check_probe.probe_self_check` 做真实破坏注入而不碰仓库文件。

Run: .venv/Scripts/python.exe -B scripts/check_process_trail.py
     .venv/Scripts/python.exe -B scripts/check_process_trail.py --trail <path>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAIL = ROOT / "docs" / "process" / "trail.jsonl"
DEFAULT_TEMPLATE = ROOT / "docs" / "prompts" / "TASK_SHARPEN.md"

# 2026-11-06 设的下限，带日期锚：防止「零条记录也算通过」的真空绿。
MIN_ROWS = 4
REQUIRED = ("id", "ts", "phase", "decision", "why", "evidence", "verification", "status")
STATUSES = ("done", "pending", "blocked")
TEMPLATE_KEYS = ("顶尖老手", "用户真正要", "规格过定",
                 "验收 A", "验收 B", "验收 C", "验收 D", "验收 E")

_LINE_ANCHOR = re.compile(r":\d+(?:-\d+)?\b")
_COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
_SCRIPT_REF = re.compile(r"scripts/[A-Za-z0-9_.-]+\.py")
_CMD_VERB = re.compile(r"\b(?:python|python\.exe|npm|git|node)\b")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}")


def validate(trail: Path, template: Path = DEFAULT_TEMPLATE) -> tuple[list[str], int]:
    """返回 (问题列表, 有效记录数)。纯函数，不写任何文件。"""
    problems: list[str] = []
    if not trail.is_file():
        return [f"trail 不存在：{trail}"], 0

    if not template.is_file():
        problems.append(f"开工模板缺失：{template}")
    else:
        text = template.read_text(encoding="utf-8")
        for key in TEMPLATE_KEYS:
            if key not in text:
                problems.append(f"开工模板缺章节/判据：{key}")

    rows: list[tuple[int, dict]] = []
    for n, raw in enumerate(trail.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"第 {n} 行不是合法 JSON：{exc.msg}")
            continue
        if not isinstance(row, dict):
            problems.append(f"第 {n} 行不是 JSON 对象")
            continue
        rows.append((n, row))

    if len(rows) < MIN_ROWS:
        problems.append(
            f"只有 {len(rows)} 条决策，低于 2026-11-06 设的下限 {MIN_ROWS}（防真空绿）")

    seen: set[str] = set()
    for n, row in rows:
        tag = f"第 {n} 行({row.get('id', '?')})"
        for key in REQUIRED:
            value = row.get(key)
            if value is None or (isinstance(value, (str, list)) and not value):
                problems.append(f"{tag} 缺必填字段 {key}")

        rid = str(row.get("id") or "")
        if rid in seen:
            problems.append(f"{tag} id 重复：{rid}")
        seen.add(rid)

        ts = row.get("ts")
        if not isinstance(ts, str) or not _TS.fullmatch(ts):
            problems.append(f"{tag} ts 不是 YYYY-MM-DD：{ts!r}")

        evidence = row.get("evidence")
        if isinstance(evidence, list) and evidence:
            if any(not isinstance(x, str) or not x.strip() for x in evidence):
                problems.append(f"{tag} evidence 含空条目")
            elif not any(isinstance(x, str) and (_LINE_ANCHOR.search(x) or _COMMIT.search(x))
                         for x in evidence):
                problems.append(f"{tag} evidence 没有一条带 file:line 或 commit 锚")

        verification = row.get("verification")
        if isinstance(verification, list) and verification:
            if any(not isinstance(x, str) or not x.strip() for x in verification):
                problems.append(f"{tag} verification 含空条目")
            if not any(isinstance(x, str) and _CMD_VERB.search(x) for x in verification):
                problems.append(f"{tag} verification 没有一条是可复跑命令")
            for item in verification:
                if not isinstance(item, str):
                    continue
                for ref in _SCRIPT_REF.findall(item):
                    if not (ROOT / ref).is_file():
                        problems.append(f"{tag} verification 引用了不存在的脚本：{ref}")

        status = row.get("status")
        if status not in STATUSES:
            problems.append(f"{tag} status 非法（应为 done/pending/blocked）：{status!r}")
        if status == "done" and not str(row.get("result") or "").strip():
            problems.append(f"{tag} status=done 但没写 result")

    return problems, len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="决策 trail：证据 + 验证方式缺一即红")
    parser.add_argument("--trail", default=str(DEFAULT_TRAIL),
                        help="trail 路径（测试可指向临时副本）")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE),
                        help="开工模板路径")
    args = parser.parse_args(argv)

    problems, count = validate(Path(args.trail), Path(args.template))
    if problems:
        print(f"FAIL 决策 trail 校验不通过（{len(problems)} 项）：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"PASS 决策 trail：{count} 条记录每条都有证据与验证方式；开工模板五条验收判据齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
