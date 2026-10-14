import assert from 'node:assert/strict';
import test from 'node:test';
import {
  EyeExpressionDriver, EYE_EXPRESSION_PRESETS, EYE_EXPRESSION_NEUTRAL, combineEyeOpen,
} from './drivers/eye-expression.js';

const close = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-10,
  `${message || 'target'}: ${actual} != ${expected}`);
const settle = (driver, seconds = 0.6) => {
  for (let elapsed = 0; elapsed < seconds; elapsed += 1 / 120) driver.update(1 / 120);
  return driver.sample();
};

test('eight stable preview names and labels', () => {
  assert.deepEqual(EYE_EXPRESSION_PRESETS.map(({ name, label }) => [name, label]), [
    ['daily', '日常'], ['happy', '开心'], ['curious', '疑惑'], ['shy', '害羞'],
    ['tsundere', '傲娇'], ['sleepy', '困困'], ['focused', '认真'], ['surprised', '惊讶'],
  ]);
});

test('entry, switching and release start at the current pose and return control', () => {
  const driver = new EyeExpressionDriver();
  assert.deepEqual(driver.sample().values, EYE_EXPRESSION_NEUTRAL);
  assert.equal(driver.sample().active, false);
  driver.set('sleepy');
  close(driver.sample().values.ParamEyeLOpen, 1);
  close(driver.sample().weight, 0);
  driver.update(0.14);
  const halfway = driver.sample();
  assert.ok(halfway.values.ParamEyeLOpen > 0.38 && halfway.values.ParamEyeLOpen < 1);
  assert.ok(halfway.weight > 0 && halfway.weight < 1);
  driver.set('happy');
  assert.deepEqual(driver.sample().values, halfway.values);
  settle(driver);
  const happy = driver.sample();
  driver.set(null);
  assert.deepEqual(driver.sample().values, happy.values);
  driver.update(0.14);
  assert.ok(driver.sample().weight > 0 && driver.sample().weight < 1);
  const released = settle(driver);
  assert.equal(released.name, null);
  assert.equal(released.active, false);
  close(released.weight, 0);
  assert.deepEqual(released.values, EYE_EXPRESSION_NEUTRAL);
});

test('sleepy half-open eyes fully blink and recover both independent openings', () => {
  const driver = new EyeExpressionDriver();
  driver.set('sleepy');
  const { values } = settle(driver);
  for (const key of ['ParamEyeLOpen', 'ParamEyeROpen']) {
    const open = values[key];
    assert.ok(open > 0.35 && open < 0.55);
    const blink = [0, 0.25, 0.8, 1, 0.8, 0.25, 0].map(progress => combineEyeOpen(open, progress));
    close(blink[3], 0, 'full blink');
    close(blink[0], open, 'before blink');
    close(blink[6], open, 'restored expression');
    close(blink[1], open * 0.75, 'multiplicative blink');
  }
});

test('happy smile and opening are preserved around blinking without owning gaze or speech', () => {
  const driver = new EyeExpressionDriver();
  driver.set('happy');
  const before = settle(driver);
  assert.ok(before.values.ParamEyeLSmile > 0.2 && before.values.ParamEyeLSmile < 0.4);
  for (const progress of [0, 0.5, 1, 0.5, 0]) {
    assert.ok(combineEyeOpen(before.values.ParamEyeLOpen, progress) <= before.values.ParamEyeLOpen);
    assert.deepEqual(driver.sample().values, before.values);
  }
  for (const key of ['ParamEyeBallX', 'ParamEyeBallY', 'ParamMouthOpenY', 'ParamMouthRound']) {
    assert.ok(!before.ownedKeys.includes(key), `must not own ${key}`);
    assert.ok(!(key in before.values));
  }
  assert.deepEqual(before.gazeOffset, { x: 0, y: 0 });
});

test('curious and tsundere preserve independent left and right brow and eye targets', () => {
  const driver = new EyeExpressionDriver();
  for (const name of ['curious', 'tsundere']) {
    driver.set(name);
    const { values } = settle(driver);
    assert.ok(Math.abs(values.ParamEyeLOpen - values.ParamEyeROpen) > 0.05);
    assert.ok(Math.abs(values.ParamBrowLY - values.ParamBrowRY) > 0.15);
    assert.notEqual(values.ParamBrowLForm, values.ParamBrowRForm);
  }
});

test('shy and tsundere glance once, then return exactly to the current baseline gaze', () => {
  for (const name of ['shy', 'tsundere']) {
    const driver = new EyeExpressionDriver();
    driver.set(name);
    driver.update(0.3);
    assert.ok(Math.abs(driver.sample().gazeOffset.x) > 0.20);
    if (name === 'shy') assert.ok(driver.sample().gazeOffset.y > 0.15, 'screen Y down');
    assert.equal(driver.set(name), false, 'selecting the same preset does not replay glance');
    driver.setIntensity(0.75);
    settle(driver, 1.5);
    assert.deepEqual(driver.sample().gazeOffset, { x: 0, y: 0 });
    settle(driver, 2);
    assert.deepEqual(driver.sample().gazeOffset, { x: 0, y: 0 });
    assert.equal(driver.sample().name, name, 'expression remains active after gaze releases');
  }
});

test('releasing mid-glance fades its gaze offset without a jump', () => {
  const driver = new EyeExpressionDriver();
  driver.set('shy');
  driver.update(0.3);
  const offset = driver.sample().gazeOffset;
  driver.set(null);
  assert.deepEqual(driver.sample().gazeOffset, offset);
  driver.update(0.14);
  const mid = driver.sample().gazeOffset;
  assert.ok(Math.abs(mid.x) < Math.abs(offset.x) && Math.abs(mid.x) > 0);
  assert.ok(mid.y < offset.y && mid.y > 0);
  driver.update(0.14);
  assert.deepEqual(driver.sample().gazeOffset, { x: 0, y: 0 });
  assert.equal(driver.sample().active, false);
});

test('intensity scales around each channel neutral, including open eyes, pupil and highlight', () => {
  const driver = new EyeExpressionDriver();
  driver.set('happy');
  const full = settle(driver).values;
  driver.setIntensity(0.5);
  const half = settle(driver).values;
  for (const key of Object.keys(EYE_EXPRESSION_NEUTRAL)) {
    close(half[key], (EYE_EXPRESSION_NEUTRAL[key] + full[key]) / 2, key);
  }
  driver.setIntensity(0);
  assert.deepEqual(settle(driver).values, EYE_EXPRESSION_NEUTRAL);
  const custom = new EyeExpressionDriver({ neutral: { ParamEyeLOpen: 0.98, ParamPupil: 1.1 } });
  custom.set('sleepy');
  const sleepy = settle(custom).values;
  close(sleepy.ParamPupil, 1.1, 'missing target uses configured neutral');
  close(sleepy.ParamEyeHighlight, 0.64);
});

test('all transitions stay finite and within declared parameter bounds', () => {
  const driver = new EyeExpressionDriver({ neutral: { ParamEyeLOpen: NaN, ParamPupil: Infinity } });
  for (const { name } of EYE_EXPRESSION_PRESETS) {
    driver.set(name);
    for (let frame = 0; frame < 200; frame++) {
      const result = driver.update([1 / 240, 1 / 30, 0, NaN, -1, Infinity][frame % 6]);
      assert.ok(Number.isFinite(result.weight) && result.weight >= 0 && result.weight <= 1);
      for (const [key, value] of Object.entries(result.values)) {
        assert.ok(Number.isFinite(value), `${name} ${key} finite`);
        const [min, max] = key === 'ParamPupil' ? [0.65, 1.5]
          : /ParamEye[LR]Open/.test(key) ? [0, 1.2]
            : ['ParamEyeLSmile', 'ParamEyeRSmile', 'ParamEyeHighlight', 'ParamCheek'].includes(key)
              ? [0, 1] : [-1, 1];
        assert.ok(value >= min && value <= max, `${name} ${key} bounded: ${value}`);
      }
      for (const value of Object.values(result.gazeOffset)) assert.ok(Number.isFinite(value) && Math.abs(value) <= 1);
    }
  }
  driver.set('surprised');
  assert.ok(settle(driver).values.ParamEyeLOpen > 1, 'surprise survives upper-opening range');
  assert.equal(combineEyeOpen(NaN, 1), 0);
  assert.equal(combineEyeOpen(1.15, Infinity), 1.15);
});

test('equal elapsed time gives the same pose and gaze at different frame rates', () => {
  const sampleAt = frames => {
    const driver = new EyeExpressionDriver();
    driver.set('shy');
    for (let frame = 0; frame < frames; frame++) driver.update(0.24 / frames);
    return driver.sample();
  };
  const one = sampleAt(1), many = sampleAt(24);
  close(one.weight, many.weight);
  for (const key of Object.keys(one.values)) close(one.values[key], many.values[key], key);
  close(one.gazeOffset.x, many.gazeOffset.x);
  close(one.gazeOffset.y, many.gazeOffset.y);
});

test('invalid preset names are rejected and returned samples cannot mutate internal state', () => {
  const driver = new EyeExpressionDriver();
  for (const bad of ['missing', '__proto__', undefined, {}, 1, { toString: () => 'daily' }]) {
    assert.throws(() => driver.set(bad), RangeError);
  }
  driver.set('happy');
  const sample = settle(driver);
  sample.values.ParamEyeLOpen = 0;
  sample.gazeOffset.x = 1;
  sample.ownedKeys.length = 0;
  assert.ok(driver.sample().values.ParamEyeLOpen > 0);
  assert.equal(driver.sample().gazeOffset.x, 0);
  assert.ok(driver.sample().ownedKeys.length > 0);
});
