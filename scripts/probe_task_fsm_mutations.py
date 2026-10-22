# -*- coding: utf-8 -*-
"""变异探针：把 server/task 的实现逐条改坏，确认 scripts/test_task_fsm.py 会红。

    & "D:/project/neurosama/.venv/Scripts/python.exe" scripts/probe_task_fsm_mutations.py

每条探针：
  1. 校验目标文件里真的存在要被替换的原文（找不到 = 探针失效，直接判失败）
  2. 备份原字节，替换，跑测试，收集失败断言
  3. finally 还原原字节，并复查还原后测试重新变绿
  4. 打印「哪条断言红了」

探针必须让测试红，且必须命中预期的断言。做不到就算探针无效，整体非零退出。
本脚本只改 server/task/ 内部实现，不改测试、不改任何其他文件。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
TEST = ROOT / "scripts" / "test_task_fsm.py"
TASK = ROOT / "server" / "task"


def run_test() -> tuple[int, list[str]]:
    proc = subprocess.run([str(PY), str(TEST)], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)
    failures = [line.strip() for line in (proc.stdout or "").splitlines()
                if line.strip().startswith("FAIL ")]
    return proc.returncode, failures


def apply_probe(name: str, filename: str, old: str, new: str,
                expect: str | tuple[str, ...]) -> bool:
    path = TASK / filename
    original = path.read_text(encoding="utf-8")
    if old not in original:
        print(f"  INVALID 探针失效：{filename} 里找不到要替换的原文")
        print(f"           原文片段: {old[:90]!r}")
        return False
    mutated = original.replace(old, new, 1)
    if mutated == original:
        print(f"  INVALID 探针失效：替换后没有变化")
        return False
    path.write_text(mutated, encoding="utf-8")
    try:
        code, failures = run_test()
    finally:
        path.write_text(original, encoding="utf-8")

    if code == 0:
        print(f"  INVALID 实现被改坏但测试仍然全绿（测试没有覆盖这条规则）")
        return False
    expects = (expect,) if isinstance(expect, str) else tuple(expect)
    missing = [e for e in expects if not any(e in f for f in failures)]
    if missing:
        print(f"  INVALID 变红了，但这些预期断言没红：{missing!r}")
        for f in failures[:6]:
            print(f"           红: {f}")
        return False
    hit = [f for f in failures if any(e in f for e in expects)]
    print(f"  ok      {name}")
    print(f"          变红断言（{len(failures)} 条，命中预期 {len(hit)} 条 / 期望 {len(expects)} 项）：")
    for f in hit[:6]:
        print(f"            - {f}")
    return True


def main() -> int:
    print("[0] 还原后基线必须全绿")
    code, failures = run_test()
    if code != 0:
        print(f"  FAIL 基线就是红的，先修实现再打探针（{len(failures)} 条失败）")
        for f in failures[:10]:
            print("   -", f)
        return 1
    print("  ok   基线全绿")

    probes = build_probes()
    print(f"\n[1] 逐条打探针（{len(probes)} 条）")
    good = 0
    for name, filename, old, new, expect in probes:
        print(f"\n- {name}")
        if apply_probe(name, filename, old, new, expect):
            good += 1

    print("\n[2] 全部还原后复查")
    code, failures = run_test()
    restored = code == 0
    print(f"  {'ok   ' if restored else 'FAIL '}还原后测试{'全绿' if restored else f'仍红（{len(failures)} 条）'}")

    print()
    print("=" * 72)
    ok = good == len(probes) and restored
    print(f"{'PASS' if ok else 'FAILED'}  探针 {good}/{len(probes)} 有效；还原{'成功' if restored else '失败'}")
    return 0 if ok else 1


def build_probes() -> list[tuple[str, str, str, str, str | tuple[str, ...]]]:
    return [
        (
            "P1 T24 的 Step 守卫恒真（允许带未取消 Step 直接结单，违反 §4.4 T24 守卫）",
            "machine.py",
            "if facts.open_steps_remaining != 0:",
            "if facts.open_steps_remaining < 0:",
            "SM-16 有 Step 未取消时不能结单",
        ),
        (
            "P2 T16 不递增 attempt（验收打回次数永不消耗，§4.7 的重试上限失效）",
            "machine.py",
            '"attempt": state.attempt + 1',
            '"attempt": state.attempt + 0',
            "T16 attempt 递增到 1",
        ),
        (
            "P3 S6 放行越界路径（../../etc/passwd 被当成合法产出物，违反 §4.10 SM-08）",
            "paths.py",
            "    if _TRAVERSAL.search(text):",
            "    if False:",
            "SM-08 越界路径被拒",
        ),
        (
            "P4 签名归一化不再折叠纯数字（同种错误换个数字就变成新签名）",
            "failures.py",
            "_NUMBER = re.compile(r\"\\b\\d+\\b\")",
            "_NUMBER = re.compile(r\"\\b(?!)x+\\b\")",
            "normalize 去数字",
        ),
        (
            "P5 T24 通配不再排除终态（取消可以从 DELIVERED 再取消一次）",
            "machine.py",
            "sources = tuple(item for item in cls.STATES if item not in cls.TERMINAL)",
            "sources = tuple(item for item in cls.STATES)",
            "Task 终态",
        ),
        (
            "P6 未知派发证据被当成可安全重放（§4.8 的 UNKNOWN 兜底被绕过）",
            "replay.py",
            "    return REPLAY_UNKNOWN",
            "    return REPLAY_SAME_KEY",
            "UNKNOWN",
        ),
        (
            # T20 的反向证据：把「只取 done」放宽成「所有记录都算成功」，
            # 失败记录就会被当成成功。期望侧（done+failed 仍应放行）不红，
            # 拒绝侧（只有 failed / 只有 replayed）必须红。
            "P7 T20 守卫不读 status（把所有记录都算成功）",
            "machine.py",
            'succeeded = [record for record in matched if record.status == "done"]',
            "succeeded = list(matched)",
            # 双向：两侧都必须红。只锁「失败当成功」而漏掉「正常重试被挡」，
            # 等于这次修复只做了一半。
            ("一条 done + 一条 failed", "只有一条 failed"),
        ),
        (
            # 第二种改坏法：status 比对写错大小写，等于永远不匹配 done。
            "P8 T20 status 比对写成大写（永远匹配不到 done）",
            "machine.py",
            'succeeded = [record for record in matched if record.status == "done"]',
            'succeeded = [record for record in matched if record.status == "DONE"]',
            "T20 允许：恰好一条 done",
        ),
    ]


if __name__ == "__main__":
    sys.exit(main())
