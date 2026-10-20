import { Type } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-durable";
import type { ExecutionEnv } from "@earendil-works/pi-durable/env";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { RunEvents, RunMeta } from "./docs.ts";
import type { RunEvent } from "./types.ts";

// The bridge that lets the durable engine *take over* a local agent instead of
// replacing it. pi-durable owns durability (checkpoint, restart recovery, single
// writer); the local CLI keeps its own real tools (Read/Write/Bash/Grep), which
// this sidecar deliberately does not reimplement.
//
// The worker shims under scripts/worker_shims/ already speak PROTOCOL §22's
// five-event line protocol on stdout, so the bridge is: spawn the shim, read its
// lines, append them to the run's event log. Nothing is invented, and cancelling
// the call kills the child (NodeExecutionEnv tracks its pids).
//
// Why not MCP: MCP earns its keep when tools cross a process, language, or
// machine boundary. Here the "tool" is already a subprocess on the same host
// speaking a line protocol, so MCP would add a protocol layer and buy nothing.

export interface TakeoverOptions {
	command: readonly string[];
	cwd: string;
	timeoutSeconds: number;
	/** Environment for the child; see `shimEnv`. */
	env: Record<string, string>;
}

export interface TakeoverResult {
	ok: boolean;
	exitCode: number;
	/** Events the shim emitted, already mapped to the run's vocabulary. */
	events: RunEvent[];
	/** The delivery card, when the shim handed one in. */
	artifact?: Record<string, unknown>;
	/** Why it did not run, when `ok` is false. */
	error?: string;
	/** Bounded tail of the child's stderr: diagnostics for a failed hand-in. */
	diagnostics?: string;
}

// A shim's event maps 1:1 onto RunEvent; `artifact` carries the delivery card the
// evaluator already knows how to read.
function toRunEvent(raw: unknown): RunEvent | undefined {
	if (typeof raw !== "object" || raw === null) return undefined;
	const value = raw as Record<string, unknown>;
	const kind = value.type;
	if (kind === "progress" || kind === "log" || kind === "error") {
		return { type: kind, step_idx: num(value.step_idx), text: String(value.text ?? "").slice(0, 600) };
	}
	if (kind === "token") {
		return {
			type: "token",
			model: String(value.model ?? ""),
			prompt_tokens: num(value.prompt_tokens),
			completion_tokens: num(value.completion_tokens),
			ttft_ms: num(value.ttft_ms),
			duration_ms: num(value.duration_ms),
			status_code: num(value.status_code),
		};
	}
	if (kind === "artifact" && typeof value.artifact === "object" && value.artifact !== null) {
		return { type: "artifact", artifact: value.artifact as Record<string, unknown> };
	}
	return undefined;
}

function num(value: unknown): number {
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : 0;
}

// The shim's environment, mirroring what cli_subproc's prepare_context injects.
// The briefing packet travels in the environment rather than on the command line:
// a command line shows up in `ps`, which broadcasts the job to every process on
// the machine.
//
// `worker_env` is the roster entry's own `env:` block (gateway URL, model label,
// timeouts, permission mode). Without it a shim starts with none of its
// configuration and exits in seconds, which looks like "the agent produced
// nothing" rather than "it was never configured".
export function shimEnv(meta: {
	workspace?: string;
	packet?: Record<string, unknown>;
	commission_id?: string;
	worker_env?: Record<string, unknown>;
	telemetry_proxy?: string;
}): Record<string, string> {
	const env: Record<string, string> = {};
	for (const [key, value] of Object.entries(meta.worker_env ?? {})) {
		if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
			env[key] = String(value);
		}
	}
	if (meta.commission_id !== undefined) env.AQI_COMMISSION_ID = String(meta.commission_id);
	if (meta.packet !== undefined) env.AQI_BRIEFING_PACKET = JSON.stringify(meta.packet);
	if (meta.workspace !== undefined && meta.workspace !== "") {
		env.AQI_WORKDIR = meta.workspace;
		env.AQI_OUTDIR = `${meta.workspace}/out`;
	}
	// The worker's own model calls must go through the observation gateway or its
	// tokens never land in telemetry. The packet's value wins: it is the address
	// Python actually serves, while the sidecar's own environment may have none.
	const proxy = meta.telemetry_proxy ?? process.env.AQI_TELEMETRY_PROXY;
	if (proxy !== undefined && proxy !== "") {
		env.AQI_TELEMETRY_PROXY = proxy;
		env.OPENAI_BASE_URL = proxy;
	}
	return env;
}

export async function runLocalAgent(env: ExecutionEnv, options: TakeoverOptions): Promise<TakeoverResult> {
	// Lines are only *parsed* after the child exits: onOutput cannot await a
	// commit, and committing per chunk would race the line a chunk boundary split.
	// Progress therefore lands as one batch, which is honest about what is durable
	// and keeps the event log free of half-lines.
	let tail = "";
	const complete: string[] = [];
	// stderr is not an event stream, but it is where a shim says *why* it died:
	// a relative path that does not resolve, a missing executable, a Python
	// traceback. Without it the run fails with "the agent handed nothing in" and
	// the real cause is gone — the operator cannot tell a broken command from a
	// silent agent. Keep a bounded tail and put it in the failure message only.
	const noise: string[] = [];
	const result = await env.exec(options.command, {
		cwd: options.cwd,
		env: options.env,
		timeout: Math.max(1, options.timeoutSeconds) * 1000,
		onOutput: (text, _context, info) => {
			if (info?.stream === "stderr") {
				noise.push(text);
				if (noise.length > 200) noise.shift();
				return;
			}
			tail += text;
			const parts = tail.split("\n");
			tail = parts.pop() ?? "";
			for (const line of parts) {
				if (line.trim() !== "") complete.push(line);
			}
		},
	}, BACKGROUND_CONTEXT);
	if (tail.trim() !== "") complete.push(tail);
	const stderrTail = noise.join("").trim().slice(-400);
	if (!result.ok) {
		const reason = String(result.error).slice(0, 240);
		return {
			ok: false,
			exitCode: -1,
			events: [],
			error: stderrTail === "" ? reason : `${reason}：${stderrTail}`.slice(0, 600),
		};
	}

	const events: RunEvent[] = [];
	let artifact: Record<string, unknown> | undefined;
	for (const line of complete) {
		let parsed: unknown;
		try {
			parsed = JSON.parse(line);
		} catch {
			continue;  // Not every line is an event; a shim may log prose.
		}
		const event = toRunEvent(parsed);
		if (event === undefined) continue;
		events.push(event);
		if (event.type === "artifact" && artifact === undefined) artifact = event.artifact;
	}
	return { ok: true, exitCode: result.value.exitCode, events, artifact,
		...(stderrTail === "" ? {} : { diagnostics: stderrTail }) };
}

// The model-facing half. The task takes the same path through `runLocalAgent`;
// this tool exists so a model-driven run can also hand work to a real CLI when
// its own narrow toolset is not enough.
export const runWorker = defineTool({
	name: "run_worker",
	description:
		"把一个本地执行体（真机 CLI Agent）拉起来干这一单，把它的进度与成果收进运行日志。command 是启动命令行；它跑在本单的工作目录里，干完会自己交差。",
	parameters: Type.Object({
		command: Type.Array(Type.String(), { minItems: 1 }),
		timeout_seconds: Type.Optional(Type.Number()),
	}),
	// Spawning a child that writes files is the definition of an outward effect:
	// a crash must surface as interrupted, never as a silent second run.
	replay: "unsafe",
	execute: async (args, api, context) => {
		const env = api.env;
		if (env === undefined) {
			return {
				isError: true,
				output: [{
					type: "text",
					text: "这个执行内核没有配运行环境，不能拉本地执行体。请用 block_and_ask 向老板说明，不要改用别的办法绕过。",
				}],
			};
		}
		const meta = await api.snapshot(RunMeta, api.conversationId, context);
		const outcome = await runLocalAgent(env, {
			command: args.command,
			cwd: meta?.workspace ?? env.cwd,
			timeoutSeconds: Math.max(1, Math.floor(Number(args.timeout_seconds ?? 600))),
			env: shimEnv(meta ?? {}),
		});
		if (!outcome.ok) {
			return {
				isError: true,
				output: [{ type: "text", text: `本地执行体没跑起来：${outcome.error ?? "未知原因"}` }],
			};
		}
		await api.commit(async (tx) => {
			const doc = await tx.doc(RunEvents, api.conversationId);
			for (const event of outcome.events) {
				if (event.type === "artifact" && doc.events.some((existing) => existing.type === "artifact")) {
					continue;  // The first hand-in wins; a rerun must not overwrite it.
				}
				doc.events.push(event);
			}
			return undefined;
		}, context);
		if (outcome.artifact !== undefined) {
			return { output: [{ type: "text", text: "本地执行体已经交差，成果在运行日志里。" }] };
		}
		// The common failure shape: the child exited 0 but never handed anything in.
		// Its stderr is where a shim says why (bad path, missing env, traceback);
		// dropping it here leaves the operator with only "it said nothing".
		const why = outcome.diagnostics === undefined ? "" : ` stderr: ${outcome.diagnostics}`;
		return {
			isError: true,
			output: [{
				type: "text",
				text: `本地执行体跑完了（退出码 ${outcome.exitCode}）但没有交差。${why}看它的进度再决定：自己补一个 submit_artifact，或者用 block_and_ask 请示。`,
			}],
		};
	},
});
