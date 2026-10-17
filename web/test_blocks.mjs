/* 动态积木纯函数单测（协议 §23）：parseCmd 白名单语法 + summarizeAqiBlocks 信封校验。
 * 模块经 data: URL 以 ESM 加载（与 test_glimmer_bubble.mjs 同模式），零依赖。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const load = async (name) => import(`data:text/javascript;base64,${Buffer.from(
  await readFile(new URL(name, import.meta.url))).toString("base64")}`);
const { parseCmd, summarizeAqiBlocks, AQI_BLOCK_KINDS } = await load("./blocks.js");

test("七种积木 kind 与协议 §23.2 对齐（RENDERERS 一个不少）", () => {
  assert.deepEqual(AQI_BLOCK_KINDS, [
    "choice_pills", "spotlight_card", "alert_card",
    "swimlane_strip", "metric_flame", "note_snippet", "memo_note",
  ]);
});

test("parseCmd 只认 <verb>:<payload>，未知形态 verb 为空", () => {
  assert.deepEqual(parseCmd("dispatch:accept:c-42"), { verb: "dispatch", payload: "accept:c-42" });
  assert.deepEqual(parseCmd("ui:preview:report.md"), { verb: "ui", payload: "preview:report.md" });
  assert.deepEqual(parseCmd("say:那你说来听听"), { verb: "say", payload: "那你说来听听" });
  assert.deepEqual(parseCmd("nocolon"), { verb: "", payload: "" });
  assert.deepEqual(parseCmd(":leading"), { verb: "", payload: "" });
  assert.deepEqual(parseCmd(""), { verb: "", payload: "" });
  assert.deepEqual(parseCmd(null), { verb: "", payload: "" });
});

test("summarizeAqiBlocks 校验信封并统计可渲染块", () => {
  const summary = summarizeAqiBlocks({
    type: "aqi_blocks", layout: "stage_holo", nav: "ops", gaze: "holo_right",
    blocks: [
      { kind: "choice_pills", question: "选哪单？", options: [{ label: "A", cmd: "dispatch:accept:1" }] },
      { kind: "mystery" },
      { kind: "spotlight_card", title: "周报" },
    ],
  });
  assert.deepEqual(summary, { count: 2, layout: "stage_holo", nav: "ops", gaze: "holo_right", source: "chat" });
});

test("summarizeAqiBlocks 对坏信封/空块安全降级", () => {
  assert.equal(summarizeAqiBlocks(null), null);
  assert.equal(summarizeAqiBlocks({ blocks: [] }), null);
  assert.equal(summarizeAqiBlocks({ blocks: [{ kind: "mystery" }] }), null);
  assert.equal(summarizeAqiBlocks({ blocks: "nope" }), null);
  const fallback = summarizeAqiBlocks({ layout: "bogus", nav: "bogus", gaze: "bogus", blocks: [{ kind: "note_snippet", text: "x" }] });
  assert.deepEqual(fallback, { count: 1, layout: "stage_holo", nav: null, gaze: null, source: "chat" });
});

/* 契约漂移护栏：AQI_BLOCK_KINDS 与 blocks.js 内部的 RENDERERS 必须一一对应。
 * 曾经前者漏了 memo_note 而后者有——导出的常量少一个，前端就把那种积木
 * 静默丢成空卡，而 check_protocol.py 只做全文子串匹配，看不见这个缺口。 */
test("导出的 kind 清单与真实渲染器一一对应（一个都不能少）", () => {
  const minimal = {
    choice_pills: { kind: "choice_pills", question: "选哪单？", options: [{ label: "甲", cmd: "dispatch:accept:c1" }] },
    spotlight_card: { kind: "spotlight_card", title: "周报" },
    alert_card: { kind: "alert_card", title: "熔断了" },
    swimlane_strip: { kind: "swimlane_strip", lanes: [{ agent_id: "scribe" }] },
    metric_flame: { kind: "metric_flame", metrics: [{ label: "调用", value: 3 }] },
    note_snippet: { kind: "note_snippet", text: "摘录" },
    memo_note: { kind: "memo_note", title: "手头清净", body: "该收的都收了工。" },
  };
  for (const kind of AQI_BLOCK_KINDS) {
    assert.ok(minimal[kind], `${kind} 忘了给最小可用样例`);
    const summary = summarizeAqiBlocks({ blocks: [minimal[kind]] });
    assert.equal(summary?.count, 1, `${kind} 在 RENDERERS 里没有对应渲染器（契约漂移）`);
  }
  // 反向：清单里没有的 kind 不该被算作可渲染。
  assert.equal(summarizeAqiBlocks({ blocks: [{ kind: "not_a_kind", text: "x" }] }), null);
});
