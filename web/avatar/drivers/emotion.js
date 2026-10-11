/* 情绪驱动：把后端已经发过来的情绪标签变成持续的表情姿态。
 *
 * 后端 detect_emotion() 是整轮一次性判定的 7 选 1，没有强度也没有混合，
 * 所以这里做两件事补上：显式交叉淡化（blend 秒）和"表情维持"——
 * 情绪切换后表情停在那儿，直到下一个情绪到来，而不是飘回中位。
 */

import { lerp, smoothstep } from "../params.js";

export class EmotionDriver {
  constructor(table = {}) {
    this.table = table;
    this.current = null;       // 正在淡入的
    this.from = null;          // 正在淡出的
    this.k = 1;
    this.blend = 0.4;
  }

  /* 拿到 config/avatar.yaml 的 emotions 段 */
  setTable(table) {
    this.table = table || {};
  }

  has(name) {
    return !!this.table[name];
  }

  set(name) {
    if (!this.has(name)) name = "base";
    if (name === this.current) return false;
    this.from = this.current;
    this.k = 0;
    this.blend = Number(this.table[name]?.blend ?? 0.4);
    this.current = name;
    return true;
  }

  update(dt) {
    if (this.k >= 1) return this.current;
    this.k = Math.min(1, this.k + dt / Math.max(0.01, this.blend));
    return this.current;
  }

  /* 返回本帧要叠加的 shift；k<1 时和上一组情绪线性混合 */
  shift() {
    const to = this.table[this.current]?.shift || {};
    if (this.k >= 1 || !this.from) return to;
    const from = this.table[this.from]?.shift || {};
    const k = smoothstep(this.k);
    const mixed = {};
    for (const key of new Set([...Object.keys(from), ...Object.keys(to)])) {
      mixed[key] = lerp(from[key] || 0, to[key] || 0, k);
    }
    return mixed;
  }

  /* 本帧是否由情绪接管视线（避免和 idle 的扫视打架） */
  get ownsGaze() {
    return this.current !== "base" && this.k < 0.75;
  }
}