import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ToolExecutionApi } from "@earendil-works/pi-durable";
import type { Context } from "@earendil-works/chord";
import { mkdir, realpath, stat, writeFile as writeTextFile } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { RunMeta } from "./docs.ts";

// The worker's only way to produce something L1 can actually check. Without it a
// run could only claim artifacts, and `evaluator._check_artifacts_exist` verifies
// refs whose kind is `file` — so a run with no write capability either invents a
// path (caught as `no_unwired_dry_run`) or reports an unverifiable kind and passes
// for the wrong reason.
//
// The workspace is the whole sandbox. It comes from `RunMeta` because a tool is
// registered once per Harness and so cannot take it as an argument; an unset
// workspace fails closed rather than guessing a directory.
async function workspaceOf(api: ToolExecutionApi, context: Context): Promise<string> {
	const meta = await api.snapshot(RunMeta, api.conversationId, context);
	return meta?.workspace ?? "";
}

// Resolve `candidate` against `root` and refuse anything landing outside it.
// `resolve` ignores the base for an absolute candidate, which is exactly the
// escape this has to stop; the trailing separator keeps `root-evil` from passing
// as a child of `root`. A missing root is an error, never a reason to fall back to
// some other directory.
export async function insideSandbox(root: string, candidate: string): Promise<string | undefined> {
	let base: string;
	try {
		base = await realpath(root);
	} catch {
		return undefined;
	}
	const target = resolve(base, candidate);
	if (target !== base && !target.startsWith(base + sep)) return undefined;
	return target;
}

export const writeFile = defineTool({
	name: "write_file",
	description:
		"把成果写进本单的工作目录。path 用工作目录内的相对路径或绝对路径，逃出工作目录会被拒绝。返回写好的绝对路径；交差时把它填进 artifacts 的 ref，kind 填 file。",
	parameters: Type.Object({
		path: Type.String({ minLength: 1 }),
		content: Type.String(),
	}),
	// Writing is an outward effect: a crash must surface as interrupted, never as a
	// silent second write of different content.
	replay: "unsafe",
	execute: async (args, api, context) => {
		const root = await workspaceOf(api, context);
		if (root === "") {
			return {
				isError: true,
				output: [{
					type: "text",
					text: "这一单没有分配工作目录，不能写文件。缺素材就用 block_and_ask 请示，或者直接用文字成果调用 submit_artifact 交差。",
				}],
			};
		}
		const exists = await stat(root).then(() => true, () => false);
		if (!exists) {
			// The host is supposed to create the workspace before handing it over.
			// Saying "escape" here would send the model chasing the wrong problem.
			return {
				isError: true,
				output: [{
					type: "text",
					text: `工作目录 ${root} 不存在，写不进去。这不是路径问题，请用 block_and_ask 向老板说明，不要改路径重试。`,
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
		try {
			await mkdir(resolve(target, ".."), { recursive: true });
			await writeTextFile(target, args.content, "utf-8");
		} catch (error) {
			return {
				isError: true,
				output: [{ type: "text", text: `写入失败：${String(error).slice(0, 200)}` }],
			};
		}
		api.output(`wrote ${target}\n`);
		return { output: [{ type: "text", text: target }] };
	},
});
