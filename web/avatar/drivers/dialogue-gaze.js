/* Dialogue attention is a target schedule, not another eye/face writer.
 * IdleDriver still owns eye motion, micro-saccades, blinks and head coordination.
 * Waiting uses the frame clock; speech begins only on the supplied audio clock.
 */
import { DIALOGUE_INTENT_PROFILES } from './dialogue-intent.js';
const STATES = new Set(['idle', 'monitoring', 'listening', 'thinking', 'preparing', 'speaking', 'yielding']);
const WAITING = new Set(['thinking', 'preparing']);

export const DIALOGUE_GAZE_STYLES = Object.freeze([
  Object.freeze({ id: 'restrained', label: '克制', faceScale: .55, browScale: .55, lidScale: .65,
    gazeScale: .72, headFollowScale: .4, headDelayScale: 1.2, headRecruitScale: 1, maxGlances: 2 }),
  Object.freeze({ id: 'natural', label: '自然', faceScale: 1, browScale: 1, lidScale: 1,
    gazeScale: 1, headFollowScale: .65, headDelayScale: 1, headRecruitScale: .75, maxGlances: 3 }),
  Object.freeze({ id: 'lively', label: '活泼', faceScale: 1.25, browScale: 1.25, lidScale: 1.15,
    gazeScale: 1.15, headFollowScale: .85, headDelayScale: .9, headRecruitScale: .65, maxGlances: 4 }),
]);
const STYLES = Object.fromEntries(DIALOGUE_GAZE_STYLES.map(style => [style.id, style]));
const limit = (value, low, high) => Math.max(low, Math.min(high, value));
const seedNumber = value => {
  if (Number.isFinite(value)) return Math.trunc(value) >>> 0;
  if (typeof value !== 'string' || !value.length || value.length > 128) return null;
  let seed = 2166136261;
  for (let i = 0; i < value.length; i++) seed = Math.imul(seed ^ value.charCodeAt(i), 16777619);
  return seed >>> 0;
};
// A plan owns a local deterministic generator. Neither sampling nor frame rate
// draws randomness, and exporting plan.seed can reproduce this exact schedule.
function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    let value = state += 0x6D2B79F5;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}

export const DIALOGUE_GAZE_DEFAULTS = Object.freeze({
  thinkingDelay: .35,
  aversionDuration: .8,
  repeatInterval: 8,
  aversionX: .30,
  aversionY: -.16,
  listenDrift: .035,
  waitingDrift: .045,
  speakingDrift: .07,
  listeningIntervalScale: 1.8,
  speakingIntervalScale: 1.15,
  reducedMotionScale: .45,
  seed: 187127,
});

const BOUNDS = Object.freeze({
  thinkingDelay: [.1, 3], aversionDuration: [.25, 1.5], repeatInterval: [2, 60],
  aversionX: [-.5, .5], aversionY: [-.35, .35],
  listenDrift: [0, .2], waitingDrift: [0, .2], speakingDrift: [0, .2],
  listeningIntervalScale: [.5, 4], speakingIntervalScale: [.5, 4],
  reducedMotionScale: [.1, 1],
});
const RELEASE = Object.freeze({ x: 0, y: 0, drift: .10, intervalScale: 1,
  headFollowScale: 1, headDelayScale: 1, headRecruitScale: 1 });
const sameTarget = (a, b) => a && b && Object.keys(RELEASE).every(key => a[key] === b[key]);

export class DialogueGazeDirector {
  constructor(options = {}) {
    this.enabled = true;
    this.cfg = { ...DIALOGUE_GAZE_DEFAULTS };
    this.style = 'natural';
    this.intent = 'neutral';
    this._seedState = this.cfg.seed;
    this._turnSequence = 0;
    this._plan = null;
    this.state = 'idle';
    this.age = 0;
    this._waitAge = 0;
    this._hasSpoken = false;
    this._turnId = null;
    this._clips = [];
    this._audioClock = null;
    this._lastSpeechStart = -Infinity;
    this._hasControl = false;
    this._lastTarget = null;
    this._resumeDirty = false;
    this._suspended = false;
    this._reducedMotion = false;
    this._command = null;
    this.configure(options);
  }

  configure(options = {}) {
    if (!options || typeof options !== 'object') return this;
    for (const [key, [low, high]] of Object.entries(BOUNDS)) {
      if (Number.isFinite(options[key])) this.cfg[key] = Math.max(low, Math.min(high, options[key]));
    }
    if (Object.hasOwn(options, 'seed')) this.resetVariation(options.seed);
    if (typeof options.style === 'string') this.setStyle(options.style);
    if (typeof options.intent === 'string') this.setIntent(options.intent);
    if (typeof options.enabled === 'boolean') this.setEnabled(options.enabled);
    return this;
  }

  setStyle(id) {
    if (typeof id !== 'string' || !Object.hasOwn(STYLES, id)) return false;
    if (this.style !== id) { this.style = id; this._command = null; }
    return true;
  }

  setIntent(id) {
    if (typeof id !== 'string' || !Object.hasOwn(DIALOGUE_INTENT_PROFILES, id)) return false;
    if (this.intent !== id) { this.intent = id; this._command = null; }
    return true;
  }

  resetVariation(seed) {
    const value = seedNumber(seed);
    if (value === null) return false;
    this.cfg.seed = this._seedState = value;
    return true;
  }

  setEnabled(enabled) {
    if (typeof enabled !== 'boolean' || enabled === this.enabled) return this;
    this.enabled = enabled;
    this._command = null;
    if (!enabled) this.stop('disabled');
    return this;
  }

  _cancelSpeech() {
    this._clips.length = 0;
    this._audioClock = null;
    this._lastSpeechStart = -Infinity;
  }

  _beginTurn() {
    this._cancelSpeech();
    this._hasSpoken = false;
    this._waitAge = 0;
    this._plan = null;
    this._turnSequence++;
  }

  _ensurePlan() {
    if (this._plan || this._hasSpoken) return;
    const seed = this._seedState >>> 0, rng = seededRandom(seed), cfg = this.cfg;
    this._seedState = (Math.imul(seed ^ 0x9E3779B9, 1664525) + 1013904223) >>> 0;
    const glances = [], initialSide = rng() < .5 ? -1 : 1;
    let start = cfg.thinkingDelay * (.9 + rng() * .2);
    for (let index = 0; index < 4; index++) {
      const duration = limit(cfg.aversionDuration * (.8 + rng() * .35), .2, 1.7);
      const side = index === 0 || rng() < .65 ? initialSide : -initialSide;
      const x = limit(Math.abs(cfg.aversionX) * side * (.82 + rng() * .24), -.5, .5);
      const y = limit(cfg.aversionY * (.85 + rng() * .25), -.35, .35);
      glances.push(Object.freeze({ index, start, end: start + duration, x, y }));
      start += Math.max(duration + 1,
        cfg.repeatInterval * (.72 + rng() * .58));
    }
    this._plan = Object.freeze({ seed, turn: this._turnSequence, styleAtStart: this.style, intentAtStart: this.intent,
      glances: Object.freeze(glances) });
  }

  _planSnapshot() {
    if (!this._plan) return null;
    const budget = Math.min(STYLES[this.style].maxGlances, DIALOGUE_INTENT_PROFILES[this.intent].budgetMax);
    return Object.freeze({ seed: this._plan.seed, turn: this._plan.turn,
      styleAtStart: this._plan.styleAtStart, intentAtStart: this._plan.intentAtStart, budget,
      glances: Object.freeze(this._plan.glances
        .map(glance => Object.freeze({ ...glance }))) });
  }

  setState(id, { newTurn = false } = {}) {
    if (!STATES.has(id)) return false;
    if (newTurn) this._beginTurn();
    // A UI label cannot start an audible response. Keep the waiting plan until
    // startSpeech() supplies a live clip whose audio clock reaches its start.
    if (id === 'speaking' && !this._playingClip()) id = 'preparing';
    const previous = this.state;
    if (id === previous && !newTurn) return true;
    // UI yielding means playback is ducked while barge-in is being confirmed;
    // stop() is the separate fact that actually invalidates the audio plan.
    if (id === 'idle' || id === 'listening' || id === 'monitoring' ||
        (id === 'thinking' && !WAITING.has(previous))) this._cancelSpeech();
    if (WAITING.has(id) && !WAITING.has(previous) && !this._hasSpoken) this._waitAge = 0;
    this.state = id;
    this.age = 0;
    this._command = null;
    if (WAITING.has(id)) this._ensurePlan();
    return true;
  }

  onEvent(message) {
    if (!message || typeof message !== 'object') return;
    const id = message.turn_id;
    const differentTurn = id != null && id !== this._turnId;
    if (message.type === 'recording' && message.state) {
      const newTurn = differentTurn || this.state !== 'listening';
      if (id != null) this._turnId = id;
      this.setState('listening', { newTurn });
    } else if (message.type === 'recording' && !message.state) {
      if (this.state === 'listening') this.setState('preparing');
    } else if (message.type === 'thinking') {
      const newTurn = differentTurn || !['listening', 'thinking', 'preparing'].includes(this.state);
      if (id != null) this._turnId = id;
      this.setState('thinking', { newTurn });
    } else if (message.type === 'asr') {
      if (typeof message.text === 'string' && !message.text.trim()) this.stop('empty_asr');
      else if (this.state === 'listening') this.setState('thinking');
    } else if (message.type === 'emotion' || message.type === 'text_delta') {
      if (this.state === 'thinking') this.setState('preparing');
    } else if (message.type === 'interrupted' || message.type === 'playback_stopped') {
      this.stop(message.type);
    } else if (message.type === 'error' && message.recoverable !== true) {
      this.stop('error');
    }
    // done/text arrival says nothing about when queued audio becomes audible.
  }

  startSpeech(clock, startedAt, duration) {
    if (!clock || !Number.isFinite(clock.currentTime) || clock.currentTime < 0 ||
        clock.state === 'closed' || !Number.isFinite(startedAt) || startedAt < 0 ||
        !Number.isFinite(duration) || duration <= 0 || !Number.isFinite(startedAt + duration)) return false;
    if (this._audioClock && (this._audioClock !== clock || startedAt < this._lastSpeechStart - .001))
      this._cancelSpeech();
    this._audioClock = clock;
    this._lastSpeechStart = startedAt;
    // Replace duplicate scheduling notifications, but preserve forward sentence
    // fragments so a queued clip does not end the currently audible one.
    this._clips = this._clips.filter(clip => clip.start !== startedAt && clip.end > clock.currentTime);
    this._clips.push({ start: startedAt, end: startedAt + duration });
    this._clips.sort((a, b) => a.start - b.start);
    if (this._clips.length > 128) this._clips.length = 128;
    if (!WAITING.has(this.state) && this.state !== 'speaking') this.setState('preparing');
    return true;
  }

  endSpeech({ turnPending = false } = {}) {
    this._cancelSpeech();
    this.setState(turnPending ? 'preparing' : 'idle');
    if (!turnPending) this._plan = null;
    this._command = null;
  }

  stop(reason = 'stop') {
    this._cancelSpeech();
    this._hasSpoken = false;
    this._waitAge = 0;
    this._plan = null;
    this.setState(reason === 'interrupted' || reason === 'playback_stopped' ? 'yielding' : 'idle');
    this._command = null;
  }

  _audioTime() {
    const clock = this._audioClock;
    if (!clock || ['suspended', 'interrupted', 'closed'].includes(clock.state) ||
        !Number.isFinite(clock.currentTime) || clock.currentTime < 0) return null;
    return clock.currentTime;
  }

  _playingClip() {
    const now = this._audioTime();
    return now == null ? null : this._clips.find(clip => now >= clip.start && now < clip.end) || null;
  }

  _syncSpeech() {
    const now = this._audioTime();
    if (now == null) return;
    const started = this._clips.some(clip => now >= clip.start);
    const playing = this._clips.some(clip => now >= clip.start && now < clip.end);
    if (started) this._hasSpoken = true;
    this._clips = this._clips.filter(clip => now < clip.end);
    if (playing && this.state !== 'speaking' && this.state !== 'yielding') {
      this.state = 'speaking'; this.age = 0;
    } else if (!playing && this.state === 'speaking') {
      this.state = 'preparing'; this.age = 0;
    }
  }

  _attention() {
    const cfg = this.cfg;
    const style = STYLES[this.style];
    const intent = DIALOGUE_INTENT_PROFILES[this.intent];
    const scale = style.gazeScale * intent.gazeScale * (this._reducedMotion ? cfg.reducedMotionScale : 1);
    const coordination = { headFollowScale: style.headFollowScale, headDelayScale: style.headDelayScale,
      headRecruitScale: style.headRecruitScale };
    const viewer = (phase, drift, intervalScale, phaseAge = this.age) => ({ phase, phaseAge,
      target: { x: 0, y: 0, drift: drift * scale * intent.driftScale, intervalScale, ...coordination } });
    if (this.state === 'idle') return { phase: 'idle', phaseAge: this.age, target: null };
    if (this.state === 'speaking') {
      const now = this._audioClock?.currentTime;
      const clip = Number.isFinite(now) ? this._clips.find(entry => now >= entry.start && now < entry.end) : null;
      return viewer('speaking', cfg.speakingDrift, cfg.speakingIntervalScale, clip ? now - clip.start : 0);
    }
    if (this.state === 'yielding') return viewer('yielding', cfg.listenDrift, cfg.listeningIntervalScale);
    if (WAITING.has(this.state)) {
      if (this._hasSpoken) return viewer('attending', cfg.waitingDrift, cfg.listeningIntervalScale);
      // Timing is fixed when waiting begins. Live emotion/style changes can
      // scale the current output, but cannot move an elapsed glance into the future.
      const timing = DIALOGUE_INTENT_PROFILES[this._plan?.intentAtStart || this.intent];
      const delay = (this._plan?.glances[0]?.start || 0) * (timing.delayScale - 1);
      const glances = (this._plan?.glances || [])
        .slice(0, Math.min(style.maxGlances, intent.budgetMax)).map(entry => ({ ...entry,
          start: entry.start + delay, end: entry.start + delay + (entry.end - entry.start) * timing.durationScale }));
      if (!glances.length || this._waitAge + 1e-9 < glances[0].start)
        return viewer('waiting', cfg.waitingDrift, cfg.listeningIntervalScale, this._waitAge);
      // Evaluate the current absolute phase rather than replaying transitions.
      // A background tab or a slow frame must never resurrect a missed glance.
      const glance = glances.find(entry => this._waitAge + 1e-9 >= entry.start && this._waitAge + 1e-9 < entry.end);
      if (glance) {
        const x = limit(glance.x * scale, -.6, .6), y = limit(glance.y * scale, -.45, .45);
        return { phase: 'averted', phaseAge: Math.max(0, this._waitAge - glance.start),
          glance: { ...glance, x, y }, target: { x, y,
          drift: cfg.waitingDrift * scale * intent.driftScale, intervalScale: cfg.listeningIntervalScale, ...coordination } };
      }
      if (this._waitAge + 1e-9 >= glances.at(-1).end)
        return viewer('attending', cfg.waitingDrift, cfg.listeningIntervalScale, this._waitAge - glances.at(-1).end);
      const previous = glances.findLast(entry => this._waitAge + 1e-9 >= entry.end);
      return viewer('returned', cfg.waitingDrift, cfg.listeningIntervalScale,
        previous ? Math.max(0, this._waitAge - previous.end) : 0);
    }
    return viewer('attending', cfg.listenDrift, cfg.listeningIntervalScale);
  }

  update(dt, { suspended = false, reducedMotion = false } = {}) {
    const step = Number.isFinite(dt) && dt > 0 ? Math.min(dt, Number.MAX_SAFE_INTEGER) : 0;
    this.age = Math.min(Number.MAX_SAFE_INTEGER, this.age + step);
    if (WAITING.has(this.state) && !this._hasSpoken)
      this._waitAge = Math.min(Number.MAX_SAFE_INTEGER, this._waitAge + step);
    this._syncSpeech();
    this._suspended = Boolean(suspended);
    this._reducedMotion = Boolean(reducedMotion);
    this._command = null;
    const { target } = this._attention();
    if (this._suspended) {
      this._resumeDirty = true;
    } else if (this.enabled && target) {
      if (!this._hasControl || this._resumeDirty || !sameTarget(target, this._lastTarget))
        this._command = { ...target };
      this._hasControl = true;
      this._lastTarget = { ...target };
      this._resumeDirty = false;
    } else {
      if (this._hasControl) this._command = { ...RELEASE };
      this._hasControl = false;
      this._lastTarget = null;
      this._resumeDirty = false;
    }
    return this.sample();
  }

  sample() {
    const { phase, phaseAge, target, glance } = this._attention();
    return { active: Boolean(this.enabled && !this._suspended && target), state: this.state,
      phase, phaseAge, age: this.age, style: this.style, intent: this.intent, plan: this._planSnapshot(),
      glance: glance ? Object.freeze({ ...glance }) : null,
      command: this._command ? { ...this._command } : null };
  }
}
