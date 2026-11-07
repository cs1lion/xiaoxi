/* delivery-notice 纯逻辑单测（零 DOM、零服务、零 Key）。
 * 钉住两件事：① 降级状态 → 文案的映射不漂移；② 气泡与字幕两套出口同源，
 * 免得以后只改一边，VOICE_FIRST 用户又收不到解释。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { NOTE, deliveryNotes, voiceFirstNotice } from "./delivery-notice.js";

test("deliveryNotes 覆盖四种降级与组合", () => {
  assert.deepEqual(deliveryNotes({}), []);
  assert.deepEqual(deliveryNotes({ audio_status: "synthesized" }), []);
  assert.deepEqual(deliveryNotes({ audio_status: "none" }), [NOTE.audio_none]);
  assert.deepEqual(deliveryNotes({ audio_status: "partial" }), [NOTE.audio_failed]);
  assert.deepEqual(deliveryNotes({ audio_status: "failed" }), [NOTE.audio_failed]);
  assert.deepEqual(deliveryNotes({ text_status: "limit_reached" }), [NOTE.limit_reached]);
  assert.deepEqual(deliveryNotes({ text_status: "partial" }), [NOTE.text_partial]);
  assert.deepEqual(
    deliveryNotes({ text_status: "partial", audio_status: "failed" }),
    [NOTE.text_partial, NOTE.audio_failed],
    "组合时两条都要给，且顺序稳定",
  );
});

test("voiceFirstNotice 只报音频侧降级", () => {
  assert.equal(voiceFirstNotice(null), null);
  assert.equal(voiceFirstNotice(undefined), null);
  assert.equal(voiceFirstNotice({}), null);
  assert.equal(voiceFirstNotice({ audio_status: "synthesized" }), null);
  assert.equal(voiceFirstNotice({ text_status: "limit_reached" }), null,
    "文字侧降级由文本本身可见，不该占字幕");
  assert.equal(voiceFirstNotice({ audio_status: "none" }), NOTE.audio_none);
  assert.equal(voiceFirstNotice({ audio_status: "partial" }), NOTE.audio_failed);
  assert.equal(voiceFirstNotice({ audio_status: "failed" }), NOTE.audio_failed);
});

test("两套出口对音频降级必须同源", () => {
  for (const status of ["none", "partial", "failed"]) {
    const notice = voiceFirstNotice({ audio_status: status });
    assert.ok(notice, `${status} 在 VOICE_FIRST 下必须有提示`);
    assert.ok(deliveryNotes({ audio_status: status }).includes(notice),
      `${status} 的气泡文案与字幕文案不同源`);
  }
});

test("app.js 两套出口都接了同一个模块（防接线被删）", () => {
  const src = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  assert.match(src, /import \{ deliveryNotes, voiceFirstNotice \} from ['"]\.\/delivery-notice\.js['"]/);
  assert.match(src, /voiceFirstNotice\(msg\.delivery\)/, "VOICE_FIRST 字幕分支丢了");
  assert.match(src, /const notes = deliveryNotes\(msg\.delivery\)/, "气泡分支丢了");
});

test("畸形输入不抛也不误报", () => {
  for (const bad of [null, undefined, 0, "none", [], { audio_status: null },
                     { audio_status: 123 }, { audio_status: "NO" }]) {
    assert.doesNotThrow(() => deliveryNotes(bad));
    assert.doesNotThrow(() => voiceFirstNotice(bad));
    assert.equal(voiceFirstNotice(bad), null);
  }
});
