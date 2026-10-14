// Real daily VRM + production director envelope, with a controlled audio clock
// and one physical renderer step per 1/60s. No LLM/TTS/microphone or API writes.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const baseline = process.argv.includes('--before');
const focus = process.argv.includes('--focus');
const root = 'eval/avatar/gesture-paths';
const requestedLabel = process.argv.find(value => value.startsWith('--label='))?.slice(8);
if (requestedLabel) assert(/^[a-z0-9-]+$/.test(requestedLabel), 'artifact label is a plain directory name');
const label = baseline ? 'before' : requestedLabel || 'after';
const out = `${root}/${label}`;
const hash = async path => createHash('sha256').update(await readFile(path)).digest('hex');
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(20000);
const errors = [], forbiddenRequests = [], checks = [], captures = [], movies = {};
page.on('pageerror', error => errors.push(error.message));
const qAngle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.reduce((sum, v, i) => sum + v * b[i], 0))));
const largest = (a, b) => Math.max(...Object.keys(a.quaternions).map(key => qAngle(a.quaternions[key], b.quaternions[key])));
try {
  await mkdir(out, { recursive: true });
  for (const [url, source] of [['vrm.js', 'renderer-source.js'], ['gesture-poses.js', 'gesture-poses.js']]) {
    const body = await readFile(baseline ? `${out}/${source}` : `web/avatar/render/${url}`, 'utf8');
    if (!baseline) await writeFile(`${out}/${source}`, body);
    // Immutable copies are the exact production sources at run start; this
    // keeps parallel authoring from changing the evidence during a long run.
    await page.route(`**/static/avatar/render/${url}`, route => route.fulfill({ contentType: 'text/javascript', body }));
  }
  await page.route('**/api/**', route => {
    if (route.request().method() !== 'GET') { forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`); return route.abort(); }
    return new URL(route.request().url()).pathname === '/api/avatar' ? route.continue() : route.fulfill({ json: { count: 0, items: [], context: '' } });
  });
  await page.addInitScript(() => {
    window.pathHarness = { microphoneRequests: 0 };
    const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
    window.AudioContext = class extends NativeAudioContext {
      constructor(...args) { super(...args); window.pathHarness.nativeCtx = this; }
    };
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
      window.pathHarness.microphoneRequests++;
      return Promise.reject(new Error('No microphone in gesture path QA'));
    };
  });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  await page.evaluate(async () => {
    const THREE = await import('three');
    const { DEFAULT_NEUTRAL } = await import('/static/avatar/params.js');
    const a = window.__avatar, r = a.svg, s = r._state, h = window.pathHarness;
    cancelAnimationFrame(a._raf); a._raf = 0; a.performance.stop(); a.lip.stop();
    r.setEyeFocus(false); r.setPresentation(false); r.setSecondaryMotion(0);
    h.neutral = { ...DEFAULT_NEUTRAL }; h.clock = { currentTime: 0 };
    const focus = s.framing.center.clone(), distance = s.camera.position.distanceTo(focus);
    const bone = name => s.vrm.humanoid.getNormalizedBoneNode(name);
    const pos = name => bone(name)?.getWorldPosition(new THREE.Vector3());
    const handVertices = [];
    const descendants = new Set(); s.vrm.humanoid.getRawBoneNode('rightHand')?.traverse(node => descendants.add(node));
    s.vrm.scene.traverse(mesh => {
      if (!mesh.isSkinnedMesh) return;
      const indices = mesh.geometry.getAttribute('skinIndex'), weights = mesh.geometry.getAttribute('skinWeight'), positions = mesh.geometry.getAttribute('position');
      if (!indices || !weights || !positions) return;
      const get = ['getX', 'getY', 'getZ', 'getW'];
      for (let i = 0; i < positions.count; i++) {
        const weight = get.reduce((sum, accessor) => sum + (descendants.has(mesh.skeleton.bones[indices[accessor](i)]) ? weights[accessor](i) : 0), 0);
        if (weight >= .5) handVertices.push({ mesh, index: i });
      }
    });
    const wrist = pos('rightHand'), hand = bone('rightHand');
    const inv = hand.getWorldQuaternion(new THREE.Quaternion()).invert();
    const points = ['ThumbMetacarpal', 'IndexProximal', 'LittleProximal'].map(name => pos(`right${name}`).sub(wrist).applyQuaternion(inv));
    const palmBase = points[1].clone().sub(points[0]).cross(points[2].clone().sub(points[0])).normalize();
    h.measure = (frame, clip = false) => {
      s.scene.updateMatrixWorld(true); s.camera.updateMatrixWorld(true);
      const shoulder = pos('rightUpperArm'), elbow = pos('rightLowerArm'), wrist = pos('rightHand');
      const handQ = hand.getWorldQuaternion(new THREE.Quaternion());
      const normal = palmBase.clone().applyQuaternion(handQ).normalize();
      const quaternions = Object.fromEntries(Object.entries(s.arms).map(([name, b]) => [name, b.node.quaternion.toArray()]));
      const restAngles = Object.fromEntries(Object.entries(s.arms).map(([name, b]) => [name, b.node.quaternion.angleTo(b.rest)]));
      const coordinates = point => { const ndc = point.clone().project(s.camera); return { ndc: ndc.toArray(), visible: Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1 && ndc.z >= -1 && ndc.z <= 1 }; };
      const value = { at: h.clock.currentTime, frame, quaternions, restAngles,
        wrist: wrist.toArray(), elbow: elbow.toArray(), shoulder: shoulder.toArray(),
        elbowAngleDeg: THREE.MathUtils.radToDeg(shoulder.clone().sub(elbow).angleTo(wrist.clone().sub(elbow))),
        palmPlaneNormal: normal.toArray(), palmFacingCamera: normal.dot(s.camera.position.clone().sub(wrist).normalize()),
        jointsInFrame: { wrist: coordinates(wrist), elbow: coordinates(elbow), shoulder: coordinates(shoulder) },
        transition: r.getPerformanceTransition(), paths: r.getGesturePaths?.() ?? null };
      value.finite = [...Object.values(quaternions).flat(), ...value.wrist, ...value.elbow, ...value.shoulder, value.elbowAngleDeg, ...value.palmPlaneNormal].every(Number.isFinite);
      if (clip) {
        value.meshClip = { vertexCount: handVertices.length, outsideCount: 0, finite: true };
        for (const { mesh, index } of handVertices) {
          const point = new THREE.Vector3().fromBufferAttribute(mesh.geometry.getAttribute('position'), index);
          mesh.applyBoneTransform(index, point); point.applyMatrix4(mesh.matrixWorld).project(s.camera);
          value.meshClip.finite &&= point.toArray().every(Number.isFinite);
          if (Math.abs(point.x) > 1 || Math.abs(point.y) > 1 || point.z < -1 || point.z > 1) value.meshClip.outsideCount++;
        }
      }
      return value;
    };
    h.image = () => {
      const canvas = document.createElement('canvas'); canvas.width = 420; canvas.height = 370;
      const c = canvas.getContext('2d'); c.fillStyle = '#e7eaf0'; c.fillRect(0, 0, 420, 370);
      c.drawImage(s.renderer.domElement, 0, 0, 420, 370);
      return canvas.toDataURL('image/jpeg', .80);
    };
    h.run = ({ name, strength = 1, reduced = false, view = 0, paths = true, movie = false }) => {
      a.performance.stop(); a.performance.sample();
      h.clock.currentTime = 0;
      for (const b of Object.values(s.arms)) b.node.quaternion.copy(b.rest);
      for (const b of s.springBones) b.node.quaternion.copy(b.rest);
      r.poseTransition.reset(); r.setGesturePaths?.(paths);
      const yaw = THREE.MathUtils.degToRad(view);
      s.camera.position.copy(focus).add(new THREE.Vector3(Math.sin(yaw) * distance, 0, Math.cos(yaw) * distance));
      s.camera.lookAt(focus); s.camera.updateProjectionMatrix();
      a.performance.preview(name, h.clock); a.performance.action.strength = strength;
      h.lastConfig = { name, strength, reduced, view, paths };
      const frames = [], images = [];
      const selected = new Set([0, 6, 12, 18, 30, 72, 126, 132, 138, 144, 168]);
      for (let i = 0; i <= 168; i++) {
        h.clock.currentTime = i / 60;
        const f = a.performance.sample(); r.setPerformance(f, reduced); r.draw(h.neutral, i ? 1 / 60 : 0);
        frames.push(h.measure(f, selected.has(i)));
        if (movie && i % 3 === 0) images.push(h.image());
      }
      return { input: h.lastConfig, frames, images };
    };
    h.interrupt = ({ name = 'self', paths = true } = {}) => {
      r.setGesturePaths?.(paths);
      a.performance.stop(); a.performance.sample(); h.clock.currentTime = 0;
      for (const b of Object.values(s.arms)) b.node.quaternion.copy(b.rest); r.poseTransition.reset();
      a.performance.preview(name, h.clock);
      for (let i = 0; i <= 18; i++) { h.clock.currentTime = i / 60; const f = a.performance.sample(); r.setPerformance(f, false); r.draw(h.neutral, i ? 1 / 60 : 0); }
      const before = h.measure(a.performance.sample()); a.onBargeIn();
      const f = a.performance.sample(); r.setPerformance(f, false); r.draw(h.neutral, 0);
      const atZero = h.measure(f), recovery = [];
      for (let i = 0; i < 90; i++) { r.setPerformance(a.performance.sample(), false); r.draw(h.neutral, 1 / 60); recovery.push(h.measure(a.performance.sample())); }
      return { before, atZero, recovery, queueLength: a.performance.queue.length };
    };
    h.toggle = () => {
      h.interrupt(); h.clock.currentTime = 0; a.performance.preview('self', h.clock);
      for (let i = 0; i <= 12; i++) { h.clock.currentTime = i / 60; const f = a.performance.sample(); r.setPerformance(f, false); r.draw(h.neutral, i ? 1 / 60 : 0); }
      const before = h.measure(a.performance.sample()); r.setGesturePaths(false); r.setPerformance(a.performance.sample(), false); r.draw(h.neutral, 0);
      const disabled = h.measure(a.performance.sample()); r.setGesturePaths(true); r.draw(h.neutral, 0);
      return { before, disabled, enabled: h.measure(a.performance.sample()) };
    };
  });
  const configurations = [];
  for (const name of ['wave', 'deny', 'self', 'palm']) {
    configurations.push({ name, strength: 1, movie: ['self', 'palm'].includes(name) }, { name, strength: .4 }, { name, reduced: true });
    if (['self', 'palm'].includes(name)) configurations.push({ name, view: -25 }, { name, view: 25 });
  }
  if (focus) configurations.splice(0, configurations.length,
    { name: 'self', strength: 1, movie: true }, { name: 'palm', strength: 1, movie: true }, { name: 'wave', reduced: true });
  for (const configuration of configurations) {
    const sequence = await page.evaluate(config => window.pathHarness.run(config), configuration);
    assert(sequence.frames.every(value => value.finite), `${configuration.name}: finite arm and palm transforms throughout actual envelope`);
    const maxStep = Math.max(...sequence.frames.slice(1).map((value, i) => largest(sequence.frames[i], value)));
    assert(maxStep < .65, `${configuration.name}: no unbounded per-frame angular step (${maxStep})`);
    assert(sequence.frames.every(value => value.elbowAngleDeg > 5 && value.elbowAngleDeg <= 180.001), `${configuration.name}: measured elbow does not hyperextend`);
    assert(Object.values(sequence.frames.at(-1).restAngles).every(angle => angle < .002), `${configuration.name}: physical recovery completes`);
    const checkpoints = sequence.frames.filter(value => value.meshClip);
    // In half-body framing the lowered rest hand can be cropped. Active strong
    // snapshots must contain the entire measured hand mesh, including fingers.
    for (const value of checkpoints.filter(value => value.frame.weight > .95)) {
      assert(value.meshClip.vertexCount > 0 && value.meshClip.finite && value.meshClip.outsideCount === 0, `${configuration.name}/${value.at}: active hand vertices remain in frame`);
    }
    if (sequence.images.length) movies[configuration.name] = sequence.images;
    delete sequence.images;
    const wristLength = sequence.frames.slice(1).reduce((sum, value, i) => sum + Math.hypot(...value.wrist.map((v, k) => v - sequence.frames[i].wrist[k])), 0);
    const maxPalmStep = Math.max(...sequence.frames.slice(1).map((value, i) => Math.acos(Math.min(1, Math.max(-1,
      value.palmPlaneNormal.reduce((sum, v, k) => sum + v * sequence.frames[i].palmPlaneNormal[k], 0))))));
    const maxWristSpeed = Math.max(...sequence.frames.slice(1).map((value, i) => Math.hypot(...value.wrist.map((v, k) => v - sequence.frames[i].wrist[k])) * 60));
    captures.push({ ...sequence, maxStep, maxPalmStep, maxWristSpeed, wristPathLength: wristLength });
    const peak = sequence.frames[72];
    console.log(`${label} ${configuration.name} strength ${sequence.input.strength} reduced ${sequence.input.reduced} view ${sequence.input.view}: max step ${maxStep.toFixed(4)}, path ${wristLength.toFixed(4)}, peak elbow ${peak.elbowAngleDeg.toFixed(1)}`);
  }
  checks.push(`${captures.length} real director/renderer sequences: finite transforms, bounded 60 Hz angular steps, elbow range, active hand clipping and final rest`);
  // Preserve expensive visual/measurement captures before later integration
  // assertions, so a stale UI or regression failure remains reviewable.
  await writeFile(`${out}/captures.json`, JSON.stringify({ label, captures }, null, 2));
  await writeFile(`${out}/movies.json`, JSON.stringify(movies));
  let comparison, interruption, toggle, nativeLab;
  if (!baseline) {
    const before = JSON.parse(await readFile(`${root}/before/report.json`, 'utf8'));
    assert.equal(await hash('web/models/seed-daily.vrm'), before.assetHash, 'model file unchanged');
    comparison = [];
    for (const sequence of captures) {
      const previous = before.captures.find(value => JSON.stringify(value.input) === JSON.stringify(sequence.input));
      assert(previous, 'every new configuration has production baseline');
      const peakDistance = largest(previous.frames[72], sequence.frames[72]);
      assert(peakDistance < .035, `${sequence.input.name}: real rendered peak remains the same within .035 rad (${peakDistance})`);
      assert(sequence.maxStep <= previous.maxStep * 1.25 + 1e-6,
        `${sequence.input.name}: preparation path does not add more than 25% to the largest real 60 Hz arm turn`);
      comparison.push({ input: sequence.input, peakDistance, oldWristPathLength: previous.wristPathLength, newWristPathLength: sequence.wristPathLength,
        oldMaxStep: previous.maxStep, newMaxStep: sequence.maxStep,
        oldMaxPalmStep: previous.maxPalmStep ?? null, newMaxPalmStep: sequence.maxPalmStep,
        oldMaxWristSpeed: previous.maxWristSpeed ?? null, newMaxWristSpeed: sequence.maxWristSpeed,
        checkpointWristDistances: [6, 12, 18, 126, 132, 138].map(i => ({ at: sequence.frames[i].at,
          distance: Math.hypot(...sequence.frames[i].wrist.map((v, k) => v - previous.frames[i].wrist[k])) })) });
    }
    checks.push(`daily model hash unchanged; ${captures.length} actual peak poses preserved; maximum 60 Hz bone step grows at most 25% against baseline`);
    if (!focus) {
    for (const name of ['wave', 'deny', 'self', 'palm']) {
      const old = await page.evaluate(name => window.pathHarness.run({ name, paths: false }), name);
      const previous = before.captures.find(value => value.input.name === name && value.input.strength === 1 && !value.input.reduced && value.input.view === 0);
      for (let i = 0; i < old.frames.length; i++) assert(largest(old.frames[i], previous.frames[i]) < 1e-6, `${name}: paths:false restores old production sequence at frame ${i}`);
    }
    checks.push('paths:false restores previous production animation sequence exactly for all four gestures');
    interruption = [];
    for (const name of ['self', 'palm', 'wave', 'deny']) {
      const value = await page.evaluate(name => window.pathHarness.interrupt({ name }), name);
      assert.deepEqual(value.atZero.quaternions, value.before.quaternions, `${name}: interruption captures the actual preparation pose at dt0`);
      assert.equal(value.atZero.frame.name, null); assert.equal(value.queueLength, 0);
      assert(value.recovery.every(frame => frame.finite && frame.at === value.before.at));
      assert(Object.values(value.recovery.at(-1).restAngles).every(angle => angle < .002));
      interruption.push({ name, ...value });
    }
    toggle = await page.evaluate(() => window.pathHarness.toggle());
    assert.deepEqual(toggle.disabled.quaternions, toggle.before.quaternions, 'disabling paths does not teleport at dt0');
    assert.deepEqual(toggle.enabled.quaternions, toggle.disabled.quaternions, 're-enabling paths does not teleport at dt0');
    checks.push('all four mid-preparation interruptions cancel cues and recover with frozen audio clock; mode toggles preserve actual dt0 pose');
    // Rejoin the production Avatar animation loop and use actual laboratory
    // controls with a native audio clock for the end-to-end cancellation check.
    await page.evaluate(() => {
      const a = window.__avatar, h = window.pathHarness; a.onBargeIn();
      a.svg.setGesturePaths(true); if (!a._raf) a._start();
      h.nativeSnapshot = () => {
        const s = a.svg._state, quaternions = Object.fromEntries(Object.entries(s.arms).map(([name, b]) => [name, b.node.quaternion.toArray()]));
        return { at: h.nativeCtx?.currentTime ?? null, frame: a.performance.sample(), paths: a.svg.getGesturePaths(),
          quaternions, restAngles: Object.fromEntries(Object.entries(s.arms).map(([name, b]) => [name, b.node.quaternion.angleTo(b.rest)])) };
      };
    });
    assert(await page.locator('#gesturePaths').isChecked(), 'authored path checkbox defaults enabled');
    assert.equal(await page.locator('#gesturePaths').isDisabled(), false, 'daily authored path control is usable');
    await page.locator('#gestureSelf').click();
    await page.waitForFunction(() => {
      const f = window.__avatar.performance.sample(); return f.name === 'self' && f.weight > .5 && f.phase < .3;
    });
    const stopped = await page.evaluate(async () => {
      const a = window.__avatar, h = window.pathHarness;
      await h.nativeCtx.suspend();
      const before = h.nativeSnapshot(); a.onBargeIn();
      a.svg.setPerformance(a.performance.sample(), false); a.svg.draw(h.neutral, 0);
      return { before, atZero: h.nativeSnapshot(), queueLength: a.performance.queue.length };
    });
    assert.deepEqual(stopped.atZero.quaternions, stopped.before.quaternions, 'native lab preparation interrupt at dt0 preserves actual pose');
    assert.equal(stopped.atZero.frame.name, null); assert.equal(stopped.queueLength, 0);
    await page.waitForFunction(() => Object.values(window.pathHarness.nativeSnapshot().restAngles).every(angle => angle < .002));
    const nativeRecovery = await page.evaluate(() => window.pathHarness.nativeSnapshot());
    assert.equal(nativeRecovery.at, stopped.before.at, 'real laboratory recovery continues with suspended native audio clock');
    assert.equal(await page.evaluate(() => window.pathHarness.nativeCtx.state), 'suspended');
    await page.locator('#gesturePaths').uncheck();
    await page.locator('#gesturePalm').click();
    await page.waitForFunction(() => { const f = window.__avatar.performance.sample(); return f.name === 'palm' && f.weight > .99; });
    const disabled = await page.evaluate(() => window.pathHarness.nativeSnapshot());
    assert.equal(disabled.paths.enabled, false);
    await page.locator('#gesturePaths').check();
    const enabled = await page.evaluate(() => window.pathHarness.nativeSnapshot());
    assert.equal(enabled.paths.enabled, true);
    nativeLab = { stopped, recovery: nativeRecovery, disabled, enabled };
    await page.locator('#gesturePathStatus').screenshot({ path: `${out}/lab-path-status.png` });
    checks.push('actual laboratory checkbox and manual gesture controls use native AudioContext; preparation interrupt returns to rest while that clock is suspended');
    }
  }
  assert.deepEqual(errors, []); assert.deepEqual(forbiddenRequests, []); assert.equal(await page.evaluate(() => window.pathHarness.microphoneRequests), 0);
  checks.push('no browser errors, microphone or API mutations');
  const report = { label, createdAt: new Date().toISOString(), assetHash: await hash('web/models/seed-daily.vrm'),
    rendererHash: await hash(`${out}/renderer-source.js`),
    profileHash: await hash(`${out}/gesture-poses.js`),
    method: 'production PerformanceDirector.preview, controlled audio currentTime; renderer dt 1/60; real normalized arm quaternions, geometric elbow, finger-joint palm plane; full weighted hand vertices at checkpoints; fixed half-body camera; neutral idle/secondary motion',
    captures, comparison, interruption, toggle, nativeLab, checks, errors, forbiddenRequests,
    limits: 'Bone/palm/path measurements establish finite continuity and camera safety, not clothing collision or anatomical naturalness. Mesh palm side still needs image inspection. No IK, velocity-continuity proof, new assets, or finger posing tested.' };
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  await writeFile(`${out}/movies.json`, JSON.stringify(movies));
  if (!baseline) {
    const beforeMovies = JSON.parse(await readFile(`${root}/before/movies.json`, 'utf8'));
    const data = { before: beforeMovies, after: movies };
    const html = `<!doctype html><meta charset="utf-8"><title>日常角色手势路径对照</title><style>body{margin:0;background:#151b26;color:#edf0f4;font:17px system-ui;padding:24px}h1{font-size:24px;margin:0 0 12px}p{color:#bfc9d8;max-width:880px;line-height:1.6}.controls{display:flex;gap:16px;align-items:center;margin:18px 0}button,select{font:inherit;border:1px solid #617083;background:#283548;color:white;border-radius:8px;padding:8px 14px}.grid{display:flex;gap:20px}figure{margin:0}img{display:block;width:420px;height:370px;background:#e7eaf0;border-radius:12px}figcaption{padding:10px 0}.time{color:#90d7c8}</style><h1>手势准备与收回 · 实际渲染对照</h1><p>同一模型、终点姿势、台词动作时窗、固定半身镜头。每条片段由生产动作导演的音频时间采样与 60 Hz 渲染生成，20 fps 展示。可暂停或拖动查看肘部先后顺序与掌心转向。</p><div class="controls"><select id="gesture"><option value="self">自我强调</option><option value="palm">摊掌</option></select><button id="play">暂停</button><input id="seek" type="range" min="0" max="56" value="0"><span class="time" id="time"></span></div><div class="grid"><figure><img id="before"><figcaption>优化前 · 同时从休息姿势缩放</figcaption></figure><figure><img id="after"><figcaption>这一步 · 分段准备与收回</figcaption></figure></div><p>这一步只调整抬手与收回的路径，峰值姿势保留。袖口权重、手指与身体协同仍需继续处理。</p><script>const clips=${JSON.stringify(data)};let playing=true,index=0,last=0;const g=document.getElementById('gesture'),p=document.getElementById('play'),seek=document.getElementById('seek');function show(){for(const side of ['before','after'])document.getElementById(side).src=clips[side][g.value][index];seek.value=index;document.getElementById('time').textContent=(index*.05).toFixed(2)+' 秒'}g.onchange=()=>{index=0;show()};p.onclick=()=>{playing=!playing;p.textContent=playing?'暂停':'播放'};seek.oninput=()=>{index=Number(seek.value);playing=false;p.textContent='播放';show()};function tick(at){if(playing&&at-last>=50){index=(index+1)%57;last=at;show()}requestAnimationFrame(tick)}show();requestAnimationFrame(tick);</script>`;
    await writeFile(`${root}/comparison.html`, html);
    const preview = await browser.newPage({ viewport: { width: 930, height: 650 } });
    await preview.setContent(html);
    await preview.locator('#play').click(); await preview.locator('#seek').fill('4');
    await preview.waitForFunction(() => [...document.images].every(img => img.complete && img.naturalWidth));
    await preview.screenshot({ path: `${root}/comparison-preparation.png`, fullPage: true });
    await preview.close();
  }
  console.log(`Gesture paths browser ${label}${focus ? ' (focused visual trial)' : ''}: ${checks.length} groups passed; ${out}/report.json`);
} finally { await browser.close(); }
