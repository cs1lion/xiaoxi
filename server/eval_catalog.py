# -*- coding: utf-8 -*-
"""评测场景目录：`evals/cases/*.yaml` → `/api/eval/scenarios` 的只读载荷。

为什么不再读 `eval/scenarios.json`：那份文件从未入库，接口因此恒返回空；
现行评测集的单一真相源已经是 `evals/cases/`（`scripts/run_evals.py` 同源）。
这里只做字段映射，不执行用例、不碰数据库、不读取 Key。

坏文件处理：单个 YAML 解析失败只进 `invalid` 清单，不影响其余用例——
诊断入口不该因为一个坏文件整页空白；真正的用例正确性由 `run_evals.py` 把关。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "evals" / "cases"


def _item(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    turns = data.get("turns")
    checks = data.get("checks")
    tags = data.get("tags")
    return {
        "id": str(data.get("id") or path.stem),
        "name": str(data.get("title") or path.stem),
        # 旧载荷用单个 user 字符串；新用例是 turns 列表，按序拼给人看。
        "user": " / ".join(str(turn) for turn in turns if isinstance(turn, str))
                if isinstance(turns, list) else "",
        "layer": str(data.get("layer") or ""),
        "status": str(data.get("status") or ""),
        "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
        "checks": checks if isinstance(checks, list) else [],
    }


def load_catalog(cases_dir: Path = CASES_DIR) -> dict[str, Any]:
    """返回 {"items", "count", "invalid"}；目录不存在时给空目录而不是报错。"""
    items: list[dict[str, Any]] = []
    invalid: list[str] = []
    if cases_dir.is_dir():
        for path in sorted(cases_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
            except (OSError, yaml.YAMLError):
                invalid.append(path.name)
                continue
            if not isinstance(data, dict):
                invalid.append(path.name)
                continue
            items.append(_item(path, data))
    items.sort(key=lambda item: item["id"])
    return {"items": items, "count": len(items), "invalid": invalid}
