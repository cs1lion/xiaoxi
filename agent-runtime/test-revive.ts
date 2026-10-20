// A hard-killed sidecar must be reopenable. The lock is the whole point of the
// single-writer rule, and a hard kill always leaves it behind, so this is the
// case the recovery path actually meets.
//
//   node --experimental-strip-types test-revive.ts
//
// What it proves: after the process is killed without a chance to clean up, a
// *new* process opens the same directory and gets the lock. That only holds
// while the retry budget outlasts the staleness window. It used to be
// 10 × 500ms = 5s against stale = 10s, so every hard-kill restart failed with
// ELOCKED — the supervisor gave up with "restart_failed" and the recovery path
// never ran at all. Measured on this machine: the sidecar never came back after
// `taskkill /F`.
//
// The kill has to be a real one. `process.exit()` runs proper-lockfile's
// signal-exit handler, which removes the lock, so an in-process exit proves
// nothing about a crash. On Windows that means `taskkill /F /T /PID`; elsewhere
// SIGKILL.
//
// The parent runs two children: one that gets killed, one that reopens.

import { spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { openAgentRuntime } from "./src/runtime.ts";

const self = fileURLToPath(import.meta.url);
const [mode, directory] = process.argv.slice(2);

function hardKill(pid: number): boolean {
	if (process.platform === "win32") {
		const killer = spawnSync("taskkill", ["/F", "/T", "/PID", String(pid)], {
			encoding: "utf8",
		});
		return killer.status === 0;
	}
	try {
		process.kill(pid, "SIGKILL");
		return true;
	} catch {
		return false;
	}
}

if (mode === "hold" || mode === "revive") {
	const runtime = await openAgentRuntime(directory);
	if (mode === "hold") {
		// Hold the lock and wait to be killed. The parent kills us; nothing here
		// releases the lock, which is exactly the state a crash leaves behind.
		console.log("HOLDING");
		await new Promise(() => setInterval(() => {}, 60_000));
	}
	// Reopening and creating a run proves the lock was really taken over, not
	// just acquired over a stale file that the library then rejected.
	const started = Date.now();
	await runtime.createRun({ commission_id: "c_revive", agent_id: "w1", packet: {} });
	console.log(`REOPENED ${Date.now() - started}`);
	await runtime.close();
} else {
	const directoryPath = mkdtempSync(join(tmpdir(), "xiaoxi-revive-"));
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

	console.log("[revive: reopen after a hard kill]");
	const child = (await import("node:child_process")).spawn(
		process.execPath,
		["--experimental-strip-types", self, "hold", directoryPath],
		{ stdio: ["ignore", "pipe", "pipe"], detached: process.platform !== "win32" },
	);
	const holding = await new Promise<boolean>((resolve) => {
		const timer = setTimeout(() => resolve(false), 30_000);
		child.stdout?.on("data", (chunk: Buffer) => {
			if (chunk.toString().includes("HOLDING")) {
				clearTimeout(timer);
				resolve(true);
			}
		});
	});
	check("子进程拿到了锁并在持有中", holding && child.pid !== undefined);
	const killed = child.pid !== undefined && hardKill(child.pid);
	check("硬杀成功（锁被留下，没机会清理）", killed);
	await new Promise((resolve) => setTimeout(resolve, 500));

	const revived = spawnSync(
		process.execPath,
		["--experimental-strip-types", self, "revive", directoryPath],
		{ encoding: "utf8" },
	);
	const reopened = /REOPENED (\d+)/.exec(`${revived.stdout ?? ""}${revived.stderr ?? ""}`);
	check("重开时拿到了锁并建了任务", reopened !== null, (revived.stderr ?? "").slice(0, 300));
	if (reopened !== null) {
		// The stale window is 10s, so a reopen that lands under it proves the
		// retry budget waited the lock out instead of failing fast with ELOCKED.
		check("重开没有在重试预算内放弃", Number(reopened[1]) < 30_000, `${reopened[1]}ms`);
	}

	console.log();
	if (failed.length > 0) {
		console.log(`=== ${passed} passed, ${failed.length} failed ===`);
		for (const item of failed) console.log(`  - ${item}`);
		process.exit(1);
	}
	console.log(`=== ${passed} passed, 0 failed ===`);
}
