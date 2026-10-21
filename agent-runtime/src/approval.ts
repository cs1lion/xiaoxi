import { createHash } from "node:crypto";
import { ToolTask, hook } from "@earendil-works/pi-durable";
import type { Context } from "@earendil-works/chord";
import { RunIndex } from "./docs.ts";

// Tools registered by an extension after startup (MCP servers are discovered at
// connect time) join the environment list. A test can clear this between cases.
const extraApproval = new Set<string>();

export function addApprovalTools(names: Iterable<string>): void {
	for (const name of names) {
		if (typeof name === "string" && name !== "") extraApproval.add(name);
	}
}

export function clearApprovalTools(): void {
	extraApproval.clear();
}

export function isApprovalRequired(name: string): boolean {
	return names("XIAOXI_APPROVAL_TOOLS").has(name);
}

// Read on each call, not at import: a long-lived sidecar may be granted a tool
// after start, and a test must be able to toggle the two lists.
function names(variable: string): Set<string> {
	const result = new Set(
		(process.env[variable] ?? "")
			.split(",")
			.map((name) => name.trim())
			.filter((name) => name !== ""),
	);
	if (variable === "XIAOXI_APPROVAL_TOOLS") {
		for (const name of extraApproval) result.add(name);
	}
	return result;
}

export interface PendingApproval {
	readonly tool: string;
	readonly action_hash: string;
}

// A grant is one (commission, tool, arguments) tuple, consumed by the call it
// authorized. A second call, even with the same tool, needs its own grant.
const granted = new Set<string>();
const pending = new Map<string, PendingApproval[]>();

function canonical(value: unknown): string {
	if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
	if (value !== null && typeof value === "object") {
		const object = value as Record<string, unknown>;
		return `{${Object.keys(object).sort()
			.map((key) => `${JSON.stringify(key)}:${canonical(object[key])}`)
			.join(",")}}`;
	}
	return JSON.stringify(value) ?? "null";
}

export function approvalHash(call: { name: string; arguments?: unknown }): string {
	return createHash("sha256")
		.update(canonical({ name: call.name, arguments: call.arguments ?? {} }))
		.digest("hex");
}

function key(commissionId: string, tool: string, actionHash: string): string {
	return `${commissionId}:${tool}:${actionHash}`;
}

function rememberPending(commissionId: string, tool: string, actionHash: string): void {
	const items = pending.get(commissionId) ?? [];
	if (!items.some((item) => item.tool === tool && item.action_hash === actionHash)) {
		items.push({ tool, action_hash: actionHash });
		pending.set(commissionId, items);
	}
}

export function grantApproval(commissionId: string, tool: string, actionHash?: string): void {
	const items = pending.get(commissionId) ?? [];
	if (actionHash !== undefined && actionHash !== "") {
		granted.add(key(commissionId, tool, actionHash));
		const remaining = items.filter((item) => !(item.tool === tool && item.action_hash === actionHash));
		if (remaining.length === 0) pending.delete(commissionId);
		else pending.set(commissionId, remaining);
		return;
	}
	// Legacy host callers grant whatever is pending for this tool. Production
	// Python sends the hash, so normal calls stay per-call.
	const matching = items.filter((item) => item.tool === tool);
	for (const item of matching) granted.add(key(commissionId, tool, item.action_hash));
	const remaining = items.filter((item) => item.tool !== tool);
	if (remaining.length === 0) pending.delete(commissionId);
	else pending.set(commissionId, remaining);
}

export function revokeApproval(commissionId: string, tool: string, actionHash?: string): void {
	if (actionHash !== undefined && actionHash !== "") {
		granted.delete(key(commissionId, tool, actionHash));
	} else {
		const items = pending.get(commissionId) ?? [];
		for (const item of items) {
			if (item.tool === tool) granted.delete(key(commissionId, tool, item.action_hash));
		}
	}
	const items = pending.get(commissionId) ?? [];
	const remaining = items.filter((item) =>
		item.tool !== tool || (actionHash !== undefined && actionHash !== "" && item.action_hash !== actionHash));
	if (remaining.length === 0) pending.delete(commissionId);
	else pending.set(commissionId, remaining);
}

export function pendingApprovals(commissionId: string): PendingApproval[] {
	return [...(pending.get(commissionId) ?? [])];
}

// The hook sees a conversationId, not a commission_id; the run index is what maps
// one to the other.
async function commissionOf(api: { snapshot: (token: typeof RunIndex, context: Context) => Promise<{ runs: Record<string, { conversationId: string }> } | undefined> }, context: Context): Promise<string | undefined> {
	const index = await api.snapshot(RunIndex, context);
	if (index === undefined) return undefined;
	for (const [keyName, ref] of Object.entries(index.runs)) {
		if (String(ref.conversationId) === String(api.conversationId)) {
			// Keys are `commission_id@attempt`; the approval ledger is per commission.
			return keyName.includes("@") ? keyName.slice(0, keyName.lastIndexOf("@")) : keyName;
		}
	}
	return undefined;
}

export const approvalHook = hook(ToolTask, {
	beforeTool: async (call, api, context) => {
		if (!names("XIAOXI_APPROVAL_TOOLS").has(call.name)) return undefined;
		if (names("XIAOXI_APPROVED_TOOLS").has(call.name)) return undefined;
		const commissionId = await commissionOf(api as never, context);
		if (commissionId === undefined) {
			return {
				block: `工具 ${call.name} 需要老板确认后才能执行。先向老板请示，不要绕过。`,
			};
		}
		const hash = approvalHash(call);
		const grantKey = key(commissionId, call.name, hash);
		if (granted.has(grantKey)) {
			granted.delete(grantKey); // one call, one grant
			return undefined;
		}
		rememberPending(commissionId, call.name, hash);
		return {
			block: `工具 ${call.name} 的这一次调用需要老板确认后才能执行。

先向老板请示，不要绕过，也不要改用别的工具达到同样目的。`,
		};
	},
});
