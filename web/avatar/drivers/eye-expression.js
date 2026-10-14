/* 眉眼预览驱动：输出独立绝对目标，眨眼和语音仍由各自驱动合成。
 * 不持有视线参数；害羞/傲娇只给当前关注点一个短暂、可衰减的偏移。
 * 与 Avatar 共用 update(dt)，不建立第二条动画循环。 */

const finiteClamp = (value, min, max, fallback = 0) => {
  const n = Number(value);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fallback;
};
const mix = (a, b, t) => a + (b - a) * t;
const ease = value => {
  const t = finiteClamp(value, 0, 1);
  return t * t * (3 - 2 * t);
};

export const EYE_EXPRESSION_NEUTRAL = Object.freeze({
  ParamEyeLOpen: 1, ParamEyeROpen: 1,
  ParamEyeLSmile: 0, ParamEyeRSmile: 0, ParamEyeBallForm: 0,
  ParamEyeLLower: 0, ParamEyeRLower: 0,
  ParamBrowLY: 0, ParamBrowRY: 0,
  ParamBrowLAngle: 0, ParamBrowRAngle: 0,
  ParamBrowLForm: 0, ParamBrowRForm: 0,
  ParamPupil: 1, ParamEyeHighlight: 1,
  ParamCheek: 0, ParamMouthForm: 0,
});

const OWNED_KEYS = Object.freeze(Object.keys(EYE_EXPRESSION_NEUTRAL));
const OPEN_KEYS = new Set(['ParamEyeLOpen', 'ParamEyeROpen']);
const UNIT_KEYS = new Set(['ParamEyeLSmile', 'ParamEyeRSmile', 'ParamEyeLLower', 'ParamEyeRLower', 'ParamEyeHighlight', 'ParamCheek']);
const bound = (key, value, fallback) => {
  if (OPEN_KEYS.has(key)) return finiteClamp(value, 0, 1.2, fallback);
  if (key === 'ParamPupil') return finiteClamp(value, 0.65, 1.5, fallback);
  if (UNIT_KEYS.has(key)) return finiteClamp(value, 0, 1, fallback);
  return finiteClamp(value, -1, 1, fallback);
};

const DEFINITIONS = {
  daily: { label: '日常', values: {
    ParamEyeLOpen: 0.97, ParamEyeROpen: 0.97,
    ParamEyeLSmile: 0.04, ParamEyeRSmile: 0.04,
    ParamBrowLY: 0.02, ParamBrowRY: 0.02, ParamMouthForm: 0.08,
  } },
  happy: { label: '开心', values: {
    ParamEyeLOpen: 1.03, ParamEyeROpen: 1.03,
    ParamEyeLSmile: 0.28, ParamEyeRSmile: 0.28, ParamEyeBallForm: 0.08,
    ParamBrowLY: 0.13, ParamBrowRY: 0.13,
    ParamBrowLAngle: -0.04, ParamBrowRAngle: 0.04,
    ParamBrowLForm: -0.15, ParamBrowRForm: -0.15,
    ParamPupil: 1.08, ParamCheek: 0.18, ParamMouthForm: 0.42,
  } },
  curious: { label: '疑惑', values: {
    ParamEyeLOpen: 1.05, ParamEyeROpen: 0.86,
    ParamEyeLSmile: 0.02, ParamEyeRSmile: 0.03,
    ParamBrowLY: 0.34, ParamBrowRY: -0.04,
    ParamBrowLAngle: -0.20, ParamBrowRAngle: -0.06,
    ParamBrowLForm: -0.20, ParamBrowRForm: 0.12,
    ParamPupil: 1.04, ParamMouthForm: -0.04,
  } },
  shy: { label: '害羞', values: {
    ParamEyeLOpen: 0.87, ParamEyeROpen: 0.88,
    ParamEyeLSmile: 0.20, ParamEyeRSmile: 0.20,
    ParamBrowLY: 0.10, ParamBrowRY: 0.10,
    ParamBrowLAngle: -0.12, ParamBrowRAngle: 0.12,
    ParamBrowLForm: -0.35, ParamBrowRForm: -0.35,
    ParamPupil: 1.03, ParamEyeHighlight: 0.96,
    ParamCheek: 0.68, ParamMouthForm: 0.16,
  }, glance: { x: -0.26, y: 0.20, duration: 1.2 } },
  tsundere: { label: '傲娇', values: {
    ParamEyeLOpen: 0.76, ParamEyeROpen: 0.83,
    ParamEyeLSmile: 0.01, ParamEyeRSmile: 0.04, ParamEyeBallForm: -0.20,
    ParamBrowLY: -0.12, ParamBrowRY: 0.09,
    ParamBrowLAngle: 0.22, ParamBrowRAngle: -0.14,
    ParamBrowLForm: 0.48, ParamBrowRForm: 0.20,
    ParamPupil: 0.96, ParamCheek: 0.24, ParamMouthForm: -0.09,
  }, glance: { x: 0.25, y: 0.025, duration: 0.85 } },
  sleepy: { label: '困困', values: {
    ParamEyeLOpen: 0.38, ParamEyeROpen: 0.40,
    ParamEyeLSmile: 0.03, ParamEyeRSmile: 0.03, ParamEyeBallForm: -0.24,
    ParamBrowLY: -0.11, ParamBrowRY: -0.11,
    ParamBrowLAngle: -0.03, ParamBrowRAngle: 0.03,
    ParamBrowLForm: -0.10, ParamBrowRForm: -0.10,
    ParamEyeHighlight: 0.64,
  } },
  focused: { label: '认真', values: {
    ParamEyeLOpen: 0.90, ParamEyeROpen: 0.90, ParamEyeBallForm: -0.08,
    ParamBrowLY: -0.15, ParamBrowRY: -0.15,
    ParamBrowLAngle: 0.13, ParamBrowRAngle: -0.13,
    ParamBrowLForm: 0.32, ParamBrowRForm: 0.32,
    ParamPupil: 0.98, ParamMouthForm: -0.08,
  } },
  surprised: { label: '惊讶', values: {
    ParamEyeLOpen: 1.15, ParamEyeROpen: 1.15, ParamEyeBallForm: 0.12,
    ParamBrowLY: 0.42, ParamBrowRY: 0.42,
    ParamBrowLAngle: -0.10, ParamBrowRAngle: 0.10,
    ParamBrowLForm: -0.20, ParamBrowRForm: -0.20,
    ParamPupil: 1.12, ParamMouthForm: -0.02,
  } },
};

export const EYE_EXPRESSION_PRESETS = Object.freeze(
  Object.entries(DEFINITIONS).map(([name, preset]) => Object.freeze({ name, label: preset.label })),
);

// Candidate-only tuning; the original assets retain their preceding presets.
// Lower lid intent is separate from whole-eye smile and retires against blink
// in the native adapter. Sleepiness keeps its persistent upper-eye closure.
const LID_PRESETS = {
  happy: { ParamEyeLOpen: 1.03, ParamEyeROpen: 1.03,
    ParamEyeLSmile: 0.16, ParamEyeRSmile: 0.18, ParamEyeLLower: 0.34, ParamEyeRLower: 0.30 },
  shy: { ParamEyeLOpen: 0.90, ParamEyeROpen: 0.92,
    ParamEyeLSmile: 0.12, ParamEyeRSmile: 0.16, ParamEyeLLower: 0.16, ParamEyeRLower: 0.22 },
};

/* 开度可以略大于 1 表示睁大；完整眨眼总能闭合到 0，随后恢复表情。 */
export function combineEyeOpen(open = 1, blink = 0) {
  return finiteClamp(open, 0, 1.2, 1) * (1 - finiteClamp(blink, 0, 1));
}

export class EyeExpressionDriver {
  constructor({ neutral = {}, transition = 0.28, profile = 'native' } = {}) {
    this.profile = profile === 'seed-lids' ? profile : 'native';
    this.neutral = {};
    for (const key of OWNED_KEYS) {
      this.neutral[key] = bound(key, neutral[key] ?? EYE_EXPRESSION_NEUTRAL[key], EYE_EXPRESSION_NEUTRAL[key]);
    }
    this.transition = finiteClamp(transition, 0.05, 2, 0.28);
    this.name = null;
    this.intensity = 1;
    this.weight = 0;
    this.values = { ...this.neutral };
    this.gazeOffset = { x: 0, y: 0 };
    this._from = { ...this.values };
    this._target = { ...this.values };
    this._weightFrom = 0;
    this._weightTarget = 0;
    this._elapsed = this.transition;
    this._intensityFrom = 1;
    this._intensityCurrent = 1;
    this._glance = null;
    this._glanceAge = 0;
    this._gazeFrom = { x: 0, y: 0 };
    this._gazeFadeAge = this.transition;
  }

  set(name) {
    if (name !== null && (typeof name !== 'string' || !Object.prototype.hasOwnProperty.call(DEFINITIONS, name))) {
      throw new RangeError(`未知眉眼表情: ${String(name)}`);
    }
    if (name === this.name) return false;
    this.name = name;
    this._gazeFrom = { ...this.gazeOffset };
    this._gazeFadeAge = 0;
    this._glance = name ? DEFINITIONS[name].glance || null : null;
    this._glanceAge = 0;
    this._retarget();
    return true;
  }

  setIntensity(value) {
    const intensity = finiteClamp(value, 0, 1, this.intensity);
    if (intensity === this.intensity) return false;
    this.intensity = intensity;
    this._retarget();
    return true;
  }

  setProfile(profile) {
    const next = profile === 'seed-lids' ? profile : 'native';
    if (next === this.profile) return false;
    this.profile = next;
    this._retarget();
    return true;
  }

  _retarget() {
    this._from = { ...this.values };
    const preset = this.name ? { ...DEFINITIONS[this.name].values,
      ...(this.profile === 'seed-lids' ? LID_PRESETS[this.name] : {}) } : this.neutral;
    for (const key of OWNED_KEYS) {
      // 缺省用该通道的中位；瞳孔/睁眼/高光的中位是 1，不能默认补 0。
      const target = preset[key] ?? this.neutral[key];
      this._target[key] = bound(key, mix(this.neutral[key], target, this.intensity), this.neutral[key]);
    }
    this._weightFrom = this.weight;
    this._weightTarget = this.name ? 1 : 0;
    this._intensityFrom = this._intensityCurrent;
    this._elapsed = 0;
  }

  update(dt) {
    const seconds = finiteClamp(dt, 0, 60);
    this._elapsed = Math.min(this.transition, this._elapsed + seconds);
    const t = ease(this._elapsed / this.transition);
    for (const key of OWNED_KEYS) this.values[key] = mix(this._from[key], this._target[key], t);
    this.weight = mix(this._weightFrom, this._weightTarget, t);
    this._intensityCurrent = mix(this._intensityFrom, this.intensity, t);
    this._glanceAge += seconds;
    this._gazeFadeAge += seconds;
    const glance = this._glance;
    let envelope = 0;
    if (glance && this._glanceAge < glance.duration) {
      const age = this._glanceAge;
      // 轻移开、短暂停顿、回看；不会在维持预设时重复播放。
      envelope = age < 0.18 ? ease(age / 0.18)
        : age < 0.30 ? 1 : 1 - ease((age - 0.30) / (glance.duration - 0.30));
    }
    const fade = ease(this._gazeFadeAge / this.transition);
    this.gazeOffset.x = mix(this._gazeFrom.x, (glance?.x || 0) * envelope * this._intensityCurrent, fade);
    this.gazeOffset.y = mix(this._gazeFrom.y, (glance?.y || 0) * envelope * this._intensityCurrent, fade);
    return this.sample();
  }

  /* weight 是预览的控制权，供调用方将原表情渐变到 values。
   * ownedKeys 不包含 gaze、语音张口或圆唇；最后再合成眨眼。 */
  sample() {
    return {
      name: this.name,
      active: this.name !== null || this.weight > 0,
      weight: this.weight,
      values: { ...this.values },
      ownedKeys: [...OWNED_KEYS],
      gazeOffset: { ...this.gazeOffset },
    };
  }
}
