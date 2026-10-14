import assert from 'node:assert/strict';
import test from 'node:test';
import { DialogueExpressionDriver, DIALOGUE_EXPRESSION_KEYS } from './drivers/dialogue-expression.js';
import { DialogueGazeDirector } from './drivers/dialogue-gaze.js';

const keys = ['ParamBrowLY', 'ParamBrowRY', 'ParamEyeLSmile', 'ParamEyeRSmile', 'ParamEyeLLower', 'ParamEyeRLower'];
const close = (actual, expected, tolerance = 1e-7) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected} (±${tolerance})`);
const frame = (state, age, extra = {}) => ({ active: true, state,
  phase: state === 'speaking' ? 'speaking' : state === 'listening' ? 'attending' : 'averted',
  age, phaseAge: age, style: 'natural', ...extra });
const advance = (driver, state, end, options = {}, fps = 60, start = 0) => {
  if (!start) driver.update(0, { ...options, dialogue: frame(state, 0, options.frame) });
  let t = start;
  while (t < end - 1e-9) {
    const step = Math.min(1 / fps, end - t);
    t += step;
    driver.update(step, { ...options, dialogue: frame(state, t, options.frame) });
  }
  return driver.sample();
};
const allZero = output => {
  assert.equal(output.active, false);
  assert.ok(Object.values(output.values).every(value => value === 0));
};

test('listening waits before a small lift and settles during long attentive listening', () => {
  const driver = new DialogueExpressionDriver();
  allZero(advance(driver, 'listening', .15));
  const rising = advance(driver, 'listening', .35, {}, 60, .15);
  assert.equal(rising.cue, 'listening-brow');
  assert.ok(rising.values.ParamBrowLY > .01 && rising.values.ParamBrowLY < .075);
  const held = advance(driver, 'listening', 1, {}, 60, .35);
  close(held.values.ParamBrowLY, .075, .0001);
  const longListen = advance(driver, 'listening', 5, {}, 60, 1);
  assert.ok(longListen.values.ParamBrowLY < held.values.ParamBrowLY * .6);
  assert.ok(longListen.values.ParamBrowLY > .035);
  assert.equal(longListen.values.ParamEyeLSmile, 0);
});

test('thinking brow follows gaze onset and uses the actual temporary glance side', () => {
  for (const x of [-.3, .3]) {
    const driver = new DialogueExpressionDriver();
    const options = { frame: { glance: { x } } };
    allZero(advance(driver, 'thinking', .15, options));
    const out = advance(driver, 'thinking', .7, options, 60, .15);
    assert.equal(out.cue, 'thinking-brow');
    assert.ok(out.values[x < 0 ? 'ParamBrowRY' : 'ParamBrowLY'] > .05);
    assert.ok(out.values[x < 0 ? 'ParamBrowLY' : 'ParamBrowRY'] < .03);
    assert.equal(out.values.ParamEyeLSmile, 0);
  }
});

test('thinking -> preparing preserves an averted phase and repeated labels do not restart it', () => {
  const driver = new DialogueExpressionDriver();
  advance(driver, 'thinking', .3);
  const before = driver.sample().values.ParamBrowLY;
  const out = driver.update(.1, { dialogue: frame('preparing', .1,
    { phaseAge: .4, phase: 'averted' }) });
  assert.ok(out.values.ParamBrowLY > before);
  assert.equal(out.cue, 'thinking-brow');
  driver.update(0, { dialogue: frame('preparing', .1, { phaseAge: .4, phase: 'averted' }) });
  close(driver.sample().values.ParamBrowLY, out.values.ParamBrowLY);
});

test('quick listening and quickly cancelled thinking never produce a delayed flash', () => {
  const driver = new DialogueExpressionDriver();
  allZero(advance(driver, 'listening', .1));
  allZero(advance(driver, 'thinking', .14));
  for (let i = 1; i <= 60; i++) allZero(driver.update(1 / 60,
    { dialogue: frame('preparing', i / 60, { phase: 'returned', phaseAge: i / 60 }) }));
});

test('actual speech waits for gaze return before adding soft eyes; preparation adds no smile', () => {
  const driver = new DialogueExpressionDriver();
  allZero(advance(driver, 'preparing', 2, { profile: 'seed-lids', frame: { phase: 'returned' } }));
  allZero(advance(driver, 'speaking', .20, { profile: 'seed-lids' }));
  const out = advance(driver, 'speaking', .8, { profile: 'seed-lids' }, 60, .20);
  assert.equal(out.cue, 'speaking-soft-eyes');
  assert.ok(out.values.ParamEyeLSmile > .04 && out.values.ParamEyeLSmile <= .045);
  assert.ok(out.values.ParamEyeLLower > .05 && out.values.ParamEyeLLower <= .055);
  assert.equal(out.values.ParamBrowLY, 0);
});

test('negative, focused and unknown emotion labels never gain an automatic smile', () => {
  for (const emotion of ['认真', '无语', '委屈', '生气', '惊讶', 'missing', '', null, {}]) {
    const driver = new DialogueExpressionDriver();
    allZero(advance(driver, 'speaking', 2, { profile: 'seed-lids', emotion }));
  }
  for (const emotion of ['base', '调侃', '得意']) {
    const driver = new DialogueExpressionDriver();
    assert.ok(advance(driver, 'speaking', 1, { profile: 'seed-lids', emotion }).values.ParamEyeLSmile > .04);
  }
});

test('returning gaze, yielding and idle release existing cues instead of leaving a held brow', () => {
  for (const state of ['preparing', 'yielding', 'idle']) {
    const driver = new DialogueExpressionDriver();
    advance(driver, 'thinking', .8);
    const initial = driver.sample().values.ParamBrowLY;
    const options = { frame: { phase: state === 'preparing' ? 'returned' : state } };
    const first = advance(driver, state, .02, options);
    assert.ok(first.values.ParamBrowLY > 0 && first.values.ParamBrowLY < initial);
    assert.equal(first.cue, 'release');
    const out = advance(driver, state, 2, options, 60, .02);
    allZero(out);
  }
});

test('explicit stop cancels delayed work; a new state or new turn can cue again', () => {
  const driver = new DialogueExpressionDriver();
  advance(driver, 'listening', .1);
  allZero(driver.stop({ immediate: true }));
  allZero(advance(driver, 'listening', 2, {}, 60, .1));
  // Same state with a rewound state clock is a new recording turn.
  const newTurn = advance(driver, 'listening', .8);
  assert.ok(newTurn.values.ParamBrowLY > .07);
  allZero(driver.stop({ immediate: true }));
  assert.ok(advance(driver, 'thinking', .8).values.ParamBrowLY > .05);
});

test('graceful stop decays the current pose and cannot resurrect on repeated old frames', () => {
  const driver = new DialogueExpressionDriver();
  advance(driver, 'speaking', 1, { profile: 'seed-lids' });
  const initial = driver.sample().values.ParamEyeLSmile;
  assert.equal(driver.stop().cue, 'release');
  const first = advance(driver, 'speaking', 1.02, { profile: 'seed-lids' }, 60, 1);
  assert.ok(first.values.ParamEyeLSmile > 0 && first.values.ParamEyeLSmile < initial);
  allZero(advance(driver, 'speaking', 3, { profile: 'seed-lids' }, 60, 1.02));
});

test('suspension observes current timing so an entire missed aversion is not replayed', () => {
  const driver = new DialogueExpressionDriver();
  allZero(advance(driver, 'thinking', .9, { suspended: true }));
  allZero(driver.update(.1, { suspended: true,
    dialogue: frame('thinking', 1, { phase: 'returned', phaseAge: .1 }) }));
  allZero(driver.update(.1, { dialogue: frame('thinking', 1.1, { phase: 'returned', phaseAge: .2 }) }));
  const resumed = advance(driver, 'listening', .7);
  assert.ok(resumed.values.ParamBrowLY > .07);
});

test('disable and an inactive director retire current cues', () => {
  for (const inactive of [{ enabled: false }, { frame: { active: false } }]) {
    const driver = new DialogueExpressionDriver();
    advance(driver, 'listening', 1);
    allZero(advance(driver, 'listening', 3, inactive, 60, 1));
  }
});

test('three styles scale small brows and lid cues without changing timing', () => {
  const brows = {}, smiles = {};
  for (const style of ['restrained', 'natural', 'lively']) {
    brows[style] = advance(new DialogueExpressionDriver(), 'listening', 1,
      { frame: { style } }).values.ParamBrowLY;
    smiles[style] = advance(new DialogueExpressionDriver(), 'speaking', 1,
      { profile: 'seed-lids', frame: { style } }).values.ParamEyeLSmile;
  }
  assert.ok(brows.restrained < brows.natural && brows.natural < brows.lively);
  assert.ok(smiles.restrained < smiles.natural && smiles.natural < smiles.lively);
  close(brows.restrained / brows.natural, .55);
  close(brows.lively / brows.natural, 1.25);
  close(smiles.restrained / smiles.natural, .65);
  close(smiles.lively / smiles.natural, 1.15);
});

test('reduced motion scales both brows and eyelids to 45 percent with matching timing', () => {
  for (const state of ['listening', 'thinking', 'speaking']) {
    const options = { profile: 'seed-lids' };
    const full = advance(new DialogueExpressionDriver(), state, 1, options);
    const reduced = advance(new DialogueExpressionDriver(), state, 1, { ...options, reducedMotion: true });
    for (const key of keys) close(reduced.values[key], full.values[key] * .45);
  }
});

test('only the calibrated lower-lid profile receives lower deltas; old models keep smile separate', () => {
  for (const profile of ['native', 'seed', 'seed-eyes', 'sample', 'unsupported', null, {}]) {
    const driver = new DialogueExpressionDriver();
    const out = advance(driver, 'speaking', 1, { profile });
    assert.ok(out.values.ParamEyeLSmile > .04);
    assert.equal(out.values.ParamEyeLLower, 0);
    assert.equal(out.values.ParamEyeRLower, 0);
  }
  const driver = new DialogueExpressionDriver();
  advance(driver, 'speaking', 1, { profile: 'seed-lids' });
  assert.ok(driver.sample().values.ParamEyeLLower > .05);
  const fallback = driver.update(0, { profile: 'native', dialogue: frame('speaking', 1) });
  assert.equal(fallback.values.ParamEyeLLower, 0);
  assert.ok(fallback.values.ParamEyeLSmile > .04);
});

test('outputs only six additive face keys and samples cannot mutate internal state', () => {
  assert.deepEqual(DIALOGUE_EXPRESSION_KEYS, keys);
  for (const state of ['listening', 'thinking', 'speaking', 'idle', 'yielding']) {
    const driver = new DialogueExpressionDriver();
    const out = advance(driver, state, 1, { profile: 'seed-lids' });
    assert.deepEqual(Object.keys(out.values), keys);
    assert.ok(Object.values(out.values).every(value => Number.isFinite(value) && value >= 0 && value < .1));
    out.values.ParamBrowLY = 42;
    assert.ok(driver.sample().values.ParamBrowLY < .1);
  }
});

test('invalid dt, ages and tags never generate NaN or unknown parameter writes', () => {
  for (const dt of [NaN, Infinity, -1, undefined, null, '0.5', 0, 1e20]) {
    const driver = new DialogueExpressionDriver();
    const out = driver.update(dt, { dialogue: { state: {}, phase: {}, age: NaN, phaseAge: Infinity, style: {} } });
    allZero(out);
    assert.deepEqual(Object.keys(out.values), keys);
  }
  const fallback = advance(new DialogueExpressionDriver(), 'listening', 1, { frame: { style: 'unknown' } });
  const natural = advance(new DialogueExpressionDriver(), 'listening', 1);
  assert.deepEqual(fallback.values, natural.values);
  const driver = new DialogueExpressionDriver();
  advance(driver, 'listening', 1);
  const before = driver.sample();
  driver.update(-1, { dialogue: frame('listening', 1) });
  assert.deepEqual(driver.sample(), before);
});

test('absolute phaseAge can skip stale preparation, while stopped audio phaseAge cannot advance a speech cue', () => {
  const driver = new DialogueExpressionDriver();
  driver.update(0, { dialogue: frame('speaking', 0), profile: 'seed-lids' });
  for (let i = 0; i < 60; i++) allZero(driver.update(1 / 60,
    { dialogue: frame('speaking', i / 60, { phaseAge: .1 }), profile: 'seed-lids' }));
  const out = driver.update(.1, { dialogue: frame('speaking', 1.1,
    { phaseAge: .7 }), profile: 'seed-lids' });
  assert.ok(out.values.ParamEyeLSmile > 0);
});

test('brow rise, speech rise and release stay consistent at 30, 60 and 144 FPS', () => {
  for (const [state, end] of [['listening', .32], ['thinking', .29], ['speaking', .43]]) {
    const outputs = [30, 60, 144].map(fps => advance(new DialogueExpressionDriver(), state, end,
      { profile: 'seed-lids' }, fps));
    for (const key of keys) {
      close(outputs[0].values[key], outputs[1].values[key], .00035);
      close(outputs[0].values[key], outputs[2].values[key], .00035);
    }
  }
  const released = [30, 60, 144].map(fps => {
    const driver = new DialogueExpressionDriver();
    advance(driver, 'speaking', 1, { profile: 'seed-lids' }, fps);
    return advance(driver, 'idle', .36, { profile: 'seed-lids', frame: { phase: 'idle' } }, fps);
  });
  for (const key of keys) close(released[0].values[key], released[2].values[key], .00015);
});

test('real director phase timing drives delayed brows and audio starts independently of generated text', () => {
  const director = new DialogueGazeDirector({ seed: 123 });
  const expression = new DialogueExpressionDriver();
  director.setState('thinking', { newTurn: true });
  const glance = director.sample().plan.glances[0];
  allZero(expression.update(glance.start + .14,
    { dialogue: director.update(glance.start + .14), profile: 'seed-lids' }));
  const brow = expression.update(.08, { dialogue: director.update(.08), profile: 'seed-lids' });
  assert.equal(brow.cue, 'thinking-brow');
  assert.ok(Math.max(brow.values.ParamBrowLY, brow.values.ParamBrowRY) > 0);
  director.onEvent({ type: 'text_delta', text: 'ready' });
  director.onEvent({ type: 'done' });
  assert.notEqual(director.sample().state, 'speaking');
  const clock = { currentTime: 10, state: 'running' };
  director.startSpeech(clock, 10.5, 2);
  expression.update(.1, { dialogue: director.update(.1), profile: 'seed-lids' });
  assert.equal(expression.sample().values.ParamEyeLSmile, 0);
  clock.currentTime = 10.5;
  expression.update(.01, { dialogue: director.update(.01), profile: 'seed-lids' });
  assert.equal(expression.sample().values.ParamEyeLSmile, 0);
  clock.currentTime = 10.65;
  expression.update(.15, { dialogue: director.update(.15), profile: 'seed-lids' });
  assert.equal(expression.sample().values.ParamEyeLSmile, 0);
  clock.currentTime = 11.1;
  const speech = expression.update(.45, { dialogue: director.update(.45), profile: 'seed-lids' });
  assert.equal(speech.cue, 'speaking-soft-eyes');
  assert.ok(speech.values.ParamEyeLSmile > .02);
  director.stop('interrupted');
  expression.stop({ immediate: true });
  allZero(expression.update(1, { dialogue: director.update(1), profile: 'seed-lids' }));
});

test('serious presentation keeps smaller brows and cannot inherit automatic smile from neutral speech', () => {
  for (const state of ['listening', 'thinking']) {
    const neutral = advance(new DialogueExpressionDriver(), state, 1);
    const serious = advance(new DialogueExpressionDriver(), state, 1, { intent: 'serious' });
    assert.equal(serious.intent, 'serious');
    assert.ok(serious.values.ParamBrowLY > 0 && serious.values.ParamBrowLY < neutral.values.ParamBrowLY * .4);
    assert.equal(serious.values.ParamEyeLSmile, 0);
  }
  const driver = new DialogueExpressionDriver();
  advance(driver, 'speaking', 1, { profile: 'seed-lids' });
  assert.ok(driver.sample().values.ParamEyeLSmile > .04);
  const changed = driver.update(0, { intent: 'serious', dialogue: frame('speaking', 1), profile: 'seed-lids' });
  allZero(changed);
  assert.equal(changed.intent, 'serious');
});

test('hesitation explicitly selected leaves a later single-side brow after aversion and no reply smile', () => {
  for (const x of [-.3, .3]) {
    const driver = new DialogueExpressionDriver();
    const options = { intent: 'hesitant', frame: { glance: { x } } };
    allZero(advance(driver, 'thinking', .32, options));
    const out = advance(driver, 'thinking', .8, options, 60, .32);
    assert.equal(out.cue, 'thinking-hesitant-brow');
    assert.ok(out.values[x < 0 ? 'ParamBrowRY' : 'ParamBrowLY'] > .04);
    assert.equal(out.values[x < 0 ? 'ParamBrowLY' : 'ParamBrowRY'], 0);
    const replied = advance(driver, 'speaking', 2, { intent: 'hesitant', profile: 'seed-lids' });
    allZero(replied);
  }
});

test('teasing waits for actual speech and viewer-return phase before brow then asymmetric eyes', () => {
  const driver = new DialogueExpressionDriver();
  const options = { intent: 'teasing', emotion: 'base', profile: 'seed-lids' };
  allZero(advance(driver, 'preparing', 3, { ...options, frame: { phase: 'returned' } }));
  allZero(advance(driver, 'speaking', .28, options));
  const brow = advance(driver, 'speaking', .40, options, 60, .28);
  assert.equal(brow.cue, 'speaking-teasing-brow');
  assert.ok(brow.values.ParamBrowRY > 0);
  assert.equal(brow.values.ParamBrowLY, 0);
  assert.equal(brow.values.ParamEyeLSmile, 0);
  const eyes = advance(driver, 'speaking', 1, options, 60, .40);
  assert.equal(eyes.cue, 'speaking-teasing-eyes');
  assert.ok(eyes.values.ParamEyeRSmile > eyes.values.ParamEyeLSmile);
  assert.ok(eyes.values.ParamEyeRLower > eyes.values.ParamEyeLLower);
  allZero(advance(new DialogueExpressionDriver(), 'speaking', 2,
    { ...options, frame: { phase: 'averted' } }));
});

test('negative and unknown emotions veto explicit teasing speaking cues and retire earlier soft eyes', () => {
  for (const emotion of ['认真', '收敛', '轻微不服', '变小气', 'unknown', '', null, {}]) {
    allZero(advance(new DialogueExpressionDriver(), 'speaking', 2,
      { intent: 'teasing', emotion, profile: 'seed-lids' }));
    for (const dt of [0, .001, 1 / 60, 1]) {
      const driver = new DialogueExpressionDriver();
      advance(driver, 'speaking', 1, { profile: 'seed-lids' });
      allZero(driver.update(dt, { dialogue: frame('speaking', 1 + dt), emotion, profile: 'seed-lids' }));
    }
  }
});

test('intent precedence is explicit and switching presentation cannot resurrect a cancelled old cue', () => {
  const options = { emotion: '调侃', frame: { intent: 'hesitant' } };
  assert.equal(advance(new DialogueExpressionDriver(), 'listening', 1, options).intent, 'hesitant');
  assert.equal(advance(new DialogueExpressionDriver(), 'listening', 1,
    { ...options, intent: 'auto' }).intent, 'teasing');
  const driver = new DialogueExpressionDriver();
  advance(driver, 'speaking', .1, { intent: 'neutral', profile: 'seed-lids' });
  driver.stop({ immediate: true });
  allZero(advance(driver, 'speaking', 2, { intent: 'teasing', profile: 'seed-lids' }, 60, .1));
  assert.ok(advance(driver, 'speaking', 1, { intent: 'teasing', profile: 'seed-lids' }).values.ParamEyeRSmile > .05);
});

test('paused speech timing never advances a delayed teasing cue', () => {
  const driver = new DialogueExpressionDriver();
  for (let i = 0; i < 120; i++) {
    allZero(driver.update(1 / 60, { intent: 'teasing', profile: 'seed-lids',
      dialogue: frame('speaking', i / 60, { phaseAge: .2 }) }));
  }
  const resumed = driver.update(.1, { intent: 'teasing', profile: 'seed-lids',
    dialogue: frame('speaking', 2, { phaseAge: .8 }) });
  assert.ok(resumed.values.ParamEyeRSmile > 0);
});

test('new intent transitions remain stable at 30, 60 and 144 FPS and scale reduced motion once', () => {
  for (const [intent, state, end] of [['serious', 'listening', .33], ['hesitant', 'thinking', .49],
    ['teasing', 'speaking', .58]]) {
    const options = { intent, profile: 'seed-lids' };
    const outputs = [30, 60, 144].map(fps => advance(new DialogueExpressionDriver(), state, end, options, fps));
    for (const key of keys) {
      close(outputs[0].values[key], outputs[1].values[key], .00035);
      close(outputs[0].values[key], outputs[2].values[key], .00035);
    }
    const reduced = advance(new DialogueExpressionDriver(), state, end, { ...options, reducedMotion: true }, 30);
    for (const key of keys) close(reduced.values[key], outputs[0].values[key] * .45);
  }
});
