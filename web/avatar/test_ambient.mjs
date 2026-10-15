/* V2 舞台驱动单测（协议 §16）：micro_act 解析/降级/去重、触摸区域台词选择。
 * ambient.js 零 avatar 导入，经 data: URL 以 ESM 加载。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const load = async (name) => import(`data:text/javascript;base64,${Buffer.from(
  await readFile(new URL(name, import.meta.url))).toString("base64")}`);
const { resolveMicroAct, pickTouchLine, AmbientDriver } = await load("./drivers/ambient.js");

const SAMPLES = { stage_samples: {
  touch_head: ["摸头A", "摸头B"],
  touch_face: ["戳脸A"],
} };

test("resolveMicroAct maps known actions and degrades unknown", () => {
  assert.deepEqual(resolveMicroAct("tilt_head"), { kind: "pose", tilt: 0.25, duration: 0.8 });
  const fallback = resolveMicroAct("does_not_exist");
  assert.equal(fallback.kind, "micro");
  assert.ok(resolveMicroAct(undefined).kind);
});

test("pickTouchLine selects by region and degrades safely", () => {
  assert.equal(pickTouchLine(SAMPLES, "head", () => 0.0), "摸头A");
  assert.equal(pickTouchLine(SAMPLES, "head", () => 0.99), "摸头B");
  assert.equal(pickTouchLine(SAMPLES, "face", () => 0.5), "戳脸A");
  assert.equal(pickTouchLine(SAMPLES, "elbow", () => 0.5), "");
  assert.equal(pickTouchLine({ stage_samples: {} }, "head", () => 0.5), "");
  assert.equal(pickTouchLine(null, "head", () => 0.5), "");
});

test("AmbientDriver applies micro_act and dedups by event_id", () => {
  const applied = [];
  const driver = new AmbientDriver({ applyMicroAct: (d) => applied.push(d.kind) });
  assert.equal(driver.handleEvent({ type: "micro_act", event_id: "e1", action: "stretch" }), true);
  assert.equal(driver.handleEvent({ type: "micro_act", event_id: "e1", action: "stretch" }), false);
  assert.deepEqual(applied, ["pose"]);
});

test("AmbientDriver degrades unknown action without throwing", () => {
  const applied = [];
  const driver = new AmbientDriver({ applyMicroAct: (d) => applied.push(d.kind) });
  assert.equal(driver.handleEvent({ type: "micro_act", event_id: "e2", action: "mystery" }), true);
  assert.deepEqual(applied, ["micro"]);
});

test("AmbientDriver ignores non-micro_act events", () => {
  const driver = new AmbientDriver({ applyMicroAct: () => assert.fail("should not apply") });
  assert.equal(driver.handleEvent({ type: "mumble", text: "x" }), false);
  assert.equal(driver.handleEvent(null), false);
});

test("AmbientDriver.touch returns a line and fires the callback", () => {
  const seen = [];
  const driver = new AmbientDriver({ samples: SAMPLES, onTouchLine: (r, line) => seen.push([r, line]) });
  assert.equal(driver.touch("face"), "戳脸A");
  assert.deepEqual(seen, [["face", "戳脸A"]]);
  assert.equal(driver.touch("elbow"), "");
});
