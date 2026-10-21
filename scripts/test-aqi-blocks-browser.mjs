/* 协议 §23 动态积木（Aqi-Blocks）浏览器契约（Voice-First 打磨轮）：
 * 小溪开口只说一两句口语（头顶微光气泡，去文字框化），状态/选项/卡片数据以
 * aqi_blocks 轮次消息随路下发，落在 Holo-Deck 或战况抽屉。本测试覆盖：
 *   ① 6 种积木的 DOM 渲染（含 metric_flame 条形、swimlane 状态文案）
 *   ② 动作回传闭环五类 cmd：dispatch:accept / ui:drawer / ui:preview / ui:starmap / say
 *   ③ nav 与 drawer_ops 的抽屉联动、gaze 视线联动
 *   ④ Holo-Deck 6 张上限回收、未知 kind/坏字段/XSS 安全降级、陈旧轮次丢弃
 *   ⑤ memo_note（第七种积木）渲染 + meta 小字
 *   ⑥ 心流防打扰（协议 §23.6）：IME 输入只发 input 也要静默、快捷键不续安静期、
 *      停手后按原序补弹、战况胶囊只报数不催
 *   ⑦ 去文字框化：流式回复不再产生 .msg.bot，口头台词住进微光气泡
 * 真实页面 + 受管 WebSocket（按服务端真实时序 turn_started→积木→done 回帧）
 * + mock 形象/API，不调用模型、不写真实数据。 */
import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
import { readFile, mkdir } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const origin = "http://aqiblocks.test";
const out = "eval/aqi-blocks";
await mkdir(out, { recursive: true });

const browser = await chromium.launch({ headless: true });
const errors = [];
// 关掉动效：`.aqi-block` 有 0.32s 的 `aqi-card-in` 入场动画（companion.css 与
// almanac.css 都定义了，且都认 `prefers-reduced-motion: reduce`）。Playwright 的
// click 要求元素在连续两帧里位置不变，动画期间框体一直在动。整门派生时 CPU
// 被占满，稳定性检查能拖过 15s 超时——2026-10-18 在 27 道门禁全套跑时
// 观察到一次 `#holoDeck ... .aqi-action` 点击超时，单独跑 45 次均不复现。
// 关动效不削弱断言：卡片仍必须可见、仍必须真的接收指针事件（不是 force 点击），
// 验的还是"这张卡点得动"。
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
page.setDefaultTimeout(15000);
page.on("pageerror", (error) => errors.push(error.message));

await page.route(`${origin}/**`, async (route) => {
  const url = new URL(route.request().url());
  if (url.pathname === "/static/avatar/index.js") {
    return route.fulfill({ contentType: "text/javascript", body: `
      const noop = () => {};
      export const avatar = new Proxy({ emotion: {set: noop}, rendererKind:'svg', onMessage: noop,
        stopSpeaking: noop }, { get: (o,k) => (k in o ? o[k] : noop) });
      export const boot = async () => {};
    ` });
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
  const h = window.__harness = { sent: [], previews: [] };
  window.addEventListener("aqi-block-preview", (event) => h.previews.push(event.detail?.ref));
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() { h.ws = this; this.readyState = 0; setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: "ready", sample_rate: 16000, session_id: "aqi-test", persona: "阿栖" }); }, 0); }
    send(data) {
      const value = JSON.parse(data);
      h.sent.push(value);
      if (value.type === "text") {
        // 受管回帧：模拟服务端一轮完整回复（口头一两句，不带积木标签）。
        setTimeout(() => {
          this.emit({ type: "thinking", turn_id: value.turn_id });
          this.emit({ type: "text_delta", turn_id: value.turn_id, delta: "好嘞，听着呢。" });
          this.emit({ type: "done", turn_id: value.turn_id, text: "好嘞，听着呢。" });
        }, 0);
      }
    }
    emit(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
});

const checks = [];
const pass = (name) => { checks.push(name); console.log("ok -", name); };
const sent = () => page.evaluate(() => window.__harness.sent);
const deckCount = () => page.locator("#holoDeck .aqi-block").count();
const gazeLog = () => page.evaluate(() => window.__aqiGazeLog || []);
/* 按服务端真实时序回一轮：turn_started（开门）→ 积木随路 → done（收轮）。
 * 轮次外的积木消息会被 TurnGate 丢弃——这正是协议 §23 的陈旧帧过滤。 */
const emitTurn = (payload) => page.evaluate((p) => {
  const ws = window.__harness.ws;
  ws.emit({ type: "turn_started", turn_id: p.turn_id, source: "text" });
  ws.emit(p);
  ws.emit({ type: "done", turn_id: p.turn_id, text: "" });
}, payload);
const emitRaw = (payload) => page.evaluate((p) => window.__harness.ws.emit(p), payload);

await page.goto(origin);
await page.locator("#status").filter({ hasText: "在线" }).waitFor();

// ---- ① 六种积木一次随路下发，全部落在 Holo-Deck ----
await emitTurn({
  type: "aqi_blocks", turn_id: "t-1", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [
    { kind: "choice_pills", question: "你手上等着验收的有 2 单，说个名字我就知道是哪一单。",
      options: [{ label: "周报整理", cmd: "dispatch:accept:c-42" }, { label: "相册分类", cmd: "dispatch:accept:c-43" }] },
    { kind: "spotlight_card", title: "周报整理", summary: "三段式周报已生成。", intercepted: "把桌面截图也塞进了附件。",
      artifacts: [{ kind: "文档", ref: "report.md", preview: "# 周报" }],
      actions: [{ label: "预览成果", cmd: "ui:preview:report.md" }] },
    { kind: "alert_card", title: "第 2 次打回", detail: "初审说语气太硬，重写。", level: "critical",
      actions: [{ label: "去处理", cmd: "ui:drawer:ops" }] },
    { kind: "swimlane_strip", lanes: [
      { agent_id: "a1", agent_name: "小风", title: "找资料", step_idx: 2, status: "running" },
      { agent_id: "a2", agent_name: "小灯", title: "写稿", step_idx: 1, status: "blocked" } ] },
    { kind: "metric_flame", note: "这一把烧掉的。",
      metrics: [{ label: "调用", value: 12 }, { label: "Token 合计", value: 8640 }, { label: "首包均值 ms", value: 820 }],
      actions: [{ label: "看战况抽屉", cmd: "ui:drawer:ops" }] },
    { kind: "note_snippet", text: "有些念头白天想不明白，晚上反而清楚了。", title: "偷师顿悟", ref: "note:77",
      actions: [{ label: "接话", cmd: "say:那你说来听听" }] },
  ],
});
assert.equal(await deckCount(), 6, "六种积木全部渲染到 Holo-Deck");
for (const kind of ["choice_pills", "spotlight_card", "alert_card", "swimlane_strip", "metric_flame", "note_snippet"]) {
  assert.equal(await page.locator(`#holoDeck .aqi-block[data-kind="${kind}"]`).count(), 1, `${kind} 渲染一卡`);
}
assert.equal(await page.locator("#holoDeck").evaluate((el) => el.hidden), false, "有卡时 Holo-Deck 可见");
assert.match(await page.locator('#holoDeck .aqi-block[data-kind="choice_pills"] .aqi-block-title').textContent(), /说个名字/);
assert.equal(await page.locator("#holoDeck .aqi-pill").count(), 2, "请示胶囊两颗");
assert.match(await page.locator('#holoDeck .aqi-block[data-kind="spotlight_card"] .aqi-artifact').textContent(), /文档 · report\.md/);
assert.match(await page.locator("#holoDeck .aqi-intercepted").textContent(), /拦了：把桌面截图也塞进了附件/);
assert.equal(await page.locator('#holoDeck .aqi-block[data-kind="alert_card"]').getAttribute("data-level"), "critical");
assert.match(await page.locator('#holoDeck .aqi-block[data-kind="alert_card"] .aqi-line').textContent(), /语气太硬/);
const lanes = page.locator("#holoDeck .aqi-lane");
assert.equal(await lanes.count(), 2, "流光条两条泳道");
assert.equal(await lanes.nth(0).getAttribute("data-status"), "running");
assert.match(await lanes.nth(0).textContent(), /小风.*干活中/);
assert.match(await lanes.nth(1).textContent(), /等你拍板/);
assert.equal(await page.locator("#holoDeck .aqi-metric-fill").count(), 3, "三个数值metric都有条形");
const widths = await page.locator("#holoDeck .aqi-metric-fill").evaluateAll((els) => els.map((el) => parseFloat(el.style.width)));
assert.equal(widths.length, 3, "三条条形");
assert.ok(Math.abs(widths[0] - 4) < 0.01, "小值保底 4%（12/8640）");
assert.equal(widths[1], 100, "峰值 100%（Token 合计）");
assert.ok(Math.abs(widths[2] - (820 / 8640) * 100) < 0.01, "按峰值精确归一（820/8640）");
assert.match(await page.locator("#holoDeck .aqi-note-body").textContent(), /白天想不明白/);
assert.equal(await page.locator("#holoDeck .aqi-ref").textContent(), "note:77");
assert.ok((await gazeLog()).includes("holo_right"), "卡片浮现时视线看向全息区");
await page.screenshot({ path: `${out}/holo-deck.png` });
pass("六种积木 DOM 渲染 + 视线联动");

// ---- ②a dispatch:accept 回传后端执行业务动作 ----
await page.locator("#holoDeck .aqi-pill").first().click();
assert.ok((await sent()).some((m) => m.type === "aqi_action" && m.cmd === "dispatch:accept:c-42"),
  "胶囊点击回传 aqi_action，cmd 带真实 commission_id");
pass("dispatch:accept 业务动作回传");

// ---- ②a2 choice:<index> 本地动作：通用按钮触发同一张卡的第 N 颗胶囊 ----
// 协议 §23.3 声明过这个动词，但前端曾没有分支，点了落进"未知动词：忽略"。
// 这里走真实渲染路径：积木自带的 actions 里放一个 choice:1，点它必须等价于
// 点第二颗胶囊（回传 dispatch:accept:c-102），越界 index 必须什么都不做。
await emitTurn({
  type: "aqi_blocks", turn_id: "t-choice", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "choice_pills", question: "这两单先收哪个？",
    options: [{ label: "甲", cmd: "dispatch:accept:c-101" },
              { label: "乙", cmd: "dispatch:accept:c-102" }],
    actions: [{ label: "收第二单", cmd: "choice:1" },
              { label: "越界", cmd: "choice:9" }] }],
});
const sentBefore = (await sent()).length;
await page.locator('#holoDeck .aqi-block[data-kind="choice_pills"] .aqi-action').first().click();
await page.waitForTimeout(150);
let afterChoice = (await sent()).slice(sentBefore);
assert.ok(afterChoice.some((m) => m.type === "aqi_action" && m.cmd === "dispatch:accept:c-102"),
  `choice:1 等价于点第二颗胶囊（实际发出 ${JSON.stringify(afterChoice)}）`);
const sentAfterValid = (await sent()).length;
await page.locator('#holoDeck .aqi-block[data-kind="choice_pills"] .aqi-action').nth(1).click();
await page.waitForTimeout(150);
assert.equal((await sent()).length, sentAfterValid, "越界 choice:9 无任何副作用");
pass("choice:<index> 本地动作与越界忽略");

// ---- ②b say:* 走普通文字轮次，小溪语音确认；去文字框化断言 ----
await page.locator('#holoDeck .aqi-block[data-kind="note_snippet"] .aqi-action').click();
assert.ok((await sent()).some((m) => m.type === "text" && m.text === "那你说来听听"),
  "say 动作填入输入框并走真实 text 轮次");
await page.locator(".glimmer-bubble").waitFor({ state: "visible" });
assert.match(await page.locator(".glimmer-bubble .glimmer-text").textContent(), /听着呢/,
  "口头台词住进头顶微光气泡");
assert.equal(await page.locator("#chat .msg.bot").count(), 0, "去文字框化：小溪不再产生 bot 文字气泡");
pass("say 动作语音确认 + 去文字框化（微光接手，无文字气泡）");

// ---- ④a Holo-Deck 6 张上限：再递两张，最旧的请示卡被回收 ----
// 这里递两张而不是一张：第一张填满上限，第二张才把最早那张挤出去。
// 原来只递一张就断言"最旧被回收"，只要前面多渲染了一张卡，这条断言就会
// 把"回收机制坏了"和"测试自己的计数漂了"混成同一个失败。
await emitTurn({ type: "aqi_blocks", turn_id: "t-2", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "note_snippet", text: "星图里又亮了一颗。", title: "星图近况", ref: "note:78",
    actions: [{ label: "看星图", cmd: "ui:starmap" }] }] });
await emitTurn({ type: "aqi_blocks", turn_id: "t-3", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "note_snippet", text: "又一张。", title: "再一张", ref: "note:79",
    actions: [{ label: "看星图", cmd: "ui:starmap" }] }] });
// 回收发生在 render 之后；卡片挂载有错峰入场，等一拍再数。
await page.waitForTimeout(200);
const deckKinds = await page.locator("#holoDeck .aqi-block").evaluateAll(
  (els) => els.map((el) => el.dataset.kind));
assert.equal(await deckCount(), 6, `超过 6 张收回最旧的，卡片区不无限生长（实际 ${deckKinds}）`);
// 按内容断言，不按 kind：②a2 又递过一张请示卡，按 kind 数会把"最早那张
// 被回收"和"后面那张还在"混成同一个失败。
assert.equal(
  await page.locator('#holoDeck .aqi-block[data-kind="choice_pills"]').filter({ hasText: "说个名字" }).count(),
  0, `最早那张请示卡被回收（实际顺序 ${deckKinds}）`);
pass("Holo-Deck 上限 6 张回收最旧");

// ---- ②c ui:starmap 本地动作：点亮星图（不经过后端） ----
await page.locator("#holoDeck .aqi-block:last-child .aqi-action").click();
await page.waitForFunction(() => document.getElementById("starmapToggle")?.getAttribute("aria-expanded") === "true");
assert.equal(await page.locator("#starmapMount").evaluate((el) => el.hidden), false, "ui:starmap 打开全屏星图");
await page.keyboard.press("Escape");   // 星图自己监听的收起键（toggle 藏在隐藏抽屉里，不可直接点）
await page.waitForFunction(() => document.getElementById("starmapMount").hidden === true);
pass("ui:starmap 本地点亮星图");

// ---- ②d ui:preview 本地动作：拉战况抽屉 + 广播成果 ref ----
// 上限回收已经把最早那张 spotlight_card 挤出去了（① 与 ②a2 之后又递了 4 张）。
// 补递一张带 ui:preview 的，确保这一步验的是预览联动本身，不是卡片还在不在。
await emitTurn({ type: "aqi_blocks", turn_id: "t-preview", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "spotlight_card", title: "预览用卡", summary: "验 ui:preview。",
    artifacts: [{ kind: "文档", ref: "report.md", preview: "# 周报" }],
    actions: [{ label: "预览成果", cmd: "ui:preview:report.md" }] }] });
await page.locator('#holoDeck .aqi-block[data-kind="spotlight_card"]').filter({ hasText: "预览用卡" })
  .locator(".aqi-action").click();
assert.equal(await page.locator("#workDrawer").evaluate((el) => el.hidden), false, "ui:preview 自动拉开工作台抽屉");
assert.equal(await page.locator("#drawerViewOps").evaluate((el) => el.hidden), false, "预览定位到战况视图");
assert.deepEqual(await page.evaluate(() => window.__harness.previews), ["report.md"], "预览广播真实成果 ref");
/* 这一步验的是：抽屉被拉开、切到战况视图、ref 被广播出去。
 * 金边样式由 test-ui-browser 的真实 computed style 断言覆盖；本用例的 ref
 * 与卡片 id 不同，因此这里不应把"类没加上"当成失败。 */
pass("ui:preview 本地预览联动：抽屉 + 战况视图 + ref 广播");

// ---- ③a nav 抽屉联动：四个目标各自跳到既有视图 ----
for (const [nav, view] of [["notes", "drawerViewWriting"], ["threads", "drawerViewCompanion"], ["starmap", "drawerViewStarmap"], ["ops", "drawerViewOps"]]) {
  await emitTurn({ type: "aqi_blocks", turn_id: `t-nav-${nav}`, layout: "stage_holo", nav, gaze: "holo_right",
    blocks: [{ kind: "note_snippet", text: `带你去${nav}。` }] });
  assert.equal(await page.locator(`#${view}`).evaluate((el) => el.hidden), false, `nav=${nav} 跳到既有视图`);
  assert.equal(await page.locator("#workDrawer").evaluate((el) => el.hidden), false, `nav=${nav} 自动拉开抽屉`);
}
pass("nav 四目标自动拉开抽屉并跳到既有视图");

// ---- ③b drawer_ops layout：积木落在战况抽屉内，视线转向抽屉 ----
await emitTurn({ type: "aqi_blocks", turn_id: "t-ops", layout: "drawer_ops", nav: null, gaze: null,
  blocks: [{ kind: "spotlight_card", title: "战况直出卡", summary: "抽屉里的积木。",
    actions: [{ label: "去战况抽屉核对", cmd: "ui:drawer:ops" }] }] });
assert.equal(await page.locator("#opsAqiBlocks .aqi-block").count(), 1, "drawer_ops 积木渲染在战况抽屉");
assert.equal(await page.locator("#drawerViewOps").evaluate((el) => el.hidden), false, "drawer_ops 自动切到战况视图");
assert.equal((await gazeLog()).at(-1), "drawer", "drawer_ops 默认视线转向抽屉");
// ---- ②e ui:drawer 本地动作（抽屉内卡片可点） ----
await page.locator("#opsAqiBlocks .aqi-action").click();
assert.equal(await page.locator("#drawerViewOps").evaluate((el) => el.hidden), false, "ui:drawer:ops 保持战况视图");
pass("drawer_ops 直出 + ui:drawer 本地动作 + 视线转向抽屉");

// ---- ④b 安全降级：未知 kind / 坏字段不渲染、不报错 ----
const before = await deckCount();
await emitTurn({ type: "aqi_blocks", turn_id: "t-bad", layout: "stage_holo", nav: null, gaze: null,
  blocks: [{ kind: "mystery_block" }, { kind: "choice_pills", question: 123, options: "nope" },
    { kind: "metric_flame", metrics: "nope" }, { kind: "spotlight_card" }] });
assert.equal(await deckCount(), before, "坏块一张都不渲染");
assert.equal(await page.locator("#opsAqiBlocks .aqi-block").count(), 1, "抽屉里的好卡不受坏消息影响");

// ---- ④d 陈旧轮次：没有对应 turn_started 的积木消息被 TurnGate 丢弃 ----
await emitRaw({ type: "aqi_blocks", turn_id: "t-stale", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "note_snippet", text: "陈旧的帧不该复活。" }] });
assert.equal(await deckCount(), before, "轮次外的积木消息被丢弃（协议 §23 turn fence）");

// ---- ④c XSS：积木内容只走 textContent，不产生元素、不执行 ----
await emitTurn({ type: "aqi_blocks", turn_id: "t-xss", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "note_snippet", text: '<img src=x onerror="window.__aqiXss=1"><script>window.__aqiXss=2</script>', title: "危险摘录" }] });
const xssCard = page.locator('#holoDeck .aqi-block[data-kind="note_snippet"]').last();
assert.match(await xssCard.locator(".aqi-note-body").textContent(), /<img src=x/);
assert.equal(await page.locator("#holoDeck img, #holoDeck script").count(), 0, "HTML Looking 文本不产生元素");
assert.equal(await page.evaluate(() => window.__aqiXss), undefined, "内联事件与脚本均未执行");
pass("未知 kind / 坏字段 / 陈旧轮次 / XSS 全部安全降级");

// ---- ④e memo_note：第七种积木（平静期安心便签）也要能落地 ----
await emitTurn({ type: "aqi_blocks", turn_id: "t-memo", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "memo_note", title: "手头清净", body: "该盯的都收了工，该答的你都点了头。",
    meta: { "在跑": 0, "待验收": 0 } }] });
const memo = page.locator('#holoDeck .aqi-block[data-kind="memo_note"]');
assert.equal(await memo.count(), 1, "memo_note 渲染一卡");
assert.match(await memo.locator(".aqi-line").textContent(), /该答的你都点了头/);
assert.match(await memo.locator(".aqi-meta-chip").first().textContent(), /在跑 0/);
pass("memo_note 安心便签渲染 + meta 小字");

// ---- ④f 心流防打扰（协议 §23.6）：打字期间挂起不弹卡，停手后原序补弹 ----
// 先把战况视图激活，胶囊呼吸灯归 executive.js 渲染（#opsPending 在抽屉里）。
await emitTurn({ type: "aqi_blocks", turn_id: "t-ops-activate", layout: "drawer_ops", nav: null, gaze: "drawer",
  blocks: [{ kind: "swimlane_strip", lanes: [{ agent_id: "scribe", agent_name: "笔录官", status: "running" }] }] });
await page.waitForFunction(() => document.getElementById("drawerViewOps")?.hidden === false);
const beforeTyping = await deckCount();
// ① 中文输入法只触发 input、一个 keydown 都不发——这是小溪的主力输入方式，
//    心流防打扰必须认它，否则整句中文打下来卡片照弹不误。
await page.locator("#textInput").click();
await page.keyboard.insertText("在改这一段的说法");
await emitTurn({ type: "aqi_blocks", turn_id: "t-held", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "note_snippet", text: "打字时不该弹出来的卡。", title: "待阅" }] });
await page.waitForTimeout(120);
assert.equal(await deckCount(), beforeTyping, "IME 输入（只有 input 事件）也让她安静：不弹卡");
assert.equal(await page.locator('#holoDeck .aqi-block[data-kind="note_snippet"]').filter({ hasText: "打字时" }).count(),
  0, "挂起的卡内容没有偷偷渲染");

// ② 停手超过静默窗（1200ms）后按原序补弹，内容一字不改。
//    注意：Holo-Deck 有 6 张上限，前面几节已经填满，所以只能等"这一张出现"，
//    不能等总数变多（总数到顶就不再变了）。
await page.waitForTimeout(1500);
await page.waitForFunction(
  () => [...document.querySelectorAll('#holoDeck .aqi-block[data-kind="note_snippet"]')]
    .some((el) => (el.textContent || "").includes("打字时不该弹出来")),
  null, { timeout: 4000 },
);
assert.match(
  await page.locator('#holoDeck .aqi-block[data-kind="note_snippet"]').filter({ hasText: "打字时" }).locator(".aqi-note-body").textContent(),
  /打字时不该弹出来的卡。/, "停手后补弹，内容原样");
await page.waitForFunction(() => document.getElementById("opsPending")?.hidden === true, null, { timeout: 4000 });

// ③ 快捷键不算打字：焦点在按钮上按 Escape，不该把安静期续上，卡片照常弹。
await page.locator("#sendBtn").focus();
await page.keyboard.press("Escape");
await emitTurn({ type: "aqi_blocks", turn_id: "t-not-held", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "note_snippet", text: "快捷键之后该弹的卡。", title: "照常" }] });
await page.waitForFunction(
  () => [...document.querySelectorAll('#holoDeck .aqi-block[data-kind="note_snippet"]')]
    .some((el) => (el.textContent || "").includes("快捷键之后")),
  null, { timeout: 4000 },
);
pass("心流防打扰：IME 静默 / 停手补弹 / 快捷键不续安静期");

// ④ 回到输入框继续打字，下一张卡重新挂起，胶囊报数（2 张：这一张 + 之后的第二张）。
await page.locator("#textInput").click();
await page.keyboard.type("abc", { delay: 5 });
await emitTurn({ type: "aqi_blocks", turn_id: "t-held-2", layout: "stage_holo", nav: null, gaze: "holo_right",
  blocks: [{ kind: "note_snippet", text: "第二张该挂起的卡。", title: "待阅二" }] });
await page.waitForTimeout(120);
assert.equal(await page.locator('#holoDeck .aqi-block[data-kind="note_snippet"]').filter({ hasText: "第二张" }).count(),
  0, "重新打字后又开始挂起");
// executive.js 收到报数后要异步刷新一次才画胶囊：等它，不断言瞬间状态。
const capsule = page.locator("#opsPending");
await page.waitForFunction(() => {
  const box = document.getElementById("opsPending");
  return box && !box.hidden && /她递了 1 张卡/.test(box.textContent || "");
}, null, { timeout: 4000 });
assert.ok((await capsule.locator(".ops-breathe").count()) >= 1, "胶囊带呼吸灯");
await page.waitForTimeout(1500);
await page.waitForFunction(
  () => [...document.querySelectorAll('#holoDeck .aqi-block[data-kind="note_snippet"]')]
    .some((el) => (el.textContent || "").includes("第二张该挂起")),
  null, { timeout: 4000 },
);
assert.match(
  await page.locator('#holoDeck .aqi-block[data-kind="note_snippet"]').filter({ hasText: "第二张" }).locator(".aqi-note-body").textContent(),
  /第二张该挂起的卡。/, "停手后第二张也补弹");
await page.waitForFunction(() => document.getElementById("opsPending")?.hidden === true, null, { timeout: 4000 });
pass("心流防打扰：胶囊只报数不催，队列清空后熄灯");

// ---- ⑤ 收尾：全程无 bot 文字气泡、无未捕获错误 ----
assert.equal(await page.locator("#chat .msg.bot").count(), 0, "整场对话小溪没有输出过一个文字气泡");
assert.deepEqual(errors, [], `全流程无未捕获页面错误: ${errors.join("; ")}`);
await page.screenshot({ path: `${out}/drawer-ops.png` });

await browser.close();
console.log(`\n${checks.length} checks passed; screenshots in ${out}/`);
