# -*- coding: utf-8 -*-
"""门禁沙盘泄漏棘轮：会漏 tmp/ 的门禁当场红。

**为什么需要它**：plan-45 发现四个浏览器门禁（drill_network / drill_badkey /
drill_ws_reconnect / step8_e2e_browser）每次跑都在 `tmp/` 留带时间戳的沙盘目录，
`finally` 只关浏览器和杀服务进程、从不删目录——全量跑一次净增 3-5 个，把
`tmp_sweep` 的计数棘轮长期顶红。修完之后（plan-45）没有常驻保护：下次有人新加
一个会漏沙盘的门禁，或那四个的修复被回归，不会有任何测试红。

plan-118 用差分法实测全部门禁，确认除一次偶然采样外零泄漏、那四处修复无回归
——但那是快照，差分脚本用完即删。

**本棘轮把差分法变成常驻资产**：跑「最可能漏」的那批门禁，每次前后量 `tmp/`
条目数，净增 > 0 即红。

**判据用差分不用绝对值**：绝对值会随 `tmp/` 自然增长假红（`tmp_sweep` 自己的
教训），差分把「自然已有多少」约掉，只对「这个门禁新建了多少」敏感。

**抽样集合动态选，不写死清单**：按 GATES 里 cmd 或门禁名命中
`browser|drill|e2e|shake|step8` 选出——plan-45 的四个全在这几类，plan-118 也
确认只有它们曾在 `tmp/` 建沙盘。新门禁若属于这几类会自动进抽样，不靠人记得加。

**跳过而非假红**：8000 未起时浏览器门禁会 SKIP 并退 0，那不是泄漏；跑不起来
（退出码 127 / 命令不存在）也跳过。只在「门禁真的跑了且 tmp/ 净增」时判红。

**门禁自己红了 ≠ 泄漏**（2026-11-09）：抽样门禁失败时会按设计把沙盘留下供调试，
此前这会被打印成「LEAK 漏沙盘」，把排查方向从「哪条断言红了」带偏。现在分两种
结论：`LEAK`（退 0 但 tmp/ 净增）与 `FAIL 门禁本身失败`（退非 0，附输出末几行），
两者都让棘轮红，但说法不同。反向的静默通过也一并堵上：门禁失败却顺手清干净了，
以前判 ok，现在判 FAIL。

**缺依赖物不算泄漏**（2026-11-09）：`pixel-world/dist` 这类 gitignore 产物不在时，
step8_e2e_browser 会自报 SKIP（退 0）且不建沙盘；这里也用 run_gates 的
`missing_artifact` 提前判掉，直接记 skip、不跑、更不计进差分。此前它 FAIL 后
按设计留沙盘，棘轮把「没构建」报成「泄漏」，干净机器全量必红。

Run: .venv/Scripts/python.exe -B scripts/test_gate_tmp_leak.py
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = ROOT / "tmp"
SANDBOX_PATTERN = re.compile(r"browser|drill|e2e|shake|step8", re.I)
MAX_GATES = 10          # 抽样上限：跑太多会让本棘轮自己变慢
PER_GATE_TIMEOUT = 120


def classify(code: int, delta: int, output: str) -> tuple[str, str]:
    """单条抽样门禁的裁决：ok / skip / leak / gate_failed。

    为什么要分开（2026-11-09）：此前只看 `delta > 0`，于是「门禁自己失败、按设计
    把沙盘留下供调试」会被打印成「LEAK 漏沙盘」—— 那是错的结论，排查方向从
    「哪条断言红了」被带偏到「谁没删目录」。反过来「门禁失败但顺手清干净了」
    又被判 ok，是个静默通过。两种都报。
    """
    if code == 127:
        return "skip", "退出码 127（命令不存在）"
    if code == 0 and re.search(r"^\s*SKIP", output, re.M):
        return "skip", "自报 SKIP"
    if code != 0:
        return "gate_failed", f"退出码 {code}"
    if delta > 0:
        return "leak", f"tmp/ 净增 {delta} 项"
    return "ok", "无净增"


def _tail(output: str, lines: int = 3) -> str:
    kept = [line for line in output.splitlines() if line.strip()][-lines:]
    return " / ".join(kept)[:300]


def _load_gates() -> tuple[dict, object]:
    """复用 run_gates 的 GATES 与依赖物判定，避免两处口径漂移。"""
    spec = importlib.util.spec_from_file_location(
        "run_gates_for_leak_scan", ROOT / "scripts" / "run_gates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GATES, mod.missing_artifact


def _tmp_count() -> int:
    return len(list(TMP.iterdir()))


def _pick_suspects(gates: dict) -> list[tuple[str, dict]]:
    """挑出最可能漏沙盘的门禁（cmd 或名字命中 sandbox 形态）。"""
    picked = []
    for name, spec in sorted(gates.items()):
        cmd = [str(a) for a in (spec.get("cmd") or [])]
        if not cmd:
            continue
        joined = " ".join(cmd) + " " + name
        if SANDBOX_PATTERN.search(joined):
            picked.append((name, spec))
    return picked[:MAX_GATES]


def main() -> int:
    gates, missing_artifact = _load_gates()
    suspects = _pick_suspects(gates)
    if not suspects:
        print("[gate_tmp_leak] 没选出抽样门禁，跳过（不是失败）")
        return 0

    print(f"[gate_tmp_leak] 抽样 {len(suspects)} 个最可能漏沙盘的门禁")
    leaked: list[tuple[str, int]] = []
    skipped: list[str] = []
    failed_gates: list[tuple[str, str, str]] = []

    for name, spec in suspects:
        # 依赖物缺失（如 pixel-world/dist 未构建）：整条门禁这轮不跑，也不计差分。
        absent = missing_artifact(spec)
        if absent is not None:
            _, rel, hint = absent
            skipped.append(f"{name}(缺 {rel})")
            print(f"  skip {name}: 缺 {rel}，不建沙盘；补救：{hint}")
            continue
        cmd = [str(a) for a in (spec.get("cmd") or [])]
        before = _tmp_count()
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                                  timeout=PER_GATE_TIMEOUT)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            skipped.append(f"{name}(超时)")
            continue
        except (FileNotFoundError, NotADirectoryError):
            skipped.append(f"{name}(命令不存在)")
            continue
        after = _tmp_count()
        delta = after - before
        output = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")

        verdict, detail = classify(code, delta, output)
        if verdict == "skip":
            skipped.append(f"{name}({detail})")
            print(f"  skip {name}: {detail}，不计泄漏")
        elif verdict == "gate_failed":
            # 门禁自己红了：沙盘按设计留着供调试，这不是「泄漏」，也不该冒充「ok」。
            failed_gates.append((name, detail, _tail(output)))
            print(f"  FAIL {name}: 门禁本身失败（{detail}），不判为泄漏")
            print(f"       | {_tail(output)}")
        elif verdict == "leak":
            leaked.append((name, delta))
            print(f"  LEAK {name}: tmp/ 净增 {delta} 项（{time.time() - started:.1f}s）")
        else:
            print(f"  ok   {name}: 无净增（{time.time() - started:.1f}s）")

    if skipped:
        print(f"  skip {', '.join(skipped)}")

    if failed_gates:
        print(f"\n=== {len(failed_gates)} 个抽样门禁自己失败（不是泄漏）===")
        for name, detail, tail in failed_gates:
            print(f"  {name}: {detail}")
            print(f"       | {tail}")
        print("\n这些门禁红了要单独查；沙盘按设计保留，本棘轮不把它算进差分。")

    if leaked:
        print(f"\n=== {len(leaked)} 个门禁漏沙盘 ===")
        for name, delta in leaked:
            print(f"  {name}: +{delta}")
        print("\n修法参考（plan-45）：沙盘目录在 finally 里删，且只在通过时删——")
        print("失败时留沙盘供调试。删除前等服务进程真退出，否则 Windows 上 EBUSY。")

    if failed_gates or leaked:
        return 1

    print(f"\n=== 抽样 {len(suspects)} 个门禁全部无 tmp/ 净增 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
