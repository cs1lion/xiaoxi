// Automatic pi-durable compaction over a real multi-turn conversation.
// The fake endpoint answers normal turns, and answers the summarization prompt
// when pi-durable asks for a checkpoint. No real model calls.
//
//   node --experimental-strip-types test-compaction-live.ts

import { createServer } from "node:http";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const events = (payloads: unknown[]): string =>
	payloads.map((payload) => `data: ${JSON.stringify(payload)}\n\n`).join("") + "data: [DONE]\n\n";

let normalCalls = 0;
let summaryCalls = 0;
const server = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}") as {
		messages?: { role?: string; content?: unknown }[];
	};
	const system = (body.messages ?? [])
		.filter((message) => message.role === "system")
		.map((message) => typeof message.content === "string" ? message.content : JSON.stringify(message.content))
		.join("\n");
	const summarizing = system.includes("context summarization assistant");
	res.writeHead(200, { "content-type": "text/event-stream" });
	if (summarizing) {
		summaryCalls += 1;
		const summary = [
			"## Goal", "- 验证长对话自动压缩。",
			"## Constraints & Preferences", "- (none)",
			"## Progress", "### Done", "- 已压缩旧对话。",
			"### In Progress", "- (none)", "### Blocked", "- (none)",
			"## Key Decisions", "- (none)", "## Next Steps", "- 继续回答。",
			"## Critical Context", "- (none)",
		].join("\n");
		res.end(events([
			{ choices: [{ index: 0, delta: { role: "assistant", content: summary }, finish_reason: null }] },
			{ choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
				usage: { prompt_tokens: 300, completion_tokens: 60, total_tokens: 360 } },
		]));
		return;
	}
	normalCalls += 1;
	const text = `第 ${normalCalls} 轮回答：` + "这是一段用于撑大上下文的长文本。".repeat(80);
	// The first two responses report a context above the blocking threshold. The
	// later ones are small so the run can finish after compaction instead of
	// compacting every turn.
	const promptTokens = normalCalls <= 2 ? 3000 : 400;
	res.end(events([
		{ choices: [{ index: 0, delta: { role: "assistant", content: text }, finish_reason: null }] },
		{ choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
			usage: { prompt_tokens: promptTokens, completion_tokens: 100, total_tokens: promptTokens + 100 } },
	]));
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (address === null || typeof address !== "object") throw new Error("no port");

process.env.LLM_BASE_URL = `http://127.0.0.1:${address.port}`;
process.env.LLM_MODEL = "fake-compact-1";
process.env.LLM_API_KEY = "test-key";
process.env.XIAOXI_CONTEXT_WINDOW = "4096";
process.env.XIAOXI_MAX_TOKENS = "512";
process.env.XIAOXI_RESERVE_TOKENS = "1536";
process.env.XIAOXI_KEEP_RECENT_TOKENS = "1024";
process.env.XIAOXI_BACKGROUND_TOKENS = "0";

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

const directory = mkdtempSync(join(tmpdir(), "xiaoxi-compact-"));
const runtime = await openAgentRuntime(directory);
try {
	const root = await runtime.harness.root(BACKGROUND_CONTEXT, {
		agent: { model: { provider: "xiaoxi", modelId: "fake-compact-1" } },
	});
	console.log("[durable: live compaction]");
	for (let turn = 1; turn <= 4; turn += 1) {
		const submission = await root.submit({
			type: "input",
			content: `第 ${turn} 轮：继续讨论这个长任务。` + "需要保留的上下文。".repeat(30),
			requestId: `compact-test-${turn}`,
		}, BACKGROUND_CONTEXT);
		const settled = await submission.wait(BACKGROUND_CONTEXT);
		check(`第 ${turn} 轮完成`, settled.status === "done", JSON.stringify(settled).slice(0, 300));
		const page = await root.entries({ order: "ascending" }, 300, undefined, BACKGROUND_CONTEXT);
		const compaction = page.items.find((entry) => entry.kind === "pi.compaction");
		if (compaction !== undefined) {
			check("产生 pi.compaction 条目", true);
			check("摘要请求真的发出", summaryCalls >= 1, String(summaryCalls));
			check("压缩后对话仍可继续", turn >= 2, String(turn));
			break;
		}
		if (turn === 4) {
			check("产生 pi.compaction 条目", false, JSON.stringify(page.items.map((entry) => entry.kind)));
			check("摘要请求真的发出", summaryCalls >= 1, String(summaryCalls));
		}
	}
	check("正常对话请求发生过", normalCalls >= 2, String(normalCalls));
} finally {
	await runtime.close();
	server.close();
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
