#!/usr/bin/env python
"""Static contract gate for the memory-assembly seam (AST, not regex).

``MemoryRetriever.build_context`` returns ``(block, ids, found)`` and
``MemoryManager.context_with_status`` returns ``(block, found)``. A call site
that binds the seam to a single name silently loses ``found`` -- and
``found=False`` is exactly what lets the persona inject its negative constraint
instead of confabulating a shared memory ("你还记得我们第一次聊天聊了什么吗"
-> inventing one). A regex cannot see that shape; this gate parses the AST.

What counts as losing ``found``:
  * any binding target (assignment, annotated assignment, ``for`` target,
    comprehension target) that does not unpack every element -- including a
    single name, a single attribute, or a single subscript target;
  * taking the result apart with a subscript or an attribute;
  * a bare statement that discards the result (production code only; see
    BARE_CALL_ALLOWED_* for benchmarks and stress tests that time the call).

The result may flow onward whole -- as a call argument, a container element, a
return value, an awaited or starred operand -- because nothing is dropped at
that site: ``assertEqual(build_context(q), ('', [], False))`` compares the full
tuple, so ``found`` is consumed. ``await`` and conditional expressions are
transparent wrappers: what matters is the target the value finally reaches.

Both definitions are pinned twice -- the return annotation AND the element
count of every ``return <tuple>`` -- so the arity cannot drift silently.

Run: python scripts/check_memory_contract.py [--self-test]
"""
from __future__ import annotations

import ast
import sys
import warnings
from pathlib import Path

# This gate parses every product .py file; unrelated lint warnings from those
# files (e.g. invalid escape sequences in docstrings) are not its business.
warnings.filterwarnings("ignore", category=SyntaxWarning)

ROOT = Path(__file__).resolve().parents[1]

# The contract: seam name -> number of tuple elements it returns.
SEAM_ARITY = {"build_context": 3, "context_with_status": 2}
# Where each seam is defined, so the gate can pin the return arity.
SEAM_DEFS = {
    "build_context": (ROOT / "server" / "memory" / "retriever.py", "build_context"),
    "context_with_status": (ROOT / "server" / "memory" / "manager.py", "context_with_status"),
}
# The single-exit delivery type. `found` must stay a NAMED field: that is what
# stops a caller dropping it positionally the way a bare tuple allows.
MEMORY_CONTEXT = (ROOT / "server" / "memory" / "retriever.py", "MemoryContext",
                  ("block", "used_ids", "found"))
# Scratch, vendored and generated trees are not product call sites. `data/`
# holds generated commission artifacts and `.agents/` the local skill bundle;
# a malformed generated .py there must not turn a product gate red.
EXCLUDE_DIRS = {".venv", "node_modules", ".git", "__pycache__", "tmp", "data",
                ".agents", ".claude", ".stepcode", "dist", "build"}
# Bare calls that discard the whole result are allowed only where timing the
# call is the point. Narrow by name, not by directory: scripts/ also holds
# run_evals.py and the one-click launcher, which are real exposure surfaces.
BARE_CALL_ALLOWED_PREFIXES = ("scripts/bench_",)
BARE_CALL_ALLOWED_EXACT = {"scripts/test_memory_concurrency.py",
                           "scripts/test_memory_fastloop.py"}

FIX_HINT = ("修法：把调用结果解包成全部元素；新调用点优先改用 deliver_context"
            "（它的 found 是命名字段，丢不掉）。")


# ---------------------------------------------------------------- helpers

def _seam_name(call: ast.Call) -> str | None:
    """The seam this call invokes, or None if it is not a seam call."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in SEAM_ARITY:
        return func.attr
    if isinstance(func, ast.Name) and func.id in SEAM_ARITY:
        return func.id
    return None


def _target_leaves(target: ast.expr) -> int | None:
    """How many values a binding target destructures.

    Nested tuples count their leaves (``a, (b, c)`` binds three), so a nested
    unpack of a three-tuple is complete. A starred element makes the count
    variable, reported as None.
    """
    if isinstance(target, (ast.Tuple, ast.List)):
        if any(isinstance(element, ast.Starred) for element in target.elts):
            return None
        return sum(_target_leaves(element) for element in target.elts)
    return 1


def _captures_all(target: ast.expr, want: int) -> bool:
    leaves = _target_leaves(target)
    return leaves is None or leaves == want


def _shown(target: ast.expr) -> str:
    leaves = _target_leaves(target)
    return "可变（含 * 展开）" if leaves is None else str(leaves)


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _flowing_seam_calls(value: ast.expr | None) -> list[tuple[str, ast.Call]]:
    """Seam calls whose value reaches a binding target directly.

    ``await`` and conditional expressions are transparent: ``block = call if c
    else y`` still binds the tuple to ``block``, so the target must unpack it.
    """
    if value is None:
        return []
    if isinstance(value, ast.Await):
        return _flowing_seam_calls(value.value)
    if isinstance(value, ast.IfExp):
        return _flowing_seam_calls(value.body) + _flowing_seam_calls(value.orelse)
    if isinstance(value, ast.Starred):
        return _flowing_seam_calls(value.value)
    if isinstance(value, ast.Call):
        name = _seam_name(value)
        return [(name, value)] if name else []
    return []


def _seam_calls_in(subtree: ast.AST) -> list[tuple[str, ast.Call]]:
    """Every seam call anywhere inside a subtree (a loop's iterable, say)."""
    found: list[tuple[str, ast.Call]] = []
    for node in ast.walk(subtree):
        if isinstance(node, ast.Call):
            name = _seam_name(node)
            if name:
                found.append((name, node))
    return found


def _bare_call_allowed(rel: str) -> bool:
    return (rel in BARE_CALL_ALLOWED_EXACT
            or rel.startswith(BARE_CALL_ALLOWED_PREFIXES))


# ---------------------------------------------------------------- checking

def check_tree(tree: ast.AST, rel: str) -> list[str]:
    """Every seam call in one module must hand ``found`` to a full unpacking."""
    parents = _parents(tree)
    handled: set[int] = set()
    problems: list[str] = []

    def require_full(target: ast.expr, want: int, name: str, lineno: int, shape: str) -> None:
        if not _captures_all(target, want):
            problems.append(
                f"{rel}:{lineno}: {name} 返回 {want} 项，{shape}只解包 "
                f"{_shown(target)} 项——found 被静默丢弃")

    # --- bindings: assignment, annotated assignment, loop and comprehension ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for name, call in _flowing_seam_calls(node.value):
                for target in node.targets:
                    require_full(target, SEAM_ARITY[name], name, target.lineno, "赋值目标")
                handled.add(id(call))
        elif isinstance(node, ast.AnnAssign):
            for name, call in _flowing_seam_calls(node.value):
                require_full(node.target, SEAM_ARITY[name], name, node.target.lineno,
                             "注解赋值目标")
                handled.add(id(call))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for name, call in _seam_calls_in(node.iter):
                require_full(node.target, SEAM_ARITY[name], name, node.target.lineno, "循环目标")
                handled.add(id(call))
        elif isinstance(node, ast.comprehension):
            for name, call in _seam_calls_in(node.iter):
                require_full(node.target, SEAM_ARITY[name], name, node.target.lineno,
                             "推导式目标")
                handled.add(id(call))

    # --- everything else: taken apart, discarded, or flows onward whole ---
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in handled:
            continue
        name = _seam_name(node)
        if name is None:
            continue
        parent = parents.get(id(node))
        if isinstance(parent, (ast.Subscript, ast.Attribute)) and parent.value is node:
            how = "下标" if isinstance(parent, ast.Subscript) else "取属性"
            problems.append(
                f"{rel}:{node.lineno}: {name} 的结果被{how}拆开——其余项（含 found）被丢弃")
        elif isinstance(parent, ast.Expr) and parent.value is node:
            if not _bare_call_allowed(rel):
                problems.append(
                    f"{rel}:{node.lineno}: {name} 裸调用丢弃结果——found 被静默丢弃")
    return problems


def scan_source(source: str, rel: str) -> tuple[list[str], int]:
    """Check one source string. Returns (violations, seam-call count)."""
    tree = ast.parse(source)
    return check_tree(tree, rel), len(_seam_calls_in(tree))


def check_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        line = getattr(exc, "lineno", 0) or 0
        return [f"{rel}:{line}: 无法解析（{type(exc).__name__}），契约门无法覆盖此文件"]
    return check_tree(tree, rel)


def _annotation_arity(node: ast.expr | None) -> int | None:
    """Element count of a fixed ``tuple[...]`` return annotation."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Tuple):
        return len(node.slice.elts)
    return None


def check_definitions() -> list[str]:
    """Pin both seams -- annotation AND actual returns -- so arity cannot drift."""
    problems: list[str] = []
    for name, (path, func) in SEAM_DEFS.items():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != func:
                continue
            arity = _annotation_arity(node.returns)
            if arity != SEAM_ARITY[name]:
                problems.append(
                    f"{rel}:{node.lineno}: {func} 的返回注解是 {arity} 项，"
                    f"契约钉住 {SEAM_ARITY[name]} 项——接缝漂移")
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Tuple):
                    got = len(sub.value.elts)
                    if got != SEAM_ARITY[name]:
                        problems.append(
                            f"{rel}:{sub.lineno}: {func} 实际 return {got} 项，"
                            f"契约钉住 {SEAM_ARITY[name]} 项——接缝漂移")
            break
        else:
            problems.append(f"{rel}: 找不到 {func} 定义，契约门失去锚点")
    return problems


def check_memory_context() -> list[str]:
    """Pin the single-exit delivery type so ``found`` stays a named field."""
    path, name, required = MEMORY_CONTEXT
    rel = path.relative_to(ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            fields = {statement.target.id for statement in node.body
                      if isinstance(statement, ast.AnnAssign)
                      and isinstance(statement.target, ast.Name)}
            missing = [field for field in required if field not in fields]
            if missing:
                return [f"{rel}:{node.lineno}: {name} 缺少字段 {missing}——"
                        "found 必须保持为命名字段，否则调用方可能静默丢掉它"]
            return []
    return [f"{rel}: 找不到 {name} 定义，契约门失去锚点"]


def product_files() -> list[Path]:
    return sorted(
        path for path in ROOT.rglob("*.py")
        if not any(part in EXCLUDE_DIRS for part in path.parts))


# ---------------------------------------------------------------- self-test

# (source, pseudo path, expect_clean) -- the gate must judge each correctly.
SELF_TEST_CASES = [
    # complete unpackings, including nested and starred
    ("block, ids, found = r.build_context(q)", "server/x.py", True),
    ("block, found = m.context_with_status(q)", "server/x.py", True),
    ("[block, ids, found] = r.build_context(q)", "server/x.py", True),
    ("block, *rest = r.build_context(q)", "server/x.py", True),
    ("a, (b, c) = r.build_context(q)", "server/x.py", True),
    # single-value capture, whatever the target's shape
    ("block = r.build_context(q)", "server/x.py", False),
    ("x = y = r.build_context(q)", "server/x.py", False),
    ("self.ctx = r.build_context(q)", "server/x.py", False),
    ("d['k'] = r.build_context(q)", "server/x.py", False),
    ("block: tuple = r.build_context(q)", "server/x.py", False),
    # partial unpacking
    ("block, ids = r.build_context(q)", "server/x.py", False),
    ("block, found = r.build_context(q)", "server/x.py", False),
    ("block, ids, found = m.context_with_status(q)", "server/x.py", False),
    # taken apart
    ("v = r.build_context(q)[0]", "server/x.py", False),
    ("v = r.build_context(q).block", "server/x.py", False),
    # transparent wrappers still bind the tuple to the target
    ("block = r.build_context(q) if c else y", "server/x.py", False),
    ("block = await r.build_context(q)", "server/x.py", False),
    ("async def f():\n    block, ids = await r.build_context(q)", "server/x.py", False),
    # loop and comprehension targets: a one-element iterable still binds the
    # whole tuple to a single name, which is the same single-value capture.
    ("for block in [r.build_context(q)]:\n    pass", "server/x.py", False),
    ("for block, ids in [r.build_context(q)]:\n    pass", "server/x.py", False),
    ("for block, ids, found in [r.build_context(q)]:\n    pass", "server/x.py", True),
    ("[(block, ids) for block, ids in [r.build_context(q)]]", "server/x.py", False),
    # the result flows onward whole: nothing is dropped at this site
    ("assertEqual(r.build_context(q), ('', [], False))", "server/x.py", True),
    ("print(r.build_context(q))", "server/x.py", True),
    ("rows.append(m.context_with_status(q))", "server/x.py", True),
    ("def f():\n    return r.build_context(q)", "server/x.py", True),
    ("d[r.build_context(q)] = 1", "server/x.py", True),
    ("patch.object(r, 'build_context', return_value=('', [], False))", "server/x.py", True),
    # bare discard: production fails, timing harnesses pass
    ("r.build_context(q)", "server/x.py", False),
    ("m.context_with_status(q)", "server/x.py", False),
    ("r.build_context(q)", "scripts/bench_voice_loop.py", True),
    ("r.build_context(q)", "scripts/test_memory_fastloop.py", True),
    ("r.build_context(q)", "scripts/run_evals.py", False),
]


def self_test() -> int:
    failures = judged = 0
    for source, rel, expect_clean in SELF_TEST_CASES:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            print(f"  SELF-TEST SKIP {rel}: {source!r} 不是可解析片段")
            continue
        judged += 1
        problems = check_tree(tree, rel)
        if bool(problems) == expect_clean:
            print(f"  SELF-TEST FAIL {rel}: {source!r} -> "
                  f"{'flagged' if problems else 'clean'}，预期 "
                  f"{'flagged' if not expect_clean else 'clean'}")
            failures += 1
    if failures:
        print(f"FAIL 自检有 {failures}/{judged} 个用例判定错误——契约门本身不可信")
        return 1
    print(f"PASS 自检 {judged} 个用例判定全部正确（完整/嵌套/星号解包、单值捕获、"
          "少目标、下标与取属性、await 与条件表达式、循环与推导式目标、"
          "整包外传、裸调用豁免）")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    problems = check_definitions()
    problems.extend(check_memory_context())
    files = product_files()
    for path in files:
        problems.extend(check_file(path))
    if problems:
        print(f"FAIL 记忆装配接缝契约有 {len(problems)} 处违例：")
        for line in problems:
            print("  " + line)
        print("\n" + FIX_HINT)
        return 1
    print(f"PASS 记忆装配接缝契约成立：{len(files)} 个文件中 "
          "build_context(3)/context_with_status(2) 的绑定点全部完整解包，接缝定义未漂移")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
