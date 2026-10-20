import type { Context } from "@earendil-works/chord";
import { defineTask, type TaskRuntime } from "@earendil-works/pi-durable";
import { RunEvents } from "./docs.ts";

export interface BackgroundInput {
	commission_id: string;
	steps: number;
	delay_ms: number;
}

type BackgroundState = { phase: "tick"; step: number };
type BackgroundResult = { done: number };
type Runtime = TaskRuntime<BackgroundInput, BackgroundState, BackgroundResult, Record<string, never>>;

// The Phase 3 primitive: work that does not need to finish on time but may run
// for a long while. Each tick is a durable checkpoint, so a restart resumes at
// the next step instead of starting over.
export const BackgroundTask = defineTask<BackgroundInput, BackgroundState, BackgroundResult>(
	{
		name: "xiaoxi.background",
		version: 1,
		initial: () => ({ phase: "tick", step: 0 }),
		phases: {
			tick: async (task, runtime: Runtime, context: Context) => {
				const step = task.state.checkpoint.step + 1;
				const last = step >= Math.max(1, task.input.steps);
				await runtime.commit(async (tx) => {
					const doc = await tx.doc(RunEvents, runtime.conversationId);
					if (!doc.events.some((event) => event.type === "progress" && event.step_idx === step)) {
						doc.events.push({ type: "progress", step_idx: step, text: `后台第 ${step} 步` });
					}
					return last
						? { status: "terminal", outcome: { status: "completed", result: { done: step } } }
						: { status: "running", checkpoint: { phase: "tick", step } };
				}, context);
				if (!last) {
					await runtime.sleep(Date.now() + Math.max(1, task.input.delay_ms), context);
				}
			},
		},
		abort: async (_task, runtime, context) => {
			await runtime.commit(
				() => ({ status: "terminal", outcome: { status: "aborted" } }),
				context,
			);
		},
	},
	{},
);
