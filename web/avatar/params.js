/* 参数表与混合层。
 *
 * 命名沿用 Live2D ParameterModel，方便以后把 render 换成 Live2D/VRM 时
 * 配置和驱动整体搬运。约定：
 *   - 有符号动作归一化到 -1..1，眼开度和瞳孔等使用各自范围
 *   - 中位由配置给定；睁眼、瞳孔倍率和高光的默认中位是 1
 *   - 每个参数有独立的一阶时间常数（tau），嘴快头慢
 *   - 驱动各自往参数上"叠加偏移"，仲裁见文件末尾的 Rigger
 */

const DEFAULT_TAU = 0.12;

/* 分组只用于排错和后续接 Live2D 的 parameter group，不参与运算。 */
export const PARAM_GROUPS = {
  head: ["ParamAngleX", "ParamAngleY", "ParamAngleZ"],
  body: ["ParamBodyAngleX", "ParamBodyAngleZ", "ParamBodyLeanX"],
  eye: ["ParamEyeLOpen", "ParamEyeROpen", "ParamEyeBallX", "ParamEyeBallY",
        "ParamEyeBallForm", "ParamEyeLSmile", "ParamEyeRSmile", "ParamEyeLLower", "ParamEyeRLower", "ParamPupil", "ParamEyeHighlight"],
  brow: ["ParamBrowLY", "ParamBrowRY", "ParamBrowLAngle", "ParamBrowRAngle",
         "ParamBrowLForm", "ParamBrowRForm"],
  mouth: ["ParamMouthOpenY", "ParamMouthForm", "ParamMouthRound", "ParamMouthY"],
  other: ["ParamCheek", "ParamBreath"],
};

export const ALL_PARAMS = Object.values(PARAM_GROUPS).flat();

export const DEFAULT_NEUTRAL = Object.freeze(Object.fromEntries(
  ALL_PARAMS.map((key) => [key,
    ["ParamEyeLOpen", "ParamEyeROpen", "ParamPupil", "ParamEyeHighlight"].includes(key) ? 1 : 0]),
));

/* 瞳孔是倍率，不能被通用的 ±1 截断；呼吸和嘴部收圆沿用有符号输入。 */
const UNSIGNED_PARAMS = new Set([
  "ParamEyeLSmile", "ParamEyeRSmile", "ParamEyeLLower", "ParamEyeRLower", "ParamEyeHighlight", "ParamMouthOpenY", "ParamCheek",
]);
export const PARAM_RANGES = Object.freeze(Object.fromEntries(ALL_PARAMS.map((key) => [key,
  Object.freeze(key === "ParamPupil" ? [0.55, 1.5]
    : key === "ParamEyeLOpen" || key === "ParamEyeROpen" ? [0, 1.2]
    : UNSIGNED_PARAMS.has(key) ? [0, 1] : [-1, 1]),
])));

export function clamp(v, lo = -1, hi = 1) {
  const n = Number(v);
  const value = Number.isFinite(n) ? n : 0;
  return value < lo ? lo : value > hi ? hi : value;
}

export function clampParam(key, value) {
  const n = Number(value);
  const [lo, hi] = PARAM_RANGES[key] || [-1, 1];
  return clamp(Number.isFinite(n) ? n : (DEFAULT_NEUTRAL[key] ?? 0), lo, hi);
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

/* idle 层微动叠加的软限幅（打磨轮 A-3）。
 *
 * sway / 说话律动 / Motion Magic / beat 四源各自都是有界的小偏移，但它们
 * **相加**进同一个 ParamAngle*：相位偶然对齐时总和会冲到单源幅度的两三倍，
 * 读起来就是"头突然抖一下"。直接 clamp 到硬上限会切出速度断点（那是新的
 * 抽搐源），按比例整体缩小又会把正常演出一起压僵。
 *
 * 软膝压缩：膝值以下恒等，正常演出与现状逐字节一致；膝值以上用 tanh 平滑
 * 压向天花板。tanh 在膝点处一阶导为 1，所以压缩是 C1 连续的，不会在膝盖
 * 处引入新的速度折角；渐近趋于天花板而永不超出，叠加再猛也不越界。
 *
 * 只覆盖头部与身体的角度通道：dialogueExpression 写的眉眼/腮红通道不产生
 * 角度累加，其自身量级（<=0.075）也远低于任何膝值。 */
const STACK_LIMIT_KEYS = new Set([
  "ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBodyAngleX", "ParamBodyAngleZ",
]);
export function softLimitStack(key, value, { knee = 0.45, ceiling = 0.75 } = {}) {
  const x = Number(value);
  if (!Number.isFinite(x)) return 0;
  if (!STACK_LIMIT_KEYS.has(key)) return x;
  const magnitude = Math.abs(x);
  if (magnitude <= knee) return x;
  const span = Math.max(1e-6, ceiling - knee);
  return Math.sign(x) * (knee + span * Math.tanh((magnitude - knee) / span));
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
      const n = clampParam(key, neutral[key] ?? DEFAULT_NEUTRAL[key]);
      this.current[key] = n;
      this.target[key] = n;
      this.neutral[key] = n;
      const tau = Number(response[key] ?? DEFAULT_TAU);
      this.tau[key] = Number.isFinite(tau) && tau >= 0 ? tau : DEFAULT_TAU;
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
 * 参数用「频率 f（Hz）+ 阻尼比 zeta」而不是「刚度 k + 阻尼 c」。
 * 原因很实际：k 和 c 不正交，调高 k 会同时让运动变快、也更容易过冲，
 * 想单独改"甩几下"根本无从下手。换成 f / zeta 之后两件事分开管：
 *
 *   f      只管"多快"          —— 2Hz 的头发就是一秒摆两个来回
 *   zeta   只管"摆几下停"      —— 和快慢完全无关
 *
 *   zeta < 1   欠阻尼，会回摆        头发、呆毛、耳朵、尾巴要这个
 *   zeta = 1   临界阻尼，刚好不过冲   镜头跟随、UI 缓动要这个
 *   zeta > 1   过阻尼，慢慢爬到位     很重的东西
 *
 * 内部关系：omega = 2*pi*f，k = omega^2，c = 2*zeta*omega。
 *
 * 积分用解析解而不是数值积分。
 *
 * 先说数值积分为什么在这里不够用。最直觉的写法是半隐式（辛）欧拉：
 *
 *     v += (k*(T-x) - c*v) * h
 *     x += v * h
 *
 * 它确实解决了显式欧拉"无条件发散"的问题（显式欧拉对无阻尼振子的每步
 * 振幅增益是 sqrt(1 + omega^2*h^2) > 1，能量单调增长），稳定条件是
 * omega*h < 2。但它**仍然是离散的**：离散振子的有效频率略低于连续频率，
 * 误差是 O(h^2) 的，而且这个误差在长时间上累积。
 *
 * 后果很具体：把内部子步固定在 1/240s 确实能让 30fps 和 60fps 跑出
 * 几乎一样的结果——但"几乎"是有代价的。实测 1.5Hz 的头发在 30fps 下
 * 相对 240fps 参考轨迹的最大偏差是 0.14，已经大到肉眼能看出甩幅不一样；
 * 4Hz 的节拍弹簧偏差 0.47，几乎等于把动画幅度打对折。
 * 子步从 1/240 改到 1/2000 能把误差压下去，代价是每帧多算 8 倍。
 *
 * 阻尼谐振子有闭式解，直接用它就没有这个矛盾：
 *   - 任意步长都精确，没有离散化误差，也就天然跨帧率一致
 *   - 不需要子步，一帧一次算完，标签页切回来给几秒的 dt 也不会有问题
 *     （数值积分遇到大 dt 要么发散要么得封顶丢时间，闭式解两种代价都没有）
 *   - 能量保证单调衰减，这是"物理写对了"最直接的判据
 *
 * 代价是每步要做 exp/cos/sin，比一次乘加贵，但这层每帧只跑二十来次，
 * 换来的正确性值这个价。
 *
 * 关键性质：t 分两次算和一次算结果完全相同，所以"把一帧拆成两半算两次"
 * 与"算一次"等价——这是跨帧率一致的充分条件。
 */
export class Spring {
  constructor(freq = 3, zeta = 0.6, value = 0) {
    this.setResponse(freq, zeta);
    this.x = value;
    this.v = 0;
  }

  setResponse(freq, zeta) {
    const f = Math.max(0.01, Number(freq) || 3);
    const z = Math.max(0, Number(zeta ?? 0.6));
    const omega = 2 * Math.PI * f;
    this.freq = f;
    this.zeta = z;
    this.omega = omega;
    this.k = omega * omega;
    this.c = 2 * z * omega;
    /* 三种阻尼区间的解析解系数都依赖这些中间量，先算好省得每帧重算 */
    const disc = z * z - 1;
    if (disc < -1e-9) {
      this.omegaD = omega * Math.sqrt(-disc);      // 欠阻尼：omega sqrt(1-zeta^2)
      this.overdamped = false;
    } else if (disc > 1e-9) {
      this.omegaH = omega * Math.sqrt(disc);       // 过阻尼：omega sqrt(zeta^2-1)
      this.overdamped = true;
    } else {
      this.overdamped = false;                     // 临界阻尼：omegaD = 0，单独一支
    }
    return this;
  }

  /* 精确推进 dt 秒。target 在这一步内视为常量。 */
  step(target, dt) {
    if (!(dt > 0)) return this.x;
    const { omega, zeta } = this;
    const x0 = this.x;
    const v0 = this.v;
    // 相对平衡点的位移，以及它的一阶项
    const d = x0 - target;
    const zd = v0 + zeta * omega * d;
    let nx, nv;

    if (this.overdamped) {
      /* 过阻尼：两个指数衰减。zeta > 1 保证 zeta*omega ± omegaH 都为正，
       * 所以两个指数都不会上溢——这正是不能用 cosh/sinh 写的原因，
       * t 大一点 cosh 就会炸。 */
      const a = Math.exp(-(zeta * omega - this.omegaH) * dt);
      const b = Math.exp(-(zeta * omega + this.omegaH) * dt);
      const ca = 0.5 * (a + b);
      const cb = 0.5 * (a - b);
      const B = zd / this.omegaH;
      nx = ca * d + cb * B;
      nv = 0.5 * (-(zeta * omega - this.omegaH) * a - (zeta * omega + this.omegaH) * b) * d +
           0.5 * (-(zeta * omega - this.omegaH) * a + (zeta * omega + this.omegaH) * b) * B;
    } else if (zeta < 1 - 1e-9) {
      /* 欠阻尼：指数包络乘简谐，头发/呆毛要的就是这一支。
       *
       * x(t) = T + e^(-zeta*omega*t) * [ d*cos(wd*t) + (zd/wd)*sin(wd*t) ]
       * 对 t 求导，注意指数包络本身也要带进去：
       *   v = e^(-zeta*omega*t) * [ (zd - zeta*omega*d)*cos(wd*t)
       *                              - (d*wd + zeta*omega*zd/wd)*sin(wd*t) ]
       * 漏掉指数那一项的 -zeta*omega*d*cos 会让速度在第一步就错掉，
       * 表现为振幅指数增长——能量单调性判据会当场抓到。 */
      const wd = this.omegaD;
      const e = Math.exp(-zeta * omega * dt);
      const c = Math.cos(wd * dt);
      const s = Math.sin(wd * dt);
      nx = e * (d * c + (zd / wd) * s);
      nv = e * ((zd - zeta * omega * d) * c
                - (d * wd + zeta * omega * zd / wd) * s);
    } else {
      /* 临界阻尼：omegaD -> 0 的极限，一支退化的指数 */
      const e = Math.exp(-omega * dt);
      nx = e * (d + zd * dt);
      nv = e * (zd - omega * (d + zd * dt));
    }

    this.x = target + nx;
    this.v = nv;
    return this.x;
  }

  /* 踢一脚：给速度一个冲量而不动位置。
   * 点头、惊讶、被打断时的"反应"都是这么做的——比把目标挪到远处再等它
   * 弹回来自然，因为后者会在到位后继续过冲，听起来像犹豫。 */
  kick(dv) {
    this.v += Number(dv) || 0;
    return this;
  }

  /* 传送：位置跳变不是运动，速度必须一起丢掉。
   * 只重置位置的话，这个弹簧下一帧算出的速度会是天文数字，
   * 接下来好几秒都在画面外抽搐。传送是调用方知道的事实，
   * 所以由调用方显式声明，别用"位移超过阈值"去猜——
   * 合法的快速运动也会触发，那个症状比原问题更难查。 */
  reset(value = this.x) {
    this.x = value;
    this.v = 0;
    return this;
  }
}

/* 驱动仲裁：多个驱动往同一个参数上叠加偏移。
 *
 * 多个姿态偏移可以同时贡献，例如情绪的歪头与说话时的轻微律动。
 * 眨眼由运行时在姿态仲裁后乘到眼睛开度上，保留表情开度和完整闭合。
 * 说话时的张口由 lip 驱动用 mute() 独占，挡住手势层残留的嘴型。
 *
 * 每帧 begin() 清空各层，驱动只写它这一帧真正负责的键。
 */
export class Rigger {
  constructor(neutral = {}) {
    this.neutral = Object.fromEntries(ALL_PARAMS.map((key) => [key,
      clampParam(key, neutral[key] ?? DEFAULT_NEUTRAL[key]),
    ]));
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
    const n = Number(value);
    const baseline = this.neutral[key] ?? 0;
    this.add(name, key, (Number.isFinite(n) ? n : baseline) - baseline);
  }

  add(name, key, delta) {
    const layer = this.layers.get(name);
    if (!layer) throw new Error(`未注册的驱动: ${name}`);
    const n = Number(delta);
    layer.values[key] = (layer.values[key] || 0) + (Number.isFinite(n) ? n : 0);
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
      let total = this.neutral[key] ?? 0;
      for (let i = this.order.length - 1; i >= 0; i--) {
        const layer = this.order[i][1];
        const v = layer.values[key];
        if (v === undefined) continue;
        total += v;
        if (layer.mutes.has(key)) break;
      }
      store.target[key] = clampParam(key, total);
    }
  }
}
