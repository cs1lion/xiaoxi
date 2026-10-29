// Profile isolation: workers cannot dispatch; orchestrators cannot write files
// or run local agents.
//
//   node --experimental-strip-types test-profiles.ts

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRpcServer } from "./src/rpc.ts";
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

	// M4：toolsForTask 按 task id 反查，与 toolsFor 同源；内核里没有这个 task
	// 时回 undefined，让只读路由回 404 而不是空清单。
	const workerToolsByTask = await runtime.toolsForTask(worker.taskId);
	check("toolsForTask 与 toolsFor 一致",
		JSON.stringify(workerToolsByTask) === JSON.stringify(workerTools),
		JSON.stringify(workerToolsByTask));
	const absentTask = await runtime.toolsForTask(4_242_424);
	check("内核里没有这个 task 时回 undefined（不是空清单）",
		absentTask === undefined, String(absentTask));

	// M4 复审 MINOR-3：runtime 层的 undefined 语义上面钉了，这里钉路由层真的
	// 把它变成 404、把真 run 变成 200。createRpcServer 此前全仓无测试 import，
	// 这条分支（M4 核心新增物的承重墙）一直没有回归看守。
	const rpcServer = createRpcServer({ runtime, token: "test-token" });
	await new Promise<void>((resolve) => rpcServer.listen(0, "127.0.0.1", resolve));
	const rpcPort = (rpcServer.address() as { port: number }).port;
	const authHeaders = { authorization: "Bearer test-token" };
	const absentRes = await fetch(
		`http://127.0.0.1:${rpcPort}/v1/runs/4242424/tools`, { headers: authHeaders });
	check("路由层：不存在的 task 回 404 not_found",
		absentRes.status === 404 && ((await absentRes.json()) as { error?: string }).error === "not_found",
		String(absentRes.status));
	const presentRes = await fetch(
		`http://127.0.0.1:${rpcPort}/v1/runs/${worker.taskId}/tools`, { headers: authHeaders });
	check("路由层：带 token 查真 run 回 200 且工具非空",
		presentRes.status === 200
		&& (((await presentRes.json()) as { tools?: string[] }).tools ?? []).length > 0,
		String(presentRes.status));
	const noTokenRes = await fetch(`http://127.0.0.1:${rpcPort}/v1/runs/${worker.taskId}/tools`);
	check("路由层：不带 token 回 401", noTokenRes.status === 401, String(noTokenRes.status));
	await new Promise<void>((resolve) => {
		rpcServer.close(() => resolve());
		rpcServer.closeAllConnections();
	});

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
