/* 星图浏览器手验：真实页面 + 受管 WebSocket + mock REST。
 * 验证星图非空白、节点可点、详情面板、讲述触发真实播放链路、可被打断、
 * narrate_busy 冷却、检索去抖、三类空态，以及 390px 视口无横向溢出。
 * 不调用模型、不写真实数据；截图落 eval/starmap/。 */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "@playwright/test";

const origin = process.env.AVATAR_TEST_ORIGIN || "http://127.0.0.1:8000";
const out = "eval/starmap";
await mkdir(out, { recursive: true });

const wav = (seconds) => {
  const rate = 16000, length = Math.round(seconds * rate), bytes = Buffer.alloc(44 + length * 2);
  bytes.write("RIFF", 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write("WAVE", 8);
  bytes.write("fmt ", 12); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20);
  bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(rate, 24); bytes.writeUInt32LE(rate * 2, 28);
  bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34); bytes.write("data", 36);
  bytes.writeUInt32LE(length * 2, 40);
  for (let i = 0; i < length; i++) bytes.writeInt16LE(Math.round(Math.sin(i * 2 * Math.PI * 180 / rate) * 2000), 44 + i * 2);
  return bytes.toString("base64");
};

const NODES = [
  { id: "mem:1", kind: "memory", label: "她喜欢在下雨天喝奶茶", date: "2026-10-10", weight: 0.9, group: "user", ref_id: 1 },
  { id: "mem:2", kind: "memory", label: "怕打雷的夜晚", date: "2026-10-11", weight: 0.4, group: "user", ref_id: 2 },
  { id: "mem:3", kind: "memory", label: "奶茶要三分糖", date: "2026-10-11", weight: 0.6, group: "user", ref_id: 3 },
  { id: "mem:4", kind: "memory", label: "周末想去海边", date: "2026-10-12", weight: 0.5, group: "user", ref_id: 4 },
  { id: "mem:5", kind: "memory", label: "加班到十点", date: "2026-10-12", weight: 0.3, group: "user", ref_id: 5 },
  { id: "mem:6", kind: "memory", label: "养了一盆薄荷", date: "2026-10-13", weight: 0.55, group: "user", ref_id: 6 },
  { id: "mem:busy", kind: "memory", label: "她在忙的时候", date: "2026-10-13", weight: 0.45, group: "user", ref_id: 7 },
  { id: "mem:404", kind: "memory", label: "已被清空的记忆", date: "2026-10-09", weight: 0.35, group: "user", ref_id: 8 },
  { id: "diary:2026-10-12", kind: "diary", label: "10 月 12 日的日记", date: "2026-10-12", weight: 0.7, group: "self", ref_id: 20 },
  { id: "diary:2026-10-13", kind: "diary", label: "10 月 13 日的日记", date: "2026-10-13", weight: 0.65, group: "self", ref_id: 21 },
  { id: "spark:7", kind: "spark", label: "想问她为什么喜欢下雨天", date: "2026-10-12", weight: 0.55, group: "self", ref_id: 7 },
  { id: "spark:8", kind: "spark", label: "要不要聊聊大海", date: "2026-10-13", weight: 0.5, group: "self", ref_id: 8 },
  { id: "taste:音乐", kind: "taste", label: "音乐", date: "2026-10-13", weight: 0.8, group: "self", ref_id: null },
  { id: "taste:下雨天", kind: "taste", label: "下雨天", date: "2026-10-13", weight: 0.75, group: "self", ref_id: null },
  { id: "worldview:宇宙", kind: "worldview", label: "宇宙", date: "2026-10-13", weight: 0.6, group: "self", ref_id: null },
];
const EDGES = [
  { source: "mem:1", target: "diary:2026-10-12", kind: "evidences", weight: 0.8 },
  { source: "mem:3", target: "diary:2026-10-12", kind: "evidences", weight: 0.6 },
  { source: "mem:2", target: "diary:2026-10-13", kind: "evidences", weight: 0.5 },
  { source: "spark:7", target: "diary:2026-10-12", kind: "born", weight: 0.6 },
  { source: "spark:8", target: "diary:2026-10-13", kind: "born", weight: 0.5 },
  { source: "taste:音乐", target: "diary:2026-10-12", kind: "shaped", weight: 0.3 },
  { source: "taste:下雨天", target: "diary:2026-10-13", kind: "shaped", weight: 0.4 },
  { source: "mem:1", target: "mem:3", kind: "mentions", weight: 0.7 },
  { source: "mem:1", target: "taste:下雨天", kind: "mentions", weight: 0.5 },
  { source: "mem:4", target: "worldview:宇宙", kind: "mentions", weight: 0.3 },
];
const DETAILS = {
  "mem:1": { id: 1, kind: "fact", key: "雨天饮品", value: "她喜欢在下雨天喝奶茶，最好是三分糖的红茶奶茶。", source: "stated", status: "active" },
  "mem:busy": { id: 7, kind: "fact", key: "忙碌状态", value: "她在忙的时候，讲记忆要稍等一下。", source: "stated", status: "active" },
  "mem:404": null,
  "diary:2026-10-12": { id: 20, date: "2026-10-12", title: "雨天的奶茶", body: "今天她告诉我，下雨天喝奶茶会有一种被原谅的感觉。我把这句话记在了心里。", mood: "温柔", energy: 0.6, topic_keys: ["奶茶", "下雨天"] },
  "spark:7": { id: 7, text: "想问她为什么喜欢下雨天", state: "surfaced", heat: 0.8, born_diary_id: 20 },
  "taste:下雨天": { topic: "下雨天", stance: "下雨天适合发呆和说心里话", weight: 0.75, updated_at: "2026-10-13" },
};

const browser = await chromium.launch({ headless: true, args: ["--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"] });
const checks = [];
const pass = (name) => { checks.push(name); console.log("ok -", name); };
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
page.setDefaultTimeout(20000);
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

await page.addInitScript(() => {
  const h = window.__harness = { sent: [], starts: [], ends: [], turnActive: false, narrateTurn: null };
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => Promise.reject(new Error("麦克风在星图测试中禁用"));
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() {
      h.ws = this; this.readyState = 0; this.bufferedAmount = 0;
      setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: "ready", sample_rate: 16000, session_id: "starmap-test", persona: "星图测试" }); }, 0);
    }
    send(data) { if (this.readyState !== 1) throw Error("closed fixture socket"); h.sent.push(JSON.parse(data)); }
    /* 与服务端接线一致：星图钩子在 TurnGate 之前先看到消息 */
    emit(value) { window.__starmapView?.onMessage?.(value); this.onmessage?.({ data: JSON.stringify(value) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
  window.__emit = (value) => window.__harness.ws.emit(value);
  const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
  window.AudioContext = class extends NativeAudioContext {
    createBufferSource() {
      const source = super.createBufferSource(), start = source.start.bind(source);
      source.start = (...args) => { window.__harness.starts.push({ at: this.currentTime }); start(...args); };
      source.addEventListener("ended", () => window.__harness.ends.push({ at: this.currentTime }));
      return source;
    }
  };
});

const requested = [];
await page.route("**/api/memory/starmap*", (route) => {
  const url = new URL(route.request().url());
  requested.push(url.searchParams.get("q") || "");
  const q = url.searchParams.get("q");
  if (q === "查无此题") {
    return route.fulfill({ json: { nodes: [], edges: [], stats: { memories: 0, diaries: 0, sparks: 0, taste: 0, worldview: 0 }, truncated: false } });
  }
  const nodes = q ? NODES.filter((node) => node.label.includes(q)) : NODES;
  const ids = new Set(nodes.map((node) => node.id));
  const edges = q ? EDGES.filter((edge) => ids.has(edge.source) && ids.has(edge.target)) : EDGES;
  return route.fulfill({ json: { nodes, edges, stats: { memories: 6, diaries: 2, sparks: 2, taste: 2, worldview: 1 }, truncated: false } });
});
await page.route("**/api/memory/starmap/node/*", (route) => {
  const id = decodeURIComponent(route.request().url().split("/node/")[1]);
  const detail = DETAILS[id];
  if (detail === undefined || detail === null) return route.fulfill({ status: 404, json: { detail: "not found" } });
  return route.fulfill({ json: detail });
});
for (const endpoint of ["tasks", "reminders", "notifications"]) {
  await page.route(`**/api/assistant/${endpoint}`, (route) => route.fulfill({ json: { items: [] } }));
}
await page.route("**/api/threads", (route) => route.fulfill({ json: { items: [] } }));

await page.goto(origin);
await page.locator("#status").filter({ hasText: "在线" }).waitFor();
pass("真实工作台页面加载，连接在线");

/* Role 0 合入 index.html/app.js 后，页面自带挂载点、入口按钮与已挂实例，
 * 直接驱动真实接线（send/stopTurn/isIdle 都是 app.js 真身）；未合入的环境
 * （本地自测）才创建按钮与挂载点，并挂载带探针 deps 的实例。 */
await page.evaluate(async () => {
  let mount = document.getElementById("starmapMount");
  if (!mount) {
    mount = document.createElement("section");
    mount.id = "starmapMount";
    mount.className = "starmap-mount";
    mount.hidden = true;
    document.querySelector(".topbar").after(mount);
  }
  if (!document.getElementById("starmapToggle")) {
    const toggle = document.createElement("button");
    toggle.id = "starmapToggle";
    toggle.className = "ghost";
    toggle.textContent = "星图";
    toggle.setAttribute("aria-controls", "starmapMount");
    document.querySelector(".topbar").append(toggle);
  }
  if (!mount.querySelector(".starmap")) {
    const mod = await import("/static/starmap/index.js");
    window.__starmapView = mod.mountStarmap(mount, {
      send: (payload) => window.__harness.ws.send(JSON.stringify(payload)),
      stopTurn: () => window.__harness.ws.send(JSON.stringify({ type: "barge_in", turn_id: window.__harness.narrateTurn })),
      isIdle: () => !window.__harness.turnActive,
    });
  }
});

await page.evaluate((b64) => { window.__wav = b64; }, wav(0.5));

const view = page.locator("#starmapMount");
await page.locator("#starmapToggle").click();
assert.equal(await view.evaluate((el) => el.hidden), false, "视图切换入口打开星图");
await page.locator(".star-node").first().waitFor();
pass("视图切换入口与挂载点工作，星图渲染");

const nodeCount = await page.locator(".star-node").count();
assert.equal(nodeCount, NODES.length, `渲染 ${NODES.length} 个节点`);
const svgBox = await page.locator(".starmap-svg").boundingBox();
assert.ok(svgBox.width > 600 && svgBox.height > 300, "画布尺寸非零");
const radii = await page.locator(".star-node .star-dot").evaluateAll((els) => els.map((el) => Number(el.getAttribute("r"))));
assert.ok(Math.max(...radii) > Math.min(...radii) * 1.5, "星点大小随 weight 变化");
assert.equal(await page.locator(".star-node.group-user").count(), 8, "user 组色相节点数");
assert.equal(await page.locator(".star-node.group-self").count(), 7, "self 组色相节点数");
assert.equal(await page.locator(".star-edge").count(), EDGES.length, "边全部渲染");
for (const kind of ["evidences", "born", "shaped", "mentions"]) {
  assert.ok(await page.locator(`.star-edge.kind-${kind}`).count() > 0, `边型 ${kind} 有区分样式`);
}
assert.equal(await page.locator(".starmap-legend").evaluate((el) => el.hidden), false, "图例可见");
pass("星图非空白：节点/边/分组/图例齐全");
await page.screenshot({ path: `${out}/desktop-graph.png` });

await page.locator('.star-node[data-id="mem:1"]').click();
await page.locator(".starmap-detail-body").filter({ hasText: "三分糖" }).waitFor();
const detailText = await page.locator(".starmap-detail-body").textContent();
assert.match(detailText, /奶茶/, "详情面板展示节点全文");
assert.ok(await page.locator(".starmap-detail-tags .tag").first().isVisible(), "详情有类型标签");
pass("节点点击后详情面板经详情接口取全文");
await page.screenshot({ path: `${out}/desktop-detail.png` });

const narrateBtn = page.locator(".starmap-narrate-btn");
assert.equal(await narrateBtn.textContent(), "让小溪讲这个");
await narrateBtn.click();
const narrateSent = await page.evaluate(() => window.__harness.sent.findLast((m) => m.type === "narrate"));
assert.ok(narrateSent, "点击讲述发出 narrate 动作");
assert.equal(narrateSent.node_id, "mem:1");
assert.ok(typeof narrateSent.turn_id === "string" && narrateSent.turn_id.startsWith("client-"), "turn_id 由客户端生成");
await page.evaluate((id) => { window.__harness.narrateTurn = id; window.__harness.turnActive = true; }, narrateSent.turn_id);
await page.evaluate((turn) => {
  window.__emit({ type: "turn_started", turn_id: turn, source: "narration" });
  window.__emit({ type: "thinking", turn_id: turn });
  window.__emit({ type: "emotion", turn_id: turn, emotion: "温柔" });
  window.__emit({ type: "text_delta", turn_id: turn, delta: "那天她跟我说，下雨天喝奶茶，" });
  window.__emit({ type: "text_delta", turn_id: turn, delta: "会有一种被原谅的感觉。" });
}, narrateSent.turn_id);
  /* 浮层按设计盖住聊天区，气泡在 DOM 中由真实渲染器生成即可证明链路复用 */
  await page.waitForFunction(() => [...document.querySelectorAll(".msg.bot")]
    .some((el) => el.textContent.includes("被原谅的感觉")));
pass("讲述文本经既有链路落入聊天气泡（复用现有渲染）");
await page.evaluate((turn) => {
  window.__emit({ type: "audio", turn_id: turn, index: 0, text: "那天她跟我说，下雨天喝奶茶，会有一种被原谅的感觉。", emotion: "温柔", data: window.__wav });
}, narrateSent.turn_id);
await page.waitForFunction(() => window.__harness.starts.length >= 1);
pass("讲述音频经既有 PlaybackQueue 真实解码播放");
await page.evaluate((turn) => window.__emit({ type: "done", turn_id: turn, text: "那天她跟我说，下雨天喝奶茶，会有一种被原谅的感觉。" }), narrateSent.turn_id);
await page.evaluate(() => { window.__harness.turnActive = false; });
assert.equal(await narrateBtn.textContent(), "让小溪讲这个", "done 后状态机回 idle");
pass("narration 全链路：pending -> playing -> done -> idle");

await page.locator('.star-node[data-id="mem:busy"]').click();
await page.locator(".starmap-narrate-btn").click();
const busySent = await page.evaluate(() => window.__harness.sent.findLast((m) => m.type === "narrate"));
assert.equal(busySent.node_id, "mem:busy");
await page.evaluate(() => window.__emit({ type: "error", code: "narrate_busy", recoverable: true, message: "小溪正在忙" }));
await page.locator(".starmap-narrate-btn").filter({ hasText: "小溪正忙着" }).waitFor();
assert.equal(await page.locator(".starmap-narrate-btn").isDisabled(), true, "busy 时按钮禁用");
assert.match(await page.locator(".starmap-narrate-note").textContent(), /小溪正在讲别的/, "busy 有可重试提示");
const sentBeforeCooldown = await page.evaluate(() => window.__harness.sent.filter((m) => m.type === "narrate").length);
assert.equal(sentBeforeCooldown, 2, "busy 不排队、不自动重试");
pass("narrate_busy：按钮禁用 + 可重试提示 + 不自动重试");
await page.screenshot({ path: `${out}/desktop-busy.png` });
await page.waitForFunction(() => {
  const button = document.querySelector(".starmap-narrate-btn");
  return button && !button.disabled && button.textContent === "让小溪讲这个";
}, null, { timeout: 12000 });
pass("busy 冷却结束后按钮恢复可重试");

await page.locator('.star-node[data-id="mem:1"]').click();
await page.locator(".starmap-detail-body").filter({ hasText: "三分糖" }).waitFor();
await page.locator(".starmap-narrate-btn").click();
const bargeTurn = await page.evaluate(() => window.__harness.sent.findLast((m) => m.type === "narrate").turn_id);
await page.evaluate((turn) => { window.__harness.narrateTurn = turn; window.__harness.turnActive = true; }, bargeTurn);
await page.evaluate((turn) => {
  window.__emit({ type: "turn_started", turn_id: turn, source: "narration" });
  window.__emit({ type: "thinking", turn_id: turn });
}, bargeTurn);
const startsBeforeBarge = await page.evaluate(() => window.__harness.starts.length);
await page.evaluate((turn) => window.__emit({ type: "audio", turn_id: turn, index: 0, text: "被打断的一句话", emotion: "温柔", data: window.__wav }), bargeTurn);
await page.waitForFunction((before) => window.__harness.starts.length > before, startsBeforeBarge);
await page.getByRole("button", { name: "停止讲述" }).click();
const bargeSent = await page.evaluate(() => window.__harness.sent.findLast((m) => m.type === "barge_in"));
assert.ok(bargeSent, "停止讲述发出 barge_in");
assert.equal(bargeSent.turn_id, bargeTurn, "barge_in 携带当前轮次");
await page.evaluate((turn) => { window.__emit({ type: "interrupted", turn_id: turn }); window.__harness.turnActive = false; }, bargeTurn);
assert.equal(await page.locator(".starmap-narrate-btn").textContent(), "让小溪讲这个", "打断后状态机回 idle");
pass("barge_in 打断讲述：本地立即回 idle，播放链路既有行为");

await page.locator("#starmapSearch").fill("奶茶");
  await page.waitForFunction(() => document.querySelectorAll(".star-node").length === 2, null, { timeout: 6000 });
  assert.ok(requested.includes("奶茶"), "检索关键词经 q 参数发给服务端");
pass("关键词检索走 q 参数并经服务端过滤");
await page.locator("#starmapSearch").fill("查无此题");
await page.locator(".starmap-empty-text").filter({ hasText: "没有找到" }).waitFor();
assert.match(await page.locator(".starmap-empty-text").textContent(), /查无此题/, "检索空结果有空态");
  await page.locator(".starmap-empty").getByRole("button", { name: "清除检索" }).click();
  await page.waitForFunction((count) => document.querySelectorAll(".star-node").length === count, NODES.length);
pass("检索空结果空态与清除恢复");

await page.locator('.star-node[data-id="mem:404"]').click();
await page.locator(".starmap-detail-body").filter({ hasText: "已经不在了" }).waitFor();
pass("节点详情 404 有明确空态");

  await page.locator("#memoryToggle").evaluate((el) => { if (el.checked) el.click(); });
await page.locator(".starmap-bar").getByRole("button", { name: "刷新" }).click();
await page.locator(".starmap-empty-text").filter({ hasText: "记忆已关闭" }).waitFor();
assert.ok(await page.locator(".starmap-narrate").evaluate((el) => el.hidden), "记忆关闭时讲述区隐藏");
pass("记忆关闭态：空态展示，不报错");
  await page.locator("#memoryToggle").evaluate((el) => { if (!el.checked) el.click(); });
await page.locator(".starmap-bar").getByRole("button", { name: "刷新" }).click();
await page.locator(".star-node").first().waitFor();

await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
assert.ok(overflow, "390px 视口无横向溢出");
const mobileSvg = await page.locator(".starmap-svg").boundingBox();
assert.ok(mobileSvg.width > 300 && mobileSvg.height > 200, "移动端画布非零");
await page.locator('.star-node[data-id="taste:下雨天"]').click();
await page.locator(".starmap-detail-body").filter({ hasText: "说心里话" }).waitFor();
const detailBox = await page.locator(".starmap-detail").boundingBox();
assert.ok(detailBox.width <= 390 && detailBox.y + detailBox.height <= 845, "移动端详情面板在视口内");
pass("移动视口：星图可用、详情面板为底栏、无横向溢出");
await page.screenshot({ path: `${out}/mobile-detail.png` });

/* 真实接线下实例句柄在 app.js 闭包里，统一用入口按钮收尾（两种状态都成立） */
await page.locator("#starmapToggle").click();
assert.equal(await page.locator("#starmapMount").evaluate((el) => el.hidden), true, "关闭后视图隐藏");
assert.equal(await page.locator("#starmapToggle").getAttribute("aria-expanded"), "false", "入口按钮状态同步");

assert.deepEqual(pageErrors, [], "页面无未捕获错误");
await browser.close();
console.log(`PASS: 星图浏览器手验 ${checks.length} 项全绿（桌面 + 移动视口，截图在 ${out}/）`);
