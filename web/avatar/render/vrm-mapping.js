/* Pure parameter mapping: no DOM, Three.js, model, or package dependencies. */
export function finiteClamp(value, min = -1, max = 1, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fallback;
}
export const safeDelta = dt => finiteClamp(dt, 0, 0.05);
export const VOWELS = Object.freeze(['aa', 'ih', 'ou', 'ee', 'oh']);
export const EMOTIONS = Object.freeze(['happy', 'sad', 'angry']);
export const EXPRESSIONS = Object.freeze(['blink', 'blinkLeft', 'blinkRight', ...VOWELS, ...EMOTIONS]);

// All angles are radians, in the normalized humanoid's local axes.
// Positive screen Y is down, as in the SVG renderer; VRM faces world +Z.
export function mapParams(params = {}) {
  const p = params || {};
  const signed = key => finiteClamp(p[key]);
  const unit = (key, fallback = 0) => finiteClamp(p[key] ?? fallback, 0, 1, fallback);
  const pitch = signed('ParamAngleY') * 0.30;
  const yaw = signed('ParamAngleX') * 0.42;
  const roll = -signed('ParamAngleZ') * 0.25;
  const open = unit('ParamMouthOpenY');
  const form = signed('ParamMouthForm');
  const round = Math.max(0, signed('ParamMouthRound'));
  const wide = Math.max(0, form);
  // Partition the available mouth opening instead of stacking full-strength vowels.
  const weights = {
    aa: (1 - round) * (1 - wide),
    ih: (1 - round) * wide * (1 - open),
    ee: (1 - round) * wide * open,
    ou: round * (1 - open),
    oh: round * open,
  };
  for (const key of VOWELS) weights[key] *= open;
  const browForm = (signed('ParamBrowLForm') + signed('ParamBrowRForm')) / 2;
  const browY = (signed('ParamBrowLY') + signed('ParamBrowRY')) / 2;
  return {
    head: [pitch * 0.72, yaw * 0.72, roll * 0.72],
    neck: [pitch * 0.28, yaw * 0.28, roll * 0.28],
    spine: [0, signed('ParamBodyAngleX') * 0.16,
      -signed('ParamBodyAngleZ') * 0.12 - signed('ParamBodyLeanX') * 0.10],
    gaze: [signed('ParamEyeBallX') * 0.32, -signed('ParamEyeBallY') * 0.22, 1],
    expressions: {
      blinkLeft: 1 - unit('ParamEyeLOpen', 1),
      blinkRight: 1 - unit('ParamEyeROpen', 1),
      ...weights,
      happy: Math.max(0, form) * 0.22 * (1 - open),
      sad: Math.max(0, -form) * Math.max(0, -browForm, -browY * 0.5) * 0.20,
      angry: Math.max(0, browForm) * 0.20,
    },
  };
}

/* capabilities[name] is metadata from an actually present expression. Never guess
 * custom names. Binary emotion expressions and expressions that override blink,
 * mouth, or lookAt are excluded: speech/blinks/gaze have priority. */
export function selectExpressions(mapped, capabilities = {}) {
  const values = {};
  const has = name => Object.prototype.hasOwnProperty.call(capabilities, name);
  for (const name of EXPRESSIONS) if (has(name)) values[name] = 0;
  const e = mapped.expressions;
  if (has('blinkLeft') && has('blinkRight')) {
    values.blinkLeft = e.blinkLeft;
    values.blinkRight = e.blinkRight;
  } else if (has('blink')) {
    values.blink = (e.blinkLeft + e.blinkRight) / 2;
  } else {
    for (const name of ['blinkLeft', 'blinkRight']) if (has(name)) values[name] = e[name];
  }
  // Missing vowel shapes fall back to an available vowel, preserving total opening.
  const available = VOWELS.filter(has);
  const fallback = available.includes('aa') ? 'aa' : available[0];
  for (const name of VOWELS) {
    const target = has(name) ? name : fallback;
    if (target) values[target] += e[name];
  }
  for (const name of EMOTIONS) {
    const cap = capabilities[name];
    if (!has(name) || !cap || cap.isBinary) continue;
    const safe = ['overrideBlink', 'overrideMouth', 'overrideLookAt']
      .every(key => cap[key] == null || cap[key] === 'none');
    if (safe) values[name] = e[name];
  }
  return values;
}
