/* 情绪驱动：把后端已经发过来的情绪标签变成持续的表情姿态。
 *
 * 后端 detect_emotion() 是整轮一次性判定的 7 选 1，没有强度也没有混合，
 * 所以这里做两件事补上：显式交叉淡化（blend 秒）和"表情维持"——
 * 情绪切换后表情停在那儿，直到下一个情绪到来，而不是飘回中位。
 *
 * ---------- 为什么还要一个连续维度 ----------
 *
 * 只有离散标签的话，"得意"永远是同一个强度：不管她只是被轻轻逗了一下，
 * 还是真的得意到不行，脸都是那一张。七选一的结果是所有情绪的
 * 表达强度完全相同，于是观感上非常平——像在翻表情包。
 *
 * 所以每个情绪除了 shift 之外，还带一个 (arousal, valence) 二维坐标：
 *   arousal   唤醒度，0 平静 ~ 1 亢奋。决定"表情打多大"、
 *             眼跳多快、呼吸多快、瞳孔放多大
 *   valence   效价，-1 消极 ~ +1 积极。决定眨眼的疏密
 *            （正面情绪下眨眼更频繁，实测是线性关系）
 *
 * 这两个值在情绪切换时也走和 shift 一样的弹簧混合，
 * 所以从"收敛"走到"得意"时，表情是渐变过去的，
 * 而不是突然跳一档。
 *
 * 更重要的是它们**独立于表情**地驱动生理通道：
 * 即使标签没变，只改 arousal，呼吸和眨眼也会跟着变。
 * 这是离散标签做不到的——7 个标签最多只能表达 7 种状态。
 */

import { lerp, Spring, clamp, clamp01, clampParam, DEFAULT_NEUTRAL } from "../params.js";

/* 没有配置时的兜底：中性、平静。 */
const NEUTRAL_AFFECT = { arousal: 0.25, valence: 0.0 };

// Missing eye-smile intent is filled only for the new eyelid candidate.
// Explicit configured smile values continue to take precedence. These targets
// use the same emotion interpolation/arousal path as the existing face cues.
const LID_EMOTION_INTENTS = Object.freeze({
  调侃: Object.freeze({ ParamEyeLSmile: 0.154, ParamEyeRSmile: 0.110, ParamEyeLLower: 0.182, ParamEyeRLower: 0.130 }),
  得意: Object.freeze({ ParamEyeLSmile: 0.176, ParamEyeRSmile: 0.187, ParamEyeLLower: 0.208, ParamEyeRLower: 0.221 }),
});

export class EmotionDriver {
  constructor(table = {}, neutral = {}) {
    this.table = table;
    this.eyeLidProfile = 'native';
    this.neutral = Object.fromEntries(Object.entries(DEFAULT_NEUTRAL).map(([key, fallback]) =>
      [key, clampParam(key, neutral[key] ?? fallback)]));
    this.current = null;       // 正在淡入的（自己的情绪）
    this.from = null;          // 正在淡出的
    this.k = 1;
    this.blend = 0.4;
    // 情绪过渡弹簧：频率比头部慢（情绪的变化比转头慢），低阻尼留一点回摆
    this.spring = new Spring(1.1, 0.42);
    this.springValue = 0;
    /* 连续情感维度。与 shift 同步混合，但单独出弹簧：
     * 唤醒度的变化比表情本身慢一点，因为生理反应总是滞后于表情
     * （脸先僵住、呼吸和瞳孔随后才跟上）。 */
    this.arousalSpring = new Spring(0.7, 0.75);
    this.valenceSpring = new Spring(0.7, 0.75);
    this.arousal = NEUTRAL_AFFECT.arousal;
    this.valence = NEUTRAL_AFFECT.valence;

    /* ---- 情感共鸣/传染模式 ----
     *
     * 当开启时，set() 不再使用自己的情绪标签，
     * 而是接收外部识别到的用户情绪（如后端 detect_emotion 返回的标签），
     * 并表达匹配的情绪——这就是"情感共鸣"或"情感匹配"。
     *
     * 与简单的镜像不同，这里使用情绪标签驱动完整的表情+生理通道：
     *   - 表情 shift 按识别到的情绪标签混合
     *   - arousal/valence 也按识别到的情绪配置混合
     *   - 瞳孔、呼吸、眨眼、眼跳都会跟着变
     *
     * 这比简单的面部镜像高级：它能表达"我理解你很伤心"而不只是"你在皱眉"，
     * 并且能处理用户没露在脸上的情绪（如语义层面的伤心）。
     *
     * 默认开启：后端 detect_emotion 返回的就是用户情绪，
     * 所以 avatar 的情绪本身就是用户情绪，默认开启匹配模式
     * 即可实现"情感共鸣"。
     *
     * ---- 情绪传染动力学 ----
     *
     * 除了直接匹配，还支持"情绪传染"模式：
     * Avatar 的情绪会**渐进式地**向用户情绪靠拢，而不是瞬间跳变。
     * 这模拟真人的情绪传染：被他人情绪感染是渐进的过程。
     *
     * 控制参数（可在 config 里配置）：
     *   contagion_rate: 0~1，传染速率，越大越快被感染
     *   resistance: 0~1，抗性，越大越难被感染（如性格坚强的人）
     *   threshold: 情绪差异超过阈值才开始传染
     */
    this.matchingMode = true;
    this.matchingTarget = null;  // 当前匹配的目标情绪标签
    this.contagionMode = false;   // 情绪传染模式（渐进式传染）
    this.contagionRate = 0.3;     // 传染速率
    this.contagionResistance = 0.2; // 抗性
    this.contagionThreshold = 0.15; // 传染阈值
  }

  /* 拿到 config/avatar.yaml 的 emotions 段 */
  setTable(table) {
    this.table = table || {};
  }

  has(name) {
    return !!this.table[name];
  }

/* 情绪的 (arousal, valence)。缺配置时给中性值，
   * 而不是抛错或给 0——0 arousal 会让她看起来像睡着，
   * 而"没有配置"本来就不该改变已有行为之外的观感。 */
affect(name) {
    const e = this.table[name];
    if (!e) return NEUTRAL_AFFECT;
    return {
      arousal: Number.isFinite(e.arousal) ? e.arousal : NEUTRAL_AFFECT.arousal,
      valence: Number.isFinite(e.valence) ? e.valence : NEUTRAL_AFFECT.valence,
    };
  }

  /* 当前活跃的情感维度（匹配模式下用匹配目标，否则用自己的）。
   * 供外部（如 index.js 灌给 idle）读取。 */
  get activeAffect() {
    const name = this.matchingMode ? this.matchingTarget : this.current;
    return name ? this.affect(name) : NEUTRAL_AFFECT;
  }

  set(name) {
    if (!this.has(name)) name = "base";
    if (name === this.current && (!this.matchingMode || name === this.matchingTarget)) return false;
    this.from = this.matchingMode ? this.matchingTarget : this.current;
    this.k = 0;
    this.blend = Number(this.table[name]?.blend ?? 0.4);
    this.current = name;
    // 过渡进度属于这次切换；上一轮的弹簧位置不能成为新表情的起点。
    this.spring.reset(0);
    this.springValue = 0;
    // 情绪变化时给弹簧一个冲量，让它有过冲和回摆
    this.spring.v += 2.5;
    // 匹配模式下，set() 也同步更新匹配目标，保证单元测试兼容
    if (this.matchingMode) {
      this.matchingTarget = name;
    } else {
      this.matchingTarget = null;
    }
    return true;
  }

  /* 开启/关闭情感共鸣模式。
   *
   * 开启后，set() 将接受外部识别的用户情绪标签，
   * 并表达匹配的情绪（shift + arousal/valence 全部匹配）。
   * 这是比简单面部镜像更高级的"情感共鸣"：
   *   - 表情 shift 按识别到的情绪混合
   *   - arousal/valence 也按识别到的情绪混合
   *   - 瞳孔、呼吸、眨眼、眼跳都会跟着变
   *   - 能表达"我理解你很伤心"而不只是"你在皱眉" */
  setMatchingMode(enabled) {
    this.matchingMode = !!enabled;
    if (!enabled) {
      this.matchingTarget = null;
    }
  }

  /* 设置当前要匹配的目标情绪（仅在 matchingMode 下生效）。
   *
   * 这是给外部（如后端 detect_emotion 返回的标签）调用的接口。
   * 相当于在 matchingMode 下的 set()。 */
  setMatchingTarget(name) {
    if (!this.matchingMode) return false;
    if (!this.has(name)) name = "base";
    if (name === this.matchingTarget) return false;
    this.matchingTarget = name;
    // 匹配目标变化时也给弹簧冲量
    this.spring.v += 2.5;
    return true;
  }

  update(dt) {
    // 匹配模式下使用 matchingTarget，否则用 current
    const activeName = this.matchingMode ? this.matchingTarget : this.current;
    if (!activeName) return "base";

    const to = this.affect(activeName);
    const from = this.affect(this.from);

    // 用比表情更慢的弹簧单独推这两个维度
    this.arousal = this.arousalSpring.step(lerp(from.arousal, to.arousal, this.k), dt);
    this.valence = this.valenceSpring.step(lerp(from.valence, to.valence, this.k), dt);

    /* ---- 情绪传染动力学 ----
     * 当开启 contagionMode 时，Avatar 的情绪会**渐进式地**向用户情绪靠拢，
     * 而不是瞬间跳变。这模拟真人的情绪传染：被他人情绪感染是渐进的过程。
     *
     * 模型：d(avatar)/dt = rate * (user - avatar) * (1 - resistance) * max(0, |user - avatar| - threshold)
     * - rate: 传染速率
     * - resistance: 抗性（性格坚强的人不易被感染）
     * - threshold: 情绪差异需超过阈值才开始传染
     */
    if (this.contagionMode && this.matchingMode && this.matchingTarget) {
      const userAffect = this.affect(this.matchingTarget);
      const dArousal = userAffect.arousal - this.arousal;
      const dValence = userAffect.valence - this.valence;
      const diff = Math.sqrt(dArousal * dArousal + dValence * dValence);

      if (diff > this.contagionThreshold) {
        const rate = this.contagionRate * (1 - this.contagionResistance);
        this.arousal += dArousal * rate * dt;
        this.valence += dValence * rate * dt;
        // clamp 到 [-1, 1]
        this.arousal = clamp01(this.arousal);
        this.valence = clamp(this.valence, -1, 1);
      }
    }

    if (this.k >= 1) return activeName;
    this.k = Math.min(1, this.k + dt / Math.max(0.01, this.blend));
    // 弹簧跟随 k，有过冲和回摆
    this.springValue = this.spring.step(this.k, dt);
    return activeName;
  }

/* 返回本帧的绝对目标姿态；用弹簧值混合，有过冲和回摆。
 *
 * 相对中位的位移按 arousal 缩放，眼开度等绝对目标不会被乘成半睁。
 * arousal 较低时表达更克制，较高时靠近配置的完整表情。
 * 没有这一步的话所有情绪都是同一个音量，观感就是平的。 */
shift() {
    // 匹配模式下使用 matchingTarget 的 shift，否则用 current
    const activeName = this.matchingMode ? this.matchingTarget : this.current;
    if (!activeName) return {};

    const to = this._eyePose(activeName);
    if (this.k >= 1) {
      return this._scale(to, this.arousal);
    }
    const from = this._eyePose(this.from);
    // 弹簧值可能超过 1（过冲）或低于 0（回摆），clamp 到 -0.5..1.5
    const k = Math.max(-0.5, Math.min(1.5, this.springValue));
    const mixed = {};
    for (const key of new Set([...Object.keys(from), ...Object.keys(to)])) {
      const neutral = this.neutral[key] ?? 0;
      const a = Number(from[key] ?? neutral);
      const b = Number(to[key] ?? neutral);
      mixed[key] = lerp(Number.isFinite(a) ? a : neutral, Number.isFinite(b) ? b : neutral, k);
    }
    return this._scale(mixed, this.arousal);
  }

  setEyeLidProfile(profile) {
    this.eyeLidProfile = profile === 'seed-lids' ? profile : 'native';
  }

  _eyePose(name) {
    const configured = this.table[name]?.shift || {};
    const defaults = this.eyeLidProfile === 'seed-lids' ? LID_EMOTION_INTENTS[name] : null;
    if (!defaults) return configured;
    const pose = { ...configured };
    for (const side of ['L', 'R']) {
      const smile = `ParamEye${side}Smile`, lower = `ParamEye${side}Lower`;
      // An explicit value, including zero, owns this side's intent. Generate
      // both defaults before emotion interpolation only when both are missing.
      if (Object.hasOwn(configured, smile) || Object.hasOwn(configured, lower)) continue;
      pose[smile] = defaults[smile]; pose[lower] = defaults[lower];
    }
    return pose;
  }

  /* 唤醒度 -> 表情强度。留一个下限，避免 arousal 很低时脸完全不动：
   * 那读起来不是"克制"，是"卡住了"。0.45 就是"再怎么平静也有底妆"。 */
  _scale(shift, arousal) {
    const gain = 0.45 + 0.55 * clamp01(arousal);
    const out = {};
    for (const [key, value] of Object.entries(shift)) {
      const neutral = this.neutral[key] ?? 0;
      const target = Number(value);
      out[key] = neutral + ((Number.isFinite(target) ? target : neutral) - neutral) * gain;
    }
    return out;
  }

  /* 本帧是否由情绪接管视线（避免和 idle 的扫视打架） */
  get ownsGaze() {
    if (this.k >= 1) return false;
    const activeName = this.matchingMode ? this.matchingTarget : this.current;
    const owns = (name) => {
      const expression = this.table[name];
      if (!expression || expression.owns_gaze === false) return false;
      return ["ParamEyeBallX", "ParamEyeBallY"].some((key) =>
        Number.isFinite(expression.shift?.[key]));
    };
    return owns(activeName) || owns(this.from);
  }
}
