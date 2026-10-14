/* 待机驱动：没人下指令时，自己该有的微动。
 *
 * 静态形象看着像贴纸，活着看着像在听人说话，差别基本就在这几条：
 * 眨眼（泊松过程，不是等间隔）、眼神有明确的注视对象、
 * 呼吸、以及一层低频噪声驱动的头微动。
 *
 * 这里有两条时间尺度，不��能混在一起调：
 *   快层（每帧）  眨眼、眼跳、呼吸、头微动 —— 反应必须在几十毫秒内完成
 *   慢层（几十秒）精神状态 —— 从精神到犯困要走半分钟，攒一��就立刻回来
 * 把两者都塞进同一套参数，最后的结果必然是"要么呆滞，要么神经质"：
 * 快层想要长时间不眨眼（像人发呆），慢层却想让眼睛有生气地一直在动。
 */

import { makeRng, clamp, clamp01, lerp, smoothstep } from "../params.js";

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

/* 延迟线：按固定时长回放过去的输入。
 *
 * 眼-头协调要用它做时序差：要么让头晚于眼 35ms，要么让眼晚于头 100ms。
 * 不需要通用的重采样实现——输入本身就是每秒 60 次的采样，
 * 按采样计数延迟再线性插值就够了。
 *
 * 为什么不用 setTimeout：标签页被节流时定时器不准，
 * 而"头比眼晚 35ms"这种量级的时序差，一旦被拉到 500ms 就完全读不出来了。
 * 这里全部挂在帧时钟上，时间关系不会因为后台标签页而崩掉。
 */
class DelayLine {
  constructor(maxDelay = 0.3) {
    this.buf = [];
    this.max = maxDelay;
  }
  push(t, x, y) {
    this.buf.push({ t, x, y });
    // 只保留可能用得上的历史
    const cut = t - this.max - 0.2;
    while (this.buf.length > 2 && this.buf[0].t < cut) this.buf.shift();
  }
  /* 取 t 时刻的值；早于缓冲起点就返回起点值（当作已经稳定） */
  at(t, fallbackX = 0, fallbackY = 0) {
    const b = this.buf;
    if (!b.length) return { x: fallbackX, y: fallbackY };
    if (t <= b[0].t) return { x: b[0].x, y: b[0].y };
    for (let i = b.length - 1; i >= 0; i--) {
      if (b[i].t <= t) {
        const cur = b[i];
        const nxt = b[i + 1];
        if (!nxt) return { x: cur.x, y: cur.y };
        const span = nxt.t - cur.t;
        const k = span > 1e-9 ? (t - cur.t) / span : 0;
        return { x: lerp(cur.x, nxt.x, k), y: lerp(cur.y, nxt.y, k) };
      }
    }
    return { x: b[0].x, y: b[0].y };
  }
  clear() {
    this.buf.length = 0;
  }
}

const DEFAULTS = {
  /* 眨眼三段：闭得快 -> 顶上停一下 -> 睁得慢。
   * 闭眼和睁眼用同一个时长是错的，真人闭眼约 70ms、睁开约 100ms，
   * 快慢不对称的眨眼一眼就能看出是假的。对称版本读起来像机械 shutter。 */
  blink: {
    mean_interval: 3.4,
    min_interval: 0.9,
    double_prob: 0.18,
    close_ms: 70,
    hold_ms: 20,
    open_ms: 110,
    double_gap_ms: 240,
  },
  saccade: { mean_interval: 1.9, max_shift: 0.55, duration: 0.22 },
  breath: { period: 4.1, depth: 1.0 },
  sway: { amp_x: 0.12, amp_y: 0.085, speed_x: 0.16, speed_y: 0.13 },
  speaking_sway: { amp: 0.18, freq: 2.7 },

  /* ---------- 眼-头协调 ----------
   *
   * 原来的做法是眼睛直接跳到注视点，头部只做一层和注视无关的噪声，
   * 于是"看哪"这件事从头到尾只有眼睛在参与。真人不是这样：
   * 移视距离小于约 15~20 度时只动眼睛，超过这个门槛头才被"征用"
   *（recruit），而且头和眼不是同时动的。
   *
   * 时序上分两种情况，这是关键区别：
   *
   *   可预期的注视点（我们自己决定看哪：思考时飘开、听你说话时低头）
   *     头**先**动，比眼早约 100ms。头重眼轻，头是准备好之后的那个动作。
   *
   *   不可预期的刺激（鼠标突然跑到别处）
   *     眼**先**动，头晚 20~50ms 才跟上。反应性的注视是眼先看到，头后跟上。
   *
   * 两种时序做同一个动作，在完全相同的注视点上读出来的东西完全不同，
   * 所以按"这个目标是不是我们预谋的"来选时序，而不是统一处理。
   *
   * 数值都是归一化眼动单位：全幅 2.0 大致相当于眼球的常规活动范围。
   * 换算成角度是粗略的（recruit≈11~14 度、head_capacity≈30 度），
   * 之所以不给精确度数，是因为 2D 侧根本没有眼球的角位移可依，
   * 这里只需要"什么时候该让头参与"这个比例关系是对的。 */
  gaze_coord: {
    recruit: 0.42,         // 移视幅度超过它，头才参与
    head_capacity: 1.15,   // 头部最多能贡献多少眼动当量的转动
    head_lead: 0.10,       // 可预期注视：头领先眼的时间
    head_lag: 0.035,       // 反应性注视：头落后眼的时间
    blink_on_shift: 0.75,  // 超过这个幅度的移视，会在到位后补一次眨眼
    refractory: 0.15,      // 两次眼跳之间的最小间隔（生理不应期）
    reactive_reach: 1.0,   // 鼠标等反应性目标按此系数抬高门槛
    /* 眼跳-眨眼互相抑制。
     *
     * 这里的教训值得写下来：最早实现的是"大幅移视时立刻眨一下眼"。
     * 看起来很合理，实际上是反的。控制眼跳和眨眼的是同一套机制
     * （omnipause neuron，OPN）——它在眼跳期间放电、扣住眨眼，
     * 在眨眼期间也扣住眼跳。所以两者是互相抑制的：
     *   - 眨眼期间发起的大眼跳，潜伏期会明显变长
     *   - 眼跳期间插入的眨眼，会让眼跳的峰值速度被削掉
     * 生理上根本不会发生的组合，正是我们最想避免的那个：
     * 大幅移视时闭眼，画面上就变成"眼球在上眼睑下面滑过去"。
     *
     * 真人的模式是：大眼跳走完之后往往补一次眨眼
     * （盖住角膜上扫那段盲区），而不是在眼跳过程中闭眼。
     * 所以这里是"到位之后"再安排，不是"开始时"就眨。 */
    opn_gate: true,        // 眼跳期间不许眨眼
    opn_settle: 0.06,      // 眼跳结束后多久才允许眨眼
    opn_lock: 0.08,        // 眨眼期间不许发起眼跳
  },

  gaze: {
    targets: { viewer: { x: 0, y: 0, drift: 0.10 } },
    move: { duration: 0.14 },
    micro: { mean_interval: 0.85, radius: 0.11, duration: 0.10 },
    /* 眼跳是弹道运动：位移越大耗时越长，但有个上限。
     * 恒定时长会让小扫视显得拖沓、大扫视显得瞬移；
     * 真实眼跳最快约 800 度/秒，按归一化眼动幅度折算大致是 3.5 单位/秒。
     * 下限 35ms 对应微眼跳的实测时长（30~50ms），低于这个数会像在抽搐。 */
    speed: 3.5,
    min_duration: 0.035,
    max_duration: 0.18,
  },
  blink_while_speaking: 0.65,   // 说话时眨眼更频繁，这个是间隔乘数

  /* ---------- 唤醒度/效价如何改写生理通道 ----------
   *
   * 这不是"情绪表情的加强版"，而是三条**互相独立**的可测关系，
   * 都有实测数据支撑。分开做的好处是：即使表情标签没变，
   * 光改唤醒度，呼吸、眨眼、瞳孔也会一起变，观感立刻就不一样了。
   *
   * 1) 唤醒度 -> 眼跳时长。实测情绪唤醒度越高，眼跳**变慢**。
   *    反直觉但有道理：情绪激动时动作精度下降、运动变慢变重。
   *    （常见误解是"激动 -> 动得快更快"，那说的是眨眼不是眼跳。）
   *
   * 2) 效价 -> 眨眼频率。实测眨眼间隔与情绪效价呈线性关系：
   *    越正面，眨眼越频繁。这一条和犯困（energy 低）方向相反，
   *    所以两者取乘积而不是相加——困且开心的人眨眼是"又快又沉"，
   *    相加会算出互相抵消的错误结果。
   *
   * 3) 唤醒度 -> 呼吸频率。安静状态实测约 0.27Hz（≈16 次/分），
   *    深度呼吸时降到 0.21Hz。也就是平静偏快、放松偏慢，
   *    情绪激动则明显加快。呼吸不随情绪变的话，
   *    无论什么表情都像"屏住呼吸在演"。
   */
affect: {
      saccade_slow: 0.55,     // 唤醒度拉满时眼跳时长乘到几倍
      blink_positive: 0.45,  // 正面情绪下眨眼间隔缩到几成
      blink_negative: 1.35,  // 负面情绪下眨眼间隔拉到几倍
      breath_fast: 1.65,     // 唤醒度拉满时呼吸频率乘到几倍
      breath_min: 1.0,       // 精神饱满时的呼吸周期倍率（相对于 cfg.breath.period）
      breath_max: 1.6,       // 快睡着时的呼吸周期倍率（浅而慢）
    },

/* 慢层：精神状态。没有这层的话，待机就是永远同一副精神抖擞的样子，
   * 而真人安静待着会慢慢松下来。energy 1=精神 0=快睡着了。 */
  energy: {
    drain_per_sec: 0.022,        // 什么都不做时掉得多快（约 45 秒到底）
    drain_speaking: 0.35,        // 说话时也在掉，但慢得多（讲着话不该越讲越困）
    recover_per_sec: 0.9,        // 恢复速率（由 wake() 按互动强度给量）
    /* 各档位的触发阈值。写成数组而不是散落的 if，是为了在时间轴上一眼
     * 看清楚"什么时候开始犯困"，改档位时不用读实现。 */
    stages: [
      { at: 0.55, name: "drowsy" },   // 开始松，眼神飘得更远
      { at: 0.30, name: "sleepy" },   // 眨眼变慢变长
      { at: 0.10, name: "dozing" },   // 快睡着了，头一点一点往下
    ],
    blink_slowdown: 2.6,        // 最困时眨眼间隔乘到几倍（成倍，不是线性）
    blink_dilate: 2.0,          // 最困时单次眨眼时长乘到几倍
    droop_per_stage: 0.22,      // 每进一档头往下低多少（ParamAngleY 归一化量）
    droop_release: 0.6,          // 精神回来时抬头的速度（每秒）——和垂下不同速，
                               // 因为"一下子抬头"才读得出"注意到你了"
    /* 认知负荷眼神回避：
     * 当角色在"思考"或"认知负荷高"时，眼神会自然回避用户注视，
     * 这是真人思考时的自然行为——盯着人很难深度思考。
     * 这里用概率和幅度控制，不是硬性锁定。 */
    cognitive_gaze_aversion: {
      enabled: true,
      probability: 0.7,        // 思考时触发回避的概率
      max_angle: 0.6,          // 最大回避角度（归一化，约 35 度）
      duration_range: [1.5, 4.0], // 回避持续时间范围（秒）
      recovery_time: 0.5,      // 恢复注视的过渡时间
    },
  },
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
      energy: { ...DEFAULTS.energy, ...(cfg.energy || {}) },
      affect: { ...DEFAULTS.affect, ...(cfg.affect || {}) },
      gaze_coord: {
        ...DEFAULTS.gaze_coord,
        // 眼-头协调的参数住在 config/avatar.yaml 的 gaze.coord 下，
        // 和注视点放在一起（它们讲的是同一件事：她在看哪）。
        // 顶层 gaze_coord 仍然认，方便单测直接构造驱动而不必搭整份配置。
        ...(cfg.gaze?.coord || {}),
        ...(cfg.gaze_coord || {}),
      },
      gaze: {
        targets: { ...DEFAULTS.gaze.targets, ...(cfg.gaze?.targets || {}) },
        move: { ...DEFAULTS.gaze.move, ...(cfg.gaze?.move || {}) },
        micro: { ...DEFAULTS.gaze.micro, ...(cfg.gaze?.micro || {}) },
        speed: cfg.gaze?.speed ?? DEFAULTS.gaze.speed,
        min_duration: cfg.gaze?.min_duration ?? DEFAULTS.gaze.min_duration,
        max_duration: cfg.gaze?.max_duration ?? DEFAULTS.gaze.max_duration,
      },
    };

    this.rng = makeRng(0x51f3a7);
    this.noise = makeNoise(this.rng);

    this.t = 0;
    /* 连续情感维度的初值必须在眨眼调度器之前就位。
     * _sampleBlink() 会读 valence，而 nextBlink 是在这里首次计算的；
     * 顺序反了的话 valence 是 undefined，
     * clamp(undefined,-1,1) 返回 undefined、lerp 再把它变成 NaN，
     * 于是 nextBlink 变成 NaN —— 而 NaN 的比较恒为假，
     * "nextBlink <= 0" 永远不成立，眨眼会被静默地彻底关掉。
     * 这种故障没有任何报错，只表现为"她再也不眨眼了"。 */
    this.arousal = 0.25;
    this.valence = 0;
    this.nextBlink = this._sampleBlink(1, 1);
    this.blinkPhase = -1;         // >=0 表示正在眨
    this.blinkQueued = 0;
    this.blinkHold = 0;           // 双眨之间的间隔计时
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
    this.gazeContext = { drift: null, intervalScale: 1, headFollowScale: 1, headDelayScale: 1, headRecruitScale: 1 };
    this.nextMicro = this._sampleMicro();
    /* 自定义注视点（鼠标）没有配置里的 drift 半径，落点单独存一份，
     * 否则每帧微扫视都会去读 undefined 的 .drift 直接抛异常。 */
    this.customAnchor = { x: 0, y: 0 };

    /* --- 眼-头协调 --- */
    /* 眼跳的目标值和头转的目标值分开存：
     * 眼睛吃超出征用门槛之外的部分，头只承担它自己那份。
     * 两条延迟线负责制造"谁先动"的时间差。 */
    this.eyeDelay = new DelayLine(0.4);
    this.headDelay = new DelayLine(0.4);
    this.headX = 0;
    this.headY = 0;
    this.headFromX = 0;
    this.headFromY = 0;
    this.headToX = 0;
    this.headToY = 0;
    this.headT = 1;
    this.headDur = 0.14;
    this.lastSaccadeAt = -10;
    this.reactive = false;
    this.pendingShiftBlink = false;

    /* 情绪/手势层自己管眼珠，idle 只在没人管的时候接管 */
    this.gazeOwned = false;

    /* 鼠标跟随 */
    this.mouseFollow = false;
    this.mousePos = null;

    /* 慢层 */
    this.energy = 1;
    this.stage = "awake";
    this.stageIndex = -1;
    /* 呼吸相位用积分推进（0..1 循环）。初值取 0：
     * 挂在最底部相当于吸气刚开始，是中位姿态，不会有跳变。 */
    this.breathPhase = 0;

    /* 认知负荷眼神回避状态 */
    this.cognitiveAversion = {
      active: false,
      targetX: 0,
      targetY: 0,
      endTime: 0,
      recoveryEndTime: 0,
    };

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

  /* 情绪层每帧把连续情感维度灌进来。
   * 呼吸、眼跳、眨眼都用它，但**不影响** idle 自己的慢层
   * （精神状态由 interaction 决定，不由情绪决定）——
   * 两者是不同的因果链，混起来会出现"她一生气就犯困"这种荒谬结果。 */
  setAffect(arousal, valence) {
    if (Number.isFinite(arousal)) this.arousal = clamp01(arousal);
    if (Number.isFinite(valence)) this.valence = clamp(valence, -1, 1);
  }

  _sampleBlink(speaking, energy = 1) {
    const { mean_interval, min_interval } = this.cfg.blink;
    const e = this.cfg.energy;
    const af = this.cfg.affect;
    // 越困越不眨眼，且是成倍地少，不是线性地少
    const slow = lerp(1, e.blink_slowdown, 1 - clamp01(energy));
    /* 效价 -> 眨眼疏密。乘算而不是加算：犯困(慢) 和 正面(快)
     * 是两个独立维度，方向相反。加算会让"又困又开心"算出个中间值，
     * 而真实表现是"又快又沉"——两次眨眼之间隔很久，
     * 但每一次都很慢很重。乘算才能同时保住这两个特征。 */
    const valence = clamp(this.valence, -1, 1);
    const tone = valence >= 0
      ? lerp(1, af.blink_positive, valence)
      : lerp(1, af.blink_negative, -valence);
    // 指数分布抽样的等价写法：泊松过程的间隔
    return min_interval + -Math.log(1 - this.rng()) * mean_interval *
      (speaking ? this.cfg.blink_while_speaking : 1) * slow * tone;
  }

  _sampleMicro() {
    const g = this.cfg.gaze.micro;
    return g.mean_interval * (0.4 + this.rng()) * this.gazeContext.intervalScale;
  }

  // Dialogue supplies an attention target, while this driver retains movement,
  // micro-saccades and blink coordination. Reset restores the configured idle.
  setGazeContext({ drift = null, intervalScale = 1, headFollowScale = 1, headDelayScale = 1, headRecruitScale = 1 } = {}) {
    const radius = drift === null || !Number.isFinite(drift) ? null : clamp(drift, 0, .3);
    const interval = Number.isFinite(intervalScale) ? clamp(intervalScale, .5, 4) : 1;
    const old = this.gazeContext.intervalScale;
    if (interval !== old) this.nextMicro *= interval / old;
    const headFollow = Number.isFinite(headFollowScale) ? clamp(headFollowScale, 0, 1.2) : 1;
    const headDelay = Number.isFinite(headDelayScale) ? clamp(headDelayScale, .5, 2) : 1;
    const headRecruit = Number.isFinite(headRecruitScale) ? clamp(headRecruitScale, .5, 1.5) : 1;
    this.gazeContext = { drift: radius, intervalScale: interval, headFollowScale: headFollow,
      headDelayScale: headDelay, headRecruitScale: headRecruit };
  }

  /* 此刻眼睛是否正闭着（含闭合段、保持段、睁开段，以及双眨的间隔）。
   * 双眨的间隔也算"闭着"，否则 OPN 门控会在两次眨之间开个口子，
   * 眼跳正好插进去，二连眨就变成"一眨一半"——那是最难看的一种。 */
  _blinking() {
    return this.blinkPhase >= 0 || this.blinkHold > 0;
  }

  /* 换注视点。真人移视是弹道式的，duration 很短，
   * 慢吞吞地转过去反而像"眼珠在飘"。 */
  setGaze(name, immediate = false) {
    if (!this.cfg.gaze.targets[name]) name = "viewer";
    this.targetName = name;
    this.reactive = false;   // 我们自己决定的注视点 = 可预期 = 头先动
    const t = this.cfg.gaze.targets[name];
    if (immediate) {
      this.gazeX = this.gazeToX = t.x;
      this.gazeY = this.gazeToY = t.y;
      this.gazeT = 1;
      this.customAnchor = { x: t.x, y: t.y };
      this._applyImmediate(t.x, t.y);
      return;
    }
    this._moveTo(t.x, t.y, this.cfg.gaze.move.duration, true);
  }

  /* 立刻把眼和头都对到位，跳过所有过渡（挂载、重置时用） */
  _applyImmediate(x, y) {
    const c = this.cfg.gaze_coord;
    const mag = Math.hypot(x, y);
    const headMag = Math.max(0, Math.min(c.head_capacity,
      (mag - c.recruit * this.gazeContext.headRecruitScale) * this.gazeContext.headFollowScale));
    this.headX = this.headFromX = this.headToX = mag > 1e-6 ? (x / mag) * headMag : 0;
    this.headY = this.headFromY = this.headToY = mag > 1e-6 ? (y / mag) * headMag : 0;
    this.headT = 1;
    this.eyeDelay.clear();
    this.headDelay.clear();
    this.eyeDelay.push(this.t, x, y);
    this.headDelay.push(this.t, this.headToX, this.headToY);
  }

  /* 动态注视目标：角色看向指定位置（如鼠标）。
   * 这会覆盖当前的注视点，直到下一个 setGaze 调用。 */
  setGazeTarget(x, y) {
    const cx = clamp(x, -1.6, 1.6);
    const cy = clamp(y, -1.2, 1.2);
    // 每次显式落点都成为新的微扫视中心，不能沿用上一处 custom 落点。
    this.customAnchor = { x: cx, y: cy };
    this.targetName = "custom";
    /* 鼠标是"外来的"刺激，属于不可预期：这时眼先动、头后跟，
     * 和我们自己选注视点时的时序正好相反。 */
    this.reactive = true;
    this._moveTo(cx, cy, 0.12, true);
  }

  /* 启用/禁用鼠标跟随模式。 */
  setMouseFollow(enabled) {
    this.mouseFollow = !!enabled;
  }

  setMousePos(x, y) {
    this.mousePos = { x, y };
  }

  /* 落点之后的微注视抖动：围绕注视对象游走，不离开它。
   * 这里必须同时兼容命名注视点和自定义（鼠标）注视点：
   * 鼠标模式下 targetName 是 "custom"，配置里查不到，
   * 之前直接读 .drift 会抛异常，整个帧循环就此停掉。 */
  _drift() {
    const t = this.targetName === "custom"
      ? null
      : this.cfg.gaze.targets[this.targetName];
    const ax = t ? t.x : this.customAnchor.x;
    const ay = t ? t.y : this.customAnchor.y;
    const base = this.gazeContext.drift ?? (t ? t.drift : this.cfg.gaze.micro.radius);
    const r = base * this.motion.gazeDrift;
    const a = this.rng() * Math.PI * 2;
    const d = Math.sqrt(this.rng()) * r;
    this._moveTo(
      ax + Math.cos(a) * d,
      ay + Math.sin(a) * d * 0.65,
      this.cfg.gaze.micro.duration
    );
  }

  /* 弹道式眼跳：位移越大耗时越长，但有上下限。
   * 用同一个 duration 走天下会同时踩两个坑：
   * 微扫视被拖成慢慢飘（看着像眼神涣散），大扫视瞬间到位（像闪现）。
   *
   * 这里同时决定"头参不参与"以及"眼和头谁先动"。
   * 响应性注视（鼠标跑了）的时间关系是眼先头后；
   * 我们自己选的注视点是可预期的，头反而先动。 */
  _moveTo(x, y, duration, force = false) {
    const g = this.cfg.gaze;
    const c = this.cfg.gaze_coord;
    const dist = Math.hypot(x - this.gazeX, y - this.gazeY);

    /* 眼跳不应期：两次眼跳之间的最小间隔（约 150ms）。
     * 只约束"自发的"微扫视，不约束显式指令。
     *
     * 这个区分是必须的：setGaze("think") 是语义指令——"她在思考，
     * 于是看向别处"。用不应期去挡它的话，指令会在最忙的那 150ms 里
     * 被静默吞掉，表现为角色该反应的时候没反应。
     * 而微扫视本来就只是噪声性质的游走，挡掉几次根本看不出来。 */
    const since = this.t - this.lastSaccadeAt;
    if (!force && since < c.refractory && dist > 0.02) {
      return false;
    }
    /* 眨眼在位时不发起眼跳：OPN 在眨眼期间是放电的，
     * 这时候硬发眼跳，潜伏期会明显变长（真实数据里可到 +80ms）。
     * 表现上就是"眼睛闭着还在往那边挤"，非常难看。 */
    if (!force && c.opn_gate && this._blinking() && dist > 0.02) {
      return false;
    }
    if (dist > c.blink_on_shift && this.blinkPhase < 0 && this.blinkHold <= 0) {
      /* 大幅移视要"到位之后"补一次眨眼，不是过程中闭眼。
       * 这里只登记一个待办，真正触发在眼跳结束之后，见 update()。 */
      this.pendingShiftBlink = true;
    }
    this.lastSaccadeAt = this.t;

    /* ---- 分配给头的那一份 ----
     * 门槛以下完全不动头：真人小幅移视就是纯眼动，
     * 头跟着一起转会读成"整个人在紧张地看"。
     * 门槛以上按剩余容量分摊，头最多贡献 head_capacity。 */
    const mag = Math.hypot(x, y);
    const headMag = Math.max(0, Math.min(c.head_capacity,
      (mag - c.recruit * this.gazeContext.headRecruitScale) * this.gazeContext.headFollowScale));
    this.headToX = mag > 1e-6 ? (x / mag) * headMag : 0;
    this.headToY = mag > 1e-6 ? (y / mag) * headMag : 0;

    /* 眼睛只负责"扣掉头那一份之后剩下的"。
     *
     * 让眼睛也直奔完整目标、再让头额外加上去是最容易犯的错：
     * 那样稳态下眼+头会等于两倍目标，头转得比要求的还多，
     * 而且偏得越远偏得越离谱（"think" 目标眼到 0.62、头再加 0.3，
     * 实际朝向了 0.92 之外的方向）。
     */
    this.gazeFromX = this.gazeX;
    this.gazeFromY = this.gazeY;
    this.gazeToX = x - this.headToX;
    this.gazeToY = y - this.headToY;
    this.gazeT = 0;
    if (duration === null) {
      /* 时长按眼睛实际要走的距离算，而不是按总距离：
       * 头分担得越多，眼要走的就越少，眼跳也就越快——
       * 这正是真实情况：大幅度移视里眼的行程明显短于头。 */
      const base = Math.hypot(this.gazeToX - this.gazeFromX,
                              this.gazeToY - this.gazeFromY) / g.speed;
      /* 唤醒度拉高 -> 眼跳变慢（实测关系，和"激动=眼跳快"的直觉相反）。
       * 只在给 duration=null（弹道自算）时生效：显式指令的时长是
       * 语义决定的，不该被情绪改写。 */
      const slow = 1 + this.cfg.affect.saccade_slow * clamp01(this.arousal);
      this.gazeDur = clamp(base * slow, g.min_duration, g.max_duration);
    } else {
      this.gazeDur = Math.max(0.02, duration);
    }

    this.headFromX = this.headX;
    this.headFromY = this.headY;
    this.headT = 0;
    /* 头比眼慢一点：头的转动惯量更大 */
    this.headDur = this.gazeDur * 1.25;
    return true;
  }

  /* 用户有动作了：精神立刻回来。
   * 慢层的意义在于"没人理她时会慢慢松下来"，
   * 一旦有任何互动就必须立刻弹回原状态，否则会变成"叫不醒"。 */
  wake(amount = 1) {
    this.energy = clamp01(this.energy + amount);
    if (this.energy > this.cfg.energy.stages[this.cfg.energy.stages.length - 1].at) {
      this.stage = "awake";
      this.stageIndex = -1;
    }
  }

  _updateEnergy(dt, speaking) {
    const e = this.cfg.energy;
    if (speaking) {
      // 说话时也在消耗，但慢得多 —— 讲着话的角色不该越讲越困
      this.energy -= e.drain_speaking * dt;
    } else {
      this.energy -= e.drain_per_sec * dt;
    }
    this.energy = clamp01(this.energy);

    let idx = -1;
    for (let i = 0; i < e.stages.length; i++) {
      if (this.energy <= e.stages[i].at) idx = i;
    }
    this.stage = idx < 0 ? "awake" : e.stages[idx].name;
    this.stageIndex = idx;
  }

  /* 精神状态 -> 头往下低多少。醒来时用不同的速率抬回去，
   * 因为"一下子抬头"看着才像注意到你了。 */
  _droop(dt) {
    const e = this.cfg.energy;
    const target = this.stageIndex < 0 ? 0 : this.stageIndex * e.droop_per_stage;
    if (target > this.droopValue) {
      this.droopValue = target;
    } else {
      this.droopValue = Math.max(target, this.droopValue - e.droop_release * dt);
    }
    return this.droopValue;
  }

  update(dt, { speaking = false, rms = 0 } = {}) {
    this.t += dt;
    if (this.droopValue === undefined) this.droopValue = 0;

    this._updateEnergy(dt, speaking);

    /* --- 眨眼（说话时更频繁，越困越慢越长） --- */
    /* 双眨的第二下和"下一次眨眼的计时"必须分开。
     * 共用一个计时器的话，第二下要么立刻触发（连成一次长眨），
     * 要么被下一轮的间隔计时吃掉（双眨随机消失）。 */
    if (this.blinkHold > 0) {
      this.blinkHold -= dt;
      if (this.blinkHold <= 0) {
        this.blinkHold = 0;
        if (this.blinkQueued > 0) {
          this.blinkQueued -= 1;
          this.blinkPhase = 0;
        }
      }
    }

    /* 大幅移视触发的眨眼，排在眼跳**结束**之后。
     *
     * 早先的实现是在移视一开始就眨，那正好和 OPN 的门控方向相反：
     * 眼跳期间 OPN 正在扣住眨眼，所以真实系统里这两种动作会互相拖慢，
     * 而我们硬要让它们同时发生，结果是眼球从上眼睑底下滑过去。
     *
     * 所以这里要等三个条件都满足：
     *   眼跳已到位（gazeT >= 1）、OPN 的扣留期已过（settle）、
     *   而且此刻没有新的眼跳在飞（有新的就把待办推后，别连着眨）
     * 缺任何一条都不眨——迟一次眨眼没有任何代价，
     * 错时机的眨眼则直接毁掉那一帧。 */
    const c2 = this.cfg.gaze_coord;
    const saccadeInFlight = this.gazeT < 1;
    if (this.pendingShiftBlink && !saccadeInFlight && !this._blinking()) {
      const sinceSaccade = this.t - this.lastSaccadeAt;
      if (sinceSaccade >= c2.opn_settle) {
        this.pendingShiftBlink = false;
        this.blinkPhase = 0;
        this.blinkQueued = 0;
        /* 插队之后把随机计时器推开，否则紧接着还会再眨一次，
         * 看起来像连续眨三下。 */
        this.nextBlink = this._sampleBlink(speaking, this.energy);
      }
    }

    /* 随机眨眼同样要避开眼跳期间：同一条 OPN 门控，没有例外。 */
    this.nextBlink -= dt;
    if (this.blinkPhase < 0 && this.blinkHold <= 0 && this.nextBlink <= 0) {
      if (!(c2.opn_gate && saccadeInFlight)) {
        this.blinkPhase = 0;
        this.blinkQueued = this.rng() < this.cfg.blink.double_prob ? 1 : 0;
        this.nextBlink = this._sampleBlink(speaking, this.energy);
      }
      /* 压住不发，不把 nextBlink 重置为 0。
       * 之前写 this.nextBlink = 0 会导致眼跳结束的那一帧 nextBlink=0，
       * 立刻再次触发眨眼，紧接着 _sampleBlink 可能给个很小的值，
       * 于是连着眨好几次——眨眼频率会被放大几十倍。 */
      // 不做任何事，让 nextBlink 继续倒计时（或保持 <=0）
    }
    if (this.blinkPhase >= 0) {
      const b = this.cfg.blink;
      const e = this.cfg.energy;
      // 犯困时单次眨眼整体放慢（睡意重的人眨得慢而沉）
      const dilate = lerp(1, e.blink_dilate, 1 - this.energy);
      const close = (b.close_ms / 1000) * dilate;
      const hold = (b.hold_ms / 1000) * dilate;
      // 睁开比闭眼慢 —— 这是真实眨眼最容易被做错的一点
      const open = (b.open_ms / 1000) * dilate;
      const previousPhase = this.blinkPhase;
      this.blinkPhase += dt;
      if (this.blinkPhase < close) {
        // 闭合段：平滑但起步快，用 smoothstep 让起手不至于太软
        this.eyeLevel = 1 - smoothstep(this.blinkPhase / close);
      } else if (this.blinkPhase < close + hold || previousPhase < close) {
        // 20/30 FPS 可一步跨过短暂闭合保持段；跨峰值这一帧仍完整闭眼。
        // 保留已经推进的时钟，不回拨阶段或重新触发眨眼。
        this.eyeLevel = 0;
      } else {
        const k = (this.blinkPhase - close - hold) / open;
        this.eyeLevel = k >= 1 ? 1 : k;
      }
      if (this.blinkPhase >= close + hold + open) {
        this.blinkPhase = -1;
        this.eyeLevel = 1;
        if (this.blinkQueued > 0) {
          // 双眨之间要留一段间隔，直接接上去会连成一次长眨
          this.blinkHold = b.double_gap_ms / 1000;
        }
      }
    }

    /* --- 注视点 --- */
    if (!this.gazeOwned) {
      // 鼠标跟随模式
      if (this.mouseFollow && this.mousePos) {
        this.targetName = "custom";
        this.customAnchor = { x: this.mousePos.x, y: this.mousePos.y };
        this.reactive = true;   // 鼠标是外来刺激 -> 眼先头后
        // 直接跟随不做平滑：鼠标已经在动，再叠一层弹簧只会显得迟钝。
        // 但仍然走 _moveTo 的弹道时长约束，否则鼠标瞬移会让眼珠"闪"过去。
        this._moveTo(this.mousePos.x, this.mousePos.y, null, true);
      } else {
        this.nextMicro -= dt;
        if (this.nextMicro <= 0) {
          this._drift();
          this.nextMicro = this._sampleMicro();
        }
      }
      if (this.gazeT < 1) {
        this.gazeT = Math.min(1, this.gazeT + dt / this.gazeDur);
        // 眼跳是等速运动（linear），不是缓入缓出：真实眼跳在启动后
        // 是匀速冲到位的，ease-in 会读成"眼皮在拖着眼球走"。
        this.gazeX = lerp(this.gazeFromX, this.gazeToX, this.gazeT);
        this.gazeY = lerp(this.gazeFromY, this.gazeToY, this.gazeT);
      }
      // 头走自己那条通道，幅度和节奏都独立
      if (this.headT < 1) {
        this.headT = Math.min(1, this.headT + dt / this.headDur);
        this.headX = lerp(this.headFromX, this.headToX, this.headT);
        this.headY = lerp(this.headFromY, this.headToY, this.headT);
      }
} else {
        // 别人接管视线时把游走时钟按住，回来再继续
        this.nextMicro = Math.max(this.nextMicro, 0.4);
      }

    /* ---- 认知负荷眼神回避 ----
     * 当角色在"思考"或"认知负荷高"时，眼神会自然回避用户注视。
     * 这里用概率触发、随机角度、随机持续时间，不是硬性锁定。
     * 只有在没有外部接管视线(gazeOwned=false)且没有鼠标跟随时才生效。 */
    if (!this.gazeOwned && !this.mouseFollow && this.cfg.cognitive_gaze_aversion?.enabled) {
      const cga = this.cfg.cognitive_gaze_aversion;
      const now = this.t;
      const ca = this.cognitiveAversion;

      if (!ca.active) {
        // 概率触发新的回避
        if (this.rng() < cga.probability * dt) {
          ca.active = true;
          const angle = this.rng() * Math.PI * 2;
          const dist = cga.max_angle * (0.3 + this.rng() * 0.7); // 30%~100% 最大角度
          ca.targetX = Math.cos(angle) * dist;
          ca.targetY = Math.sin(angle) * dist;
          const duration = cga.duration_range[0] + this.rng() * (cga.duration_range[1] - cga.duration_range[0]);
          ca.endTime = this.t + duration;
          ca.recoveryEndTime = ca.endTime + cga.recovery_time;
        }
      }

      if (ca.active) {
        if (now >= ca.endTime) {
          ca.active = false;
        } else {
          // 眼神偏离目标
          const elapsed = now - (ca.endTime - (ca.recoveryEndTime - ca.endTime + cga.recovery_time));
          if (now < ca.endTime) {
            // 回避阶段：朝向目标偏移
            const t = elapsed / (ca.endTime - (ca.endTime - ca.recoveryEndTime + cga.recovery_time));
            const eased = t * t * (3 - 2 * t); // smoothstep
            this.gazeX += ca.targetX * eased;
            this.gazeY += ca.targetY * eased;
          } else if (now < ca.recoveryEndTime) {
            // 恢复阶段：平滑回到原注视点
            const t = (now - ca.endTime) / cga.recovery_time;
            const eased = t * t * (3 - 2 * t);
            this.gazeX += ca.targetX * (1 - eased);
            this.gazeY += ca.targetY * (1 - eased);
          } else {
            ca.active = false;
          }
        }
      }
    }

    /* ---- 相互凝视调节 ----
     * 真人持续注视对方眼睛有上限（约 3-7 秒），超过会自然移开。
     * 只有在相互注视（用户看角色、角色看用户）时生效。
     * 这里用最大凝视时长 + 强制断开时间来模拟。 */
    if (this.cfg.gaze?.mutual_gaze?.enabled && !this.gazeOwned) {
      const mg = this.cfg.gaze.mutual_gaze;
      if (!this._mutualGazeState) {
        this._mutualGazeState = {
          gazingSince: 0,
          breakingSince: 0,
          inBreak: false,
        };
      }
      const mgs = this._mutualGazeState;

      // 简化判断：假设目标注视点是 viewer(0,0) 时为相互凝视
      const lookingAtViewer = this.targetName === "viewer";
      const now = this.t;

      if (!mgs.inBreak) {
        if (lookingAtViewer) {
          if (mgs.gazingSince === 0) mgs.gazingSince = now;
          if (now - mgs.gazingSince > mg.max_duration) {
            // 达到最大凝视时长，强制断开
            mgs.inBreak = true;
            mgs.breakingSince = now;
            mgs.breakDuration = mg.break_duration[0] + this.rng() * (mg.break_duration[1] - mg.break_duration[0]);
            // 设置一个回避目标（随机偏移）
            const angle = this.rng() * Math.PI * 2;
            const dist = 0.5 + this.rng() * 0.5;
            this.cognitiveAversion.targetX = Math.cos(angle) * dist;
            this.cognitiveAversion.targetY = Math.sin(angle) * dist;
            this.cognitiveAversion.active = true;
            this.cognitiveAversion.endTime = now + mg.break_duration[1];
            this.cognitiveAversion.recoveryEndTime = now + mg.break_duration[1] + mg.recovery_delay;
          }
        } else {
          mgs.gazingSince = 0;
        }
      } else {
        if (now - mgs.breakingSince > mgs.breakDuration) {
          mgs.inBreak = false;
          mgs.breakingSince = 0;
          // 恢复延迟
          this.cognitiveAversion.recoveryEndTime = this.t + mg.recovery_delay;
        }
      }
    }

    /* ---- 眼-头时序差 ----
     * 两条延迟线在这里查询"对方延迟之后"的值：
     *   可预期注视：眼晚 head_lead，头先动
     *   反应性注视：头晚 head_lag，眼先动
     * 时序差只有 35~100ms 量级，靠眼跳本身的速度曲线放大是放不出来的，
     * 必须真的把其中一条通道延后。 */
    const c = this.cfg.gaze_coord;
    this.eyeDelay.push(this.t, this.gazeX, this.gazeY);
    this.headDelay.push(this.t, this.headX, this.headY);
    const eyeShift = this.reactive
      ? this.eyeDelay.at(this.t)
      : this.eyeDelay.at(this.t - c.head_lead, this.gazeX, this.gazeY);
    const headShift = this.reactive
      ? this.headDelay.at(this.t - Math.min(.4, c.head_lag * this.gazeContext.headDelayScale), this.headX, this.headY)
      : this.headDelay.at(this.t, this.headX, this.headY);

    const droop = this._droop(dt);
    /* 呼吸频率跟着唤醒度走。
     *
     * 安静状态实测约 0.27Hz（周期 ≈3.7s），深度呼吸降到 0.21Hz。
     * 情绪激动时呼吸明显加快，而且**呼气变浅、吸气变急**，
     * 所以这里不只改频率，也让深度随唤醒度略降——
     * 只是频率不变的话，那口气听起来像"憋着演"。
     *
     * 频��靠积分而不是直接取 t/period：周期一变，
     * 直接用 t/period 会让呼吸相位瞬间跳变（声音没变、呼吸突然重来一次），
     * 积分则保证周期是连续变化的，过渡听不出来。 */
    this.breathPhase += dt / this._breathPeriod();
    if (this.breathPhase > 1) this.breathPhase -= 1;
    const breathDepth = this.cfg.breath.depth * lerp(1, 0.72, clamp01(this.arousal));

    return {
      // blink 是"闭眼量"（0 睁 / 1 闭），直接作为 ParamEyeLOpen 的负增量
      blink: clamp01(1 - this.eyeLevel),
      gazeX: eyeShift.x,
      gazeY: eyeShift.y,
      /* 头要跟着"看"过去转。这一项和下面的 swayX/swayY 是两回事：
       * sway 是与注视无关的底层噪声，这里是注视本身带出来的转头。
       * 少了它，"看哪"这件事从头到尾只有眼睛参与，头永远不动。 */
      headX: headShift.x,
      headY: headShift.y,
      breath: Math.sin(this.breathPhase * Math.PI * 2),
      breathDepth,
      swayX: this.noise(this.t / this.cfg.sway.speed_x) * this.cfg.sway.amp_x * this.motion.sway,
      swayY: this.noise(this.t / this.cfg.sway.speed_y + 41.7) * this.cfg.sway.amp_y * this.motion.sway,
      bob: speaking
        ? Math.sin(this.t * Math.PI * 2 * this.cfg.speaking_sway.freq) *
          this.cfg.speaking_sway.amp * clamp(rms) * this.motion.bob
        : 0,
      /* 慢层输出：头一点一点往下垂。情绪层会压过它（优先级更高），
       * 所以这里不需要怕和说话姿势打架。 */
      droop,
      energy: this.energy,
      stage: this.stage,
      arousal: this.arousal,
      valence: this.valence,
    };
  }

  /* 当前呼吸周期。犯困会把呼吸拉长（浅而慢），
   * 情绪唤醒会把呼吸压短（急而浅）。两者相乘而不是相加，
   * 因为它们作用在不同的量纲上：犯困改的是"慢"，情绪改的是"急"。
   *
   * 缩放基准是"精神饱满 + 情绪平静"，也就是 cfg.breath.period 本来代表的
   * 那个状态——配置里的数值要继续有效，不能被一个理论值盖掉。
   * 之前这里把 period 除以实测的 3.7s 再去乘倍率，结果是
   * 中等状态下算出来只有 1.1s：把"相对某个基准的倍率"
   * 错当成"绝对周期"，基准和倍率混在一起乘，值就缩掉了一截。 */
  _breathPeriod() {
    const af = this.cfg.affect;
    const byEnergy = lerp(af.breath_max, af.breath_min, clamp01(this.energy));
    const byArousal = 1 / lerp(1, af.breath_fast, clamp01(this.arousal));
    return Math.max(1.2, this.cfg.breath.period * byEnergy * byArousal);
  }

  /* 让出视线控制权（比如 emotion/gesture 正在把眼睛转向别处） */
  claimGaze(claimed) {
    this.gazeOwned = claimed;
  }
}
