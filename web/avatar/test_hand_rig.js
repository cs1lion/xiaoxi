import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VrmRig } from './render/vrm.js';
import { DAILY_GESTURE_MODEL_NAME } from './render/gesture-poses.js';
import { HAND_BONE_NAMES, sampleHandPose } from './render/hand-poses.js';

function fixture({ names = HAND_BONE_NAMES, model = DAILY_GESTURE_MODEL_NAME } = {}) {
  const rig = new VrmRig();
  const nodes = Object.fromEntries(names.map(name => {
    const node = new THREE.Object3D();
    node.quaternion.setFromEuler(new THREE.Euler(.12, -.07, .03));
    return [name, node];
  }));
  const state = { vrm: { meta: { name: model }, humanoid: { getNormalizedBoneNode: name => nodes[name] } } };
  rig._state = state; rig._captureFingers(state);
  return { rig, state, nodes };
}
const frame = { name: 'palm', source: 'preview', weight: 1, strength: 1 };
const settle = (rig, state, input = frame) => {
  for (let i = 0; i < 120; ++i) rig._applyFingers(state, input, 1 / 60);
};
const target = (bone, offset = [0, 0, 0]) => bone.rest.clone().multiply(new THREE.Quaternion()
  .setFromEuler(new THREE.Euler(...offset, 'XYZ')));
const rotations = state => Object.fromEntries(Object.entries(state.fingers).map(([name, bone]) => [name, bone.node.quaternion.clone()]));

test('captures all 30 supported joints and keeps cloned bind poses for the thumbs', () => {
  const { rig, state, nodes } = fixture();
  assert.equal(Object.keys(state.fingers).length, 30);
  const rest = rotations(state);
  settle(rig, state, { name: null, weight: 0 });
  const pose = sampleHandPose({}, { profile: 'daily' });
  for (const name of HAND_BONE_NAMES) {
    const bone = state.fingers[name];
    assert.equal(bone.node, nodes[name]);
    assert.notEqual(bone.rest, bone.node.quaternion);
    assert.ok(bone.rest.equals(rest[name]));
    assert.ok(bone.node.quaternion.angleTo(target(bone, pose[name])) < 1e-6, name);
  }
  assert.ok(state.fingers.rightThumbProximal.node.quaternion.angleTo(rest.rightThumbProximal) > .01);
});

test('manual hand targets switch from actual pose without touching bind or arm transition state', () => {
  const { rig, state } = fixture();
  rig.setPerformance(frame); rig.setHandPosePreview('softGrip'); settle(rig, state);
  const controller = rig.poseTransition, diagnostic = controller.getDiagnostic();
  const actual = rotations(state);
  assert.equal(rig.setHandPosePreview('open'), true);
  rig._applyFingers(state, frame, 0);
  for (const [name, bone] of Object.entries(state.fingers)) assert.ok(bone.node.quaternion.equals(actual[name]));
  const expected = sampleHandPose(frame, { profile: 'daily', preview: { name: 'open', side: 'right' } });
  rig._applyFingers(state, frame, 1 / 60);
  for (const name of HAND_BONE_NAMES.filter(name => name.startsWith('right'))) {
    const bone = state.fingers[name], destination = target(bone, expected[name]);
    const distance = actual[name].angleTo(destination);
    assert.ok(bone.node.quaternion.angleTo(actual[name]) > 0);
    assert.ok(bone.node.quaternion.angleTo(destination) < distance);
  }
  assert.equal(rig.poseTransition, controller);
  assert.deepEqual(controller.getDiagnostic(), diagnostic);
  settle(rig, state);
  for (const [name, bone] of Object.entries(state.fingers)) assert.ok(bone.node.quaternion.angleTo(target(bone, expected[name])) < 1e-6);
});

test('disable clears manual selection at zero time and every joint returns to its binding', () => {
  const { rig, state } = fixture();
  rig.setHandPosePreview('softGrip', 'left'); settle(rig, state);
  const actual = rotations(state);
  rig.setFingerRelaxation(false);
  assert.deepEqual(rig.getHandPosePreview(), { available: true, enabled: false, name: null, side: 'right' });
  rig._applyFingers(state, frame, 0);
  for (const [name, bone] of Object.entries(state.fingers)) assert.ok(bone.node.quaternion.equals(actual[name]));
  settle(rig, state);
  for (const bone of Object.values(state.fingers)) assert.ok(bone.node.quaternion.angleTo(bone.rest) < 1e-6);
  assert.equal(rig.setHandPosePreview('open'), false);
  rig.setFingerRelaxation(true); settle(rig, state, { name: null, weight: 0 });
  assert.equal(rig.getHandPosePreview().name, null);
});

test('completion, speech, another gesture and cancellation end inspection, then recover using renderer time', () => {
  const cancellationReasons = ['stop', 'interrupted', 'playback_stopped', 'recording', 'thinking', 'complete'];
  for (const input of [
    { ...frame, source: 'llm' }, { ...frame, source: 'fallback' }, { ...frame, name: 'wave' },
    { name: null, weight: 0 },
    ...cancellationReasons.map(reason => ({ ...frame, transition: { reason } })),
  ]) {
    const { rig, state } = fixture();
    rig.setPerformance(frame); rig.setHandPosePreview('softGrip'); settle(rig, state);
    const actual = rotations(state);
    rig.setPerformance(input);
    assert.equal(rig.getHandPosePreview().name, null);
    rig._applyFingers(state, input, 0);
    for (const [name, bone] of Object.entries(state.fingers)) assert.ok(bone.node.quaternion.equals(actual[name]));
    settle(rig, state, input);
    const expected = sampleHandPose(input, { profile: 'daily' });
    for (const [name, bone] of Object.entries(state.fingers)) assert.ok(bone.node.quaternion.angleTo(target(bone, expected[name])) < 1e-6);
  }
});

test('partial rigs skip missing fingers, unadapted models remain unchanged and invalid controls reject', () => {
  const partial = fixture({ names: ['rightThumbMetacarpal', 'leftIndexProximal'] });
  assert.equal(partial.rig.getFingerRelaxation().enabled, true);
  assert.equal(partial.rig.getHandPosePreview().available, false);
  assert.equal(partial.rig.setHandPosePreview('relaxed'), false);
  settle(partial.rig, partial.state);
  assert.ok(Object.values(rotations(partial.state)).every(q => q.toArray().every(Number.isFinite)));
  const leftOnly = fixture({ names: HAND_BONE_NAMES.filter(name => name.startsWith('left')) });
  assert.equal(leftOnly.rig.setHandPosePreview('open'), false);
  assert.equal(leftOnly.rig.setHandPosePreview('open', 'left'), true);
  assert.deepEqual(leftOnly.rig.getHandPosePreview(), { available: true, enabled: true, name: 'open', side: 'left' });
  for (const [name, side] of [['fist', 'right'], ['open', 'both'], [42, 'right']]) {
    assert.throws(() => partial.rig.setHandPosePreview(name, side), RangeError);
  }
  const other = fixture({ model: 'Other VRM' }), initial = Object.fromEntries(Object.entries(other.nodes).map(([name, node]) => [name, node.quaternion.clone()]));
  assert.deepEqual(other.state.fingers, {});
  assert.equal(other.rig.setHandPosePreview('open'), false);
  settle(other.rig, other.state);
  for (const [name, node] of Object.entries(other.nodes)) assert.ok(node.quaternion.equals(initial[name]));
  assert.equal(new VrmRig().getHandPosePreview().available, false);
});

test('large deltas are bounded and dispose removes stale inspection state', () => {
  const a = fixture(), b = fixture();
  a.rig.setHandPosePreview('softGrip'); b.rig.setHandPosePreview('softGrip');
  a.rig._applyFingers(a.state, frame, 20); b.rig._applyFingers(b.state, frame, .05);
  for (const name of HAND_BONE_NAMES) assert.ok(a.nodes[name].quaternion.angleTo(b.nodes[name].quaternion) < 1e-7);
  a.rig._state = null; a.rig.dispose();
  assert.equal(a.rig.handPosePreview, null);
});
