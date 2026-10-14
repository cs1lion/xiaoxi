// Deterministic real daily VRM pose evidence. Never invokes LLM, TTS or capture APIs.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { chromium } from '@playwright/test';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const label = process.argv.includes('--baseline') ? 'baseline' : 'calibrated';
const out = `eval/avatar/gesture-calibration/${label}`;
if (process.argv.length === 2 && !existsSync('eval/avatar/gesture-calibration/baseline/report.json')) {
  console.log('SKIP gesture-calibration: 缺少 baseline 夹具（本地证据未入 git；先跑一次 --baseline 捕获后恢复）');
  process.exit(0);
}
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(20000);
const errors = [], forbiddenRequests = [], captures = [], checks = [];
page.on('pageerror', e => errors.push(e.message));
const hash = async path => createHash('sha256').update(await readFile(path)).digest('hex');
const assetHash = await hash('web/models/seed-daily.vrm');
let rendererSource = await readFile('web/avatar/render/vrm.js', 'utf8');
if (label === 'baseline') {
  try { rendererSource = await readFile(`${out}/renderer-source.js`, 'utf8'); } catch { /* first baseline capture */ }
}
const rendererHash = createHash('sha256').update(rendererSource).digest('hex');
let profileHash = null;
if (rendererSource.includes('./gesture-poses.js')) {
  try { profileHash = await hash('web/avatar/render/gesture-poses.js'); } catch { /* original renderer has no gesture profile */ }
}

try {
  await mkdir(out, { recursive: true });
  if (label === 'baseline') {
    await writeFile(`${out}/renderer-source.js`, rendererSource);
    await page.route('**/static/avatar/render/vrm.js', route => route.fulfill({ contentType: 'text/javascript', body: rendererSource }));
  }
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') {
      forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`);
      return route.abort();
    }
    return new URL(route.request().url()).pathname === '/api/avatar' ? route.continue()
      : route.fulfill({ json: { count: 0, items: [], context: '' } });
  });
  await page.addInitScript(() => {
    window.gestureHarness = { microphoneRequests: 0 };
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
      window.gestureHarness.microphoneRequests++;
      return Promise.reject(new Error('No microphone access in pose calibration QA'));
    };
  });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  await page.evaluate(async () => {
    const THREE = await import('three');
    const { DEFAULT_NEUTRAL } = await import('/static/avatar/params.js');
    const a = window.__avatar, r = a.svg, s = r._state, h = window.gestureHarness;
    cancelAnimationFrame(a._raf); a._raf = 0;
    a.performance.stop(); a.lip.stop();
    r.setEyeFocus(false); r.setPresentation(false); r.setSecondaryMotion(0);
    const neutral = { ...DEFAULT_NEUTRAL };
    const focus = s.framing.center.clone();
    const cameraDistance = s.camera.position.distanceTo(focus);
    h.sequence = 0;
    const bone = name => s.vrm.humanoid.getNormalizedBoneNode(name);
    const pos = name => bone(name)?.getWorldPosition(new THREE.Vector3());
    // Inspect actual skinned hand vertices, rather than treating an in-frame
    // wrist as proof that all fingertips are in frame.
    const handVertices = { left: [], right: [] };
    for (const side of ['left', 'right']) {
      const descendants = new Set();
      s.vrm.humanoid.getRawBoneNode(`${side}Hand`)?.traverse(node => descendants.add(node));
      s.vrm.scene.traverse(mesh => {
        if (!mesh.isSkinnedMesh) return;
        const indices = mesh.geometry.getAttribute('skinIndex'), weights = mesh.geometry.getAttribute('skinWeight');
        const positions = mesh.geometry.getAttribute('position');
        if (!indices || !weights || !positions) return;
        const get = ['getX', 'getY', 'getZ', 'getW'];
        for (let i = 0; i < positions.count; i++) {
          const handWeight = get.reduce((sum, accessor) => sum + (descendants.has(mesh.skeleton.bones[indices[accessor](i)]) ? weights[accessor](i) : 0), 0);
          if (handWeight >= .5) handVertices[side].push({ mesh, index: i });
        }
      });
    }
    h.palmBases = {};
    for (const side of ['left', 'right']) {
      const hand = bone(`${side}Hand`);
      const names = ['ThumbMetacarpal', 'IndexProximal', 'LittleProximal'];
      const points = names.map(suffix => pos(side + suffix));
      if (points.every(Boolean)) {
        const inverse = hand.getWorldQuaternion(new THREE.Quaternion()).invert();
        const wrist = hand.getWorldPosition(new THREE.Vector3());
        const local = points.map(v => v.clone().sub(wrist).applyQuaternion(inverse));
        // This oriented triangle convention comes from actual thumb/index/little
        // joint geometry, not an assumed +/- local axis. Plane polarity alone
        // cannot prove which mesh side is palmar: inspect the saved hand image.
        const normal = local[1].clone().sub(local[0]).cross(local[2].clone().sub(local[0])).normalize();
        if (side === 'left') normal.negate();
        h.palmBases[side] = { localNormal: normal, points: Object.fromEntries(names.map((name, i) => [name, local[i].toArray()])), method: 'thumb→index × thumb→little; left side polarity reversed; mesh polarity requires visual verification' };
      }
    }
    h.measure = () => {
      s.scene.updateMatrixWorld(true); s.camera.updateMatrixWorld(true);
      const joints = {};
      for (const side of ['left', 'right']) {
        const shoulder = pos(`${side}UpperArm`), elbow = pos(`${side}LowerArm`), wrist = pos(`${side}Hand`);
        const elbowAngle = shoulder.clone().sub(elbow).angleTo(wrist.clone().sub(elbow));
        const handQ = bone(`${side}Hand`).getWorldQuaternion(new THREE.Quaternion());
        const base = h.palmBases[side];
        const normal = base?.localNormal.clone().applyQuaternion(handQ).normalize();
        const view = s.camera.position.clone().sub(wrist).normalize();
        const meshClip = { vertexCount: handVertices[side].length, outsideCount: 0, ndcMin: [Infinity, Infinity, Infinity], ndcMax: [-Infinity, -Infinity, -Infinity], finite: true, method: 'actual skinned vertices with >=.5 aggregate weight on raw hand and finger descendants' };
        for (const { mesh, index } of handVertices[side]) {
          const point = new THREE.Vector3().fromBufferAttribute(mesh.geometry.getAttribute('position'), index);
          mesh.applyBoneTransform(index, point); point.applyMatrix4(mesh.matrixWorld).project(s.camera);
          const coordinates = point.toArray();
          meshClip.finite &&= coordinates.every(Number.isFinite);
          coordinates.forEach((n, i) => { meshClip.ndcMin[i] = Math.min(meshClip.ndcMin[i], n); meshClip.ndcMax[i] = Math.max(meshClip.ndcMax[i], n); });
          if (Math.abs(point.x) > 1 || Math.abs(point.y) > 1 || point.z < -1 || point.z > 1) meshClip.outsideCount++;
        }
        joints[side] = { shoulder: shoulder.toArray(), elbow: elbow.toArray(), wrist: wrist.toArray(), elbowAngle, elbowAngleDeg: THREE.MathUtils.radToDeg(elbowAngle), handWorldQuaternion: handQ.toArray(), palmPlaneNormal: normal?.toArray(), palmFacingCamera: normal?.dot(view),
          meshClip,
          clip: Object.fromEntries([['shoulder', shoulder], ['elbow', elbow], ['wrist', wrist]].map(([name, point]) => {
            const ndc = point.clone().project(s.camera);
            return [name, { ndc: ndc.toArray(), visible: Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1 && ndc.z >= -1 && ndc.z <= 1 }];
          })) };
      }
      const quaternions = Object.fromEntries(Object.entries(s.arms).map(([name, value]) => [name, value.node.quaternion.toArray()]));
      const restAngles = Object.fromEntries(Object.entries(s.arms).map(([name, value]) => [name, value.node.quaternion.angleTo(value.rest)]));
      return { joints, quaternions, restAngles, finite: Object.values(quaternions).flat().every(Number.isFinite) && Object.values(joints).every(v => v.meshClip.finite && [...v.shoulder, ...v.elbow, ...v.wrist, v.elbowAngle, ...v.handWorldQuaternion, ...(v.palmPlaneNormal || [])].every(Number.isFinite)), transition: r.getPerformanceTransition(), calibration: r.getGestureCalibration?.() ?? null, camera: { position: s.camera.position.toArray(), quaternion: s.camera.quaternion.toArray(), fov: s.camera.fov, near: s.camera.near, far: s.camera.far, aspect: s.camera.aspect } };
    };
    h.fixed = ({ name, strength = 1, phase = .5, weight = 1, reduced = false, view = 0 }) => {
      for (const b of Object.values(s.arms)) b.node.quaternion.copy(b.rest);
      for (const b of s.springBones) b.node.quaternion.copy(b.rest);
      r.poseTransition.reset();
      const frame = { name, strength, phase, weight, transition: { id: ++h.sequence, reason: 'start', from: 'rest', to: name } };
      r.setPerformance(frame, reduced);
      const yaw = THREE.MathUtils.degToRad(view);
      s.camera.position.copy(focus).add(new THREE.Vector3(Math.sin(yaw) * cameraDistance, 0, Math.cos(yaw) * cameraDistance));
      s.camera.lookAt(focus); s.camera.updateMatrixWorld(true); s.camera.updateProjectionMatrix();
      for (let i = 0; i < 12; i++) r.draw(neutral, .05);
      h.input = { name, strength, phase, weight, reduced, view, warmupFrames: 12, dt: .05 };
      return { input: h.input, ...h.measure() };
    };
    h.interrupt = () => {
      const before = h.measure();
      // Production interruption cancellation, followed by the production renderer.
      a.performance.preview('wave', { currentTime: 0 }); a.performance.sample();
      a.onBargeIn();
      const frame = a.performance.sample();
      r.setPerformance(frame, false); r.draw(neutral, 0);
      const atZero = h.measure(), steps = [];
      for (let i = 0; i < 90; i++) { r.draw(neutral, 1 / 60); if ([0, 8, 20, 89].includes(i)) steps.push({ frame: i + 1, ...h.measure() }); }
      return { before, atZero, steps, performance: frame, queueLength: a.performance.queue.length };
    };
    h.bases = Object.fromEntries(Object.entries(h.palmBases).map(([side, base]) => [side, { ...base, localNormal: base.localNormal.toArray() }]));
    document.querySelector('.badge').textContent = 'FIXED POSE / DAILY VRM';
    document.querySelector('#calibrationRegions').setAttribute('hidden', '');
  });
  const configurations = [];
  for (const name of ['wave', 'deny', 'self', 'palm']) {
    for (const view of [0, -25, 25]) configurations.push({ name, view });
    configurations.push({ name, strength: .4, view: 0 }, { name, reduced: true, view: 0 });
    if (label !== 'baseline') {
      configurations.push({ name, strength: .75, view: 0 });
      for (const phase of [.25, .75]) configurations.push({ name, phase, view: 0 });
      configurations.push({ name, weight: .5, view: 0 });
      if (['self', 'palm'].includes(name)) {
        for (const view of [-25, 25]) configurations.push({ name, weight: .5, view });
        for (const weight of [.25, .75]) configurations.push({ name, weight, view: 0 });
      }
    }
  }
  for (const config of configurations) {
    const sample = await page.evaluate(config => window.gestureHarness.fixed(config), config);
    assert(sample.finite, `${config.name}: every measured transform is finite`);
    assert(sample.joints.right.elbowAngleDeg > 20 && sample.joints.right.elbowAngleDeg < 181, `${config.name}: elbow does not hyperextend`);
    const stem = `${config.name}-strength${sample.input.strength}-phase${sample.input.phase}-${sample.input.weight === 1 ? '' : `weight${sample.input.weight}-`}${config.reduced ? 'reduced-' : ''}view${sample.input.view}`;
    const filename = `${stem}.png`;
    await page.evaluate(text => { document.querySelector('#caption').textContent = text; }, `${label} · ${config.name} · strength ${sample.input.strength} · weight ${sample.input.weight} · phase ${sample.input.phase} · ${sample.input.reduced ? 'reduced' : 'normal'} · view ${sample.input.view}°`);
    await page.locator('#viewport').screenshot({ path: `${out}/${filename}` });
    const repeat = await page.evaluate(config => window.gestureHarness.fixed(config), config);
    assert.deepEqual(repeat.quaternions, sample.quaternions, 'repeated fixed pose is deterministic');
    captures.push({ file: filename, ...sample });
  }
  await writeFile(`${out}/pose-metadata.json`, JSON.stringify({ label, assetHash, rendererHash, profileHash, captures }, null, 2));
  assert(captures.every(s => s.joints.right.palmPlaneNormal?.length === 3), 'right palm plane derives from existing finger joint geometry');
  checks.push(`${captures.length} fixed daily VRM poses have finite transforms, measured joints, palm planes and clipping; repeated quaternions exactly match`);
  let interruption, originalProfileComparison;
  if (label !== 'baseline') {
    assert(captures.every(s => s.calibration?.available && s.calibration.enabled), 'Daily model calibration is available and enabled by default');
    for (const sample of captures) {
      const inputLabel = `${sample.input.name} strength ${sample.input.strength} weight ${sample.input.weight} phase ${sample.input.phase} view ${sample.input.view}`;
      assert(Object.values(sample.joints.right.clip).every(p => p.visible), `${inputLabel}: performing shoulder, elbow and wrist remain inside camera`);
      assert(sample.joints.right.meshClip.vertexCount > 0, 'real hand vertices are measurable');
      assert.equal(sample.joints.right.meshClip.outsideCount, 0, `${inputLabel}: all measured hand/finger mesh vertices remain in camera`);
    }
    const baseline = JSON.parse(await readFile('eval/avatar/gesture-calibration/baseline/report.json', 'utf8'));
    assert.equal(assetHash, baseline.assetHash, 'calibration does not change the model asset');
    originalProfileComparison = [];
    await page.evaluate(() => window.__avatar.svg.setGestureCalibration(false));
    for (const name of ['wave', 'deny', 'self', 'palm']) {
      const sample = await page.evaluate(name => window.gestureHarness.fixed({ name }), name);
      const original = baseline.captures.find(s => s.input.name === name && s.input.view === 0 && s.input.strength === 1 && !s.input.reduced && s.input.phase === .5);
      assert.equal(sample.calibration.enabled, false);
      for (const key of Object.keys(sample.quaternions)) {
        const a = sample.quaternions[key], b = original.quaternions[key];
        const distance = 2 * Math.acos(Math.min(1, Math.abs(a.reduce((sum, value, i) => sum + value * b[i], 0))));
        assert(distance < 1e-6, `${name}/${key}: disabling calibration preserves the old target exactly`);
      }
      originalProfileComparison.push({ name, ...sample });
    }
    await page.evaluate(() => window.__avatar.svg.setGestureCalibration(true));
    checks.push('Daily calibration defaults enabled; disabling it restores original four targets; model asset hash is unchanged; performing arm joints stay in frame');
    await page.evaluate(() => window.gestureHarness.fixed({ name: 'wave' }));
    interruption = await page.evaluate(() => window.gestureHarness.interrupt());
    assert.deepEqual(interruption.atZero.quaternions, interruption.before.quaternions, 'interrupt draw(dt0) starts from actual pose');
    assert.equal(interruption.queueLength, 0);
    assert.equal(interruption.performance.name, null);
    assert(Object.values(interruption.steps.at(-1).restAngles).every(a => a < .002), 'physical recovery reaches neutral arms');
    assert(interruption.steps.every(s => s.finite));
    await page.locator('#viewport').screenshot({ path: `${out}/interrupted-rest.png` });
    checks.push('production interruption cancels gesture/queue immediately and recovers from actual pose to rest');
  }
  assert.deepEqual(errors, []); assert.deepEqual(forbiddenRequests, []);
  assert.equal(await page.evaluate(() => window.gestureHarness.microphoneRequests), 0);
  checks.push('no browser errors, microphone capture or mutating API requests');
  const bases = await page.evaluate(() => window.gestureHarness.bases);
  const report = { label, asset: 'web/models/seed-daily.vrm', assetHash, rendererHash, profileHash, origin, createdAt: new Date().toISOString(), neutral: 'DEFAULT_NEUTRAL; idle loop frozen; eye focus off; secondary motion disabled', bases, captures, originalProfileComparison, interruption, checks, errors, forbiddenRequests };
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  const cards = captures.filter(s => s.input.strength === 1 && s.input.weight === 1 && !s.input.reduced && s.input.phase === .5);
  const html = `<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#111821;color:#e5edf5;font:17px sans-serif}.grid{display:grid;grid-template-columns:repeat(3,320px);gap:10px;padding:20px}figure{margin:0}img{width:320px;height:298px;object-fit:contain}figcaption{padding:5px}</style><div class="grid">${cards.map(s => `<figure><img src="data:image/png;base64,${''}"><figcaption>${s.input.name} · ${s.input.view}°</figcaption></figure>`).join('')}</div>`;
  const sheet = await browser.newPage({ viewport: { width: 1000, height: 1320 } });
  await sheet.setContent(html);
  const images = await Promise.all(cards.map(s => readFile(`${out}/${s.file}`)));
  await sheet.evaluate(data => { document.querySelectorAll('img').forEach((img, i) => img.src = `data:image/png;base64,${data[i]}`); }, images.map(b => b.toString('base64')));
  await sheet.waitForFunction(() => [...document.images].every(i => i.complete && i.naturalWidth));
  await sheet.screenshot({ path: `${out}/contact-sheet.png`, fullPage: true });
  await sheet.close();
  if (label !== 'baseline') {
    const baseline = JSON.parse(await readFile('eval/avatar/gesture-calibration/baseline/report.json', 'utf8'));
    const fronts = list => ['wave', 'deny', 'self', 'palm'].map(name => list.find(s => s.input.name === name && s.input.view === 0 && s.input.strength === 1 && s.input.weight === 1 && !s.input.reduced && s.input.phase === .5));
    const before = fronts(baseline.captures), after = fronts(captures);
    const comparison = await browser.newPage({ viewport: { width: 1440, height: 710 } });
    await comparison.setContent(`<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#111821;color:#e5edf5;font:17px sans-serif}.grid{display:grid;grid-template-columns:repeat(4,340px);gap:10px;padding:20px}figure{margin:0}img{width:340px;height:302px;object-fit:contain}figcaption{padding:4px}</style><div class="grid">${[...before, ...after].map((s, i) => `<figure><img><figcaption>${i < 4 ? '修改前' : '校准后'} · ${s.input.name}</figcaption></figure>`).join('')}</div>`);
    const images = await Promise.all([...before.map(s => `eval/avatar/gesture-calibration/baseline/${s.file}`), ...after.map(s => `${out}/${s.file}`)].map(path => readFile(path)));
    await comparison.evaluate(data => { document.querySelectorAll('img').forEach((img, i) => img.src = `data:image/png;base64,${data[i]}`); }, images.map(b => b.toString('base64')));
    await comparison.waitForFunction(() => [...document.images].every(i => i.complete && i.naturalWidth));
    await comparison.screenshot({ path: 'eval/avatar/gesture-calibration/comparison.png', fullPage: true });
    await comparison.close();
  }
  console.log(JSON.stringify({ label, out, assetHash, rendererHash, captures: captures.length, checks }, null, 2));
} finally {
  await browser.close();
}
