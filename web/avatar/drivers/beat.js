/* 节拍同步驱动：从音频能量中检测节拍，驱动角色弹跳。
 *
 * 参考 moeru-ai/airi 的 Beat Sync 子系统：
 * 用 Web Audio API 的 AnalyserNode 获取低频能量，
 * 检测能量脉冲（超过移动平均 + 阈值），
 * 在节拍点触发一个衰减的弹跳脉冲。
 *
 * 弹跳通过弹簧实现，这样有过冲和回摆，
 * 比直接设置位置更自然。
 */

import { clamp, Spring } from "../params.js";

export class BeatDriver {
  constructor() {
    this.energy = 0;
    this.smoothEnergy = 0;
    this.lastBeatTime = 0;
    this.beatInterval = 0.5;       // 估计的节拍间隔（秒）
    this.pulse = new Spring(180, 8);  // 弹跳弹簧
    this.pulseValue = 0;
    this.enabled = true;
  }

  /* 每帧调用。analyser 是播放链路上的 AnalyserNode。 */
  update(analyser, dt) {
    if (!this.enabled || !analyser) {
      this.pulseValue = this.pulse.step(0, dt);
      return { beat: false, strength: 0, pulse: this.pulseValue };
    }

    // 获取低频能量（0-250Hz 范围，对应鼓点/重拍）
    const buf = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(buf);
    let sum = 0;
    const bins = Math.min(8, buf.length);  // 前 8 个 bin ≈ 0-250Hz
    for (let i = 0; i < bins; i++) sum += buf[i];
    this.energy = sum / bins / 255;

    // 平滑能量
    this.smoothEnergy += (this.energy - this.smoothEnergy) * 0.25;

    // 节拍检测：能量超过平滑值 + 阈值，且距离上次节拍足够远
    const now = performance.now() / 1000;
    const threshold = this.smoothEnergy * 1.3 + 0.08;
    const isBeat = this.energy > threshold && now - this.lastBeatTime > this.beatInterval * 0.6;

    if (isBeat) {
      this.lastBeatTime = now;
      // 更新估计的节拍间隔（指数移动平均）
      if (now - this.lastBeatTime > 0) {
        // 用当前间隔更新估计
      }
      // 触发弹跳脉冲
      this.pulse.v += 3.5;
    }

    // 弹簧自然衰减
    this.pulseValue = this.pulse.step(0, dt);

    return {
      beat: isBeat,
      strength: clamp(this.pulseValue, 0, 1),
      pulse: this.pulseValue,
      energy: this.energy,
    };
  }

  setEnabled(on) {
    this.enabled = !!on;
  }
}