/* 语音交付状态的「用户可见提示」纯逻辑。
 *
 * 为什么单独抽出来：web/app.js 的 done 分支原来只在 !VOICE_FIRST（有聊天气泡）
 * 路径把 audio_status 的降级说明 append 成 <small>；VOICE_FIRST 下气泡注释
 * 整段不跑，字幕（#subtitle）是唯一出口，于是「明明开了语音却没声音」时用户
 * 得不到任何解释（2026-11-06 交接 §七列为遗留）。
 *
 * 把两套提示收敛到同一份规则：deliveryNotes() 给气泡逐条列出，
 * voiceFirstNotice() 给字幕一句话。这样以后改文案只需改一处，不会两边漂移。
 * 纯函数、零 DOM、零协议字段——只读既有的 done.delivery.*。
 */

export const NOTE = Object.freeze({
  limit_reached: "本轮已达长度上限，可能尚未展开完整；可以让我继续。",
  text_partial: "生成中断，这里只保留了部分回答。",
  audio_none: "本轮没有语音输出（语音未启用或使用了静默引擎），请阅读文字。",
  audio_failed: "部分或全部语音合成失败，请阅读文字。",
});

/** 有聊天气泡模式：返回要逐条 append 的说明（可能为空数组）。 */
export function deliveryNotes(delivery) {
  if (!delivery || typeof delivery !== "object") return [];
  const notes = [];
  if (delivery.text_status === "limit_reached") notes.push(NOTE.limit_reached);
  if (delivery.text_status === "partial") notes.push(NOTE.text_partial);
  if (delivery.audio_status === "none") notes.push(NOTE.audio_none);
  if (delivery.audio_status === "partial" || delivery.audio_status === "failed") {
    notes.push(NOTE.audio_failed);
  }
  return notes;
}

/** VOICE_FIRST 字幕模式：只报音频侧降级，文字侧由文本本身可见；无降级返回 null。 */
export function voiceFirstNotice(delivery) {
  if (!delivery || typeof delivery !== "object") return null;
  if (delivery.audio_status === "none") return NOTE.audio_none;
  if (delivery.audio_status === "partial" || delivery.audio_status === "failed") {
    return NOTE.audio_failed;
  }
  return null;
}
