/* V2 三梯度运行链路浏览器手验（协议 §16）：micro_act→Avatar、mumble→微光气泡/轻声、
 * 气泡展开带入对话、mumble 轮次不出聊天气泡、未知事件不崩。真实页面 + 受管 WebSocket。
 * 不调用模型、不写真实数据；截图落 eval/ambient/。 */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "@playwright/test";

const origin = process.env.AVATAR_TEST_ORIGIN || "http://127.0.0.1:8000";
const out = "eval/ambient";
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

const browser = await chromium.launch({ headless: true, args: ["--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"] });
const checks = [];
const pass = (name) => { checks.push(name); console.log("ok -", name); };
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, reducedMotion: "no-preference" });
page.setDefaultTimeout(20000);
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

await page.addInitScript(() => {
  const h = window.__harness = { sent: [], starts: [], ends: [] };
  window.__microActLog = [];
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => Promise.reject(new Error("mic disabled in ambient test"));
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() { h.ws = this; this.readyState = 0; setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: "ready", sample_rate: 16000, session_id: "ambient-test", persona: "阿栖" }); }, 0); }
    send(data) { h.sent.push(JSON.parse(data)); }
    emit(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
  window.__emit = (value) => window.__harness.ws.emit(value);
  const NativeAC = window.AudioContext || window.webkitAudioContext;
  window.AudioContext = class extends NativeAC {
    createBufferSource() {
      const source = super.createBufferSource(), start = source.start.bind(source);
      source.start = (...args) => { window.__harness.starts.push(1); start(...args); };
      source.addEventListener("ended", () => window.__harness.ends.push(1));
      return source;
    }
  };
});
for (const endpoint of ["tasks", "reminders", "notifications"]) {
  await page.route(`**/api/assistant/${endpoint}`, (route) => route.fulfill({ json: { items: [] } }));
}
await page.route("**/api/threads", (route) => route.fulfill({ json: { items: [] } }));

await page.goto(origin);
await page.locator("#status").filter({ hasText: "在线" }).waitFor();
await page.waitForTimeout(600);
pass("真实工作台页面加载，连接在线");

const nowS = () => Math.floor(Date.now() / 1000);

// ---- 链路 A：micro_act → Avatar 动作路径 ----
await page.evaluate(() => { window.__microActLog = []; });
await page.evaluate((t) => window.__emit({ type: "micro_act", event_id: "m1", action: "tilt_head", trigger_reason: "idle_22s", expires_at: t + 30 }), nowS());
await page.waitForTimeout(60);
let log = await page.evaluate(() => window.__microActLog);
assert.ok(log.length >= 1 && log.includes("pose"), "micro_act 驱动了真实 Avatar 动作路径");
pass("micro_act 抵达 Avatar 动作路径");
await page.evaluate((t) => window.__emit({ type: "micro_act", event_id: "m1", action: "tilt_head", expires_at: t + 30 }), nowS());
await page.waitForTimeout(40);
assert.equal((await page.evaluate(() => window.__microActLog)).length, log.length, "重复 event_id 被去重");
pass("micro_act event_id 去重");
await page.evaluate((t) => window.__emit({ type: "micro_act", event_id: "m2", action: "no_such_action", expires_at: t + 30 }), nowS());
await page.waitForTimeout(40);
log = await page.evaluate(() => window.__microActLog);
assert.ok(log.includes("micro"), "未知动作安全降级为 micro，不抛错");
pass("未知 micro_act 安全降级");

// ---- 链路 B（前端）：mumble 源轮次播放音频但不产生聊天气泡 ----
const botBefore = await page.locator("#chat .msg.bot").count();
const startsBefore = (await page.evaluate(() => window.__harness.starts)).length;
await page.evaluate((b64) => {
  window.__emit({ type: "turn_started", turn_id: "t-mumble", source: "mumble" });
  window.__emit({ type: "thinking", turn_id: "t-mumble" });
  window.__emit({ type: "audio", turn_id: "t-mumble", index: 0, emotion: "base", text: "……", data: b64 });
  window.__emit({ type: "done", turn_id: "t-mumble", text: "……" });
}, wav(0.4));
await page.waitForTimeout(300);
const botAfter = await page.locator("#chat .msg.bot").count();
const startsAfter = (await page.evaluate(() => window.__harness.starts)).length;
assert.equal(botAfter, botBefore, "mumble 轮次不新增持久聊天气泡");
assert.ok(startsAfter > startsBefore, "mumble 轮次音频经共享播放链路播出");
pass("mumble 源轮次：轻声播出、无聊天气泡");

// 等这轮轻柔音频播完、播放队列回到空闲，微光气泡的去抖门（proactiveLocallyIdle）才放行。
await page.waitForFunction(() => window.__harness.starts.length > 0
  && window.__harness.ends.length >= window.__harness.starts.length, null, { timeout: 6000 }).catch(() => {});
await page.waitForTimeout(250);

// ---- 链路 C：mumble → 微光气泡 ----
await page.evaluate((t) => window.__emit({ type: "mumble", event_id: "mu1", text: "这行要是换个写法会不会更干净……",
  source_refs: ["reflection:r1"], voice: false, bubble: { text: "这行要是换个写法会不会更干净……", ttl_ms: 60000 }, expires_at: t + 60 }), nowS());
const bubble = page.locator(".glimmer-bubble");
await bubble.waitFor({ state: "visible" });
assert.match(await bubble.innerText(), /更干净/, "微光气泡展示真实 mumble 文本");
await page.screenshot({ path: `${out}/glimmer.png` });
pass("mumble 渲染微光气泡");
await page.evaluate(() => window.__emit({ type: "mumble", event_id: "mu1", text: "重复", bubble: { text: "重复", ttl_ms: 60000 } }));
assert.match(await bubble.innerText(), /更干净/, "重复 mumble 不覆盖已有气泡");
pass("mumble 去重");

// ---- 健壮性：未知事件 / 缺失数据不崩 ----
await page.evaluate(() => { window.__emit({ type: "totally_unknown_event", foo: 1 }); window.__emit({ type: "mumble", event_id: "muX" }); });
await page.waitForTimeout(40);
assert.deepEqual(pageErrors, [], "未知事件与缺失字段不触发页面错误");
pass("未知事件与缺失数据安全降级");

// ---- 触摸 head/face → 服务端选台词（遵循授权/静音）----
await page.evaluate(() => { const t = document.getElementById("proactiveToggle"); if (t) t.checked = true; });
await page.evaluate(() => window.dispatchEvent(new CustomEvent("aqi-touch", { detail: { region: "head" } })));
await page.waitForTimeout(50);
assert.ok(await page.evaluate(() => window.__harness.sent.some((m) => m.type === "touch" && m.region === "head")),
  "授权下触摸 head 发送 touch 事件");
await page.evaluate(() => { window.__harness.sent.length = 0; const t = document.getElementById("proactiveToggle"); if (t) t.checked = false; });
await page.evaluate(() => window.dispatchEvent(new CustomEvent("aqi-touch", { detail: { region: "face" } })));
await page.waitForTimeout(50);
assert.ok(!(await page.evaluate(() => window.__harness.sent.some((m) => m.type === "touch"))),
  "静音时触摸不发送（遵循静音策略）");
pass("触摸台词遵循授权/静音");

// ---- 链路 C（接话，放最后：会发起一轮用户对话）----
await bubble.click();
await page.waitForTimeout(60);
const sentText = await page.evaluate(() => window.__harness.sent.find((m) => m.type === "text"));
assert.ok(sentText && /更干净/.test(sentText.text || ""), "展开气泡把真实上下文作为一轮对话发出");
pass("气泡展开把上下文带入对话");

assert.deepEqual(pageErrors, [], "全流程无未捕获页面错误");
await browser.close();
console.log(`\n${checks.length} checks passed; screenshots in ${out}/`);
