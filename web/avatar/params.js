/* 参数表与混合层。
 *
 * 命名沿用 Live2D ParameterModel，方便以后把 render 换成 Live2D/VRM 时
 * 配置和驱动整体搬运。约定：
 *   - 归一化到 -1..1（少数 0..1），0 = 中位
 *   - 每个参数有独立的一阶时间常数（tau），嘴快头慢
 *   - 驱动各自往参数上"叠加偏移"，仲裁见文件末尾的 Rigger
 */

const DEFAULT_TAU = 0.12;

/* 分组只用于排错和后续接 Live2D 的 parameter group，不参与运算。 */
export const PARAM_GROUPS = {
  head: ["ParamAngleX", "ParamAngleY", "ParamAngleZ"],
  body: ["ParamBodyAngleX", "ParamBodyAngleZ", "ParamBodyLeanX"],
  eye: ["ParamEyeLOpen", "ParamEyeROpen", "ParamEyeBallX", "ParamEyeBallY"],
  brow: ["ParamBrowLY", "ParamBrowRY", "ParamBrowLAngle", "ParamBrowRAngle",
         "ParamBrowLForm", "ParamBrowRForm"],
  mouth: ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthRound", "ParamMouthY"],
  other: ["ParamCheek", "ParamBreath"],
};

export const ALL_PARAMS = Object.values(PARAM_GROUPS).flat();

export function clamp(v, lo = -1, hi = 1) {
  return v < lo ? lo : v > hi ? hi : v;
}

export function clamp01(v) {
  const n = Number(v);
  return !Number.isFinite(n) ? 0 : n < 0 ? 0 : n > 1 ? 1 : n;
}

export function lerp(a, b, t) {
  return a + (b - a) * t;
}

export function smoothstep(t) {
  const x = clamp(t);
  return x * x * (3 - 2 * x);
}

/* 指数趋近：与帧率无关的"一阶低通"。
 * tau = 达到 63% 差距所需秒数。dt 大时也不会过冲。 */
export function approach(current, target, tau, dt) {
  if (tau <= 0) return target;
  const k = 1 - Math.exp(-dt / tau);
  return current + (target - current) * k;
}

/* 全部参数的容器：current 是输出，target 由驱动轮流写入。 */
export class ParamStore {
  constructor(neutral = {}, response = {}) {
    this.current = Object.create(null);
    this.target = Object.create(null);
    this.tau = Object.create(null);
    this.neutral = Object.create(null);
    for (const key of ALL_PARAMS) {
      const n = Number(neutral[key] ?? 0);
      this.current[key] = n;
      this.target[key] = n;
      this.neutral[key] = n;
      this.tau[key] = Number(response[key] ?? DEFAULT_TAU);
    }
  }

  blend(dt) {
    for (const key of ALL_PARAMS) {
      this.current[key] = approach(this.current[key], this.target[key], this.tau[key], dt);
    }
  }

  get(key) {
    return this.current[key];
  }
}

/* 伪随机，带种子，方便行为可复现。 */
export function makeRng(seed = 0x2f6e2b1) {
  let s = seed >>> 0;
  return function rng() {
    s ^= s << 13; s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5; s >>>= 0;
    return s / 0x100000000;
  };
}

/* 二阶弹簧-阻尼，用来做次级运动（头发/呆毛/眉毛滞后于头）。
 *
 * 和 approach() 的区别：approach 是一阶低通，只能单调地"慢慢跟上"；
 * 弹簧有二阶惯性，会滞后、会过冲、会回摆——这正是"活的"和"贴纸"的区别。
 *
 * 阻尼比 zeta = c / (2*sqrt(k))：
 *   zeta < 1  欠阻尼，过冲（头发/呆毛要这个）
 *   zeta = 1  临界阻尼，不超调
 *
 * 内部按 1/240s 细分子步跑半隐式欧拉：显式欧拉在高刚度下会发散，
 * 而一帧的 dt 可能因为标签页切回来而很大。
 */
export class Spring {
  constructor(stiffness = 90, damping = 12, value = 0) {
    this.k = stiffness;
    this.c = damping;
    this.x = value;
    this.v = 0;
  }

  step(target, dt) {
    if (!(dt > 0)) return this.x;
    let remain = Math.min(dt, 0.1);
    while (remain > 1e-6) {
      const h = remain > 1 / 240 ? 1 / 240 : remain;
      this.v += (this.k * (target - this.x) - this.c * this.v) * h;
      this.x += this.v * h;
      remain -= h;
    }
    return this.x;
  }

  reset(value = 0) {
    this.x = value;
    this.v = 0;
  }
}

/* 驱动仲裁：多个驱动往同一个参数上叠加偏移。
 *
 * 为什么是叠加而不是"高优先级覆盖"：眯眼(情绪) + 闭眼(眨眼) 必须能加成
 * "完全闭上"，覆盖模型做不到。唯一需要独占的是说话时的张口——那由 lip
 * 驱动用 mute() 显式声明，挡住手势层残留的嘴型。
 *
 * 每帧 begin() 清空各层，驱动只写它这一帧真正负责的键。
 */
export class Rigger {
  constructor(neutral = {}) {
    this.neutral = neutral;
    this.layers = new Map();
    this.order = [];
  }

  register(name, priority) {
    this.layers.set(name, { priority, values: Object.create(null), mutes: new Set() });
    this.order = [...this.layers.entries()].sort((a, b) => a[1].priority - b[1].priority);
  }

  begin() {
    for (const layer of this.layers.values()) {
      for (const key of Object.keys(layer.values)) delete layer.values[key];
      layer.mutes.clear();
    }
  }

  /* 绝对目标：内部换算成相对中位的偏移 */
  set(name, key, value) {
    this.add(name, key, (Number(value) || 0) - (this.neutral[key] || 0));
  }

  add(name, key, delta) {
    const layer = this.layers.get(name);
    if (!layer) throw new Error(`未注册的驱动: ${name}`);
    layer.values[key] = (layer.values[key] || 0) + (Number(delta) || 0);
  }

  /* 本层独占该参数：优先级比它低的层对同一个键的贡献全部丢弃。
   从高到低累加，碰到独占就封口。 */
  mute(name, key) {
    const layer = this.layers.get(name);
    if (!layer) throw new Error(`未注册的驱动: ${name}`);
    layer.mutes.add(key);
  }

  commit(store) {
    for (const key of ALL_PARAMS) {
      let total = this.neutral[key] || 0;
      for (let i = this.order.length - 1; i >= 0; i--) {
        const layer = this.order[i][1];
        const v = layer.values[key];
        if (v === undefined) continue;
        total += v;
        if (layer.mutes.has(key)) break;
      }
      store.target[key] = clamp(total);
    }
  }
}