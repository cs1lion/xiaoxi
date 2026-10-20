// The takeover bridge: the durable engine spawns a local agent and adopts its
// events. This is what makes "durable 接管本地 agent" real rather than aspirational
// — without it the sidecar has no way to run anything but its own model.
//
//   node --experimental-strip-types test-takeover.ts
//
// The "local agent" is a real Node script that speaks PROTOCOL §22's five-event
// line protocol, exactly like scripts/worker_shims/*.py does. No model and no
// network: the point is the bridge, not the agent.

import { mkdirSync, mkdtempSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { BACKGROUND_CONTEXT } = await import("@earendil-works/chord/context");
const { openAgentRuntime } = await import("./src/runtime.ts");
const { shimEnv } = await import("./src/takeover.ts");

// A stand-in for a real worker shim: writes a deliverable into AQI_OUTDIR, then
// emits progress, a token and the delivery card on stdout. Built as an array of
// lines so no escape sequence is nested twice over.
const fakeShim = [
	`import { mkdirSync, writeFileSync } from "node:fs";`,
	`import { join } from "node:path";`,
	`const out = process.env.AQI_OUTDIR;`,
	`mkdirSync(out, { recursive: true });`,
	`writeFileSync(join(out, "note.txt"), "OK" + String.fromCharCode(10), "utf-8");`,
	`const emit = (event) => process.stdout.write(JSON.stringify(event) + String.fromCharCode(10));`,
	`emit({ type: "progress", step_idx: 1, text: "fake worker started" });`,
	`emit({ type: "token", model: "step-3.7-flash", prompt_tokens: 11,`,
	`       completion_tokens: 5, ttft_ms: 20, duration_ms: 60, status_code: 200 });`,
	`emit({ type: "progress", step_idx: 2, text: "fake worker wrote note.txt" });`,
	`const card = { summary: "假执行体写了 note.txt", intercepted: "没有需要老板拍板的事",`,
	`  artifacts: [{ kind: "file", ref: join(out, "note.txt") }], wired: true };`,
	`emit({ type: "artifact", artifact: card });`,
].join("\n");

const sandbox = mkdtempSync(join(tmpdir(), "xiaoxi-takeover-"));
const shimPath = join(sandbox, "fake_shim.mjs");
writeFileSync(shimPath, fakeShim, "utf-8");

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

console.log("[takeover: durable drives a local agent]");
const directory = mkdtempSync(join(tmpdir(), "xiaoxi-takeover-runtime-"));
const runtime = await openAgentRuntime(directory);

// The shim's environment is what makes it look like a real commission: it reads
// the packet and the workspace from there, never from the command line.
const workspace = join(sandbox, "work");
mkdirSync(workspace, { recursive: true });
const env = shimEnv({
	workspace,
	commission_id: "c_takeover",
	packet: { objective: "写一个 note.txt", deliverable: "out/note.txt" },
});
check("密件走环境变量，不进命令行",
	typeof env.AQI_BRIEFING_PACKET === "string" && env.AQI_BRIEFING_PACKET.includes("写一个 note.txt"),
	JSON.stringify(env).slice(0, 160));
check("工作目录与输出目录都给了", env.AQI_WORKDIR === workspace && env.AQI_OUTDIR === `${workspace}/out`,
	JSON.stringify(env));

const ref = await runtime.createRun({
	commission_id: "c_takeover",
	agent_id: "dsh",
	packet: { objective: "写一个 note.txt", deliverable: "out/note.txt" },
	workspace,
	command: [process.execPath, shimPath],
});
const settled = await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
const view = await runtime.readRun(ref.taskId);

const outcome = settled.state.outcome as { status: string; result?: { artifact?: Record<string, unknown> } };
check("接管跑完并完成", outcome.status === "completed", JSON.stringify(outcome).slice(0, 200));
const kinds = (view?.events ?? []).map((event) => event.type);
check("进度事件收了回来", kinds.filter((kind) => kind === "progress").length === 2, JSON.stringify(kinds));
check("token 事件收了回来", kinds.includes("token"), JSON.stringify(kinds));
check("本地执行体交的差被采纳", outcome.result?.artifact?.summary === "假执行体写了 note.txt",
	JSON.stringify(outcome.result));
check("它写的文件真的在沙箱里", existsSync(join(workspace, "out", "note.txt")));
check("文件内容是它自己写的", readFileSync(join(workspace, "out", "note.txt"), "utf-8") === "OK\n");
check("没有留下失败痕迹", !kinds.includes("error"), JSON.stringify(kinds));

// A shim that never hands in must fail the run, not silently pass. It exits 0,
// so the failure text is produced by the "ran but handed nothing in" branch —
// that branch must still carry stderr, because a shim that quietly dies of a
// missing env var looks exactly like a shim that chose to say nothing.
const silentShim = join(sandbox, "silent_shim.mjs");
writeFileSync(silentShim, [
	`process.stderr.write("config missing: AQI_WORKSPACE_ROOT" + String.fromCharCode(10));`,
	`const emit = (event) => process.stdout.write(JSON.stringify(event) + String.fromCharCode(10));`,
	`emit({ type: "progress", step_idx: 1, text: "干活中" });`,
].join("\n"), "utf-8");
const ref2 = await runtime.createRun({
	commission_id: "c_silent",
	agent_id: "dsh",
	packet: { objective: "什么都不交" },
	workspace,
	command: [process.execPath, silentShim],
});
const settled2 = await runtime.harness.waitForTask(ref2.taskId, BACKGROUND_CONTEXT);
const outcome2 = settled2.state.outcome as { status: string; error?: { message?: string } };
check("不交差的本地执行体让这一单失败", outcome2.status === "failed", JSON.stringify(outcome2));
check("退出码 0 但没交差时也带回 stderr",
	String(outcome2.error?.message ?? "").includes("AQI_WORKSPACE_ROOT"),
	String(outcome2.error?.message ?? "").slice(0, 240));

// A command that does not exist must surface as a failure with a reason, not a hang.
const ref3 = await runtime.createRun({
	commission_id: "c_missing",
	agent_id: "dsh",
	packet: { objective: "x" },
	workspace,
	command: ["definitely-not-a-real-executable-xyz"],
});
const settled3 = await runtime.harness.waitForTask(ref3.taskId, BACKGROUND_CONTEXT);
const outcome3 = settled3.state.outcome as { status: string; error?: { message?: string } };
check("执行体不存在时明确失败", outcome3.status === "failed", JSON.stringify(outcome3));
check("失败带了原因", String(outcome3.error?.message ?? "").length > 0, JSON.stringify(outcome3.error));

// A shim that dies before emitting anything (a relative path that does not
// resolve is the real-world case) exits without a delivery card. The failure
// must carry what the child said on stderr, otherwise the operator sees
// "handed nothing in" and cannot tell a broken command from a silent agent.
const dyingShim = join(sandbox, "dying_shim.mjs");
writeFileSync(dyingShim, [
	`process.stderr.write("Cannot find module: scripts/worker_shims/x.py" + String.fromCharCode(10));`,
	`process.exit(1);`,
].join("\n"), "utf-8");
const ref4 = await runtime.createRun({
	commission_id: "c_dying",
	agent_id: "dsh",
	packet: { objective: "x" },
	workspace,
	command: [process.execPath, dyingShim],
});
const settled4 = await runtime.harness.waitForTask(ref4.taskId, BACKGROUND_CONTEXT);
const outcome4 = settled4.state.outcome as { status: string; error?: { message?: string } };
const reason4 = String(outcome4.error?.message ?? "");
check("执行体静默死掉时失败", outcome4.status === "failed", JSON.stringify(outcome4));
check("失败里带回了 stderr 的原因", reason4.includes("Cannot find module"), reason4.slice(0, 240));

// Rejection → retry must start a *fresh* task. Keying idempotency on the
// commission alone returned the previous terminal task, so the agent was never
// spawned again and the rejected delivery was handed in a second time.
const attempts: number[] = [];
const countingShim = join(sandbox, "attempt_shim.mjs");
writeFileSync(countingShim, [
	`import { appendFileSync } from "node:fs";`,
	`appendFileSync(${JSON.stringify(join(sandbox, "attempts.log"))}, "run" + String.fromCharCode(10));`,
	`const card = { summary: "attempt " + (process.env.N ?? "?"), intercepted: "没有",`,
	`  artifacts: [{ kind: "note", ref: "note:x" }], wired: true };`,
	`process.stdout.write(JSON.stringify({ type: "artifact", artifact: card }) + String.fromCharCode(10));`,
].join("\n"), "utf-8");
const runOnce = async (attempt: number) => {
	const ref = await runtime.createRun({
		commission_id: "c_retry", agent_id: "dsh", packet: { objective: "x" },
		workspace, command: [process.execPath, countingShim], attempt,
	});
	await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
	return ref.taskId;
};
const first = await runOnce(0);
const retry = await runOnce(1);
const repeat = await runOnce(1);
const lines = readFileSync(join(sandbox, "attempts.log"), "utf-8").trim().split("\n").length;
check("重做拿到新任务", retry !== first, `${first} vs ${retry}`);
check("重投同一次仍幂等", repeat === retry, `${retry} vs ${repeat}`);
check("执行体在重做时真的又跑了一次", lines === 2, `${lines} 次`);

await runtime.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
