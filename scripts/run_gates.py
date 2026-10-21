"""门禁报告：一次跑完所有黄金命令，并区分"跑过并断言"与"跳过了"。

为什么需要它：若干回归脚本在环境不满足时会打印 SKIP 并以 0 退出
（`test-ui-nav-browser.mjs` 在 8000 未起时就是这样）。单独看退出码全是绿的，
但那一轮其实什么都没验。这个脚本把每条命令的退出码与输出一起收上来，
凡是出现 SKIP 的金门禁单独列一行，最后以非零退出——除非显式传 `--allow-skip`。

用法：
    python scripts/run_gates.py                # 全部门禁，跳过即失败
    python scripts/run_gates.py --allow-skip   # 跳过只告警（兼容旧口径）
    python scripts/run_gates.py --only protocol,core,evals
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)


def npm_cmd() -> list[str]:
    """Windows 上 npm 是 npm.cmd；Git Bash 里的 npm 是 shell 脚本，subprocess 跑不动。

    直接写 ["npm", "test"] 在 Windows 会得到退出码 127（command not found），
    于是门禁报告把一条其实全绿的套件记成失败——先解析，别猜。
    """
    for candidate in ("npm.cmd", "npm.exe", "npm"):
        found = shutil.which(candidate)
        if found:
            return [found]
    return ["npm"]


def node_cmd() -> list[str]:
    """同 npm_cmd：解析出真实可执行文件，缺 node 时给出明确失败而不是 127。"""
    for candidate in ("node.exe", "node"):
        found = shutil.which(candidate)
        if found:
            return [found]
    return ["node"]

# name -> (command, 判定"真的断言过"的附加条件)
# skip_regex 命中输出即视为"这轮没验"，而不是通过。
GATES: dict[str, dict[str, object]] = {
    "protocol": {
        "cmd": [str(PY), "scripts/check_protocol.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "core": {
        "cmd": [str(PY), "scripts/test_core.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "scheduler_health": {
        "cmd": [str(PY), "scripts/test_scheduler_health.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "budget_scope": {
        "cmd": [str(PY), "scripts/test_budget_scope.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "backup_restore": {
        "cmd": [str(PY), "scripts/test_backup_restore.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "operation_outbox": {
        "cmd": [str(PY), "scripts/test_operation_outbox.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "mood_writer": {
        "cmd": [str(PY), "scripts/test_mood_writer.py"],
        "skip_regex": r"^\s*SKIP",
    },
    # 配置键审计：把"yaml 配了但访问器白名单没收 → 静默失效"做成门禁。
    # 它是"防死配置"的元检查——写进仓库却不跑，等于又一次死接线。
    # 2026-10-18 第一次接进来自曝 4 个死键（circadian / mood / seed_fork_min/max），
    # 其中前两个正是当天声称"已接上线"的 §15 配置。
    "config_keys": {
        "cmd": [str(PY), "scripts/audit_config_keys.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "evals": {
        "cmd": [str(PY), "scripts/run_evals.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "npm": {
        "cmd": [*npm_cmd(), "test"],
        "skip_regex": r"^\s*SKIP",
    },
    "executive_v3": {
        "cmd": [str(PY), "scripts/test_executive_v3.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "adapter_ext": {
        "cmd": [str(PY), "scripts/test_agent_adapter_ext.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "codex_shim": {
        "cmd": [str(PY), "scripts/test_codex_worker_shim.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "claude_shim": {
        "cmd": [str(PY), "scripts/test_claude_worker_shim.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "gateway": {
        "cmd": [str(PY), "scripts/test_anthropic_gateway.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "task_planning": {
        "cmd": [str(PY), "scripts/test_task_planning.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "cross_day": {
        "cmd": [str(PY), "scripts/test_cross_day_companion.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_conversation": {
        "cmd": [str(PY), "scripts/test_thread_conversation.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "self_evolution": {
        "cmd": [str(PY), "scripts/test_self_evolution_quality.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "agent_loop": {
        "cmd": [str(PY), "scripts/test_agent_loop.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "llm_client": {
        "cmd": [str(PY), "scripts/test_llm_client.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "self_evolution_send": {
        "cmd": [str(PY), "scripts/test_self_evolution_send.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "config_hot": {
        "cmd": [str(PY), "scripts/test_config_hot.py"],
        "skip_regex": r"^\s*SKIP",
    },
    # 慢环执行内核：适配器契约、进程监督、以及 agent-runtime 自己的接管测试。
    # 此前这三条只写在 docs/TESTING.md 里，没有进 run_gates——本轮改 durable
    # 时发现"文档说跑过、报告里根本没有这一行"。缺 node 时 supervisor 会打
    # SKIP，run_gates 会把跳过判失败，这是有意的：接管测试没跑就不算绿。
    "durable_adapter": {
        "cmd": [str(PY), "scripts/test_durable_adapter.py"],
        "skip_regex": r"^\s*SKIP",
    },
    # 工作台授权面：共享 DurableState、老板确认单、按调用放行、C3 提议提交。
    # 这条不需要浏览器；它验的是"浏览器点一下"之后台账与 sidecar 的真实变化。
    "durable_authorization_ui": {
        "cmd": [str(PY), "scripts/test_durable_authorization_ui.py"],
        "skip_regex": r"^\s*SKIP",
    },
    # S5 像素世界服务端桥：begin/delegate、world.dispatch/status、路由差分、
    # 后台任务并发/取消。世界端真帧 e2e 另属 QA 门禁。
    "world_bridge": {
        "cmd": [str(PY), "scripts/test_world_bridge.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_supervisor": {
        "cmd": [str(PY), "scripts/test_durable_supervisor.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_takeover": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-takeover.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_recovery": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-takeover-recovery.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_revive": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-revive.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_app_wiring": {
        "cmd": [str(PY), "scripts/test_durable_app_wiring.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_reconcile": {
        "cmd": [str(PY), "scripts/test_durable_reconcile.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_soak": {
        "cmd": [str(PY), "scripts/test_durable_soak.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_storage": {
        "cmd": [str(PY), "scripts/test_storage_archive.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_approval_per_call": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-approval-per-call.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_approval_chain": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-approval-chain.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_compaction": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-compaction.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_compaction_live": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-compaction-live.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_skills": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-skills.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_read_file": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-read-file.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_list_files": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-list-files.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_host_tools": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-host-tools.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_read_api": {
        "cmd": [str(PY), "scripts/test_durable_read_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_profiles": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-profiles.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_orchestrator_tools": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-orchestrator-tools.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_orchestrator_api": {
        "cmd": [str(PY), "scripts/test_durable_orchestrator_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_orchestrator_run": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-orchestrator-run.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_block_kind": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-block-kind.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_c3_api": {
        "cmd": [str(PY), "scripts/test_durable_c3_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_clarification_cap": {
        "cmd": [str(PY), "scripts/test_durable_clarification_cap.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_mcp": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-mcp.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "durable_mcp_live": {
        "cmd": [*node_cmd(), "--experimental-strip-types", "test-mcp-live.ts"],
        "cwd": "agent-runtime",
        "skip_regex": r"^\s*SKIP",
    },
    "gateway_env": {
        "cmd": [str(PY), "-B", "scripts/test_gateway_env.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "evolution_quality_report": {
        # 评测报告不是回归门禁，但它在 Windows GBK 控制台崩过一次；
        # 收进来是为了让"报告跑得完"本身成为一条断言。
        "cmd": [str(PY), "scripts/eval_self_evolution_quality.py"],
        "skip_regex": r"^\s*SKIP",
    },
    # ---- 浏览器门禁：环境不满足时 SKIP 且退出 0，是本次要盯住的那一类 ----
    "ui_browser": {
        "cmd": ["node", "scripts/test-ui-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "ui_nav_browser": {
        "cmd": ["node", "scripts/test-ui-nav-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "blocks_browser": {
        "cmd": ["node", "scripts/test-aqi-blocks-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "companion_browser": {
        "cmd": ["node", "scripts/test-companion-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "voice_browser": {
        "cmd": ["node", "scripts/test-voice-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
}

# 这些门禁的本体是纯逻辑单测，不需要本地服务，永远必须真跑。
ALWAYS_REQUIRED = ("protocol", "core", "scheduler_health", "budget_scope", "backup_restore", "operation_outbox", "mood_writer", "evals", "npm", "executive_v3", "adapter_ext",
                   "codex_shim", "claude_shim", "gateway", "task_planning", "cross_day",
                   "thread_conversation", "self_evolution", "agent_loop", "llm_client",
                   "self_evolution_send", "config_hot", "evolution_quality_report",
                   "durable_adapter", "durable_supervisor", "durable_takeover",
                   "durable_recovery", "durable_revive", "durable_app_wiring",
                   "durable_reconcile", "durable_soak", "durable_storage", "durable_compaction",
                   "durable_compaction_live", "durable_approval_per_call",
                   "durable_approval_chain", "durable_skills", "durable_read_file",
                   "durable_list_files", "durable_host_tools", "durable_read_api",
                   "durable_profiles", "durable_orchestrator_tools", "durable_orchestrator_api",
                   "durable_orchestrator_run", "durable_block_kind", "durable_c3_api",
                   "durable_clarification_cap", "durable_mcp", "durable_mcp_live",
                   "durable_authorization_ui", "world_bridge", "gateway_env")


def run_gate(name: str, spec: dict) -> dict:
    started = time.time()
    try:
        cwd = ROOT / str(spec["cwd"]) if spec.get("cwd") else ROOT
        proc = subprocess.run(spec["cmd"], cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = 124, "TIMEOUT after 900s"
    except FileNotFoundError as exc:
        code, output = 127, f"command not found: {exc}"
    skipped = bool(re.search(spec["skip_regex"], output, re.M))
    return {"name": name, "exit": code, "skipped": skipped,
            "seconds": round(time.time() - started, 1), "output_tail": output[-1500:]}


def main() -> int:
    parser = argparse.ArgumentParser(description="黄金门禁报告")
    parser.add_argument("--allow-skip", action="store_true",
                        help="跳过的门禁只告警，不算失败（兼容旧口径）")
    parser.add_argument("--only", default="", help="逗号分隔的门禁名")
    parser.add_argument("--json", default="", help="把报告写到这个 JSON 文件")
    args = parser.parse_args()

    wanted = [n.strip() for n in args.only.split(",") if n.strip()]
    names = [n for n in GATES if not wanted or n in wanted]
    unknown = [n for n in wanted if n not in GATES]
    if unknown:
        print(f"未知门禁: {unknown}；可选: {sorted(GATES)}")
        return 2

    rows = []
    for name in names:
        print(f"[run ] {name}", flush=True)
        row = run_gate(name, GATES[name])
        verdict = "PASS" if row["exit"] == 0 and not row["skipped"] else (
            "SKIP" if row["skipped"] else "FAIL")
        row["verdict"] = verdict
        rows.append(row)
        print(f"[{verdict:4}] {name:20} exit={row['exit']:<4} {row['seconds']:>6}s"
              + ("  ← 这轮没有真的断言" if verdict == "SKIP" else ""), flush=True)

    failed = [r for r in rows if r["verdict"] == "FAIL"]
    skipped = [r for r in rows if r["verdict"] == "SKIP"]
    must_not_skip = [r for r in skipped if r["name"] in ALWAYS_REQUIRED]

    print("\n=== 门禁报告 ===")
    print(f"  跑过并断言 : {sum(1 for r in rows if r['verdict'] == 'PASS')}")
    print(f"  失败       : {len(failed)}" + (f"  {', '.join(r['name'] for r in failed)}" if failed else ""))
    print(f"  跳过       : {len(skipped)}" + (f"  {', '.join(r['name'] for r in skipped)}" if skipped else ""))
    for row in skipped:
        print(f"      - {row['name']}: 环境不满足，退出码 0 但什么都没验")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"rows": [{k: v for k, v in r.items() if k != 'output_tail'} for r in rows],
             "failed": [r["name"] for r in failed],
             "skipped": [r["name"] for r in skipped]}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    if failed:
        return 1
    if skipped and not args.allow_skip:
        print("\n结论：有门禁这轮没有真的跑。先按上面的提示起服务，或用 --allow-skip 明确接受。")
        return 1
    print("\n结论：所有门禁都真跑过并断言。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
