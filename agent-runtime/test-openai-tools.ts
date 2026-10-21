// Phase 0 gate ②: prove pi-ai speaks OpenAI function calling to a custom
// OpenAI-compatible endpoint, with streaming, Chinese text, and usage.
//
//   node --experimental-strip-types test-openai-tools.ts
//
// The endpoint is a local fake, so this closes the plumbing half of the gate.
// Whether the real proxy forwards `tools` unchanged is a fact only the real
// endpoint can settle; the supervisor logs the raw request for that check.

import { createServer } from "node:http";
import { Type, createModels, createProvider, envApiKeyAuth } from "@earendil-works/pi-ai";
import type { AssistantMessage, Model } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";

const chunks = (payloads: unknown[]): string =>
	payloads.map((payload) => `data: ${JSON.stringify(payload)}\n\n`).join("") + "data: [DONE]\n\n";

const server = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}") as {
		messages?: { role?: string }[];
		tools?: unknown[];
	};
	const sawToolResult = (body.messages ?? []).some((message) => message.role === "tool" || message.role === "toolResult");
	const toolsForwarded = Array.isArray(body.tools) && body.tools.length > 0;

	res.writeHead(200, { "content-type": "text/event-stream" });
	if (!sawToolResult) {
		res.end(
			chunks([
				{ choices: [{ index: 0, delta: { role: "assistant", content: "我先" }, finish_reason: null }] },
				{ choices: [{ index: 0, delta: { content: "交差。" }, finish_reason: null }] },
				{
					choices: [
						{
							index: 0,
							delta: {
								tool_calls: [
									{ index: 0, id: "call_1", type: "function", function: { name: "submit_artifact", arguments: "" } },
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
							delta: { tool_calls: [{ index: 0, function: { arguments: '{"summary":"完成"}' } }] },
							finish_reason: null,
						},
					],
				},
				{
					choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
					usage: { prompt_tokens: 12, completion_tokens: 7, total_tokens: 19 },
				},
			]),
		);
		return;
	}
	res.end(
		chunks([
			{ choices: [{ index: 0, delta: { role: "assistant", content: "好的，收到。" }, finish_reason: null }] },
			{
				choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
				usage: { prompt_tokens: 21, completion_tokens: 4, total_tokens: 25 },
			},
		]),
	);
});

await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (address === null || typeof address !== "object") throw new Error("no port");
const baseUrl = `http://127.0.0.1:${address.port}`;

const model: Model<"openai-completions"> = {
	id: "fake-1",
	name: "Fake",
	api: "openai-completions",
	provider: "xiaoxi" as Model["provider"],
	baseUrl,
	input: ["text"],
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } as unknown as Model["cost"],
	reasoning: false,
	contextWindow: 8192,
	maxTokens: 1024,
};

const provider = createProvider({
	id: "xiaoxi",
	name: "Xiaoxi",
	baseUrl,
	auth: { apiKey: envApiKeyAuth("Xiaoxi LLM", ["XIAOXI_TEST_KEY"]) },
	models: [model],
	api: openAICompletionsApi(),
});
const models = createModels();
models.setProvider(provider);

const tool = {
	name: "submit_artifact",
	description: "交差",
	parameters: Type.Object({ summary: Type.String() }),
};

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

async function run(messages: unknown[]) {
	const stream = models.streamSimple(
		model,
		{ systemPrompt: "你是执行体。", messages: messages as never, tools: [tool] },
		{ apiKey: "test-key" },
	);
	const events: { type: string; [key: string]: unknown }[] = [];
	for await (const event of stream) events.push(event as never);
	const done = events.find((event) => event.type === "done");
	return { events, message: done?.message as AssistantMessage | undefined };
}

console.log("[phase 0: custom openai-compatible endpoint]");
if (process.env.DEBUG) { /* debug hook */ }
const first = await run([{ role: "user", content: "干活" }]);
const text = first.events
	.filter((event) => event.type === "text_delta")
	.map((event) => String(event.delta ?? ""))
	.join("");
check("流式中文文本", text === "我先交差。", JSON.stringify(text));
const started = first.events.filter((event) => event.type === "toolcall_start");
const ended = first.events.filter((event) => event.type === "toolcall_end");
check("工具调用开始/结束事件都在", started.length === 1 && ended.length === 1);
const blocks = (first.message as { content?: { type: string; name?: string; arguments?: unknown }[] } | undefined)?.content ?? [];
const call = blocks.filter((block) => block.type === "toolCall");
check("工具参数被解析成对象", JSON.stringify(call[0]?.arguments) === '{"summary":"完成"}', JSON.stringify(call));
const usage = (first.message as { usage?: { input?: number } } | undefined)?.usage;
check("usage 被读出来", usage?.input === 12, JSON.stringify(usage));

if (process.env.DEBUG) console.log("MSG", JSON.stringify(first.message));
const second = await run([
	{ role: "user", content: "干活" },
	{ role: "toolResult", toolCallId: "call_1", toolName: "submit_artifact",
		content: [{ type: "text", text: '{"summary":"完成"}' }], isError: false, timestamp: Date.now() },
]);
const secondText = second.events
	.filter((event) => event.type === "text_delta")
	.map((event) => String(event.delta ?? ""))
	.join("");
if (process.env.DEBUG) console.log("EV2", JSON.stringify(second.events));
check("工具结果回来后能继续对话", secondText === "好的，收到。", JSON.stringify(secondText));

server.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
