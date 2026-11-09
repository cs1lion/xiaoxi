# -*- coding: utf-8 -*-
"""逐门禁跑一遍全量，并给每个门禁的子进程注入 `DB_AUDIT_GATE=<门禁名>`。

为什么不直接用 `run_gates.py`：它只把环境原样传给门禁进程，没有「当前门禁名」
这一维；而写 `data/` 的常常是门禁的孙子进程（例如 drill → uvicorn → sandbox_worker），
光看 argv 认不出是哪个门禁。逐条调用 `run_gate()` 并在每次前改环境变量，就把
归属钉死了。

用法：
    $env:PYTHONPATH = "<本目录>"
    $env:DB_AUDIT_ROOT = "<仓库根>"
    $env:DB_AUDIT_LOG = "<输出 jsonl>"
    <venv>\\Scripts\\python.exe docs\\evidence\\...\\run_audited_gates.py [--only a,b]

输出：每条门禁的 PASS/FAIL/SKIP 与耗时，最后一行汇总。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


def load_run_gates():
    spec = importlib.util.spec_from_file_location(
        "_rg", os.path.join(ROOT, "scripts", "run_gates.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    only: set[str] | None = None
    if "--only" in argv:
        only = {part.strip() for part in argv[argv.index("--only") + 1].split(",") if part.strip()}

    rg = load_run_gates()
    passed = failed = skipped = 0
    started = time.time()
    for name, gate in rg.GATES.items():
        if only and name not in only:
            continue
        os.environ["DB_AUDIT_GATE"] = name
        result = rg.run_gate(name, gate)
        if result["skipped"]:
            skipped += 1
            verdict = "SKIP"
        elif result["exit"] == 0:
            passed += 1
            verdict = "PASS"
        else:
            failed += 1
            verdict = "FAIL"
        print(f"[{verdict}] {name:32s} exit={result['exit']} {result['seconds']}s", flush=True)
        if verdict == "FAIL":
            print("  " + (result["output_tail"] or "").strip().replace("\n", "\n  "), flush=True)
    print(f"\n跑过并断言 {passed} / 失败 {failed} / 跳过 {skipped}，"
          f"总耗时 {round(time.time() - started, 1)}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
