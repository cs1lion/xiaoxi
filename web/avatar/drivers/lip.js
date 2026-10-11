/* 口型驱动：按真实音频时钟采样 viseme 时间轴。
 *
 * 播放时记录 (AudioContext.currentTime, timeline)，之后每帧只用时钟差采样，
 * 不依赖 setTimeout —— setTimeout 在标签页被节流时会和声音对不上。
 *
 * 音频包络（RMS）只做"微调制"：给张口幅度一点起伏和抖动，让嘴活起来。
 * 闭塞音（open < 0.05）不调制，否则 b/p/m 的闭合会被音量糊掉。
 */

import { clamp } from "../params.js";
import { sampleTimeline } from "../timeline.js";

/* wide -> ParamMouthForm(-1 撅 / +1 咧) 的映射区间 */
const WIDE_CENTER = 0.54;
const WIDE_SPAN = 0.34;

export class LipDriver {
  constructor() {
    this.timeline = null;
    this.startedAt = 0;
    this.duration = 0;
    this.speaking = false;
    this.rms = 0;
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

  setRms(value) {
    this.rms = clamp(value);
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

    // 包络跟着音频走，做平滑避免频谱抖动
    this._smoothRms += (this.rms - this._smoothRms) * 0.35;

    let open = frame.open;
    if (open > 0.05) {
      const energy = 0.62 + 0.55 * this._smoothRms;
      open = open * energy + this._smoothRms * 0.05;
    } else {
      // 闭塞音：保留闭合，只留一点点唇缝颤动
      open += this._smoothRms * 0.012;
    }

    return {
      open: clamp(open, 0, 1),
      form: clamp((frame.wide - WIDE_CENTER) / WIDE_SPAN, -1, 1),
      round: clamp((frame.round - 0.42) / 0.52, -1, 1),
    };
  }
}