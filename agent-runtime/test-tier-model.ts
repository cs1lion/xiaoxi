// The roster's cost tier has to pick the model, or a cheap-tier worker silently
// runs on the expensive one (or the other way round). §4.4 of
// docs/PI_DURABLE_MIGRATION.md calls this out as a free win; it is only free if
// the mapping is actually wired per conversation.
//
//   node --experimental-strip-types test-tier-model.ts
//
// No model calls: the assertion is on which model the conversation resolved to.

import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { BACKGROUND_CONTEXT } = await import("@earendil-works/chord/context");
const { modelForTier, modelsFromEnv } = await import("./src/models.ts");
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

console.log("[tier -> model]");
process.env.LLM_BASE_URL = "http://127.0.0.1:9";
process.env.LLM_MODEL = "base-model";
delete process.env.XIAOXI_MODEL_CHEAP;
delete process.env.XIAOXI_MODEL_MEDIUM;
delete process.env.XIAOXI_MODEL_HIGH;
check("没配覆盖就都落回 LLM_MODEL", modelForTier("cheap", "base") === "base"
	&& modelForTier("high", "base") === "base");
process.env.XIAOXI_MODEL_CHEAP = "step-3.7-flash";
process.env.XIAOXI_MODEL_HIGH = "step-5-preview";
check("覆盖按档生效", modelForTier("cheap", "base") === "step-3.7-flash"
	&& modelForTier("high", "base") === "step-5-preview");
check("没覆盖的档仍落回基准", modelForTier("medium", "base") === "base");
check("档名缺失不猜", modelForTier(undefined, "base") === "base");
check("档名乱填不猜", modelForTier("expensive", "base") === "base");
// The catalog has to carry every tier's model, or the per-conversation selection
// resolves to something the provider cannot serve.
const catalog = modelsFromEnv();
check("三个档的模型都注册进了目录",
	catalog.getModel("xiaoxi", "step-3.7-flash") !== undefined
	&& catalog.getModel("xiaoxi", "step-5-preview") !== undefined
	&& catalog.getModel("xiaoxi", "base-model") !== undefined,
	JSON.stringify(catalog.getModels("xiaoxi").map((entry) => entry.id)));

const directory = mkdtempSync(join(tmpdir(), "xiaoxi-tier-"));
const runtime = await openAgentRuntime(directory);
const agentModel = async (cost_tier: string | undefined) => {
	const ref = await runtime.createRun({
		commission_id: `c_tier_${cost_tier ?? "none"}`,
		agent_id: "w1",
		packet: { objective: "x" },
		cost_tier,
	});
	const conversation = await runtime.harness.conversation(ref.conversationId as never, BACKGROUND_CONTEXT);
	if (conversation === undefined) return undefined;
	const agent = await conversation.agent(BACKGROUND_CONTEXT);
	return agent.model;
};
check("cheap 档选中廉价模型", String((await agentModel("cheap"))?.modelId) === "step-3.7-flash",
	JSON.stringify(await agentModel("cheap")));
check("high 档选中贵模型", String((await agentModel("high"))?.modelId) === "step-5-preview",
	JSON.stringify(await agentModel("high")));
check("没带档就落回基准", String((await agentModel(undefined))?.modelId) === "base-model",
	JSON.stringify(await agentModel(undefined)));

await runtime.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
