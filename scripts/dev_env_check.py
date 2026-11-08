"""本机产物自检：换 worktree / 换机器跑门禁前，先看清缺了什么。

为什么需要它
------------
`run_gates.py` 的部分门禁依赖不在 git 里的本机产物：`.venv`、`node_modules`、
`agent-runtime/node_modules`、`.agents` bundle、`web/vendor/`、`web/models/`、
`pixel-world/`、Playwright 浏览器。缺项报出来的错像代码坏了——`durable_*`
抛 `ERR_MODULE_NOT_FOUND`、`venv_lock` 说解释器缺失——同一个人在同一个仓库
换一棵 worktree 就会得到相反结论。

本脚本只读，不创建、不下载、不修改任何文件；它不是门禁，不进
`run_gates.py` 的判定。需要时手动跑：

    .\\.venv\\Scripts\\python.exe scripts\\dev_env_check.py

判定口径：
- **必须**：缺了门禁主体成片失败（不是个别用例）。
- **部分**：只影响列出的门禁，其余照跑；`.agents` / `web/models` 缺失时
  脚本自己会 SKIP，不判红（是否算覆盖损失由你判断）。

退出码：有「必须」项缺失时 1，否则 0。部分项缺失只告警。
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WIN = os.name == "nt"


def classify_gates() -> dict[str, list[str]]:
    """从 run_gates.GATES 现读门禁清单，避免本脚本复制一份会过期的名单。

    run_gates 顶层只做路径与常量定义，导入不执行门禁（main 在 __main__ 下）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_gates  # noqa: E402

    groups: dict[str, list[str]] = {"python": [], "ts": [], "npm": [], "browser": []}
    for name, spec in run_gates.GATES.items():
        cmd = [str(part) for part in spec["cmd"]]
        if any(part.endswith(".ts") for part in cmd):
            groups["ts"].append(name)
        elif any(os.path.basename(part).lower().startswith("npm") for part in cmd):
            groups["npm"].append(name)
        elif any(part.endswith(".mjs") for part in cmd):
            groups["browser"].append(name)
        elif any(part.endswith(".py") for part in cmd):
            groups["python"].append(name)
    for names in groups.values():
        names.sort()
    return groups


def names_text(names: list[str]) -> str:
    return "、".join(names) if names else "无"


def venv_python() -> Path:
    if IS_WIN:
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def playwright_browsers_dir() -> Path | None:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    candidates = [Path(override)] if override else []
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "ms-playwright")
    else:
        candidates.append(Path.home() / ".cache" / "ms-playwright")
    for path in candidates:
        if path.is_dir():
            return path
    return None


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def emit(present: bool, title: str, required: bool, affected: str,
         fix: str, detail: str = "") -> None:
    tag = "[ 在 ]" if present else "[ 缺 ]"
    level = "必须" if required else "部分"
    print(f"{tag} {title}  [{level}]")
    print(f"       影响：{affected}")
    if detail:
        print(f"       现状：{detail}")
    if not present:
        print(f"       补法：{fix}")
    print()


def main() -> int:
    gates = classify_gates()
    py_gates = gates["python"]
    ts_gates = gates["ts"]
    npm_gates = gates["npm"]
    browser_gates = gates["browser"]
    node_gates = npm_gates + browser_gates
    all_gates = sorted(n for group in gates.values() for n in group)

    missing_required: list[str] = []
    missing_partial: list[str] = []

    print(f"本机产物自检  root={ROOT}")
    print(f"门禁清单读自 run_gates.GATES：共 {len(all_gates)} 条"
          f"（Python {len(py_gates)} / TS {len(ts_gates)} / npm {len(npm_gates)}"
          f" / 浏览器 {len(browser_gates)}）")
    print()
    print("== 必须（缺了门禁主体成片失败） ==")
    print()

    # 1. .venv
    py = venv_python()
    present = py.is_file()
    if not present:
        missing_required.append(".venv")
    emit(
        present, f".venv（{py.relative_to(ROOT)}）", True,
        f"全部 Python 门禁（{len(py_gates)} 条）。缺了 run_gates 会退回启动它的"
        "解释器；`venv_lock` 仍会直接判红。",
        "仓库根跑 `uv sync --frozen`（不要用 `uv pip install -r requirements.txt`）。"
        "仅本机多 worktree 时可建 junction："
        "`cmd /c mklink /J <本树>\\.venv <有依赖的树>\\.venv`"
        "（先确认两边 uv.lock 相同；CI / 新 clone 一律用 uv sync）。",
        "" if present else f"{py} 不存在",
    )

    # 2. node / npm 可执行
    node = shutil.which("node")
    npm = shutil.which("npm")
    present = bool(node and npm)
    if not present:
        missing_required.append("node/npm")
    emit(
        present, "node / npm 可执行", True,
        f"Node 侧门禁（{len(node_gates)} 条：npm {len(npm_gates)} + 浏览器 "
        f"{len(browser_gates)}）与 TS 门禁（{len(ts_gates)} 条，需 node）",
        "安装 Node.js 22 LTS（npm 随附）；确认 `node --version` 与 `npm --version` 可用。",
        f"node={node or '缺'}  npm={npm or '缺'}",
    )

    print("== 部分（只影响列出的门禁，其余照跑） ==")
    print()

    # 3. 根 node_modules
    root_modules = ROOT / "node_modules"
    present = root_modules.is_dir()
    if not present:
        missing_partial.append("node_modules")
    emit(
        present, "node_modules/（仓库根）", False,
        f"npm 门禁（{names_text(npm_gates)}）与浏览器门禁（{names_text(browser_gates)}）",
        "仓库根跑 `npm ci`（按 package-lock.json）。仅本机多 worktree 时可"
        " `cmd /c mklink /J <本树>\\node_modules <有依赖的树>\\node_modules`。",
        "" if present else f"{root_modules} 不存在；浏览器门禁会因 "
        "`@playwright/test` 找不到而直接失败，不是 SKIP。",
    )

    # 4. agent-runtime/node_modules
    runtime_modules = ROOT / "agent-runtime" / "node_modules"
    present = runtime_modules.is_dir()
    if not present:
        missing_partial.append("agent-runtime/node_modules")
    emit(
        present, "agent-runtime/node_modules/", False,
        f"{len(ts_gates)} 条 TS 门禁：{names_text(ts_gates)}",
        "`cd agent-runtime` 后跑 `npm ci`。多 worktree 可"
        " `cmd /c mklink /J <本树>\\agent-runtime\\node_modules "
        "<有依赖的树>\\agent-runtime\\node_modules`。",
        "" if present else f"{runtime_modules} 不存在；TS 门禁报 "
        "`ERR_MODULE_NOT_FOUND`（如 @earendil-works/chord），看起来像代码坏了。",
    )

    # 5. web/vendor
    vendor_files = [
        ROOT / "web" / "vendor" / "three" / "three.module.js",
        ROOT / "web" / "vendor" / "three-vrm" / "three-vrm.module.js",
    ]
    absent_vendor = [str(p.relative_to(ROOT)) for p in vendor_files if not p.is_file()]
    present = not absent_vendor
    if not present:
        missing_partial.append("web/vendor")
    emit(
        present, "web/vendor/three + three-vrm（浏览器 importmap 依赖）", False,
        f"浏览器门禁（{names_text(browser_gates)}）；页面 importmap 指向 "
        "`/static/vendor/...`，缺了模块加载失败。",
        "先装根 node_modules，再跑 `npm run vendor:avatar`（从 node_modules 复制 "
        "three 0.180.0 与 three-vrm 3.5.5）。",
        "缺：" + "、".join(absent_vendor) if absent_vendor else "",
    )

    # 6. Playwright 浏览器
    browsers = playwright_browsers_dir()
    chromium = sorted(p.name for p in browsers.glob("chromium*")
                      if p.is_dir()) if browsers else []
    present = bool(chromium)
    if not present:
        missing_partial.append("playwright-browser")
    emit(
        present, "Playwright Chromium", False,
        f"浏览器门禁（{names_text(browser_gates)}）：`chromium.launch()` 需要已下载的浏览器",
        "`npx playwright install chromium`（版本由 package-lock.json 的 "
        "@playwright/test 1.61.1 决定）。",
        f"目录={browsers or '缺'}；" + (f"找到 {names_text(chromium)}" if chromium else "无 chromium*"),
    )

    # 7. .agents bundle
    skills = ROOT / ".agents" / "skills"
    entry = skills / "poteto-mode" / "SKILL.md"
    body = skills / "pstack" / "poteto-mode" / "SKILL.md"
    dsh = skills / "pstack" / "DSH.md"
    principles = sorted(skills.glob("pstack/principle-*/SKILL.md"))
    missing_agents = [str(p.relative_to(ROOT)) for p in (entry, body, dsh) if not p.is_file()]
    present = not missing_agents and bool(principles)
    if not present:
        missing_partial.append(".agents")
    test_gate = "poteto_loading" if "poteto_loading" in gates["python"] else None
    emit(
        present, ".agents bundle（poteto-mode + principle-*）", False,
        (f"`{test_gate}`" if test_gate else "无门禁直接依赖（poteto_loading 不在本分支 GATES）")
        + "；该脚本在 bundle 缺失时显式 SKIP，不判红。它只验文件契约，不冒充"
          "「会话已加载」。",
        "按 pstack 安装流程恢复 bundle。重装会覆盖本机两份修正，之后从 HEAD 检出："
        "`git checkout -- .agents/skills/pstack/DSH.md "
        ".agents/skills/pstack/docs/harness/claude-code.md`。",
        ("缺：" + "、".join(missing_agents)) if missing_agents
        else "" if principles else "principle-*/SKILL.md 为空",
    )

    # 8. web/models
    models = [
        "seed-daily.vrm",
        "seed-daily-eyes.vrm",
        "seed-daily-lids.vrm",
    ]
    absent_models = [name for name in models
                     if not (ROOT / "web" / "models" / name).is_file()]
    present = not absent_models
    if not present:
        missing_partial.append("web/models")
    seed_gates = sorted(name for name in py_gates if "seed_eye" in name)
    emit(
        present, "web/models/（seed-daily 链）", False,
        (f"seed 视觉门禁：{names_text(seed_gates)}" if seed_gates
         else "无（相关门禁已移出 GATES，见 scripts/gates_unregistered.txt）")
        + "；缺资产时它们输出 SKIP，不判红。",
        "第三方许可资产（VirtualCast / VRM Public License 1.0，需署名），"
        "不入库、不自动下载：向维护者取副本；或在有 `seed-san.vrm` 的机器上按"
        " `scripts/build_seed_daily.py` → `build_seed_eye_expression.py` → "
        "`build_seed_eye_lids.py` 重建（前一步需 numpy + Pillow）。",
        ("缺：" + "、".join(absent_models)) if absent_models else "",
    )

    # 9. pixel-world
    world_entry = ROOT / "pixel-world" / "dist" / "index.html"
    present = world_entry.is_file()
    if not present:
        missing_partial.append("pixel-world")
    step8 = sorted(name for name in browser_gates if "step8" in name)
    emit(
        present, "pixel-world/dist/（/world 静态产物）", False,
        (f"{names_text(step8)}（它 goto /world 并等 `window.Oracle`）" if step8
         else "无门禁直接依赖") + "；`pixel_world_hosting` 不依赖源码，用临时根自造产物。",
        "世界工程已拆到 `cs1lion/pixel-world-engine`；按该仓 README 构建后把 "
        "dist 放到 `pixel-world/dist/`（主仓不再承担构建）。",
        "" if present else f"{world_entry.relative_to(ROOT)} 不存在",
    )

    print("== 运行时前提（不是磁盘产物） ==")
    print()

    # 10. 8000 端口
    present = port_open(8000)
    emit(
        present, "127.0.0.1:8000 本地服务", False,
        f"浏览器门禁（{names_text(browser_gates)}）；服务未起时多数显式 SKIP，"
        "`run_gates` 会把 SKIP 判为「这轮没真跑」。",
        "仓库根跑 `.\\.venv\\Scripts\\python.exe scripts\\run.py --port 8000`；"
        "改过 `web/` 后先杀旧进程再重启（StaticFiles 会缓存目录列表）。",
        "端口未监听" if not present else "",
    )

    if missing_required:
        print(f"结果：必须项缺 {len(missing_required)} 项（{names_text(missing_required)}）"
              f"；部分项缺 {len(missing_partial)} 项"
              + (f"（{names_text(missing_partial)}）" if missing_partial else "")
              + "。")
        print("先补齐必须项，再看部分项是否在你这次要跑的门禁范围里。")
        return 1
    print(f"结果：必须项齐全；部分项缺 {len(missing_partial)} 项"
          + (f"（{names_text(missing_partial)}）" if missing_partial else "")
          + "。")
    print("部分项只影响上面列出的门禁；不在范围内的可以直接跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
