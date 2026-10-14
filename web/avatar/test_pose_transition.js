import assert from 'node:assert/strict';
import { Euler, Quaternion } from 'three';
import { PoseTransition } from './drivers/pose-transition.js';
import { PerformanceDirector } from './drivers/performance.js';
import { VrmRig } from './render/vrm.js';
import { Avatar } from './index.js';

let count = 0;
function test(name, fn) { fn(); count++; console.log(`ok ${count} - ${name}`); }
const rotation = (x, y = 0, z = 0) => new Quaternion().setFromEuler(new Euler(x, y, z));
const signal = (id, from = 'wave', to = 'rest', reason = 'interrupted') => ({ id, from, to, reason });
const close = (a, b) => assert.ok(a.angleTo(b) < 1e-7, `rotation gap ${a.angleTo(b)}`);
const smooth = value => value * value * (3 - 2 * value);

test('a replacement starts from actual rotations, not the previous target', () => {
  const controller = new PoseTransition();
  controller.update(signal(1, 'rest', 'self', 'start'), { arm: rotation(0) }, { arm: rotation(1.2) }, .02);
  const actual = rotation(.23, -.12), target = rotation(-.6, .3);
  const result = controller.update(signal(2, 'self', 'palm', 'replace'), { arm: actual }, { arm: target }, 0);
  close(result.arm, actual);
  assert.ok(Math.abs(controller.getDiagnostic().distance - actual.angleTo(target)) < 1e-12);
  assert.equal(controller.getDiagnostic().reason, 'replace');
  close(actual, rotation(.23, -.12));
  close(target, rotation(-.6, .3));
});

test('angular distance determines a finite duration bounded to 0.35 seconds', () => {
  const short = new PoseTransition(), long = new PoseTransition(), huge = new PoseTransition();
  for (const [controller, distance] of [[short, .05], [long, .9], [huge, Math.PI]]) {
    controller.update(signal(1), { arm: rotation(distance) }, { arm: rotation(0) }, 0);
  }
  assert.ok(short.getDiagnostic().duration < long.getDiagnostic().duration);
  for (const controller of [short, long, huge]) {
    assert.ok(controller.getDiagnostic().duration >= .08 && controller.getDiagnostic().duration <= .35);
  }
});

test('zero-weight incoming envelopes still compare against the approved destination pose', () => {
  const controller = new PoseTransition(), actual = rotation(.7), liveTarget = rotation(0), approved = rotation(.73);
  const result = controller.update(signal(1, 'self', 'palm', 'replace'),
    { arm: actual }, { arm: liveTarget }, 0, { arm: approved });
  close(result.arm, actual);
  assert.ok(controller.getDiagnostic().distance < .031, 'duration measures actual-to-approved pose, not actual-to-rest');
  assert.ok(controller.getDiagnostic().duration < .09);
});

test('stable identity captures actual rotations only once', () => {
  const controller = new PoseTransition(), origin = rotation(.9), target = rotation(0);
  controller.update(signal(1), { arm: origin }, { arm: target }, .05);
  const result = controller.update(signal(1), { arm: target }, { arm: target }, .05);
  const progress = controller.getDiagnostic().progress;
  close(result.arm, origin.clone().slerp(target, smooth(progress)));
  assert.ok(result.arm.angleTo(target) > .01, 'later current argument does not replace the original snapshot');
});

test('interruption cancels logical speech immediately and recovers with a frozen clock', () => {
  const director = new PerformanceDirector(), clock = { currentTime: 0 }, controller = new PoseTransition();
  director.preview('wave', clock);clock.currentTime = .6;
  const active = director.sample();assert.equal(active.name, 'wave');
  director.onEvent({ type: 'interrupted' });director.stop();
  const cancelled = director.sample();
  assert.equal(cancelled.name, null);assert.equal(cancelled.weight, 0);assert.equal(director.queue.length, 0);
  assert.equal(cancelled.transition.reason, 'interrupted');
  assert.equal(cancelled.transition.from, 'wave');assert.equal(cancelled.transition.to, 'rest');
  let actual = rotation(-.35, 0, -.6), elapsed = 0;
  const target = rotation(0);
  controller.update(cancelled.transition, { arm: actual }, { arm: target }, 0);
  let previousDistance = actual.angleTo(target);
  while (controller.getDiagnostic().active) {
    const result = controller.update(director.sample().transition, { arm: actual }, { arm: target }, .016);
    actual = result.arm;elapsed += .016;
    const distance = actual.angleTo(target);
    assert.ok(distance <= previousDistance + 1e-10);
    previousDistance = distance;
  }
  close(actual, target);assert.ok(elapsed <= .366);
  assert.equal(clock.currentTime, .6, 'recovery does not require audio-clock progress');
  assert.equal(controller.getDiagnostic().progress, 1);
});

test('rapid replacements and recovery always snapshot the latest actual pose', () => {
  const controller = new PoseTransition();
  let actual = rotation(.8, .1);
  for (const [id, target] of [[1, rotation(-.6)], [2, rotation(.2, -.5)], [3, rotation(0)]]) {
    const before = actual.clone();
    close(controller.update(signal(id), { arm: actual }, { arm: target }, 0).arm, before);
    actual = controller.update(signal(id), { arm: actual }, { arm: target }, .02).arm;
    assert.ok(actual.angleTo(target) <= before.angleTo(target) + 1e-10);
  }
});

test('render delta is bounded and diagnostics cannot mutate the controller', () => {
  const controller = new PoseTransition();
  for (const dt of [NaN, Infinity, -1]) {
    controller.update(signal(1), { arm: rotation(1) }, { arm: rotation(0) }, dt);
    assert.equal(controller.getDiagnostic().progress, 0);
  }
  controller.update(signal(1), { arm: rotation(1) }, { arm: rotation(0) }, 10);
  const diagnostic = controller.getDiagnostic();
  assert.ok(Math.abs(diagnostic.progress - .05 / diagnostic.duration) < 1e-12);
  diagnostic.progress = 99;assert.ok(controller.getDiagnostic().progress < 1);
  controller.reset();assert.equal(controller.getDiagnostic(), null);
});

test('same-time samples are stable and same-pose restart creates one replacement', () => {
  const director = new PerformanceDirector(), clock = { currentTime: 0 };
  director.preview('self', clock);clock.currentTime = .6;
  const first = director.sample();
  assert.equal(director.sample().transition.id, first.transition.id);
  director.preview('self', clock);
  const replacement = director.sample();
  assert.equal(replacement.transition.from, 'self');assert.equal(replacement.transition.to, 'self');
  assert.equal(replacement.transition.reason, 'replace');
  assert.equal(director.sample().transition.id, replacement.transition.id);
  assert.ok(replacement.transition.id > first.transition.id);
});

test('Avatar interruption preserves the specific reason across repeated stopSpeaking calls', () => {
  const avatar = new Avatar().configure({}), clock = { currentTime: 0 };
  avatar.performance.preview('wave', clock);clock.currentTime = .6;avatar.performance.sample();
  avatar.onMessage({ type: 'interrupted' });
  const cancelled = avatar.performance.sample();
  assert.equal(cancelled.name, null);assert.equal(cancelled.weight, 0);
  assert.equal(cancelled.transition.reason, 'interrupted');
  assert.equal(cancelled.transition.to, 'rest');
});

test('missing descriptors reset snapshots for a fixed calibration pose', () => {
  const controller = new PoseTransition();
  controller.update(signal(1), { arm: rotation(.8) }, { arm: rotation(0) }, .02);
  assert.equal(controller.update(null, { arm: rotation(0) }, { arm: rotation(0) }, 0), null);
  assert.equal(controller.getDiagnostic(), null);
  const actual = rotation(.1);
  close(controller.update(signal(1), { arm: actual }, { arm: rotation(0) }, 0).arm, actual);
});

test('unstarted cues emit no transition and deliberate rest gaps stay rest', () => {
  const director = new PerformanceDirector(), clock = { currentTime: 0 };
  director.speak('你好', clock, 2, 3);
  assert.equal(director.sample().transition, null);
  clock.currentTime = 2.7;assert.equal(director.sample().transition.to, 'wave');
  clock.currentTime = 6;
  const gap = director.sample();assert.equal(gap.name, null);assert.equal(gap.weight, 0);
  assert.equal(gap.transition.to, 'rest');assert.equal(gap.transition.reason, 'complete');
  director.preview('palm', clock);
  const next = director.sample();assert.equal(next.transition.from, 'rest');assert.equal(next.transition.reason, 'start');
});

test('renderer diagnostics and comparison toggle are safe before mount', () => {
  const rig = new VrmRig();
  assert.equal(rig.getPerformanceTransition(), null);
  assert.doesNotThrow(() => rig.setPerformanceTransitions(false));
  assert.equal(rig.getPerformanceTransition(), null);
  assert.doesNotThrow(() => rig.setPerformanceTransitions(true));
  assert.equal(rig.getPerformanceTransition(), null);
  rig.dispose();
});

console.log(`${count} pose transition tests passed`);
