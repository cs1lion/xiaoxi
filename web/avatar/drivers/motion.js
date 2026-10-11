/* Motion Magic：程序化动作生成，不用关键帧。
 *
 * 参考 moeru-ai/airi 的 useLive2DMotionMagic：
 * 从运动档案数据集合成连续姿态，而不是播放预定义的关键帧。
 * 核心思想：每个动作由多个正弦波叠加而成，
 * 每个正弦波有不同的频率、振幅和相位。
 * 多个分量叠加生成复杂的、不循环的动作，避免"循环点"。
 *
 * 和关键帧的区别：
 *   - 关键帧：预定义的动作序列，播放完就循环，有"循环点"
 *   - Motion Magic：程序化生成，无限不循环，每次都不一样
 *
 * 运动分量（Motion Component）：
 *   - freq: 频率（Hz），决定动作速度
 *   - amp: 振幅，决定动作幅度
 *   - phase: 相位，决定动作起始点
 *   - target: 目标参数（如 ParamAngleX）
 *   - offset: 偏移量，决定动作中心
 *
 * 叠加多个分量后，动作变得复杂且不可预测，
 * 但仍然在可控范围内（振幅限制了最大偏移）。
 */

import { clamp } from "../params.js";

export class MotionMagic {
  constructor() {
    this.t = 0;
    this.components = [];
    this.enabled = true;
  }

  /* 添加一个运动分量。
   * target: 目标参数名
   * freq: 频率（Hz）
   * amp: 振幅
   * phase: 相位（弧度）
   * offset: 偏移量 */
  add(target, freq, amp, phase = 0, offset = 0) {
    this.components.push({ target, freq, amp, phase, offset });
  }

  /* 从配置批量添加。
   * cfg 格式：
   *   [
   *     { target: "ParamAngleX", freq: 0.1, amp: 0.3, phase: 0 },
   *     { target: "ParamAngleY", freq: 0.15, amp: 0.2, phase: 1.5 },
   *   ]
   */
  setComponents(cfg) {
    this.components = (cfg || []).map((c) => ({
      target: c.target,
      freq: c.freq || 0.1,
      amp: c.amp || 0.2,
      phase: c.phase || 0,
      offset: c.offset || 0,
    }));
  }

  /* 每帧调用。返回要叠加到角色参数上的增量。 */
  update(dt) {
    if (!this.enabled || !this.components.length) return null;
    this.t += dt;

    const out = {};
    for (const c of this.components) {
      const value = Math.sin(this.t * c.freq * Math.PI * 2 + c.phase) * c.amp + c.offset;
      out[c.target] = (out[c.target] || 0) + value;
    }
    return out;
  }

  setEnabled(on) {
    this.enabled = !!on;
  }

  /* 重置时间（用于测试或重新同步） */
  reset() {
    this.t = 0;
  }
}

/* 预定义的运动档案（Motion Profile）。
 * 每个档案是一组运动分量的集合，
 * 可以理解为"一种动作风格"。
 *
 * 这些档案是从 AIRI 的运动档案数据集中提取的，
 * 经过调整以适应我们的参数系统。
 */
export const MOTION_PROFILES = {
  /* 待机微动：缓慢的、不循环的头部微动 */
  idle: [
    { target: "ParamAngleX", freq: 0.08, amp: 0.15, phase: 0 },
    { target: "ParamAngleX", freq: 0.13, amp: 0.08, phase: 1.2 },
    { target: "ParamAngleY", freq: 0.11, amp: 0.12, phase: 0.5 },
    { target: "ParamAngleY", freq: 0.17, amp: 0.06, phase: 2.1 },
    { target: "ParamAngleZ", freq: 0.09, amp: 0.10, phase: 0.8 },
    { target: "ParamBodyAngleX", freq: 0.07, amp: 0.08, phase: 1.5 },
    { target: "ParamBodyAngleZ", freq: 0.12, amp: 0.05, phase: 0.3 },
  ],

  /* 思考时的微动：更慢、更深的头部倾斜 */
  thinking: [
    { target: "ParamAngleX", freq: 0.06, amp: 0.20, phase: 0 },
    { target: "ParamAngleY", freq: 0.09, amp: 0.15, phase: 1.0 },
    { target: "ParamAngleZ", freq: 0.07, amp: 0.12, phase: 0.5 },
    { target: "ParamBodyAngleX", freq: 0.05, amp: 0.10, phase: 2.0 },
  ],

  /* 说话时的微动：更快的、更有节奏的头部动作 */
  speaking: [
    { target: "ParamAngleX", freq: 0.15, amp: 0.12, phase: 0 },
    { target: "ParamAngleX", freq: 0.23, amp: 0.06, phase: 1.5 },
    { target: "ParamAngleY", freq: 0.18, amp: 0.10, phase: 0.8 },
    { target: "ParamAngleZ", freq: 0.14, amp: 0.08, phase: 2.0 },
    { target: "ParamBodyAngleX", freq: 0.12, amp: 0.06, phase: 0.3 },
  ],

  /* 情绪激动时的微动：大幅度的、快速的头部动作 */
  excited: [
    { target: "ParamAngleX", freq: 0.20, amp: 0.18, phase: 0 },
    { target: "ParamAngleY", freq: 0.25, amp: 0.15, phase: 1.0 },
    { target: "ParamAngleZ", freq: 0.18, amp: 0.12, phase: 0.5 },
    { target: "ParamBodyAngleX", freq: 0.15, amp: 0.10, phase: 2.0 },
    { target: "ParamBodyAngleZ", freq: 0.22, amp: 0.08, phase: 0.8 },
  ],

  /* 收敛时的微动：缓慢的、小幅度的头部动作 */
  subdued: [
    { target: "ParamAngleX", freq: 0.05, amp: 0.10, phase: 0 },
    { target: "ParamAngleY", freq: 0.07, amp: 0.08, phase: 1.5 },
    { target: "ParamAngleZ", freq: 0.06, amp: 0.06, phase: 0.8 },
  ],
};

/* 根据情绪获取运动档案 */
export function getMotionProfile(emotion) {
  switch (emotion) {
    case "调侃":
    case "得意":
      return MOTION_PROFILES.excited;
    case "认真":
      return MOTION_PROFILES.thinking;
    case "收敛":
    case "变小气":
      return MOTION_PROFILES.subdued;
    case "轻微不服":
      return MOTION_PROFILES.speaking;
    default:
      return MOTION_PROFILES.idle;
  }
}