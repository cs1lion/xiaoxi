#!/usr/bin/env python
"""像素世界独立门禁：类型检查 + 纯逻辑测试 + 调色板预算。

分工（D7-A，2026-10-19）：
- 本地默认 **strict**：`node scripts/audit-palette.mjs --strict`，世界色数 >16 或 UI >8
  就直接失败。本地是"要提交的人"自己跑，预算破了就该挡住。
- CI 用 **report-only**：`python scripts/test_pixel_world.py --report-only`，
  audit 去掉 `--strict`，只打印报告、不改退出码。理由：配色预算是视觉规范，
  不该让一个色号把别人的功能改动整体挡掉；但报告仍进 CI 日志留档，便于事后追。
- 只跑"零外部依赖"层：`npm run check`（tsc --noEmit）与 `npm run test:inprocess`
  （node --test，单进程跑同一批 test/*.test.ts）。用单进程入口是为了受限环境：
  `node --test` 默认按文件起子进程、用管道收输出，DSH 沙箱会直接拒绝（spawn EPERM）。
  浏览器冒烟 smoke 需要 dev server / Playwright，不放在本门禁；由
  `.github/workflows/pixel-world.yml` 按路径触发或本地按需单独跑。
- 不依赖主产品任何文件；可在 CI 与本地复用。

用法：
    python scripts/test_pixel_world.py                # 本地门禁（audit 用 --strict）
    python scripts/test_pixel_world.py --report-only  # CI 模式（audit 只报告）
    python scripts/test_pixel_world.py --list         # 只列当前模式与命令
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "pixel-world"

# Windows 控制台默认按 GBK 输出，中文在 CI/工具日志里会花屏；统一成 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPORT_ONLY_FLAG = "--report-only"


def npm() -> str:
    found = shutil.which("npm")
    if found:
        return found
    fallback = "npm.cmd" if sys.platform == "win32" else "npm"
    if shutil.which(fallback):
        return fallback
    return ""


def node() -> str:
    return shutil.which("node") or "node"


def audit_command(report_only: bool) -> list[str]:
    """本地 strict；CI report-only（只打印，退出码恒 0）。"""
    command = [node(), "scripts/audit-palette.mjs"]
    if not report_only:
        command.append("--strict")
    return command


def mode_label(report_only: bool) -> str:
    if report_only:
        # 别把它读成“门禁不会红”：只有配色预算被降级成报告。
        return (
            "ci-report-only（配色预算只报告、不改退出码；"
            "check/test 仍参与退出码，不是「门禁不会红」）"
        )
    return "local-strict（配色超预算即失败；check/test 同样参与退出码）"


def steps(report_only: bool) -> list[tuple[str, list[str]]]:
    return [
        ("check", [npm(), "run", "check"]),
        # 走单进程入口：受限环境/DSH 沙箱里，`node --test` 默认按文件起子进程、
        # 用管道收 stdout，会直接被拒（spawn EPERM）。`test:inprocess` 用
        # `--experimental-test-isolation=none` 在同一进程跑同一批 test/*.test.ts，
        # 本地与 CI 结果一致，也不需要任何提权。
        ("test", [npm(), "run", "test:inprocess"]),
        ("audit", audit_command(report_only)),
    ]


def main(argv: list[str]) -> int:
    report_only = REPORT_ONLY_FLAG in argv

    if not WORLD.is_dir():
        print(f"FAIL pixel-world directory missing: {WORLD}")
        return 1
    if "--list" in argv:
        print(f"mode: {mode_label(report_only)}")
        for name, cmd in steps(report_only):
            print(f"{name}: {' '.join(cmd)} (cwd={WORLD})")
        return 0

    missing = [label for label, exe in (("npm", npm()), ("node", node())) if not exe]
    if missing:
        print(f"FAIL 缺少可执行文件：{', '.join(missing)}（请先安装 Node.js / npm）")
        return 1

    print(f"mode: {mode_label(report_only)}", flush=True)
    failures = 0
    for name, cmd in steps(report_only):
        print(f"==> {name}: {' '.join(cmd)}", flush=True)
        # 不捕获子进程输出：Windows 沙箱下 pipe 会触发 spawn EPERM，
        # 直接继承 stdio 既避开该限制，也让 CI 日志保留原始输出。
        result = subprocess.run(cmd, cwd=WORLD)
        print(f"<== {name}: exit={result.returncode}", flush=True)
        if result.returncode != 0:
            failures += 1
    print("pixel_world gate:", "PASS" if failures == 0 else f"FAIL ({failures} step(s))")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
