/* 确认门可达性单测（零真实 DOM，极简 element fake）。
 *
 * 背景：bootAssistant 把 panel 挂上去之后、preferences 拉回来之前有一个网络往返的空窗，
 * 而面板这时对用户已经可见。行动确认区（gate）必须在这个空窗之前就建好，否则空窗里到达的
 * confirmation 结果会被 showAssistantResult 的 `if (!actionArea) return` 静默丢弃——
 * 没有按钮也没有报错，有副作用的动作永远无法获批。
 *
 * 另外两条：面板压根没启动时要挡在 panel.open 之前；确认成功后 refresh() 不能把一次
 * 成功的确认显示成错误。
 *
 * 顺序约定（模块是单例，actionArea/panel 一旦赋值就回不到初始状态）：
 *   1. 未启动      —— 不能调用 bootAssistant
 *   2. 在途        —— 必须第一个调用 bootAssistant，且停在 preferences 请求上
 *   3. 失败路径    —— 此时才可以让 bootAssistant 跑完
 *   4. 批准按钮    —— 同上
 * Node 单文件内 test() 按声明顺序串行执行。 */
import test from "node:test";
import assert from "node:assert/strict";
import { bootAssistant, showAssistantResult } from "./assistant.js";

// bootAssistant 会 fire-and-forget 地 import interaction-preferences.js；这个 fake 下
// FakeElement 没有 select.value 默认值，bootPreferences 会在 dimensions[...][1] 上抛
// TypeError，被 .catch 吞掉并 console.warn。与本次断言无关，静音以免污染输出。
console.warn = () => {};

const dispatched = [];

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.textContent = "";
    this.className = "";
    this.type = "";
    this.open = false;
    this.id = "";
    this.dataset = {};
    this.attributes = {};
    this.listeners = [];
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  append(...nodes) { for (const n of nodes) this.children.push(n); }
  prepend(...nodes) { for (const n of nodes) this.children.unshift(n); }
  replaceChildren() { this.children.length = 0; }
  remove() { this.removed = true; }
  dispatchEvent(event) { dispatched.push(event); return true; }
  addEventListener(type, callback) { this.listeners.push({ type, callback }); }
  get classList() {
    const names = String(this.className || "").split(/\s+/).filter(Boolean);
    return { contains: name => names.includes(name) };
  }
  // 只支持测试用到的几种选择器：.class / #id / tag
  querySelector(selector) {
    return this.children.find(child => matches(child, selector)) || null;
  }
}

const matches = (element, selector) => {
  if (selector.startsWith(".")) return element.classList.contains(selector.slice(1));
  if (selector.startsWith("#")) return element.id === selector.slice(1);
  return element.tagName === selector;
};

const mount = new FakeElement("div");
globalThis.document = {
  createElement: tag => new FakeElement(tag),
  querySelector: selector => (selector === "#chat" ? null : mount),
  hidden: false,
  activeElement: null,
};

const confirmResult = {
  text: "要新增这个任务吗？", run_id: "r1",
  confirmation: { id: "c1", tool: "task.create", arguments: { title: "买菜" } },
};

// serviceOnline=false 时拒绝请求（逼 bootAssistant 走 catch）；true 时返回空列表。
let serviceOnline = false;
const installStubFetch = () => {
  globalThis.fetch = () => serviceOnline
    ? Promise.resolve({ ok: true, status: 200, json: async () => ({ items: [] }) })
    : Promise.reject(new Error("任务服务没响应"));
};
installStubFetch();

const gateButtons = () => {
  const event = dispatched.find(e => e.type === "assistant-result");
  const card = event?.detail?.card;
  if (!card) return null;
  const row = card.children.find(child => String(child.className || "").includes("assistant-row"));
  return row ? row.children.map(button => button.textContent) : null;
};

// 经 mount 取到 errorArea（模块级，未导出）。两条判据缺一不可：
//  - 只认带 .assistant-error 的面板：偏好面板的 className 同样是 'panel assistant-panel'，
//    只按 className 认会在偏好面板也挂上时认错；
//  - 在其中取最后一个：fake 的 remove() 不从父节点脱离，早前测试留下的 panel 还在
//    mount.children 里，取第一个会读到上一轮启动残留的 errorArea。
const errorText = () => {
  const panels = mount.children.filter(child => String(child.className || "").includes("assistant-panel"));
  const panel = panels.findLast(p => p.children.some(c => String(c.className || "").includes("assistant-error")));
  const area = panel?.children.find(c => String(c.className || "").includes("assistant-error"));
  return area?.textContent ?? null;
};

test("面板还没启动时，结果不被渲染也不抛错", () => {
  dispatched.length = 0;
  // bootAssistant 尚未跑完（WS 结果早于启动完成送达）时 actionArea/panel 都还是 undefined。
  // 此时 `if (!actionArea) return` 必须挡住后面的 panel.open 赋值，
  // 否则会在 undefined 上抛 TypeError，把整条结果处理链路带崩。
  assert.doesNotThrow(() => showAssistantResult(confirmResult));
  assert.equal(dispatched.length, 0, "未就绪时不该发出 assistant-result 事件");
});

test("preferences 还在途中时，确认门已经就绪", async () => {
  dispatched.length = 0;
  // 只挂住 preferences 这一个请求，把 bootAssistant 停在"面板已可见、preferences 还没回来"
  // 的在途空窗里。放行时让它以失败告终（ok:false），bootAssistant 就会走 catch 提前 return，
  // 走不到末尾的 setInterval——那个 5 秒定时器会让 node 进程永不退出，npm test 整条链挂住。
  let releasePrefs;
  globalThis.fetch = () => new Promise(resolve => {
    releasePrefs = () => resolve({ ok: false, status: 500, json: async () => ({ detail: '服务暂不可用' }) });
  });
  const booting = bootAssistant(); // 不 await：停在 await api('preferences/proactivity')

  serviceOnline = true;
  showAssistantResult(confirmResult);

  assert.deepEqual(gateButtons(), ["确认执行", "不执行"],
    "preferences 在途时 confirmation 结果也必须渲染出确认按钮");

  releasePrefs();
  await booting; // preferences 失败 → catch 提前 return，不建 setInterval
  installStubFetch(); // 还原标准 stub，后续测试才能逼出失败路径
});

test("任务服务启动失败后，确认门依然可达", async () => {
  dispatched.length = 0;
  serviceOnline = false;
  installStubFetch();
  await bootAssistant(); // preferences 拉取失败 → 提前 return 的错误路径

  serviceOnline = true;
  showAssistantResult(confirmResult);

  assert.deepEqual(gateButtons(), ["确认执行", "不执行"],
    "启动失败时 confirmation 结果必须渲染出确认按钮，而不是被静默丢弃");
});

test("确认门的批准按钮真的会调用确认接口", async () => {
  dispatched.length = 0;
  serviceOnline = false;
  installStubFetch();
  await bootAssistant();
  serviceOnline = true;

  const approvals = [];
  showAssistantResult(confirmResult, { confirm: async (result, approve) => { approvals.push(approve); return { text: "已处理" }; } });

  const event = dispatched.find(e => e.type === "assistant-result");
  const row = event.detail.card.children.find(child => String(child.className || "").includes("assistant-row"));
  const approveButton = row.children[0];
  await approveButton.onclick(); // 点击「确认执行」（assistant.js 的 button 用 onclick）

  assert.deepEqual(approvals, [true], "点「确认执行」必须带上 approve=true");
  // 确认成功后 refresh() 会在 tasks/reminders/inbox 还是 undefined 的错误路径上被调用；
  // 它必须自己挡住，否则 TypeError 被 button() 吞成 errorArea 文案——
  // 用户会看到一条错误，尽管确认其实已经成功。
  assert.equal(errorText(), "", "确认成功后不该留下错误文案");
});
