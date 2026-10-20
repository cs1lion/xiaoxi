import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ToolExecutionApi } from "@earendil-works/pi-durable";
import type { Context } from "@earendil-works/chord";
import { readdir, realpath, stat } from "node:fs/promises";
import { join, sep } from "node:path";
import { RunMeta } from "./docs.ts";
import { insideSandbox } from "./write_file.ts";

// A manager needs to know what is already in its workspace before asking a
// child worker to touch it. Listing is read-only and bounded; symlinks are
// reported but never followed, so a listing cannot escape the sandbox.
const MAX_ENTRIES = 300;
const MAX_DEPTH = 4;

async function workspaceOf(api: ToolExecutionApi, context: Context): Promise<string> {
	const meta = await api.snapshot(RunMeta, api.conversationId, context);
	return meta?.workspace ?? "";
}

export const listFiles = defineTool({
	name: "list_files",
	description:
		"列出本单工作目录内的文件和子目录（只读，最多 300 项，深度最多 4 层）。path 省略时从工作目录根开始；不能列出工作目录外。",
	parameters: Type.Object({
		path: Type.Optional(Type.String()),
		depth: Type.Optional(Type.Number({ minimum: 0, maximum: MAX_DEPTH })),
	}),
	replay: "safe",
	execute: async (args, api, context) => {
		const root = await workspaceOf(api, context);
		if (root === "") {
			return {
				isError: true,
				output: [{ type: "text", text: "这一单没有分配工作目录，不能列文件。" }],
			};
		}
		const target = await insideSandbox(root, args.path ?? ".");
		if (target === undefined) {
			return {
				isError: true,
				output: [{ type: "text", text: `路径逃出了工作目录 ${root}，已拒绝。` }],
			};
		}
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
		if (info === undefined) {
			return { isError: true, output: [{ type: "text", text: `目录不存在：${args.path ?? "."}` }] };
		}
		const maxDepth = Math.max(0, Math.min(MAX_DEPTH, Math.floor(Number(args.depth ?? 2))));
		const entries: { path: string; type: "file" | "dir" | "symlink"; size?: number }[] = [];
		const walk = async (directory: string, relative: string, depth: number): Promise<void> => {
			if (entries.length >= MAX_ENTRIES) return;
			let items;
			try {
				items = await readdir(directory, { withFileTypes: true });
			} catch {
				return;
			}
			items.sort((left, right) => left.name.localeCompare(right.name));
			for (const item of items) {
				if (entries.length >= MAX_ENTRIES) return;
				if (item.name === "node_modules" || item.name === ".git") continue;
				const next = relative === "" ? item.name : `${relative}/${item.name}`;
				const full = join(directory, item.name);
				if (item.isSymbolicLink()) {
					entries.push({ path: next, type: "symlink" });
				} else if (item.isDirectory()) {
					entries.push({ path: next, type: "dir" });
					if (depth < maxDepth) await walk(full, next, depth + 1);
				} else if (item.isFile()) {
					const size = await stat(full).then((value) => value.size, () => undefined);
					entries.push({ path: next, type: "file", ...(size === undefined ? {} : { size }) });
				}
			}
		};
		if (info.isDirectory()) {
			await walk(target, "", 0);
		} else if (info.isFile()) {
			entries.push({ path: args.path ?? ".", type: "file", size: info.size });
		}
		return {
			output: [{
				type: "text",
				text: JSON.stringify({
					root, path: args.path ?? ".", depth: maxDepth, entries,
					truncated: entries.length >= MAX_ENTRIES,
				}, null, 2),
			}],
		};
	},
});
