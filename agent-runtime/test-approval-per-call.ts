// Approval is bound to one (tool, arguments) call and consumed once.
//
//   node --experimental-strip-types test-approval-per-call.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
	approvalHash, approvalHook, clearApprovalTools, grantApproval, pendingApprovals,
} from "./src/approval.ts";

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

process.env.XIAOXI_APPROVAL_TOOLS = "write_file";
delete process.env.XIAOXI_APPROVED_TOOLS;
clearApprovalTools();

const api = {
	conversationId: "conv-1",
	snapshot: async () => ({ runs: { "c1@0": { conversationId: "conv-1" } } }),
};
const beforeTool = (approvalHook as unknown as {
	handlers: { beforeTool: (call: unknown, api: unknown, context: unknown) => Promise<unknown> };
}).handlers.beforeTool;
const callA = { name: "write_file", arguments: { path: "a.md", content: "A" } };
const callB = { name: "write_file", arguments: { path: "b.md", content: "B" } };

try {
	console.log("[approval: per call]");
	const blockedA = await beforeTool(callA, api, BACKGROUND_CONTEXT);
	check("第一次调用被拦", blockedA !== undefined, JSON.stringify(blockedA));
	const pending = pendingApprovals("c1");
	check("待批准记录带 action_hash", pending.length === 1 && pending[0].tool === "write_file" &&
		pending[0].action_hash === approvalHash(callA), JSON.stringify(pending));
	grantApproval("c1", "write_file", pending[0].action_hash);
	const allowed = await beforeTool(callA, api, BACKGROUND_CONTEXT);
	check("同一次调用被放行", allowed === undefined, JSON.stringify(allowed));
	const replay = await beforeTool(callA, api, BACKGROUND_CONTEXT);
	check("授权一次后被消费", replay !== undefined, JSON.stringify(replay));
	const blockedB = await beforeTool(callB, api, BACKGROUND_CONTEXT);
	check("同工具不同参数仍需批准", blockedB !== undefined, JSON.stringify(blockedB));
} finally {
	delete process.env.XIAOXI_APPROVAL_TOOLS;
	clearApprovalTools();
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
