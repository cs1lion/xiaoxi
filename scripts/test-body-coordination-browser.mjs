// Real Daily renderer/director QA. Offline fixed clock; no speech/model/mic or API mutations.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { chromium } from '@playwright/test';

const root = 'eval/avatar/body-coordination';
if (process.argv.length === 2 && !existsSync(`${root}/before/report.json`)) {
  console.log('SKIP body-coordination: 缺少 before 夹具（本地证据未入 git；先跑一次 --before 捕获后恢复）');
  process.exit(0);
}
const before = process.argv.includes('--before'), focus = process.argv.includes('--focus');
const obliqueOnly = process.argv.includes('--oblique-only');
const requested = process.argv.find(value => value.startsWith('--label='))?.slice(8);
if (requested) assert(/^[a-z0-9-]+$/.test(requested));
const label = before ? 'before' : requested || 'after', out = `${root}/${label}`;
const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const hash = async file => createHash('sha256').update(await readFile(file)).digest('hex');
const qAngle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.reduce((sum, value, i) => sum + value*b[i], 0)) / (Math.hypot(...a) * Math.hypot(...b))));
const distance = (a, b) => Math.hypot(...a.map((value, i) => value - b[i]));
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'] });
const page = await browser.newPage({ viewport: { width: 1320, height: 960 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(20000);
const errors = [], forbiddenRequests = [], checks = [], captures = [], dynamics = [], movies = {};
page.on('pageerror', error => errors.push(error.message));
try {
  await mkdir(out, { recursive: true });
  const sources = ['vrm.js', 'gesture-poses.js', 'finger-poses.js'];
  if (!before) sources.push('body-poses.js');
  const sourceHashes = {};
  for (const file of sources) {
    const body = await readFile(before ? `${out}/${file}` : `web/avatar/render/${file}`, 'utf8');
    if (!before) await writeFile(`${out}/${file}`, body);
    sourceHashes[file] = createHash('sha256').update(body).digest('hex');
    await page.route(`**/static/avatar/render/${file}`, route => route.fulfill({ contentType: 'text/javascript', body }));
  }
  await page.route('**/api/**', route => {
    if (route.request().method() !== 'GET') { forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`); return route.abort(); }
    return new URL(route.request().url()).pathname === '/api/avatar' ? route.continue() : route.fulfill({ json: { count: 0, items: [], context: '' } });
  });
  await page.addInitScript(() => {
    const h = window.bodyHarness = { microphoneRequests: 0 };
    const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
    window.AudioContext = class extends NativeAudioContext { constructor(...args) { super(...args); h.nativeCtx = this; } };
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => { h.microphoneRequests++; return Promise.reject(new Error('No microphone in body QA')); };
  });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  await page.evaluate(async () => {
    const THREE = await import('three'), { DEFAULT_NEUTRAL } = await import('/static/avatar/params.js');
    const a = window.__avatar, r = a.svg, s = r._state, h = window.bodyHarness;
    cancelAnimationFrame(a._raf); a._raf = 0; a.performance.stop(); a.lip.stop();
    r.setEyeFocus(false); r.setPresentation(false); r.setSecondaryMotion(0);
    s.observer?.disconnect(); s.renderer.setPixelRatio(1); s.renderer.setSize(600, 720, false); s.camera.aspect = 600/720; r._frameCamera(s);
    h.neutral = { ...DEFAULT_NEUTRAL }; h.clock = { currentTime: 0 }; h.sequence = 0; h.modelHeight = s.modelHeight;
    h.bodyNames = ['hips', 'spine', 'chest', 'upperChest', 'neck', 'head', 'leftShoulder', 'rightShoulder'];
    h.armNames = Object.keys(s.arms);
    h.fingerNames = ['left', 'right'].flatMap(side => ['Thumb', 'Index', 'Middle', 'Ring', 'Little'].flatMap(digit =>
      (digit === 'Thumb' ? ['Metacarpal', 'Proximal', 'Distal'] : ['Proximal', 'Intermediate', 'Distal']).map(joint => side + digit + joint)));
    const normalized = name => s.vrm.humanoid.getNormalizedBoneNode(name), raw = name => s.vrm.humanoid.getRawBoneNode(name);
    const names = [...h.bodyNames, ...h.armNames, ...h.fingerNames, 'leftEye', 'rightEye'];
    h.bind = Object.fromEntries(names.map(name => [name, { node: normalized(name), raw: raw(name), rest:
      s.bones?.[name]?.rest.clone() ?? s.arms?.[name]?.rest.clone() ?? s.fingers?.[name]?.rest.clone() ??
      s.bodyCoordination?.[name]?.rest.clone() ?? normalized(name)?.quaternion.clone() }]).filter(([, value]) => value.node));
    for (const value of Object.values(h.bind)) value.node.quaternion.copy(value.rest);
    s.vrm.humanoid.update(); s.scene.updateMatrixWorld(true);
    h.meshes = []; s.vrm.scene.traverse(node => { if (node.isMesh) h.meshes.push(node); });
    // Advance the original draw/VRM component pipeline at 60 Hz, submitting GPU
    // renders only when capturing evidence. World matrices still update each step.
    h.quietDraw = (params, dt) => {
      const render = s.renderer.render; s.renderer.render = () => {};
      try { r.draw(params, dt); } finally { s.renderer.render = render; }
      s.scene.updateMatrixWorld(true);
    };
    const vertexWorld = ({ mesh, index }) => {
      const point = new THREE.Vector3().fromBufferAttribute(mesh.geometry.getAttribute('position'), index);
      mesh.applyBoneTransform(index, point); return point.applyMatrix4(mesh.matrixWorld);
    };
    const tipVertices = {}, chestVertices = [], handVertices = { left: [], right: [] }, digits = ['Thumb', 'Index', 'Middle', 'Ring', 'Little'];
    const handDescendants = {};
    for (const side of ['left', 'right']) { handDescendants[side] = new Set(); raw(side + 'Hand').traverse(node => handDescendants[side].add(node)); }
    for (const digit of digits) tipVertices[digit] = [];
    for (const mesh of h.meshes) {
      if (!mesh.isSkinnedMesh) continue;
      const indices = mesh.geometry.getAttribute('skinIndex'), weights = mesh.geometry.getAttribute('skinWeight');
      if (!indices || !weights) continue;
      const referenced = mesh.geometry.index ? [...new Set(mesh.geometry.index.array)] : Array.from({ length: indices.count }, (_, index) => index);
      for (const index of referenced) {
        const weighted = bone => ['getX', 'getY', 'getZ', 'getW'].reduce((sum, get) => sum + (mesh.skeleton.bones[indices[get](index)] === bone ? weights[get](index) : 0), 0);
        if (weighted(raw('chest')) >= .35) chestVertices.push({ mesh, index });
        for (const side of ['left', 'right']) if (['getX', 'getY', 'getZ', 'getW'].reduce((sum, get) => sum + (handDescendants[side].has(mesh.skeleton.bones[indices[get](index)]) ? weights[get](index) : 0), 0) >= .35) handVertices[side].push({ mesh, index });
        for (const digit of digits) if (weighted(raw('right' + digit + 'Distal')) >= .35) tipVertices[digit].push({ mesh, index });
      }
    }
    for (const digit of digits) {
      const prefix = 'right' + digit, distal = normalized(prefix + 'Distal').getWorldPosition(new THREE.Vector3());
      const previous = normalized(prefix + (digit === 'Thumb' ? 'Proximal' : 'Intermediate')).getWorldPosition(new THREE.Vector3());
      const direction = distal.clone().sub(previous).normalize();
      tipVertices[digit].sort((a, b) => vertexWorld(b).sub(distal).dot(direction) - vertexWorld(a).sub(distal).dot(direction));
      tipVertices[digit] = tipVertices[digit].slice(0, Math.max(3, Math.ceil(tipVertices[digit].length * .12)));
    }
    h.geometry = { method: 'only unique POSITION indices actually referenced by geometry.index; tip subset is furthest 12% of >=.35 distal weights at captured bind',
      chestVertexCount: chestVertices.length, handVertexCounts: Object.fromEntries(['left', 'right'].map(side => [side, handVertices[side].length])),
      tipVertices: Object.fromEntries(digits.map(digit => [digit, tipVertices[digit].map(({ mesh, index }) => ({ mesh: mesh.name, index }))])) };
    h.refreshBounds = () => { for (const mesh of h.meshes) if (mesh.isSkinnedMesh) { mesh.computeBoundingSphere(); mesh.computeBoundingBox(); } };
    h.camera = { position: s.camera.position.clone(), quaternion: s.camera.quaternion.clone(), fov: s.camera.fov, near: s.camera.near, far: s.camera.far, aspect: s.camera.aspect };
    h.cameraCenter = s.framing.center.clone();
    h.restoreCamera = () => { s.renderer.setSize(600, 720, false); s.camera.position.copy(h.camera.position); s.camera.quaternion.copy(h.camera.quaternion); s.camera.fov = h.camera.fov; s.camera.near = h.camera.near; s.camera.far = h.camera.far; s.camera.aspect = h.camera.aspect; s.camera.updateProjectionMatrix(); };
    h.projectedHands = () => Object.fromEntries(['left', 'right'].map(side => {
      const points = handVertices[side].map(vertex => vertexWorld(vertex).project(s.camera));
      return [side, { vertices: points.length, outside: points.filter(point => Math.abs(point.x) > 1 || Math.abs(point.y) > 1 || point.z < -1 || point.z > 1).length,
        min: [Math.min(...points.map(p => p.x)), Math.min(...points.map(p => p.y)), Math.min(...points.map(p => p.z))],
        max: [Math.max(...points.map(p => p.x)), Math.max(...points.map(p => p.y)), Math.max(...points.map(p => p.z))] }];
    }));
    const world = node => node.getWorldPosition(new THREE.Vector3());
    h.measure = (frame, geometry = false) => {
      s.scene.updateMatrixWorld(true);
      const joints = Object.fromEntries(Object.entries(h.bind).map(([name, value]) => [name, {
        local: value.node.quaternion.toArray(), world: world(value.node).toArray(), worldQ: value.node.getWorldQuaternion(new THREE.Quaternion()).toArray(),
        rawLocal: value.raw?.quaternion.toArray(), rawWorld: value.raw ? world(value.raw).toArray() : null,
        restAngle: value.node.quaternion.angleTo(value.rest),
      }]));
      const head = normalized('head'), headQInverse = head.getWorldQuaternion(new THREE.Quaternion()).invert();
      const targetLocal = s.target.position.clone().sub(world(head)).applyQuaternion(headQInverse).normalize().toArray();
      const rightTips = Object.fromEntries(digits.map(digit => {
        const vertices = tipVertices[digit], point = new THREE.Vector3();
        for (const vertex of vertices) point.add(vertexWorld(vertex)); point.divideScalar(vertices.length);
        return [digit, { world: point.toArray(), vertices: vertices.length,
          nearestChestVertexDistance: geometry ? Math.min(...chestVertices.map(vertex => point.distanceTo(vertexWorld(vertex)))) : null }];
      }));
      const finite = Object.values(joints).every(joint => [...joint.local, ...joint.world, ...joint.worldQ, ...(joint.rawLocal || []), ...(joint.rawWorld || [])].every(Number.isFinite));
      return { at: h.clock.currentTime, frame, joints, rightTips, targetLocal, finite,
        capability: r.getBodyCoordination?.() ?? { available: false, enabled: false },
        camera: { position: s.camera.position.toArray(), quaternion: s.camera.quaternion.toArray(), fov: s.camera.fov, aspect: s.camera.aspect },
        posedBounds: h.meshes.filter(mesh => mesh.isSkinnedMesh).map(mesh => ({ name: mesh.name, sphere: mesh.boundingSphere ? { center: mesh.boundingSphere.center.toArray(), radius: mesh.boundingSphere.radius } : null })),
      };
    };
    h.image = (width = 600, height = 720) => {
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const c = canvas.getContext('2d'); c.fillStyle = '#e7eaf0'; c.fillRect(0, 0, width, height); c.drawImage(s.renderer.domElement, 0, 0, width, height);
      return canvas.toDataURL('image/jpeg', .9);
    };
    h.restFrame = () => ({ name: null, weight: 0, phase: 0, strength: 1, transition: { id: ++h.sequence, reason: 'start', from: 'rest', to: 'rest' } });
    h.reset = (enabled = true) => {
      a.performance.stop(); a.performance.sample(); h.clock.currentTime = 0;
      for (const value of Object.values(h.bind)) value.node.quaternion.copy(value.rest);
      for (const value of s.springBones) value.node.quaternion.copy(value.rest);
      r.poseTransition.reset(); r.setBodyCoordination?.(enabled); h.restoreCamera();
      const rest = h.restFrame(); for (let i = 0; i < 120; i++) { r.setPerformance(rest, false); h.quietDraw(h.neutral, 1/60); }
      r.poseTransition.reset();
    };
    h.render = (frame, dt, params = h.neutral, visible = true) => {
      r.setPerformance(frame, !!frame.reduced); h.quietDraw(params, dt);
      if (visible) { h.refreshBounds(); r.draw(params, 0); }
      return h.measure(frame);
    };
    h.fixed = ({ name = null, strength = 1, reduced = false, enabled = true, overlay = false }) => {
      h.reset(enabled);
      const frame = { name, strength, reduced, phase: .5, weight: name ? 1 : 0, transition: { id: ++h.sequence, reason: 'start', from: 'rest', to: name || 'rest' } };
      const params = overlay ? { ...h.neutral, ParamAngleX: .25, ParamAngleY: -.2, ParamAngleZ: .1, ParamBodyAngleX: .15,
        ParamBodyAngleZ: -.1, ParamBodyLeanX: .15, ParamEyeBallX: .35, ParamEyeBallY: -.25, ParamBreath: .6 } : h.neutral;
      r.setPerformance(frame, reduced); for (let i = 0; i < 120; i++) h.quietDraw(params, 1/60);
      h.refreshBounds(); r.draw(params, 0); const sample = h.measure(frame, true), image = h.image(), angledImages = {}, views = { front: h.projectedHands() };
      for (const sign of [-1, 1]) {
        s.renderer.setSize(840, 720, false); s.camera.aspect = 840/720;
        s.camera.position.copy(h.camera.position).sub(h.cameraCenter).multiplyScalar(1.15).applyAxisAngle(new THREE.Vector3(0, 1, 0), sign * 25*Math.PI/180).add(h.cameraCenter);
        s.camera.lookAt(h.cameraCenter); s.camera.updateProjectionMatrix(); h.refreshBounds(); r.draw(params, 0);
        angledImages[sign === -1 ? 'left25' : 'right25'] = h.image(840, 720);
        views[sign === -1 ? 'left25' : 'right25'] = h.projectedHands();
      }
      h.restoreCamera(); return { input: { name, strength, reduced, enabled, overlay }, sample, image, angledImages, views };
    };
    h.dynamic = ({ name, enabled = true }) => {
      h.reset(enabled); const frames = [], images = [];
      a.performance.preview(name === 'self-palm-rest' ? 'self' : 'wave', h.clock);
      const last = name === 'self-palm-rest' ? 288 : 180;
      for (let i = 0; i <= last; i++) {
        if (name === 'self-palm-rest') {
          h.clock.currentTime = i/60;
          if (i === 66) a.performance.preview('palm', h.clock);
        } else {
          h.clock.currentTime = Math.min(i, 60)/60;
          if (i === 60) a.performance.stop('interrupted');
        }
        const frame = a.performance.sample(); frames.push(h.render(frame, i ? 1/60 : 0, h.neutral, i % 3 === 0));
        if (i % 3 === 0) images.push(h.image());
      }
      return { name, enabled, frames, images };
    };
    h.toggle = () => {
      h.fixed({ name: 'palm' }); const before = h.measure(r.performance);
      r.setBodyCoordination(false); r.draw(h.neutral, 0); const disabled = h.measure(r.performance);
      r.setBodyCoordination(true); r.draw(h.neutral, 0); const enabled = h.measure(r.performance);
      return { before, disabled, enabled };
    };
  });
  const configurations = [{ name: null }];
  for (const name of ['wave', 'deny', 'palm', 'self']) configurations.push({ name }, { name, strength: .4 }, { name, reduced: true });
  if (focus) configurations.splice(0, configurations.length, { name: null }, { name: 'self' }, { name: 'palm' }, { name: 'wave' });
  configurations.push({ name: 'palm', overlay: true });
  for (const input of configurations) {
    const capture = await page.evaluate(input => window.bodyHarness.fixed(input), input);
    const stem = `${input.name || 'rest'}-strength${capture.input.strength}${input.reduced ? '-reduced' : ''}${input.overlay ? '-overlay' : ''}`;
    if (!obliqueOnly) await writeFile(`${out}/${stem}.jpg`, Buffer.from(capture.image.split(',')[1], 'base64')); delete capture.image;
    for (const [view, image] of Object.entries(capture.angledImages)) await writeFile(`${out}/${stem}-${view}.jpg`, Buffer.from(image.split(',')[1], 'base64'));
    delete capture.angledImages;
    assert(capture.sample.finite); captures.push({ stem, ...capture }); console.log(`${label}: ${stem}`);
    for (const view of ['left25', 'right25']) for (const side of ['left', 'right']) assert.equal(capture.views[view][side].outside, 0, `${stem}/${view}/${side}: fixed oblique camera contains actual hand-influenced vertices`);
  }
  if (obliqueOnly) {
    await writeFile(`${out}/oblique-camera-audit.json`, JSON.stringify({ createdAt: new Date().toISOString(), sourceHashes, distanceGain: 1.15, captures: captures.map(({ stem, input, views }) => ({ stem, input, views })) }, null, 2));
    console.log(`Body coordination ${label}: fixed oblique views repaired and actual hand projection passed`);
    await browser.close(); process.exit(0);
  }
  for (const name of ['self-palm-rest', 'wave-interrupt']) {
    const dynamic = await page.evaluate(name => window.bodyHarness.dynamic({ name }), name);
    assert(dynamic.frames.every(frame => frame.finite)); movies[name] = dynamic.images; delete dynamic.images; dynamics.push(dynamic);
  }
  await writeFile(`${out}/movies.json`, JSON.stringify(movies));
  checks.push(`${captures.length} fixed actual half-body renders and self→palm→rest / wave interruption with finite normalized/raw joints and matched posed bounds`);
  let comparisons, toggle, disabled, nativeLab;
  const modelHeight = await page.evaluate(() => window.bodyHarness.modelHeight);
  if (!before) {
    const baseline = JSON.parse(await readFile(`${root}/before/report.json`, 'utf8'));
    assert.equal(await hash('web/models/seed-daily.vrm'), baseline.assetHash);
    const unchanged = await page.evaluate(() => [...window.bodyHarness.armNames, ...window.bodyHarness.fingerNames, 'spine', 'neck', 'head']);
    comparisons = [];
    const compare = (sample, old, stem) => {
      for (const name of unchanged) assert(qAngle(sample.joints[name].local, old.joints[name].local) < 1e-6, `${stem}/${name}: existing local arm and finger animation preserved`);
      assert(distance(sample.camera.position, old.camera.position) < 1e-9 && qAngle(sample.camera.quaternion, old.camera.quaternion) < 1e-7, 'fixed matched half-body camera');
      assert(distance(sample.targetLocal, old.targetLocal) < 1e-6, 'ocular residual remains in animated head frame');
      const body = Object.fromEntries(['chest', 'rightShoulder'].filter(name => sample.joints[name]).map(name => [name, qAngle(sample.joints[name].local, old.joints[name].local)]));
      const maxWorldDelta = Math.max(...Object.keys(sample.joints).map(name => distance(sample.joints[name].world, old.joints[name].world)));
      const tipDeltas = Object.fromEntries(Object.keys(sample.rightTips).map(name => [name, distance(sample.rightTips[name].world, old.rightTips[name].world)]));
      assert(Object.values(body).every(angle => angle < .12), 'support rotations stay below seven degrees');
      assert(maxWorldDelta < modelHeight * .06, 'support does not displace any measured humanoid joint beyond 6% model height');
      assert(Object.values(tipDeltas).every(delta => delta < modelHeight * .06), 'actual skinned tips stay within limited body-support displacement');
      return { stem, body, maxWorldDelta, tipDeltas, headWorldAngle: qAngle(sample.joints.head.worldQ, old.joints.head.worldQ) };
    };
    for (const capture of captures) {
      const old = baseline.captures.find(value => value.stem === capture.stem); assert(old);
      comparisons.push(compare(capture.sample, old.sample, capture.stem));
    }
    for (const dynamic of dynamics) {
      const old = baseline.dynamics.find(value => value.name === dynamic.name); assert(old);
      dynamic.comparisons = dynamic.frames.map((frame, i) => compare(frame, old.frames[i], `${dynamic.name}/${i}`));
      for (let i = 1; i < dynamic.frames.length; i++) for (const name of ['chest', 'rightShoulder']) if (dynamic.frames[i].joints[name])
        assert(qAngle(dynamic.frames[i].joints[name].local, dynamic.frames[i-1].joints[name].local) < .025, `${dynamic.name}/${i}: body frame steps stay small`);
      const end = dynamic.frames.at(-1), rest = captures.find(value => !value.input.name).sample;
      for (const name of ['chest', 'rightShoulder']) if (end.joints[name]) assert(qAngle(end.joints[name].local, rest.joints[name].local) < .002, 'body recovers to rest');
      if (dynamic.name === 'wave-interrupt') assert(dynamic.frames.slice(60).every(frame => frame.at === 1), 'recovery proceeds on renderer dt despite frozen audio clock');
    }
    const rest = comparisons.find(value => value.stem.startsWith('rest-')); assert(Object.values(rest.body).every(angle => angle < 1e-6), 'neutral rest unchanged');
    for (const name of ['wave', 'deny', 'palm', 'self']) {
      const full = comparisons.find(value => value.stem === `${name}-strength1`);
      if (!full) continue;
      assert(Object.values(full.body).some(angle => angle > .003), `${name}: actual body support reaches model`);
      const soft = comparisons.find(value => value.stem === `${name}-strength0.4`), reduced = comparisons.find(value => value.stem === `${name}-strength1-reduced`);
      if (soft) assert(Object.values(soft.body).reduce((a,b) => a+b,0) < Object.values(full.body).reduce((a,b) => a+b,0), 'strength scales body support');
      if (reduced) assert(Object.values(reduced.body).reduce((a,b) => a+b,0) < Object.values(full.body).reduce((a,b) => a+b,0)*.6, 'reduced mode limits body support');
    }
    checks.push('local arm/finger animation and head-relative gaze unchanged; limited actual body/world support; strength/reduced scaling; rest and frozen-audio recovery');
    disabled = await page.evaluate(() => window.bodyHarness.fixed({ name: 'palm', enabled: false })); delete disabled.image; delete disabled.angledImages;
    const oldPalm = baseline.captures.find(value => value.stem === 'palm-strength1').sample;
    for (const name of ['chest', 'rightShoulder']) assert(qAngle(disabled.sample.joints[name].local, oldPalm.joints[name].local) < 1e-6, 'disable smoothly returns to original bind support');
    toggle = await page.evaluate(() => window.bodyHarness.toggle());
    for (const name of Object.keys(toggle.before.joints)) {
      assert.deepEqual(toggle.disabled.joints[name].local, toggle.before.joints[name].local, 'dt0 disable does not jump actual joints');
      assert.deepEqual(toggle.enabled.joints[name].local, toggle.disabled.joints[name].local, 'dt0 enable does not jump actual joints');
    }
    checks.push('body toggle preserves all actual joints at dt0 and disabling restores original support');
    if (!focus) {
      await page.evaluate(() => { const a = window.__avatar; a.onBargeIn(); a.svg.setBodyCoordination(true); if (!a._raf) a._start(); });
      assert(await page.locator('#bodyCoordination').isChecked()); assert.equal(await page.locator('#bodyCoordination').isDisabled(), false);
      await page.locator('#gestureWave').click();
      await page.waitForFunction(() => { const frame = window.__avatar.performance.sample(); return frame.name === 'wave' && frame.weight > .99; });
      const interruption = await page.evaluate(async () => {
        const a = window.__avatar, h = window.bodyHarness; await h.nativeCtx.suspend(); const before = h.measure(a.performance.sample()), at = h.nativeCtx.currentTime;
        a.onBargeIn(); a.svg.setPerformance(a.performance.sample(), false); a.svg.draw(h.neutral, 0);
        return { before, zero: h.measure(a.performance.sample()), at, queueLength: a.performance.queue.length };
      });
      for (const name of ['chest', 'rightShoulder']) assert.deepEqual(interruption.zero.joints[name].local, interruption.before.joints[name].local);
      assert.equal(interruption.queueLength, 0);
      await page.waitForFunction(() => { const a = window.__avatar, h = window.bodyHarness; const sample = h.measure(a.performance.sample()); return ['chest', 'rightShoulder'].every(name => sample.joints[name].restAngle < .002); });
      const recovery = await page.evaluate(() => ({ ...window.bodyHarness.measure(window.__avatar.performance.sample()), audioAt: window.bodyHarness.nativeCtx.currentTime }));
      assert.equal(recovery.audioAt, interruption.at);
      await page.locator('#bodyCoordination').uncheck(); assert.equal(await page.evaluate(() => window.__avatar.svg.getBodyCoordination().enabled), false);
      await page.locator('#bodyCoordination').check(); assert.equal(await page.evaluate(() => window.__avatar.svg.getBodyCoordination().enabled), true);
      await page.locator('#bodyCoordinationStatus').screenshot({ path: `${out}/lab-status.png` }); nativeLab = { interruption, recovery };
      checks.push('actual lab toggle and native AudioContext interruption recover while audio is suspended');
    }
  }
  assert.deepEqual(errors, []); assert.deepEqual(forbiddenRequests, []); assert.equal(await page.evaluate(() => window.bodyHarness.microphoneRequests), 0);
  checks.push('no browser errors, microphone, speech/model calls or API mutation');
  const report = { label, focus, createdAt: new Date().toISOString(), assetHash: await hash('web/models/seed-daily.vrm'), sourceHashes,
    geometry: await page.evaluate(() => window.bodyHarness.geometry),
    modelHeight, captures, dynamics, comparisons, toggle, disabled, nativeLab, checks, errors, forbiddenRequests,
    method: 'actual production VRM normalized/raw joints, fixed 600×720 half-body camera, fixed 840×720 ±25° views 15% farther away with actual hand-influenced indexed vertex projections; deterministic neutral and head/body/gaze/breath input overlay; actual skinned bounds refreshed before every screenshot; original production draw/VRM update at 60Hz, GPU submission skipped only during warm-up and uncaptured frames, captured dynamics rendered at 20fps; native-clock UI interruption',
    limits: 'Tests establish continuity and bounded motion, not anatomical or collision correctness. Fixed oblique views inspect collar/shoulder/self spacing. Nearest active indexed chest vertex distance is a geometry observation, not a contact solver. ParamBreath overlay verifies existing input behavior; no breathing implementation is inferred from the parameter.' };
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(`Body coordination ${label}${focus ? ' focused' : ''}: ${checks.length} groups passed; ${out}/report.json`);
} finally { await browser.close(); }
