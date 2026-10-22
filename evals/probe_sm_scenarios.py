# -*- coding: utf-8 -*-
"""变异探针：证明 evals/task_fsm_sm_scenarios.py 的 18 条场景真的在守东西。

    $env:PYTHONIOENCODING="utf-8"
    & "D:/project/neurosama/.venv/Scripts/python.exe" evals/probe_sm_scenarios.py

做法与 scripts/probe_task_fsm_mutations.py 一致：逐条把 server/task 的实现改坏，
跑场景文件收集失败断言，finally 还原原字节并复查还原后重新全绿。

这一轮探的不是「断言在不在」，而是「场景这一层有没有独立价值」。
逐边断言（test_task_fsm.py）已经守过这些不变量了；场景要证明的是：
**把到达 Given 的那条迁移或守卫改坏，场景也会红**——否则场景就只是逐边的复读。

探针清单
--------
P1  同签名不再直接 block（retry.py 的 current == previous 分支恒假）
    -> 打中的应是 SM-05（第二次同签名直接 BLOCKED，不消耗第 3 次）
P2  T18 不再要求 human_required_count=0（放行带 human 标准的验收）
    -> 打中的应是 SM-17（不直接 ACCEPTED）
P3  deliver() 不再因幂等跳过执行器（seen_done 恒空）
    -> 打中的应是 SM-11（第二次不产生第二个副作用）

跑之前确认实现已停止写入：探针靠「改坏 -> 跑 -> 还原」工作，若同时有别人在写
被探的文件，会出现假阴（探针效果被覆盖，实测过一次 4/6）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 用当前解释器重跑，不假设 .venv 的位置——worktree 没有自己的 venv，
# 硬编码路径在这里必然 FileNotFoundError。
PY = Path(sys.executable)
EVAL = ROOT / "evals" / "task_fsm_sm_scenarios.py"
TASK = ROOT / "server" / "task"


def run_eval() -> tuple[int, list[str]]:
    proc = subprocess.run([str(PY), str(EVAL)], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)
    failures = [line.strip() for line in (proc.stdout or "").splitlines()
                if line.strip().startswith("FAIL ")]
    return proc.returncode, failures


def apply_probe(name: str, filename: str, old: str, new: str, expect: str) -> bool:
    path = TASK / filename
    # 字节级读写：文本模式会把 CRLF 转成 LF，还原后 git status 仍显示 M，
    # 看起来像「探针留了改动」，实际只是行尾变了。字节级还原才是逐字节一致。
    original = path.read_bytes()
    text = original.decode("utf-8")
    if old not in text:
        print(f"  INVALID 探针失效：{filename} 里找不到要替换的原文")
        print(f"           原文片段: {old[:90]!r}")
        return False
    mutated = text.replace(old, new, 1)
    if mutated == text:
        print(f"  INVALID 探针失效：替换后没有变化")
        return False
    path.write_bytes(mutated.encode("utf-8"))
    try:
        code, failures = run_eval()
    finally:
        path.write_bytes(original)

    if code == 0:
        print(f"  INVALID 实现被改坏但场景仍然全绿（场景没有覆盖这条规则）")
        return False
    hit = [f for f in failures if expect in f]
    if not hit:
        print(f"  INVALID 变红了，但红的不是预期场景（期望含 {expect!r}）")
        for f in failures[:6]:
            print(f"           红: {f}")
        return False
    print(f"  ok      {name}")
    print(f"          变红断言（{len(failures)} 条，命中预期 {len(hit)} 条）：")
    for f in hit[:3]:
        print(f"            - {f}")
    return True


PROBES = (
    (
        "P1 同签名不再直接 block（SM-05 的 Given 被破坏）",
        "retry.py",
        "    if current and current == previous:",
        "    if False and current and current == previous:",
        "SM-05",
    ),
    (
        "P2 T18 不再拦 human 标准（SM-17 的前半被破坏）",
        "machine.py",
        "    if facts.human_required_count > 0:",
        "    if False and facts.human_required_count > 0:",
        "SM-17",
    ),
    (
        "P3 deliver() 不再因幂等跳过执行器（SM-11 的核心被破坏）",
        "delivery.py",
        '        record.idempotency_key for record in records if record.status == "done"',
        '        record.idempotency_key for record in records if False and record.status == "done"',
        "SM-11",
    ),
)


def main() -> int:
    print("[0] 还原后基线必须全绿")
    code, failures = run_eval()
    if code != 0:
        print(f"  INVALID 基线就不是全绿，先修场景再跑探针：{failures[:3]}")
        return 1
    print("  ok   基线全绿")

    print(f"\n[1] 逐条打探针（{len(PROBES)} 条）")
    ok = 0
    for name, filename, old, new, expect in PROBES:
        if apply_probe(name, filename, old, new, expect):
            ok += 1

    print("\n[2] 全部还原后复查")
    code, failures = run_eval()
    if code != 0:
        print(f"  INVALID 还原后不绿：{failures[:3]}")
        return 1
    print("  ok   还原后场景全绿")

    print()
    print("=" * 72)
    if ok != len(PROBES):
        print(f"FAILED 探针 {ok}/{len(PROBES)} 有效")
        return 1
    print(f"PASS 探针 {ok}/{ok} 有效；还原成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
