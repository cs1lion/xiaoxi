/* Semantic cues use Unicode codepoint offsets, not JavaScript UTF-16 indices.
 * Providers may supply complete word timings; absent or partial timings are
 * explicitly marked estimated and spread over the decoded audio duration. */
export const INTENT_GESTURES = Object.freeze({
  greeting: 'wave', farewell: 'wave', explanation: 'palm', question: 'palm',
  reassurance: 'palm', disagreement: 'deny', self_reference: 'self', neutral: null,
});

const spoken = char => /[\p{L}\p{N}\p{M}]/u.test(char);
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

// A valid LLM envelope with no usable cues still means intentional stillness.
// A missing/unsupported envelope returns null so the caller can use legacy rules.
export function validatePerformancePlan(text, performance) {
  if (typeof text !== 'string' || !performance || performance.version !== 1 ||
      performance.source !== 'llm' || !Array.isArray(performance.cues)) return null;
  const chars = Array.from(text), cues = [];
  for (const cue of performance.cues.slice(0, 32)) {
    if (!cue || !Object.hasOwn(INTENT_GESTURES, cue.intent)) continue;
    const expected = INTENT_GESTURES[cue.intent];
    if (cue.gesture !== expected || expected === null) continue;
    const { start_char: start, end_char: end, anchor, strength } = cue;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 ||
        end <= start || end > chars.length || typeof anchor !== 'string' ||
        chars.slice(start, end).join('') !== anchor || !Array.from(anchor).some(spoken) ||
        (strength !== undefined && (typeof strength !== 'number' || !Number.isFinite(strength)))) continue;
    cues.push({ intent: cue.intent, gesture: expected, anchor, start_char: start, end_char: end,
      strength: clamp(strength ?? .4, .15, .75) });
  }
  cues.sort((a, b) => a.start_char - b.start_char || a.end_char - b.end_char);
  const selected = [];
  for (const cue of cues) {
    if (selected.length && cue.start_char < selected.at(-1).end_char) continue;
    selected.push(cue);
    if (selected.length === 2) break;
  }
  return { version: 1, source: 'llm', cues: selected };
}

// Match all spoken characters sequentially. This disambiguates repeated phrases
// and prevents incomplete provider data from claiming provider alignment.
// Provider boundaries anchor whole words accurately; a cue covering only part
// of a word uses approximate uniform interpolation inside that word's bounds.
function providerMap(chars, duration, wordTimings) {
  if (!Array.isArray(wordTimings) || !wordTimings.length || wordTimings.length > 4096) return null;
  const units = chars.map((char, index) => ({ char, index })).filter(unit => spoken(unit.char));
  if (!units.length) return null;
  const map = new Map();
  let cursor = 0, lastEnd = 0;
  for (const word of wordTimings) {
    if (!word || typeof word.text !== 'string' || !word.text.trim() ||
        !Number.isFinite(word.start) || !Number.isFinite(word.end) ||
        word.start < 0 || word.end <= word.start || word.start < lastEnd - .001 ||
        word.end > duration + .04) return null;
    const letters = Array.from(word.text).filter(spoken);
    if (!letters.length) { lastEnd = word.end; continue; }
    for (let i = 0; i < letters.length; i++) {
      const unit = units[cursor++];
      if (!unit || unit.char !== letters[i]) return null;
      map.set(unit.index, {
        start: clamp(word.start + (word.end - word.start) * i / letters.length, 0, duration),
        end: clamp(word.start + (word.end - word.start) * (i + 1) / letters.length, 0, duration),
      });
    }
    lastEnd = word.end;
  }
  return cursor === units.length ? map : null;
}

function estimatedMap(chars, duration) {
  const weights = chars.map(char => {
    if (/[。！？!?…]/u.test(char)) return 1.5;
    if (/[，、；：,;:]/u.test(char)) return .8;
    if (/\s/u.test(char)) return .12;
    if (/[a-zA-Z]/u.test(char)) return .38;
    if (/\p{N}/u.test(char)) return .65;
    return spoken(char) ? 1 : .3;
  });
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  const head = Math.min(.12, duration * .06), tail = Math.min(.1, duration * .05);
  const unit = (duration - head - tail) / Math.max(total, 1);
  const map = new Map();
  let time = head;
  chars.forEach((char, index) => {
    map.set(index, { start: time, end: time + weights[index] * unit });
    time += weights[index] * unit;
  });
  return map;
}

export function buildSpeechPlan(text, duration, options = {}) {
  const plan = validatePerformancePlan(text, options?.performance);
  if (!plan || !Number.isFinite(duration) || duration <= 0) return null;
  const chars = Array.from(text);
  const provided = providerMap(chars, duration, options?.wordTimings);
  const map = provided || estimatedMap(chars, duration);
  const alignment = provided ? 'provider' : 'estimated';
  return { ...plan, alignment, cues: plan.cues.map(cue => {
    const covered = chars.slice(cue.start_char, cue.end_char)
      .map((_, offset) => map.get(cue.start_char + offset)).filter(Boolean);
    return { ...cue, anchorStart: covered[0].start, anchorEnd: covered.at(-1).end, alignment };
  }) };
}
