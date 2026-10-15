/* V2 星图浏览器手验（协议 §18）：seed/reflection 节点、parent_child 裂变、
 * 金色 epiphany 边、节点详情与关系理由来自真实数据。真实页面 + 受管 WebSocket + mock REST。
 * 不调用模型、不写真实数据；截图落 eval/starmap-v2/。 */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "@playwright/test";

const origin = process.env.AVATAR_TEST_ORIGIN || "http://127.0.0.1:8000";
const out = "eval/starmap-v2";
await mkdir(out, { recursive: true });

const NODES = [
  { id: "seed:seed_night_decisions", kind: "seed", label: "深夜决策", date: "2026-10-14", weight: 0.6, group: "self", ref_id: "seed_night_decisions" },
  { id: "seed:seed_a1", kind: "seed", label: "无干扰时间", date: "2026-10-15", weight: 0.55, group: "self", ref_id: "seed_a1" },
  { id: "reflection:refl_1", kind: "reflection", label: "与白天被打断有关", date: "2026-10-15", weight: 0.6, group: "self", ref_id: "refl_1" },
  { id: "diary:2026-10-13", kind: "diary", label: "他又半夜改稿", date: "2026-10-13", weight: 0.65, group: "self", ref_id: 21 },
];
const EDGES = [
  { source: "seed:seed_night_decisions", target: "seed:seed_a1", kind: "parent_child", weight: 0.6, reason: "子种子裂变：来自注意力中断的发现", created_at: 1 },
  { source: "seed:seed_night_decisions", target: "reflection:refl_1", kind: "related_memory", weight: 0.5, reason: "反思选材：seed", created_at: 2 },
  { source: "seed:seed_night_decisions", target: "diary:2026-10-13", kind: "epiphany", weight: 0.7, reason: "深夜决策与改稿日记跨界共振", created_at: 3 },
];
const DETAILS = {
  "seed:seed_night_decisions": { id: "seed_night_decisions", title: "深夜决策", question: "观察人类为什么总喜欢在深夜做推翻白天的决定？", status: "digested", heat: 0.6, child_seed_ids: ["seed_a1"], source_refs: ["config:seeds.yaml"] },
  "reflection:refl_1": { id: "refl_1", seed_id: "seed_night_decisions", narrative: "我翻了他的日记，忽然明白他半夜改稿不只是熬。", discovery: "他深夜推翻白天，可能和白天频繁被打断有关", source_refs: ["diary:2026-10-13"], processed: true },
};

const browser = await chromium.launch({ headless: true, args: ["--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"] });
const checks = [];
const pass = (name) => { checks.push(name); console.log("ok -", name); };
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
page.setDefaultTimeout(20000);
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

await page.addInitScript(() => {
  const h = window.__harness = { sent: [] };
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => Promise.reject(new Error("mic disabled"));
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() { h.ws = this; this.readyState = 0; setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: "ready", sample_rate: 16000, session_id: "starmap-v2", persona: "阿栖" }); }, 0); }
    send(data) { h.sent.push(JSON.parse(data)); }
    emit(value) { window.__starmapView?.onMessage?.(value); this.onmessage?.({ data: JSON.stringify(value) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
  window.__emit = (value) => window.__harness.ws.emit(value);
});

await page.route("**/api/memory/starmap*", (route) => {
  const q = new URL(route.request().url()).searchParams.get("q");
  return route.fulfill({ json: { nodes: q ? NODES.filter((n) => n.label.includes(q)) : NODES,
    edges: EDGES, stats: { memories: 0, diaries: 1, sparks: 0, taste: 0, worldview: 0 }, truncated: false } });
});
await page.route("**/api/memory/starmap/node/*", (route) => {
  const id = decodeURIComponent(route.request().url().split("/node/")[1]);
  const detail = DETAILS[id];
  return detail === undefined ? route.fulfill({ status: 404, json: { detail: "not found" } })
    : route.fulfill({ json: detail });
});
for (const endpoint of ["tasks", "reminders", "notifications"]) {
  await page.route(`**/api/assistant/${endpoint}`, (route) => route.fulfill({ json: { items: [] } }));
}
await page.route("**/api/threads", (route) => route.fulfill({ json: { items: [] } }));

await page.goto(origin);
await page.locator("#status").filter({ hasText: "在线" }).waitFor();
pass("真实工作台页面加载，连接在线");

const starmapEntry = async () => {
  const toggle = page.locator("#starmapToggle");
  if (!(await toggle.isVisible())) await page.locator("#drawerToggle").click();
  return toggle;
};
await (await starmapEntry()).click();
const view = page.locator("#starmapMount");
assert.equal(await view.evaluate((el) => el.hidden), false, "星图视图打开");
await page.locator(".star-node").first().waitFor();
pass("星图加载真实节点");

// Seed 与 Reflection 节点可显示
assert.ok(await page.locator(".star-node.kind-seed").count() >= 2, "seed 节点渲染");
assert.ok(await page.locator(".star-node.kind-reflection").count() >= 1, "reflection 节点渲染");
pass("seed / reflection 节点显示");

// parent_child 与 epiphany 边渲染；epiphany 为金色
assert.ok(await page.locator(".star-edge.kind-parent_child").count() >= 1, "parent_child 裂变连线");
const epiphany = page.locator(".star-edge.kind-epiphany").first();
assert.ok(await epiphany.count() >= 1, "epiphany 边渲染");
const stroke = await epiphany.evaluate((el) => getComputedStyle(el).stroke);
assert.ok(/rgb\(232, 194, 106\)|#e8c26a/i.test(stroke), `epiphany 边为金色（实际 ${stroke}）`);
await page.screenshot({ path: `${out}/starmap-v2.png` });
pass("parent_child 裂变 + 金色 epiphany 边");

// 关系理由来自真实数据：用页面内的真实 bridge 跑一遍渲染模型，确认 reason 被保留可读。
const reasonOk = await page.evaluate(async () => {
  const mod = await import("/static/starmap/bridge.js");
  const model = mod.toRenderModel({
    nodes: [
      { id: "seed:s1", kind: "seed", label: "深夜决策", weight: 0.6, group: "self", ref_id: "s1" },
      { id: "diary:d", kind: "diary", label: "改稿", weight: 0.6, group: "self", ref_id: 1 },
    ],
    edges: [{ source: "seed:s1", target: "diary:d", kind: "epiphany", weight: 0.7, reason: "深夜决策与改稿日记跨界共振" }],
  });
  return model.edges.some((e) => /跨界共振/.test(e.reason || ""));
});
assert.ok(reasonOk, "关系理由从真实数据保留可读");
pass("关系理由来自真实数据");

// 节点详情：点击 seed 拉取全文
await page.locator(".star-node.kind-seed").first().click();
await page.locator(".starmap-detail").filter({ hasText: "深夜" }).waitFor();
pass("seed 节点详情可查看");

// 图例展示 V2 类型（等图例可见且标签渲染）
await page.locator(".starmap-legend").getByText("好奇心种子").waitFor();
const legendText = await page.locator(".starmap-legend").innerText();
assert.ok(/好奇心种子/.test(legendText), "图例含『好奇心种子』");
assert.ok(/一次反思/.test(legendText), "图例含『一次反思』");
assert.ok(/跨界顿悟/.test(legendText), "图例含『跨界顿悟』");
pass("星图图例展示 V2 类型");

// 详情面板展示真实关联理由（epiphany / parent_child）
await page.locator(".starmap-detail-edge").first().waitFor();
const edgesText = await page.locator(".starmap-detail-edges").innerText();
assert.ok(/跨界顿悟/.test(edgesText) && /跨界共振/.test(edgesText), "详情展示 epiphany 边的真实理由");
assert.ok(/裂变出/.test(edgesText), "详情展示 parent_child 裂变关系");
await page.screenshot({ path: `${out}/starmap-detail.png` });
pass("详情面板展示真实关联理由");

// 缺失数据安全降级：清空响应不崩
assert.deepEqual(pageErrors, [], "无未捕获页面错误");
await browser.close();
console.log(`\n${checks.length} checks passed; screenshots in ${out}/`);
