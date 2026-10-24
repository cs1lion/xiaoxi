/* 契约 v2 · §25 前端消费层单测：node web/events-v2.test.mjs
 *
 * 全部用模拟 WS 帧驱动，不起服务、不连 socket——这正是把信封归约与
 * 事件到 UI 状态的归约做成纯逻辑模块的原因（浏览器 e2e 是另一条缝）。
 */
import test from "node:test";
import assert from "node:assert/strict";

import { isV2Frame, createEventStream, createOpsState, createEventHub,
  buildCommand, newRequestId, V2_EVENT_TYPES, V2_COMMAND_TYPES } from "./events-v2.js";

let seqOf = 0;
/** 造一条 §25.1 信封。id/seq 默认自增，模拟服务端连接级单调递增。 */
function frame(type, payload, { id, seq, ts, task_id = "t1", step_id, v = 2 } = {}) {
  seqOf += 1;
  const out = { v, id: id ?? `e${seqOf}`, seq: seq ?? seqOf, ts: ts ?? 1000 + seqOf, type, payload };
  if (task_id !== undefined) out.task_id = task_id;
  if (step_id !== undefined) out.step_id = step_id;
  return out;
}

/* ---------- 一、信封形状（§25.1） ---------- */

test("v=2 且九类事件名之一的帧才是 v2 帧", () => {
  assert.equal(isV2Frame(frame("TASK_STATE_CHANGE", {})), true);
  assert.equal(isV2Frame({ ...frame("TASK_STATE_CHANGE", {}), v: 1 }), false);
  assert.equal(isV2Frame(frame("TASK_SNAPSHOT", {})), false); // §25.4 明确禁止的类型
  assert.equal(isV2Frame({ type: "commission_update" }), false); // v1 事件
  assert.equal(isV2Frame(null), false);
});

test("缺 id / 非整数 seq / 假 ts 的帧整条忽略", () => {
  const stream = createEventStream();
  assert.equal(stream.accept({ v: 2, type: "AGENT_PRESENCE", seq: 1, ts: 1, payload: {} }).length, 0);
  assert.equal(stream.accept({ v: 2, id: "x", type: "AGENT_PRESENCE", ts: 1, payload: {} }).length, 0);
  assert.equal(stream.accept({ v: 2, id: "x", type: "AGENT_PRESENCE", seq: 1.5, ts: 1, payload: {} }).length, 0);
  assert.equal(stream.accept({ v: 2, id: "x", type: "AGENT_PRESENCE", seq: 1, ts: NaN, payload: {} }).length, 0);
  assert.equal(stream.lastSeq, null);
});

/* ---------- 二、id 去重（§25.5 至少一次投递） ---------- */

test("同 id 重复帧只交付一次，不产生重复 UI", () => {
  const stream = createEventStream();
  const f = frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "SPEC_LOCKED", attempt: 0, risk: "low" });
  assert.equal(stream.accept(f).length, 1);
  assert.equal(stream.accept({ ...f }).length, 0); // 网络重发同一帧
  assert.equal(stream.accept(f).length, 0);
});

test("去重表有界：超上限丢最老 id，新 id 照常工作", () => {
  const stream = createEventStream({ maxSeenIds: 2 });
  stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "a", seq: 1 }));
  stream.accept(frame("AGENT_PRESENCE", { state: "thinking" }, { id: "b", seq: 2 }));
  stream.accept(frame("AGENT_PRESENCE", { state: "speaking" }, { id: "c", seq: 3 }));
  // b 还在表里：重复即丢。a 已被逐出：同内容重投按新帧交付（seq 连得上）。
  assert.equal(stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "b", seq: 4 })).length, 0);
  assert.equal(stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "a", seq: 4 })).length, 1);
});

/* ---------- 三、seq 保序（§25.1 连接级单调） ---------- */

test("乱序帧按 seq 补齐后依序交付", () => {
  const stream = createEventStream();
  stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "a", seq: 1 }));
  assert.deepEqual(
    stream.accept(frame("AGENT_PRESENCE", { state: "speaking" }, { id: "c", seq: 3 })),
    [], // 缺 2，先压着
  );
  const delivered = stream.accept(frame("AGENT_PRESENCE", { state: "thinking" }, { id: "b", seq: 2 }));
  assert.deepEqual(delivered.map((item) => item.frame.id), ["b", "c"]); // 2 到了，2、3 连排释放
  assert.equal(stream.lastSeq, 3);
});

test("缺口等不来（at-most-once 被丢）时窗口溢出按序跳过，画面不卡死", () => {
  const stream = createEventStream({ reorderWindow: 8 });
  stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "s1", seq: 1 }));
  for (let seq = 3; seq <= 10; seq += 1) { // 2 号位永远缺席；8 帧进窗未超窗
    assert.deepEqual(stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: `s${seq}`, seq })),
      [], `seq ${seq} 应压窗等待`);
  }
  // 第 9 个等待帧超窗：缺口 2 按 at-most-once 语义视为已被丢，
  // 从最小 seq(3) 起连排释放，画面不卡在过去。
  const delivered = stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "s11", seq: 11 }));
  assert.deepEqual(delivered.map((item) => item.frame.id),
    ["s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11"]);
  assert.equal(stream.lastSeq, 11);
  assert.equal(stream.pendingCount, 0);
  // 缺口跳过后恢复常规：连续 seq 直接交付。
  assert.deepEqual(
    stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "s12", seq: 12 }))
      .map((item) => item.frame.id),
    ["s12"]);
});

test("迟到帧（seq 已过）标 late 照常交付，交由 UI 状态层拒绝回退", () => {
  const stream = createEventStream();
  stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "a", seq: 5 }));
  const delivered = stream.accept(frame("AGENT_PRESENCE", { state: "speaking" }, { id: "old", seq: 3 }));
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].late, true);
  assert.equal(stream.lastSeq, 5); // 序列号不被迟到帧拉回
});

test("resetConnection 重算 seq 起点但保留 id 去重（断线补齐会重放旧帧）", () => {
  const stream = createEventStream();
  stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "a", seq: 9 }));
  stream.resetConnection();
  assert.equal(stream.lastSeq, null);
  assert.equal(stream.accept(frame("AGENT_PRESENCE", { state: "idle" }, { id: "a", seq: 1 })).length, 0); // 重放被去重
  assert.equal(stream.accept(frame("AGENT_PRESENCE", { state: "thinking" }, { id: "b", seq: 2 })).length, 1);
});

/* ---------- 四、九类事件各归约出可见状态（§25.2） ---------- */

function run(frames) {
  const ops = createOpsState();
  for (const item of frames) ops.apply(item);
  return ops.snapshot();
}

test("TASK_STATE_CHANGE / STEP_STATE_CHANGE 归约出任务卡与步骤", () => {
  const snap = run([
    frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "SPEC_LOCKED", attempt: 0, risk: "medium" }, { task_id: "t1", id: "a1", seq: 1 }),
    frame("STEP_STATE_CHANGE", { from: "CREATED", to: "RUNNING", agent: "dsh", station: "工位A", attempt: 1 }, { task_id: "t1", step_id: "s1", id: "a2", seq: 2 }),
  ]);
  assert.equal(snap.tasks.length, 1);
  assert.equal(snap.tasks[0].status, "SPEC_LOCKED");
  assert.equal(snap.tasks[0].risk, "medium");
  assert.equal(snap.tasks[0].steps[0].status, "RUNNING");
  assert.equal(snap.tasks[0].steps[0].agent, "dsh");
  assert.equal(snap.tasks[0].steps[0].station, "工位A");
});

test("CONTRACT_PROPOSED 归约出契约卡；同 contract_id 新版本覆盖旧版本", () => {
  const snap = run([
    frame("CONTRACT_PROPOSED", { contract_id: "c1", version: 1, goal: "整理笔记", artifact: { kind: "file", format: "md" }, risk: "high", highlighted_slots: ["artifact"], criteria: [{ id: "k1", kind: "format", passed: false }], budget: { minutes: 10 } }, { task_id: "t1", id: "b1", seq: 1 }),
    frame("CONTRACT_PROPOSED", { contract_id: "c1", version: 2, goal: "整理笔记 v2", artifact: { kind: "file", format: "md" }, risk: "high", highlighted_slots: ["artifact"], criteria: [], budget: { minutes: 10 } }, { task_id: "t1", id: "b2", seq: 2 }),
  ]);
  assert.equal(snap.contracts.length, 1); // 同 id 覆盖，不出现两张卡
  assert.equal(snap.contracts[0].version, 2);
  assert.equal(snap.contracts[0].highlightedSlots.length, 1);
});

test("ASK_USER 归约出提问卡，markAnswered 只答最新一条未答的", () => {
  const ops = createOpsState();
  ops.apply(frame("ASK_USER", { question: "要哪种格式？", options: ["md", "csv"], missing: ["artifact"] }, { task_id: "t1", id: "c1", seq: 1 }));
  ops.apply(frame("ASK_USER", { question: "预算给多少？", options: [], missing: ["budget"] }, { task_id: "t1", id: "c2", seq: 2 }));
  let snap = ops.snapshot();
  assert.equal(snap.asks.length, 2);
  assert.equal(snap.asks.every((ask) => !ask.answered), true);
  ops.markAnswered("t1");
  snap = ops.snapshot();
  assert.equal(snap.asks[1].answered, true); // 最新一条
  assert.equal(snap.asks[0].answered, false);
});

test("TOOL_EXECUTION 归约出工具流水（steps_done/steps_total，无 progress 假条）", () => {
  const snap = run([
    frame("TOOL_EXECUTION", { tool: "sandbox_cli", status: "running", steps_done: 2, steps_total: 5 }, { task_id: "t1", id: "d1", seq: 1 }),
  ]);
  assert.equal(snap.tools[0].taskId, "t1");
  assert.equal(snap.tools[0].rows[0].stepsDone, 2);
  assert.equal(snap.tools[0].rows[0].stepsTotal, 5);
  assert.equal("progress" in snap.tools[0].rows[0], false); // §25.4 禁止的字段
});

test("VERIFICATION_REPORT / DELIVERY_EFFECT / MEMORY_UPDATE / AGENT_PRESENCE 各自归约", () => {
  const snap = run([
    frame("VERIFICATION_REPORT", { passed: true, findings: [], criteria: [{ id: "k1", kind: "format", passed: true }] }, { task_id: "t1", id: "e1", seq: 1 }),
    frame("DELIVERY_EFFECT", { effect_id: "fx1", type: "write_file", target_class: "local", status: "pending", idempotency_key: "k-1" }, { task_id: "t1", id: "e2", seq: 2 }),
    frame("MEMORY_UPDATE", { action: "add", memory_ref: "m/42", source: "task", confidence: 0.8, provenance: "commission t1" }, { task_id: "t1", id: "e3", seq: 3 }),
    frame("AGENT_PRESENCE", { state: "speaking" }, { task_id: undefined, id: "e4", seq: 4 }),
  ]);
  assert.equal(snap.verifications[0].passed, true);
  assert.equal(snap.verifications[0].criteria.length, 1);
  assert.equal(snap.effects[0].rows[0].status, "pending");
  assert.equal(snap.effects[0].rows[0].idempotencyKey, "k-1");
  assert.equal(snap.memory[0].memoryRef, "m/42");
  assert.equal(snap.presence.state, "speaking");
});

test("AGENT_PRESENCE 四态之外的安全忽略", () => {
  const snap = run([
    frame("AGENT_PRESENCE", { state: "dancing" }, { task_id: undefined, id: "p1", seq: 1 }),
  ]);
  assert.equal(snap.presence, null);
});

test("DELIVERY_EFFECT 同 effect_id 二次到达只更新状态，不重复占位", () => {
  const snap = run([
    frame("DELIVERY_EFFECT", { effect_id: "fx1", type: "write_file", target_class: "local", status: "pending", idempotency_key: "k" }, { task_id: "t1", id: "f1", seq: 1 }),
    frame("DELIVERY_EFFECT", { effect_id: "fx1", type: "write_file", target_class: "local", status: "done", idempotency_key: "k" }, { task_id: "t1", id: "f2", seq: 2 }),
  ]);
  assert.equal(snap.effects[0].rows.length, 1);
  assert.equal(snap.effects[0].rows[0].status, "done");
});

test("迟到帧不回退画面：旧 ts 的 TASK/STEP 状态被 ts 守卫拦下", () => {
  const snap = run([
    frame("TASK_STATE_CHANGE", { from: "EXECUTING", to: "VERIFYING", attempt: 0, risk: "low" }, { task_id: "t1", id: "g1", seq: 5, ts: 5000 }),
    frame("TASK_STATE_CHANGE", { from: "SPEC_LOCKED", to: "EXECUTING", attempt: 0, risk: "low" }, { task_id: "t1", id: "g0", seq: 4, ts: 4000 }), // 迟到
  ]);
  assert.equal(snap.tasks[0].status, "VERIFYING"); // 不被拉回 EXECUTING
});

test("同内容不同信封的 TASK 事件不触发可见变化（不产生重复 UI）", () => {
  const ops = createOpsState();
  assert.equal(ops.apply(frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "SPEC_LOCKED", attempt: 0, risk: "low" }, { task_id: "t1", id: "h1", seq: 1 })), true);
  assert.equal(ops.apply(frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "SPEC_LOCKED", attempt: 0, risk: "low" }, { task_id: "t1", id: "h2", seq: 2 })), false);
});

/* ---------- 五、五个前端命令（§25.3） ---------- */

test("五个命令都能构造，必填字段与 request_id 齐全", () => {
  const built = [
    buildCommand("CONFIRM_CONTRACT", { task_id: "t1", contract_id: "c1", version: 2, confirmed_slots: ["artifact"] }),
    buildCommand("ANSWER", { task_id: "t1", answer: "md", option_index: 0 }),
    buildCommand("CANCEL_TASK", { task_id: "t1", reason: "user_abort" }),
    buildCommand("APPROVE_SIDE_EFFECT", { task_id: "t1", effect_id: "fx1" }),
    buildCommand("RETRY_STEP", { task_id: "t1", step_id: "s1" }),
  ];
  for (const item of built) {
    assert.equal(item.ok, true);
    assert.equal(typeof item.command.request_id, "string");
    assert.ok(item.command.request_id.length > 0);
  }
  assert.deepEqual(built[0].command.confirmed_slots, ["artifact"]);
  assert.equal(built[1].command.option_index, 0);
  assert.equal(built[2].command.reason, "user_abort");
});

test("缺必填字段的命令不构造，报缺失字段名", () => {
  assert.equal(buildCommand("CONFIRM_CONTRACT", { task_id: "t1" }).ok, false);
  assert.match(buildCommand("CONFIRM_CONTRACT", { task_id: "t1" }).error, /contract_id/);
  assert.match(buildCommand("ANSWER", { task_id: "t1" }).error, /answer/);
  assert.equal(buildCommand("CANCEL_TASK", {}).ok, false);
  assert.equal(buildCommand("APPROVE_SIDE_EFFECT", { task_id: "t1" }).ok, false);
  assert.equal(buildCommand("RETRY_STEP", { task_id: "t1" }).ok, false);
  assert.equal(buildCommand("NOT_A_COMMAND", {}).ok, false);
});

test("类型校验：confirmed_slots 非字符串数组 / answer 非字符串 / reason 越界都拒绝", () => {
  assert.equal(buildCommand("CONFIRM_CONTRACT", { task_id: "t", contract_id: "c", version: 1, confirmed_slots: "artifact" }).ok, false);
  assert.equal(buildCommand("ANSWER", { task_id: "t", answer: 42 }).ok, false);
  assert.equal(buildCommand("CANCEL_TASK", { task_id: "t", reason: "because" }).ok, false);
  // reason 不传是合法的（可选字段），option_index 只在整数时随行。
  const cmd = buildCommand("CANCEL_TASK", { task_id: "t" }).command;
  assert.equal("reason" in cmd, false);
  const answer = buildCommand("ANSWER", { task_id: "t", answer: "ok" }).command;
  assert.equal("option_index" in answer, false);
});

test("request_id 幂等键：外部传入则复用（网络重发），否则每次新生成", () => {
  assert.equal(buildCommand("CANCEL_TASK", { task_id: "t", request_id: "retry-1" }).command.request_id, "retry-1");
  const a = buildCommand("CANCEL_TASK", { task_id: "t" }).command.request_id;
  const b = buildCommand("CANCEL_TASK", { task_id: "t" }).command.request_id;
  assert.notEqual(a, b);
  assert.notEqual(newRequestId(), newRequestId());
});

/* ---------- 六、hub：app.js 喂帧 / executive.js 订阅与发命令 ---------- */

test("handleFrame 只认 v2 帧；v1 事件原样放行给旧链路", () => {
  const hub = createEventHub();
  assert.equal(hub.handleFrame({ type: "commission_update", status: "running" }), false);
  assert.equal(hub.handleFrame(frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "SPEC_LOCKED", attempt: 0, risk: "low" }, { id: "x1", seq: 1 })), true);
  assert.equal(hub.snapshot().tasks.length, 1);
});

test("未知 type（含被禁的 TASK_SNAPSHOT）整条吞掉不进 v1 链路、不进状态", () => {
  const hub = createEventHub();
  let notified = 0;
  hub.subscribe(() => { notified += 1; });
  assert.equal(hub.handleFrame(frame("TASK_SNAPSHOT", { tasks: [] }, { id: "x1", seq: 1 })), true);
  assert.equal(notified, 0);
  assert.equal(hub.snapshot().tasks.length, 0);
});

test("订阅者收到快照；重复/同内容帧不重复广播（不产生重复 UI）", () => {
  const hub = createEventHub();
  let notified = 0;
  hub.subscribe(() => { notified += 1; });
  const f = frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "SPEC_LOCKED", attempt: 0, risk: "low" }, { id: "y1", seq: 1 });
  hub.handleFrame(f);
  hub.handleFrame({ ...f }); // 重发
  hub.handleFrame(frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "SPEC_LOCKED", attempt: 0, risk: "low" }, { id: "y2", seq: 2 })); // 同内容新信封
  assert.equal(notified, 1);
});

test("sendCommand 经注入的通道发出；通道未就绪时命令照常返回（留待重发）", () => {
  const hub = createEventHub();
  const sent = [];
  hub.attachSender((payload) => { sent.push(payload); return true; });
  const result = hub.sendCommand("RETRY_STEP", { task_id: "t1", step_id: "s1" });
  assert.equal(result.sent, true);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, "RETRY_STEP");
  assert.equal(sent[0].step_id, "s1");

  hub.attachSender(() => false); // 连接断开
  const offline = hub.sendCommand("CANCEL_TASK", { task_id: "t1" });
  assert.equal(offline.ok, true);
  assert.equal(offline.sent, false);
  assert.equal(typeof offline.command.request_id, "string"); // 幂等键在手，可原样重发
});

test("ANSWER 发出后该任务最新提问标记为已答并广播", () => {
  const hub = createEventHub();
  hub.handleFrame(frame("ASK_USER", { question: "格式？", options: ["md"], missing: [] }, { task_id: "t1", id: "z1", seq: 1 }));
  hub.attachSender(() => true);
  hub.sendCommand("ANSWER", { task_id: "t1", answer: "md", option_index: 0 });
  const snap = hub.snapshot();
  assert.equal(snap.asks[0].answered, true);
});

test("常量与协议对齐：九类事件 + 五命令，无禁止项", () => {
  assert.equal(V2_EVENT_TYPES.length, 9);
  assert.equal(V2_COMMAND_TYPES.length, 5);
  assert.equal(V2_EVENT_TYPES.includes("TASK_SNAPSHOT"), false);
  assert.equal(V2_EVENT_TYPES.includes("VOICE_STATE"), false);
});

test("快照是渲染契约：全部集合都是数组（渲染层用 length/遍历，不用 Map API）", () => {
  const snap = run([
    frame("TASK_STATE_CHANGE", { from: "PROPOSED", to: "EXECUTING", attempt: 0, risk: "low" }, { task_id: "t1", id: "i1", seq: 1 }),
    frame("STEP_STATE_CHANGE", { from: "CREATED", to: "RUNNING", agent: "dsh", attempt: 0 }, { task_id: "t1", step_id: "s1", id: "i2", seq: 2 }),
    frame("TOOL_EXECUTION", { tool: "x", status: "ok" }, { task_id: "t1", id: "i3", seq: 3 }),
    frame("DELIVERY_EFFECT", { effect_id: "f", type: "t", target_class: "c", status: "done", idempotency_key: "k" }, { task_id: "t1", id: "i4", seq: 4 }),
  ]);
  for (const value of [snap.tasks, snap.contracts, snap.asks, snap.tools, snap.verifications, snap.effects, snap.memory]) {
    assert.equal(Array.isArray(value), true);
  }
  assert.equal(Array.isArray(snap.tasks[0].steps), true);
  assert.equal(snap.tasks[0].steps.length, 1);
});
