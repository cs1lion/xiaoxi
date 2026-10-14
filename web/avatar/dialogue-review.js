/* Opt-in, in-memory dialogue evidence. The caller owns turn IDs, playback
 * boundaries and the render loop; this recorder neither infers speech from
 * generated text nor writes to the avatar, browser storage or the server. */
import { PARAM_GROUPS } from './params.js';

const PARAM_KEYS = [...PARAM_GROUPS.head, ...PARAM_GROUPS.eye, ...PARAM_GROUPS.brow, ...PARAM_GROUPS.mouth];
const PARAM_SET = new Set(PARAM_KEYS);
const EVENT_TYPES = new Set(['turn_started', 'input', 'recording', 'asr', 'thinking',
  'emotion', 'text_delta', 'done', 'interrupted', 'playback_stopped', 'error',
  'playback-start', 'playback-end', 'state', 'intent', 'style']);
const META_KEYS = ['state', 'reason', 'source', 'style', 'intent', 'clipId', 'index',
  'duration', 'durationMs', 'startedAt', 'scheduledAt', 'audioTime', 'recoverable',
  'blocked', 'scenario', 'textStatus', 'audioStatus'];
const SESSION_KEYS = ['model', 'style', 'intent', 'source', 'scenario', 'page', 'version'];
const CHANNEL_KEYS = ['smile', 'lowerLid', 'eyeWide', 'eyeForm', 'irisSize', 'pupil',
  'browHeight', 'browAngle', 'browForm', 'highlight', 'cheek', 'mouthForm'];

export const DIALOGUE_REVIEW_TEXT_LIMITS = Object.freeze({ input: 4096, response: 16384,
  eventText: 4096, events: 512 });

const boundedInt = (value, fallback, low, high) => Number.isFinite(value)
  ? Math.max(low, Math.min(high, Math.trunc(value))) : fallback;
const finite = value => Number.isFinite(value) ? value : null;
const label = (value, max = 96) => typeof value === 'string' ? value.slice(0, max) : '';
function text(value, max) {
  if (typeof value !== 'string') return '';
  // Event data/audio is never copied. Also reject an accidentally supplied
  // binary data URL or standalone long base64 payload in a text field.
  if (/^\s*data:[^,]*;base64,/i.test(value) ||
      (value.length >= 256 && /^[A-Za-z0-9+/]+={0,2}$/.test(value))) return '[binary data omitted]';
  return value.slice(0, max);
}
const copy = value => JSON.parse(JSON.stringify(value));
function fields(value, keys) {
  const result = {};
  if (!value || typeof value !== 'object') return result;
  for (const key of keys) {
    const item = value[key];
    if (typeof item === 'string') result[key] = text(item, 96);
    else if (typeof item === 'boolean' || Number.isFinite(item)) result[key] = item;
  }
  return result;
}
function params(value) {
  const result = {};
  if (!value || typeof value !== 'object') return result;
  for (const key of PARAM_KEYS) if (Number.isFinite(value[key])) result[key] = value[key];
  return result;
}
function capabilities(renderer) {
  let source;
  try { source = renderer?.getEyeCapabilities?.(); } catch (_) { /* optional renderer */ }
  const result = { available: source?.available === true, profile: label(source?.profile), channels: {} };
  for (const key of CHANNEL_KEYS) {
    const channel = source?.channels?.[key];
    if (channel && typeof channel === 'object') result.channels[key] = fields(channel,
      ['available', 'mode', 'approximate', 'pureTranslation']);
  }
  return result;
}

export class DialogueReviewRecorder {
  constructor({ now = () => performance.now(), maxTurns = 12, maxDurationMs = 120000,
    sampleIntervalMs = 100, maxSamples = 1200 } = {}) {
    this._now = typeof now === 'function' ? now : () => performance.now();
    this.limits = Object.freeze({ maxTurns: boundedInt(maxTurns, 12, 1, 50),
      maxDurationMs: boundedInt(maxDurationMs, 120000, 1, 600000),
      sampleIntervalMs: boundedInt(sampleIntervalMs, 100, 1, 60000),
      maxSamples: boundedInt(maxSamples, 1200, 1, 6000), ...DIALOGUE_REVIEW_TEXT_LIMITS });
    this.recording = false;
    this.metadata = {};
    this.current = null;
    this.turns = [];
    this._sequence = 0;
    this._lastTime = 0;
    this._lastSampleAt = -Infinity;
  }

  _time(at) {
    let candidate = at;
    if (!Number.isFinite(candidate) || candidate < 0) {
      try { candidate = this._now(); } catch (_) { candidate = this._lastTime; }
    }
    // A bad clock or a backwards timestamp must not produce NaN/negative ages.
    this._lastTime = Math.max(this._lastTime, Number.isFinite(candidate) ? candidate : 0);
    return this._lastTime;
  }

  start(metadata = {}) {
    const at = this._time();
    if (this.current) this._finish('restarted', at);
    this.metadata = fields(metadata, SESSION_KEYS);
    this.recording = true;
    return this.snapshot();
  }

  stop(reason = 'stopped') {
    const at = this._time();
    if (this.current) this._finish(reason, at);
    this.recording = false;
    return this.snapshot();
  }

  beginTurn({ id, input = '', source = 'chat', scenario = 'unspecified' } = {}) {
    if (!this.recording) return false;
    if (id != null && typeof id !== 'string' && !Number.isFinite(id)) return false;
    const turnId = id == null ? `review-${++this._sequence}` : id;
    if (typeof turnId === 'string' && (!turnId.length || turnId.length > 128)) return false;
    // Retired IDs cannot be used to resurrect late playback or server events.
    if (this.turns.some(turn => turn.id === turnId)) return false;
    if (this.current?.id === turnId) return true;
    const at = this._time();
    if (this.current) this._finish('superseded', at);
    // The limit includes the current turn as well as completed turns.
    while (this.turns.length >= this.limits.maxTurns) this.turns.shift();
    const savedInput = text(input, this.limits.input);
    this.current = { id: turnId, input: savedInput, source: source === 'lab' ? 'lab' : 'chat',
      scenario: label(scenario) || 'unspecified', startedAt: at, endedAt: null,
      durationMs: 0, reason: null, generatedText: '', spokenText: '', events: [], frames: [],
      truncated: { input: typeof input === 'string' && savedInput.length < input.length,
        generatedText: false, spokenText: false, eventText: false,
        events: false, frames: false, duration: false } };
    this._lastSampleAt = -Infinity;
    return true;
  }

  _alive(at) {
    if (!this.recording || !this.current) return false;
    if (at - this.current.startedAt >= this.limits.maxDurationMs) {
      this.current.truncated.duration = true;
      this._finish('duration-limit', this.current.startedAt + this.limits.maxDurationMs);
      return false;
    }
    this.current.durationMs = Math.max(0, at - this.current.startedAt);
    return true;
  }

  _finish(reason, at) {
    const turn = this.current;
    if (!turn) return false;
    const duration = Math.max(0, at - turn.startedAt);
    turn.truncated.duration ||= duration > this.limits.maxDurationMs;
    turn.durationMs = Math.min(this.limits.maxDurationMs, duration);
    turn.endedAt = turn.startedAt + turn.durationMs;
    turn.reason = label(reason) || 'complete';
    this.turns.push(turn);
    while (this.turns.length > this.limits.maxTurns) this.turns.shift();
    this.current = null;
    this._lastSampleAt = -Infinity;
    return true;
  }

  _append(key, value, separator = '') {
    if (!value) return;
    const turn = this.current;
    const joined = turn[key] + (turn[key] ? separator : '') + value;
    turn[key] = joined.slice(0, this.limits.response);
    if (joined.length > this.limits.response) turn.truncated[key] = true;
  }

  recordEvent(event, at) {
    if (!this.recording || !this.current || !event || typeof event !== 'object') return false;
    if (!EVENT_TYPES.has(event.type)) return false;
    // An absent ID means the caller explicitly routed the event to current.
    // A different supplied ID is always rejected before it touches the clock.
    if (event.turn_id != null && event.turn_id !== this.current.id) return false;
    const stamp = this._time(at);
    if (!this._alive(stamp)) return false;
    const turn = this.current;
    const rawText = typeof event.text === 'string' ? event.text : event.type === 'text_delta' ? event.delta : '';
    const savedText = text(rawText, this.limits.eventText);
    const saved = { type: event.type, t: stamp - turn.startedAt };
    if (savedText) saved.text = savedText;
    if (typeof rawText === 'string' && savedText.length < rawText.length) turn.truncated.eventText = true;
    if (typeof event.emotion === 'string') saved.emotion = label(event.emotion);
    const meta = fields(event.meta, META_KEYS);
    if (typeof event.state === 'boolean' || typeof event.state === 'string') meta.state =
      typeof event.state === 'string' ? label(event.state) : event.state;
    if (Object.keys(meta).length) saved.meta = meta;
    if (event.type === 'asr' || event.type === 'input') {
      turn.input = text(rawText, this.limits.input);
      turn.truncated.input ||= typeof rawText === 'string' && turn.input.length < rawText.length;
    } else if (event.type === 'text_delta') {
      const delta = text(rawText, this.limits.response);
      this._append('generatedText', delta);
      turn.truncated.generatedText ||= typeof rawText === 'string' && delta.length < rawText.length;
    }
    else if (event.type === 'done' && typeof event.text === 'string') {
      turn.generatedText = text(event.text, this.limits.response);
      turn.truncated.generatedText ||= turn.generatedText.length < event.text.length;
    } else if (event.type === 'playback-start') {
      const spoken = text(rawText, this.limits.response);
      this._append('spokenText', spoken, '\n');
      turn.truncated.spokenText ||= typeof rawText === 'string' && spoken.length < rawText.length;
    }
    turn.events.push(saved);
    if (turn.events.length > this.limits.events) {
      turn.events.shift(); turn.truncated.events = true;
    }
    // A server `done` ends generation, not queued audible playback. Explicit
    // cancellation is recorded here; the caller ends the turn after cleanup.
    return true;
  }

  recordFrame(avatar, at) {
    if (!this.recording || !this.current || !avatar || typeof avatar !== 'object') return false;
    const stamp = this._time(at);
    if (!this._alive(stamp)) return false;
    if (stamp - this._lastSampleAt < this.limits.sampleIntervalMs) return false;
    if (this.current.frames.length >= this.limits.maxSamples) {
      this.current.truncated.frames = true;
      return false;
    }
    this._lastSampleAt = stamp;
    const dialogue = avatar.dialogueFrame || {}, expression = avatar.dialogueExpressionFrame || {};
    const eye = avatar.eyeFrame || {}, renderer = avatar.svg;
    const emotion = avatar.emotion?.matchingMode ? avatar.emotion.matchingTarget : avatar.emotion?.current;
    const frame = { t: stamp - this.current.startedAt,
      state: label(dialogue.state), phase: label(dialogue.phase),
      age: finite(dialogue.age), phaseAge: finite(dialogue.phaseAge),
      style: label(dialogue.style), intent: label(dialogue.intent ?? expression.intent),
      active: dialogue.active === true, owner: label(dialogue.owner), suspended: dialogue.suspended === true,
      cue: label(expression.cue), appliedValues: params(expression.appliedValues),
      protectedKeys: Array.isArray(expression.protectedKeys) ? expression.protectedKeys
        .filter(key => PARAM_SET.has(key)).slice(0, PARAM_KEYS.length) : [],
      params: params(eye.params), blink: finite(eye.blink), gazeMode: label(avatar.gazeMode),
      emotion: label(emotion), preview: label(eye.preview), reducedMotion: avatar.reducedMotion === true,
      renderer: label(avatar.rendererKind), eyeLidMode: label(renderer?.eyeLidMode),
      capabilities: capabilities(renderer),
      audioTime: finite(avatar.audioCtx?.currentTime), audioState: label(avatar.audioCtx?.state) };
    this.current.frames.push(frame);
    return true;
  }

  endTurn(id, reason = 'complete') {
    if (!this.recording || !this.current || id !== this.current.id) return false;
    return this._finish(reason, this._time());
  }

  snapshot() {
    if (this.recording && this.current) this._alive(this._time());
    return copy({ recording: this.recording, metadata: this.metadata,
      current: this.current, turns: this.turns, limits: this.limits });
  }

  listTurns() {
    if (this.recording && this.current) this._alive(this._time());
    return [...this.turns, ...(this.current ? [this.current] : [])].map(turn => ({
      id: turn.id, input: turn.input, scenario: turn.scenario, source: turn.source,
      durationMs: turn.durationMs, reason: turn.reason, frameCount: turn.frames.length,
      current: turn === this.current,
    }));
  }

  getTurn(id) {
    if (this.recording && this.current) this._alive(this._time());
    const turn = this.current?.id === id ? this.current : this.turns.find(item => item.id === id);
    return turn ? copy(turn) : null;
  }

  clear() {
    this.current = null;
    this.turns = [];
    this.metadata = {};
    this._lastSampleAt = -Infinity;
    return this.snapshot();
  }
}
