// Compaction settings must leave room for the answer and for a cut.
//
//   node --experimental-strip-types test-compaction.ts

import { compactionSettings, contextWindow, maxTokens } from "./src/models.ts";

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

const saved = { ...process.env };
function reset(): void {
	for (const key of ["XIAOXI_CONTEXT_WINDOW", "XIAOXI_MAX_TOKENS", "XIAOXI_RESERVE_TOKENS",
		"XIAOXI_KEEP_RECENT_TOKENS", "XIAOXI_BACKGROUND_TOKENS"]) {
		delete process.env[key];
	}
}

console.log("[durable: compaction settings]");
reset();
const defaults = compactionSettings();
check("默认窗口是 32768", contextWindow() === 32768, String(contextWindow()));
check("默认答案预算是 8000", maxTokens() === 8000, String(maxTokens()));
check("保留区 >= 答案预算", defaults.reserveTokens >= maxTokens(),
	JSON.stringify(defaults));
check("压缩切点留得下", contextWindow() - defaults.reserveTokens > defaults.keepRecentTokens,
	JSON.stringify(defaults));
check("后台压缩阈值非负", defaults.backgroundTokens >= 0, JSON.stringify(defaults));

process.env.XIAOXI_CONTEXT_WINDOW = "131072";
const large = compactionSettings();
check("大窗口保留 12000", large.keepRecentTokens === 12000, JSON.stringify(large));
check("大窗口仍留得下切点", 131072 - large.reserveTokens > large.keepRecentTokens,
	JSON.stringify(large));

process.env.XIAOXI_CONTEXT_WINDOW = "8192";
process.env.XIAOXI_MAX_TOKENS = "8000";
let threw = false;
try {
	compactionSettings();
} catch {
	threw = true;
}
check("窗口装不下答案时显式失败", threw);

reset();
for (const [key, value] of Object.entries(saved)) {
	if (value !== undefined) process.env[key] = value;
}
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
