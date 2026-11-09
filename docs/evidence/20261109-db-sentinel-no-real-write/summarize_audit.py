# -*- coding: utf-8 -*-
"""把 sitecustomize 审计日志汇总成「门禁名 → 真实路径 → 是否写」。

用法：
    python summarize_audit.py <审计 jsonl> [<仓库根>]

判据：`event=write`（进程退出时 stat 与连接建立时不一致）即为「写」；只有
`event=open` 的是「只读」。门的名字来自 scripts/run_gates.py 的 GATES 表：
子进程 argv[0]（或调用栈里最近的门禁脚本）与某条 cmd 里的脚本同名即归属，
对不上的单列「未归属」，不猜。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import OrderedDict


def load_gates(root: str) -> dict[str, str]:
    """脚本 basename → 门禁名。"""
    spec = importlib.util.spec_from_file_location(
        "_gates_for_audit", os.path.join(root, "scripts", "run_gates.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # 模块级只有常量与函数定义，不跑门禁
    mapping: dict[str, str] = {}
    for name, gate in module.GATES.items():
        for token in gate.get("cmd", []):
            base = os.path.basename(str(token))
            if base.endswith((".py", ".mjs", ".js")):
                mapping.setdefault(base, name)
    return mapping


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    log_path = argv[0]
    root = argv[1] if len(argv) > 1 else os.getcwd()
    gates = load_gates(root)

    rows: "OrderedDict[str, dict]" = OrderedDict()
    for line in open(log_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        ident = ev.get("gate") or ""  # run_audited_gates.py 注入的当前门禁名，优先
        if not ident:
            for token in ev.get("argv") or []:
                ident = gates.get(os.path.basename(str(token)), "")
                if ident:
                    break
        if not ident:
            for frame in ev.get("stack", "").split(" | "):
                ident = gates.get(os.path.basename(frame.split(":")[0]), "")
                if ident:
                    break
        slot = rows.setdefault(ident or "未归属",
                               {"targets": {}, "why": set(), "pids": set()})
        target = ev["target"]
        info = slot["targets"].setdefault(target, {"wrote": False, "created": False})
        slot["pids"].add(ev.get("pid"))
        if ev.get("event") == "file_write":
            info["wrote"] = True
            slot["why"].add(f"以 {ev.get('mode')} 模式写文件")
        if ev.get("event") == "write":
            info["wrote"] = True
            slot["why"].add(f"stat 变化 {ev.get('before')} → {ev.get('after')}"
                            + (f"；语句旁证 {','.join(ev.get('actions') or [])}" if ev.get("actions") else ""))
        if ev.get("event") == "open" and not ev.get("existed_before", True):
            info["created"] = True
            slot["why"].add("connect 时文件不存在（被创建）")

    if not rows:
        print("审计结果：没有任何进程打开过 <root>/data/ 下的 SQLite 文件。")
        return 0

    print("| 门禁 | 打开的真实路径 | 是否写 | 依据 |")
    print("| --- | --- | --- | --- |")
    for name, slot in rows.items():
        targets = "<br>".join(sorted(slot["targets"]))
        wrote = any(v["wrote"] or v["created"] for v in slot["targets"].values())
        why = "；".join(sorted(slot["why"])) or "只有 open，进程退出时 stat 未变"
        print(f"| {name} | {targets} | {'写' if wrote else '只读'} | {why} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
