// Real MCP stdio round trip: spawn the fixture server, initialize, list tools,
// call a tool, and verify approval/replay policy.
//
//   node --experimental-strip-types test-mcp-live.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { clearApprovalTools, isApprovalRequired } from "./src/approval.ts";
import { connectMcp } from "./src/mcp.ts";

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

const directory = mkdtempSync(join(tmpdir(), "xiaoxi-mcp-live-"));
const configPath = join(directory, "mcp.json");
writeFileSync(configPath, JSON.stringify({
	mcpServers: {
		echo: {
			command: process.execPath,
			args: [resolve("fixtures/mcp-echo-server.mjs")],
		},
	},
}), "utf-8");

clearApprovalTools();
try {
	console.log("[durable: live MCP stdio]");
	const bundle = await connectMcp(configPath);
	const names = bundle.tools.map((tool) => tool.name).sort();
	check("真实 stdio server 连上", bundle.names.includes("echo"), JSON.stringify(bundle.names));
	check("tools/list 转成 durable 工具",
		names.join(",") === "mcp__echo__echo,mcp__echo__mutate", names.join(","));
	const echo = bundle.tools.find((tool) => tool.name === "mcp__echo__echo");
	const mutate = bundle.tools.find((tool) => tool.name === "mcp__echo__mutate");
	check("只读工具 replay safe", echo?.replay === "safe", String(echo?.replay));
	check("写工具 replay unsafe", mutate?.replay === "unsafe", String(mutate?.replay));
	check("写工具自动进审批", isApprovalRequired("mcp__echo__mutate"));
	check("只读工具不进审批", !isApprovalRequired("mcp__echo__echo"));
	if (echo === undefined) throw new Error("missing echo tool");
	const result = await echo.execute({ text: "hello" }, {} as never, BACKGROUND_CONTEXT);
	const text = (result.output ?? []).map((item) => ("text" in item ? item.text : "")).join("\n");
	check("tools/call 往返内容正确", text.includes("echo:hello"), text);
	check("调用不是错误", result.isError !== true, JSON.stringify(result));
	await bundle.close();
	check("关闭后工具仍可枚举（快照）", bundle.tools.length === 2);
} finally {
	clearApprovalTools();
	rmSync(directory, { recursive: true, force: true });
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
