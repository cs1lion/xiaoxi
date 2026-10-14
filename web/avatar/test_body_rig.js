import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VrmRig } from './render/vrm.js';
import { DAILY_GESTURE_MODEL_NAME } from './render/gesture-poses.js';
import { mapParams } from './render/vrm-mapping.js';

function fixture({ model = DAILY_GESTURE_MODEL_NAME, partial = false } = {}) {
  const rig = new VrmRig(), scene = new THREE.Scene();
  const nodes = Object.fromEntries(['spine', 'chest', 'rightShoulder', 'neck', 'head', 'rightUpperArm']
    .map(name => [name, new THREE.Object3D()]));
  scene.add(nodes.spine); nodes.spine.add(nodes.chest);
  nodes.chest.add(nodes.neck, nodes.rightShoulder);
  nodes.neck.add(nodes.head); nodes.rightShoulder.add(nodes.rightUpperArm);
  nodes.spine.position.y = .8; nodes.chest.position.y = .3;
  nodes.neck.position.y = .2; nodes.head.position.y = .1;
  nodes.rightShoulder.position.set(-.08, .12, 0);
  nodes.chest.quaternion.setFromEuler(new THREE.Euler(.02, -.03, .01));
  nodes.rightShoulder.quaternion.setFromEuler(new THREE.Euler(-.01, .04, -.02));
  const vrm = { meta: { name: model }, humanoid: {
    getNormalizedBoneNode: name => partial && name === 'rightShoulder' ? null : nodes[name],
    update() {},
  }, expressionManager: { setValue() {} }, update() {} };
  const capture = name => ({ node: nodes[name], rest: nodes[name].quaternion.clone() });
  const state = { vrm, scene, camera: new THREE.PerspectiveCamera(), target: new THREE.Object3D(), modelHeight: 1.7,
    renderer: { render() {} }, expressions: {}, springBones: [],
    bones: Object.fromEntries(['spine', 'neck', 'head'].map(name => [name, capture(name)])),
    arms: { rightUpperArm: capture('rightUpperArm') }, fingers: {} };
  scene.add(state.target); rig._state = state; rig._captureBody(state);
  return { rig, state, nodes };
}
const settle = (rig, state, frame) => {
  for (let i = 0; i < 120; ++i) rig._applyBody(state, frame, 1 / 60);
};

test('only available Daily joints are captured without sharing their binding rotations', () => {
  const { rig, state, nodes } = fixture({ partial: true });
  assert.deepEqual(Object.keys(state.bodyCoordination), ['chest']);
  assert.deepEqual(rig.getBodyCoordination(), { available: true, enabled: true });
  assert.notEqual(state.bodyCoordination.chest.rest, nodes.chest.quaternion);
  assert.ok(state.bodyCoordination.chest.rest.equals(nodes.chest.quaternion));
  const other = fixture({ model: 'Other VRM' }), before = other.nodes.chest.quaternion.clone();
  other.rig._applyBody(other.state, { name: 'wave', weight: 1 }, 1 / 60);
  assert.ok(other.nodes.chest.quaternion.equals(before));
  assert.deepEqual(other.rig.getBodyCoordination(), { available: false, enabled: false });
  assert.deepEqual(new VrmRig().getBodyCoordination(), { available: false, enabled: false });
});

test('zero-time mode changes preserve actual body rotations and do not restart arm transitions', () => {
  const { rig, state, nodes } = fixture();
  settle(rig, state, { name: 'wave', weight: 1 });
  const signal = { id: 4, reason: 'start', from: 'self', to: 'palm' };
  rig.poseTransition.update(signal, { arm: new THREE.Quaternion() },
    { arm: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, .3, 0)) }, .01);
  const diagnostic = rig.poseTransition.getDiagnostic(), actual = nodes.chest.quaternion.clone();
  rig.setBodyCoordination(false); rig._applyBody(state, { name: 'wave', weight: 1 }, 0);
  assert.ok(nodes.chest.quaternion.equals(actual));
  assert.deepEqual(rig.poseTransition.getDiagnostic(), diagnostic);
  settle(rig, state, { name: 'wave', weight: 1 });
  for (const { node, rest } of Object.values(state.bodyCoordination)) assert.ok(node.quaternion.angleTo(rest) < 1e-7);
  assert.deepEqual(rig.getBodyCoordination(), { available: true, enabled: false });
});

test('cancelled gestures recover on renderer time and long deltas stay bounded', () => {
  const a = fixture(), b = fixture(), frame = { name: 'palm', weight: 1, strength: .4 };
  a.rig._applyBody(a.state, frame, 8); b.rig._applyBody(b.state, frame, .05);
  assert.ok(a.nodes.chest.quaternion.angleTo(b.nodes.chest.quaternion) < 1e-7);
  settle(a.rig, a.state, frame);
  const before = a.nodes.chest.quaternion.clone();
  a.rig._applyBody(a.state, { name: null, weight: 0 }, 0);
  assert.ok(a.nodes.chest.quaternion.equals(before));
  settle(a.rig, a.state, { name: null, weight: 0 });
  for (const { node, rest } of Object.values(a.state.bodyCoordination)) assert.ok(node.quaternion.angleTo(rest) < 1e-7);
});

test('production draw retains mapped spine/head and local arm targets, then aims gaze in the final head frame', () => {
  const a = fixture(), b = fixture(); b.rig.setBodyCoordination(false);
  const params = { ParamBodyAngleX: .4, ParamBodyAngleZ: -.2, ParamBodyLeanX: .1,
    ParamAngleX: .2, ParamAngleY: -.1, ParamEyeBallX: .3, ParamEyeBallY: -.2 };
  const frame = { name: 'palm', weight: 1, strength: .4 };
  for (let i = 0; i < 120; ++i) {
    for (const { rig } of [a, b]) { rig.setPerformance(frame); rig.draw(params, 1 / 60); }
  }
  const mapped = mapParams(params);
  for (const name of ['spine', 'neck', 'head']) {
    const expected = a.state.bones[name].rest.clone().multiply(new THREE.Quaternion()
      .setFromEuler(new THREE.Euler(...mapped[name])));
    assert.ok(a.nodes[name].quaternion.angleTo(expected) < 1e-7);
    assert.ok(a.nodes[name].quaternion.angleTo(b.nodes[name].quaternion) < 1e-7);
  }
  assert.ok(a.nodes.rightUpperArm.quaternion.angleTo(b.nodes.rightUpperArm.quaternion) < 1e-7);
  const head = a.nodes.head.getWorldPosition(new THREE.Vector3());
  const direction = a.state.target.position.clone().sub(head).normalize();
  const expected = new THREE.Vector3(...mapped.gaze).normalize()
    .applyQuaternion(a.nodes.head.getWorldQuaternion(new THREE.Quaternion()));
  assert.ok(direction.distanceTo(expected) < 1e-12, 'gaze uses the current animated body and head');
  assert.ok(head.distanceTo(b.nodes.head.getWorldPosition(new THREE.Vector3())) > .0001);
});
