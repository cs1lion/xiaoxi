/* 星图单测：渲染模型转换 / 检索去抖 / narrate 状态机 / 空态与关闭态。
 * bridge.js 是零导入纯模块，经 data: URL 以 ESM 加载（package.json 无 type:module）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const load = async (name) => import(`data:text/javascript;base64,${Buffer.from(
  await readFile(new URL(name, import.meta.url))).toString("base64")}`);
const {
  toRenderModel, classifyEmpty, createGraphSearch, createNarration,
  createStarmapClient, LIST_LIMIT,
} = await load("./bridge.js");

class FakeClock {
  now = 0;
  next = 0;
  tasks = new Map();
  setTimeout = (run, delay) => { const id = ++this.next; this.tasks.set(id, { at: this.now + delay, run }); return id; };
  clearTimeout = (id) => this.tasks.delete(id);
  async advance(ms) {
    const target = this.now + ms;
    while (true) {
      const entry = [...this.tasks].sort((a, b) => a[1].at - b[1].at).find(([, task]) => task.at <= target);
      if (!entry) break;
      const [id, task] = entry; this.tasks.delete(id); this.now = task.at; task.run(); await flush();
    }
    this.now = target; await flush();
  }
}
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

const graphPayload = () => ({
  nodes: [
    { id: "mem:1", kind: "memory", label: "她喜欢在下雨天喝奶茶", date: "2026-10-10", weight: 0.9, group: "user", ref_id: 1 },
    { id: "mem:2", kind: "memory", label: "怕打雷", date: "2026-10-11", weight: 0.4, group: "user", ref_id: 2 },
    { id: "diary:2026-10-12", kind: "diary", label: "10 月 12 日", date: "2026-10-12", weight: 0.7, group: "self", ref_id: 3 },
    { id: "spark:7", kind: "spark", label: "想问她为什么喜欢下雨天", date: "2026-10-12", weight: 0.55, group: "self", ref_id: 7 },
    { id: "taste:音乐", kind: "taste", label: "音乐", date: "2026-10-13", weight: 0.8, group: "self", ref_id: null },
  ],
  edges: [
    { source: "mem:1", target: "diary:2026-10-12", kind: "evidences", weight: 0.8 },
    { source: "spark:7", target: "diary:2026-10-12", kind: "born", weight: 0.6 },
    { source: "taste:音乐", target: "diary:2026-10-12", kind: "shaped", weight: 0.3 },
    { source: "mem:1", target: "mem:2", kind: "mentions", weight: 0.2 },
  ],
  stats: { memories: 2, diaries: 1, sparks: 1, taste: 1, worldview: 0 },
  truncated: false,
});

test("toRenderModel 整理节点、边、度数、统计", () => {
  const model = toRenderModel(graphPayload());
  assert.equal(model.nodes.length, 5);
  assert.equal(model.edges.length, 4);
  assert.equal(model.empty, false);
  assert.equal(model.truncated, false);
  assert.equal(model.byId.get("mem:1").weight, 0.9);
  assert.equal(model.byId.get("mem:1").group, "user");
  assert.equal(model.byId.get("mem:1").refId, 1);
  assert.equal(model.byId.get("taste:音乐").refId, null);
  assert.equal(model.byId.get("diary:2026-10-12").degree, 3);
  assert.equal(model.byId.get("mem:1").degree, 2);
  assert.equal(model.byId.get("mem:2").degree, 1);
  assert.deepEqual(model.stats, { memories: 2, diaries: 1, sparks: 1, taste: 1, worldview: 0 });
  assert.deepEqual(model.kindCounts, { memory: 2, diary: 1, spark: 1, taste: 1 });
  assert.deepEqual(model.groupCounts, { user: 2, self: 3 });
});

test("toRenderModel 对缺字段与脏数据有防御", () => {
  const model = toRenderModel({
    nodes: [
      { id: "mem:1" },
      { id: "mem:2", weight: 5, label: "x".repeat(200), group: "外星", kind: "" },
      { id: "mem:3", date: null, ref_id: undefined },
      { kind: "memory", label: "没有 ID" },
      null,
      { id: "mem:1", label: "重复 ID 被忽略" },
    ],
    edges: [
      { source: "mem:1", target: "mem:2", kind: "evidences" },
      { source: "mem:1", target: "mem:404", kind: "born", weight: 1 },
      { source: "mem:1", target: "mem:1", kind: "mentions" },
      null,
    ],
  });
  assert.equal(model.nodes.length, 3);
  assert.equal(model.edges.length, 1);
  assert.equal(model.byId.get("mem:1").weight, 0.5);
  assert.equal(model.byId.get("mem:1").kind, "memory");
  assert.equal(model.byId.get("mem:1").date, null);
  assert.equal(model.byId.get("mem:2").weight, 1);
  assert.equal(model.byId.get("mem:2").label.length, 80);
  assert.equal(model.byId.get("mem:2").group, "user");
  assert.equal(model.byId.get("mem:2").kind, "memory");
  assert.equal(model.byId.get("mem:1").degree, 1);
  assert.deepEqual(model.stats, { memories: 0, diaries: 0, sparks: 0, taste: 0, worldview: 0 });
  assert.equal(toRenderModel(null).empty, true);
  assert.equal(toRenderModel({}).empty, true);
});

test("classifyEmpty 区分关闭、空图、检索无结果", () => {
  const model = toRenderModel(graphPayload());
  assert.equal(classifyEmpty(model, { memoryEnabled: true, query: "" }), null);
  const off = classifyEmpty(model, { memoryEnabled: false, query: "奶茶" });
  assert.equal(off.kind, "off");
  assert.match(off.message, /记忆已关闭/);
  const emptyModel = toRenderModel({ nodes: [], edges: [] });
  assert.equal(classifyEmpty(emptyModel, { memoryEnabled: true, query: "" }).kind, "empty");
  const search = classifyEmpty(emptyModel, { memoryEnabled: true, query: "奶茶" });
  assert.equal(search.kind, "search");
  assert.match(search.message, /奶茶/);
});

test("检索去抖：连续输入只发一次请求且采用最后一次关键词", async () => {
  const clock = new FakeClock();
  const calls = [];
  const search = createGraphSearch({
    load: (q) => { calls.push(q); return Promise.resolve(toRenderModel({ nodes: [], edges: [] })); },
    delay: 300,
    timers: clock,
  });
  search.search("奶");
  search.search("奶茶");
  search.search("奶茶 雷");
  assert.deepEqual(calls, []);
  assert.equal(search.pending, "奶茶 雷");
  await clock.advance(299);
  assert.deepEqual(calls, []);
  await clock.advance(2);
  await flush();
  assert.deepEqual(calls, ["奶茶 雷"]);
  assert.equal(search.pending, null);
});

test("检索 flush / run / cancel 与过期响应丢弃", async () => {
  const clock = new FakeClock();
  const calls = [];
  const deferred = [];
  const results = [];
  const search = createGraphSearch({
    load: (q) => {
      calls.push(q);
      return new Promise((resolve) => deferred.push({ q, resolve }));
    },
    delay: 300,
    timers: clock,
    onResult: (model, q) => results.push(q),
  });
  search.search("a");
  search.flush();
  assert.deepEqual(calls, ["a"]);
  search.search("b");
  await clock.advance(300);
  await flush();
  assert.deepEqual(calls, ["a", "b"]);
  search.cancel();
  for (const item of deferred) item.resolve(toRenderModel({ nodes: [], edges: [] }));
  await flush();
  assert.deepEqual(results, [], "cancel 之后所有在途结果都算过期");
});

test("检索失败走 onError 且不渲染", async () => {
  const clock = new FakeClock();
  const errors = [];
  const results = [];
  const search = createGraphSearch({
    load: () => Promise.reject(new Error("星图加载失败（500）")),
    delay: 10,
    timers: clock,
    onError: (error) => errors.push(error.message),
    onResult: () => results.push("不该出现"),
  });
  search.run("奶茶");
  await flush();
  assert.deepEqual(errors, ["星图加载失败（500）"]);
  assert.deepEqual(results, []);
});

const makeNarration = (overrides = {}) => {
  const sent = [];
  const stopped = [];
  const clock = overrides.clock || new FakeClock();
  let counter = 0;
  const machine = createNarration({
    send: (payload) => { sent.push(payload); return true; },
    stopTurn: () => stopped.push(true),
    timers: clock,
    newTurnId: () => `client-test-${++counter}`,
    ...overrides,
  });
  return { machine, sent, stopped, clock };
};

test("narrate 状态机：idle -> pending -> playing -> done -> idle", () => {
  const { machine, sent } = makeNarration();
  assert.equal(machine.state, "idle");
  assert.equal(machine.canStart(), true);
  assert.equal(machine.start("mem:1"), true);
  assert.equal(machine.state, "pending");
  assert.deepEqual(sent, [{ type: "narrate", node_id: "mem:1", turn_id: "client-test-1" }]);
  machine.onMessage({ type: "turn_started", turn_id: "client-test-1", source: "narration" });
  assert.equal(machine.state, "playing");
  machine.onMessage({ type: "thinking", turn_id: "client-test-1" });
  machine.onMessage({ type: "audio", turn_id: "client-test-1", data: "x" });
  assert.equal(machine.state, "playing");
  machine.onMessage({ type: "done", turn_id: "client-test-1", text: "讲完了" });
  assert.equal(machine.state, "idle");
  assert.equal(machine.snapshot().note, "");
});

test("narrate 状态机：busy 错误不带 turn_id 也按忙碌处理，冷却后可重试", async () => {
  const clock = new FakeClock();
  const { machine, sent } = makeNarration({ clock, busyCooldownMs: 4000 });
  machine.start("mem:1");
  machine.onMessage({ type: "error", code: "narrate_busy", recoverable: true, message: "小溪正在忙" });
  assert.equal(machine.state, "busy");
  assert.match(machine.snapshot().note, /小溪正在讲别的/);
  assert.equal(machine.start("mem:2"), false, "busy 时不排队");
  assert.equal(sent.length, 1);
  await clock.advance(3999);
  assert.equal(machine.state, "busy");
  await clock.advance(2);
  assert.equal(machine.state, "idle");
  assert.equal(machine.start("mem:2"), true);
  assert.equal(sent.length, 2);
});

test("narrate 状态机：busy 错误带任意 turn_id 同样按忙碌处理", () => {
  const { machine } = makeNarration();
  machine.start("mem:1");
  machine.onMessage({ type: "error", code: "narrate_busy", turn_id: "client-test-1" });
  assert.equal(machine.state, "busy");
});

test("narrate 状态机：barge_in 打断回 idle，stop 本地立即回 idle", () => {
  const first = makeNarration();
  first.machine.start("mem:1");
  first.machine.onMessage({ type: "turn_started", turn_id: "client-test-1", source: "narration" });
  first.machine.onMessage({ type: "interrupted", turn_id: "client-test-1" });
  assert.equal(first.machine.state, "idle");
  assert.match(first.machine.snapshot().note, /讲述已停下/);

  const second = makeNarration();
  second.machine.start("mem:2");
  second.machine.onMessage({ type: "turn_started", turn_id: "client-test-1", source: "narration" });
  assert.equal(second.machine.stop(), true);
  assert.equal(second.machine.state, "idle");
  assert.deepEqual(second.stopped, [true]);
  assert.equal(second.machine.stop(), false);
});

test("narrate 状态机：非忙碌 error 与等待超时都回 idle", async () => {
  const clock = new FakeClock();
  const failed = makeNarration({ clock, pendingTimeoutMs: 15000 });
  failed.machine.start("mem:1");
  failed.machine.onMessage({ type: "error", turn_id: "client-test-1", message: "节点不存在" });
  assert.equal(failed.machine.state, "idle");
  assert.equal(failed.machine.snapshot().note, "节点不存在");

  const timedOut = makeNarration({ clock, pendingTimeoutMs: 15000 });
  timedOut.machine.start("mem:1");
  await clock.advance(15001);
  assert.equal(timedOut.machine.state, "idle");
  assert.match(timedOut.machine.snapshot().note, /讲述没有开始/);
});

test("narrate 状态机：别的轮次事件不干扰，isIdle 为假时不能开始", () => {
  const { machine, sent } = makeNarration({ isIdle: () => false });
  assert.equal(machine.canStart(), false);
  machine.start("mem:9");
  assert.equal(machine.state, "pending");
  machine.onMessage({ type: "turn_started", turn_id: "别人的轮次", source: "narration" });
  machine.onMessage({ type: "done", turn_id: "别人的轮次" });
  assert.equal(machine.state, "pending");
  machine.onMessage({ type: "done", turn_id: "client-test-1" });
  assert.equal(machine.state, "idle");
  assert.equal(sent.length, 1);
});

test("REST 客户端：URL 参数、limit 上限、404 与异常", async () => {
  const seen = [];
  const ok = (payload) => ({ ok: true, status: 200, json: async () => payload });
  const client = createStarmapClient({
    fetchImpl: async (url) => { seen.push(url); return ok({ nodes: [], edges: [] }); },
  });
  await client.loadGraph({ days: 90, q: "奶茶", limit: 9999 });
  assert.match(seen[0], /^\/api\/memory\/starmap\?/);
  assert.match(seen[0], /days=90/);
  assert.match(seen[0], /q=/);
  assert.ok(seen[0].includes(`limit=${LIST_LIMIT}`), "limit 超过 400 时压到 400");
  seen.length = 0;
  await client.loadGraph({});
  assert.ok(!seen[0].includes("days="), "没传 days 时不塞默认值");

  const missing = await createStarmapClient({
    fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({}) }),
  }).loadNode("mem:404");
  assert.deepEqual(missing, { missing: true, node: null });

  const failing = createStarmapClient({
    fetchImpl: async () => ({ ok: false, status: 500, json: async () => ({}) }),
  });
  await assert.rejects(() => failing.loadGraph(), /星图加载失败（500）/);
  await assert.rejects(() => failing.loadNode("mem:1"), /节点加载失败（500）/);

  const broken = createStarmapClient({
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => { throw new Error("bad json"); } }),
  });
  await assert.rejects(() => broken.loadGraph(), /格式异常/);
});

test("REST 客户端：loadNode 成功路径、缺 ID 抛错、kinds 透传、limit 边界", async () => {
  const seen = [];
  const ok = (payload, status = 200) => ({ ok: status < 400, status, json: async () => payload });
  const client = createStarmapClient({
    fetchImpl: async (url) => { seen.push(url); return ok({ id: "mem:1", kind: "memory", body: "全文" }); },
  });
  // loadNode 200 成功：返回 { missing:false, node }，nodeId 经 encodeURIComponent 转义
  const got = await client.loadNode("mem:1 特殊");
  assert.deepEqual(got, { missing: false, node: { id: "mem:1", kind: "memory", body: "全文" } });
  assert.ok(seen[0].startsWith("/api/memory/starmap/node/"), "loadNode 打节点详情端点");
  assert.ok(seen[0].includes(encodeURIComponent("mem:1 特殊")), "nodeId 经 encodeURIComponent 转义");

  // loadNode 空/非字符串 ID → 抛错且根本不发请求
  seen.length = 0;
  await assert.rejects(() => client.loadNode(""), /缺少节点 ID/);
  await assert.rejects(() => client.loadNode(42), /缺少节点 ID/);
  assert.deepEqual(seen, [], "缺 ID 时不发请求");

  // loadGraph kinds 透传
  seen.length = 0;
  await client.loadGraph({ kinds: "memory,diary" });
  assert.match(seen[0], /kinds=memory/, "kinds 参数透传到查询串");

  // loadGraph limit 边界：0/坏值回落 LIST_LIMIT，负数钳到 1
  seen.length = 0;
  await client.loadGraph({ limit: 0 });
  assert.ok(seen[0].includes(`limit=${LIST_LIMIT}`), "limit=0 回落上限");
  seen.length = 0;
  await client.loadGraph({ limit: -5 });
  assert.ok(seen[0].includes("limit=1"), "负 limit 钳到 1");

  // loadGraph days 非法值（0/负数/NaN）不塞查询参数
  seen.length = 0;
  await client.loadGraph({ days: 0 });
  assert.ok(!seen[0].includes("days="), "days=0 不塞");
});

test("narrate：subscribe 收快照 / unsubscribe 停收 / reset 清定时器回 idle", () => {
  const snaps = [];
  const { machine } = makeNarration();
  const unsub = machine.subscribe((s) => snaps.push(s.state));
  machine.start("mem:1");   // -> pending
  machine.onMessage({ type: "turn_started", turn_id: "client-test-1", source: "narration" });  // -> playing
  assert.deepEqual(snaps, ["pending", "playing"], "每次状态变更都推快照给订阅者");
  unsub();
  machine.stop();           // -> idle，但已退订
  assert.deepEqual(snaps, ["pending", "playing"], "unsubscribe 后不再收到快照");

  // reset：pending 中复位，清掉 pending 超时定时器、回 idle、清空 note 并推快照
  const clock = new FakeClock();
  const snaps2 = [];
  const second = makeNarration({ clock, pendingTimeoutMs: 15000 });
  second.machine.subscribe((s) => snaps2.push(s.state));
  second.machine.start("mem:2");
  assert.equal(clock.tasks.size, 1, "pending 期间挂着超时定时器");
  second.machine.reset();
  assert.deepEqual(snaps2, ["pending", "idle"], "reset 回 idle 并推快照");
  assert.equal(clock.tasks.size, 0, "reset 清掉了 pending 超时定时器");
  assert.equal(second.machine.snapshot().note, "", "reset 清空 note");
});
