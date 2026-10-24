/* 本轮参考清单守卫单测：node web/context-usage.test.mjs
 *
 * 覆盖 context-usage.js 里两个纯函数：validId（资料 id 合法性）与 validCount（计数合法性），
 * 以及 appendContextUsage 的护栏段——四个条件任一不满足就 return null。
 *
 * 只测护栏段，不测放行后的渲染：护栏一过函数立刻 document.createElement / 展开 container.children，
 * node 环境没有 DOM，跑不动（会抛 TypeError / ReferenceError）。
 */
import test from "node:test";
import assert from "node:assert/strict";

import { appendContextUsage, validCount, validId } from "./context-usage.js";

const NUL = "\0";

test("validId：接受普通字符串", () => {
  assert.ok(validId("m1"));
  assert.ok(validId("memory-2026-10-21"));
  assert.ok(validId(" 前后空白会被 trim，仍算合法 "));
  assert.ok(validId("笔记"));
});

test("validId：256 字符上限放行，257 拒绝", () => {
  assert.ok(validId("a".repeat(256)));
  assert.equal(validId("a".repeat(257)), false);
  assert.equal(validId("a".repeat(300)), false);
});

test("validId：拒绝空串与纯空白", () => {
  assert.equal(validId(""), false);
  assert.equal(validId(" "), false);
  assert.equal(validId("   "), false);
  assert.equal(validId("\t\n "), false);
});

test("validId：拒绝含 NUL 的字符串", () => {
  assert.equal(validId(`a${NUL}b`), false);
  assert.equal(validId(NUL), false);
  assert.equal(validId(`${NUL}`), false);
});

test("validId：接受正整数", () => {
  assert.equal(validId(1), true);
  assert.equal(validId(7), true);
  assert.equal(validId(Number.MAX_SAFE_INTEGER), true);
});

test("validId：拒绝 0、负数与小数", () => {
  assert.equal(validId(0), false);
  assert.equal(validId(-0), false);
  assert.equal(validId(-1), false);
  assert.equal(validId(-999), false);
  assert.equal(validId(1.5), false);
  assert.equal(validId(0.5), false);
  assert.equal(validId(-0.5), false);
});

test("validId：拒绝 null / undefined / NaN", () => {
  assert.equal(validId(null), false);
  assert.equal(validId(undefined), false);
  assert.equal(validId(NaN), false);
});

test("validId：拒绝其他类型", () => {
  assert.equal(validId(true), false);
  assert.equal(validId({}), false);
  assert.equal(validId([]), false);
  assert.equal(validId(Infinity), false);
});

test("validCount：接受 0 与正整数", () => {
  assert.equal(validCount(0), true);
  assert.equal(validCount(1), true);
  assert.equal(validCount(32), true);
  assert.equal(validCount(Number.MAX_SAFE_INTEGER), true);
});

test("validCount：拒绝负数", () => {
  assert.equal(validCount(-1), false);
  assert.equal(validCount(-0.5), false);
});

test("validCount：拒绝非整数", () => {
  assert.equal(validCount(1.5), false);
  assert.equal(validCount(0.1), false);
});

test("validCount：拒绝 NaN 与 Infinity", () => {
  assert.equal(validCount(NaN), false);
  assert.equal(validCount(Infinity), false);
  assert.equal(validCount(-Infinity), false);
});

test("validCount：拒绝非数字类型", () => {
  assert.equal(validCount("3"), false);
  assert.equal(validCount(""), false);
  assert.equal(validCount(null), false);
  assert.equal(validCount(undefined), false);
  assert.equal(validCount(true), false);
  assert.equal(validCount({}), false);
});

test("validCount：Number.MAX_SAFE_INTEGER 放行，+1 拒绝", () => {
  assert.equal(validCount(Number.MAX_SAFE_INTEGER), true);
  assert.equal(validCount(Number.MAX_SAFE_INTEGER + 1), false);
  assert.equal(validCount(Number.MAX_SAFE_INTEGER + 2), false);
});

test("appendContextUsage 护栏：container 为空直接返回 null", () => {
  assert.equal(appendContextUsage(null, { schema_version: 1, enabled: true }), null);
  assert.equal(appendContextUsage(undefined, { schema_version: 1, enabled: true }), null);
});

test("appendContextUsage 护栏：usage 为空直接返回 null", () => {
  assert.equal(appendContextUsage({}, null), null);
  assert.equal(appendContextUsage({}, undefined), null);
  assert.equal(appendContextUsage({}, {}), null);
});

test("appendContextUsage 护栏：schema_version 不是 1 直接返回 null", () => {
  assert.equal(appendContextUsage({}, { schema_version: 2, enabled: true }), null);
  assert.equal(appendContextUsage({}, { schema_version: "1", enabled: true }), null);
  assert.equal(appendContextUsage({}, { enabled: true }), null);
});

test("appendContextUsage 护栏：enabled 不是布尔直接返回 null", () => {
  assert.equal(appendContextUsage({}, { schema_version: 1, enabled: "true" }), null);
  assert.equal(appendContextUsage({}, { schema_version: 1, enabled: 1 }), null);
  assert.equal(appendContextUsage({}, { schema_version: 1 }), null);
});
