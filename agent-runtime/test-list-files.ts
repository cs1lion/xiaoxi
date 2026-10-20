// list_files: bounded, sandboxed, read-only listing.
//
//   node --experimental-strip-types test-list-files.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { listFiles } from "./src/list_files.ts";

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

const root = mkdtempSync(join(tmpdir(), "xiaoxi-list-"));
const textOf = (result: { output?: { type: string; text?: string }[] }): string =>
	(result.output ?? []).map((item) => item.text ?? "").join("\n");
const api = (workspace: string) => ({
	conversationId: "c1",
	snapshot: async () => ({ workspace }),
});

try {
	writeFileSync(join(root, "note.txt"), "hello", "utf-8");
	mkdirSync(join(root, "sub", "deep", "more"), { recursive: true });
	writeFileSync(join(root, "sub", "deep.txt"), "deep", "utf-8");
	writeFileSync(join(root, "sub", "deep", "deeper.txt"), "deeper", "utf-8");
	writeFileSync(join(root, "sub", "deep", "more", "most.txt"), "most", "utf-8");

	console.log("[durable: list_files]");
	const listed = await listFiles.execute({}, api(root) as never, BACKGROUND_CONTEXT);
	check("列出根文件", textOf(listed).includes("note.txt"), textOf(listed));
	check("列出子目录与深层文件", textOf(listed).includes("sub") && textOf(listed).includes("sub/deep.txt"),
		textOf(listed));
	check("默认深度不越界", !textOf(listed).includes("most.txt"), textOf(listed));
	const deep = await listFiles.execute({ depth: 4 }, api(root) as never, BACKGROUND_CONTEXT);
	check("depth=4 能列更深一层", textOf(deep).includes("most.txt"), textOf(deep));

	const shallow = await listFiles.execute({ depth: 0 }, api(root) as never, BACKGROUND_CONTEXT);
	check("depth=0 只列第一层", textOf(shallow).includes("note.txt") && !textOf(shallow).includes("deep.txt"),
		textOf(shallow));

	const escaped = await listFiles.execute({ path: "../" }, api(root) as never, BACKGROUND_CONTEXT);
	check("拒绝路径逃逸", escaped.isError === true, JSON.stringify(escaped));

	const bare = await listFiles.execute({}, api("") as never, BACKGROUND_CONTEXT);
	check("没有工作目录时拒绝", bare.isError === true, JSON.stringify(bare));
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
