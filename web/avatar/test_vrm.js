/* Run: node --experimental-default-type=module web/avatar/test_vrm.js
 * Pure mapping tests: no npm install, browser, WebGL, or model required. */
import assert from 'node:assert/strict';
import { mapParams, selectExpressions, safeDelta, finiteClamp, VOWELS, EXPRESSIONS } from './render/vrm-mapping.js';
let count = 0;
function test(name, fn) { fn(); count++; console.log(`ok ${count} - ${name}`); }
const capabilities = Object.fromEntries(EXPRESSIONS.map(name => [name, {
  isBinary: false, overrideBlink: 'none', overrideMouth: 'none', overrideLookAt: 'none',
}]));
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-10, `${a} != ${b}`);

test('neutral is still, open-eyed, and silent', () => {
  const m = mapParams();
  for (const bone of ['head', 'neck', 'spine']) for (const n of m[bone]) close(n, 0);
  for (const n of Object.values(m.expressions)) close(n, 0);
  assert.equal(m.gaze[2], 1);
});
test('dt is finite and bounded after tab suspension', () => {
  for (const dt of [NaN, Infinity, -Infinity, undefined, 'bad', -4]) assert.equal(safeDelta(dt), 0);
  assert.equal(safeDelta(200), 0.05);
  assert.equal(safeDelta(0.016), 0.016);
  assert.equal(finiteClamp(5), 1);
});
test('mapping does not mutate input, uses limited rotations and +Z gaze', () => {
  const p = Object.freeze({ ParamAngleX: 20, ParamAngleY: -20, ParamAngleZ: 20,
    ParamEyeBallX: 1, ParamEyeBallY: 1 });
  const m = mapParams(p);
  close(m.head[1] + m.neck[1], 0.42);
  close(m.head[0] + m.neck[0], -0.30);
  close(m.head[2] + m.neck[2], -0.25);
  assert.ok(m.gaze[0] > 0 && m.gaze[1] < 0 && m.gaze[2] > 0);
});
test('bad inputs never emit non-finite numbers', () => {
  const names = ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamBodyAngleX',
    'ParamBodyAngleZ', 'ParamBodyLeanX', 'ParamEyeBallX', 'ParamEyeBallY',
    'ParamEyeLOpen', 'ParamEyeROpen', 'ParamMouthOpenY', 'ParamMouthForm',
    'ParamMouthRound', 'ParamBrowLForm', 'ParamBrowRForm', 'ParamBrowLY', 'ParamBrowRY'];
  for (const value of [NaN, Infinity, -Infinity, 'bad', undefined, null, -100, 100]) {
    const m = mapParams(Object.fromEntries(names.map(name => [name, value])));
    for (const list of [m.head, m.neck, m.spine, m.gaze, Object.values(m.expressions)]) {
      assert.ok(list.every(Number.isFinite));
    }
  }
});
test('vowels partition mouth opening and stay silent when closed', () => {
  for (const open of [0, 0.1, 0.5, 1]) for (const form of [-1, 0, 0.4, 1]) {
    for (const round of [-1, 0, 0.6, 1]) {
      const m = mapParams({ ParamMouthOpenY: open, ParamMouthForm: form, ParamMouthRound: round });
      close(VOWELS.reduce((sum, key) => sum + m.expressions[key], 0), open);
      for (const v of Object.values(m.expressions)) assert.ok(v >= 0 && v <= 1);
    }
  }
});
test('all five vowel shapes can receive positive weight', () => {
  const m = mapParams({ ParamMouthOpenY: 0.5, ParamMouthForm: 0.5, ParamMouthRound: 0.5 });
  assert.ok(VOWELS.every(key => m.expressions[key] > 0));
});
test('unilateral blinks do not stack generic blink', () => {
  const m = mapParams({ ParamEyeLOpen: 0, ParamEyeROpen: 0.7 });
  const e = selectExpressions(m, capabilities);
  assert.equal(e.blink, 0);
  assert.equal(e.blinkLeft, 1);
  close(e.blinkRight, 0.3);
  close(selectExpressions(m, { blink: {} }).blink, 0.65);
});
test('capability filtering only writes present shapes and falls back for vowels', () => {
  const m = mapParams({ ParamMouthOpenY: 0.7, ParamMouthRound: 1 });
  assert.deepEqual(selectExpressions(m, {}), {});
  close(selectExpressions(m, { aa: {} }).aa, 0.7);
  close(selectExpressions(m, { oh: {} }).oh, 0.7);
  assert.deepEqual(Object.keys(selectExpressions(m, { blinkLeft: {} })), ['blinkLeft']);
});
test('restrained emotion uses mouth and brow cues', () => {
  const m = mapParams({ ParamMouthForm: -1, ParamBrowLForm: -1, ParamBrowRForm: -1 });
  assert.equal(m.expressions.sad, 0.20);
  assert.equal(m.expressions.angry, 0);
  const angry = mapParams({ ParamBrowLForm: 1, ParamBrowRForm: 1 });
  assert.equal(angry.expressions.angry, 0.20);
});
test('emotion overrides and binary emotion never suppress lips, gaze, or blink', () => {
  const m = mapParams({ ParamMouthForm: 1, ParamEyeLOpen: 0, ParamEyeROpen: 0 });
  assert.ok(selectExpressions(m, capabilities).happy > 0);
  for (const property of ['overrideMouth', 'overrideBlink', 'overrideLookAt']) {
    for (const mode of ['block', 'blend']) {
      const e = selectExpressions(m, { ...capabilities, happy: { [property]: mode } });
      assert.equal(e.happy, 0);
      assert.equal(e.blinkLeft, 1);
    }
  }
  assert.equal(selectExpressions(m, { happy: { isBinary: true } }).happy, 0);
});
console.log(`${count} VRM mapping tests passed`);
