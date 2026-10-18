/* Pure stage model: camera blend, framing interpolation, morph filtering.
 * Run: node --experimental-default-type=module --test web/avatar/test_stage_camera.js */
import assert from 'node:assert/strict';
import test from 'node:test';
import { StageCamera, resolveMorphWeights, resolveStageConfig, STAGE_DEFAULTS } from './render/stage.js';

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

test('idle camera keeps the base framing and a slight 3/4 yaw', () => {
  const camera = new StageCamera();
  const base = { width: 0.68, height: 0.864, centerY: 1.15, headY: 1.37 };
  const sample = camera.sample(1.7, base);
  close(sample.width, base.width);
  close(sample.height, base.height);
  close(sample.centerY, base.centerY);
  close(sample.yaw, STAGE_DEFAULTS.camera.idle.yaw * Math.PI / 180);
  assert.equal(sample.mode, 'idle');
});

test('speaking pushes in and up, then releases without overshoot', () => {
  const camera = new StageCamera();
  const base = { width: 0.68, height: 0.864, centerY: 1.15, headY: 1.37 };
  camera.setSpeaking(true);
  let previous = camera.blend;
  for (let i = 0; i < 180; i++) {
    camera.update(1 / 60);
    assert.ok(camera.blend >= previous, 'blend is monotonic while entering');
    assert.ok(camera.blend <= 1, 'blend never overshoots');
    previous = camera.blend;
  }
  close(camera.blend, 1);
  const speaking = camera.sample(1.7, base);
  close(speaking.width, STAGE_DEFAULTS.camera.speaking.width * 1.7);
  close(speaking.height, STAGE_DEFAULTS.camera.speaking.height * 1.7);
  close(speaking.centerY, base.headY + STAGE_DEFAULTS.camera.speaking.centerY * 1.7);
  close(speaking.yaw, STAGE_DEFAULTS.camera.speaking.yaw * Math.PI / 180);
  assert.equal(speaking.mode, 'speaking');

  camera.setSpeaking(false);
  previous = camera.blend;
  for (let i = 0; i < 600; i++) {
    camera.update(1 / 60);
    assert.ok(camera.blend <= previous, 'blend is monotonic while exiting');
    assert.ok(camera.blend >= 0, 'blend never undershoots');
    previous = camera.blend;
  }
  assert.equal(camera.blend, 0);
  assert.equal(camera.sample(1.7, base).mode, 'idle');
});

test('enter is quicker than exit and half-way is the mode switch', () => {
  const enter = new StageCamera(), exit = new StageCamera();
  enter.setSpeaking(true);
  for (let i = 0; i < 60; i++) enter.update(1 / 60);
  const entered = enter.blend;
  exit.blend = 1;
  exit.setSpeaking(false);
  for (let i = 0; i < 60; i++) exit.update(1 / 60);
  assert.ok(entered > 1 - exit.blend, 'one second in is further than one second out');
  assert.equal(enter.mode, 'speaking');
  const halfway = new StageCamera();
  halfway.blend = 0.49;
  halfway.update(0);
  assert.equal(halfway.mode, 'idle');
  halfway.blend = 0.5;
  halfway.update(0);
  assert.equal(halfway.mode, 'speaking');
});

test('suspended or invalid dt changes nothing', () => {
  const camera = new StageCamera();
  camera.setSpeaking(true);
  camera.update(0.1);
  const before = camera.blend;
  for (const dt of [0, -1, NaN, Infinity, undefined]) camera.update(dt);
  close(camera.blend, before);
});

test('config guards fall back instead of producing broken framing', () => {
  const resolved = resolveStageConfig({ camera: { enter: -2, exit: NaN,
    speaking: { width: 0, height: -3, centerY: NaN, yaw: 'wide' } } });
  assert.equal(resolved.camera.enter, STAGE_DEFAULTS.camera.enter);
  assert.equal(resolved.camera.exit, STAGE_DEFAULTS.camera.exit);
  assert.equal(resolved.camera.speaking.width, STAGE_DEFAULTS.camera.speaking.width);
  assert.equal(resolved.camera.speaking.height, STAGE_DEFAULTS.camera.speaking.height);
  assert.equal(resolved.camera.speaking.centerY, STAGE_DEFAULTS.camera.speaking.centerY);
  assert.equal(resolved.camera.speaking.yaw, STAGE_DEFAULTS.camera.speaking.yaw);
  const sample = new StageCamera().sample(1.7, { width: 0.68, height: 0.86, centerY: 1.1, headY: 1.3 });
  for (const value of [sample.width, sample.height, sample.centerY, sample.yaw]) assert.ok(Number.isFinite(value));
});

test('morph weights only keep asset-present names and clamp to 0..1', () => {
  const targets = [{ influences: [0, 0], index: 0 }];
  const available = new Map([['mouth_wide', targets], ['lip_up', targets]]);
  const resolved = resolveMorphWeights({ mouth_wide: 0.4, lip_up: 3, fake_morph: 0.5,
    zero: 0, bad: NaN }, available);
  assert.deepEqual(resolved.map(item => [item.name, item.weight]), [['mouth_wide', 0.4], ['lip_up', 1]]);
  assert.equal(resolved[0].targets, targets);
  assert.deepEqual(resolveMorphWeights({ mouth_wide: 0.4 }, new Map()), []);
});
