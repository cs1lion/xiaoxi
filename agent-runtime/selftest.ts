// Phase 0 gate: prove on this machine that a task survives a hard process death
// and that a repeated create is idempotent.
//
//   node --experimental-strip-types selftest.ts
//
// The parent runs three children so the crash is a real process exit, not an
// exception: crash -> resume -> idem.

import { spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { RunIndex } from "./src/docs.ts";
import { openAgentRuntime } from "./src/runtime.ts";

const context = BACKGROUND_CONTEXT;
const self = fileURLToPath(import.meta.url);
const [mode, directory] = process.argv.slice(2);

function child(args: string[], env: Record<string, string> = {}): { code: number | null; out: string } {
	const result = spawnSync(process.execPath, ["--experimental-strip-types", self, ...args], {
		encoding: "utf8",
		env: { ...process.env, ...env },
	});
	return { code: result.status, out: `${result.stdout ?? ""}${result.stderr ?? ""}` };
}

if (mode === "crash") {
	const runtime = await openAgentRuntime(directory);
	const ref = await runtime.createRun({ commission_id: "c1", agent_id: "w1", packet: { objective: "自检" } });
	console.log(`TASK ${ref.taskId}`);
	// The task calls process.exit(9) itself after its second durable progress commit.
	await runtime.harness.waitForTask(ref.taskId, context);
	console.log("NO_CRASH");
} else if (mode === "resume") {
	const runtime = await openAgentRuntime(directory);
	const index = await runtime.harness.snapshot(RunIndex, context);
	const ref = index?.runs["c1@0"];
	if (ref === undefined) throw new Error("run index lost the commission");
	// The task is still pending after reopen; waiting for it enables scheduling.
	const settled = await runtime.harness.waitForTask(ref.taskId, context);
	const view = await runtime.readRun(ref.taskId);
	console.log(`OUTCOME ${JSON.stringify(settled.state.outcome)}`);
	console.log(`EVENTS ${JSON.stringify(view?.events ?? [])}`);
	await runtime.close();
} else if (mode === "idem") {
	const runtime = await openAgentRuntime(directory);
	const first = await runtime.createRun({ commission_id: "c1", agent_id: "w1", packet: {} });
	const second = await runtime.createRun({ commission_id: "c1", agent_id: "w1", packet: {} });
	console.log(`IDS ${first.taskId} ${second.taskId} ${first.conversationId === second.conversationId}`);
	await runtime.harness.waitForTask(first.taskId, context);
	await runtime.close();
} else {
	const directoryPath = mkdtempSync(join(tmpdir(), "xiaoxi-selftest-"));
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

	console.log("[phase 0: crash and resume]");
	const crashed = child(["crash", directoryPath], { XIAOXI_SPIKE_CRASH: "1" });
	check("第一次运行真的死在任务中途", crashed.code === 9, `exit ${crashed.code}`);
	check("死前已经落了任务号", /TASK \d+/.test(crashed.out), crashed.out.slice(0, 200));

	const resumed = child(["resume", directoryPath]);
	check("重开后续跑完成", resumed.out.includes('"status":"completed"'), resumed.out.slice(0, 300));
	const eventsLine = resumed.out.split("\n").find((line) => line.startsWith("EVENTS ")) ?? "EVENTS []";
	const events = JSON.parse(eventsLine.slice("EVENTS ".length)) as { type: string; step_idx?: number }[];
	const progress = events.filter((event) => event.type === "progress");
	check("三条进度一条不多一条不少", progress.length === 3, JSON.stringify(progress));
	check("没有重复的步骤", new Set(progress.map((event) => event.step_idx)).size === progress.length);
	check("产出恰好一条", events.filter((event) => event.type === "artifact").length === 1);

	console.log("[phase 0: repeated create]");
	const idem = child(["idem", directoryPath]);
	const ids = idem.out.split("\n").find((line) => line.startsWith("IDS ")) ?? "IDS   false";
	const parts = ids.slice("IDS ".length).split(" ");
	check("重复 POST 返回同一单", parts[0] === parts[1], ids);
	check("重复 POST 复用同一会话", parts[2] === "true", ids);

	console.log();
	if (failed.length > 0) {
		console.log(`=== ${passed} passed, ${failed.length} failed ===`);
		for (const item of failed) console.log(`  - ${item}`);
		process.exit(1);
	}
	console.log(`=== ${passed} passed, 0 failed ===`);
}
