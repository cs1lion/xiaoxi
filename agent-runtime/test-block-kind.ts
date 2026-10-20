// block_and_ask carries a structured kind; the ledger uses it instead of
// guessing from the question text.
//
//   node --experimental-strip-types test-block-kind.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { blockAndAsk } from "./src/worker.ts";
import type { RunEvent } from "./src/types.ts";

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

const events: RunEvent[] = [];
const api = {
	conversationId: "c1",
	commit: async (fn: (tx: unknown) => unknown) =>
		fn({ doc: async () => ({ events }) }),
};

console.log("[durable: block kind]");
await blockAndAsk.execute({ question: "批准 write_file 吗？", kind: "approval" },
	api as never, BACKGROUND_CONTEXT);
await blockAndAsk.execute({ question: "交付目录放哪？" },
	api as never, BACKGROUND_CONTEXT);
const approval = events[0];
const clarification = events[1];
check("approval 结构化落事件", approval.type === "error" && approval.kind === "approval",
	JSON.stringify(approval));
check("默认是 clarification", clarification.type === "error" && clarification.kind === "clarification",
	JSON.stringify(clarification));

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
