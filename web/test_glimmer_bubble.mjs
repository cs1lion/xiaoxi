/* V2 微光气泡状态机单测（协议 §16.2）。纯模块，经 data: URL 以 ESM 加载。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const load = async (name) => import(`data:text/javascript;base64,${Buffer.from(
  await readFile(new URL(name, import.meta.url))).toString("base64")}`);
const { createGlimmer } = await load("./glimmer_bubble.js");

const mumble = (over = {}) => ({ type: "mumble", event_id: "m1", text: "这行能更干净点……",
  source_refs: ["reflection:r1"], expires_at: 1000, ...over });

test("show makes a valid mumble visible and dedups by event_id", () => {
  const g = createGlimmer();
  assert.equal(g.show(mumble()), true);
  assert.equal(g.status, "visible");
  assert.equal(g.show(mumble()), false); // same event_id -> no re-pop
});

test("empty/invalid mumble never shows", () => {
  const g = createGlimmer();
  assert.equal(g.show({ event_id: "x", text: "   " }), false);
  assert.equal(g.show(null), false);
  assert.equal(g.status, "hidden");
});

test("expand carries the real context for接话", () => {
  const seen = [];
  const g = createGlimmer({ onExpand: (ctx) => seen.push(ctx) });
  g.show(mumble());
  const ctx = g.expand();
  assert.deepEqual(ctx, { text: "这行能更干净点……", source_refs: ["reflection:r1"] });
  assert.equal(g.status, "expanded");
  assert.deepEqual(seen, [ctx]);
});

test("dismiss clears and is idempotent", () => {
  const g = createGlimmer();
  g.show(mumble());
  assert.equal(g.dismiss(), true);
  assert.equal(g.status, "hidden");
  assert.equal(g.current, null);
  assert.equal(g.dismiss(), false);
});

test("poll expires naturally past expires_at", () => {
  const expired = [];
  const g = createGlimmer({ onExpire: (e) => expired.push(e) });
  g.show(mumble({ expires_at: 1000 }));
  assert.equal(g.poll(999), false);
  assert.equal(g.status, "visible");
  assert.equal(g.poll(1000), true);
  assert.equal(g.status, "hidden");
  assert.equal(expired.length, 1);
  assert.equal(expired[0].text, "这行能更干净点……");
});

test("update refreshes the same bubble and can extend its life", () => {
  const g = createGlimmer();
  assert.equal(g.show(mumble()), true);
  assert.equal(g.update("换了半句", 5000), true);
  assert.equal(g.current.text, "换了半句");
  assert.ok(g.current.expires_at > 1000, "ttl 顺延：口头微光说完才淡出");
  assert.equal(g.update("   "), false, "空文本不刷新");
  assert.equal(g.current.text, "换了半句");
});

test("update on a hidden bubble is a no-op", () => {
  const g = createGlimmer();
  assert.equal(g.update("看不见"), false);
  assert.equal(g.status, "hidden");
});

test("show normalizes malformed event fields instead of passing them through", () => {
  // source_refs 非数组、expires_at 非 number、event_id 缺省——都要降级成安全形状，
  // 否则畸形 current 会被 expand/poll 当成真数据用（静默失效）。
  const g = createGlimmer();
  assert.equal(g.show({ text: "一句话", source_refs: "not-an-array", expires_at: "soon" }), true);
  assert.equal(g.current.event_id, null);
  assert.deepEqual(g.current.source_refs, []);
  assert.equal(g.current.expires_at, null);
  // 规范化后的 expires_at=null 让 poll 成为空操作，不会误判到期。
  assert.equal(g.poll(Date.now() + 10 ** 9), false);
  assert.equal(g.status, "visible");
});

test("expand is refused unless a bubble is actually visible", () => {
  // hidden 时没有 current 可展开；expanded 是终态里的展示态，不能从 hidden 直达。
  const hidden = createGlimmer();
  assert.equal(hidden.expand(), null);
  assert.equal(hidden.status, "hidden");
  const g = createGlimmer();
  g.show(mumble());
  g.expand();
  assert.equal(g.status, "expanded");
  // 已展开后再 expand 不重复回调、状态不变。
  assert.equal(g.expand(), null);
  assert.equal(g.status, "expanded");
});

test("missing callbacks never throw", () => {
  // app.js 之外（测试、未来的非 DOM 调用方）可能一个回调都不传。
  const g = createGlimmer();
  g.show(mumble());
  assert.doesNotThrow(() => g.expand());
  assert.doesNotThrow(() => g.update("再换一句", 1000));
  assert.doesNotThrow(() => g.poll(10 ** 12));
  assert.doesNotThrow(() => g.dismiss());
});
