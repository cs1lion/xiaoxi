// read_file: read inside the per-run sandbox, refuse escapes, no workspace means
// no reads.
//
//   node --experimental-strip-types test-read-file.ts

import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readFile } from "./src/read_file.ts";

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

const root = mkdtempSync(join(tmpdir(), "xiaoxi-read-"));
const textOf = (result: { output?: { type: string; text?: string }[] }): string =>
	(result.output ?? []).map((item) => item.text ?? "").join("\n");
const api = (workspace: string) => ({
	conversationId: "c1",
	snapshot: async () => ({ workspace }),
	output: () => undefined,
});

try {
	writeFileSync(join(root, "note.txt"), "hello sandbox", "utf-8");
	writeFileSync(join(root, "big.txt"), "长".repeat(20_000), "utf-8");

	console.log("[durable: read_file]");
	const read = await readFile.execute({ path: "note.txt" }, api(root) as never, BACKGROUND_CONTEXT);
	check("读得到沙箱内文件", textOf(read).includes("hello sandbox"), textOf(read));
	check("成功结果不是错误", read.isError !== true, JSON.stringify(read));

	const big = await readFile.execute({ path: "big.txt" }, api(root) as never, BACKGROUND_CONTEXT);
	const bigText = textOf(big);
	check("大文件截到 12000 字符", bigText.includes("已截断") && bigText.length < 12_500,
		String(bigText.length));

	const escaped = await readFile.execute({ path: "../outside.txt" }, api(root) as never, BACKGROUND_CONTEXT);
	check("拒绝 ../ 逃逸", escaped.isError === true, JSON.stringify(escaped));

	const missing = await readFile.execute({ path: "nope.txt" }, api(root) as never, BACKGROUND_CONTEXT);
	check("不存在的文件报错", missing.isError === true && textOf(missing).includes("没有这个文件"),
		textOf(missing));

	const bare = await readFile.execute({ path: "note.txt" }, api("") as never, BACKGROUND_CONTEXT);
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
