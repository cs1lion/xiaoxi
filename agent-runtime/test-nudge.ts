// A model that answers in prose instead of calling the delivery tool is common
// (about one run in five with step-5-preview). The run must recover with one
// nudge in the same conversation rather than failing the commission.
//
//   node --experimental-strip-types test-nudge.ts
//
// The endpoint is a local fake, so this is deterministic and offline.

import { createServer } from "node:http";

const events = (payloads: unknown[]): string =>
	payloads.map((payload) => `data: ${JSON.stringify(payload)}\n\n`).join("") + "data: [DONE]\n\n";

const toolCall = (): unknown[] => [
	{ choices: [{ index: 0, delta: { role: "assistant", content: "交差。" }, finish_reason: null }] },
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
									summary: "催过一次之后才交差",
									intercepted: "没有",
									artifacts: [{ kind: "note", ref: "note:n_9" }],
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
	{ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 40, completion_tokens: 12, total_tokens: 52 } },
];

// Prose only, on the first turn. The nudge turn is recognized by the user text.
const server = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}") as {
		messages?: { role?: string; content?: unknown }[];
	};
	const nudged = JSON.stringify(body.messages ?? []).includes("你还没有交差");
	res.writeHead(200, { "content-type": "text/event-stream" });
	if (nudged) {
		res.end(events(toolCall()));
		return;
	}
	res.end(
		events([
			{ choices: [{ index: 0, delta: { role: "assistant", content: "三版标题如下：一、二、三。" }, finish_reason: null }] },
			{ choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 } },
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

const { BACKGROUND_CONTEXT } = await import("@earendil-works/chord/context");
const { mkdtempSync } = await import("node:fs");
const { tmpdir } = await import("node:os");
const { join } = await import("node:path");
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

console.log("[nudge: prose first, deliver on the nudge]");
const directory = mkdtempSync(join(tmpdir(), "xiaoxi-nudge-"));
const runtime = await openAgentRuntime(directory);
const ref = await runtime.createRun({
	commission_id: "c_nudge",
	agent_id: "w1",
	packet: { objective: "理三版标题", deliverable: "按密件要求交付" },
});
const settled = await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
const view = await runtime.readRun(ref.taskId);

const outcome = settled.state.outcome as { status: string; result?: { artifact?: { summary?: string } } };
check("催一次之后任务完成", outcome.status === "completed", JSON.stringify(outcome).slice(0, 200));
check("产物来自第二轮调用", outcome.result?.artifact?.summary === "催过一次之后才交差",
	JSON.stringify(outcome.result));
const errors = (view?.events ?? []).filter((event) => event.type === "error");
check("没有留下失败痕迹", errors.length === 0, JSON.stringify(errors).slice(0, 200));

await runtime.close();
server.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
