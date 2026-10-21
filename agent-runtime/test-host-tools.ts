// C1 host tools: memory/notes/threads/workers go through the token-protected
// host API; a missing host or token must fail loudly, not fabricate.
//
//   node --experimental-strip-types test-host-tools.ts

import { createServer } from "node:http";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { hostTools } from "./src/host_tools.ts";

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

const seen: string[] = [];
const server = createServer(async (req, res) => {
	const auth = req.headers.authorization ?? "";
	seen.push(`${req.method} ${req.url} ${auth}`);
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const body = Buffer.concat(parts).toString("utf8");
	if (auth !== "Bearer test-token") {
		res.writeHead(401, { "content-type": "application/json" });
		res.end(JSON.stringify({ error: "unauthorized" }));
		return;
	}
	res.writeHead(200, { "content-type": "application/json" });
	if (req.url === "/internal/durable/memory/search") {
		res.end(JSON.stringify({
			status: "found", stated: [{ id: 1, value: "用户喜欢短标题" }],
			inferred: [{ id: 2, value: "可能喜欢短句", requires_confirmation: true }],
			uncertain: [], note: "",
		}));
		return;
	}
	if (req.url === "/internal/durable/notes/search") {
		res.end(JSON.stringify({ status: "available", items: [{ id: "n1" }] }));
		return;
	}
	if (req.url === "/internal/durable/notes/read") {
		res.end(JSON.stringify({ body: "便笺正文", ref: { type: "note", id: "n1", version: 1 } }));
		return;
	}
	if (req.url === "/internal/durable/threads/read") {
		res.end(JSON.stringify({ id: "t1", revision: 2 }));
		return;
	}
	if (req.url === "/internal/durable/workers" || req.url === "/internal/durable/workers/status") {
		res.end(JSON.stringify({ items: [
			{ id: "scribe", adapter: "durable", in_flight: 1, available: false },
			{ id: "builder", adapter: "cli_subproc", in_flight: 0, available: true },
		] }));
		return;
	}
	res.end(JSON.stringify({ ok: true, body }));
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (address === null || typeof address !== "object") throw new Error("no port");
process.env.XIAOXI_HOST_URL = `http://127.0.0.1:${address.port}`;
process.env.XIAOXI_HOST_TOKEN = "test-token";

const textOf = (result: { output?: { type: string; text?: string }[] }): string =>
	(result.output ?? []).map((item) => item.text ?? "").join("\n");
const tool = (name: string) => {
	const found = hostTools.find((item) => item.name === name);
	if (found === undefined) throw new Error(`missing tool ${name}`);
	return found;
};

try {
	console.log("[durable: host tools]");
	const memory = await tool("search_memory").execute({ query: "标题" }, {} as never, BACKGROUND_CONTEXT);
	check("search_memory 返回分档", textOf(memory).includes("用户喜欢短标题") &&
		textOf(memory).includes("可能喜欢短句"), textOf(memory));
	check("search_memory 无错误", memory.isError !== true, JSON.stringify(memory));

	const notes = await tool("search_notes").execute({ query: "标题" }, {} as never, BACKGROUND_CONTEXT);
	check("search_notes 返回条目", textOf(notes).includes("n1"), textOf(notes));
	const note = await tool("read_note").execute({ note_id: "n1", version: 1 }, {} as never, BACKGROUND_CONTEXT);
	check("read_note 返回正文", textOf(note).includes("便笺正文"), textOf(note));
	const thread = await tool("read_thread").execute({ thread_id: "t1" }, {} as never, BACKGROUND_CONTEXT);
	check("read_thread 返回事项", textOf(thread).includes("t1"), textOf(thread));

	const workers = await tool("list_workers").execute({}, {} as never, BACKGROUND_CONTEXT);
	check("list_workers 返回花名册", textOf(workers).includes("scribe") &&
		textOf(workers).includes("builder"), textOf(workers));
	const status = await tool("worker_status").execute({ agent_id: "scribe" }, {} as never, BACKGROUND_CONTEXT);
	check("worker_status 过滤并保留容量", textOf(status).includes("scribe") &&
		textOf(status).includes("in_flight") && !textOf(status).includes("builder"), textOf(status));

	check("所有请求都带 token", seen.every((line) => line.endsWith("Bearer test-token")),
		seen.join("\n"));

	process.env.XIAOXI_HOST_TOKEN = "wrong";
	const denied = await tool("search_memory").execute({ query: "标题" }, {} as never, BACKGROUND_CONTEXT);
	check("错误 token 失败", denied.isError === true, JSON.stringify(denied));
	process.env.XIAOXI_HOST_TOKEN = "";
	delete process.env.XIAOXI_HOST_URL;
	const missing = await tool("list_workers").execute({}, {} as never, BACKGROUND_CONTEXT);
	check("未配置宿主时失败", missing.isError === true, JSON.stringify(missing));
} finally {
	server.close();
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
