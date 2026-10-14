import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VRMExpression, VRMExpressionManager, VRMExpressionMorphTargetBind } from '@pixiv/three-vrm';
import { AvatarCalibration, captureCalibrationFingerprint, compareCalibrationFingerprints } from './calibration.js';
import { ParamStore, Spring } from './params.js';
import { VrmRig } from './render/vrm.js';
import { createEyeMorphAdapter } from './render/eye-morph.js';
import { PoseTransition } from './drivers/pose-transition.js';

const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);

// Exercise the production VRM draw and expression/morph adapter using real
// Three objects. Only rasterization and file/network loading are stubbed.
function fixture({ running = true } = {}) {
  const rig = new VrmRig(), model = new THREE.Group(), scene = new THREE.Scene();
  model.name = 'Calibration asset'; scene.add(model);
  const head = new THREE.Object3D(), arm = new THREE.Object3D(), chest = new THREE.Object3D();
  head.name = 'Head'; head.position.set(0, 1.4, 0); model.add(head); model.add(arm);
  head.quaternion.setFromEuler(new THREE.Euler(.07, -.12, 0));
  const headRest = new THREE.Quaternion(), armRest = new THREE.Quaternion();
  arm.quaternion.setFromEuler(new THREE.Euler(.5, 0, 0));
  chest.name = 'Chest'; model.add(chest);
  chest.quaternion.setFromEuler(new THREE.Euler(.01, -.01, 0));
  const chestRest = new THREE.Quaternion();
  const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  mesh.name = 'Face'; model.add(mesh);
  mesh.morphTargetDictionary = { eye_smile: 0, eye_open: 1, blink_l: 2, blink_r: 3,
    eye_brow_up_L: 4, eye_brow_up_R: 5, eye_brow_down_L: 6, eye_brow_down_R: 7, cheek: 8, aa: 9 };
  mesh.morphTargetInfluences = Array(10).fill(0);
  const manager = new VRMExpressionManager(), expressions = {};
  for (const [name, index] of [['blinkLeft', 2], ['blinkRight', 3], ['aa', 9]]) {
    const expression = new VRMExpression(name);
    expression.addBind(new VRMExpressionMorphTargetBind({ primitives: [mesh], index, weight: 1 }));
    manager.registerExpression(expression); expressions[name] = expression;
  }
  manager.setValue('aa', .23); manager.update();
  const camera = new THREE.PerspectiveCamera(30, 1.25, .01, 100);
  camera.position.set(0, 1.4, 4); camera.lookAt(0, 1.4, 0); camera.updateMatrixWorld(true);
  const rect = { width: 600, height: 480 };
  const renderer = { domElement: { getBoundingClientRect: () => rect, width: 600, height: 480 },
    renders: 0, render() { this.renders++; } };
  const vrm = { scene: model, meta: { name: 'Test Seed' }, expressionManager: manager,
    humanoid: { update() {} }, lookAt: {}, update() { manager.update(); } };
  rig._state = { scene, vrm, renderer, camera, target: new THREE.Object3D(), modelHeight: 1.7,
    bones: { head: { node: head, rest: headRest } }, arms: { rightUpperArm: { node: arm, rest: armRest } },
    bodyCoordination: { chest: { node: chest, rest: chestRest } },
    expressions, springBones: [], eyeAdapter: createEyeMorphAdapter(vrm),
    ambient: new THREE.HemisphereLight(), key: new THREE.DirectionalLight(), fill: new THREE.DirectionalLight() };
  rig.performance = { name: 'wave', weight: .7 }; rig.secondaryScale = .8;
  const avatar = { svg: rig, store: new ParamStore(), rendererKind: 'vrm', _rendererRequest: 4,
    _raf: running ? 77 : 0, clock: 12.3, gazeMode: 'follow', eyeHighlight: .8,
    idle: { energy: .7, gaze: { x: .5 }, spring: new Spring(1, .7, .2) },
    performance: { queue: [{ name: 'wave' }], state: 'speaking' },
    starts: 0, _start() { this.starts++; this._raf = 88; } };
  avatar.store.current.ParamEyeLOpen = .62;
  avatar.store.target.ParamEyeLOpen = .55;
  avatar.eyeFrame = { params: { ...avatar.store.current }, blink: .3 };
  scene.updateMatrixWorld(true);
  return { avatar, rig, head, headRest, arm, armRest, chest, chestRest, mesh, manager, camera, rect, renderer };
}

test('fixed poses drive actual morphs, close half-open eyes fully, and recover exactly', () => {
  const { avatar, mesh } = fixture();
  const session = new AvatarCalibration(avatar);
  session.enter();
  const sleepy = session.pose({ expression: 'sleepy' });
  const half = session.pose({ expression: 'sleepy', blink: .5 });
  close(half.params.ParamEyeLOpen, sleepy.params.ParamEyeLOpen * .5);
  const blink = session.pose({ expression: 'sleepy', blink: 1 });
  assert.equal(blink.params.ParamEyeLOpen, 0);
  assert.equal(blink.params.ParamEyeROpen, 0);
  assert.equal(mesh.morphTargetInfluences[2], 1);
  assert.equal(mesh.morphTargetInfluences[3], 1);
  assert.equal(blink.native.eye_smile, 0);
  assert.deepEqual(session.pose({ expression: 'sleepy' }).params, sleepy.params);
  assert.ok(session.snapshot().registration.compatible);
  session.exit();
});

test('absolute overrides are bounded and composed before blink; expression/gaze/mouth do not invalidate registration', () => {
  const { avatar } = fixture();
  const session = new AvatarCalibration(avatar), initial = session.enter();
  const result = session.pose({ expression: 'curious', blink: .25, gaze: { x: -1, y: 1 }, mouthOpen: .6,
    overrides: { ParamEyeLOpen: .8, ParamPupil: 99, ParamAngleX: 1, Unknown: 4 } });
  close(result.params.ParamEyeLOpen, .6);
  assert.equal(result.params.ParamPupil, 1.5);
  assert.equal(result.params.ParamAngleX, 0);
  assert.equal(result.params.ParamEyeBallX, -1);
  assert.equal(result.params.ParamMouthOpenY, .6);
  assert.ok(!('Unknown' in result.params));
  assert.ok(compareCalibrationFingerprints(initial.fingerprint, result.fingerprint).compatible);
  const reset = session.reset();
  assert.equal(reset.params.ParamEyeLOpen, 1);
  assert.equal(reset.params.ParamEyeBallX, 0);
  session.exit();
});

test('exit restores driving state, displayed morphs and live bone transforms without replacing rest quaternions', () => {
  const { avatar, rig, head, headRest, arm, armRest, chest, chestRest, mesh, renderer } = fixture();
  const headBefore = head.quaternion.clone(), armBefore = arm.quaternion.clone(), morphBefore = [...mesh.morphTargetInfluences];
  const eyeFrame = avatar.eyeFrame, performance = rig.performance;
  const chestBefore = chest.quaternion.clone();
  const session = new AvatarCalibration(avatar);
  session.enter();
  assert.equal(avatar._raf, 0);
  assert.ok(chest.quaternion.equals(chestRest), 'calibration fixes the body layer even at zero frame time');
  session.pose({ expression: 'happy', mouthOpen: .8 });
  avatar.idle.energy = .05; avatar.idle.gaze.x = -.6; avatar.idle.spring.reset(3);
  avatar.performance.queue.push({ name: 'deny' });
  avatar.store.current.ParamEyeLOpen = 0;
  assert.equal(session.exit(), true);
  assert.equal(avatar.idle.energy, .7);
  assert.equal(avatar.idle.gaze.x, .5);
  assert.equal(avatar.idle.spring.x, .2);
  assert.deepEqual(avatar.performance.queue, [{ name: 'wave' }]);
  close(avatar.store.current.ParamEyeLOpen, .62);
  close(avatar.store.target.ParamEyeLOpen, .55);
  assert.equal(avatar.eyeFrame, eyeFrame);
  assert.equal(rig.performance, performance);
  close(rig.secondaryScale, .8);
  assert.ok(head.quaternion.equals(headBefore)); assert.ok(arm.quaternion.equals(armBefore));
  assert.ok(chest.quaternion.equals(chestBefore), 'exit restores the actual body pose');
  assert.equal(rig._state.bodyCoordination.chest.rest, chestRest);
  assert.equal(rig._state.bones.head.rest, headRest);
  assert.equal(rig._state.arms.rightUpperArm.rest, armRest);
  assert.deepEqual(mesh.morphTargetInfluences, morphBefore);
  assert.equal(avatar.starts, 1); assert.equal(avatar._raf, 88);
  assert.ok(renderer.renders > 0);
  assert.equal(session.exit(), false); assert.equal(avatar.starts, 1);
});

test('an already paused Avatar stays paused on exit', () => {
  const { avatar } = fixture({ running: false });
  const session = new AvatarCalibration(avatar);
  session.enter(); session.exit();
  assert.equal(avatar._raf, 0); assert.equal(avatar.starts, 0);
});

test('fixed eye calibration clears manual hand inspection and restores thumb pose and selection on exit', () => {
  const { avatar, rig } = fixture({ running: false });
  const thumb = new THREE.Object3D();
  thumb.quaternion.setFromEuler(new THREE.Euler(.09, .01, .12));
  rig._state.vrm.scene.add(thumb);
  const rest = new THREE.Quaternion(), actual = thumb.quaternion.clone();
  rig._state.fingers = { rightThumbMetacarpal: { node: thumb, rest } };
  const selected = rig.handPosePreview = Object.freeze({ name: 'softGrip', side: 'right' });
  const session = new AvatarCalibration(avatar);
  session.enter();
  assert.equal(rig.handPosePreview, null);
  assert.ok(thumb.quaternion.equals(rest), 'fixed calibration resets finger transforms at dt0');
  session.pose({ expression: 'happy' });
  assert.ok(thumb.quaternion.equals(rest));
  session.exit();
  assert.ok(thumb.quaternion.equals(actual));
  assert.equal(rig._state.fingers.rightThumbMetacarpal.rest, rest);
  assert.equal(rig.handPosePreview, selected);
});

test('exit preserves the real gesture transition controller, interrupted state, quaternion origins and enable flag', () => {
  const { avatar, rig, arm } = fixture();
  rig.poseTransition = new PoseTransition();
  rig.performanceTransitionsEnabled = true;
  rig.gesturePathsEnabled = false;
  rig.fingerRelaxationEnabled = false;
  rig.bodyCoordinationEnabled = false;
  const controller = rig.poseTransition;
  const signal = { id: 13, reason: 'interrupted', from: 'wave', to: 'palm' };
  const current = { rightUpperArm: arm.quaternion.clone() };
  const targets = { rightUpperArm: new THREE.Quaternion().setFromEuler(new THREE.Euler(-.3, .1, .5)) };
  controller.update(signal, current, targets, .025);
  const state = { ...controller.state }, diagnostic = controller.getDiagnostic();
  const origin = controller.origins.rightUpperArm.clone();
  const session = new AvatarCalibration(avatar);
  session.enter();
  assert.equal(controller.state, null, 'production draw without a transition signal resets it during calibration');
  session.pose({ expression: 'happy' });
  rig.performanceTransitionsEnabled = false;
  rig.gesturePathsEnabled = true;
  rig.fingerRelaxationEnabled = true;
  rig.bodyCoordinationEnabled = true;
  session.exit();
  assert.equal(rig.poseTransition, controller);
  assert.equal(rig.performanceTransitionsEnabled, true);
  assert.equal(rig.gesturePathsEnabled, false, 'calibration restores the prior path comparison mode');
  assert.equal(rig.fingerRelaxationEnabled, false, 'calibration restores the prior finger comparison mode');
  assert.equal(rig.bodyCoordinationEnabled, false, 'calibration restores the prior body comparison mode');
  assert.equal(controller.signalId, 13);
  assert.deepEqual(controller.state, state);
  assert.deepEqual(controller.getDiagnostic(), diagnostic);
  assert.ok(controller.origins.rightUpperArm.equals(origin));
  controller.update(signal, current, targets, .025);
  assert.ok(controller.state.elapsed > state.elapsed, 'the same transition resumes rather than restarting');
  assert.ok(controller.origins.rightUpperArm.equals(origin));
});

test('camera and lighting restore at full precision and repeated entry establishes a fresh registration', () => {
  const { avatar, camera, rig } = fixture();
  camera.position.z = 4.1234567890123;
  const before = camera.position.clone();
  const near = camera.near, intensity = rig._state.key.intensity;
  const session = new AvatarCalibration(avatar);
  session.enter();
  camera.position.set(1, 2, 3); camera.near = .08; camera.updateProjectionMatrix();
  rig._state.key.intensity = .31;
  session.exit();
  assert.ok(camera.position.equals(before)); assert.equal(camera.near, near);
  assert.equal(rig._state.key.intensity, intensity);
  const next = session.enter();
  assert.equal(next.registration.compatible, true);
  session.exit();
});

test('disposing the renderer while paused invalidates calibration without restarting a disposed avatar', () => {
  const { avatar, rig } = fixture();
  const session = new AvatarCalibration(avatar);
  session.enter(); rig._state = null; rig._generation++; avatar._rendererRequest++;
  assert.equal(session.isCurrent(), false);
  assert.throws(() => session.snapshot(), /model changed/);
  assert.equal(avatar._raf, 0); assert.equal(avatar.starts, 0);
});

test('a renderer request invalidates the old session and safely releases its paused RAF', () => {
  const { avatar } = fixture();
  const session = new AvatarCalibration(avatar);
  session.enter(); avatar._rendererRequest++;
  assert.equal(session.isCurrent(), false);
  assert.throws(() => session.pose({ expression: 'happy' }), /model changed/);
  assert.equal(session.active, false); assert.equal(avatar.starts, 1);
});

test('camera, viewport, appearance and asset changes explicitly invalidate registration', () => {
  const { avatar, camera, rect, rig } = fixture();
  const session = new AvatarCalibration(avatar), initial = session.enter();
  camera.position.z += .1;
  let result = compareCalibrationFingerprints(initial.fingerprint, captureCalibrationFingerprint(avatar));
  assert.ok(result.reasons.includes('camera'));
  rect.width += 10;
  result = compareCalibrationFingerprints(initial.fingerprint, captureCalibrationFingerprint(avatar));
  assert.ok(result.reasons.includes('viewport'));
  rig._state.key.intensity += 1;
  assert.ok(compareCalibrationFingerprints(initial.fingerprint, captureCalibrationFingerprint(avatar)).reasons.includes('appearance'));
  const changedAsset = jsonClone(initial.fingerprint); changedAsset.conditions.asset.generation++;
  assert.ok(compareCalibrationFingerprints(initial.fingerprint, changedAsset).reasons.includes('asset'));
  assert.equal(session.isCurrent(), false);
  assert.throws(() => session.pose({ expression: 'happy' }), /scene conditions changed/);
  assert.equal(session.active, false);
});

test('real viewport resize invalidates the frozen session and exit preserves the new aspect/framing', () => {
  const { avatar, camera, rect, rig } = fixture();
  rig._state.framing = { center: new THREE.Vector3(0, 1.4, 0), height: 1, width: .8, depth: .1 };
  rig._state.eyeFraming = { center: new THREE.Vector3(0, 1.45, 0), height: .4, width: .35, depth: .04 };
  rig._state.eyeFocus = false;
  rig._frameCamera(rig._state);
  const oldPosition = camera.position.clone();
  const session = new AvatarCalibration(avatar);
  session.enter({ eyeFocus: true });
  rect.width = 320; rect.height = 640;
  // Simulate the actual observer path firing before the stale session exits.
  camera.aspect = .5; rig._frameCamera(rig._state);
  assert.equal(session.isCurrent(), false);
  assert.throws(() => session.pose({ expression: 'happy' }), /scene conditions changed/);
  assert.equal(session.active, false);
  assert.equal(camera.aspect, .5);
  assert.equal(rig._state.eyeFocus, false);
  assert.ok(camera.position.z > oldPosition.z);
  assert.equal(avatar._raf, 88);
  const newSession = new AvatarCalibration(avatar);
  const snapshot = newSession.enter();
  assert.equal(snapshot.registration.compatible, true);
  assert.equal(snapshot.viewport.width, 320);
  assert.equal(snapshot.camera.aspect, .5);
  newSession.exit();
});

const jsonClone = value => JSON.parse(JSON.stringify(value));
test('untrusted numeric pose values emit bounded finite JSON and report actual capabilities/occlusion limitations', () => {
  const { avatar } = fixture();
  const session = new AvatarCalibration(avatar); session.enter();
  const result = session.pose({ expression: 'happy', intensity: NaN, blink: Infinity,
    gaze: { x: Infinity, y: 'bad' }, mouthOpen: -99,
    overrides: { ParamBrowLY: NaN, ParamEyeROpen: Infinity } });
  assert.ok(Object.values(result.params).every(Number.isFinite));
  assert.ok(result.headAnchor.available); close(result.headAnchor.x, .5); close(result.headAnchor.y, .5);
  assert.equal(result.capabilities.profile, 'seed');
  assert.ok(result.components.find(c => c.id === 'eyes').missing.length);
  assert.equal(result.components.find(c => c.id === 'eyes').occlusion.validated, false);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), result);
  assert.throws(() => session.pose({ expression: 'unsupported' }), /眉眼表情/);
  session.exit();
});
