/* 上下文预算展示单测：node web/context-budget.test.mjs
 *
 * 覆盖 formatContextBudget 的每一段分支：护栏、关闭态、预算段、项数段、未展示段，
 * 以及三段齐全时的拼接顺序。零 DOM、零请求——usage 直接喂字面量。
 * 期望串逐字复制 context-budget.js 的输出，全角引号与间隔号一起锁住。
 */
import test from "node:test";
import assert from "node:assert/strict";

import { formatContextBudget } from "./context-budget.js";

const ON = { schema_version: 1, enabled: true };
const BUDGET = { scope: "dynamic_context", unit: "characters" };

test("护栏：usage 为 undefined / null / {} 时不出字", () => {
  assert.equal(formatContextBudget(undefined), "");
  assert.equal(formatContextBudget(null), "");
  assert.equal(formatContextBudget({}), "");
});

test("护栏：schema_version 不为 1 时不出字", () => {
  assert.equal(formatContextBudget({ schema_version: 2, enabled: true }), "");
  assert.equal(formatContextBudget({ schema_version: "1", enabled: true }), "");
  assert.equal(formatContextBudget({ schema_version: 0, enabled: false }), "");
  assert.equal(formatContextBudget({ ...ON, schema_version: undefined }), "");
});

test("护栏：enabled 不是布尔时不出字", () => {
  assert.equal(formatContextBudget({ schema_version: 1, enabled: "true" }), "");
  assert.equal(formatContextBudget({ schema_version: 1, enabled: 1 }), "");
  assert.equal(formatContextBudget({ schema_version: 1 }), "");
  assert.equal(formatContextBudget({ schema_version: 1, enabled: null }), "");
});

test("关闭记忆时给出关闭态说明，预算段不再展开", () => {
  const closed = "上一轮已关闭记忆，没有自动带入保存的记忆、历史偏好或关联资料。";
  assert.equal(formatContextBudget({ schema_version: 1, enabled: false }), closed);
  assert.equal(formatContextBudget({
    schema_version: 1,
    enabled: false,
    budget: { ...BUDGET, used_chars: 1200, max_chars: 4000, omitted_items: 3 },
    items: [{}, {}],
    omitted_usage_items: 5,
  }), closed);
});

test("开启但三段全空 -> 没有列出补充资料", () => {
  const none = "上一轮没有列出补充资料。";
  assert.equal(formatContextBudget({ ...ON }), none);
  assert.equal(formatContextBudget({ ...ON, budget: {} }), none);
  assert.equal(formatContextBudget({ ...ON, budget: null }), none);
  assert.equal(formatContextBudget({ ...ON, budget: undefined, items: undefined, omitted_usage_items: undefined }), none);
});

test("预算段：scope 与 unit 正确且 used_chars 与 max_chars 均为有效数", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000 } }),
    "上一轮：补充资料 1200 / 4000 字符。详细清单见对应消息里的“本轮参考”。");
});

test("预算段：used_chars 为 0 仍算有效", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 0, max_chars: 4000 } }),
    "上一轮：补充资料 0 / 4000 字符。详细清单见对应消息里的“本轮参考”。");
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 0, max_chars: 0 } }),
    "上一轮：补充资料 0 / 0 字符。详细清单见对应消息里的“本轮参考”。");
});

test("预算段：used_chars 为负则整段预算不展示", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: -1, max_chars: 4000 } }),
    "上一轮没有列出补充资料。");
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: -1, max_chars: 4000 }, items: [{}, {}] }),
    "上一轮：本轮列出 2 项资料索引。详细清单见对应消息里的“本轮参考”。");
});

test("预算段：used_chars 非整数同样整段不展示", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200.5, max_chars: 4000 } }),
    "上一轮没有列出补充资料。");
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: "1200", max_chars: 4000 } }),
    "上一轮没有列出补充资料。");
});

test("预算段：max_chars 非整数则整段预算不展示", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000.5 } }),
    "上一轮没有列出补充资料。");
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: "4000" } }),
    "上一轮没有列出补充资料。");
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: -8 } }),
    "上一轮没有列出补充资料。");
});

test("预算段：scope 不符则不展示", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, scope: "static_context", used_chars: 1200, max_chars: 4000 } }),
    "上一轮没有列出补充资料。");
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, scope: undefined, used_chars: 1200, max_chars: 4000 } }),
    "上一轮没有列出补充资料。");
});

test("预算段：unit 不符则不展示", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, unit: "tokens", used_chars: 1200, max_chars: 4000 } }),
    "上一轮没有列出补充资料。");
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, unit: "characters ", used_chars: 1200, max_chars: 4000 } }),
    "上一轮没有列出补充资料。");
});

test("预算段：omitted_items 大于 0 时追加省略说明", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000, omitted_items: 3 } }),
    "上一轮：补充资料 1200 / 4000 字符，因预算省略 3 项。详细清单见对应消息里的“本轮参考”。");
});

test("预算段：omitted_items 为 0 时不追加", () => {
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000, omitted_items: 0 } }),
    "上一轮：补充资料 1200 / 4000 字符。详细清单见对应消息里的“本轮参考”。");
});

test("预算段：omitted_items 非法值一律不追加", () => {
  const plain = "上一轮：补充资料 1200 / 4000 字符。详细清单见对应消息里的“本轮参考”。";
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000, omitted_items: undefined } }), plain);
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000, omitted_items: -2 } }), plain);
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000, omitted_items: 1.5 } }), plain);
  assert.equal(formatContextBudget({ ...ON, budget: { ...BUDGET, used_chars: 1200, max_chars: 4000, omitted_items: "3" } }), plain);
});

test("项数段：items 为数组时报项数，含 0 项", () => {
  assert.equal(formatContextBudget({ ...ON, items: [] }),
    "上一轮：本轮列出 0 项资料索引。详细清单见对应消息里的“本轮参考”。");
  assert.equal(formatContextBudget({ ...ON, items: [{}, {}, {}] }),
    "上一轮：本轮列出 3 项资料索引。详细清单见对应消息里的“本轮参考”。");
});

test("项数段：items 不是数组则不报", () => {
  const none = "上一轮没有列出补充资料。";
  assert.equal(formatContextBudget({ ...ON, items: undefined }), none);
  assert.equal(formatContextBudget({ ...ON, items: null }), none);
  assert.equal(formatContextBudget({ ...ON, items: "3" }), none);
  assert.equal(formatContextBudget({ ...ON, items: { length: 3 } }), none);
});

test("未展示段：omitted_usage_items 大于 0 时报未展示数", () => {
  assert.equal(formatContextBudget({ ...ON, omitted_usage_items: 5 }),
    "上一轮：另有 5 项未展示。详细清单见对应消息里的“本轮参考”。");
});

test("未展示段：omitted_usage_items 为 0 时不报", () => {
  assert.equal(formatContextBudget({ ...ON, omitted_usage_items: 0 }),
    "上一轮没有列出补充资料。");
});

test("未展示段：omitted_usage_items 非法值一律不报", () => {
  const none = "上一轮没有列出补充资料。";
  assert.equal(formatContextBudget({ ...ON, omitted_usage_items: undefined }), none);
  assert.equal(formatContextBudget({ ...ON, omitted_usage_items: -1 }), none);
  assert.equal(formatContextBudget({ ...ON, omitted_usage_items: 2.5 }), none);
  assert.equal(formatContextBudget({ ...ON, omitted_usage_items: "5" }), none);
});

test("三段齐全时按 预算、项数、未展示 顺序用 · 连接", () => {
  assert.equal(formatContextBudget({
    schema_version: 1,
    enabled: true,
    budget: { scope: "dynamic_context", unit: "characters", used_chars: 1200, max_chars: 4000, omitted_items: 3 },
    items: [{ kind: "memory", id: "m1" }, { kind: "note", id: 7 }, { kind: "task", id: "t1" }],
    omitted_usage_items: 2,
  }), "上一轮：补充资料 1200 / 4000 字符，因预算省略 3 项 · 本轮列出 3 项资料索引 · 另有 2 项未展示。详细清单见对应消息里的“本轮参考”。");
});

test("缺预算段时，项数与未展示仍按该顺序用 · 连接", () => {
  assert.equal(formatContextBudget({
    schema_version: 1,
    enabled: true,
    budget: { scope: "dynamic_context", unit: "characters", used_chars: -1, max_chars: 4000 },
    items: [{}, {}],
    omitted_usage_items: 4,
  }), "上一轮：本轮列出 2 项资料索引 · 另有 4 项未展示。详细清单见对应消息里的“本轮参考”。");
});
