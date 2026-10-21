// Phase 3 primitive: a background task that may run for a long time, checkpoint
// per step, and does not block the conversation's idle wait.
//
//   node --experimental-strip-types test-background.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BackgroundTask } from "./src/background.ts";
import { RunEvents } from "./src/docs.ts";
import { openAgentRuntime } from "./src/runtime.ts";

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

console.log("[phase 3: background task]");
const runtime = await openAgentRuntime(mkdtempSync(join(tmpdir(), "xiaoxi-bg-")));
const root = await runtime.harness.root(context, {});
const started = Date.now();
const taskId = await root.commit(
	(tx) =>
		tx.createTask(
			BackgroundTask,
			{ commission_id: "c_bg", steps: 3, delay_ms: 40 },
			{ ownership: { kind: "conversation" }, background: true },
		),
	context,
);

// A background task must not hold the conversation's idle wait.
await runtime.harness.waitForIdle(context);
check("空闲等待不等后台任务", true);

const settled = await runtime.harness.waitForTask(taskId, context);
const outcome = settled.state.outcome as { status: string; result?: { done?: number } };
check("后台任务跑完", outcome.status === "completed", JSON.stringify(outcome).slice(0, 160));
check("三步都落成进度", outcome.result?.done === 3, JSON.stringify(outcome.result));
const doc = await runtime.harness.snapshot(RunEvents, root.id, context);
const progress = (doc?.events ?? []).filter((event) => event.type === "progress");
check("进度不重复", progress.length === 3, JSON.stringify(progress));
check("步骤之间有真实等待", Date.now() - started >= 80, `${Date.now() - started}ms`);

await runtime.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
