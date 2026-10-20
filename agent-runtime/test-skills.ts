// Skills: advertise names/descriptions, read the body on demand, never escape
// the skill directory.
//
//   node --experimental-strip-types test-skills.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadSkills, skillsExtension, __test } from "./src/skills.ts";

let passed = 0;
const failed: string[] = [];
const check = (name: string, ok: boolean, detail = ""): void => {
	if (ok) {
		passed += 1;
		console.log(`  ok   ${name}`);
	} else {
		failed.push(`${name} ${detail}`);
		console.log(`  FAIL ${name} ${detail}`);
	}
};

const root = mkdtempSync(join(tmpdir(), "xiaoxi-skills-"));
function skill(path: string, content: string): void {
	mkdirSync(join(root, path, ".."), { recursive: true });
	writeFileSync(join(root, path), content, "utf-8");
}

try {
	skill("alpha/SKILL.md", `---
name: alpha
description: 把长文整理成短标题。用于标题、摘要、改写。
---
# alpha body
STEP-ONE`);
	skill("alpha/references/notes.md", "REFERENCE-CONTENT");
	skill("beta/SKILL.md", `---
name: beta
description: 整理表格。
---
# beta body`);
	skill("nested/gamma/SKILL.md", `---
description: 没有显式 name，用目录名。
---
# gamma body`);
	skill("hidden/SKILL.md", `---
name: hidden
description: 不应出现在模型列表里。
disable-model-invocation: true
---
# hidden body`);
	skill("bad/SKILL.md", `---
name: bad
---
没有 description`);

	console.log("[durable: skills]");
	const skills = await loadSkills([root]);
	check("发现三个可调用技能", skills.map((item) => item.name).join(",") === "alpha,beta,gamma",
		JSON.stringify(skills.map((item) => item.name)));
	check("没有 description 的技能被拒绝", !skills.some((item) => item.name === "bad"));
	check("disable-model-invocation 的技能不进列表", !skills.some((item) => item.name === "hidden"));

	const extension = skillsExtension(skills);
	check("有技能时安装扩展", extension !== undefined);
	if (extension === undefined) throw new Error("missing extension");
	const section = await extension.sections?.[0]?.render({} as never, BACKGROUND_CONTEXT);
	check("section 广告名称与描述", section?.includes("alpha") === true && section?.includes("短标题") === true,
		String(section));
	check("section 不泄露正文", section?.includes("STEP-ONE") !== true, String(section));

	const read = extension.tools?.[0];
	if (read === undefined) throw new Error("missing read_skill");
	const textOf = (result: { output?: { type: string; text?: string }[] }): string =>
		(result.output ?? []).map((item) => item.text ?? "").join("\n");

	const body = await read.execute({ name: "alpha" }, {} as never, BACKGROUND_CONTEXT);
	check("read_skill 读 SKILL.md 正文", textOf(body).includes("STEP-ONE"), textOf(body));
	const reference = await read.execute({ name: "alpha", path: "references/notes.md" },
		{} as never, BACKGROUND_CONTEXT);
	check("read_skill 读技能内参考文件", textOf(reference).includes("REFERENCE-CONTENT"),
		textOf(reference));
	const escaped = await read.execute({ name: "alpha", path: "../beta/SKILL.md" },
		{} as never, BACKGROUND_CONTEXT);
	check("read_skill 拒绝路径逃逸", escaped.isError === true, JSON.stringify(escaped));
	const unknown = await read.execute({ name: "nope" }, {} as never, BACKGROUND_CONTEXT);
	check("read_skill 对未知技能报错", unknown.isError === true && textOf(unknown).includes("alpha"),
		textOf(unknown));

	const parsed = __test.frontmatter(`---
name: x
description: "带引号的描述"
---
body`);
	check("frontmatter 解析引号值", parsed.data.description === "带引号的描述", JSON.stringify(parsed.data));
} finally {
	rmSync(root, { recursive: true, force: true });
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
