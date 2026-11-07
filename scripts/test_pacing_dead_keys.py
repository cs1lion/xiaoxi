# -*- coding: utf-8 -*-
"""pacing.yaml 死键棘轮：新增「生产代码里没人读」的配置键必须显式登记。

**为什么需要它**：2026-11-06 实测 `config/pacing.yaml` 有一批键在
`server/` / `web/` 里零读取方，其中 `thinking_ms` 还被
`docs/prompts/Role2_LowLatency_Voice_Stage.md:25,32` 称作「权威参数」——文档与
代码不一致。既有 `scripts/audit_config_keys.py:28-39` 只扫 `memory.yaml`，
靠 `*_DEFAULTS` 白名单常量判定，而 `pacing_cfg()` 直接 `_load_yaml`，没有
defaults 常量，框架套不上。

**口径（为什么不直接删键）**：这批键里有 `refuse_sexual_minors` /
`refuse_self_harm_support` 等安全声明，以及产品侧的语气/话题约定，删掉是产品
决策而非死代码清理。按 `correct` 的优先级，正确解法是把老毛病变成 CI 闸门：
既有死键登记进 `scripts/pacing_dead_keys.txt` 作基线，**新增**死键即红；
键被真正接上（从 dead 里消失）只告警，不判红。

**消费方定义**：只扫 `server/` 与 `web/` 的字符串字面量。不扫 `scripts/`——
测试/门禁脚本里提到键名只是断言，不构成消费；把算进去只会让棘轮漏报。
同理用 AST `Constant(str)` 而非纯文本包含，避免注释里的词被当成读取。

**自检内置**：默认先跑 4 条沙盘断言（有效消费不算死键 / 基线内不红 /
新增死键判红 / 抹掉基线判红），再跑真实审计。沙盘在系统临时目录，
不碰仓库文件、不需要服务与 Key。

Run: .venv/Scripts/python.exe -B scripts/test_pacing_dead_keys.py
     .venv/Scripts/python.exe -B scripts/test_pacing_dead_keys.py --print-dead
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "pacing.yaml"
BASELINE = ROOT / "scripts" / "pacing_dead_keys.txt"
SCAN_DIRS = ("server", "web")


def leaf_keys(node) -> set[str]:
    """展开嵌套 dict 的叶键；list 里的 dict 也展开，标量 list 视为叶值。"""
    out: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, dict):
                out |= leaf_keys(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        out |= leaf_keys(item)
            else:
                out.add(str(key))
    return out


def literals(root: Path) -> set[str]:
    """生产代码里出现过的字符串字面量（AST，不看注释）。"""
    out: set[str] = set()
    for dirname in SCAN_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.suffix == ".py":
                    tree = ast.parse(path.read_bytes(), filename=str(path))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Constant) and isinstance(node.value, str):
                            out.add(node.value)
                elif path.suffix in (".js", ".ts"):
                    text = path.read_text(encoding="utf-8")
                    out.update(re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", text))
            except (SyntaxError, ValueError, UnicodeDecodeError, OSError):
                continue
    return out


def dead_now(config: Path, root: Path) -> set[str]:
    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    return {key for key in leaf_keys(data) if key not in literals(root)}


def read_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


def audit(config: Path, root: Path, baseline_path: Path) -> tuple[list[str], set[str], set[str]]:
    """返回 (问题, 当前死键, 已接上的旧死键)。纯函数，不写文件。"""
    dead = dead_now(config, root)
    baseline = read_baseline(baseline_path)
    new_dead = dead - baseline
    stale = baseline - dead
    problems = [
        f"新增未登记死键：{key}（生产代码零读取方；要么接上消费方，"
        f"要么显式登记进 scripts/pacing_dead_keys.txt 并说明理由）"
        for key in sorted(new_dead)
    ]
    return problems, dead, stale


def self_test() -> list[str]:
    """4 条判别力断言：不是恒绿、也不是恒红。"""
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pacing-dead-test-") as tmp:
        root = Path(tmp)
        for dirname in ("config", "server", "web", "scripts"):
            (root / dirname).mkdir()
        config = root / "config" / "pacing.yaml"
        baseline = root / "scripts" / "pacing_dead_keys.txt"
        (root / "server" / "consumer.py").write_text('x = "live_key"\n', encoding="utf-8")

        def write_cfg(extra: str = "") -> None:
            config.write_text(f"live_key: 1\ndead_key: 2\n{extra}", encoding="utf-8")

        write_cfg()
        baseline.write_text("# baseline\ndead_key\n", encoding="utf-8")
        found, dead, _ = audit(config, root, baseline)
        if dead != {"dead_key"}:
            problems.append(f"有效消费方被误判成死键：{sorted(dead)}")
        if found:
            problems.append(f"基线内的死键不该判红：{found}")

        write_cfg("new_dead: 3\n")
        found, _, _ = audit(config, root, baseline)
        if not found:
            problems.append("新增死键没判红——棘轮无效")

        write_cfg()
        baseline.write_text("# baseline\n", encoding="utf-8")
        found, _, _ = audit(config, root, baseline)
        if not found:
            problems.append("抹掉基线后仍绿——棘轮无效")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pacing.yaml 死键棘轮")
    parser.add_argument("--print-dead", action="store_true",
                        help="只打印当前死键（用于生成基线），不做自检与审计")
    args = parser.parse_args(argv)

    if args.print_dead:
        for key in sorted(dead_now(CONFIG, ROOT)):
            print(key)
        return 0

    issues = self_test()
    if issues:
        print("FAIL 自检不通过：")
        for item in issues:
            print(f"  - {item}")
        return 1

    problems, dead, stale = audit(CONFIG, ROOT, BASELINE)
    print(f"自检 4 条通过；pacing.yaml 当前死键 {len(dead)} 个，基线 {len(dead) - len(dead - read_baseline(BASELINE))} 个命中")
    for key in sorted(stale):
        print(f"  info 已被接上（可从基线移除）：{key}")
    if problems:
        print(f"FAIL 死键棘轮：{len(problems)} 个新键未登记")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("PASS 无新增死键；新增无消费方配置键会被本门禁挡住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
