import type { Server } from "node:http";
import { createRpcServer } from "./rpc.ts";
import { openAgentRuntime } from "./runtime.ts";

const directory = process.env.XIAOXI_RUNTIME_DIR ?? "data/durable";
const token = process.env.XIAOXI_RUNTIME_TOKEN || undefined;

const runtime = await openAgentRuntime(directory);
let server: Server | undefined;
let closing = false;

async function shutdown(): Promise<void> {
	if (closing) return;
	closing = true;
	server?.close();
	await runtime.close();
	process.exit(0);
}

server = createRpcServer({ runtime, token, onShutdown: shutdown });
server.listen(0, "127.0.0.1", () => {
	const address = server?.address();
	if (address !== null && address !== undefined && typeof address === "object") {
		// The supervisor reads this line and nothing else from stdout.
		console.log(`PORT ${address.port}`);
	}
});

process.on("SIGTERM", () => void shutdown());
process.on("SIGINT", () => void shutdown());
