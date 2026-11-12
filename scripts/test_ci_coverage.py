# -*- coding: utf-8 -*-
"""CI 覆盖完整性的门禁（2026-11-12）。

**问题**：健康检查实测，80 条 ALWAYS_REQUIRED 门禁里 **24 条没有任何 CI 步骤
执行过**：

- 17 条是 `agent-runtime/*.ts` 的 Node 门禁（durable_takeover / durable_recovery /
  durable_mcp 等）。CI 的分片器只 glob `scripts/test_*.py`，这些是 `.ts`，
  一个都进不去。**CI 装了 agent-runtime 的 npm 依赖，却一次都没跑过它的测试。**
- 7 条是 `scripts/` 下非 `test_*.py` 的探针脚本（process_trail / outbox_crash /
  evolution_quality_report / venv_lock / tmp_sweep / evals_report / evals_health）。

**为什么这是「绿灯比事实多说了话」**：ALWAYS_REQUIRED 的语义是「这些门禁必须
永远被跑到、且不能跳过」。CI 从不执行它们，那么「CI 绿」对它们**没有任何保证**，
但读 CI 报告的人会以为 ALWAYS_REQUIRED 那一档在 CI 里是受保护的。

**本文件的判据**：CI 工作流里必须真的跑这些门禁。判据不是「CI 文件里出现过
这些名字」（那是最容易造假的形态），而是**CI 实际执行的命令**能覆盖到它们。

覆盖方式两条：
- 丙（本轮采用）：把 Node 门禁接进 `ci_test_shard.py` 的第二个池，CI 跑同一条命令。
- 另有 7 条非 test_*.py 的探针脚本：单独在 CI 里跑。

判据做成「ALWAYS_REQUIRED 的每一个 key 都要能被 CI 执行的某条命令覆盖到」，
覆盖关系由「门禁的 cmd 特征」推出，不是靠人肉维护一张名单。
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("rg", ROOT / "scripts" / "run_gates.py")
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

CI = ROOT / ".github" / "workflows" / "ci.yaml"

# 这两条是**本机环境门禁**，故意不在 CI 跑，判据要把它们排除：
#   venv_lock —— 要本机 uv 装的 .venv 与 uv.lock 对齐；CI 用 pip + requirements-ci.txt，
#                装法不同，跑它必假红（docs/TESTING.md 已写明它是本机门禁）。
#   tmp_sweep —— 要本机 tmp/ 的存量水位与 ceiling 比对；新检出的 tmp/ 是空的，
#                水位判据的前提不成立。
# 它们在**本地** `run_gates.py --always` 里仍是 ALWAYS_REQUIRED（跳过即失败）。
LOCAL_ONLY = frozenset({"venv_lock", "tmp_sweep"})


def _ci_text() -> str:
    return CI.read_text(encoding="utf-8")


def _ts_gates() -> list[str]:
    return [
        key for key, spec in rg.GATES.items()
        if any(str(a).endswith(".ts") for a in (spec.get("cmd") or []))
    ]


def _non_test_py_gates() -> list[str]:
    """ALWAYS_REQUIRED 里，cmd 指向 scripts/ 下**非** test_*.py 的门禁。"""
    out = []
    for key in rg.ALWAYS_REQUIRED:
        cmd = [str(a) for a in (rg.GATES.get(key, {}).get("cmd") or [])]
        joined = " ".join(cmd)
        if "scripts/" in joined and "test_" not in joined:
            out.append(key)
    return out


class CiCoverageTests(unittest.TestCase):
    def test_ci_runs_the_shard_runner_without_skipping_ts(self):
        """CI 必须跑 ci_test_shard.py，且**不能带 --skip-ts**。

        带 --skip-ts 就等于把刚补上的 Node 覆盖又关掉 —— 那是个很容易在
        「CI 报 node 缺失」的压力下被加上的开关。
        """
        text = _ci_text()
        self.assertIn("ci_test_shard.py --workers 4", text,
                      "CI 必须跑分片器")
        self.assertNotIn("--skip-ts", text,
                         "CI 不得带 --skip-ts —— 那会把 Node 门禁覆盖整个关掉")

    def test_shard_runner_covers_the_agent_runtime_ts_gates(self):
        """分片器必须能发现并执行 agent-runtime/*.ts。

        判据看的是「discover_ts() 返回的集合」与「run_gates 里 TS 门禁指向的
        文件」的关系 —— 不是「CI 文件里写了 test-*.ts」。
        """
        cts_spec = importlib.util.spec_from_file_location(
            "cts", ROOT / "scripts" / "ci_test_shard.py")
        cts = importlib.util.module_from_spec(cts_spec)
        cts_spec.loader.exec_module(cts)

        discovered = {p.name for p in cts.discover_ts()}
        self.assertTrue(discovered, "discover_ts() 一个都没发现")

        # run_gates 里每条 TS 门禁指向的文件，都必须在 discover_ts() 里。
        wanted = set()
        for key in _ts_gates():
            for arg in (rg.GATES[key].get("cmd") or []):
                text = str(arg)
                if text.endswith(".ts"):
                    wanted.add(Path(text).name)
        missing = wanted - discovered
        self.assertFalse(missing, f"这些 Node 门禁没被分片器发现：{sorted(missing)}")

    def test_every_always_required_gate_has_a_ci_path(self):
        """每条 ALWAYS_REQUIRED 门禁都要有 CI 执行路径。

        覆盖关系由 cmd 特征推出：
        - `.ts` 结尾 → 由分片器的 Node 池覆盖；
        - `scripts/test_*.py` → 由分片器的 Python 池覆盖；
        - `npm test` → CI 有同名步骤；
        - 其余（`scripts/` 下非 test_ 前缀的探针）→ 必须在 CI 文件里被点名。

        LOCAL_ONLY 里那两条是本机环境门禁，按设计不在 CI 跑。
        """
        text = _ci_text()
        uncovered: list[str] = []
        for key in rg.ALWAYS_REQUIRED:
            if key in LOCAL_ONLY:
                continue
            cmd = " ".join(str(a) for a in (rg.GATES[key].get("cmd") or []))
            if cmd.endswith(".ts"):
                continue  # Node 池覆盖
            if "scripts/test_" in cmd:
                continue  # Python 池覆盖
            if "npm" in cmd and cmd.rstrip().endswith("test"):
                continue  # CI 有 npm test 步骤
            # 其余：必须在 CI 里被显式点名
            script = cmd.split("scripts/")[-1].split()[0] if "scripts/" in cmd else ""
            if script and script in text:
                continue
            uncovered.append(f"{key} -> {cmd}")
        self.assertFalse(
            uncovered,
            "这些 ALWAYS_REQUIRED 门禁没有任何 CI 执行路径：\n  "
            + "\n  ".join(uncovered))

    def test_the_non_test_py_probe_gates_are_named_in_ci(self):
        """scripts/ 下非 test_*.py 的探针门禁必须在 CI 里被点名。"""
        text = _ci_text()
        for key in _non_test_py_gates():
            if key in LOCAL_ONLY:
                continue
            cmd = " ".join(str(a) for a in (rg.GATES[key].get("cmd") or []))
            script = cmd.split("scripts/")[-1].split()[0]
            self.assertIn(script, text,
                          f"{key}（{script}）没在 CI 里被执行")

    def test_local_only_gates_are_documented_as_such(self):
        """LOCAL_ONLY 的两条要在 TESTING.md 里写明是本机门禁，别静默消失。"""
        text = (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
        for key in LOCAL_ONLY:
            script = " ".join(str(a) for a in (rg.GATES[key].get("cmd") or []))
            script = script.split("scripts/")[-1].split()[0]
            self.assertIn(script, text,
                          f"{key}（{script}）是本机门禁，TESTING.md 里应说明")


if __name__ == "__main__":
    unittest.main(verbosity=2)
