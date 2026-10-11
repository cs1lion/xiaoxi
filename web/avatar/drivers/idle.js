/* 待机驱动：没人下指令时，自己该有的微动。
 *
 * 静态形象看着像贴纸，活着看着像在听人说话，差别基本就在这几条：
 * 眨眼（泊松过程，不是等间隔）、视线扫视（偶尔真的看向别处）、
 * 呼吸、以及一层低频噪声驱动的头微动。
 */

import { makeRng, clamp, lerp, smoothstep } from "../params.js";

/* 一维值噪声：比 Perlin 便宜，视觉上够用。
 * 用于头微动的"活"，不能用随机数直接跳——那会看起来在抽搐。 */
function makeNoise(rng, samples = 256) {
  const table = Array.from({ length: samples }, () => rng() * 2 - 1);
  return function noise(x) {
    const wrapped = ((x % samples) + samples) % samples;
    const i = Math.floor(wrapped);
    const f = wrapped - i;
    const a = table[i % samples];
    const b = table[(i + 1) % samples];
    return lerp(a, b, smoothstep(f));
  };
}

const DEFAULTS = {
  blink: { mean_interval: 3.4, min_interval: 0.9, double_prob: 0.18, close_ms: 90, hold_ms: 34 },
  saccade: { mean_interval: 1.9, max_shift: 0.55, duration: 0.22 },
  breath: { period: 4.1, depth: 1.0 },
  sway: { amp_x: 0.12, amp_y: 0.085, speed_x: 0.16, speed_y: 0.13 },
  speaking_sway: { amp: 0.18, freq: 2.7 },
};

export class IdleDriver {
  constructor(cfg = {}) {
    this.cfg = { ...DEFAULTS, ...cfg };
    this.rng = makeRng(0x51f3a7);
    this.noise = makeNoise(this.rng);

    this.t = 0;
    this.nextBlink = this._sampleBlink();
    this.blinkPhase = -1;         // >=0 表示正在眨
    this.blinkQueued = 0;
    this.eyeLevel = 1;           // 睁眼

    this.nextSaccade = this._sampleSaccade();
    this.gazeX = 0;
    this.gazeY = 0;
    this.gazeFromX = 0;
    this.gazeFromY = 0;
    this.gazeToX = 0;
    this.gazeToY = 0;
    this.gazeT = 1;
    this.gazeDur = 0.2;

    /* 情绪/手势层自己管眼珠，idle 只在没人管的时候接管 */
    this.gazeOwned = false;
  }

  _sampleBlink() {
    const { mean_interval, min_interval } = this.cfg.blink;
    // 指数分布抽样的等价写法：泊松过程的间隔
    return min_interval + -Math.log(1 - this.rng()) * mean_interval;
  }

  _sampleSaccade() {
    return this.cfg.saccade.mean_interval * (0.5 + this.rng());
  }

  /* 眨眼和视线都写在这里；眼珠位置由 rig.commit 做叠加仲裁。 */
  update(dt, { speaking = false, rms = 0 } = {}) {
    this.t += dt;

    /* --- 眨眼 --- */
    this.nextBlink -= dt;
    if (this.blinkPhase < 0 && this.nextBlink <= 0) {
      this.blinkPhase = 0;
      this.blinkQueued = this.rng() < this.cfg.blink.double_prob ? 1 : 0;
      this.nextBlink = this._sampleBlink();
    }
    if (this.blinkPhase >= 0) {
      const close = this.cfg.blink.close_ms / 1000;
      const hold = this.cfg.blink.hold_ms / 1000;
      this.blinkPhase += dt;
      // 梯形而不是三角：闭得快 -> 保持 -> 睁开。
      // 三角形峰值正好落在关闭时刻，60fps 下常常一帧都采不到，
      // 结果就是"眨眼永远闭不严"。
      if (this.blinkPhase < close) {
        this.eyeLevel = 1 - this.blinkPhase / close;
      } else if (this.blinkPhase < close + hold) {
        this.eyeLevel = 0;
      } else {
        const k = (this.blinkPhase - close - hold) / close;
        this.eyeLevel = k >= 1 ? 1 : k;
      }
      if (this.blinkPhase >= close * 2 + hold) {
        this.blinkPhase = -1;
        this.eyeLevel = 1;
        if (this.blinkQueued > 0) {
          this.blinkQueued -= 1;
          this.blinkPhase = 0;          // 直接接第二下
        }
      }
    }

    /* --- 视线扫视 --- */
    if (!this.gazeOwned) {
      this.nextSaccade -= dt;
      if (this.nextSaccade <= 0) {
        this.gazeFromX = this.gazeX;
        this.gazeFromY = this.gazeY;
        const m = this.cfg.saccade.max_shift;
        this.gazeToX = (this.rng() * 2 - 1) * m;
        this.gazeToY = (this.rng() * 2 - 1) * m * 0.6;
        this.gazeT = 0;
        this.gazeDur = this.cfg.saccade.duration * (0.6 + this.rng() * 0.9);
        this.nextSaccade = this._sampleSaccade();
      }
      if (this.gazeT < 1) {
        this.gazeT = Math.min(1, this.gazeT + dt / this.gazeDur);
        const k = smoothstep(this.gazeT);
        this.gazeX = lerp(this.gazeFromX, this.gazeToX, k);
        this.gazeY = lerp(this.gazeFromY, this.gazeToY, k);
      }
    } else {
      // 别人接管视线时把扫视时钟按住，回来再继续
      this.nextSaccade = Math.max(this.nextSaccade, 0.4);
    }

    const out = {
      // blink 是"闭眼量"（0 睁 / 1 闭），直接作为 ParamEyeLOpen 的负增量
      blink: clamp(1 - this.eyeLevel),
      gazeX: this.gazeX,
      gazeY: this.gazeY,
      breath: Math.sin((this.t / this.cfg.breath.period) * Math.PI * 2),
      swayX: this.noise(this.t / this.cfg.sway.speed_x) * this.cfg.sway.amp_x,
      swayY: this.noise(this.t / this.cfg.sway.speed_y + 41.7) * this.cfg.sway.amp_y,
      bob: speaking
        ? Math.sin(this.t * Math.PI * 2 * this.cfg.speaking_sway.freq) *
          this.cfg.speaking_sway.amp * clamp(rms)
        : 0,
    };
    return out;
  }

  /* 让出视线控制权（比如 emotion/gesture 正在把眼睛转向别处） */
  claimGaze(claimed) {
    this.gazeOwned = claimed;
  }
}