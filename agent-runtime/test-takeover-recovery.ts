// Recovery under a hard process death *during a takeover*. This is the property
// the whole migration is for: the sidecar can die with a real local agent's work
// half-recorded, and reopening must finish from the event log without running
// that agent a second time. Re-running would burn tokens and could overwrite work
// the CLI already delivered.
//
//   node --experimental-strip-types test-takeover-recovery.ts
//
// The parent runs two children so the crash is a real process exit.

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { RunIndex } from "./src/docs.ts";
import { openAgentRuntime } from "./src/runtime.ts";

const self = fileURLToPath(import.meta.url);
const [mode, directory] = process.argv.slice(2);

function child(args: string[], env: Record<string, string> = {}): { code: number | null; out: string } {
	const result = spawnSync(process.execPath, ["--experimental-strip-types", self, ...args], {
		encoding: "utf8",
		env: { ...process.env, ...env },
	});
	return { code: result.status, out: `${result.stdout ?? ""}${result.stderr ?? ""}` };
}

// A shim that counts how many times it has been started, so a re-run is visible
// rather than merely suspected.
const countingShim = [
	`import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";`,
	`import { join } from "node:path";`,
	`const out = process.env.AQI_OUTDIR;`,
	`mkdirSync(out, { recursive: true });`,
	`appendFileSync(join(out, "runs.log"), "run" + String.fromCharCode(10), "utf-8");`,
	`writeFileSync(join(out, "note.txt"), "OK" + String.fromCharCode(10), "utf-8");`,
	`const emit = (event) => process.stdout.write(JSON.stringify(event) + String.fromCharCode(10));`,
	`emit({ type: "progress", step_idx: 1, text: "counting worker started" });`,
	`const card = { summary: "计数执行体写了 note.txt", intercepted: "没有",`,
	`  artifacts: [{ kind: "file", ref: join(out, "note.txt") }], wired: true };`,
	`emit({ type: "artifact", artifact: card });`,
].join("\n");

if (mode === "crash" || mode === "resume") {
	const runtime = await openAgentRuntime(directory);
	if (mode === "crash") {
		const shimPath = join(directory, "counting_shim.mjs");
		writeFileSync(shimPath, countingShim, "utf-8");
		const workspace = join(directory, "work");
		mkdirSync(workspace, { recursive: true });
		const ref = await runtime.createRun({
			commission_id: "c_recover",
			agent_id: "dsh",
			packet: { objective: "写一个 note.txt" },
			workspace,
			command: [process.execPath, shimPath],
		});
		console.log(`TASK ${ref.taskId}`);
		// The task calls process.exit(9) itself once the shim's delivery card is
		// durable but before the terminal outcome is committed.
		await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
		console.log("NO_CRASH");
	} else {
		const index = await runtime.harness.snapshot(RunIndex, BACKGROUND_CONTEXT);
		const ref = index?.runs["c_recover@0"];
		if (ref === undefined) throw new Error("run index lost the commission");
		const settled = await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
		const view = await runtime.readRun(ref.taskId);
		console.log(`OUTCOME ${JSON.stringify(settled.state.outcome)}`);
		console.log(`EVENTS ${JSON.stringify(view?.events ?? [])}`);
		await runtime.close();
	}
} else {
	const directoryPath = mkdtempSync(join(tmpdir(), "xiaoxi-takeover-recover-"));
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

	console.log("[takeover recovery: hard death mid-run]");
	const crashed = child(["crash", directoryPath], { XIAOXI_TAKEOVER_CRASH_AFTER_ARTIFACT: "1" });
	check("第一次运行真的死在交差之后、收尾之前", crashed.code === 9, `exit ${crashed.code}`);
	check("死前已经落了任务号", /TASK \d+/.test(crashed.out), crashed.out.slice(0, 200));
	const runsLog = join(directoryPath, "work", "out", "runs.log");
	check("本地执行体在崩溃前确实跑过一次", existsSync(runsLog), runsLog);
	const before = existsSync(runsLog) ? readFileSync(runsLog, "utf-8").trim().split("\n").length : 0;
	check("只跑了一次", before === 1, `${before} 次`);

	const resumed = child(["resume", directoryPath]);
	const outcomeLine = resumed.out.split("\n").find((line) => line.startsWith("OUTCOME ")) ?? "";
	const outcome = JSON.parse(outcomeLine.slice("OUTCOME ".length) || "{}") as {
		status?: string;
		result?: { artifact?: { summary?: string } };
	};
	check("重开后这一单完成", outcome.status === "completed", JSON.stringify(outcome).slice(0, 240));
	check("完成时用的是本地执行体交的差",
		outcome.result?.artifact?.summary === "计数执行体写了 note.txt", JSON.stringify(outcome.result));

	const eventsLine = resumed.out.split("\n").find((line) => line.startsWith("EVENTS ")) ?? "EVENTS []";
	const events = JSON.parse(eventsLine.slice("EVENTS ".length)) as { type: string }[];
	const artifacts = events.filter((event) => event.type === "artifact");
	check("交差只有一条，没有被重跑覆盖", artifacts.length === 1, JSON.stringify(artifacts));
	check("没有留下失败痕迹", !events.some((event) => event.type === "error"), JSON.stringify(events));

	const after = existsSync(runsLog) ? readFileSync(runsLog, "utf-8").trim().split("\n").length : 0;
	check("重开没有把本地执行体再拉起来", after === before, `崩溃前 ${before} 次，重开后 ${after} 次`);

	console.log();
	if (failed.length > 0) {
		console.log(`=== ${passed} passed, ${failed.length} failed ===`);
		for (const item of failed) console.log(`  - ${item}`);
		process.exit(1);
	}
	console.log(`=== ${passed} passed, 0 failed ===`);
}
