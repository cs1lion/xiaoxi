import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ToolExecutionApi } from "@earendil-works/pi-durable";
import type { Context } from "@earendil-works/chord";
import { readFile as readText, realpath, stat } from "node:fs/promises";
import { sep } from "node:path";
import { RunMeta } from "./docs.ts";
import { insideSandbox } from "./write_file.ts";

// A worker that can only write cannot check its own work, and a long-running
// task usually starts by reading what is already in its sandbox. The root comes
// from RunMeta for the same reason write_file does: tools are registered once,
// while the sandbox is per conversation.
//
// 12k chars stays below keepRecentTokens (≈12k) so compaction can still cut
// around a single read. A 30k-char Chinese file measured ~20k+ tokens and
// defeated threshold compaction: the kept recent message alone exceeded the
// whole reserve, and the next answer got one token of output.
const MAX_CHARS = 12_000;

async function workspaceOf(api: ToolExecutionApi, context: Context): Promise<string> {
	const meta = await api.snapshot(RunMeta, api.conversationId, context);
	return meta?.workspace ?? "";
}

export const readFile = defineTool({
	name: "read_file",
	description:
		"读取本单工作目录内的文本文件。path 用工作目录内的相对路径或绝对路径；不能读工作目录外。大文件只返回前 12000 字符。",
	parameters: Type.Object({
		path: Type.String({ minLength: 1 }),
	}),
	replay: "safe",
	execute: async (args, api, context) => {
		const root = await workspaceOf(api, context);
		if (root === "") {
			return {
				isError: true,
				output: [{
					type: "text",
					text: "这一单没有分配工作目录，不能读文件。缺素材就用 block_and_ask 请示。",
				}],
			};
		}
		const rootExists = await stat(root).then((value) => value.isDirectory(), () => false);
		if (!rootExists) {
			return {
				isError: true,
				output: [{
					type: "text",
					text: `工作目录 ${root} 不存在或不是目录，读不了。这不是路径问题，请用 block_and_ask 说明。`,
				}],
			};
		}
		const target = await insideSandbox(root, args.path);
		if (target === undefined) {
			return {
				isError: true,
				output: [{ type: "text", text: `路径逃出了工作目录 ${root}，已拒绝。` }],
			};
		}
		// A symlink inside the sandbox can point outside it; resolve the real path
		// before reading.
		const [rootReal, targetReal] = await Promise.all([
			realpath(root).catch(() => undefined),
			realpath(target).catch(() => undefined),
		]);
		if (rootReal !== undefined && targetReal !== undefined
			&& targetReal !== rootReal && !targetReal.startsWith(rootReal + sep)) {
			return {
				isError: true,
				output: [{ type: "text", text: `符号链接指向工作目录外，已拒绝。` }],
			};
		}
		const info = await stat(target).then((value) => value, () => undefined);
		if (info === undefined || !info.isFile()) {
			return {
				isError: true,
				output: [{ type: "text", text: `工作目录里没有这个文件：${args.path}` }],
			};
		}
		let text: string;
		try {
			text = await readText(target, "utf-8");
		} catch (error) {
			return {
				isError: true,
				output: [{ type: "text", text: `读取失败：${String(error).slice(0, 200)}` }],
			};
		}
		if (text.includes("\u0000")) {
			return { isError: true, output: [{ type: "text", text: "这是二进制文件，read_file 只读文本。" }] };
		}
		if (text.length > MAX_CHARS) {
			text = `${text.slice(0, MAX_CHARS)}\n\n[已截断，原文件更长]`;
		}
		api.output(`read ${target}\n`);
		return { output: [{ type: "text", text }] };
	},
});
