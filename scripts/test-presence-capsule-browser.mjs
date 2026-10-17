/* 陪伴心流三态胶囊（Presence Mode Capsule）浏览器契约（打磨轮 2026-10-16）：
 * ✨ 灵动伴随 → 🌙 静默旁观 → 🍃 无痕闲聊 循环切换，底层布尔位
 * #memoryToggle / #proactiveToggle 与 WS config 的 memory / proactivity
 * 组合正确派发，/api/memory/toggle 持久化与失败回滚，确认气泡与微表情反馈，
 * 主动搭话心跳仅在“灵动伴随”下放行。真实页面 + 受管 WebSocket，不调用模型。 */
import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const origin = "http://presence.test";
const out = "eval/presence";
await mkdir(out, { recursive: true });

const browser = await chromium.launch({ headless: true });
const errors = [];
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.setDefaultTimeout(20000);
page.on("pageerror", (error) => errors.push(error.message));

const memoryToggles = [];
await page.route(`${origin}/**`, async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname === "/static/avatar/index.js") {
    return route.fulfill({ contentType: "text/javascript", body: `
      const calls = window.__avatarCalls = [];
      export const avatar = new Proxy({
        clock: 1.5,
        gesture: { play: (name) => calls.push(["gesture.play", name]) },
        setEyeExpression: (name) => calls.push(["setEyeExpression", name]),
        blinkEyes: () => calls.push(["blinkEyes"]),
        emotion: { set: () => {} },
        onMessage: () => {},
      }, { get: (o, k) => (k in o ? o[k] : () => {}) });
      export const boot = async () => {};
    ` });
  }
  if (url.pathname === "/api/memory/toggle") {
    memoryToggles.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: true } });
  }
  if (url.pathname.startsWith("/api/")) {
    const data = url.pathname === "/api/persona"
      ? { name: "阿栖", tagline: "离线测试", voices: ["base", "认真"], default_voice: "base" }
      : { count: 0, items: [] };
    return route.fulfill({ json: data });
  }
  const relative = url.pathname === "/" ? "web/index.html" : "web/" + url.pathname.replace(/^\/static\//, "");
  const absolute = path.resolve(root, relative);
  if (!absolute.startsWith(root + path.sep)) return route.abort();
  try {
    const body = await readFile(absolute);
    const contentType = absolute.endsWith(".js") || absolute.endsWith(".mjs") ? "text/javascript"
      : absolute.endsWith(".css") ? "text/css" : "text/html";
    return route.fulfill({ body, contentType });
  } catch { return route.fulfill({ status: 404, body: "not found" }); }
});
await page.addInitScript(() => {
  const h = window.__harness = { sent: [] };
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() { h.ws = this; this.readyState = 0; setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: "ready", sample_rate: 16000, session_id: "presence-test", persona: "阿栖" }); }, 0); }
    send(data) { h.sent.push(JSON.parse(data)); }
    emit(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
});

const checks = [];
const pass = (name) => { checks.push(name); console.log("ok -", name); };
const sent = () => page.evaluate(() => window.__harness.sent);
const lastConfig = async () => (await sent()).filter((m) => m.type === "config").at(-1);
const capsule = page.locator("#presenceCapsule");
const capsuleLabel = () => capsule.locator(".presence-label").innerText();
const avatarCalls = () => page.evaluate(() => window.__avatarCalls);

await page.goto(origin);
await page.locator("#status").filter({ hasText: "在线" }).waitFor();
await page.waitForTimeout(400);

// ---- 形态：双 Switch 已移除，胶囊落在输入胶囊栏（不是顶栏） ----
assert.equal(await page.locator("label.switch").filter({ hasText: "允许阿栖记住" }).count(), 0,
  "“允许阿栖记住”文字标签与工业风 Switch 已移除");
assert.equal(await page.locator("label.switch").filter({ hasText: "允许阿栖主动开口" }).count(), 0,
  "“允许阿栖主动开口”文字标签与工业风 Switch 已移除");
assert.equal(await capsule.count(), 1, "三态胶囊存在");
assert.ok(await capsule.evaluate((el) => el.closest(".composer") !== null),
  "胶囊位于底部胶囊栏区域，不在顶栏");
assert.equal(await page.locator(".topbar #presenceCapsule").count(), 0, "顶栏没有新增按钮");
assert.match(await page.evaluate(() => getComputedStyle(document.getElementById("presenceCapsule")).backdropFilter),
  /blur\(24px\) saturate\(/, "胶囊为空间毛玻璃材质（blur 24px + saturate 180%）");
pass("双 Switch 移除，毛玻璃胶囊落在胶囊栏");

// ---- 底层布尔位：协议零破坏 ----
assert.deepEqual(await page.evaluate(() => [
  document.getElementById("memoryToggle").checked,
  document.getElementById("proactiveToggle").checked,
]), [true, true], "隐藏布尔位仍是唯一状态源，默认双开");
pass("底层布尔位 allow_memory / allow_proactive 复用不变");

// ---- 模式 1（默认）：✨ 灵动伴随 ----
assert.equal(await capsuleLabel(), "灵动伴随");
assert.ok(await capsule.evaluate((el) => el.classList.contains("is-lively")));
assert.match(await capsule.getAttribute("aria-label"), /灵动伴随/);
await page.screenshot({ path: `${out}/mode-lively.png` });
pass("模式 1 默认：✨ 灵动伴随");

// ---- 点击 → 模式 2：🌙 静默旁观 ----
await capsule.click();
assert.equal(await capsuleLabel(), "静默旁观");
assert.ok(await capsule.evaluate((el) => el.classList.contains("is-quiet")));
const quiet = await lastConfig();
assert.equal(quiet.proactivity, false, "静默旁观：allow_proactive=false（绝不主动开口）");
assert.equal(quiet.memory, true, "静默旁观：allow_memory=true（只乖巧记下）");
assert.equal(memoryToggles.length, 0, "记忆未变化时不重复写 REST");
assert.match(await page.locator(".glimmer-bubble").innerText(), /安安静静陪着你/, "切换确认气泡：好嘞，我安安静静陪着你，不打岔~");
assert.ok((await avatarCalls()).some((c) => c[0] === "gesture.play" && c[1] === "nod"),
  "静默旁观触发乖巧点头微动作");
await page.screenshot({ path: `${out}/mode-quiet.png` });
pass("模式 2：🌙 静默旁观 → {memory:true, proactivity:false} + 点头 + 确认气泡");

// ---- 点击 → 模式 3：🍃 无痕闲聊 ----
await capsule.click();
assert.equal(await capsuleLabel(), "无痕闲聊");
assert.ok(await capsule.evaluate((el) => el.classList.contains("is-off")));
const off = await lastConfig();
assert.equal(off.proactivity, false, "无痕闲聊：allow_proactive=false（仅被动回应）");
assert.equal(off.memory, false, "无痕闲聊：allow_memory=false（暂停写入长期记忆）");
assert.deepEqual(memoryToggles.at(-1), { enabled: false }, "切到无痕闲聊时 /api/memory/toggle 持久化 enabled=false");
assert.match(await page.locator(".glimmer-bubble").innerText(), /绝不往小本本上记/, "切换确认气泡：明白，现在开始说的悄悄话，我绝不往小本本上记。");
assert.ok((await avatarCalls()).some((c) => c[0] === "blinkEyes"), "无痕闲聊触发眨眼微表情");
await page.screenshot({ path: `${out}/mode-off.png` });
pass("模式 3：🍃 无痕闲聊 → {memory:false, proactivity:false} + 眨眼 + REST 持久化");

// ---- 无痕闲聊下心跳静音：不发 proactive_check ----
await page.waitForTimeout(16000);
assert.ok(!(await sent()).some((m) => m.type === "proactive_check"),
  "无痕闲聊（未授权主动开口）下不发送主动搭话心跳");

// ---- 点击回到模式 1：✨ 灵动伴随 ----
await capsule.click();
assert.equal(await capsuleLabel(), "灵动伴随");
const lively = await lastConfig();
assert.equal(lively.proactivity, true, "灵动伴随：allow_proactive=true（自然主动开口搭话）");
assert.equal(lively.memory, true, "灵动伴随：allow_memory=true（记忆沉淀与笔记偷师）");
assert.deepEqual(memoryToggles.at(-1), { enabled: true }, "回到灵动伴随时记忆恢复持久化");
assert.match(await page.locator(".glimmer-bubble").innerText(), /随时跟你说/, "切换确认气泡：那我看到好玩的、或者有想法了就随时跟你说咯！");
assert.ok((await avatarCalls()).some((c) => c[0] === "gesture.play" && c[1] === "nod"),
  "灵动伴随触发前倾/点头微动作");
assert.ok((await avatarCalls()).some((c) => c[0] === "setEyeExpression" && c[1] === "happy"),
  "灵动伴随触发开心微表情");
await page.screenshot({ path: `${out}/mode-lively-again.png` });
pass("回到模式 1：✨ 灵动伴随 → {memory:true, proactivity:true} + 开心微表情 + 确认气泡");

// ---- 灵动伴随下心跳放行：15s 内必发 proactive_check ----
await page.waitForFunction(() => window.__harness.sent.some((m) => m.type === "proactive_check"),
  null, { timeout: 20000 });
pass("灵动伴随下主动搭话心跳放行（25~30s 首次阈值可达）");

// ---- 循环回到模式 2 ----
await capsule.click();
assert.equal(await capsuleLabel(), "静默旁观");
pass("三态循环：灵动伴随 → 静默旁观 → 无痕闲聊 → 灵动伴随 → 静默旁观");

// ---- 失败回滚：REST 503 时胶囊与配置一起回滚 ----
await page.route(`${origin}/api/memory/toggle`, (route) => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
await capsule.click();   // 静默旁观 → 无痕闲聊（记忆关，REST 失败）
await page.waitForFunction(() => document.querySelector("#memoryFeedback").textContent.includes("设置未保存"),
  null, { timeout: 8000 });
assert.equal(await capsuleLabel(), "静默旁观", "保存失败后胶囊滚回上一态");
assert.deepEqual(await page.evaluate(() => [
  document.getElementById("memoryToggle").checked,
  document.getElementById("proactiveToggle").checked,
]), [true, false], "保存失败后布尔位回滚");
const rolled = await lastConfig();
assert.equal(rolled.memory, true, "回滚后重新同步 WS 配置，服务端与界面一致");
pass("REST 失败回滚：布尔位、胶囊视觉、WS 配置三方一致");

assert.deepEqual(errors, [], `全流程无未捕获页面错误: ${errors.join("; ")}`);
await browser.close();
console.log(`\n${checks.length} checks passed; screenshots in ${out}/`);
