// A hand-in that points at a file which was never written is exactly what the
// project's `no_unwired_dry_run` rule exists to catch, and a model copying the
// path back into its own JSON tends to escape the separators twice. So the
// delivery card must be canonicalized against the sandbox and a missing file must
// refuse the hand-in, not be recorded as delivered.
//
//   node --experimental-strip-types test-handin.ts

import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { canonicalRefs } = await import("./src/worker.ts");

const sandbox = mkdtempSync(join(tmpdir(), "xiaoxi-handin-"));
mkdirSync(join(sandbox, "out"), { recursive: true });
const real = join(sandbox, "out", "titles.md");
writeFileSync(real, "一、深夜的代码只属于我一个人\n", "utf-8");

// The tool only needs `snapshot` to learn the workspace.
const api = { snapshot: async () => ({ workspace: sandbox }) };

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

console.log("[hand-in: canonical refs]");
const good = await canonicalRefs(api as never, undefined as never, [
	{ kind: "file", ref: real },
	{ kind: "note", ref: "note:n_1" },
]);
check("真实文件的 ref 原样通过", good.refs[0]?.ref === real && good.missing.length === 0,
	JSON.stringify(good));
check("非 file 型不动", good.refs[1]?.ref === "note:n_1", JSON.stringify(good.refs[1]));

const missing = await canonicalRefs(api as never, undefined as never, [
	{ kind: "file", ref: join(sandbox, "out", "never-written.md") },
]);
check("没写过的文件被列为缺失", missing.missing.length === 1, JSON.stringify(missing));
check("缺失的 ref 不被改写成看似有效", missing.refs[0]?.ref === join(sandbox, "out", "never-written.md"));

const escape = await canonicalRefs(api as never, undefined as never, [
	{ kind: "file", ref: join(sandbox, "..", "escaped.md") },
]);
check("沙箱外的路径算缺失，不当成有效成果", escape.missing.length === 1, JSON.stringify(escape));

// The Windows-specific mangling: the model escapes the separators a second time.
const doubled = await canonicalRefs(api as never, undefined as never, [
	{ kind: "file", ref: real.replace(/\\/g, "\\\\") },
]);
check("双反斜杠的 ref 被还原成真路径",
	doubled.missing.length === 0 && doubled.refs[0]?.ref === real, JSON.stringify(doubled));

const noWorkspace = await canonicalRefs(
	{ snapshot: async () => ({ workspace: "" }) } as never, undefined as never,
	[{ kind: "file", ref: real }],
);
check("没有工作目录时不敢当它有效", noWorkspace.missing.length === 1, JSON.stringify(noWorkspace));

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
