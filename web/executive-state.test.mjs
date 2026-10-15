/* 心流防打扰单测：node web/executive-state.test.mjs
 *
 * 覆盖"用户在打字时她绝不瞎吵吵"这条主观闭环：只点亮胶囊呼吸灯、静默不播报。
 */
import test from "node:test";
import assert from "node:assert/strict";

import { QUIET_MS, createFlowGuard, pendingMessage, shouldHighlight } from "./executive-state.js";

test("刚打过字 -> 安静", () => {
  const guard = createFlowGuard({ now: () => 10_000 });
  guard.markTyping(9_500);
  assert.equal(guard.isQuiet(10_000), true);
});

test("停下超过静默窗 -> 可以开口", () => {
  const guard = createFlowGuard({ now: () => 10_000 });
  guard.markTyping(10_000 - QUIET_MS - 500);
  assert.equal(guard.isQuiet(10_000), false);
});

test("边界：正好等于静默窗的那一瞬不算安静", () => {
  const guard = createFlowGuard({ quietMs: 1000, now: () => 5_000 });
  guard.markTyping(4_000);
  assert.equal(guard.isQuiet(5_000), false);
});

test("一次都没打字 -> 不安静", () => {
  const guard = createFlowGuard({ now: () => 10_000 });
  assert.equal(guard.isQuiet(10_000), false);
});

test("写作宽屏视图里一律安静", () => {
  const guard = createFlowGuard({ now: () => 10_000, isWriting: () => true });
  assert.equal(guard.isQuiet(10_000), true);
  guard.markTyping(0);
  assert.equal(guard.isQuiet(999_999), true);
});

test("reset 之后回到初始状态", () => {
  const guard = createFlowGuard({ now: () => 10_000 });
  guard.markTyping(9_999);
  assert.equal(guard.isQuiet(10_000), true);
  guard.reset();
  assert.equal(guard.isQuiet(10_000), false);
});

test("markTyping 可显式给时间戳，测试不必等真实时间", () => {
  const guard = createFlowGuard({ now: () => 0 });
  assert.equal(guard.markTyping(4_242), 4_242);
  assert.equal(guard.isQuiet(4_242 + QUIET_MS - 1), true);
});

test("安静时只报数不出声", () => {
  assert.equal(pendingMessage(2, true), "2 单结了。你在忙，我先不出声，灯给你留着。");
  assert.equal(pendingMessage(2, false), "2 单等你验收。");
  assert.equal(pendingMessage(0, true), "");
  assert.equal(pendingMessage(0, false), "");
});

test("安静时卡片不高亮，避免视觉上也在催", () => {
  assert.equal(shouldHighlight(true), false);
  assert.equal(shouldHighlight(false), true);
});
