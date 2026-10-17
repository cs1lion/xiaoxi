/* 协议 §23.6 心流防打扰：aqi_blocks 待阅队列单测：node web/aqi-flow.test.mjs
 *
 * 覆盖"她在打字时绝不抢焦点弹卡"这条主观闭环：只挂起、只报数、停手后原序补弹。
 * 零 DOM、零计时等待——now 全可注入。
 */
import test from "node:test";
import assert from "node:assert/strict";

import { AQI_QUIET_MS, aqiPendingMessage, createAqiFlow } from "./aqi-flow.js";

const MSG = (id) => ({ type: "aqi_blocks", layout: "stage_holo", blocks: [{ kind: "note_snippet", text: id }] });

test("刚打过字 -> 安静，消息挂起不弹", () => {
  const flow = createAqiFlow({ now: () => 10_000 });
  flow.markTyping(9_500);
  assert.equal(flow.isQuiet(10_000), true);
  assert.equal(flow.hold(MSG("a")), 1);
  assert.equal(flow.count, 1);
});

test("停下超过静默窗 -> 可以弹，按原序补弹", () => {
  const flow = createAqiFlow({ now: () => 10_000 });
  flow.markTyping(10_000 - AQI_QUIET_MS - 500);
  assert.equal(flow.isQuiet(10_000), false);
  flow.hold(MSG("a"));
  flow.hold(MSG("b"));
  assert.deepEqual(flow.drain(), [MSG("a"), MSG("b")]);
  assert.equal(flow.count, 0);
});

test("边界：正好等于静默窗的那一瞬不算安静", () => {
  const flow = createAqiFlow({ quietMs: 1000, now: () => 5_000 });
  flow.markTyping(4_000);
  assert.equal(flow.isQuiet(5_000), false);
});

test("一次都没打字 -> 不安静，不该挂起", () => {
  const flow = createAqiFlow({ now: () => 10_000 });
  assert.equal(flow.isQuiet(10_000), false);
  assert.equal(flow.count, 0);
});

test("写作宽屏视图里一律安静", () => {
  const flow = createAqiFlow({ now: () => 10_000, isWriting: () => true });
  assert.equal(flow.isQuiet(10_000), true);
  flow.markTyping(0);
  assert.equal(flow.isQuiet(999_999), true);
});

test("挂起的消息内容原样保留：不改写、不重排、不丢字段", () => {
  const flow = createAqiFlow({ now: () => 0 });
  const first = MSG("first");
  const second = { type: "aqi_blocks", layout: "drawer_ops", nav: "ops", gaze: "drawer", source: "executive", blocks: [] };
  flow.hold(first);
  flow.hold(second);
  const drained = flow.drain();
  assert.equal(drained[0], first, "同一引用，未被拷贝改写");
  assert.deepEqual(drained[1], second, "信封字段一个没少");
});

test("安静时只报数不催，停手后才请她翻", () => {
  assert.equal(aqiPendingMessage(2, true), "她递了 2 张卡。你在忙，我先不出声，灯给你留着。");
  assert.equal(aqiPendingMessage(2, false), "2 张卡等你翻。");
  assert.equal(aqiPendingMessage(0, true), "");
  assert.equal(aqiPendingMessage(0, false), "");
});

test("reset 之后回到初始状态，待阅队列一并清空", () => {
  const flow = createAqiFlow({ now: () => 10_000 });
  flow.markTyping(9_999);
  flow.hold(MSG("a"));
  assert.equal(flow.isQuiet(10_000), true);
  flow.reset();
  assert.equal(flow.isQuiet(10_000), false);
  assert.equal(flow.count, 0);
  assert.deepEqual(flow.drain(), []);
});

test("drain 之后再次挂起从 1 重新计数", () => {
  const flow = createAqiFlow({ now: () => 0, quietMs: 10 });
  flow.hold(MSG("a"));
  flow.hold(MSG("b"));
  flow.drain();
  assert.equal(flow.hold(MSG("c")), 1);
  assert.equal(flow.count, 1);
});
