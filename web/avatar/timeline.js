/* 分句文本 -> 带时间轴的口型序列。
 *
 * 关键点：我们知道这句音频的真实时长（decodeAudioData 之后的 buffer.duration），
 * 而口型的相对权重来自文本。两者相除就把整句摊平到真实时间上，
 * 所以不需要 TTS 提供任何音素时间戳。
 *
 * 这是 text-driven 路线的核心，也是比纯音频 MFCC 分类更适合中文的地方：
 * 中文的口型本来就是由已知字形决定的，没必要再从波形里猜。
 */

import { segmentsFor } from "./viseme.js";

/* edge-tts 开头/结尾通常各有一小段静音，别让嘴在静音里张开。 */
const HEAD_TRIM = 0.06;
const TAIL_TRIM = 0.05;

export function buildTimeline(text, durationSec) {
  const spans = [];
  let total = 0;

  for (const char of text || "") {
    const { parts, weight, pause } = segmentsFor(char);
    if (!parts.length && !pause) continue;
    const scale = pause || 1;
    for (const part of parts) {
      const w = part.weight * weight * scale;
      spans.push({ shape: part.shape, weight: w });
      total += w;
    }
  }

  const duration = Math.max(0.12, durationSec || 0.12);
  if (!spans.length || total <= 0) {
    return { segments: [{ t0: 0, t1: duration, open: 0, wide: 0.5, round: 0.5 }], duration };
  }

  const usable = duration * (1 - HEAD_TRIM - TAIL_TRIM);
  const unit = usable / total;
  const segments = [];
  let t = duration * HEAD_TRIM;

  for (const span of spans) {
    const dt = span.weight * unit;
    // 极短的段直接跳过，否则高频音素会让嘴抽搐
    if (dt < 0.018) continue;
    segments.push({
      t0: t,
      t1: t + dt,
      open: span.shape.open,
      wide: span.shape.wide,
      round: span.shape.round,
    });
    t += dt;
  }

  if (!segments.length) {
    segments.push({ t0: 0, t1: duration, open: 0, wide: 0.5, round: 0.5 });
  }
  return { segments, duration };
}

/* 在 t 时刻采样：返回相邻两段之间的线性插值。
 * 插值而不是硬切，口型过渡才不会出现"跳帧"。 */
export function sampleTimeline(timeline, t) {
  const segs = timeline.segments;
  if (!segs.length) return null;
  if (t <= segs[0].t0) return segs[0];
  const last = segs[segs.length - 1];
  if (t >= last.t1) return last;

  // 时间轴有序，二分
  let lo = 0;
  let hi = segs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].t0 <= t) lo = mid; else hi = mid;
  }
  const a = segs[lo];
  const b = segs[hi];
  const span = b.t0 - a.t1;
  // 段与段之间的缝隙是闭合的（闭塞音的爆破、字间过渡）
  if (span > 1e-6) {
    if (t <= a.t1) {
      const k = 1 - (a.t1 - t) / span;
      return { open: a.open * (1 - k), wide: a.wide, round: a.round };
    }
    const k = (t - a.t1) / span;
    return { open: b.open * k, wide: b.wide, round: b.round };
  }
  const span2 = b.t1 - b.t0 || 1e-6;
  const k = Math.max(0, Math.min(1, (t - a.t1) / span2));
  return {
    open: a.open + (b.open - a.open) * k,
    wide: a.wide + (b.wide - a.wide) * k,
    round: a.round + (b.round - a.round) * k,
  };
}