// MCP bridge: sanitize names, keep read-only tools replay-safe, and put
// mutating tools behind the approval hook.
//
//   node --experimental-strip-types test-mcp.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { clearApprovalTools, isApprovalRequired } from "./src/approval.ts";
import {
	expandEnv,
	parseMcpConfig,
	toolsForServerAsync,
} from "./src/mcp.ts";

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

const fake = {
	async listTools() {
		return [
			{
				name: "read_doc",
				description: "读取文档",
				inputSchema: { type: "object", properties: { id: { type: "string" } } },
				annotations: { readOnlyHint: true },
			},
			{
				name: "write doc",
				description: "写文档",
				inputSchema: { type: "object", properties: {} },
				annotations: { readOnlyHint: false },
			},
			{
				name: "big_doc",
				description: "返回超长文档",
				inputSchema: { type: "object", properties: {} },
				annotations: { readOnlyHint: true },
			},
		];
	},
	async callTool(name: string, args: Record<string, unknown> = {}) {
		return {
			content: [{
				type: "text" as const,
				text: name === "big_doc" ? "长".repeat(30_000) : `${name}:${JSON.stringify(args)}`,
			}],
			isError: name.includes("write"),
		};
	},
};

console.log("[durable: mcp]");
clearApprovalTools();
const tools = await toolsForServerAsync("docs", fake, { approval: "mutating" });

check("工具名带 server 前缀并净化", tools.map((tool) => tool.name).join(",") ===
	"mcp__docs__read_doc,mcp__docs__write_doc,mcp__docs__big_doc",
	JSON.stringify(tools.map((tool) => tool.name)));
check("只读工具可安全重放", tools[0]?.replay === "safe", String(tools[0]?.replay));
check("写工具不可重放", tools[1]?.replay === "unsafe", String(tools[1]?.replay));
check("写工具进入审批", isApprovalRequired("mcp__docs__write_doc"));
check("只读工具不进入审批", !isApprovalRequired("mcp__docs__read_doc"));

const readResult = await tools[0].execute({ id: "n1" }, {} as never, BACKGROUND_CONTEXT);
const readText = (readResult.output ?? []).map((item) => ("text" in item ? item.text : "")).join("\n");
check("调用结果转成文本", readText.includes("read_doc") && readText.includes("n1"), readText);
check("成功结果不是错误", readResult.isError !== true, JSON.stringify(readResult));

const writeResult = await tools[1].execute({}, {} as never, BACKGROUND_CONTEXT);
check("MCP isError 透传", writeResult.isError === true, JSON.stringify(writeResult));

const bigResult = await tools[2].execute({}, {} as never, BACKGROUND_CONTEXT);
const bigText = (bigResult.output ?? []).map((item) => ("text" in item ? item.text : "")).join("\n");
check("MCP 超长结果被截断",
	bigText.includes("已截断") && bigText.length < 12_500, String(bigText.length));

const config = parseMcpConfig(JSON.stringify({
	mcpServers: {
		docs: {
			command: "npx",
			args: ["-y", "fake-mcp"],
			env: { DOCS_TOKEN: "${DOCS_TOKEN}" },
		},
	},
}));
check("解析 mcpServers", config.mcpServers?.docs?.command === "npx", JSON.stringify(config));
const saved = process.env.DOCS_TOKEN;
process.env.DOCS_TOKEN = "test-token";
check("展开环境变量", expandEnv("${DOCS_TOKEN}") === "test-token", expandEnv("${DOCS_TOKEN}"));
if (saved === undefined) delete process.env.DOCS_TOKEN;
else process.env.DOCS_TOKEN = saved;

clearApprovalTools();
console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
