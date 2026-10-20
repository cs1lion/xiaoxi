import { Type } from "@earendil-works/pi-ai";
import {
	defineExtension,
	defineTool,
	type Extension,
	type ToolRegistration,
} from "@earendil-works/pi-durable";
import type { Context } from "@earendil-works/chord";
import {
	McpClient,
	StdioTransport,
	StreamableHttpTransport,
	toLlmContent,
	type CallToolResult,
	type Tool as McpTool,
} from "@earendil-works/pi-mcp";
import { readFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { addApprovalTools } from "./approval.ts";

// MCP tools are the one part of the worker surface that crosses a process or
// machine boundary. The durable harness has no built-in MCP extension, so this
// module is the bridge: connect the configured servers once at sidecar start,
// wrap every tool as a durable `defineTool`, and keep the connection alive for
// the sidecar's lifetime.
//
// Safety defaults:
// - A mutating MCP tool goes through the existing approval hook. The model is
//   blocked and must ask the boss; Python grants it per commission.
// - Only tools the server annotates `readOnlyHint: true` are replay-safe. A
//   crash interrupts everything else instead of silently doing it twice.
// - MCP servers inherit a minimal environment, not the sidecar's LLM keys.
// - Text results are capped well below keepRecentTokens. An uncapped server
//   reply can be larger than the whole compaction reserve, in which case a
//   single message defeats threshold compaction and squeezes the answer to zero.

const MAX_RESULT_CHARS = 12_000;

export interface McpServerConfig {
	command?: string;
	args?: readonly string[];
	cwd?: string;
	env?: Record<string, string>;
	url?: string;
	headers?: Record<string, string>;
	enabled?: boolean;
	timeout?: number;
	approval?: "all" | "mutating" | "none";
}

export interface McpConfig {
	mcpServers?: Record<string, McpServerConfig>;
}

export interface McpBundle {
	readonly extension?: Extension;
	readonly tools: readonly ToolRegistration[];
	readonly names: readonly string[];
	close(): Promise<void>;
}

interface McpToolSource {
	listTools(options?: { timeoutMs?: number }): Promise<McpTool[]>;
	callTool(
		name: string,
		args?: Record<string, unknown>,
		options?: { signal?: AbortSignal; timeoutMs?: number },
	): Promise<CallToolResult>;
}

const DEFAULT_TIMEOUT_SECONDS = 60;
const CONNECT_TIMEOUT_MS = 8_000;
const MAX_TOOL_NAME = 64;

function repoRoot(): string {
	return resolve(import.meta.dirname, "../..");
}

export function mcpConfigPath(env = process.env): string {
	const raw = (env.XIAOXI_MCP_CONFIG ?? "").trim();
	if (raw === "") return resolve(repoRoot(), "config/mcp.json");
	return isAbsolute(raw) ? raw : resolve(repoRoot(), raw);
}

export function expandEnv(value: string, env = process.env): string {
	return value.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_match, name: string) => env[name] ?? "");
}

export function parseMcpConfig(text: string): McpConfig {
	const parsed: unknown = JSON.parse(text);
	if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
		throw new Error("MCP config must be a JSON object");
	}
	const servers = (parsed as Record<string, unknown>).mcpServers;
	if (servers !== undefined && (typeof servers !== "object" || servers === null || Array.isArray(servers))) {
		throw new Error("mcpServers must be an object");
	}
	return parsed as McpConfig;
}

function baseEnv(): Record<string, string> {
	const keep = process.platform === "win32"
		? ["PATH", "PATHEXT", "SystemRoot", "ComSpec", "TEMP", "TMP", "USERPROFILE",
			"HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"]
		: ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"];
	const env: Record<string, string> = {};
	for (const key of keep) {
		const value = process.env[key];
		if (value !== undefined) env[key] = value;
	}
	return env;
}

function serverEnv(config: McpServerConfig): Record<string, string> {
	const env = baseEnv();
	for (const [key, value] of Object.entries(config.env ?? {})) {
		env[key] = expandEnv(value);
	}
	return env;
}

function uniqueName(raw: string, used: Set<string>): string {
	const base = raw.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, MAX_TOOL_NAME);
	let name = base;
	let suffix = 2;
	while (used.has(name)) {
		const tail = `_${suffix}`;
		name = `${base.slice(0, MAX_TOOL_NAME - tail.length)}${tail}`;
		suffix += 1;
	}
	used.add(name);
	return name;
}

function textResult(text: string, isError = false) {
	return {
		isError,
		output: [{ type: "text" as const, text }],
	};
}

export async function toolsForServerAsync(
	server: string,
	client: McpToolSource,
	options: { approval?: "all" | "mutating" | "none"; timeoutMs?: number; used?: Set<string> } = {},
): Promise<ToolRegistration[]> {
	const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_SECONDS * 1000;
	const approval = options.approval ?? "mutating";
	const used = options.used ?? new Set<string>();
	const listed = await client.listTools({ timeoutMs });
	const tools: ToolRegistration[] = [];
	for (const tool of listed) {
		const name = uniqueName(`mcp__${server}__${tool.name}`, used);
		const readOnly = tool.annotations?.readOnlyHint === true;
		if (approval === "all" || (approval === "mutating" && !readOnly)) {
			addApprovalTools([name]);
		}
		const schema = tool.inputSchema ?? {};
		tools.push(defineTool({
			name,
			description: `${server}: ${tool.description ?? tool.title ?? tool.name}`,
			parameters: Type.Unsafe({
				...schema,
				type: "object",
				properties: (schema.properties as Record<string, unknown> | undefined) ?? {},
			}),
			// A read-only server annotation is the only thing that earns safe
			// replay. Everything else must surface as interrupted after a crash.
			replay: readOnly ? "safe" : "unsafe",
			execute: async (args, _api, context: Context) => {
				try {
					const result = await client.callTool(tool.name, args as Record<string, unknown>, {
						signal: context.abortSignal,
						timeoutMs,
					});
					const content = toLlmContent(result);
					if (content.length === 0) {
						content.push({ type: "text", text: "MCP 工具没有返回内容。" });
					}
					let left = MAX_RESULT_CHARS;
					const capped = content.map((item) => {
						if (item.type !== "text") return item;
						const raw = typeof item.text === "string" ? item.text : "";
						if (left <= 0) return { ...item, text: "[已截断，MCP 返回过长]" };
						if (raw.length > left) {
							const text = `${raw.slice(0, left)}\n\n[已截断，MCP 返回过长]`;
							left = 0;
							return { ...item, text };
						}
						left -= raw.length;
						return item;
					});
					return {
						output: capped as never,
						isError: result.isError === true,
					};
				} catch (error) {
					return textResult(`MCP ${server}/${tool.name} 调用失败：${String(error).slice(0, 240)}`, true);
				}
			},
		}));
	}
	return tools;
}

async function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
	let timer: ReturnType<typeof setTimeout> | undefined;
	try {
		return await Promise.race([
			promise,
			new Promise<T>((_resolve, reject) => {
				timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
			}),
		]);
	} finally {
		if (timer !== undefined) clearTimeout(timer);
	}
}

async function connectOne(
	name: string,
	config: McpServerConfig,
	used: Set<string>,
): Promise<{ client: McpClient; tools: ToolRegistration[] } | undefined> {
	const timeoutMs = Math.max(1, Number(config.timeout ?? DEFAULT_TIMEOUT_SECONDS)) * 1000;
	const client = new McpClient({
		name: `xiaoxi-${name}`,
		version: "3.0",
		requestTimeoutMs: timeoutMs,
	});
	let stderr = "";
	try {
		if (typeof config.command === "string" && config.command.trim() !== "") {
			const transport = new StdioTransport({
				command: config.command,
				args: config.args ?? [],
				cwd: config.cwd === undefined ? undefined : resolve(repoRoot(), config.cwd),
				env: serverEnv(config),
				inheritEnv: false,
				stderr: "pipe",
				onStderr: (chunk) => {
					stderr = `${stderr}${chunk}`.slice(-1_000);
				},
			});
			await withTimeout(client.connect(transport), CONNECT_TIMEOUT_MS, `MCP ${name} connect`);
		} else if (typeof config.url === "string" && config.url.trim() !== "") {
			const headers: Record<string, string> = {};
			for (const [key, value] of Object.entries(config.headers ?? {})) {
				headers[key] = expandEnv(value);
			}
			const transport = new StreamableHttpTransport({ url: config.url, headers });
			await withTimeout(client.connect(transport), CONNECT_TIMEOUT_MS, `MCP ${name} connect`);
		} else {
			throw new Error("neither `command` nor `url` is set");
		}
		const tools = await toolsForServerAsync(name, client, {
			approval: config.approval,
			timeoutMs,
			used,
		});
		return { client, tools };
	} catch (error) {
		const detail = stderr.trim() === "" ? "" : ` (stderr: ${stderr.trim().slice(-200)})`;
		console.error(`[xiaoxi-mcp] ${name} 未连接：${String(error).slice(0, 300)}${detail}`);
		await client.close().catch(() => undefined);
		return undefined;
	}
}

export async function connectMcp(path = mcpConfigPath()): Promise<McpBundle> {
	let config: McpConfig;
	try {
		config = parseMcpConfig(await readFile(path, "utf-8"));
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
			console.error(`[xiaoxi-mcp] 读取 ${path} 失败：${String(error).slice(0, 240)}`);
		}
		return { tools: [], names: [], close: async () => undefined };
	}
	const clients: McpClient[] = [];
	const tools: ToolRegistration[] = [];
	const names: string[] = [];
	const used = new Set<string>();
	for (const [name, server] of Object.entries(config.mcpServers ?? {})) {
		if (server === null || typeof server !== "object" || server.enabled === false) continue;
		if (!/^[A-Za-z0-9_-]+$/.test(name)) {
			console.error(`[xiaoxi-mcp] 跳过非法 server 名 ${name}`);
			continue;
		}
		const connected = await connectOne(name, server, used);
		if (connected === undefined) continue;
		clients.push(connected.client);
		tools.push(...connected.tools);
		names.push(name);
	}
	const close = async (): Promise<void> => {
		await Promise.all(clients.map((client) => client.close().catch(() => undefined)));
	};
	if (tools.length === 0) {
		return { tools: [], names, close };
	}
	return {
		extension: defineExtension({ name: "xiaoxi-mcp", tools }),
		tools,
		names,
		close,
	};
}

export const __test = { uniqueName, textResult, baseEnv, serverEnv };
