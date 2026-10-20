// Phase 0 gate ②, real endpoint half: does the actual OpenAI-compatible
// endpoint forward `tools` and return function calls?
//
//   node --experimental-strip-types test-live-tools.ts
//
// Skips (exit 0) when LLM_API_KEY is absent. One small request, no retries.

import { createModels, createProvider, envApiKeyAuth } from "@earendil-works/pi-ai";
import type { Model } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { Type } from "@earendil-works/pi-ai";

const apiKey = process.env.LLM_API_KEY ?? "";
if (apiKey === "") {
	console.log("SKIP live tools: LLM_API_KEY 未配置");
	process.exit(0);
}
const baseUrl = (process.env.LLM_BASE_URL ?? "https://api.deepseek.com").replace(/\/+$/, "");
const modelId = process.env.LLM_MODEL ?? "deepseek-chat";

const model: Model<"openai-completions"> = {
	id: modelId,
	name: modelId,
	api: "openai-completions",
	provider: "xiaoxi" as Model["provider"],
	baseUrl,
	input: ["text"],
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } as unknown as Model["cost"],
	reasoning: false,
	contextWindow: 32768,
	maxTokens: 512,
};

const provider = createProvider({
	id: "xiaoxi",
	name: "Xiaoxi",
	baseUrl,
	auth: { apiKey: envApiKeyAuth("Xiaoxi LLM", ["LLM_API_KEY"]) },
	models: [model],
	api: openAICompletionsApi(),
});
const models = createModels();
models.setProvider(provider);

let outboundTools = -1;
const stream = models.streamSimple(
	model,
	{
		systemPrompt: "你是执行体。需要交差时必须调用工具，不要用文字代替。",
		messages: [{ role: "user", content: "用 submit_artifact 交差，summary 填 ok。" }],
		tools: [
			{
				name: "submit_artifact",
				description: "交差",
				parameters: Type.Object({ summary: Type.String() }),
			},
		],
	},
	{
		apiKey,
		maxTokens: 256,
		onPayload: (payload) => {
			const body = payload as { tools?: unknown[] };
			outboundTools = Array.isArray(body.tools) ? body.tools.length : 0;
			return undefined;
		},
	},
);

const events: { type: string }[] = [];
for await (const event of stream) events.push(event as never);
const done = events.find((event) => event.type === "done") as
	| { message?: { content?: { type: string; name?: string }[] } }
	| undefined;
const toolCalls = (done?.message?.content ?? []).filter((block) => block.type === "toolCall");

console.log(`outbound tools: ${outboundTools}`);
console.log(`tool calls: ${toolCalls.map((block) => block.name ?? "?").join(",") || "(none)"}`);
console.log(`events: ${[...new Set(events.map((event) => event.type))].join(",")}`);

if (outboundTools <= 0) {
	console.log("=== FAIL: 请求没有带 tools ===");
	process.exit(1);
}
if (toolCalls.length === 0) {
	console.log("=== FAIL: 端点收到了 tools，但模型没有返回 function call ===");
	process.exit(1);
}
console.log("=== PASS: 真实端点透传 tools 并返回 function call ===");
