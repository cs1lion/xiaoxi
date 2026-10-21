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

# 访问器 -> (defaults 常量, memory.yaml 里的段名, 消费 cfg 的 (文件, 函数))
# 消费者**可以跨文件**：self_evolution_cfg() 的 cfg 实际在 server/assistant/loop.py
# 的 _reflect 里消费。第一版把它们当同模块，`_reflect` 的读取一个都没扫到，
# 于是"读了但没登记"这条主检查形同虚设（2026-10-18 修）。
TARGETS = [
    ("server/memory/evolution.py", "self_evolution_cfg", "SELF_EVOLUTION_DEFAULTS",
     "self_evolution", [("server/assistant/loop.py", "_reflect"),
                        ("server/assistant/loop.py", "_fork_bounds"),
                        ("server/dialogue/orchestrator.py", "_circadian_and_mood")]),
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


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(sub, ast.Name) and sub.id == name for sub in ast.walk(node))


def cfg_variables(tree: ast.Module, func_name: str, accessor: str | None = None) -> set[str]:
    """函数里哪些变量能当"配置对象"读。

    - `accessor is None`（访问器自身）：局部变量全收。函数只有几行，候选集大一点
      不会误报，猜错才会整个漏掉。
    - `accessor` 给定（消费函数）：只收**由该访问器产出**的变量（`cfg = ... or
      self_evolution_cfg()`），以及名字本身像配置的参数。全收会把 `trace.get(...)`
      这类日志字段算成"读了但没登记的配置键"，制造一堆假阳性——假阳性比漏检更糟，
      它会让真问题淹在噪声里。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name):
            continue
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            lowered = arg.arg.lower()
            if accessor is None or any(tok in lowered for tok in ("cfg", "config", "conf")):
                names.add(arg.arg)
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                if accessor is not None and not _mentions(sub.value, accessor):
                    continue
                targets = sub.targets
            elif isinstance(sub, ast.AnnAssign):
                if accessor is not None and (sub.value is None or not _mentions(sub.value, accessor)):
                    continue
                targets = [sub.target]
            elif isinstance(sub, (ast.For, ast.comprehension)):
                if accessor is not None:
                    continue
                targets = [sub.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _find_func(tree: ast.Module, name: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def read_keys(func_node: ast.AST | None, variables: set[str]) -> set[str]:
    """这些变量**在当前函数体内**读到的字面量键：`.get("k", ...)` 与 `["k"]`。

    必须限定作用域：`variables` 是一个跨函数的并集（`cfg` 在好几个函数里都叫 cfg），
    若整棵树扫，`loop.py` 里别的函数读的 `cfg.get("mumble_seconds")` 会被算到
    `self_evolution_cfg()` 头上，报出一堆"读了但没登记"的假阳性。
    """
    keys: set[str] = set()
    if func_node is None:
        return keys
    for node in ast.walk(func_node):
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
        reads = read_keys(_find_func(tree, func), variables)
        for rel_consumer, consumer_fn in consumers:
            cpath = ROOT / rel_consumer
            if not cpath.is_file():
                print(f"  !! 消费方文件不存在，跳过: {rel_consumer}")
                continue
            ctree = ast.parse(cpath.read_text(encoding="utf-8"), filename=str(cpath))
            cfunc = _find_func(ctree, consumer_fn)
            if cfunc is None:
                print(f"  !! 消费方函数不存在，跳过: {rel_consumer}::{consumer_fn}")
                continue
            variables |= cfg_variables(ctree, consumer_fn, accessor=func)
            reads |= read_keys(cfunc, variables)
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
