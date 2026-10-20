// The sidecar's internal contract. Nothing here crosses into PROTOCOL.md:
// Python is the only party that speaks to the browser.

export type RunEvent =
	| { type: "progress"; step_idx: number; text: string }
	| {
			type: "token";
			model: string;
			prompt_tokens: number;
			completion_tokens: number;
			ttft_ms: number;
			duration_ms: number;
			status_code: number;
		}
	| { type: "artifact"; artifact: Record<string, unknown> }
	| { type: "log"; text: string }
	| { type: "error"; text: string; kind?: "clarification" | "approval" };

export interface RunRequest {
	commission_id: string;
	agent_id: string;
	packet: Record<string, unknown>;
	/** Absolute directory this run may write into. Empty means "no writes". */
	workspace?: string;
	/** The roster entry's cost tier; selects the model for this run's conversation. */
	cost_tier?: string;
	/** Command line of the local agent this run takes over, if any. */
	command?: string[];
	/** The roster entry's own `env:` block, handed to the taken-over agent. */
	worker_env?: Record<string, unknown>;
	/** Observation proxy the host serves for the worker's own model calls. */
	telemetry_proxy?: string;
	/** Worker runs one commission; orchestrator runs C2 dispatch/track/answer. */
	profile?: "worker" | "orchestrator";
	/** Host-issued C2 lease; never sent by the model. */
	lease_id?: string;
	/** Host session the lease belongs to. */
	session_id?: string;
	/**
	 * Which attempt at this commission this is (the ledger's retry_count).
	 * Part of the idempotency key: a re-run after the evaluator rejected the
	 * previous delivery must get a fresh task, while a repeated POST for the
	 * same attempt must still return the existing one.
	 */
	attempt?: number;
}

export interface RunRef {
	taskId: number;
	conversationId: string;
}
