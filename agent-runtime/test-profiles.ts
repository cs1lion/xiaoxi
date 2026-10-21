// Profile isolation: workers cannot dispatch; orchestrators cannot write files
// or run local agents.
//
//   node --experimental-strip-types test-profiles.ts

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openAgentRuntime } from "./src/runtime.ts";

let passed = 0;
const failed: string[] = [];
const check = (name: string, ok: boolean, detail = ""): void => {
	if (ok) {
		passed += 1;
		console.log(`  ok   ${name}`);
	} else {
		failed.push(`${name} ${detail}`);
		console.log(`  FAIL ${name} ${detail}`);
	}
};

const directory = mkdtempSync(join(tmpdir(), "xiaoxi-profiles-"));
delete process.env.LLM_BASE_URL;
delete process.env.LLM_MODEL;
process.env.XIAOXI_WORKER_MODE = "script";

try {
	console.log("[durable: profiles]");
	const runtime = await openAgentRuntime(directory);
	const worker = await runtime.createRun({
		commission_id: "c_worker_profile",
		agent_id: "scribe",
		packet: { objective: "写一份标题" },
		profile: "worker",
	});
	const workerTools = await runtime.toolsFor(worker.conversationId);
	check("worker 可写文件", workerTools.includes("write_file"), JSON.stringify(workerTools));
	check("worker 可跑本地执行体", workerTools.includes("run_worker"), JSON.stringify(workerTools));
	check("worker 有只读记忆", workerTools.includes("search_memory"), JSON.stringify(workerTools));
	check("worker 不能派单", !workerTools.includes("dispatch_task"), JSON.stringify(workerTools));
	check("worker 不能审阅/提议验收",
		!workerTools.includes("review_delivery") && !workerTools.includes("propose_accept"),
		JSON.stringify(workerTools));

	const orchestrator = await runtime.createRun({
		commission_id: "c_orchestrator_profile",
		agent_id: "orchestrator",
		packet: { objective: "整理任务", prompt: "整理任务" },
		profile: "orchestrator",
		lease_id: "lease-test",
		session_id: "session-test",
	});
	const orchTools = await runtime.toolsFor(orchestrator.conversationId);
	check("orchestrator 能派单", orchTools.includes("dispatch_task"), JSON.stringify(orchTools));
	check("orchestrator 能跟踪", orchTools.includes("task_status") && orchTools.includes("task_events"),
		JSON.stringify(orchTools));
	check("orchestrator 能答澄清", orchTools.includes("answer_blocked"), JSON.stringify(orchTools));
	check("orchestrator 能只读审阅", orchTools.includes("review_delivery"), JSON.stringify(orchTools));
	check("orchestrator 只能提议验收/打回",
		orchTools.includes("propose_accept") && orchTools.includes("propose_reject"),
		JSON.stringify(orchTools));
	check("orchestrator 没有 commit 权",
		!orchTools.includes("accept_delivery") && !orchTools.includes("reject_delivery"),
		JSON.stringify(orchTools));
	check("orchestrator 有只读记忆", orchTools.includes("search_memory"), JSON.stringify(orchTools));
	check("orchestrator 不能写文件", !orchTools.includes("write_file"), JSON.stringify(orchTools));
	check("orchestrator 不能跑本地执行体", !orchTools.includes("run_worker"), JSON.stringify(orchTools));
	check("orchestrator 不能交差", !orchTools.includes("submit_artifact"), JSON.stringify(orchTools));
	check("orchestrator 没有 MCP 工具", !orchTools.some((name) => name.startsWith("mcp__")),
		JSON.stringify(orchTools));
	await runtime.close();
} finally {
	rmSync(directory, { recursive: true, force: true });
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
