// The approval chain used to stop at the block: the hook refused the call and told
// the model to ask, but nothing carried the boss's answer back, so a blocked tool
// stayed blocked for the life of the sidecar. Three runs against a real Harness
// cover the loop — block, no leak across commissions, and the call running once
// granted.
//
//   node --experimental-strip-types test-approval-chain.ts

import { createServer } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const events = (payloads: unknown[]): string =>
	payloads.map((payload) => `data: ${JSON.stringify(payload)}\n\n`).join("") + "data: [DONE]\n\n";

const sandbox = mkdtempSync(join(tmpdir(), "xiaoxi-approval-sandbox-"));
const target = join(sandbox, "out", "titles.md");

const server = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = JSON.parse(Buffer.concat(parts).toString("utf8") || "{}") as {
		messages?: { role?: string; content?: unknown }[];
	};
	const messages = body.messages ?? [];
	const toolResults = messages.filter((message) => message.role === "tool").length;
	const blocked = JSON.stringify(messages).includes("需要老板确认");
	res.writeHead(200, { "content-type": "text/event-stream" });
	if (toolResults >= 2 || blocked) {
		res.end(
			events([
				{ choices: [{ index: 0, delta: { role: "assistant", content: "那我先交差。" }, finish_reason: null }] },
				{
					choices: [
						{
							index: 0,
							delta: {
								tool_calls: [
									{ index: 0, id: "cs", type: "function", function: { name: "submit_artifact", arguments: "" } },
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
												summary: "被拦下了，交个文字成果",
												intercepted: "没有",
												artifacts: [{ kind: "note", ref: "note:n_1" }],
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
				{ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 30, completion_tokens: 12, total_tokens: 42 } },
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
									{ index: 0, id: "cs2", type: "function", function: { name: "submit_artifact", arguments: "" } },
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
												summary: "写了 titles.md",
												intercepted: "没有",
												artifacts: [{ kind: "file", ref: target }],
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
				{ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 34, completion_tokens: 15, total_tokens: 49 } },
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
								{ index: 0, id: "cw", type: "function", function: { name: "write_file", arguments: "" } },
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
										arguments: JSON.stringify({ path: "out/titles.md", content: "一、深夜的代码\n" }),
									},
								},
							],
						},
						finish_reason: null,
					},
				],
			},
			{ choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage: { prompt_tokens: 24, completion_tokens: 10, total_tokens: 34 } },
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
// write_file is the gated tool for this test.
process.env.XIAOXI_APPROVAL_TOOLS = "write_file";
delete process.env.XIAOXI_APPROVED_TOOLS;

const { BACKGROUND_CONTEXT } = await import("@earendil-works/chord/context");
const { openAgentRuntime } = await import("./src/runtime.ts");
const { grantApproval, pendingApprovals } = await import("./src/approval.ts");
const { SIDECAR_ROUTES } = await import("./src/routes.ts");

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

console.log("[approval chain]");
check("路由表里有审批入口", (SIDECAR_ROUTES as readonly string[]).includes("/v1/approvals"),
	JSON.stringify(SIDECAR_ROUTES));

const directory = mkdtempSync(join(tmpdir(), "xiaoxi-approval-"));
const runtime = await openAgentRuntime(directory);
const drive = async (commissionId: string, attempt = 0) => {
	const ref = await runtime.createRun({
		commission_id: commissionId,
		agent_id: "w1",
		packet: { objective: "写一版标题" },
		workspace: sandbox,
		attempt,
	});
	await runtime.harness.waitForTask(ref.taskId, BACKGROUND_CONTEXT);
};

const hasPending = (commissionId: string, tool: string): boolean =>
	pendingApprovals(commissionId).some((item) => item.tool === tool);
const grantPending = (commissionId: string, tool: string): void => {
	const item = pendingApprovals(commissionId).find((entry) => entry.tool === tool);
	grantApproval(commissionId, tool, item?.action_hash);
};

await drive("c_blocked");
check("未批准的工具被拦下", !existsSync(target));
check("拦下的调用进了待批准清单", hasPending("c_blocked", "write_file"),
	JSON.stringify(pendingApprovals("c_blocked")));

// The boss answered for this commission only, for this exact call.
grantPending("c_blocked", "write_file");
check("放行后待批准清单清空", pendingApprovals("c_blocked").length === 0);
await drive("c_blocked", 1);
check("批准之后同一调用真的跑起来", existsSync(target), target);
rmSync(target, { force: true });

// Another commission must still be blocked: a grant is per commission, not global.
await drive("c_other");
check("批准不跨单泄漏", hasPending("c_other", "write_file"),
	JSON.stringify(pendingApprovals("c_other")));
check("没被批准的那单仍然写不进去", !existsSync(target));

grantPending("c_other", "write_file");
check("另一单也放行之后才写得进去", !existsSync(target), "放行前不该有文件");
await drive("c_other", 1);
check("另一单批准后工具真的跑起来", existsSync(target), target);

await runtime.close();
server.close();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
