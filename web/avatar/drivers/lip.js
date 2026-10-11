/* 口型驱动：按真实音频时钟采样 viseme 时间轴。
 *
 * 播放时记录 (AudioContext.currentTime, timeline)，之后每帧只用时钟差采样，
 * 不依赖 setTimeout —— setTimeout 在标签页被节流时会和声音对不上。
 *
 * 音频包络（RMS）只做"微调制"：给张口幅度一点起伏和抖动，让嘴活起来。
 * 闭塞音（open < 0.05）不调制，否则 b/p/m 的闭合会被音量糊掉。
 */

/* 有 analyser 时做"微调制"：给张口幅度一点起伏和抖动，让嘴活起来。
 * 闭塞音（open < 0.05）不调制，否则 b/p/m 的闭合会被音量糊掉。
 *
 * 注意：没有挂 analyser（静音 TTS、测试驱动）时 active=false，必须完全
 * 跳过调制。之前这里无条件乘一个 <1 的系数，导致没有音频分析的场合嘴
 * 永久偏小 38%。 */

import { clamp } from "../params.js";
import { sampleTimeline } from "../timeline.js";

/* wide -> ParamMouthForm(-1 撅 / +1 咧) 的映射区间 */
const WIDE_CENTER = 0.54;
const WIDE_SPAN = 0.34;

/* TTS 输出的 RMS 经 ×5 归一后，讲话时典型落在 0.65 附近 */
const RMS_FLOOR = 0.65;
const RMS_GAIN = 0.45;

export class LipDriver {
  constructor() {
    this.timeline = null;
    this.startedAt = 0;
    this.duration = 0;
    this.speaking = false;
    this.rms = 0;
    this.hasEnvelope = false;
    this._smoothRms = 0;
  }

  /* timeline 已经在外部按 chunk 真实时长构建好了 */
  speak(timeline, startedAt) {
    this.timeline = timeline;
    this.startedAt = startedAt;
    this.duration = timeline.duration;
    this.speaking = true;
  }

  stop() {
    this.speaking = false;
    this.timeline = null;
  }

  setEnvelope(rms, active) {
    this.rms = clamp(rms);
    this.hasEnvelope = !!active;
  }

  /* ctx: AudioContext，用于取播放位置。返回 null 表示这一帧不碰嘴。 */
  sample(ctx) {
    if (!this.speaking || !this.timeline) return null;
    const t = ctx.currentTime - this.startedAt;
    if (t >= this.duration) {
      // 交给渲染线程收尾，这里不再独占 ParamMouthOpenY
      this.speaking = false;
      return null;
    }
    if (t < 0) return null;

    const frame = sampleTimeline(this.timeline, t);
    if (!frame) return null;

    this._smoothRms += (this.rms - this._smoothRms) * 0.35;

    let open = frame.open;
    if (this.hasEnvelope) {
      const delta = this._smoothRms - RMS_FLOOR;
      const energy = clamp(1 + RMS_GAIN * delta, 0.7, 1.25);
      if (open > 0.05) {
        open = open * energy + delta * 0.06;
      } else {
        // 闭塞音：保留闭合，只留一点点唇缝颤动
        open += delta * 0.03;
      }
    }

    return {
      open: clamp(open, 0, 1),
      form: clamp((frame.wide - WIDE_CENTER) / WIDE_SPAN, -1, 1),
      round: clamp((frame.round - 0.42) / 0.52, -1, 1),
      // 共发音：张大嘴时眼睛会跟着眯一点。只在开口很大时起效，
      // 小幅口型保持原样，否则整句话都在眯眼。
      squint: Math.max(0, clamp(open, 0, 1) - 0.55) * 0.55,
    };
  }
}