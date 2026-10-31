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
    "gate_tmp_leak": {
        # 门禁沙盘泄漏棘轮（差分检测）：跑最可能漏 tmp/ 的那批门禁，每次前后
        # 量 tmp/ 条目数，净增即红。plan-45 的四个浏览器门禁曾长期漏沙盘把
        # tmp_sweep 顶红，plan-118 差分法实测全部门禁后确认仅那四处，本棘轮
        # 把那个快照变成常驻保护。
        # 刻意**不**进 ALWAYS_REQUIRED：单轮约 140s（最重业务门禁的 10 倍），
        # 进那一档会把「每次改完必跑」推到 9 分多。它验的是元性质，跟
        # check_staged_scope / check_probe 同类，但量级差两个数量级，故只进
        # 全量层。判据用差分不用绝对值（绝对值随 tmp/ 自然增长假红）。
        "cmd": [str(PY), "scripts/test_gate_tmp_leak.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "check_staged_scope": {
        # check_staged_scope 自身回归：判据（暂存区 ⊆ 声明路径）不许退化成
        # 永真。测试在临时 git 仓库造场景，不碰真实 index。纯逻辑、零网络零
        # Key、确定性，ALWAYS_REQUIRED。
        "cmd": [str(PY), "scripts/test_check_staged_scope.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "check_probe": {
        # check_probe 自身回归：它的四条判据是三次真实事故逼出来的（等价改动、
        # 锚点撞多处、注入没生效、还原不干净），不许退化。纯进程内逻辑、临时
        # 目录造 CRLF 目标文件、零网络零 Key、确定性，ALWAYS_REQUIRED。
        "cmd": [str(PY), "scripts/test_check_probe.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "run_gates_verdict": {
        # R51 裁决口径：退出码非零一律 FAIL，只有 exit==0 且命中 skip_regex
        # 才算 SKIP。合成门禁驱出各 verdict 分支，零网络零 Key、不跑真实门禁。
        # ALWAYS_REQUIRED：纯进程内逻辑，不碰磁盘仓库文件，无服务依赖。
        "cmd": [str(PY), "scripts/test_run_gates.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "gate_coverage": {
        # 棘轮：scripts/test_*.py 要么登记进 GATES，要么进 gates_unregistered.txt。
        # CI 的 glob 比这里宽得多，差集里的脚本坏了没人知道（master 曾连续红
        # 多次而日志只有一句 exit code 1）。这条本身也要登记 —— 不登记它就拦自己。
        "cmd": [str(PY), "scripts/test_gate_coverage.py"],
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
    "outbox_crash": {
        "cmd": [str(PY), "scripts/probe_outbox_crash.py"],
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
    "task_fsm": {
        # T4（#22）：契约 v2 §4 状态机全套（合法迁移 + 非法组合锁）。
        "cmd": [str(PY), "scripts/test_task_fsm.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "contract_v2": {
        # T4（#22）：契约层 lint/gate/risk（含 3b 权限-风险跨字段）。
        "cmd": [str(PY), "scripts/test_contract.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "contract_ledger_rules": {
        # 台账验收规则 -> 契约缺口 的桥接：老板写了规则就不许报「缺验收标准」。
        # 两套词表（台账 artifact_* vs 契约 CheckSpec）规范未定义映射，
        # 所以只点名 kind、不假装转换。冻结边界见 docs/CONTRACT_V2_SCOPE.md。
        "cmd": [str(PY), "scripts/test_contract_ledger_rules.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "v2_broadcast": {
        # W1（#25）：§25 事件协议 v2 线上广播层（信封/台账桥/五命令）。
        "cmd": [str(PY), "scripts/test_v2_broadcast.py"],
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
    # §17 的第二层：覆盖必须到达读取方，且 startup/shutdown 真的装上 watcher。
    # 上面那条只测 config_hot 模块本身；2026-10-22 抓到的事故（机制写得完整、
    # 那条测试全绿、而生产代码零处调用 start_watcher）正在它视线外。
    "config_hot_consumers": {
        "cmd": [str(PY), "scripts/test_config_hot_consumers.py"],
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
    # ---- 第四十九批认领：gates_unregistered 里的确定性离线测试 ----
    # 认领前逐个实跑过（绿 97/97；剔除 PIL 缺失整组跳过的
    # test_eye_calibration_analysis 后剩 96），按字母序分批登记。
    # 判据：unittest 或 check() 断言、无外部服务/Key/端口、无墙钟阈值。
    "acceptance_dispatch": {
        "cmd": [str(PY), "scripts/test_acceptance_dispatch.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "activity": {
        "cmd": [str(PY), "scripts/test_activity.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "activity_dialogue": {
        "cmd": [str(PY), "scripts/test_activity_dialogue.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "activity_integration": {
        "cmd": [str(PY), "scripts/test_activity_integration.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "activity_material_scope": {
        "cmd": [str(PY), "scripts/test_activity_material_scope.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "activity_websocket": {
        "cmd": [str(PY), "scripts/test_activity_websocket.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "ambient_orchestration": {
        "cmd": [str(PY), "scripts/test_ambient_orchestration.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "ambient_tiers": {
        "cmd": [str(PY), "scripts/test_ambient_tiers.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "aqi_blocks": {
        "cmd": [str(PY), "scripts/test_aqi_blocks.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "asr_language": {
        "cmd": [str(PY), "scripts/test_asr_language.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "assistant_api": {
        "cmd": [str(PY), "scripts/test_assistant_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "assistant_context": {
        "cmd": [str(PY), "scripts/test_assistant_context.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "assistant_continuation": {
        "cmd": [str(PY), "scripts/test_assistant_continuation.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "assistant_store": {
        "cmd": [str(PY), "scripts/test_assistant_store.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "assistant_websocket": {
        "cmd": [str(PY), "scripts/test_assistant_websocket.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "cli_subproc_adapter": {
        "cmd": [str(PY), "scripts/test_cli_subproc_adapter.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "companion_integration": {
        "cmd": [str(PY), "scripts/test_companion_integration.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "context_broker": {
        "cmd": [str(PY), "scripts/test_context_broker.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "context_budget": {
        "cmd": [str(PY), "scripts/test_context_budget.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "context_budget_integration": {
        "cmd": [str(PY), "scripts/test_context_budget_integration.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "curiosity_graph": {
        "cmd": [str(PY), "scripts/test_curiosity_graph.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "durable_shadow": {
        "cmd": [str(PY), "scripts/test_durable_shadow.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "editor_discussion_websocket": {
        "cmd": [str(PY), "scripts/test_editor_discussion_websocket.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "editor_insight_outputs": {
        "cmd": [str(PY), "scripts/test_editor_insight_outputs.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "editor_insights": {
        "cmd": [str(PY), "scripts/test_editor_insights.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "editor_insights_api": {
        "cmd": [str(PY), "scripts/test_editor_insights_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "editor_quality_eval": {
        "cmd": [str(PY), "scripts/test_editor_quality_eval.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "episodic_diary": {
        "cmd": [str(PY), "scripts/test_episodic_diary.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "event_emitter": {
        "cmd": [str(PY), "scripts/test_event_emitter.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "events_v2": {
        "cmd": [str(PY), "scripts/test_events_v2.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "evolution_soak": {
        "cmd": [str(PY), "scripts/test_evolution_soak.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "experience": {
        "cmd": [str(PY), "scripts/test_experience.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "experience_websocket": {
        "cmd": [str(PY), "scripts/test_experience_websocket.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_gate_bridge": {
        "cmd": [str(PY), "scripts/test_intent_gate_bridge.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_parser": {
        "cmd": [str(PY), "scripts/test_intent_parser.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_parser_contract": {
        "cmd": [str(PY), "scripts/test_intent_parser_contract.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_probe": {
        "cmd": [str(PY), "scripts/test_intent_probe.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "interpretation": {
        "cmd": [str(PY), "scripts/test_interpretation.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "judge_three_state": {
        "cmd": [str(PY), "scripts/test_judge_three_state.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_api": {
        "cmd": [str(PY), "scripts/test_memory_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_eval": {
        "cmd": [str(PY), "scripts/test_memory_eval.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_evidence": {
        "cmd": [str(PY), "scripts/test_memory_evidence.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_extraction_contract": {
        "cmd": [str(PY), "scripts/test_memory_extraction_contract.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_orchestrator": {
        "cmd": [str(PY), "scripts/test_memory_orchestrator.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_preference": {
        "cmd": [str(PY), "scripts/test_memory_preference.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_preference_parser": {
        "cmd": [str(PY), "scripts/test_memory_preference_parser.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_presentation": {
        "cmd": [str(PY), "scripts/test_memory_presentation.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_self_evolution": {
        "cmd": [str(PY), "scripts/test_memory_self_evolution.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_store_contract": {
        "cmd": [str(PY), "scripts/test_memory_store_contract.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "micro_delivery": {
        "cmd": [str(PY), "scripts/test_micro_delivery.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "mine_eval_candidates": {
        "cmd": [str(PY), "scripts/test_mine_eval_candidates.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "music": {
        "cmd": [str(PY), "scripts/test_music.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "music_api": {
        "cmd": [str(PY), "scripts/test_music_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "note_conversation": {
        "cmd": [str(PY), "scripts/test_note_conversation.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "note_edit_integration": {
        "cmd": [str(PY), "scripts/test_note_edit_integration.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "note_edit_websocket": {
        "cmd": [str(PY), "scripts/test_note_edit_websocket.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "note_material_scope": {
        "cmd": [str(PY), "scripts/test_note_material_scope.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "note_proposals": {
        "cmd": [str(PY), "scripts/test_note_proposals.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "note_proposals_api": {
        "cmd": [str(PY), "scripts/test_note_proposals_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "note_versions": {
        "cmd": [str(PY), "scripts/test_note_versions.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "notes": {
        "cmd": [str(PY), "scripts/test_notes.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "practice": {
        "cmd": [str(PY), "scripts/test_practice.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "practice_websocket": {
        "cmd": [str(PY), "scripts/test_practice_websocket.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "preferences": {
        "cmd": [str(PY), "scripts/test_preferences.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "procedural_memory_eval_snapshot": {
        "cmd": [str(PY), "scripts/test_procedural_memory_eval_snapshot.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "procedural_memory_induction": {
        "cmd": [str(PY), "scripts/test_procedural_memory_induction.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "proposals_integration": {
        "cmd": [str(PY), "scripts/test_proposals_integration.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "reading_trial_loop": {
        "cmd": [str(PY), "scripts/test_reading_trial_loop.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "reflect_cycle": {
        "cmd": [str(PY), "scripts/test_reflect_cycle.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "reply_delivery": {
        "cmd": [str(PY), "scripts/test_reply_delivery.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "response_composer": {
        "cmd": [str(PY), "scripts/test_response_composer.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "response_integration": {
        "cmd": [str(PY), "scripts/test_response_integration.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "restart_recovery": {
        "cmd": [str(PY), "scripts/test_restart_recovery.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "seed_eye_expression": {
        "cmd": [str(PY), "scripts/test_seed_eye_expression.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "seed_eye_lids": {
        "cmd": [str(PY), "scripts/test_seed_eye_lids.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "soul_polish": {
        "cmd": [str(PY), "scripts/test_soul_polish.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "speech_performance": {
        "cmd": [str(PY), "scripts/test_speech_performance.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "speech_performance_route": {
        "cmd": [str(PY), "scripts/test_speech_performance_route.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "static_cache": {
        "cmd": [str(PY), "scripts/test_static_cache.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "step_stream": {
        "cmd": [str(PY), "scripts/test_step_stream.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "stream_delivery": {
        "cmd": [str(PY), "scripts/test_stream_delivery.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "stream_interpretation": {
        "cmd": [str(PY), "scripts/test_stream_interpretation.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_continuity": {
        "cmd": [str(PY), "scripts/test_thread_continuity.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_history": {
        "cmd": [str(PY), "scripts/test_thread_history.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_history_api": {
        "cmd": [str(PY), "scripts/test_thread_history_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_materials": {
        "cmd": [str(PY), "scripts/test_thread_materials.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_materials_api": {
        "cmd": [str(PY), "scripts/test_thread_materials_api.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_proposals": {
        "cmd": [str(PY), "scripts/test_thread_proposals.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "thread_recall": {
        "cmd": [str(PY), "scripts/test_thread_recall.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "threads": {
        "cmd": [str(PY), "scripts/test_threads.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "tts_trim_silence": {
        "cmd": [str(PY), "scripts/test_tts_trim_silence.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "voice_taste": {
        "cmd": [str(PY), "scripts/test_voice_taste.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "voice_turns": {
        "cmd": [str(PY), "scripts/test_voice_turns.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "world_intent": {
        "cmd": [str(PY), "scripts/test_world_intent.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "ws_origin": {
        "cmd": [str(PY), "scripts/test_ws_origin.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "procedural_memory_boundaries": {
        "cmd": [str(PY), "scripts/test_procedural_memory_boundaries.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "procedural_memory_diagnostics": {
        "cmd": [str(PY), "scripts/test_procedural_memory_diagnostics.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "procedural_memory_eval": {
        "cmd": [str(PY), "scripts/test_procedural_memory_eval.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "procedural_memory_source_projection": {
        "cmd": [str(PY), "scripts/test_procedural_memory_source_projection.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "assistant_ui_contract": {
        "cmd": [str(PY), "scripts/test_assistant_ui_contract.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "dsh_worker_shim": {
        "cmd": [str(PY), "scripts/test_dsh_worker_shim.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_gate_wiring": {
        "cmd": [str(PY), "scripts/test_intent_gate_wiring.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_guard": {
        "cmd": [str(PY), "scripts/test_intent_guard.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_retrieval_gate": {
        "cmd": [str(PY), "scripts/test_memory_retrieval_gate.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "pixel_world_hosting": {
        "cmd": [str(PY), "scripts/test_pixel_world_hosting.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "stream_route": {
        "cmd": [str(PY), "scripts/test_stream_route.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "workbuddy_worker_shim": {
        "cmd": [str(PY), "scripts/test_workbuddy_worker_shim.py"],
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
    "experience_entry_browser": {
        "cmd": ["node", "scripts/test-experience-entry-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "voice_browser": {
        "cmd": ["node", "scripts/test-voice-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "step8_e2e_browser": {
        # T3（#23）：§25 慢环垂直切片 e2e。自起独立端口真实服务（默认 8021，
        # E2E_PORT 可覆盖），不依赖也不占用 8000/8010，无 Key 走 Mock 全链。
        "cmd": ["node", "scripts/test-step8-e2e-browser.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_manage": {
        # S2（#28）：记忆软停用/证据链时间（python 侧）。
        "cmd": [str(PY), "scripts/test_memory_manage.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_ranking": {
        # #31：两阶段检索必须与「全表建对象再排序」逐条等价。
        # 用改动前的实现当对照器；投影漏一列、打平次序变化都会红。
        # ALWAYS_REQUIRED：纯逻辑、:memory:、无服务依赖，确定性等价比对。
        "cmd": [str(PY), "scripts/test_memory_ranking_equivalence.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_fastloop": {
        # #31：记忆检索不得阻塞事件循环（量的是行为，不是有没有 to_thread）。
        # 刻意不进 ALWAYS_REQUIRED：判据是墙钟停顿（sleep(0) 心跳 + 10ms
        # 阈值），重载机器上有假红风险（2026-10-27 实测 11.93ms；线程池预热
        # 后连跑 5 次全绿）。它随全套回归常规跑，但不做「跳过即失败」的
        # 硬门；真值以 code review 与 LATENCY_BASELINE 的实测为准，不以
        # 某一刻的机器负载为准。
        "cmd": [str(PY), "scripts/test_memory_fastloop.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "drill_network": {
        # S3（#29）场景①：断网自动重连回归（自起 8033）。
        "cmd": ["node", "scripts/drill-s1-network.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "drill_badkey": {
        # S3（#29）场景②：坏 Key 快败与文案回归（自起 8034 + 401 桩）。
        "cmd": ["node", "scripts/drill-s2-badkey.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "drill_ws_reconnect": {
        # S3（#29）场景③：WS 断连重连与 v2 语义回归（自起 8035）。
        "cmd": ["node", "scripts/drill-s3-ws-reconnect.mjs"],
        "skip_regex": r"^\s*SKIP",
    },
    "db_sentinel": {
        # #39：测试进程 DB_PATH 指向仓库 data/ 时启动即失败（隔离的机械保证）。
        "cmd": [str(PY), "scripts/test_db_sentinel.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_normalize": {
        # B1（#42）：意图输入归一化（礼貌前缀/语气词/ASR 同音字）。
        "cmd": [str(PY), "scripts/test_intent_normalize.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "intent_corpus": {
        # B1（#42）：语料格式校验 + 判定器在语料上的测量（基线落 eval/intent/）。
        "cmd": [str(PY), "scripts/measure_intent_corpus.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "lessons_cap": {
        # #59：lessons 扫描边界来自真实行数（COUNT），不是写死的 1000。
        # active 超过 1000 条时，最老那批必须仍被 prune 看见、被检索命中。
        "cmd": [str(PY), "scripts/test_lessons_cap.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "venv_lock": {
        # 跑门禁的解释器与 uv.lock 一致：2026-10-25 事故的形状是「门禁全绿，
        # 但 .venv 缺整个语音栈」——离线门禁 mock 掉 ASR/TTS，环境残缺它们
        # 看不见。关键子集（语音栈 + == 钉死项）缺失/不符即 FAIL，全集漂移
        # 只报数。纯离线、亚秒级、自带失败路径自检，ALWAYS_REQUIRED。
        "cmd": [str(PY), "scripts/check_venv_lock.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "tmp_sweep": {
        # tmp/ 只增不减的计数棘轮（AGENTS.md 待办第 3 条）：dry-run 跑，
        # 条目数超 scripts/tmp_ceiling.txt 即红。只扫 tmp/（.gitignore），
        # 删不到仓库文件也不碰 data/。源头修复在 evals/checks.py 的沙盘
        # 随用随清；这是兜底。
        "cmd": [str(PY), "scripts/tmp_sweep.py", "--dry-run",
                "--ceiling", "scripts/tmp_ceiling.txt"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_concurrency": {
        # memory 检索并发不变量：读+写竞争下索引三代状态自洽、无死锁、
        # 无半边索引。纯逻辑、临时库、无服务依赖，ALWAYS_REQUIRED。
        "cmd": [str(PY), "scripts/test_memory_concurrency.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_api_offloop": {
        # memory REST 路由不得独占事件循环：四条路由与语音 WS 共享 loop，
        # 同步 SQLite 读会把循环停住整个读取时长（实测 3000 条记忆时
        # starmap 一次 202ms）。判据是调度计数不是墙钟，同步版 beats=0、
        # to_thread 版 beats~7 万，阈值 10 双向数量级余量。
        # ALWAYS_REQUIRED：纯逻辑、临时库、无服务依赖。
        "cmd": [str(PY), "scripts/test_memory_api_offloop.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "memory_retrieval": {
        # Retrieval-layer behavior invariants: a valid-time bound (fact_valid_until
        # in the past, or fact_valid_from in the future) must demote the row, never
        # drop it -- a historical question still has to reach a fact the user
        # explicitly bounded. A NULL bound must not affect ordinary retrieval. A
        # superseded row carrying an explicit bound stays reachable. Every check is
        # a score or position comparison; fixtures use relative time.time() bounds
        # (1000s slack), so there is no wall-clock threshold, sleep, network, or
        # randomness. 49 cases run in 0.4s, hence ALWAYS_REQUIRED.
        "cmd": [str(PY), "scripts/test_memory_retrieval.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "voice_offloop": {
        # app.py 的异步面不得在循环线程跑 SQLite：四个语音处理器
        # （_narrate_turn / _touch_reply / _proactive_check 的 mumble 分支 /
        # _confirm_action）的 4 处 store_message 写 + _narrate_turn 1 处
        # starmap_node 读，两个 WS 端点（websocket_endpoint / proactive_socket）
        # 的 7 处会话调用，10 条 memory REST 路由（snapshot/context/correct/
        # forget/forget_key/forget_all/remember/history_of/count/set_disabled，
        # 实测 15-76ms 或 O(表长)全表扫描），加 _aqi_action / _executive_finish
        # 的 executive 台账写（accept/reassign/reject 实测 9.42ms）与
        # _proactive_check 自发话题分支的 spend_spark（median 9.05ms），
        # 已全部卸载到 to_thread。写提交（BEGIN IMMEDIATE + fsync）卸、亚毫秒
        # 有界索引读（runtime.get / pending_reports / recent_messages）留同步。
        # 判据是线程身份不是墙钟：同步版恒在 MainThread（探针实测），to_thread
        # 版恒在 asyncio_* worker，双向无灰区。同时钉落库与会话参数逐字不变，
        # 以及 REST 路由的 limit 夹取（-1/0 夹成 1、超大值夹到路由帽；
        # SQLite 的 LIMIT -1 是无限制）。
        # ALWAYS_REQUIRED：AST 装载生产 app.py + 假模块，无服务无网络。
        "cmd": [str(PY), "scripts/test_voice_offloop.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "proactive_offloop": {
        # 第二十批（#31 残留，2026-10-31 评审实证）：voice_offloop 用替身驱动
        # app 处理器，proactive_turn 是 AsyncMock、ambient_event 是 lambda——
        # **替身测不到被 mock 掉的真实函数体**，而真身里躺着两处无界全库检索
        # （proactive_turn 的 search+context_for、破冰路径的 search）。本门禁
        # 反向驱动：打桩依赖（retriever/llm/store_message），跑真身，判据是
        # 线程身份（同步版恒在循环线程，to_thread 版恒在 asyncio worker）。
        # 同时钉许可位前置的短路顺序（allow_* 全关时零次检索）与破冰的
        # app.py 调用形态（await to_thread(ambient_event)）。
        # ALWAYS_REQUIRED：临时库 + 纯进程内，无服务无网络。
        "cmd": [str(PY), "scripts/test_proactive_offloop.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "verify_package": {
        # server/verify 的 IO 禁令此前**没有任何机械强制**：check.py:30 与
        # verifier.py:30 都写「由 scripts/test_verify.py 的 IO 断言机械检查」，
        # 但该文件从未存在于工作树（docs/TESTING.md:201，2026-10-26 核；git log
        # --all 亦零命中）。2026-10-31 补齐：三种 check 正反例 + reader 注入 +
        # IO 静态扫描（扫描器自己用违规样例自证，防「有洞的校验器报全 OK」），
        # 外加 acceptance_verify 桥接的三态渲染契约（M2/issue #61 落地面零测试）。
        # ALWAYS_REQUIRED：临时目录 + 假 reader，无服务无网络无真实库。
        "cmd": [str(PY), "scripts/test_verify.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "router_writes_offloop": {
        # 两个 REST 路由器的 6 个写端点（thread-proposals 与 note-proposals 的
        # revise/confirm/reject）的 service 写必须跑在工作线程。判据是线程
        # 身份：同步版恒在 MainThread（探针实测 6 个写全落），to_thread 版
        # 恒在 asyncio_* worker。editor-insights 的 dismiss/feedback 按判据
        # 不卸（纯内存写无 fsync，且 dismiss 会跨线程取消 analyze 的 Task）。
        # ALWAYS_REQUIRED：ASGI 内场，无服务无网络。
        "cmd": [str(PY), "scripts/test_router_writes_offloop.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "broadcast_e2e": {
        # 广播链端到端：台账回调/v2 hub 从 worker 线程触发，经
        # call_soon_threadsafe 转回循环后真到达对端连接。判据是帧到达
        # 与内容正确，非时序。破坏版（就地 create_task）实测两连接收 0 帧，
        # 断言必红。真 Connection + 真 sink + FakeSocket，无服务无网络。
        # ALWAYS_REQUIRED。
        "cmd": [str(PY), "scripts/test_broadcast_e2e.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "shared_retriever_concurrency": {
        # 多连接共享同一 retriever 实例（manager.retriever，manager.py:59）
        # 的并发检索不变量：无死锁、读内部自洽、零匹配查询恒落空、top-1
        # 锚点不被并发写挤掉、收敛后共享实例与全新实例逐位相等。判据全是
        # 行为与值相等，无墙钟依赖。注意「无撕裂读」此刻在单锁下不可观察
        # （探针实证），是有牙齿的惰性断言，docstring 已写明定位。
        # ALWAYS_REQUIRED。
        "cmd": [str(PY), "scripts/test_shared_retriever_concurrency.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "evals_report": {
        # 评测报告层（docs/EVAL_MECHANISM_PLAN.md）：读 evals/results/ 快照出
        # 通过率（平均与全过概率分列）、与上次的配对差异、token 成本。
        # 报告内容不进门禁，收进来是为了让"报告跑得完"本身成为一条断言；
        # 空目录 / 单快照 / 快照损坏都是 exit 0 的正常态，不许退化成 SKIP。
        "cmd": [str(PY), "scripts/evals_report.py"],
        "skip_regex": r"^\s*SKIP",
    },
    "evals_health": {
        # 评测健康度层（docs/EVAL_MECHANISM_PLAN.md）：横向扫全史快照，回答报告层
        # 不回答的四个问题——offline 是否还确定 / live 是一直抖还是最近退化 /
        # 快照数据质量 / 评测体系自己是否在过拟合。退出码语义与报告层不同：仅近期
        # 窗口（RECENT_WINDOW_DAYS 天）内 offline 用例失效才 exit 1；live 抖动、
        # 历史欠账、数据质量、空目录、快照全损坏一律 exit 0。xfail 用例不计失败，
        # 故也不进门禁判定。空态不许退化成跳过，输出不得出行首 SKIP。
        # 2026-10-26：本条目曾被并行会话的提交误删，已按原样接回。
        "cmd": [str(PY), "scripts/evals_health.py"],
        "skip_regex": r"^\s*SKIP",
    },
}

# 这些门禁的本体是纯逻辑单测，不需要本地服务，永远必须真跑。
ALWAYS_REQUIRED = ("protocol", "core", "scheduler_health", "budget_scope", "backup_restore", "operation_outbox", "outbox_crash", "mood_writer", "evals", "npm", "executive_v3", "adapter_ext",
                   "codex_shim", "claude_shim", "gateway", "task_planning", "cross_day",
                   "thread_conversation", "self_evolution", "agent_loop", "llm_client",
                   "self_evolution_send", "config_hot", "config_hot_consumers",
                   "evolution_quality_report", "evals_report", "evals_health", "lessons_cap", "venv_lock",
                   "tmp_sweep", "run_gates_verdict", "check_probe", "check_staged_scope",
                   "memory_ranking", "memory_concurrency", "memory_retrieval",
                   "memory_api_offloop",
                   "voice_offloop", "proactive_offloop",
                   "router_writes_offloop",
                   "broadcast_e2e",
                   "shared_retriever_concurrency",
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


# unittest 原生 skip 的汇总行：`OK (skipped=N)`，且只认 N>0（`[1-9]\d*` 天然排除
# `skipped=0`）。行首锚定 + re.M，与式① `^\s*SKIP` 同口径——只认结论行，不认正文里
# 恰好出现的字样。允许 `OK (skipped=N, expected failures=M)` 并存形态（CPython 的
# TextTestResult 把 skipped 排在括号内首位）。`FAILED (...)` 行首不是 OK，命不中本式。
UNITTEST_SKIPPED_RE = re.compile(r"^\s*OK \(skipped=([1-9]\d*)", re.M)


def run_gate(name: str, spec: dict) -> dict:
    started = time.time()
    try:
        cwd = ROOT / str(spec["cwd"]) if spec.get("cwd") else ROOT
        # 生产库哨兵（#39）**默认打开**：所有门禁子进程带 TEST_SENTINEL=1，
        # 打开仓库 data/ 下的库即报错。
        #
        # 曾经默认关着，因为开启后有 8 个历史套件立刻变红（mood_writer、cross_day、
        # self_evolution、config_hot、config_hot_consumers、world_bridge、
        # evolution_quality_report、memory_manage）——它们用模块级单例或真实库。
        # #41 已把八个全部钉到临时根，且 `_service_data_dir` 的参照点从会被 patch
        # 的 `config.ROOT` 换成了 `__file__` 锚。**关着的门卫等于没有门卫**：
        # #39 的 379 条正是从这条通道攒进真实台账的，而一个「开了就 8 红」的
        # 开关没人会一直开着。
        #
        # 需要临时关掉（例如调试一个尚未隔离的新套件）：GATE_SENTINEL=off。
        env = {**os.environ}
        if os.environ.get("GATE_SENTINEL", "on") == "on":
            env["TEST_SENTINEL"] = "1"
        proc = subprocess.run(spec["cmd"], cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900, env=env)
        code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = 124, "TIMEOUT after 900s"
    except FileNotFoundError as exc:
        code, output = 127, f"command not found: {exc}"
    # skip 探测走两条式，命中其一即 skipped=True（裁决式在 main()：exit!=0 一律
    # FAIL，这里只是「这轮什么都没验」的线索，R51 红线不因本处放宽）：
    #   式① spec["skip_regex"]（默认 `^\s*SKIP`）——仓库自定义脚本形态，照旧。
    #   式② UNITTEST_SKIPPED_RE——unittest 装饰器 skip（@unittest.skipUnless /
    #       skipIf）走的是原生汇总行 `OK (skipped=N)`，行首是 `OK` 不是 `SKIP`，
    #       式① 永远命不中；2026-10-31 实测 procedural_memory_eval（Ran 24 tests、
    #       skipped=20、exit 0）就被裁成 PASS。必须补上这条。
    # 只认 N>0：`OK` 裸行与 `OK (skipped=0)` 都是「全跑全过」，判 SKIP 会把真跑过的
    # 绿洗成假跳。多结论行时任一 N>0 即命中（re.search 扫全文）。
    skipped = bool(re.search(spec["skip_regex"], output, re.M)
                   or UNITTEST_SKIPPED_RE.search(output))
    return {"name": name, "exit": code, "skipped": skipped,
            "seconds": round(time.time() - started, 1), "output_tail": output[-1500:]}


def main() -> int:
    # 输出一律 UTF-8，不管是被管道还是被重定向接走。Windows 下 stdout 不是
    # 控制台时默认走 locale（GBK），而门禁脚本自己吐中文——子进程按 UTF-8 写
    # （下面 subprocess.run 显式 encoding="utf-8"）、父进程按 GBK 编码出去，
    # 于是 `run_gates.py | tail` 出来的报告全是 `�����`。而「失败时贴输出」
    # 正是为了让人读得懂失败原因，贴一坨乱码等于没贴（2026-10-25 实测）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description="黄金门禁报告")
    parser.add_argument("--allow-skip", action="store_true",
                        help="跳过的门禁只告警，不算失败（兼容旧口径）")
    parser.add_argument("--only", default="", help="逗号分隔的门禁名")
    parser.add_argument("--always", action="store_true",
                        help="只跑 ALWAYS_REQUIRED 那一档（必须永不跳过的门禁）")
    parser.add_argument("--json", default="", help="把报告写到这个 JSON 文件")
    args = parser.parse_args()

    wanted = [n.strip() for n in args.only.split(",") if n.strip()]
    if args.always:
        wanted = list(ALWAYS_REQUIRED)
    names = [n for n in GATES if not wanted or n in wanted]
    unknown = [n for n in wanted if n not in GATES]
    if unknown:
        print(f"未知门禁: {unknown}；可选: {sorted(GATES)}")
        return 2

    rows = []
    for name in names:
        print(f"[run ] {name}", flush=True)
        row = run_gate(name, GATES[name])
        # R51：非零退出码一律 FAIL（fail-closed），SKIP 只在 exit==0 时成立。
        # 曾经的裁决式把 `skipped` 排在非零退出码之前——一个「打印了 SKIP 行
        # 却又 exit!=0」的门禁会被误判成 SKIP，于是：① 失败输出不被贴（下面
        # 只对 FAIL 贴）；② 末尾 `if failed` 不含它；③ `--allow-skip` 又放过
        # skipped——三者叠加，一句真实失败被洗成绿，结论还打印「所有门禁都真
        # 跑过并断言」（2026-10-27 合成门禁实证：exit=1+SKIP 行 + --allow-skip
        # → main() exit 0）。SKIP 的语义是「环境不满足、没跑、退出码 0」；
        # 退出码非零意味着门禁真跑了且有失败，绝不能算 SKIP。
        if row["exit"] != 0:
            verdict = "FAIL"
        elif row["skipped"]:
            verdict = "SKIP"
        else:
            verdict = "PASS"
        row["verdict"] = verdict
        rows.append(row)
        print(f"[{verdict:4}] {name:20} exit={row['exit']:<4} {row['seconds']:>6}s"
              + ("  ← 这轮没有真的断言" if verdict == "SKIP" else ""), flush=True)
        # 失败时把子进程输出贴出来。不贴的话，唯一可得的证据就是一个 exit code
        # ——而失败往往正是「脚本自己没跑起来」，光看退出码无从判断（实测：
        # drill_network 间歇失败，报告里只有 exit=1，得手动重跑五次才抓得到）。
        if verdict == "FAIL":
            tail = (row.get("output_tail") or "").strip()
            if tail:
                for line in tail.splitlines()[-25:]:
                    print(f"       | {line}", flush=True)

    failed = [r for r in rows if r["verdict"] == "FAIL"]
    skipped = [r for r in rows if r["verdict"] == "SKIP"]
    # 注意判据：**任何**门禁跳过即失败（除非 --allow-skip），并不区分它是否在
    # ALWAYS_REQUIRED 里——实际比 ALWAYS_REQUIRED 更严。这里不再保留一个
    # 「ALWAYS_REQUIRED 专属」的过滤变量：它从未被使用，留着会让人以为跳过有
    # 两种语义（2026-10-27 删）。

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
