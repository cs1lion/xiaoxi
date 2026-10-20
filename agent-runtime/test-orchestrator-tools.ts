// C2 sidecar tools: lease/session come from RunMeta, not model arguments;
// worker profile cannot call them.
//
//   node --experimental-strip-types test-orchestrator-tools.ts

import { createServer } from "node:http";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
	answerBlocked, dispatchTask, orchestratorTools, proposeAccept, proposeReject,
	reviewDelivery, taskEvents, taskStatus,
} from "./src/orchestrator_tools.ts";

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

const requests: { url?: string; body: unknown }[] = [];
const server = createServer(async (req, res) => {
	const parts: Buffer[] = [];
	for await (const part of req) parts.push(part as Buffer);
	const raw = Buffer.concat(parts).toString("utf8");
	requests.push({ url: req.url, body: raw === "" ? {} : JSON.parse(raw) });
	res.writeHead(200, { "content-type": "application/json" });
	if (req.url === "/internal/durable/review") {
		res.end(JSON.stringify({ commission_id: "c_1", can_accept: true,
			recommendation: "accept", findings: [] }));
		return;
	}
	if (req.url === "/internal/durable/propose") {
		res.end(JSON.stringify({ proposal_id: "p_1", action: "accept" }));
		return;
	}
	if (req.url?.startsWith("/internal/durable/confirmations")) {
		res.end(JSON.stringify({ items: [] }));
		return;
	}
	if (req.url === "/internal/durable/dispatch") {
		const value = (raw === "" ? {} : JSON.parse(raw)) as Record<string, unknown>;
		if (value.title === "confirm me" && !value.confirmation_id) {
			res.end(JSON.stringify({ confirmation_required: true, confirmation_id: "conf-1" }));
			return;
		}
		if (value.confirmation_id === "conf-1") {
			res.end(JSON.stringify({ commission_id: "c_confirm", status: "running", mode: "confirm" }));
			return;
		}
	}
	res.end(JSON.stringify({ commission_id: "c_1", status: "running", mode: "auto" }));
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (address === null || typeof address !== "object") throw new Error("no port");
process.env.XIAOXI_HOST_URL = `http://127.0.0.1:${address.port}`;
process.env.XIAOXI_HOST_TOKEN = "test-token";

const api = (profile: string, taskId = 42, callId = "call-7") => ({
	conversationId: "conv-1",
	taskId,
	callId,
	snapshot: async () => ({
		profile, lease_id: "lease-1", session_id: "sess-1",
	}),
});
const textOf = (result: { output?: { type: string; text?: string }[] }): string =>
	(result.output ?? []).map((item) => item.text ?? "").join("\n");

try {
	console.log("[durable: orchestrator tools]");
	const dispatched = await dispatchTask.execute({
		title: "整理标题", brief: "把七月选题理成三版标题", agent_id: "scribe",
		// A forged lease must be ignored; the tool has no lease parameter.
		lease_id: "forged", max_concurrency: 99,
	} as never, api("orchestrator") as never, BACKGROUND_CONTEXT);
	check("dispatch_task 返回宿主结果", textOf(dispatched).includes("c_1"), textOf(dispatched));
	const body = requests[0]?.body as Record<string, unknown>;
	check("lease 来自 RunMeta", body.lease_id === "lease-1", JSON.stringify(body));
	check("session 来自 RunMeta", body.session_id === "sess-1", JSON.stringify(body));
	check("幂等键由 task/call 生成", body.idempotency_key === "orchestrator:42:call-7",
		JSON.stringify(body));
	check("模型不能伪造额度", !("max_concurrency" in body), JSON.stringify(body));
	const confirmed = await dispatchTask.execute({ title: "confirm me", brief: "确认后自动重试" },
		api("orchestrator", 42, "call-14") as never, BACKGROUND_CONTEXT);
	check("confirm 由工具自动重试", textOf(confirmed).includes("c_confirm"), textOf(confirmed));

	const status = await taskStatus.execute({ commission_id: "c_1" },
		api("orchestrator", 42, "call-8") as never, BACKGROUND_CONTEXT);
	check("task_status 走宿主", textOf(status).includes("running"), textOf(status));
	const events = await taskEvents.execute({ commission_id: "c_1", limit: 10 },
		api("orchestrator", 42, "call-9") as never, BACKGROUND_CONTEXT);
	check("task_events 走宿主", textOf(events).includes("c_1"), textOf(events));
	const answered = await answerBlocked.execute({
		commission_id: "c_1", blocked_question_id: "q_1", answer: "放到 out 目录",
	}, api("orchestrator", 42, "call-10") as never, BACKGROUND_CONTEXT);
	check("answer_blocked 走宿主", textOf(answered).includes("auto"), textOf(answered));
	const reviewed = await reviewDelivery.execute({ commission_id: "c_1" },
		api("orchestrator", 42, "call-11") as never, BACKGROUND_CONTEXT);
	check("review_delivery 只读审阅", textOf(reviewed).includes("can_accept"), textOf(reviewed));
	const proposed = await proposeAccept.execute({ commission_id: "c_1" },
		api("orchestrator", 42, "call-12") as never, BACKGROUND_CONTEXT);
	check("propose_accept 返回提案号", textOf(proposed).includes("p_1"), textOf(proposed));
	const rejectProposal = await proposeReject.execute({
		commission_id: "c_1", suggested_feedback: "标题太长",
	}, api("orchestrator", 42, "call-13") as never, BACKGROUND_CONTEXT);
	check("propose_reject 也走只读提案", textOf(rejectProposal).includes("p_1"), textOf(rejectProposal));
	check("所有请求路径正确", requests.every((item) =>
		item.url?.startsWith("/internal/durable/")), JSON.stringify(requests.map((item) => item.url)));

	const denied = await dispatchTask.execute({ title: "x", brief: "y" },
		api("worker") as never, BACKGROUND_CONTEXT);
	check("worker profile 不能派单", denied.isError === true &&
		textOf(denied).includes("orchestrator_profile_required"), textOf(denied));
	check("编排者没有 commit 工具",
		!orchestratorTools.some((tool) => tool.name === "accept_delivery" ||
			tool.name === "reject_delivery"), JSON.stringify(orchestratorTools.map((tool) => tool.name)));
} finally {
	server.close();
}

console.log();
if (failed.length > 0) {
	console.log(`=== ${passed} passed, ${failed.length} failed ===`);
	for (const item of failed) console.log(`  - ${item}`);
	process.exit(1);
}
console.log(`=== ${passed} passed, 0 failed ===`);
