import { createModels, createProvider, envApiKeyAuth } from "@earendil-works/pi-ai";
import type { Model } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";

export interface ModelEnv {
	baseUrl: string;
	modelId: string;
}

// The project talks to an OpenAI-compatible proxy, so the provider is built
// here instead of relying on the generated model catalog.
export function modelEnv(): ModelEnv | undefined {
	const baseUrl = (process.env.LLM_BASE_URL ?? "").trim().replace(/\/+$/, "");
	const modelId = (process.env.LLM_MODEL ?? "").trim();
	if (baseUrl === "" || modelId === "") return undefined;
	return { baseUrl, modelId };
}

// A reasoning model spends its whole completion budget before it says anything:
// step-5-preview returned `finish_reason: "length"` with empty content and no tool
// call at 1200, and delivered the artifact at 1188 once the ceiling was raised.
// 1200 therefore fails the run without the model ever getting a turn.
// Deliberately NOT `LLM_MAX_TOKENS`: that one is the fast loop's short-reply
// budget (400 in .env) and would be far too small here.
const DEFAULT_MAX_TOKENS = 8000;
const DEFAULT_CONTEXT_WINDOW = 32768;

function positiveInt(variable: string, fallback: number): number {
	const raw = Number.parseInt((process.env[variable] ?? "").trim(), 10);
	return Number.isFinite(raw) && raw > 0 ? raw : fallback;
}

export function maxTokens(): number {
	return positiveInt("XIAOXI_MAX_TOKENS", DEFAULT_MAX_TOKENS);
}

export function contextWindow(): number {
	return positiveInt("XIAOXI_CONTEXT_WINDOW", DEFAULT_CONTEXT_WINDOW);
}

// Pi's built-in compaction policy assumes a much larger window (reserve 16384,
// keep 20000). Against a 32k window that leaves no room to cut, so the sidecar
// resolves its own values here and asserts the relationship the scheduler needs:
//
//   contextWindow - reserveTokens > keepRecentTokens
//
// `reserveTokens` must also cover the model's answer budget. Without that, a
// compaction can succeed and the very next request still overflows.
export function compactionSettings(): {
	reserveTokens: number;
	keepRecentTokens: number;
	backgroundTokens: number;
} {
	const window = contextWindow();
	const answer = maxTokens();
	if (window <= answer + 2048) {
		throw new Error(
			`XIAOXI_CONTEXT_WINDOW (${window}) is too small for XIAOXI_MAX_TOKENS (${answer}); leave at least 2048 tokens for context`,
		);
	}
	const reserve = Math.max(
		answer + 1024,
		positiveInt("XIAOXI_RESERVE_TOKENS", Math.max(answer + 1024, 4096)),
	);
	const room = window - reserve;
	if (room <= 2048) {
		throw new Error(
			`compaction reserve (${reserve}) leaves only ${room} tokens of a ${window}-token window`,
		);
	}
	const requestedKeep = positiveInt(
		"XIAOXI_KEEP_RECENT_TOKENS",
		Math.min(12000, Math.floor(room / 2)),
	);
	const keepRecentTokens = Math.max(1024, Math.min(requestedKeep, room - 1024));
	const backgroundRaw = Number.parseInt((process.env.XIAOXI_BACKGROUND_TOKENS ?? "").trim(), 10);
	const backgroundRequested = Number.isFinite(backgroundRaw) && backgroundRaw >= 0 ? backgroundRaw : 4096;
	const backgroundTokens = Math.min(
		backgroundRequested,
		4096,
		Math.max(0, Math.floor((room - keepRecentTokens) / 2)),
	);
	return { reserveTokens: reserve, keepRecentTokens, backgroundTokens };
}

// The roster's three cost tiers (config/agents_roster.yaml), mapped to model ids
// the gateway serves. Per-conversation model selection is the cheap win the
// migration doc calls out in §4.4: 琐事走廉价档. A tier with no override falls
// back to LLM_MODEL, so a deployment that sets one model still works.
const TIERS = ["cheap", "medium", "high"] as const;
export type CostTier = (typeof TIERS)[number];

export function isCostTier(value: unknown): value is CostTier {
	return typeof value === "string" && (TIERS as readonly string[]).includes(value);
}

export function modelForTier(tier: string | undefined, fallback: string): string {
	if (tier === undefined) return fallback;
	const override = (process.env[`XIAOXI_MODEL_${tier.toUpperCase()}`] ?? "").trim();
	return override === "" ? fallback : override;
}

function model(baseUrl: string, modelId: string): Model<"openai-completions"> {
	return {
		id: modelId,
		name: modelId,
		api: "openai-completions",
		provider: "xiaoxi" as Model["provider"],
		baseUrl,
		input: ["text"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } as unknown as Model["cost"],
		reasoning: false,
		contextWindow: contextWindow(),
		maxTokens: maxTokens(),
	};
}

export function modelsFromEnv(): ReturnType<typeof createModels> {
	const models = createModels();
	const env = modelEnv();
	if (env === undefined) return models;
	const ids = TIERS.map((tier) => modelForTier(tier, env.modelId));
	const catalog = [...new Set(ids)].map((id) => model(env.baseUrl, id));
	models.setProvider(
		createProvider({
			id: "xiaoxi",
			name: "Xiaoxi",
			baseUrl: env.baseUrl,
			auth: { apiKey: envApiKeyAuth("Xiaoxi LLM", ["LLM_API_KEY"]) },
			models: catalog,
			api: openAICompletionsApi(),
		}),
	);
	return models;
}
