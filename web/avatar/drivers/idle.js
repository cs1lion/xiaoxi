/* 待机驱动：没人下指令时，自己该有的微动。
 *
 * 静态形象看着像贴纸，活着看着像在听人说话，差别基本就在这几条：
 * 眨眼（泊松过程，不是等间隔）、眼神有明确的注视对象、
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
  gaze: {
    targets: { viewer: { x: 0, y: 0, drift: 0.10 } },
    move: { duration: 0.14 },
    micro: { mean_interval: 0.85, radius: 0.11, duration: 0.10 },
  },
  blink_while_speaking: 0.65,   // 说话时眨眼更频繁，这个是间隔乘数
};

export class IdleDriver {
  constructor(cfg = {}) {
    // 嵌套对象要逐层合，不然只传 {blink:{}} 会把其余全丢掉
    this.cfg = {
      ...DEFAULTS, ...cfg,
      blink: { ...DEFAULTS.blink, ...(cfg.blink || {}) },
      saccade: { ...DEFAULTS.saccade, ...(cfg.saccade || {}) },
      breath: { ...DEFAULTS.breath, ...(cfg.breath || {}) },
      sway: { ...DEFAULTS.sway, ...(cfg.sway || {}) },
      speaking_sway: { ...DEFAULTS.speaking_sway, ...(cfg.speaking_sway || {}) },
      gaze: {
        targets: { ...DEFAULTS.gaze.targets, ...(cfg.gaze?.targets || {}) },
        move: { ...DEFAULTS.gaze.move, ...(cfg.gaze?.move || {}) },
        micro: { ...DEFAULTS.gaze.micro, ...(cfg.gaze?.micro || {}) },
      },
    };

    this.rng = makeRng(0x51f3a7);
    this.noise = makeNoise(this.rng);

    this.t = 0;
    this.nextBlink = this._sampleBlink(1);
    this.blinkPhase = -1;         // >=0 表示正在眨
    this.blinkQueued = 0;
    this.eyeLevel = 1;           // 睁眼

    /* --- 注视点 --- */
    this.targetName = "viewer";
    this.gazeX = 0;
    this.gazeY = 0;
    this.gazeFromX = 0;
    this.gazeFromY = 0;
    this.gazeToX = 0;
    this.gazeToY = 0;
    this.gazeT = 1;
    this.gazeDur = 0.14;
    this.nextMicro = this._sampleMicro();

    /* 情绪/手势层自己管眼珠，idle 只在没人管的时候接管 */
    this.gazeOwned = false;

    /* 系统"减少动态效果"时压低幅度，由 Avatar 从 matchMedia 灌进来 */
    this.motion = { sway: 1, bob: 1, gazeDrift: 1 };
  }

  setMotion(scale) {
    this.motion = {
      sway: scale.sway ?? 1,
      bob: scale.bob ?? 1,
      gazeDrift: scale.gazeDrift ?? 1,
    };
  }

  _sampleBlink(speaking) {
    const { mean_interval, min_interval } = this.cfg.blink;
    // 指数分布抽样的等价写法：泊松过程的间隔
    return min_interval + -Math.log(1 - this.rng()) * mean_interval *
      (speaking ? this.cfg.blink_while_speaking : 1);
  }

  _sampleMicro() {
    return this.cfg.gaze.micro.mean_interval * (0.4 + this.rng());
  }

  /* 换注视点。真人移视是弹道式的，duration 很短，
   * 慢吞吞地转过去反而像"眼珠在飘"。 */
  setGaze(name, immediate = false) {
    if (!this.cfg.gaze.targets[name]) name = "viewer";
    this.targetName = name;
    const t = this.cfg.gaze.targets[name];
    if (immediate) {
      this.gazeX = this.gazeToX = t.x;
      this.gazeY = this.gazeToY = t.y;
      this.gazeT = 1;
      return;
    }
    this._moveTo(t.x, t.y, this.cfg.gaze.move.duration);
  }

  /* 落点之后的微注视抖动：围绕 target 游走，不离开注视对象 */
  _drift() {
    const t = this.cfg.gaze.targets[this.targetName];
    const r = t.drift * this.motion.gazeDrift;
    const a = this.rng() * Math.PI * 2;
    const d = Math.sqrt(this.rng()) * r;
    this._moveTo(
      t.x + Math.cos(a) * d,
      t.y + Math.sin(a) * d * 0.65,
      this.cfg.gaze.micro.duration
    );
  }

  _moveTo(x, y, duration) {
    this.gazeFromX = this.gazeX;
    this.gazeFromY = this.gazeY;
    this.gazeToX = x;
    this.gazeToY = y;
    this.gazeT = 0;
    this.gazeDur = Math.max(0.02, duration);
  }

  update(dt, { speaking = false, rms = 0 } = {}) {
    this.t += dt;

    /* --- 眨眼（说话时更频繁） --- */
    this.nextBlink -= dt;
    if (this.blinkPhase < 0 && this.nextBlink <= 0) {
      this.blinkPhase = 0;
      this.blinkQueued = this.rng() < this.cfg.blink.double_prob ? 1 : 0;
      this.nextBlink = this._sampleBlink(speaking);
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

    /* --- 注视点 --- */
    if (!this.gazeOwned) {
      this.nextMicro -= dt;
      if (this.nextMicro <= 0) {
        this._drift();
        this.nextMicro = this._sampleMicro();
      }
      if (this.gazeT < 1) {
        this.gazeT = Math.min(1, this.gazeT + dt / this.gazeDur);
        const k = smoothstep(this.gazeT);
        this.gazeX = lerp(this.gazeFromX, this.gazeToX, k);
        this.gazeY = lerp(this.gazeFromY, this.gazeToY, k);
      }
    } else {
      // 别人接管视线时把游走时钟按住，回来再继续
      this.nextMicro = Math.max(this.nextMicro, 0.4);
    }

    return {
      // blink 是"闭眼量"（0 睁 / 1 闭），直接作为 ParamEyeLOpen 的负增量
      blink: clamp(1 - this.eyeLevel),
      gazeX: this.gazeX,
      gazeY: this.gazeY,
      breath: Math.sin((this.t / this.cfg.breath.period) * Math.PI * 2),
      swayX: this.noise(this.t / this.cfg.sway.speed_x) * this.cfg.sway.amp_x * this.motion.sway,
      swayY: this.noise(this.t / this.cfg.sway.speed_y + 41.7) * this.cfg.sway.amp_y * this.motion.sway,
      bob: speaking
        ? Math.sin(this.t * Math.PI * 2 * this.cfg.speaking_sway.freq) *
          this.cfg.speaking_sway.amp * clamp(rms) * this.motion.bob
        : 0,
    };
  }

  /* 让出视线控制权（比如 emotion/gesture 正在把眼睛转向别处） */
  claimGaze(claimed) {
    this.gazeOwned = claimed;
  }
}