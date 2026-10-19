#!/usr/bin/env python
"""像素世界独立门禁：类型检查 + 纯逻辑测试。

设计原则（D7-A）：
- 只跑"零外部依赖"层：`npm run check`（tsc --noEmit）与 `npm test`（node --test）。
- 浏览器冒烟 smoke 需要 dev server / Playwright，不放在本门禁；由
  `.github/workflows/pixel-world.yml` 按路径触发或本地按需单独跑。
- 不依赖主产品任何文件；可在 CI 与本地复用。

用法：
    python scripts/test_pixel_world.py          # 跑门禁
    python scripts/test_pixel_world.py --list   # 只列命令
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "pixel-world"


def npm() -> str:
    found = shutil.which("npm")
    if found:
        return found
    fallback = "npm.cmd" if sys.platform == "win32" else "npm"
    if shutil.which(fallback):
        return fallback
    return ""


def steps() -> list[tuple[str, list[str]]]:
    return [
        ("check", [npm(), "run", "check"]),
        ("test", [npm(), "test"]),
    ]


def main(argv: list[str]) -> int:
    if not WORLD.is_dir():
        print(f"FAIL pixel-world directory missing: {WORLD}")
        return 1
    if "--list" in argv:
        for name, cmd in steps():
            print(f"{name}: {' '.join(cmd)} (cwd={WORLD})")
        return 0

    if not npm():
        print("FAIL npm not found in PATH（请先安装 Node.js / npm）")
        return 1

    failures = 0
    for name, cmd in steps():
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
