"""配置键审计：找出"代码读了、但 defaults 白名单没登记"的键。

为什么值得一个脚本：`self_evolution_cfg()` 这类访问器只放行 defaults 里出现过的键
（`cfg.update({k: v for k, v in configured.items() if k in cfg})`）。漏登记的后果
不是报错，而是**配置写了也静默失效**——2026-10-17 踩过一次：
`max_model_calls_per_cycle` / `max_retries` 没进 `SELF_EVOLUTION_DEFAULTS`，
`_reflect` 从 cfg 读它们时永远拿到硬编码回落，而回落值恰好相等，所以没有任何症状。

判定口径：把模块里所有 `X.get("k", d)` / `X["k"]` 的字面量键，与 defaults 常量
的键做差集。读得到却不登记的，就是"yaml 里写了也不算"的键。

只读源码，不导入配置、不联网。

    python scripts/audit_config_keys.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 访问器 -> (defaults 常量, memory.yaml 里的段名, 同模块内消费 cfg 的函数)
TARGETS = [
    ("server/memory/evolution.py", "self_evolution_cfg", "SELF_EVOLUTION_DEFAULTS",
     "self_evolution", ["_reflect"]),
    ("server/memory/feedback.py", "feedback_cfg", "FEEDBACK_DEFAULTS",
     "feedback", []),
    ("server/memory/lessons.py", "lessons_cfg", "LESSON_DEFAULTS",
     "lessons", []),
    ("server/dialogue/ambient.py", "ambient_cfg", "AMBIENT_DEFAULTS",
     "ambient", []),
]


def defaults_keys(tree: ast.Module, name: str) -> set[str] | None:
    """白名单常量里的键。带类型注解的赋值是 AnnAssign，也要认。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                if isinstance(node.value, ast.Dict):
                    return {k.value for k in node.value.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                return None
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(node.value, ast.Dict):
                        return {k.value for k in node.value.keys
                                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    return None
    return None


def cfg_variables(tree: ast.Module, func_name: str) -> set[str]:
    """访问器里出现过的局部变量名——凡是能被 `.get()` / `[]` 读的都算候选。

    不猜"哪个变量才是配置"：访问器只有几行，候选集大一点没有误报风险，
    猜错了才会整个漏掉。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name):
            continue
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = sub.targets
            elif isinstance(sub, ast.AnnAssign):
                targets = [sub.target]
            elif isinstance(sub, (ast.For, ast.comprehension)):
                targets = [sub.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def read_keys(tree: ast.Module, variables: set[str]) -> set[str]:
    """这些变量上读到的字面量键：`.get("k", ...)` 与 `["k"]`。"""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "get" or not node.args:
                continue
            base = node.func.value
            if isinstance(base, ast.Name) and base.id in variables:
                if (isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    keys.add(node.args[0].value)
        elif isinstance(node, ast.Subscript):
            base = node.value
            if isinstance(base, ast.Name) and base.id in variables:
                slice_node = node.slice
                if (isinstance(slice_node, ast.Constant)
                        and isinstance(slice_node.value, str)):
                    keys.add(slice_node.value)
    return keys


def main() -> int:
    import yaml  # 项目既有依赖；只读 yaml，不导入 server

    memory_yaml = yaml.safe_load((ROOT / "config" / "memory.yaml").read_text(encoding="utf-8"))
    total = 0
    for rel, func, const, section, consumers in TARGETS:
        path = ROOT / rel
        if not path.is_file():
            print(f"[skip] {rel} 不存在")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        whitelist = defaults_keys(tree, const)
        print(f"\n=== {rel} :: {func}()")
        if whitelist is None:
            print(f"  {const} 不是字面量 dict，本脚本判不了，跳过")
            continue
        variables = cfg_variables(tree, func)
        for name in consumers:
            variables |= cfg_variables(tree, name)
        reads = read_keys(tree, variables)
        configured = (memory_yaml or {}).get(section)
        configured_keys = set(configured) if isinstance(configured, dict) else set()
        print(f"  defaults 键 : {sorted(whitelist)}")
        print(f"  读到的键    : {sorted(reads)}")
        print(f"  yaml 里写了 : {sorted(configured_keys) or '（该段为空或未配置）'}")

        # 1. 代码读了但没登记：yaml 写了也静默失效（2026-10-17 踩过的那个坑）
        unregistered = sorted(reads - whitelist)
        if unregistered:
            total += len(unregistered)
            print(f"  !! 读了但没登记 —— yaml 里写了也静默失效: {unregistered}")
        else:
            print("  ok 读到的键全部已登记")

        # 2. yaml 写了但没登记：同一故障的用户侧视角，报出来才有意义
        dead = sorted(configured_keys - whitelist)
        if dead:
            total += len(dead)
            print(f"  !! yaml 里配了但 defaults 没收 —— 这些键现在完全不生效: {dead}")

        # 3. 登记了但没人读：可能是死配置，也可能由别处消费，只提示不计数
        unused = sorted(whitelist - reads)
        if unused:
            print(f"  .. 登记了但上述函数没读到（可能死配置）: {unused}")

    print(f"\n=== 问题键合计: {total} ===")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
