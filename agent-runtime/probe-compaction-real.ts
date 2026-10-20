// Real-model compaction probe: forces pi-durable to summarize with the
// configured LLM, then checks that an early fact survives the summary and that
// the conversation can continue. Temporary directory only; costs a few dozen
// thousand tokens.
//
//   node --env-file=../.env --experimental-strip-types probe-compaction-real.ts

import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";

// Production-like budget from config/agents_roster.yaml (32k window, 8k answer).
// The summarizer shares the answer cap, so a tiny cap truncates real summaries.
process.env.XIAOXI_CONTEXT_WINDOW = "32768";
process.env.XIAOXI_MAX_TOKENS = "8000";
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

// Keep each message well below keepRecentTokens so compaction can cut at a
// message boundary. The oversized-message boundary is covered separately by
// the read_file/MCP caps.
const PAD = ("项目要求：整理迁移记录，覆盖上下文压缩、技能目录、MCP 工具桥与本地 Agent 接管；"
	+ "每条结论都要有证据，不要补写未验证的事实。").repeat(80);
const FACTS = [
	"暗号是青石桥-8472。",
	"硬约束：最终报告只能写三条结论。",
	"已决定先写标题，再写摘要。",
];

const directory = mkdtempSync(join(tmpdir(), "xiaoxi-compact-real-"));
const runtime = await openAgentRuntime(directory);
try {
	const root = await runtime.harness.root(BACKGROUND_CONTEXT, {
		agent: { model: { provider: "xiaoxi", modelId: process.env.LLM_MODEL ?? "step-3.7-flash" } },
	});
	console.log("[durable: real compaction]");
	console.log(`  model=${process.env.LLM_MODEL ?? ""} dir=${directory}`);
	let compaction: { content?: unknown; data?: unknown } | undefined;
	const MAX_TURNS = 8;
	for (let turn = 1; turn <= MAX_TURNS; turn += 1) {
		const fact = FACTS[turn - 1] ?? `继续讨论第 ${turn} 项要求，仍然只依据已给材料。`;
		const submission = await root.submit({
			type: "input",
			content: `第 ${turn} 轮：${fact}\n${PAD}`,
			requestId: `compact-real-${turn}`,
		}, BACKGROUND_CONTEXT);
		const settled = await submission.wait(BACKGROUND_CONTEXT);
		check(`第 ${turn} 轮完成`, settled.status === "done", JSON.stringify(settled).slice(0, 300));
		const page = await root.entries({ order: "ascending" }, 500, undefined, BACKGROUND_CONTEXT);
		compaction = page.items.find((entry) => entry.kind === "pi.compaction") as typeof compaction;
		if (compaction !== undefined) {
			console.log(`  compaction 出现在第 ${turn} 轮`);
			break;
		}
		if (turn === MAX_TURNS) {
			check("产生 pi.compaction 条目", false,
				JSON.stringify(page.items.map((entry) => entry.kind)));
		}
	}
	if (compaction !== undefined) {
		check("产生 pi.compaction 条目", true);
		const text = JSON.stringify(compaction);
		console.log("  summary entry:", text.slice(0, 1600));
		// The summary may render the separator with spaces; the end-to-end check
		// below is the strict one. Here we only require the unique digits.
		check("摘要保留了暗号", text.includes("8472"));
		check("摘要长度可控", text.length < 20000, String(text.length));
		const after = await root.submit({
			type: "input",
			content: "根据上面的压缩摘要回答：第一轮最开始留下的暗号是什么？只回答暗号本身。",
			requestId: "compact-real-after",
		}, BACKGROUND_CONTEXT);
		const settled = await after.wait(BACKGROUND_CONTEXT);
		check("压缩后对话仍能完成", settled.status === "done", JSON.stringify(settled).slice(0, 300));
		const finalPage = await root.entries({ order: "ascending" }, 500, undefined, BACKGROUND_CONTEXT);
		const assistant = [...finalPage.items].reverse().find((entry) => entry.kind === "pi.assistant");
		const reply = JSON.stringify(assistant ?? {});
		console.log("  final reply:", reply.slice(0, 500));
		check("压缩后回答还记得暗号", reply.includes("青石桥-8472"), reply.slice(0, 300));
		const followUp = await root.submit({
			type: "input",
			content: "最后确认：最终报告有什么硬约束？一句话。",
			requestId: "compact-real-constraint",
		}, BACKGROUND_CONTEXT);
		const followSettled = await followUp.wait(BACKGROUND_CONTEXT);
		check("压缩后继续第二轮也完成", followSettled.status === "done",
			JSON.stringify(followSettled).slice(0, 300));
		const lastPage = await root.entries({ order: "ascending" }, 500, undefined, BACKGROUND_CONTEXT);
		const lastAssistant = [...lastPage.items].reverse().find((entry) => entry.kind === "pi.assistant");
		const constraint = JSON.stringify(lastAssistant ?? {});
		console.log("  constraint reply:", constraint.slice(0, 500));
		check("压缩后仍能读回硬约束", constraint.includes("三条"), constraint.slice(0, 300));
	}
} finally {
	await runtime.close();
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
