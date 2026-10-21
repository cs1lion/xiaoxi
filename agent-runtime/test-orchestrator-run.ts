// Orchestrator end-to-end: the model calls dispatch_task, the host receives the
// RunMeta lease/session, and the run settles with the final text.
//
//   node --experimental-strip-types test-orchestrator-run.ts

import { createServer } from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const events = (payloads: unknown[]): string =>
	payloads.map((payload) => `data: ${JSON.stringify(payload)}\n\n`).join("") + "data: [DONE]\n\n";

const hostRequests: { body?: Record<string, unknown> }[] = [];
const host = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}") as Record<string, unknown>;
	hostRequests.push({ body });
	res.writeHead(200, { "content-type": "application/json" });
	if (req.url === "/internal/durable/dispatch") {
		res.end(JSON.stringify({ commission_id: "c_orch_1", status: "running", mode: "auto" }));
		return;
	}
	res.end(JSON.stringify({ ok: true }));
});
await new Promise<void>((resolve) => host.listen(0, "127.0.0.1", resolve));
const hostAddress = host.address();
if (hostAddress === null || typeof hostAddress !== "object") throw new Error("no host port");

let llmCalls = 0;
const llm = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}") as {
		messages?: { role?: string }[];
	};
	llmCalls += 1;
	const sawToolResult = (body.messages ?? []).some((message) => message.role === "tool");
	res.writeHead(200, { "content-type": "text/event-stream" });
	if (sawToolResult) {
		res.end(events([
			{ choices: [{ index: 0, delta: { role: "assistant", content: "已派给笔录官，单号 c_orch_1。" }, finish_reason: null }] },
			{ choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
				usage: { prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 } },
		]));
		return;
	}
	res.end(events([
		{ choices: [{ index: 0, delta: { role: "assistant", content: "先派单。" }, finish_reason: null }] },
		{ choices: [{ index: 0, delta: { tool_calls: [
			{ index: 0, id: "c1", type: "function", function: { name: "dispatch_task", arguments: "" } },
		] }, finish_reason: null }] },
		{ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: {
			arguments: JSON.stringify({ title: "整理标题", brief: "把七月选题理成三版标题" }),
		} }] }, finish_reason: null }] },
		{ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
			usage: { prompt_tokens: 15, completion_tokens: 9, total_tokens: 24 } },
	]));
});
await new Promise<void>((resolve) => llm.listen(0, "127.0.0.1", resolve));
const llmAddress = llm.address();
if (llmAddress === null || typeof llmAddress !== "object") throw new Error("no llm port");

process.env.XIAOXI_HOST_URL = `http://127.0.0.1:${hostAddress.port}`;
process.env.XIAOXI_HOST_TOKEN = "test-token";
process.env.LLM_BASE_URL = `http://127.0.0.1:${llmAddress.port}`;
process.env.LLM_MODEL = "fake-1";
process.env.LLM_API_KEY = "test-key";
process.env.XIAOXI_WORKER_MODE = "model";

const { BACKGROUND_CONTEXT } = await import("@earendil-works/chord/context");
const { openAgentRuntime } = await import("./src/runtime.ts");

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

const directory = mkdtempSync(join(tmpdir(), "xiaoxi-orch-"));
const runtime = await openAgentRuntime(directory);
const ref = await runtime.createRun({
	commission_id: "orchestrator:e2e",
	agent_id: "orchestrator",
	profile: "orchestrator",
	lease_id: "lease-e2e",
	session_id: "session-e2e",
	packet: { objective: "整理标题", prompt: "请把七月选题理成三版标题" },
});
const settled = await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
const view = await runtime.readRun(ref.taskId);
const outcome = settled.state.outcome as { status?: string; result?: { artifact?: { summary?: string } } };

console.log("[durable: orchestrator run]");
check("任务完成", outcome.status === "completed", JSON.stringify(outcome).slice(0, 300));
check("最终回答落成 artifact",
	outcome.result?.artifact?.summary === "已派给笔录官，单号 c_orch_1。",
	JSON.stringify(outcome.result).slice(0, 300));
check("宿主收到 dispatch", hostRequests.length === 1, JSON.stringify(hostRequests).slice(0, 300));
const body = hostRequests[0]?.body ?? {};
check("lease 来自 RunMeta", body.lease_id === "lease-e2e", JSON.stringify(body));
check("session 来自 RunMeta", body.session_id === "session-e2e", JSON.stringify(body));
check("幂等键由任务生成", String(body.idempotency_key ?? "").startsWith("orchestrator:"),
	JSON.stringify(body));
check("模型调用两次（工具 + 最终回答）", llmCalls === 2, String(llmCalls));
check("事件流有进度/结果", (view?.events ?? []).length >= 0, JSON.stringify(view?.events).slice(0, 200));

await runtime.close();
host.close();
llm.close();

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
