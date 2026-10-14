/* Small dialogue cues are additive offsets, not expression ownership.
 * Avatar masks every explicitly configured emotion/gesture/preview key before
 * applying this output. Gaze, head, mouth and blinking retain their own writers.
 */
import { DIALOGUE_GAZE_STYLES } from './dialogue-gaze.js';
import { resolveDialogueIntent } from './dialogue-intent.js';

export const DIALOGUE_EXPRESSION_KEYS = Object.freeze([
  'ParamBrowLY', 'ParamBrowRY', 'ParamEyeLSmile', 'ParamEyeRSmile',
  'ParamEyeLLower', 'ParamEyeRLower',
]);

const zero = () => Object.fromEntries(DIALOGUE_EXPRESSION_KEYS.map(key => [key, 0]));
const finiteTime = value => Number.isFinite(value) && value >= 0 ? value : null;
const ease = value => {
  const t = Math.max(0, Math.min(1, value));
  return t * t * (3 - 2 * t);
};
const ramp = (age, delay, duration) => ease((age - delay) / duration);
const SMILE_EMOTIONS = new Set(['base', '调侃', '得意']);
const EPSILON = 1e-6;

// Integrate a first-order response with a linearly varying target over this
// frame. Unlike frame-count lerp, timing and release remain stable across FPS.
const follow = (current, previousTarget, target, dt, tau) => {
  if (dt <= 0) return current;
  const decay = Math.exp(-dt / tau);
  const change = target - previousTarget;
  return current * decay + previousTarget * (1 - decay) +
    change * (1 - tau * (1 - decay) / dt);
};

export class DialogueExpressionDriver {
  constructor() {
    this.values = zero();
    this._target = zero();
    this._context = null;
    this._phaseAge = 0;
    this._age = 0;
    this._turn = null;
    this._blocked = null;
    this.intent = 'neutral';
    this.cue = 'idle';
  }

  stop({ immediate = false } = {}) {
    // Retiring this exact context prevents a cancelled delayed cue from
    // restarting on the next frame. A new context or rewound age is a new turn.
    this._blocked = this._context;
    this._target = zero();
    this.cue = 'release';
    if (immediate) {
      this.values = zero();
      this.cue = 'idle';
    }
    return this.sample();
  }

  update(dt, options = {}) {
    const { dialogue, enabled = true, suspended = false, reducedMotion = false,
      profile = 'native', emotion = 'base' } = options || {};
    const frame = dialogue && typeof dialogue === 'object' ? dialogue : {};
    this.intent = resolveDialogueIntent({ intent: options?.intent ?? frame.intent ?? 'auto', emotion });
    const step = Number.isFinite(dt) && dt > 0 ? Math.min(dt, 3600) : 0;
    const state = typeof frame.state === 'string' ? frame.state : 'idle';
    const phase = typeof frame.phase === 'string' ? frame.phase : 'idle';
    const age = finiteTime(frame.age);
    const turn = frame.plan?.turn ?? null;
    // A thinking -> preparing label does not restart an ongoing aversion.
    const group = state === 'thinking' || state === 'preparing' ? 'waiting' : state;
    const context = `${group}:${phase}`;
    const restarted = context !== this._context || (age != null && age + 1e-8 < this._age) ||
      (turn != null && this._turn != null && turn !== this._turn);
    if (restarted) {
      this._phaseAge = 0;
      this._blocked = null;
    } else {
      this._phaseAge = Math.min(3600, this._phaseAge + step);
    }
    this._context = context;
    this._age = age ?? (restarted ? 0 : this._age + step);
    this._turn = turn;
    const phaseAge = finiteTime(frame.phaseAge);
    if (phaseAge != null) this._phaseAge = phaseAge;
    else if (state === 'listening' || state === 'speaking') this._phaseAge = this._age;

    const style = DIALOGUE_GAZE_STYLES.find(item => item.id === frame.style) ||
      DIALOGUE_GAZE_STYLES.find(item => item.id === 'natural');
    const motionScale = reducedMotion ? .45 : 1;
    const browScale = (style.browScale ?? style.faceScale) * motionScale;
    const lidScale = (style.lidScale ?? style.faceScale) * motionScale;
    const target = zero();
    let cue = 'idle';
    const allowed = enabled && !suspended && frame.active !== false && this._blocked !== context;
    const smileAllowed = SMILE_EMOTIONS.has(emotion) &&
      this.intent !== 'serious' && this.intent !== 'hesitant';
    if (allowed && state === 'listening') {
      // A quick listen/reply never reaches the onset, and a long listen settles
      // from the small greeting lift into a quieter attentive brow.
      const k = ramp(this._phaseAge, .15, .24) *
        (1 - .45 * ramp(this._phaseAge, 2.4, 1.2));
      const intentScale = this.intent === 'serious' ? .35 : this.intent === 'hesitant' ? .55 :
        this.intent === 'teasing' ? .8 : 1;
      target.ParamBrowLY = .075 * k * browScale * intentScale;
      target.ParamBrowRY = .065 * k * browScale * intentScale;
      cue = k > 0 ? 'listening-brow' : 'listening-wait';
    } else if (allowed && ['thinking', 'preparing'].includes(state) && phase === 'averted') {
      const x = Number.isFinite(frame.glance?.x) ? frame.glance.x : .3;
      if (this.intent === 'hesitant') {
        // Aversion already started. A later single-side lift leaves room for
        // the eye motion instead of triggering eye and brow together.
        const k = ramp(this._phaseAge, .32, .26);
        target[x < 0 ? 'ParamBrowRY' : 'ParamBrowLY'] = .047 * k * browScale;
        cue = k > 0 ? 'thinking-hesitant-brow' : 'thinking-wait';
      } else {
        const k = ramp(this._phaseAge, .15, .22);
        const intentScale = this.intent === 'serious' ? .3 : this.intent === 'teasing' ? .75 : 1;
        target.ParamBrowLY = (x < 0 ? .025 : .06) * k * browScale * intentScale;
        target.ParamBrowRY = (x < 0 ? .06 : .025) * k * browScale * intentScale;
        cue = k > 0 ? 'thinking-brow' : 'thinking-wait';
      }
    } else if (allowed && state === 'speaking' && smileAllowed) {
      if (this.intent === 'teasing') {
        // Director starts this phase on the real audio clock and requests
        // viewer focus. An explicitly averted phase cannot start this cue.
        if (phase === 'speaking' || phase === 'returned') {
          const brow = ramp(this._phaseAge, .28, .26);
          const smile = ramp(this._phaseAge, .42, .30);
          target.ParamBrowRY = .038 * brow * browScale;
          target.ParamEyeLSmile = .043 * smile * lidScale;
          target.ParamEyeRSmile = .053 * smile * lidScale;
          if (profile === 'seed-lids') {
            target.ParamEyeLLower = .038 * smile * lidScale;
            target.ParamEyeRLower = .054 * smile * lidScale;
          }
          cue = smile > 0 ? 'speaking-teasing-eyes' : brow > 0 ?
            'speaking-teasing-brow' : 'speaking-wait';
        }
      } else {
        const k = ramp(this._phaseAge, .20, .34);
        target.ParamEyeLSmile = target.ParamEyeRSmile = .045 * k * lidScale;
        if (profile === 'seed-lids')
          target.ParamEyeLLower = target.ParamEyeRLower = .055 * k * lidScale;
        cue = k > 0 ? 'speaking-soft-eyes' : 'speaking-wait';
      }
    }
    for (const key of DIALOGUE_EXPRESSION_KEYS) {
      const releasing = target[key] < this.values[key];
      const next = follow(this.values[key], this._target[key], target[key], step,
        releasing ? .16 : .075);
      this.values[key] = next < EPSILON ? 0 : Math.max(0, next);
    }
    // Do this after integration: a previous positive target must not create a
    // transient smile on the first frame with a serious/negative label.
    if (!smileAllowed) {
      for (const key of ['ParamEyeLSmile', 'ParamEyeRSmile', 'ParamEyeLLower', 'ParamEyeRLower']) {
        this.values[key] = target[key] = 0;
      }
    }
    // Missing lower-lid channels are never approximated with another parameter;
    // switching back to an old model also retires an existing offset immediately.
    if (profile !== 'seed-lids') this.values.ParamEyeLLower = this.values.ParamEyeRLower = 0;
    this._target = target;
    const active = DIALOGUE_EXPRESSION_KEYS.some(key => this.values[key] > EPSILON);
    this.cue = cue === 'idle' && active ? 'release' : cue;
    return this.sample();
  }

  sample() {
    return { active: DIALOGUE_EXPRESSION_KEYS.some(key => this.values[key] > EPSILON),
      cue: this.cue, intent: this.intent, values: { ...this.values } };
  }
}
