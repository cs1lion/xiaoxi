import { Type } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-durable";
import { stat } from "node:fs/promises";
import { RunEvents, RunMeta } from "./docs.ts";
import { insideSandbox } from "./write_file.ts";

// A model copying the path write_file handed it back into its own JSON arguments
// tends to escape the separators a second time, producing `D:\\project\\...` —
// a string that is not a path on Windows. L1's `artifacts_exist` then reports a
// file that is sitting right there as missing. Canonicalizing here, against the
// same sandbox write_file used, means the delivery card carries a path that is
// real by construction instead of trusting the model's transcription.
export async function canonicalRefs(
	api: { snapshot: (token: typeof RunMeta, id: never, context: never) => Promise<{ workspace: string } | undefined> },
	context: never,
	artifacts: { kind?: unknown; ref?: unknown }[],
): Promise<{ refs: { kind?: unknown; ref?: unknown }[]; missing: string[] }> {
	const meta = await api.snapshot(RunMeta, api.conversationId as never, context);
	const root = meta?.workspace ?? "";
	const missing: string[] = [];
	const refs: { kind?: unknown; ref?: unknown }[] = [];
	for (const item of artifacts) {
		if (item.kind !== "file" || typeof item.ref !== "string") {
			refs.push(item);
			continue;
		}
		const flattened = item.ref.replace(/\\\\/g, "\\");
		const target = root === "" ? undefined : await insideSandbox(root, flattened);
		const exists = target === undefined ? false : await stat(target).then((s) => s.isFile(), () => false);
		if (!exists) {
			missing.push(flattened);
			refs.push(item);
			continue;
		}
		refs.push({ ...item, ref: target });
	}
	return { refs, missing };
}

// The only two ways a run can end. Both terminate the round: without that the
// model would keep talking after handing in its work.
export const submitArtifact = defineTool({
	name: "submit_artifact",
	description:
		"交差。summary 写做了什么；intercepted 写替老板拦下了什么，没有就写『没有』；artifacts 至少一项；wired 必须为 true。file 型 ref 必须填 write_file 返回的那个绝对路径。",
	parameters: Type.Object({
		summary: Type.String({ minLength: 1 }),
		intercepted: Type.String({ minLength: 1 }),
		artifacts: Type.Array(
			Type.Object({
				kind: Type.String(),
				ref: Type.String(),
				preview: Type.Optional(Type.String()),
			}),
		),
		wired: Type.Boolean(),
	}),
	// Handing in is an outward decision: a crash must surface as "interrupted",
	// never as a silent second hand-in.
	replay: "unsafe",
	execute: async (args, api, context) => {
		const { refs, missing } = await canonicalRefs(api as never, context as never, args.artifacts);
		if (missing.length > 0) {
			// Refuse the hand-in rather than record a card pointing at nothing: the
			// project's own `no_unwired_dry_run` rule exists to catch exactly this.
			return {
				isError: true,
				output: [{
					type: "text",
					text: `这些 file 型成果不存在：${missing.join("、")}。先用 write_file 把它们写进工作目录，再拿返回的绝对路径来交差。`,
				}],
			};
		}
		const artifact = { ...args, artifacts: refs };
		await api.commit(async (tx) => {
			const doc = await tx.doc(RunEvents, api.conversationId);
			if (!doc.events.some((event) => event.type === "artifact")) {
				doc.events.push({ type: "artifact", artifact });
			}
			return undefined;
		}, context);
		return {
			output: [{ type: "text", text: "交差已记录，这一轮结束。" }],
			control: { terminate: true },
		};
	},
});

export const blockAndAsk = defineTool({
	name: "block_and_ask",
	description:
		"干不下去时请示老板。问一句具体的选择题，不要空泛地求助。kind 默认 clarification；如果是在请老板批准某个工具/动作，必须用 approval，它会进入确认单路径，不能由自动编排代答。",
	parameters: Type.Object({
		question: Type.String(),
		kind: Type.Optional(Type.Union([Type.Literal("clarification"), Type.Literal("approval")])),
	}),
	replay: "unsafe",
	execute: async (args, api, context) => {
		await api.commit(async (tx) => {
			const doc = await tx.doc(RunEvents, api.conversationId);
			doc.events.push({
				type: "error",
				text: args.question.slice(0, 400),
				kind: args.kind === "approval" ? "approval" : "clarification",
			});
			return undefined;
		}, context);
		return {
			output: [{ type: "text", text: "已经把问题交上去了。" }],
			control: { terminate: true },
		};
	},
});
