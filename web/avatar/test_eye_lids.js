/* The shipped V3 asset is exercised with the real VRM expression manager.
 * File loading and rasterization are the only substitutes; manager reset/bind
 * behavior, the renderer adapter, and frozen calibration are production code. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';
import { VRMExpression, VRMExpressionManager, VRMExpressionMorphTargetBind } from '@pixiv/three-vrm';
import { createEyeMorphAdapter } from './render/eye-morph.js';
import { VrmRig } from './render/vrm.js';
import { AvatarCalibration } from './calibration.js';
import { ALL_PARAMS, DEFAULT_NEUTRAL, ParamStore, Rigger, clampParam } from './params.js';
import { EyeExpressionDriver, combineEyeOpen } from './drivers/eye-expression.js';
import { EmotionDriver } from './drivers/emotion.js';
import { Avatar } from './index.js';

const CHANNELS = Object.freeze({ smileL: 'neuro_eye_smile_L', smileR: 'neuro_eye_smile_R',
  lowerL: 'neuro_lower_lid_raise_L', lowerR: 'neuro_lower_lid_raise_R' });
const close = (actual, expected, label = '') => assert.ok(Number.isFinite(actual) && Math.abs(actual - expected) < 1e-8,
  `${label}: ${actual} != ${expected}`);

function fixture(asset = 'seed-daily-lids', { omit = [] } = {}) {
  const bytes = readFileSync(new URL(`../models/${asset}.vrm`, import.meta.url));
  const gltf = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString());
  const scene = new THREE.Scene(), nodes = new Map();
  for (let i = 0; i < gltf.nodes.length; i++) {
    const mesh = gltf.meshes[gltf.nodes[i].mesh];
    if (!mesh?.extras?.targetNames?.length) continue;
    const node = new THREE.Object3D();
    node.morphTargetDictionary = Object.fromEntries(mesh.extras.targetNames
      .map((name, index) => [name, index]).filter(([name]) => !omit.includes(name)));
    node.morphTargetInfluences = mesh.extras.targetNames.map(() => 0);
    scene.add(node); nodes.set(i, node);
  }
  const expressionManager = new VRMExpressionManager();
  for (const [name, source] of Object.entries(gltf.extensions.VRMC_vrm.expressions.preset)) {
    const expression = new VRMExpression(name);
    for (const key of ['isBinary', 'overrideBlink', 'overrideMouth', 'overrideLookAt'])
      if (source[key] != null) expression[key] = source[key];
    for (const bind of source.morphTargetBinds || []) expression.addBind(new VRMExpressionMorphTargetBind({
      primitives: [nodes.get(bind.node)], index: bind.index, weight: bind.weight,
    }));
    expressionManager.registerExpression(expression);
  }
  const native = name => {
    for (const node of nodes.values()) {
      for (const [raw, index] of Object.entries(node.morphTargetDictionary))
        if (raw.slice(raw.lastIndexOf('.') + 1) === name) return node.morphTargetInfluences[index];
    }
    return undefined;
  };
  return { scene, meta: gltf.extensions.VRMC_vrm.meta, expressionManager, native, nodes };
}

function playbackFixture(options) {
  const vrm = fixture('seed-daily-lids', options), rig = new VrmRig();
  const head = new THREE.Object3D(); head.position.set(0, 1.4, 0); vrm.scene.add(head);
  const manager = vrm.expressionManager;
  vrm.humanoid = { update() {} }; vrm.update = () => manager.update();
  const camera = new THREE.PerspectiveCamera(30, 1, .01, 100);
  camera.position.set(0, 1.4, 4); camera.lookAt(0, 1.4, 0); camera.updateMatrixWorld(true);
  rig._state = { vrm, scene: vrm.scene, camera, target: new THREE.Object3D(), modelHeight: 1.7,
    bones: { head: { node: head, rest: head.quaternion.clone() } }, springBones: [],
    expressions: Object.fromEntries(Object.keys(manager.expressionMap).map(name => [name, manager.getExpression(name)])),
    eyeAdapter: createEyeMorphAdapter(vrm), renderer: { render() {},
      domElement: { width: 720, height: 720, getBoundingClientRect: () => ({width: 720, height: 720}) } },
    ambient: new THREE.HemisphereLight(), key: new THREE.DirectionalLight(), fill: new THREE.DirectionalLight() };
  const avatar = { svg: rig, store: new ParamStore(), rendererKind: 'vrm', _rendererRequest: 1,
    _raf: 41, clock: 1, starts: 0, _start() { this.starts++; this._raf = 42; } };
  vrm.scene.updateMatrixWorld(true);
  return { vrm, rig, avatar };
}

function productionFrameFixture(shift, gestures = {}) {
  const {vrm, rig} = playbackFixture(), avatar = new Avatar();
  avatar.configure({
    response: Object.fromEntries(ALL_PARAMS.map(key => [key, 0])),
    emotions: {base: {arousal: 1, shift: {}}, 得意: {arousal: 1, shift}}, gestures,
  });
  avatar.svg = rig; avatar.rendererKind = 'vrm';
  avatar.emotion.set('得意');
  for (let frame = 0; frame < 600; frame++) avatar.emotion.update(1 / 60);
  return {avatar, vrm, rig};
}

test('lower-lid parameters have zero neutrals and unsigned arbitration bounds', () => {
  const store = new ParamStore(), rigger = new Rigger(); rigger.register('eye', 20);
  for (const key of ['ParamEyeLLower', 'ParamEyeRLower']) {
    assert.ok(ALL_PARAMS.includes(key)); assert.equal(DEFAULT_NEUTRAL[key], 0);
    assert.equal(store.neutral[key], 0); close(clampParam(key, -2), 0); close(clampParam(key, 2), 1);
    close(clampParam(key, NaN), 0); close(clampParam(key, Infinity), 0);
  }
  rigger.set('eye', 'ParamEyeLLower', 4); rigger.set('eye', 'ParamEyeRLower', -4); rigger.commit(store);
  close(store.target.ParamEyeLLower, 1); close(store.target.ParamEyeRLower, 0);
});

test('enhanced profile transfers without jumping, native fallbacks retire lower lids, and release returns to neutral', () => {
  const driver = new EyeExpressionDriver({profile: 'native'});
  driver.set('happy'); driver.update(1);
  const legacy = driver.sample();
  assert.equal(legacy.values.ParamEyeLLower, 0); assert.equal(legacy.values.ParamEyeRLower, 0);
  driver.setProfile('seed-lids');
  assert.deepEqual(driver.sample().values, legacy.values, 'profile switch starts from the visible pose');
  driver.update(1); const enhanced = driver.sample();
  assert.equal(enhanced.name, 'happy'); assert.equal(enhanced.weight, 1);
  assert.ok(enhanced.values.ParamEyeLLower > 0); assert.ok(enhanced.values.ParamEyeRLower > 0);
  for (const key of ['ParamEyeLLower', 'ParamEyeRLower']) assert.ok(enhanced.ownedKeys.includes(key));
  for (const key of ['ParamEyeBallX', 'ParamEyeBallY', 'ParamMouthOpenY']) assert.ok(!enhanced.ownedKeys.includes(key));
  for (const value of Object.values(enhanced.values)) assert.ok(Number.isFinite(value));
  assert.equal(combineEyeOpen(enhanced.values.ParamEyeLOpen, 1), 0);
  assert.deepEqual(driver.sample().values, enhanced.values, 'procedural blinking cannot mutate expression intent');
  driver.setProfile('native'); assert.deepEqual(driver.sample().values, enhanced.values);
  driver.update(1); assert.deepEqual(driver.sample().values, legacy.values);
  driver.setProfile('seed-lids'); driver.update(1); driver.setIntensity(0);
  driver.update(1); close(driver.sample().values.ParamEyeLLower, 0); close(driver.sample().values.ParamEyeRLower, 0);
  driver.setIntensity(1); driver.update(1); driver.set(null); driver.update(1);
  assert.equal(driver.sample().active, false); close(driver.sample().weight, 0);
  close(driver.sample().values.ParamEyeLLower, 0); close(driver.sample().values.ParamEyeRLower, 0);
});

test('ordinary emotion gains candidate smile intent only when missing, with explicit zero/configured values taking precedence', () => {
  const table = {base: {shift: {}}, 得意: {arousal: .85, shift: {ParamEyeLOpen: .66, ParamEyeROpen: .62, ParamMouthForm: .52}}};
  const driver = new EmotionDriver(table);
  const settle = () => { for (let frame = 0; frame < 600; frame++) driver.update(1 / 60); return driver.shift(); };
  driver.set('得意'); const native = settle();
  assert.ok(!('ParamEyeLSmile' in native)); assert.ok(!('ParamEyeRSmile' in native));
  driver.setEyeLidProfile('seed-lids'); const enhanced = driver.shift();
  assert.ok(enhanced.ParamEyeLSmile > 0 && enhanced.ParamEyeRSmile > 0);
  for (const [key, value] of Object.entries(native)) close(enhanced[key], value, `protected ${key}`);
  assert.equal(driver.ownsGaze, false);
  driver.setEyeLidProfile('native'); assert.deepEqual(driver.shift(), native);
  driver.setTable({...table, 得意: {...table.得意, shift: {...table.得意.shift, ParamEyeLSmile: 0, ParamEyeRSmile: .12}}});
  driver.setEyeLidProfile('seed-lids'); const explicit = driver.shift();
  close(explicit.ParamEyeLSmile, 0, 'explicit suppression');
  close(explicit.ParamEyeRSmile, .12 * (.45 + .55 * driver.arousal), 'explicit configured value');
  assert.ok(!('ParamEyeLLower' in explicit), 'explicit smile owns its side and cannot generate a default lower lid');
  assert.equal(table.得意.shift.ParamEyeLSmile, undefined, 'fallback cannot mutate configuration');
});

test('candidate emotion smiles follow the existing crossfade and retire when emotion returns to a nonsmiling state', () => {
  const driver = new EmotionDriver({base: {arousal: .25, shift: {}},
    得意: {arousal: .85, blend: .3, shift: {ParamMouthForm: .52}},
    认真: {arousal: .6, blend: .4, shift: {ParamEyeLOpen: 1, ParamEyeROpen: 1}}});
  driver.setEyeLidProfile('seed-lids'); driver.set('得意');
  for (let frame = 0; frame < 600; frame++) driver.update(1 / 60);
  const outgoing = driver.shift(); driver.set('认真');
  close(driver.shift().ParamEyeLSmile, outgoing.ParamEyeLSmile, 'new transition starts with outgoing smile');
  assert.equal(driver.ownsGaze, false, 'adding smile cannot take over gaze');
  for (let frame = 0; frame < 600; frame++) {
    driver.update(1 / 60);
    for (const value of Object.values(driver.shift())) assert.ok(Number.isFinite(value));
  }
  assert.ok(!('ParamEyeLSmile' in driver.shift())); assert.ok(!('ParamEyeRSmile' in driver.shift()));
  close(driver.shift().ParamEyeLOpen, 1); close(driver.shift().ParamEyeROpen, 1);
});

test('production Avatar frame preserves explicitly configured lower zero and smile amplitude without post-commit splitting', () => {
  const shift = {ParamEyeLLower: 0, ParamEyeLSmile: .8, ParamEyeRLower: 0, ParamEyeRSmile: 0};
  const {avatar} = productionFrameFixture(shift);
  avatar._frame(1 / 60);
  assert.equal(avatar.eyeExpression.profile, 'seed-lids');
  assert.equal(avatar.emotion.eyeLidProfile, 'seed-lids');
  close(avatar.store.target.ParamEyeLLower, 0); close(avatar.store.target.ParamEyeLSmile, .8);
  close(avatar.store.target.ParamEyeRLower, 0); close(avatar.store.target.ParamEyeRSmile, 0);
  assert.deepEqual(shift, {ParamEyeLLower: 0, ParamEyeLSmile: .8, ParamEyeRLower: 0, ParamEyeRSmile: 0});
});

test('production Avatar frame respects a partially configured lower lid and generates defaults only for the untouched side', () => {
  const {avatar} = productionFrameFixture({ParamEyeLLower: .1});
  avatar._frame(1 / 60);
  close(avatar.store.target.ParamEyeLLower, .1); close(avatar.store.target.ParamEyeLSmile, 0);
  assert.ok(avatar.store.target.ParamEyeRLower > .1 && avatar.store.target.ParamEyeRSmile > .1,
    'unconfigured opposite side still participates in the natural candidate emotion');
  avatar.svg.setEyeLidMode('native'); avatar._frame(1 / 60);
  close(avatar.store.target.ParamEyeLLower, .1, 'native mode cannot mutate an explicit parameter intent');
  close(avatar.store.target.ParamEyeRLower, 0, 'native mode retires generated defaults');
});

test('production Avatar frame preserves real higher-layer gesture targets after arbitration', () => {
  const gestureTargets = {ParamEyeLLower: .1, ParamEyeLSmile: .8, ParamEyeRLower: 0, ParamEyeRSmile: .6};
  const {avatar} = productionFrameFixture({}, {explicitLids: {hold: true, priority: 50,
    keys: [{at: 0, dur: .01, params: gestureTargets}]}});
  avatar.emotion.set('base');
  for (let frame = 0; frame < 600; frame++) avatar.emotion.update(1 / 60);
  assert.equal(avatar.gesture.play('explicitLids', 0), true);
  avatar._frame(1 / 60);
  for (const [key, value] of Object.entries(gestureTargets)) close(avatar.store.target[key], value, key);
  assert.equal(avatar.eyeExpression.sample().active, false, 'preview cannot hide a gesture arbitration error');
});

test('complete V3 advertises independent lower lids and smiles while protecting gaze/vowel bindings', () => {
  const vrm = fixture(), originalLook = vrm.expressionManager.getExpression('lookLeft').binds.slice();
  const originalVowel = vrm.expressionManager.getExpression('aa').binds.slice();
  const adapter = createEyeMorphAdapter(vrm);
  assert.equal(adapter.capabilities.profile, 'seed-lids');
  assert.equal(adapter.capabilities.channels.smile.mode, 'independent');
  assert.equal(adapter.capabilities.channels.lowerLid.available, true);
  assert.equal(adapter.capabilities.channels.lowerLid.mode, 'independent');
  assert.deepEqual(vrm.expressionManager.getExpression('lookLeft').binds, originalLook);
  assert.deepEqual(vrm.expressionManager.getExpression('aa').binds, originalVowel);
});

test('one-sided lower lift and smile never move the opposite-side target or native bilateral smile', () => {
  const vrm = fixture(), adapter = createEyeMorphAdapter(vrm);
  for (const [side, opposite] of [['L', 'R'], ['R', 'L']]) {
    adapter.write({ [`ParamEye${side}Lower`]: .6, [`ParamEye${side}Smile`]: .4 });
    vrm.expressionManager.update();
    close(vrm.native(CHANNELS['lower' + side]), .6); close(vrm.native(CHANNELS['smile' + side]), .4);
    close(vrm.native(CHANNELS['lower' + opposite]), 0); close(vrm.native(CHANNELS['smile' + opposite]), 0);
    close(vrm.native('eye_smile'), 0);
  }
});

test('partial blink gates each lid independently, full blink suppresses both, release restores exact intent', () => {
  const vrm = fixture(), adapter = createEyeMorphAdapter(vrm), manager = vrm.expressionManager;
  const intent = { ParamEyeLLower: .7, ParamEyeRLower: .2, ParamEyeLSmile: .8, ParamEyeRSmile: .3 };
  manager.setValue('aa', .65); manager.setValue('lookLeft', .45);
  for (const [openL, openR] of [[1, 1], [.4, .8], [0, 1], [0, 0], [1, 1]]) {
    manager.setValue('blinkLeft', 1 - openL); manager.setValue('blinkRight', 1 - openR);
    adapter.write({ ...intent, ParamEyeLOpen: openL, ParamEyeROpen: openR }); manager.update();
    close(vrm.native(CHANNELS.lowerL), .7 * openL); close(vrm.native(CHANNELS.lowerR), .2 * openR);
    close(vrm.native(CHANNELS.smileL), .8 * openL); close(vrm.native(CHANNELS.smileR), .3 * openR);
    close(vrm.native('blink_L'), 1 - openL); close(vrm.native('blink_R'), 1 - openR);
    close(vrm.native('lip_a'), .65); close(vrm.native('look_left'), .45);
  }
});

test('native comparison retires added lids/smiles, restores the original smile, and switching back has no stale weights', () => {
  const vrm = fixture(), adapter = createEyeMorphAdapter(vrm);
  const params = { ParamEyeLOpen: .4, ParamEyeROpen: .8, ParamEyeLSmile: .6, ParamEyeRSmile: .2,
    ParamEyeLLower: .8, ParamEyeRLower: .6, ParamBrowLY: .5, ParamBrowRY: -.3 };
  adapter.write(params); vrm.expressionManager.update(); close(vrm.native(CHANNELS.lowerL), .32);
  adapter.write(params, { lidMode: 'native' }); vrm.expressionManager.update();
  for (const name of Object.values(CHANNELS)) close(vrm.native(name), 0);
  close(vrm.native('eye_smile'), .16); close(vrm.native('neuro_brow_raise_L'), .5);
  adapter.write(params, { lidMode: 'calibrated', browMode: 'native' }); vrm.expressionManager.update();
  close(vrm.native('eye_smile'), 0); close(vrm.native(CHANNELS.smileL), .24);
  close(vrm.native('eye_brow_up_L'), .5); close(vrm.native('neuro_brow_raise_L'), 0);
  adapter.write(); vrm.expressionManager.update();
  for (const name of [...Object.values(CHANNELS), 'eye_smile', 'eye_brow_up_L']) close(vrm.native(name), 0);
});

test('partial or legacy assets never claim a complete lower-lid profile or approximate a missing lower channel', () => {
  for (const omitted of Object.values(CHANNELS)) {
    const vrm = fixture('seed-daily-lids', { omit: [omitted] }), adapter = createEyeMorphAdapter(vrm);
    assert.notEqual(adapter.capabilities.profile, 'seed-lids', `missing ${omitted}`);
  }
  for (const asset of ['seed-daily', 'seed-daily-eyes', 'sample']) {
    const vrm = fixture(asset), adapter = createEyeMorphAdapter(vrm);
    assert.equal(adapter.capabilities.channels.lowerLid.available, false, asset);
    adapter.write({ ParamEyeLLower: 1, ParamEyeRLower: 1, ParamEyeLSmile: .4, ParamEyeRSmile: .4 });
    vrm.expressionManager.update();
    close(vrm.native(asset === 'sample' ? 'Fcl_EYE_Joy_L' : 'eye_smile'), .4);
    close(vrm.native(asset === 'sample' ? 'Fcl_EYE_Surprised' : 'eye_open'), 0);
  }
});

test('the production rig closes lower/smile on full blink and validates independent lid/brow mode commands', () => {
  const { vrm, rig } = playbackFixture();
  for (const invalid of ['', 'unsupported', undefined]) assert.throws(() => rig.setEyeLidMode(invalid), RangeError);
  rig.setEyeBrowMode('native'); rig.setEyeLidMode('calibrated');
  const params = { ParamEyeLOpen: 0, ParamEyeROpen: 0, ParamEyeLLower: 1, ParamEyeRLower: 1,
    ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamMouthOpenY: .6, ParamBrowLY: .4 };
  rig.draw(params, 1 / 60);
  for (const name of Object.values(CHANNELS)) close(vrm.native(name), 0);
  close(vrm.native('blink_L'), 1); close(vrm.native('blink_R'), 1); close(vrm.native('lip_a'), .6);
  close(vrm.native('eye_brow_up_L'), .4); close(vrm.native('neuro_brow_raise_L'), 0);
  rig.draw({ ...params, ParamEyeLOpen: 1, ParamEyeROpen: 1 }, 1 / 60);
  for (const name of Object.values(CHANNELS)) close(vrm.native(name), 1);
});

test('frozen calibration switches lid modes without refitting and restores prior modes, morphs, and playback', () => {
  const { vrm, rig, avatar } = playbackFixture();
  rig.setEyeLidMode('native'); rig.setEyeBrowMode('calibrated');
  rig.draw({ParamEyeLSmile: .4, ParamEyeRSmile: .4}, 0);
  const before = [...vrm.nodes.values()].map(node => [...node.morphTargetInfluences]);
  const session = new AvatarCalibration(avatar), initial = session.enter();
  const params = { ParamEyeLLower: .6, ParamEyeRLower: 0, ParamEyeLSmile: .4, ParamEyeRSmile: 0,
    ParamEyeLOpen: 1, ParamEyeROpen: 1 };
  const enhanced = session.pose({expression: 'daily', overrides: params, lidMode: 'calibrated'});
  close(enhanced.nativeMorphs[CHANNELS.lowerL], .6); close(enhanced.nativeMorphs[CHANNELS.lowerR], 0);
  const native = session.pose({expression: 'daily', overrides: params, lidMode: 'native'});
  close(native.nativeMorphs[CHANNELS.lowerL], 0); close(native.nativeMorphs.eye_smile, .2);
  assert.equal(native.fingerprint.id, initial.fingerprint.id);
  assert.equal(session.pose({expression: 'daily', overrides: params, lidMode: 'calibrated', blink: 1}).nativeMorphs[CHANNELS.lowerL], 0);
  assert.throws(() => session.pose({lidMode: 'missing'}), RangeError);
  session.exit();
  assert.equal(rig.eyeLidMode, 'native'); assert.equal(rig.eyeBrowMode, 'calibrated');
  assert.deepEqual([...vrm.nodes.values()].map(node => [...node.morphTargetInfluences]), before);
  assert.equal(avatar._raf, 42); assert.equal(avatar.starts, 1);
});
