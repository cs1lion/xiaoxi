// Minimal MCP stdio server used by test-mcp-live.ts. No dependencies.
import { createInterface } from "node:readline";

const tools = [
	{
		name: "echo",
		description: "Echo a string",
		inputSchema: {
			type: "object",
			properties: { text: { type: "string" } },
			required: ["text"],
		},
		annotations: { readOnlyHint: true },
	},
	{
		name: "mutate",
		description: "A mutating tool",
		inputSchema: { type: "object", properties: {} },
		annotations: { readOnlyHint: false },
	},
];

function reply(message) {
	process.stdout.write(`${JSON.stringify(message)}\n`);
}

const lines = createInterface({ input: process.stdin });
lines.on("line", (line) => {
	let message;
	try {
		message = JSON.parse(line);
	} catch {
		return;
	}
	if (message.id === undefined) return; // notification
	if (message.method === "initialize") {
		reply({
			jsonrpc: "2.0", id: message.id,
			result: {
				protocolVersion: message.params?.protocolVersion ?? "2025-11-25",
				capabilities: { tools: {} },
				serverInfo: { name: "fixture", version: "1.0.0" },
			},
		});
		return;
	}
	if (message.method === "ping") {
		reply({ jsonrpc: "2.0", id: message.id, result: {} });
		return;
	}
	if (message.method === "tools/list") {
		reply({ jsonrpc: "2.0", id: message.id, result: { tools } });
		return;
	}
	if (message.method === "tools/call") {
		const params = message.params ?? {};
		const name = params.name;
		const args = params.arguments ?? {};
		if (name === "echo") {
			reply({ jsonrpc: "2.0", id: message.id,
				result: { content: [{ type: "text", text: `echo:${String(args.text ?? "")}` }] } });
			return;
		}
		if (name === "mutate") {
			reply({ jsonrpc: "2.0", id: message.id,
				result: { content: [{ type: "text", text: "mutated" }], isError: false } });
			return;
		}
		reply({ jsonrpc: "2.0", id: message.id,
			error: { code: -32602, message: `unknown tool ${name}` } });
		return;
	}
	reply({ jsonrpc: "2.0", id: message.id,
		error: { code: -32601, message: `method not found: ${message.method}` } });
});
