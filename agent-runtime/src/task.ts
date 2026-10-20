import type { Context } from "@earendil-works/chord";
import { AgentDoc, AssistantEntry, UsageDoc, configure, defineTask, type TaskRuntime } from "@earendil-works/pi-durable";
import { RunEvents, RunMeta } from "./docs.ts";
import { runLocalAgent, shimEnv } from "./takeover.ts";
import type { RunEvent, RunRequest } from "./types.ts";

type RunState = { phase: "deliver" };
type RunResult = { artifact: Record<string, unknown> };
type Runtime = TaskRuntime<RunRequest, RunState, RunResult, Record<string, never>>;

async function emitOnce(runtime: Runtime, context: Context, step: number, text: string): Promise<void> {
	await runtime.commit(async (tx) => {
		const doc = await tx.doc(RunEvents, runtime.conversationId);
		// Idempotent by step number: a rerun after a crash replays nothing.
		if (!doc.events.some((event) => event.type === "progress" && event.step_idx === step)) {
			doc.events.push({ type: "progress", step_idx: step, text });
		}
		return undefined;
	}, context);
}

async function artifactOf(runtime: Runtime, context: Context): Promise<Record<string, unknown> | undefined> {
	const doc = await runtime.snapshot(RunEvents, runtime.conversationId, context);
	const found = doc?.events.find((event) => event.type === "artifact");
	return found?.artifact;
}

async function finish(runtime: Runtime, context: Context, artifact: Record<string, unknown>): Promise<void> {
	await runtime.commit(() => ({
		status: "terminal",
		outcome: { status: "completed", result: { artifact } },
	}), context);
}

// The project briefing, mirroring the worker shims' `prompt_for` fields
// (scripts/worker_shims/*.py): the same commission must read identically
// whichever engine runs it, so an A/B compares engines rather than prompt
// wording. `deliverable` used to be dropped here, which left the durable worker
// blind to the line of duty the packet already spelled out.
function listOf(value: unknown, limit = 8): string[] {
	if (!Array.isArray(value)) return [];
	return value
		.slice(0, limit)
		.map((item) => (typeof item === "string" ? item : JSON.stringify(item)))
		.filter((item) => item.trim() !== "");
}

function briefingOf(request: RunRequest): string {
	const packet = request.packet;
	const objective = packet.objective;
	const text = typeof objective === "string" && objective.trim() !== "" ? objective : request.commission_id;
	const lines = [text];
	const deliverable = packet.deliverable;
	if (typeof deliverable === "string" && deliverable.trim() !== "") {
		lines.push(`交付：${deliverable.trim()}`);
	}
	const constraints = listOf(packet.constraints, 6);
	if (constraints.length > 0) {
		lines.push(`约束：${constraints.join("；")}`);
	}
	// The memory block already carries its own untrusted-data frame
	// (server/memory/commission_context.py). It belongs here, with the rest of the
	// briefing: `instructions` survives compaction, a first user message does not.
	const memory = typeof packet.memory_block === "string" ? packet.memory_block.trim() : "";
	if (memory !== "") {
		lines.push(memory);
	}
	const rubrics = listOf(packet.rubrics, 8);
	if (rubrics.length > 0) {
		lines.push(`审核铁律（交付前逐条自检）：\n${rubrics.map((item) => `- ${item}`).join("\n")}`);
	}
	const acceptance = listOf(packet.acceptance, 8);
	if (acceptance.length > 0) {
		lines.push(`验收检查：\n${acceptance.map((item) => `- ${item}`).join("\n")}`);
	}
	const feedback = typeof packet.feedback === "string" ? packet.feedback.trim() : "";
	if (feedback !== "") {
		lines.push(`上一轮批注（这一单必须逐条回应）：\n${feedback.slice(0, 1200)}`);
	}
	return lines.join("\n");
}

// Engine instruction, mirroring the shims' tail lines ("Create the deliverable
// as a real file..."): without it the model sometimes answers in prose and never
// calls the delivery tool, which the run then has to fail. With a workspace the
// deliverable must be a real file: L1's `artifacts_exist` only verifies refs whose
// kind is `file`, so a run that never writes one cannot be checked at all.
function deliveryInstruction(workspace: string | undefined): string {
	const write = workspace === undefined
		? ""
		: `先用 write_file 把成果写进工作目录 ${workspace}（只能写这个目录里的文件），`;
	return `干完必须调用 submit_artifact 交差：summary 写做了什么，intercepted 写替老板拦下了什么，artifacts 至少一项，wired 为 true。${write}交差时 artifacts 里写真实文件的绝对路径、kind 填 "file"。不要编造没有写过的文件路径，不要只用文字回答。`;
}

const NUDGE = "你还没有交差。现在调用 submit_artifact，把这一单的成果交上来。";

// One original turn plus one nudge. Each attempt is its own submission with its
// own requestId, so a crash between them re-runs the phase without duplicating
// the work already admitted.
const NUDGE_ATTEMPTS = 2;

// The briefing belongs in `instructions`, not the first user message: compaction
// summarizes messages and leaves sections alone, so a long run keeps its brief
// (§4.4 of docs/PI_DURABLE_MIGRATION.md). A `ConversationHandle` exposes only
// submit/abort, so the change goes through the exported `configure()` inside this
// task's own commit.
//
// Appended, never replacing: `openAgentRuntime` seeds the instructions with the
// run's framing, including the anti-fabrication rule ("缺素材时用 block_and_ask …
// 不要编造"). Overwriting the whole string silently dropped that constraint.
async function ensureInstructions(runtime: Runtime, context: Context, briefing: string): Promise<void> {
	const state = await runtime.snapshot(AgentDoc, runtime.conversationId, context);
	const current = state?.instructions ?? "";
	if (current.includes(briefing)) return;
	const next = current === "" ? briefing : `${current}\n\n${briefing}`;
	await runtime.commit(async (tx) => {
		await configure(tx, runtime.conversationId, { instructions: next });
		return undefined;
	}, context);
}

function workerMode(): "script" | "model" {
	if (process.env.XIAOXI_WORKER_MODE === "script") return "script";
	if (process.env.XIAOXI_WORKER_MODE === "model") return "model";
	const configured = (process.env.LLM_BASE_URL ?? "") !== "" && (process.env.LLM_MODEL ?? "") !== "";
	return configured ? "model" : "script";
}

async function answerText(runtime: Runtime, context: Context, answerId: string | undefined): Promise<string> {
	if (answerId === undefined) return "";
	const entry = await runtime.entry(AssistantEntry, answerId as never, context);
	const message = entry?.model?.[0] as { content?: { type: string; text?: string }[] } | undefined;
	return (message?.content ?? []).map((block) => (block.type === "text" ? String(block.text ?? "") : "")).join("");
}

async function emitUsage(runtime: Runtime, context: Context): Promise<void> {
	// Usage lives in the conversation's `pi.usage` ledger, keyed by model, not on
	// the assistant entry. Sum it once per run.
	const state = await runtime.snapshot(UsageDoc, runtime.conversationId, context);
	if (process.env.XIAOXI_DEBUG) console.error("[xiaoxi] usage state", JSON.stringify(state));
	if (state === undefined) return;
	let input = 0;
	let output = 0;
	let model = "";
	for (const [key, usage] of Object.entries(state.models ?? {})) {
		input += Number((usage as { input?: number }).input ?? 0);
		output += Number((usage as { output?: number }).output ?? 0);
		model = key;
	}
	if (input === 0 && output === 0) return;
	await runtime.commit(async (tx) => {
		const doc = await tx.doc(RunEvents, runtime.conversationId);
		if (!doc.events.some((event) => event.type === "token")) {
			doc.events.push({
				type: "token",
				model,
				prompt_tokens: input,
				completion_tokens: output,
				ttft_ms: 0,
				duration_ms: 0,
				status_code: 200,
			});
		}
		return undefined;
	}, context);
	if (process.env.XIAOXI_DEBUG) {
		const after = await runtime.snapshot(RunEvents, runtime.conversationId, context);
		console.error("[xiaoxi] events after usage", JSON.stringify((after?.events ?? []).map((event) => event.type)));
	}
}

async function deliverOrchestrator(runtime: Runtime, context: Context, request: RunRequest): Promise<void> {
	const conversation = await runtime.conversation(runtime.conversationId, context);
	if (conversation === undefined) throw new Error("orchestrator conversation is missing");
	await ensureInstructions(runtime, context, briefingOf(request));
	const prompt = typeof request.packet.prompt === "string" && request.packet.prompt.trim() !== ""
		? request.packet.prompt.trim()
		: briefingOf(request);
	const submission = await conversation.submit(
		{ type: "input", content: prompt, requestId: `run:${runtime.taskId}:orchestrator` },
		context,
	);
	const settled = await submission.wait(context);
	const said = settled.status === "done" ? await answerText(runtime, context, settled.answer) : "";
	if (settled.status === "done" && said.trim() !== "") {
		const artifact = { summary: said.trim().slice(0, 400), artifacts: [], orchestrated: true };
		await runtime.commit(() => ({
			status: "terminal",
			outcome: { status: "completed", result: { artifact } },
		}), context);
		return;
	}
	const reason = settled.status === "done" ? "编排者没有给出回答" : `编排者运行未完成：${settled.status}`;
	await runtime.commit(() => ({
		status: "terminal",
		outcome: { status: "failed", error: { message: reason } },
	}), context);
}

async function deliverWithModel(runtime: Runtime, context: Context, request: RunRequest): Promise<void> {
	const already = await artifactOf(runtime, context);
	if (already !== undefined) {
		await finish(runtime, context, already);
		return;
	}
	const conversation = await runtime.conversation(runtime.conversationId, context);
	if (conversation === undefined) throw new Error("run conversation is missing");
	await ensureInstructions(runtime, context, briefingOf(request));
	// A model that answers in prose instead of calling the tool is common (roughly
	// one run in five with step-5-preview). One nudge in the same conversation
	// recovers most of those; the harness has no forced tool choice to lean on.
	let said = "";
	for (let attempt = 1; attempt <= NUDGE_ATTEMPTS; attempt += 1) {
		// The stable requestId is what makes a crash before the answer arrive at the
		// same submission instead of submitting the work twice.
		const submission = await conversation.submit(
			{
				type: "input",
				content: attempt === 1 ? deliveryInstruction(request.workspace) : NUDGE,
				requestId: `run:${runtime.taskId}:${attempt}`,
			},
			context,
		);
		const settled = await submission.wait(context);
		const artifact = await artifactOf(runtime, context);
		if (artifact !== undefined) {
			await emitUsage(runtime, context);
			await finish(runtime, context, artifact);
			return;
		}
		said = settled.status === "done" ? await answerText(runtime, context, settled.answer) : "";
		if (settled.status !== "done") break;
	}
	const reason = `没有调用 submit_artifact${said === "" ? "" : `：${said.slice(0, 200)}`}`;
	await runtime.commit(async (tx) => {
		const doc = await tx.doc(RunEvents, runtime.conversationId);
		doc.events.push({ type: "error", text: `执行体没交差：${reason}` });
		return {
			status: "terminal",
			outcome: { status: "failed", error: { message: reason } },
		};
	}, context);
}

// The takeover path: spawn the local agent through the same helper the
// `run_worker` tool uses, then let the shim's own artifact finish the run. The
// shim already speaks PROTOCOL §22, so no translation layer is invented here.
async function takeoverWithWorker(runtime: Runtime, context: Context, request: RunRequest): Promise<void> {
	const already = await artifactOf(runtime, context);
	if (already !== undefined) {
		await finish(runtime, context, already);
		return;
	}
	const command = request.command ?? [];
	const env = await runtime.env(context);
	if (env === undefined) throw new Error("takeover requested but no execution environment is configured");
	const meta = await runtime.snapshot(RunMeta, runtime.conversationId, context);
	const outcome = await runLocalAgent(env, {
		command,
		cwd: meta?.workspace ?? env.cwd,
		timeoutSeconds: 600,
		env: shimEnv(meta ?? {}),
	});
	const artifact = outcome.ok ? (outcome.artifact ?? (await artifactOf(runtime, context))) : undefined;
	// A shim that exits 0 without a delivery card usually died before it could
	// emit one (bad path, missing key, crashed CLI). Its stderr tail is the only
	// account of that, so attach it instead of reporting "handed nothing in".
	const missing = artifact === undefined
		? `本地执行体没有交差${outcome.diagnostics ? `（stderr：${outcome.diagnostics}）` : ""}`
		: undefined;
	const failure = !outcome.ok ? (outcome.error ?? "takeover failed") : missing;
	await runtime.commit(async (tx) => {
		const doc = await tx.doc(RunEvents, runtime.conversationId);
		for (const event of outcome.events) {
			if (event.type === "artifact" && doc.events.some((existing) => existing.type === "artifact")) {
				continue;  // The first hand-in wins; a rerun must not overwrite it.
			}
			doc.events.push(event);
		}
		if (failure !== undefined) {
			doc.events.push({ type: "error", text: `接管没成：${failure}`.slice(0, 600) });
			return { status: "terminal", outcome: { status: "failed", error: { message: failure } } };
		}
		return undefined;  // The terminal outcome is a separate commit, below.
	}, context);
	// Crash spike for the recovery test: die *after* the shim's events (including
	// its delivery card) are durable but *before* the terminal outcome. Reopening
	// must then finish from the event log without spawning the agent a second
	// time — the guarantee that matters, because re-running a real CLI would burn
	// tokens and could overwrite work it already delivered.
	if (failure === undefined && process.env.XIAOXI_TAKEOVER_CRASH_AFTER_ARTIFACT === "1") {
		process.exit(9);
	}
	if (failure !== undefined) return;
	await runtime.commit(() => ({
		status: "terminal",
		outcome: { status: "completed", result: { artifact } },
	}), context);
}

// The scripted worker runs without a model so durability can be tested on its
// own; the model worker is the production path once a provider is configured.
const SCRIPT_STEPS = [
	{ step: 1, text: "接单" },
	{ step: 2, text: "干活" },
	{ step: 3, text: "收尾" },
];
// Test/soak hook: the scripted path normally runs in a few milliseconds, which
// is too fast to hit with a hard kill. Default stays 5ms.
const STEP_DELAY_MS = Math.max(1, Number(process.env.XIAOXI_STEP_DELAY_MS ?? 5) || 5);

export const RunTask = defineTask<RunRequest, RunState, RunResult>(
	{
		name: "xiaoxi.run",
		version: 1,
		initial: () => ({ phase: "deliver" }),
		phases: {
			deliver: async (task, runtime, context) => {
				if (task.input.profile === "orchestrator") {
					await deliverOrchestrator(runtime, context, task.input);
					return;
				}
				// A run with a local agent to take over does not run its own model: the
				// whole point is that the real CLI keeps its own tools, and a model turn
				// here would only paraphrase work the CLI already did.
				if (Array.isArray(task.input.command) && task.input.command.length > 0) {
					await takeoverWithWorker(runtime, context, task.input);
					return;
				}
				if (workerMode() === "model") {
					await deliverWithModel(runtime, context, task.input);
					return;
				}
				for (const { step, text } of SCRIPT_STEPS) {
					await emitOnce(runtime, context, step, text);
					if (step === 2 && process.env.XIAOXI_SPIKE_CRASH === "1") {
						process.exit(9);
					}
					await runtime.sleep(Date.now() + STEP_DELAY_MS, context);
				}
				const already = await artifactOf(runtime, context);
				if (already !== undefined) {
					await finish(runtime, context, already);
					return;
				}
				const artifact = { summary: `完成：${task.input.commission_id}`, artifacts: [] };
				await runtime.commit(async (tx) => {
					const events = await tx.doc(RunEvents, runtime.conversationId);
					events.events.push({ type: "artifact", artifact });
					return {
						status: "terminal",
						outcome: { status: "completed", result: { artifact } },
					};
				}, context);
			},
		},
		abort: async (_task, runtime, context) => {
			await runtime.commit(async (tx) => {
				const events = await tx.doc(RunEvents, runtime.conversationId);
				events.events.push({ type: "error", text: "cancelled" });
				return { status: "terminal", outcome: { status: "aborted" } };
			}, context);
		},
	},
	{},
);
