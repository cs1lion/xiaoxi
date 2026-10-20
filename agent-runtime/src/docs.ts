import { defineDoc } from "@earendil-works/pi-durable";
import type { RunEvent, RunRef } from "./types.ts";

// One document per run, living on the run's own conversation. Conversation
// scope, not task scope: task documents are retired at terminal, and the
// adapter still has to read the event log after the task ends.
export const RunEvents = defineDoc<{ events: RunEvent[] }>({
	kind: "xiaoxi.run-events",
	version: 1,
	scope: "conversation",
	history: "latest",
	fork: "initial",
	initial: () => ({ events: [] }),
});

// commission_id -> run. Session scope so a reopened Harness answers the same
// run for a repeated POST /v1/runs instead of starting a second one.
export const RunIndex = defineDoc<{ runs: Record<string, RunRef> }>({
	kind: "xiaoxi.runs",
	version: 1,
	scope: "session",
	initial: () => ({ runs: {} }),
});

// The one directory a run may write into, plus what a taken-over local agent
// needs to run. Tools are registered once for the whole Harness and so cannot
// take per-run arguments; they read this instead. An empty workspace means "this
// run may not write", which is what makes write_file fail closed rather than
// guess a directory.
export const RunMeta = defineDoc<{
	workspace: string;
	commission_id: string;
	packet: Record<string, unknown>;
	/** The local agent's command line; empty means this run has none to take over. */
	command: string[];
	/** The roster entry's own `env:` block, handed to the taken-over agent. */
	worker_env: Record<string, unknown>;
	/** Observation proxy the host serves; the child's model calls go through it. */
	telemetry_proxy: string;
	/** worker | orchestrator: which extension profile this conversation selects. */
	profile: string;
	/** Host-issued C2 lease; empty for worker runs. */
	lease_id: string;
	/** Host session the lease belongs to. */
	session_id: string;
}>({
	kind: "xiaoxi.run-meta",
	version: 1,
	scope: "conversation",
	history: "latest",
	fork: "initial",
	initial: () => ({ workspace: "", commission_id: "", packet: {}, command: [], worker_env: {},
		telemetry_proxy: "", profile: "worker", lease_id: "", session_id: "" }),
});
