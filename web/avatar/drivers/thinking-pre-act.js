/* 思考期预演（打磨轮 A-2，协议第十一节"首包提速与默认值"）。
 *
 * thinking 到首个 audio 之间用户听到的是沉默。沉默本身不可怕，可怕的是沉默
 * 里角色完全静止——那读起来像卡死了，不像在想事情。这个驱动在那段沉默里补
 * 三个很小的"正在想"信号：一次轻眨眼、一下缓慢的微歪头、一点几乎察觉不到
 * 的摇晃。
 *
 * 边界划得很清楚，每一条都是为了防止和既有演出打架：
 *   - 绝不碰 ParamEyeBallX/Y。思考时的视线微移归 DialogueGazeDirector 的
 *     等待/回避排程所有，两边都写视线就是两个 gaze 打架。
 *   - 只往 idle 层加小偏移。gesture 层的 think 轨（priority 30）继续持有
 *     歪头姿势，本驱动在它上面加"活"的微动，不抢它的戏；偏移随后还会经过
 *     idle 层叠加软限幅（params.js 的 softLimitStack）。
 *   - reduced-motion 下降级为静态：不歪头也不眨眼。基线待机微动不受影响。
 *   - 收尾时机：首个 audio（Avatar.speak）、done、interrupted、新轮次。
 *
 * 眨眼走"返回值"而不是自己写参数：Avatar 把它与 idle 自发眨眼取 max 合成。
 * 两条曲线直接相加会双重闭合（眼睑被拉过 0 再钳回来），取 max 保证任何时刻
 * 只有一条在真正闭眼。 */

import { clamp01, smoothstep } from "../params.js";

const DEFAULTS = {
  blinkAt: 0.55,       // 等待开始后多久补一次轻眨眼（秒）
  blinkClose: 0.07,    // 闭合段
  blinkHold: 0.02,     // 完全闭着的保持
  blinkOpen: 0.15,     // 张开段（比闭合慢，和真人的眨眼一致）
  tiltAmpZ: 0.05,      // 微歪头幅度（ParamAngleZ 归一量）
  tiltAmpX: 0.03,      // 微点头幅度（ParamAngleX 归一量）
  period: 5.2,         // 摇晃周期：和呼吸(4.1s)、点头(≈6s)、头发慢漂(7.3s)都错开
  fadeIn: 0.6,         // 起势时长：等 think 手势落位后再慢慢加，不抢开场
};

export class ThinkingPreactDriver {
  constructor(cfg = {}) {
    this.cfg = { ...DEFAULTS, ...cfg };
    this.active = false;
    this.age = 0;
    this.blinked = false;
    this.blinkAge = null;
  }

  configure(cfg = {}) {
    if (cfg && typeof cfg === "object") this.cfg = { ...this.cfg, ...cfg };
    return this;
  }

  /* 新一轮等待开始（或重复的 thinking 重来一次）。 */
  start() {
    this.active = true;
    this.age = 0;
    this.blinked = false;
    this.blinkAge = null;
  }

  stop({ immediate = false } = {}) {
    this.active = false;
    if (immediate) {
      this.age = 0;
      this.blinked = false;
      this.blinkAge = null;
    }
  }

  get running() { return this.active; }

  /* 返回 { values, blink } 或 null（未激活 / reduced-motion 静态降级）。
   * blink 是这一帧新产生的闭眼量，由调用方与其它眨眼源取 max 合成。 */
  update(dt, { reducedMotion = false } = {}) {
    if (!this.active) return null;
    const step = Number.isFinite(dt) && dt > 0 ? Math.min(dt, 0.1) : 0;
    this.age += step;
    if (reducedMotion) return null;
    const cfg = this.cfg;
    let blink = 0;
    if (!this.blinked && this.age >= cfg.blinkAt) {
      this.blinked = true;
      this.blinkAge = 0;
    }
    if (this.blinkAge !== null) {
      this.blinkAge += step;
      const t = this.blinkAge;
      const total = cfg.blinkClose + cfg.blinkHold + cfg.blinkOpen;
      if (t >= total) {
        this.blinkAge = null;
      } else if (t < cfg.blinkClose) {
        blink = smoothstep(t / cfg.blinkClose);
      } else if (t < cfg.blinkClose + cfg.blinkHold) {
        blink = 1;
      } else {
        blink = 1 - smoothstep((t - cfg.blinkClose - cfg.blinkHold) / cfg.blinkOpen);
      }
    }
    // 起势用 smoothstep：think 手势正在落位，预演从它上面轻轻长出来。
    const envelope = smoothstep(clamp01(this.age / cfg.fadeIn));
    const phase = (this.age / cfg.period) * Math.PI * 2;
    return {
      values: {
        ParamAngleZ: Math.sin(phase) * cfg.tiltAmpZ * envelope,
        ParamAngleX: Math.sin(phase * 0.63 + 1.1) * cfg.tiltAmpX * envelope,
      },
      blink,
    };
  }
}
