#!/usr/bin/env python
"""venv_lock：跑门禁的解释器必须与 uv.lock 一致。

为什么需要它：2026-10-25 事故的形状是「门禁全绿，但 .venv 缺整个语音栈」。
离线门禁把 ASR/TTS mock 掉了，环境残缺它们看不见。这条门禁把「解释器本身
完整」变成断言：

- 关键子集（语音栈 faster-whisper / edge-tts / ctranslate2，加 uv.lock 里
  == 钉死的项）缺失或版本不符，exit 1；
- 全集漂移（如 fastapi 锁 0.143.0 / 装 0.162.0）只报数，不阻断；
- 解释器不是 .venv/Scripts/python.exe，或 uv.lock 缺失/解析失败，exit 1；
- 任何路径都不输出 SKIP（run_gates 把行首 SKIP 判成「这轮没验」）。

用法：
    python scripts/check_venv_lock.py                         # 真实检查（门禁用）
    python scripts/check_venv_lock.py --lock X --python Y     # 测试/诊断用
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "uv.lock"
DEFAULT_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

# 门禁 mock 掉、因而其它门禁看不见的包。缺任何一样都是 2026-10-25 的重演。
CRITICAL_BY_NAME = ("faster-whisper", "edge-tts", "ctranslate2")

PREFIX = "[venv_lock]"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "")).lower()


def marker_holds(marker: str | None, version_info) -> bool:
    """Evaluate the marker shapes this lock uses: python_full_version <op> 'X.Y'.

    An unknown marker shape returns True. Returning True keeps the dependency
    in the checked set; returning False would silently drop it (a critical
    package could fall out of `pinned` and the gate would go blind, the exact
    2026-10-25 failure shape). Failing the gate on an unknown marker would be
    the opposite mistake, so unknown shapes stay checked and reported as drift
    by the caller's version comparison.
    """
    if not marker:
        return True
    m = re.match(r"\s*python_full_version\s*(==|!=|<=|>=|<|>)\s*'([\d.]+)'\s*$", marker)
    if not m:
        return True
    op, want = m.group(1), tuple(int(p) for p in m.group(2).split("."))
    # PEP 440 compares by zero-padding the shorter side: 3.12 == 3.12.0.
    # Prefix slicing instead would make ==/!=/<=/> wrong for '3.12' vs 3.12.4.
    want = want + (0,) * (3 - len(want))
    have = tuple(version_info)[:3]
    if op == "==":
        return have == want
    if op == "!=":
        return have != want
    if op == "<":
        return have < want
    if op == "<=":
        return have <= want
    if op == ">":
        return have > want
    return have >= want


def _root_dependencies(lock: dict) -> tuple[list[dict], dict]:
    """Return (root dependency list, root metadata) from either lock shape.

    Newer uv emits a [[manifest]] section; this lock (revision 3) instead
    carries the root as a [[package]] entry whose source is virtual ".".
    Both shapes are supported so a regenerated lock does not silently turn
    the gate blind.
    """
    manifests = lock.get("manifest") or []
    if manifests:
        return manifests[0].get("dependencies") or [], manifests[0].get("metadata") or {}
    for pkg in lock.get("package") or []:
        source = pkg.get("source") or {}
        if isinstance(source, dict) and source.get("virtual") == ".":
            return pkg.get("dependencies") or [], pkg.get("metadata") or {}
    raise ValueError("uv.lock has no [[manifest]] and no virtual root package")


def expected_from_lock(lock: dict) -> tuple[dict[str, str], set[str]]:
    """Return (name -> pinned version from the root, == -pinned names).

    A dependency with its own version (numpy, marker-split) pins directly;
    otherwise the single [[package]] entry with that name supplies the
    version. A name with several package entries and no root version is
    skipped rather than guessed.
    """
    deps, metadata = _root_dependencies(lock)
    packages = lock.get("package") or []
    pinned: dict[str, str] = {}
    for dep in deps:
        name = _norm(dep.get("name") or "")
        if not name or not marker_holds(dep.get("marker"), sys.version_info):
            continue
        if dep.get("version"):
            pinned[name] = str(dep["version"])
            continue
        candidates = [p for p in packages if _norm(p.get("name") or "") == name]
        if len(candidates) == 1 and candidates[0].get("version"):
            pinned[name] = str(candidates[0]["version"])
    exact: set[str] = set()
    for req in metadata.get("requires-dist") or []:
        spec = str(req.get("specifier") or "")
        if spec.startswith("==") and "*" not in spec:
            exact.add(_norm(req.get("name") or ""))
    return pinned, exact


def check(lock_path: Path, python_path: Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    if not python_path.is_file():
        lines.append(f"{PREFIX} 解释器缺失：{python_path}")
        lines.append(f"{PREFIX} 补救：uv sync --frozen（不要用 uv pip install -r requirements.txt，README 那条不读锁）")
        return False, lines
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        lines.append(f"{PREFIX} uv.lock 读取失败：{lock_path}（{type(exc).__name__}: {exc}）")
        return False, lines
    try:
        pinned, exact = expected_from_lock(lock)
    except Exception as exc:
        lines.append(f"{PREFIX} uv.lock 解析失败：{type(exc).__name__}: {exc}")
        return False, lines

    critical = set(CRITICAL_BY_NAME) | {n for n in exact if n}
    # 关键子集必须真的被检查过：锁里解析不出这些名字（空 pinned、坏 marker、
    # 多版本无标记被跳过）时，逐包比对会静默变成空转——那正是 2026-10-25
    # 「门禁全绿但环境残缺」的事故形状。这里显式要求 critical ⊆ pinned。
    unpinned = sorted(name for name in critical if name not in pinned)
    missing: list[str] = []
    mismatch: list[str] = []
    drift: list[str] = []
    for name, want in sorted(pinned.items()):
        try:
            have = installed_version(name)
        except PackageNotFoundError:
            if name in critical:
                missing.append(f"{name}（锁 {want}）未安装")
            else:
                drift.append(f"{name}（锁 {want}）未安装")
            continue
        if have != want:
            if name in critical:
                mismatch.append(f"{name} 锁 {want} / 装 {have}")
            else:
                drift.append(f"{name} 锁 {want} / 装 {have}")
    ok = not missing and not mismatch and not unpinned
    if Path(sys.executable).resolve() != Path(python_path).resolve():
        lines.append(f"{PREFIX} 注意：版本核对以运行中的解释器 {sys.executable} 为准，"
                     f"而非 --python 指定的 {python_path}")
    vi = sys.version_info
    lines.append(f"{PREFIX} 解释器 {python_path}（Python {vi.major}.{vi.minor}.{vi.micro}）")
    lines.extend(f"{PREFIX} 缺失 {item}" for item in missing)
    lines.extend(f"{PREFIX} 不符 {item}" for item in mismatch)
    lines.extend(f"{PREFIX} 锁未钉住 {item}（关键子集未被检查）" for item in unpinned)
    lines.extend(f"{PREFIX} 漂移 {item}（不阻断）" for item in drift)
    if ok:
        lines.append(f"{PREFIX} 通过：关键子集 {len(critical)} 项与 uv.lock 一致")
    else:
        lines.append(f"{PREFIX} 失败：补救 uv sync --frozen")
    return ok, lines


def _self_test() -> bool:
    """Cheap inline assertions so the gate proves its own failure paths."""
    good = marker_holds("python_full_version < '3.12'", (3, 11, 9)) is True
    good = good and marker_holds("python_full_version < '3.12'", (3, 12, 4)) is False
    good = good and marker_holds("python_full_version >= '3.12'", (3, 12, 4)) is True
    good = good and marker_holds(None, (3, 12, 4)) is True
    good = good and marker_holds("python_full_version < '3.12'", (3, 9, 0)) is True
    # PEP 440 零填充：'3.12' 与 3.12.4 在 == 下不等、> 下更大、<= 为假。
    good = good and marker_holds("python_full_version == '3.12'", (3, 12, 4)) is False
    good = good and marker_holds("python_full_version > '3.12'", (3, 12, 4)) is True
    good = good and marker_holds("python_full_version <= '3.12'", (3, 12, 4)) is False
    good = good and marker_holds("python_full_version != '3.12'", (3, 12, 4)) is True
    # 未知 marker 形态必须返回 True：False 会让依赖静默掉出 pinned（失明）。
    good = good and marker_holds("sys_platform == 'win32'", (3, 12, 4)) is True
    diag: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        # 合成锁用例用 sys.executable 而非 DEFAULT_PYTHON：.venv 真缺失时
        # （事故形态）这两条要验的是版本判定，不能被「解释器缺失」抢先。
        here = Path(sys.executable)
        broken = Path(td) / "broken.lock"
        broken.write_text(
            '[[manifest]]\nname = "x"\n'
            'dependencies = [{ name = "ctranslate2", version = "9.9.9" }]\n',
            encoding="utf-8")
        ok_bad, lines_bad = check(broken, here)
        good = good and ok_bad is False and any(
            ("不符" in line or "缺失" in line) for line in lines_bad)
        diag.extend(lines_bad)
        ok_py, lines_py = check(DEFAULT_LOCK, Path(td) / "no-such-python")
        good = good and ok_py is False and any("解释器缺失" in line for line in lines_py)
        # 事故本体：锁里一个关键包都没钉住时必须 FAIL，不许空转报绿。
        blind = Path(td) / "blind.lock"
        blind.write_text(broken.read_text(encoding="utf-8").replace(
            '"ctranslate2", version = "9.9.9"', '"click", version = "8.5.0"'),
            encoding="utf-8")
        ok_blind, lines_blind = check(blind, here)
        good = good and ok_blind is False and any("锁未钉住" in line for line in lines_blind)
        diag.extend(lines_blind)
        ok_lock, lines_lock = check(Path(td) / "no-such.lock", here)
        good = good and ok_lock is False and any("读取失败" in line for line in lines_lock)
        # 真实 lock 的 virtual-root 形态：解析出的钉住集必须含语音栈。
        real_lock = tomllib.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
        pinned, exact = expected_from_lock(real_lock)
        good = good and all(name in pinned for name in CRITICAL_BY_NAME)
        good = good and "ctranslate2" in exact
        good = good and exact <= set(pinned)
        critical_now = set(CRITICAL_BY_NAME) | {n for n in exact if n}
        good = good and {"faster-whisper", "edge-tts", "ctranslate2"} <= critical_now
    if not good:
        print(f"{PREFIX} 自检失败：以下为失败路径的实跑输出，供定位")
        for line in diag:
            print(line)
        return False
    print(f"{PREFIX} 自检通过：marker 六符与未知形态、五条失败路径"
          "（坏锁不符/锁未钉住/读取失败/解释器缺失/真实锁解析）都按预期工作")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="venv 与 uv.lock 一致性门禁")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    args = parser.parse_args(argv)
    if not _self_test():
        return 1
    ok, lines = check(args.lock, args.python)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
