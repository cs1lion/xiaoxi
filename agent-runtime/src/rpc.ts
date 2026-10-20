import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { grantApproval, pendingApprovals, revokeApproval } from "./approval.ts";
import type { AgentRuntime } from "./runtime.ts";
import type { RunRequest } from "./types.ts";

export interface RpcOptions {
	runtime: AgentRuntime;
	token?: string;
	onShutdown?: () => Promise<void>;
}

function json(res: ServerResponse, status: number, body: unknown): void {
	res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
	res.end(JSON.stringify(body));
}

async function readBody(req: IncomingMessage): Promise<unknown> {
	const chunks: Buffer[] = [];
	for await (const chunk of req) chunks.push(chunk as Buffer);
	const text = Buffer.concat(chunks).toString("utf8").trim();
	if (text === "") return {};
	return JSON.parse(text);
}

function authorized(req: IncomingMessage, token: string | undefined): boolean {
	if (token === undefined) return true;
	return req.headers.authorization === `Bearer ${token}`;
}

function asRequest(body: unknown): RunRequest | undefined {
	if (typeof body !== "object" || body === null) return undefined;
	const value = body as Record<string, unknown>;
	if (typeof value.commission_id !== "string" || value.commission_id === "") return undefined;
	if (typeof value.agent_id !== "string") return undefined;
	const packet = typeof value.packet === "object" && value.packet !== null
		? (value.packet as Record<string, unknown>)
		: {};
	return {
		commission_id: value.commission_id,
		agent_id: value.agent_id,
		packet,
		workspace: typeof value.workspace === "string" ? value.workspace : "",
		command: Array.isArray(value.command)
			? value.command.filter((item): item is string => typeof item === "string")
			: [],
		worker_env: typeof value.worker_env === "object" && value.worker_env !== null
			? (value.worker_env as Record<string, unknown>)
			: {},
		cost_tier: typeof value.cost_tier === "string" ? value.cost_tier : undefined,
		attempt: Number.isSafeInteger(Number(value.attempt)) && Number(value.attempt) >= 0
			? Number(value.attempt)
			: 0,
		telemetry_proxy: typeof value.telemetry_proxy === "string" ? value.telemetry_proxy : "",
		profile: value.profile === "orchestrator" ? "orchestrator" : "worker",
		lease_id: typeof value.lease_id === "string" ? value.lease_id : "",
		session_id: typeof value.session_id === "string" ? value.session_id : "",
	};
}

export function createRpcServer(options: RpcOptions) {
	const { runtime, token, onShutdown } = options;

	return createServer(async (req, res) => {
		const url = new URL(req.url ?? "/", "http://127.0.0.1");
		const path = url.pathname;

		if (path === "/healthz") {
			json(res, 200, { ok: true });
			return;
		}
		if (!authorized(req, token)) {
			json(res, 401, { error: "unauthorized" });
			return;
		}

		try {
			if (path === "/v1/stats" && req.method === "GET") {
				const memory = process.memoryUsage();
				const handles = (process as unknown as {
					_getActiveHandles?: () => unknown[];
				})._getActiveHandles?.().length ?? 0;
				json(res, 200, {
					pid: process.pid,
					uptime_s: process.uptime(),
					rss: memory.rss,
					heap_used: memory.heapUsed,
					heap_total: memory.heapTotal,
					external: memory.external,
					active_handles: handles,
				});
				return;
			}

			if (path === "/v1/runs" && req.method === "POST") {
				const request = asRequest(await readBody(req));
				if (request === undefined) {
					json(res, 400, { error: "invalid_request" });
					return;
				}
				const ref = await runtime.createRun(request);
				json(res, 200, { task_id: ref.taskId, conversation_id: ref.conversationId });
				return;
			}

			const runMatch = /^\/v1\/runs\/(\d+)$/.exec(path);
			if (runMatch !== null && req.method === "GET") {
				const view = await runtime.readRun(Number(runMatch[1]));
				if (view === undefined) {
					json(res, 404, { error: "not_found" });
					return;
				}
				json(res, 200, view);
				return;
			}

			const cancelMatch = /^\/v1\/runs\/(\d+)\/cancel$/.exec(path);
			if (cancelMatch !== null && req.method === "POST") {
				json(res, 200, { result: await runtime.cancel(Number(cancelMatch[1])) });
				return;
			}

			if (path === "/v1/events" && req.method === "GET") {
				await streamEvents(req, res, runtime, url);
				return;
			}

			if (path === "/v1/lookup" && req.method === "GET") {
				const commissionId = url.searchParams.get("commission_id") ?? "";
				if (commissionId === "") {
					json(res, 400, { error: "invalid_request" });
					return;
				}
				const attempt = Number(url.searchParams.get("attempt") ?? 0);
				const ref = await runtime.lookup(commissionId,
					Number.isSafeInteger(attempt) && attempt >= 0 ? attempt : 0);
				json(res, 200, ref === undefined
					? { found: false }
					: { found: true, task_id: ref.taskId, conversation_id: ref.conversationId });
				return;
			}

			if (path === "/v1/approvals" && req.method === "GET") {
				const commissionId = url.searchParams.get("commission_id") ?? "";
				json(res, 200, { pending: pendingApprovals(commissionId) });
				return;
			}

			if (path === "/v1/approvals" && req.method === "POST") {
				const body = await readBody(req) as {
					commission_id?: unknown; tool?: unknown; action_hash?: unknown; revoke?: unknown;
				};
				const commissionId = typeof body.commission_id === "string" ? body.commission_id : "";
				const tool = typeof body.tool === "string" ? body.tool : "";
				const actionHash = typeof body.action_hash === "string" ? body.action_hash : undefined;
				if (commissionId === "" || tool === "") {
					json(res, 400, { error: "invalid_request" });
					return;
				}
				if (body.revoke === true) revokeApproval(commissionId, tool, actionHash);
				else grantApproval(commissionId, tool, actionHash);
				json(res, 200, { pending: pendingApprovals(commissionId) });
				return;
			}

			if (path === "/v1/shutdown" && req.method === "POST") {
				json(res, 200, { ok: true });
				if (onShutdown !== undefined) setTimeout(() => void onShutdown(), 10);
				return;
			}

			json(res, 404, { error: "not_found" });
		} catch (error) {
			json(res, 500, { error: error instanceof Error ? error.message : String(error) });
		}
	});
}

async function streamEvents(
	req: IncomingMessage,
	res: ServerResponse,
	runtime: AgentRuntime,
	url: URL,
): Promise<void> {
	const taskId = Number(url.searchParams.get("task_id"));
	if (!Number.isSafeInteger(taskId) || taskId <= 0) {
		json(res, 400, { error: "invalid_task_id" });
		return;
	}
	let since = Number(url.searchParams.get("since") ?? 0);
	if (!Number.isSafeInteger(since) || since < 0) since = 0;

	res.writeHead(200, {
		"content-type": "text/event-stream; charset=utf-8",
		"cache-control": "no-cache",
		connection: "keep-alive",
	});
	res.write(": connected\n\n");

	let closed = false;
	req.on("close", () => {
		closed = true;
	});

	const tick = async (): Promise<void> => {
		if (closed) return;
		const view = await runtime.readRun(taskId);
		if (view === undefined) {
			res.write(`data: ${JSON.stringify({ type: "error", text: "run_not_found" })}\n\n`);
			res.end();
			return;
		}
		for (let index = since; index < view.events.length; index += 1) {
			res.write(`data: ${JSON.stringify(view.events[index])}\n\n`);
		}
		since = view.events.length;
		if (view.status === "terminal") {
			res.end();
			return;
		}
		setTimeout(() => void tick(), 50);
	};

	await tick();
}
