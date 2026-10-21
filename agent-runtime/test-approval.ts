// Phase 2 approval gate: a tool on the approval list is blocked until the host
// grants it, and the block is visible to the model instead of being retried.
//
//   node --experimental-strip-types test-approval.ts

import { createServer } from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const requests: string[] = [];

const events = (payloads: unknown[]): string =>
	payloads.map((payload) => `data: ${JSON.stringify(payload)}\n\n`).join("") + "data: [DONE]\n\n";

const server = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const raw = Buffer.concat(parts).toString("utf8") || "{}";
	requests.push(raw);
	const body = JSON.parse(raw) as { messages?: { role?: string }[] };
	const sawToolResult = (body.messages ?? []).some((message) => message.role === "tool");
	res.writeHead(200, { "content-type": "text/event-stream" });
	if (sawToolResult) {
		res.end(
			events([
				{ choices: [{ index: 0, delta: { role: "assistant", content: "收到。" }, finish_reason: null }] },
				{
					choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
					usage: { prompt_tokens: 20, completion_tokens: 2, total_tokens: 22 },
				},
			]),
		);
		return;
	}
	res.end(
		events([
			{
				choices: [
					{
						index: 0,
						delta: {
							tool_calls: [
								{ index: 0, id: "c1", type: "function", function: { name: "submit_artifact", arguments: "" } },
							],
						},
						finish_reason: null,
					},
				],
			},
			{
				choices: [
					{
						index: 0,
						delta: {
							tool_calls: [
								{
									index: 0,
									function: {
										arguments: JSON.stringify({
											summary: "三版",
											intercepted: "无",
											artifacts: [{ kind: "note", ref: "note:n_1" }],
											wired: true,
										}),
									},
								},
							],
						},
						finish_reason: null,
					},
				],
			},
			{
				choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
				usage: { prompt_tokens: 12, completion_tokens: 6, total_tokens: 18 },
			},
		]),
	);
});

await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (address === null || typeof address !== "object") throw new Error("no port");
process.env.LLM_BASE_URL = `http://127.0.0.1:${address.port}`;
process.env.LLM_MODEL = "fake-1";
process.env.LLM_API_KEY = "test-key";
process.env.XIAOXI_WORKER_MODE = "model";
process.env.XIAOXI_APPROVAL_TOOLS = "submit_artifact";
delete process.env.XIAOXI_APPROVED_TOOLS;

const { BACKGROUND_CONTEXT } = await import("@earendil-works/chord/context");
const { openAgentRuntime } = await import("./src/runtime.ts");
const context = BACKGROUND_CONTEXT;

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

async function runOnce(commissionId: string) {
	const runtime = await openAgentRuntime(mkdtempSync(join(tmpdir(), "xiaoxi-approval-")));
	const ref = await runtime.createRun({ commission_id: commissionId, agent_id: "w1", packet: { objective: "干活" } });
	const settled = await runtime.harness.waitForTask(ref.taskId, context);
	const view = await runtime.readRun(ref.taskId);
	await runtime.close();
	return { outcome: settled.state.outcome as { status: string }, view };
}

console.log("[phase 2: approval gate]");
const denied = await runOnce("c_denied");
check("未批准时交不了差", denied.outcome.status === "failed", JSON.stringify(denied.outcome));
check("阻断信息传到了模型",
	requests.some((body) => body.includes("需要老板确认")), "请求里没看到阻断原因");

requests.length = 0;
process.env.XIAOXI_APPROVED_TOOLS = "submit_artifact";
const granted = await runOnce("c_granted");
check("批准后正常交差", granted.outcome.status === "completed", JSON.stringify(granted.outcome));
check("批准后不再阻断", !requests.some((body) => body.includes("需要老板确认")));

server.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
