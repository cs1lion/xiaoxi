# -*- coding: utf-8 -*-
"""R51 裁决口径门禁：钉住 run_gates 的完整裁决表。

**为什么需要它**：`run_gates.py` 的裁决式曾在 R51 之前把 `skipped` 排在非零
退出码之前——一个「打印了 SKIP 行却又 exit≠0」的门禁会被误判成 SKIP，于是
① 失败输出不被贴；② 末尾 `if failed` 不含它；③ `--allow-skip` 又放过
skipped。三者叠加，一句真实失败被洗成绿，结论还打印「所有门禁都真跑过并
断言」。这条 fail-open 在 2026-10-27 被合成门禁实证后修掉。

但真实门禁覆盖不到它：`ui_nav_browser` 只在 8000 未起时才 SKIP，8000 运行时
它判 PASS。即这条最关键的行为**没有任何常驻覆盖**，修复之后又回到无保护状态。
本门禁用合成门禁把它钉住。

**钉的是完整裁决表，三条腿都要在**（只钉一条会漏掉另外两种误判）：

| exit | 输出命中 skip_regex | 正确 verdict |
|---|---|---|
| ≠0  | 是                  | FAIL（不能因带 SKIP 行就被洗成 SKIP）|
| ≠0  | 否                  | FAIL |
| 0   | 是                  | SKIP（不是 FAIL，也不是 PASS）|
| 0   | 否                  | PASS |

前两行是 R51 的本体（fail-closed），第三行防「把没跑误判成失败」——
它同样危险：会让一条环境不满足的门禁把整套报告染红，训练人忽略红色。
第四行是基线，证明测量器活着。

**清理纪律**：合成门禁脚本写在 `tempfile.mkdtemp()` 里，`finally` 里删。
`run_gates` 每次跑都在 tmp/ 留沙盘曾是 tmp_sweep 长期红的原因（2026-10-27
修掉四个浏览器门禁的泄漏），本门禁不能变成新的泄漏源。

Run: .venv/Scripts/python.exe -B scripts/test_gate_verdict_r51.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []

# 合成门禁的三种形态：exit 与「是否打印 SKIP 行」的四种组合。
SYNTHETICS = {
    "r51_fail_with_skip_line": (1, True),
    "r51_fail_plain": (1, False),
    "r51_skip_clean": (0, True),
    "r51_pass_plain": (0, False),
}
EXPECTED = {
    "r51_fail_with_skip_line": "FAIL",
    "r51_fail_plain": "FAIL",
    "r51_skip_clean": "SKIP",
    "r51_pass_plain": "PASS",
}
SKIP_REGEX = r"^\s*SKIP"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def _load_run_gates():
    spec = importlib.util.spec_from_file_location(
        "run_gates_under_test", ROOT / "scripts" / "run_gates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_synthetic(sandbox: Path, name: str, exit_code: int, print_skip: bool) -> str:
    script = sandbox / f"{name}.py"
    body = ["import sys"]
    if print_skip:
        # 故意写成行首 SKIP：命中 run_gates 的 skip_regex，正是旧裁决式
        # 会把 exit≠0 也判成 SKIP 的那种输出。
        body.append("print('SKIP synthetic: environment not satisfied')")
    body.append("print('synthetic gate ran')")
    body.append(f"sys.exit({exit_code})")
    script.write_text("\n".join(body) + "\n", encoding="utf-8")
    return str(script)


def _run_gates(mod, only: list[str], allow_skip: bool) -> tuple[int, str]:
    """跑一次 run_gates.main()，返回 (退出码, 捕获的输出)。"""
    argv = ["run_gates.py", "--only", ",".join(only)]
    if allow_skip:
        argv.append("--allow-skip")
    old_argv = sys.argv
    sys.argv = argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = mod.main()
    finally:
        sys.argv = old_argv
    return code, buf.getvalue()


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="gate-verdict-r51-"))
    try:
        mod = _load_run_gates()
        # 合成门禁只注入到模块副本里，不碰磁盘上的 run_gates.py。
        for name, (exit_code, print_skip) in SYNTHETICS.items():
            mod.GATES[name] = {
                "cmd": [sys.executable, "-B", _write_synthetic(sandbox, name, exit_code, print_skip)],
                "skip_regex": SKIP_REGEX,
            }
        names = list(SYNTHETICS)

        code, out = _run_gates(mod, names, allow_skip=False)
        print("[不带 --allow-skip]")
        for name in names:
            verdict = _verdict_of(out, name)
            print(f"        {name}: {verdict}")
            check(f"{name} 判 {EXPECTED[name]}", verdict == EXPECTED[name],
                  f"实际 {verdict}")

        # 关键一条：--allow-skip 不得把「打印 SKIP 行却 exit≠0」的真实失败
        # 洗成绿。R51 之前这里会 exit 0。
        code_allow, out_allow = _run_gates(mod, names, allow_skip=True)
        print("[带 --allow-skip]")
        print(f"        main() exit={code_allow}")
        check("带 --allow-skip 时真实失败仍让 main() 退 1（不被洗成绿）",
              code_allow == 1, f"exit={code_allow}")

        # 反向基线：只有真 SKIP 时 --allow-skip 才放行，证明它没有把一切染红。
        mod.GATES.pop("r51_fail_with_skip_line")
        mod.GATES.pop("r51_fail_plain")
        code_skip_only, _ = _run_gates(mod, ["r51_skip_clean", "r51_pass_plain"], allow_skip=True)
        print(f"[只有真 SKIP 与 PASS 时带 --allow-skip] main() exit={code_skip_only}")
        check("只有真 SKIP 时 --allow-skip 放行（exit 0），证明它不是把一切染红",
              code_skip_only == 0, f"exit={code_skip_only}")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


def _verdict_of(output: str, name: str) -> str:
    """从 run_gates 的裁决行取 verdict。

    输出有两种前缀行，必须区分：
        [run ] <name>                  ← 开跑提示，无 exit=
        [FAIL] <name>        exit=1 …  ← 裁决行（verdict 右对齐补空格）
    按 `exit=` 判定才是裁决行，否则会把 `[run ]` 的 token 当成裁决结果。
    """
    for line in output.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("[") or "exit=" not in stripped:
            continue
        token = stripped[1:stripped.index("]")].strip()
        rest = stripped[stripped.index("]") + 1:].strip()
        if rest.split(" ")[0] == name:
            return token
    return "MISSING"


if __name__ == "__main__":
    raise SystemExit(main())
