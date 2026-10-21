// The durable worker's only way to produce something L1 can check is a real file,
// so write_file has to both work and stay inside its sandbox. A tool that writes
// outside the commission's directory is a security bug, not a convenience.
//
//   node --experimental-strip-types test-write-file.ts
//
// Two halves: the escape rules as a pure function (the security boundary), and
// one model-driven run through a fake endpoint that proves the real path writes a
// file the evaluator can then find.

import { createServer } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const events = (payloads: unknown[]): string =>
	payloads.map((payload) => `data: ${JSON.stringify(payload)}\n\n`).join("") + "data: [DONE]\n\n";

const sandbox = mkdtempSync(join(tmpdir(), "xiaoxi-write-sandbox-"));
const outside = mkdtempSync(join(tmpdir(), "xiaoxi-write-outside-"));
const escapeTarget = join(outside, "escaped.txt");
const sibling = `${sandbox}-evil`;
mkdirSync(sibling, { recursive: true });

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

const { BACKGROUND_CONTEXT } = await import("@earendil-works/chord/context");
const { openAgentRuntime } = await import("./src/runtime.ts");
const { RunMeta } = await import("./src/docs.ts");
const { insideSandbox } = await import("./src/write_file.ts");

console.log("[write_file: escape rules]");
const ok = await insideSandbox(sandbox, "out/titles.md");
check("目录内的相对路径放行", ok === join(sandbox, "out", "titles.md"), String(ok));
check("../ 逃逸被拒", (await insideSandbox(sandbox, "../escaped.txt")) === undefined);
check("工作目录外的绝对路径被拒", (await insideSandbox(sandbox, escapeTarget)) === undefined);
check("同名前缀的兄弟目录被拒", (await insideSandbox(sandbox, join(sibling, "x.txt"))) === undefined);
check("深层 ../ 也被拒", (await insideSandbox(sandbox, "a/b/../../../escaped.txt")) === undefined);
check("工作目录本身放行（写它自己不是逃逸）", (await insideSandbox(sandbox, ".")) === sandbox);

// ---------------------------------------------------------------- model-driven
// A fake endpoint whose model writes the deliverable and then hands it in, the
// way the production path is expected to run.
const server = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}") as {
		messages?: { role?: string }[];
	};
	// One branch per tool result already in the transcript: write, then hand in,
	// then a closing line. Keying on the role alone cannot tell the two apart.
	const toolResults = (body.messages ?? []).filter((message) => message.role === "tool").length;
	res.writeHead(200, { "content-type": "text/event-stream" });
	if (toolResults >= 2) {
		res.end(
			events([
				{ choices: [{ index: 0, delta: { role: "assistant", content: "都办好了。" }, finish_reason: null }] },
				{ choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 40, completion_tokens: 5, total_tokens: 45 } },
			]),
		);
		return;
	}
	if (toolResults === 1) {
		res.end(
			events([
				{ choices: [{ index: 0, delta: { role: "assistant", content: "交差。" }, finish_reason: null }] },
				{
					choices: [
						{
							index: 0,
							delta: {
								tool_calls: [
									{ index: 0, id: "c2", type: "function", function: { name: "submit_artifact", arguments: "" } },
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
							delta: {
								tool_calls: [
									{
										index: 0,
										function: {
											arguments: JSON.stringify({
												summary: "写了一版标题",
												intercepted: "没有",
												artifacts: [{ kind: "file", ref: join(sandbox, "out", "titles.md") }],
												wired: true,
											}),
										},
									},
								],
							},
							finish_reason: null,
						},
					],
				},
				{ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 30, completion_tokens: 14, total_tokens: 44 } },
			]),
		);
		return;
	}
	res.end(
		events([
			{ choices: [{ index: 0, delta: { role: "assistant", content: "我先写文件。" }, finish_reason: null }] },
			{
				choices: [
					{
						index: 0,
						delta: {
							tool_calls: [
								{ index: 0, id: "c1", type: "function", function: { name: "write_file", arguments: "" } },
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
						delta: {
							tool_calls: [
								{
									index: 0,
									function: {
										arguments: JSON.stringify({
											path: "out/titles.md",
											content: "一、深夜的代码只属于我一个人\n",
										}),
									},
								},
							],
						},
						finish_reason: null,
					},
				],
			},
			{ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 22, completion_tokens: 11, total_tokens: 33 } },
		]),
	);
});

await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (address === null || typeof address !== "object") throw new Error("no port");

process.env.LLM_BASE_URL = `http://127.0.0.1:${address.port}`;
process.env.LLM_MODEL = "fake-1";
process.env.LLM_API_KEY = "test-key";
process.env.XIAOXI_WORKER_MODE = "model";

console.log("[write_file: model-driven run]");
const directory = mkdtempSync(join(tmpdir(), "xiaoxi-write-"));
const runtime = await openAgentRuntime(directory);
const ref = await runtime.createRun({
	commission_id: "c_write",
	agent_id: "w1",
	packet: { objective: "写一版标题", deliverable: "out/titles.md" },
	workspace: sandbox,
});
const settled = await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
const view = await runtime.readRun(ref.taskId);

const outcome = settled.state.outcome as { status: string; result?: { artifact?: { artifacts?: { kind?: string; ref?: string }[] } } };
check("任务完成", outcome.status === "completed", JSON.stringify(outcome).slice(0, 200));
const refs = outcome.result?.artifact?.artifacts ?? [];
check("交差引用的是真实文件", refs[0]?.kind === "file", JSON.stringify(refs));
const written = join(sandbox, "out", "titles.md");
check("文件真的写在沙箱里", existsSync(written), written);
check("内容就是模型写的", existsSync(written) && readFileSync(written, "utf-8").includes("深夜的代码"));
check("没有东西写进沙箱外", !existsSync(escapeTarget) && !existsSync(join(sibling, "x.txt")));

const task = await runtime.harness.getTask(ref.taskId, BACKGROUND_CONTEXT);
const meta = await runtime.harness.snapshot(RunMeta, task?.conversationId as never, BACKGROUND_CONTEXT);
check("工作目录记在 RunMeta 里", meta?.workspace === sandbox, String(meta?.workspace));

await runtime.close();
server.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
