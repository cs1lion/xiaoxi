import { Type } from "@earendil-works/pi-ai";
import {
	defineExtension,
	defineTool,
	section,
	type Extension,
	type ToolRegistration,
} from "@earendil-works/pi-durable";
import type { Context } from "@earendil-works/chord";
import { readFile, readdir, realpath, stat } from "node:fs/promises";
import { basename, delimiter, dirname, isAbsolute, resolve, sep } from "node:path";
import { parse as parseYaml } from "yaml";

// Progressive disclosure, matching the Agent Skills layout Pi's coding agent
// uses: every skill is a directory with a SKILL.md. Only the name/description
// goes into the system prompt; the body is read on demand, so a long skill does
// not tax every request.
//
// This is deliberately separate from `.agents/skills`: that directory belongs to
// the developer tooling in this repo (oil-ui, pstack, …) and must not leak into a
// production worker's prompt. `XIAOXI_SKILLS_DIR` points at the product skills.

export interface Skill {
	readonly name: string;
	readonly description: string;
	readonly directory: string;
	readonly file: string;
}

const MAX_SKILLS = 64;
const MAX_SKILL_FILE_CHARS = 20_000;
const MAX_DESCRIPTION_CHARS = 1_024;
const MAX_SECTION_CHARS = 6_000;

function repoRoot(): string {
	return resolve(import.meta.dirname, "../..");
}

function resolveDir(value: string): string {
	return isAbsolute(value) ? value : resolve(repoRoot(), value);
}

export function skillDirectories(env = process.env): string[] {
	const raw = (env.XIAOXI_SKILLS_DIR ?? "").trim();
	if (raw === "") return [resolve(repoRoot(), "config/skills")];
	return raw
		.split(delimiter)
		.map((item) => item.trim())
		.filter((item) => item !== "")
		.map(resolveDir);
}

function frontmatter(text: string): { data: Record<string, unknown>; body: string } {
	const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
	if (match === null) return { data: {}, body: text };
	let parsed: unknown;
	try {
		parsed = parseYaml(match[1]);
	} catch {
		return { data: {}, body: text };
	}
	const data = typeof parsed === "object" && parsed !== null
		? parsed as Record<string, unknown>
		: {};
	return { data, body: text.slice(match[0].length) };
}

function textValue(value: unknown): string {
	return typeof value === "string" ? value.trim() : "";
}

function skillFrom(directory: string, file: string, text: string): Skill | undefined {
	const { data } = frontmatter(text);
	const description = textValue(data.description).slice(0, MAX_DESCRIPTION_CHARS);
	if (description === "") return undefined; // A skill without a routing description is not loadable.
	const declared = textValue(data.name);
	const fallback = basename(directory).toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
	const name = (declared || fallback).slice(0, 64);
	if (name === "") return undefined;
	if (data["disable-model-invocation"] === true) return undefined;
	return { name, description, directory, file };
}

async function isFile(path: string): Promise<boolean> {
	return stat(path).then((value) => value.isFile(), () => false);
}

async function walk(directory: string, depth: number, found: string[]): Promise<void> {
	if (depth > 3 || found.length >= MAX_SKILLS) return;
	let entries;
	try {
		entries = await readdir(directory, { withFileTypes: true });
	} catch {
		return;
	}
	const marker = entries.find((entry) => entry.isFile() && entry.name === "SKILL.md");
	if (marker !== undefined) {
		found.push(resolve(directory, "SKILL.md"));
		return; // A skill directory is not searched for nested skills.
	}
	for (const entry of entries) {
		if (!entry.isDirectory()) continue;
		if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
		await walk(resolve(directory, entry.name), depth + 1, found);
	}
}

export async function loadSkills(directories: string[]): Promise<Skill[]> {
	const skills: Skill[] = [];
	const seen = new Set<string>();
	for (const directory of directories) {
		const files: string[] = [];
		await walk(directory, 0, files);
		for (const file of files) {
			if (skills.length >= MAX_SKILLS) break;
			let text: string;
			try {
				text = await readFile(file, "utf-8");
			} catch {
				continue;
			}
			const skill = skillFrom(dirname(file), file, text);
			if (skill === undefined || seen.has(skill.name)) continue;
			seen.add(skill.name);
			skills.push(skill);
		}
	}
	return skills.sort((left, right) => left.name.localeCompare(right.name));
}

function renderSkills(skills: readonly Skill[]): string | undefined {
	if (skills.length === 0) return undefined;
	const lines = [
		"以下技能按需加载。先调用 read_skill({name}) 读取完整 SKILL.md，再按其中的步骤执行；不要凭名字猜流程。",
	];
	for (const skill of skills) {
		const line = `- ${skill.name}: ${skill.description}`;
		const next = lines.concat(line).join("\n");
		if (next.length > MAX_SECTION_CHARS) break;
		lines.push(line);
	}
	return lines.join("\n");
}

function readTool(skills: readonly Skill[]) {
	const byName = new Map(skills.map((skill) => [skill.name, skill]));
	return defineTool({
		name: "read_skill",
		description:
			"读取一个技能的完整 SKILL.md，或该技能目录内的参考文件。先用技能列表里的 name；path 省略时读 SKILL.md。只能读技能目录内的文件。",
		parameters: Type.Object({
			name: Type.String({ minLength: 1 }),
			path: Type.Optional(Type.String()),
		}),
		replay: "safe",
		execute: async (args, _api, _context: Context) => {
			const skill = byName.get(args.name);
			if (skill === undefined) {
				return {
					isError: true,
					output: [{
						type: "text",
						text: `没有名为 ${args.name} 的技能。可用技能：${[...byName.keys()].join("、") || "无"}。`,
					}],
				};
			}
			const target = args.path === undefined || args.path === ""
				? skill.file
				: await insideSkill(skill.directory, args.path);
			if (target === undefined) {
				return {
					isError: true,
					output: [{ type: "text", text: `路径逃出了技能目录 ${skill.directory}，已拒绝。` }],
				};
			}
			let text: string;
			try {
				text = await readFile(target, "utf-8");
			} catch (error) {
				return {
					isError: true,
					output: [{
						type: "text",
						text: `读取 ${args.path ?? "SKILL.md"} 失败：${String(error).slice(0, 200)}`,
					}],
				};
			}
			if (text.includes("\u0000")) {
				return { isError: true, output: [{ type: "text", text: "这是二进制文件，read_skill 只读文本。" }] };
			}
			if (text.length > MAX_SKILL_FILE_CHARS) {
				text = `${text.slice(0, MAX_SKILL_FILE_CHARS)}\n\n[已截断，原文件更长]`;
			}
			return { output: [{ type: "text", text }] };
		},
	});
}

async function insideSkill(root: string, candidate: string): Promise<string | undefined> {
	let base: string;
	try {
		base = await realpath(root);
	} catch {
		return undefined;
	}
	const target = resolve(base, candidate);
	if (target !== base && !target.startsWith(base + sep)) return undefined;
	// A symlink inside the skill directory can still point outside it.
	try {
		const real = await realpath(target);
		if (real !== base && !real.startsWith(base + sep)) return undefined;
		return real;
	} catch {
		return target; // Missing file: let the reader report "not found".
	}
}

export function skillsExtension(skills: readonly Skill[]): Extension | undefined {
	if (skills.length === 0) return undefined;
	return defineExtension({
		name: "xiaoxi-skills",
		sections: [section("skills", () => renderSkills(skills))],
		tools: [readTool(skills) as ToolRegistration],
	});
}

export const __test = { frontmatter, skillFrom, insideSkill, renderSkills };
