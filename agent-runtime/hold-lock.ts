// Acquire the sidecar's directory lock for an external maintenance step.
// Prints LOCKED and waits for stdin to close; if the sidecar is alive the lock
// is already held and this exits non-zero without touching anything.
//
//   node --experimental-strip-types hold-lock.ts <directory>

import lockfile from "proper-lockfile";

const directory = process.argv[2];
if (directory === undefined || directory === "") {
	console.error("usage: hold-lock.ts <directory>");
	process.exit(2);
}
try {
	const release = await lockfile.lock(directory, {
		realpath: false,
		stale: 10_000,
		update: 5_000,
		retries: 0,
	});
	console.log("LOCKED");
	const finish = async (): Promise<void> => {
		await release();
		process.exit(0);
	};
	process.stdin.resume();
	process.stdin.on("end", () => void finish());
	process.on("SIGTERM", () => void finish());
	process.on("SIGINT", () => void finish());
} catch (error) {
	console.log(`LOCKED_FAILED ${error instanceof Error ? error.message : String(error)}`);
	process.exit(2);
}
