// Native AudioContext + real daily VRM. Controlled render steps supplement the
// actual laboratory flow so replacement continuity can be measured at dt=0.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const out = 'eval/avatar/pose-transitions';
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(15000);
const checks = [], errors = [], forbiddenRequests = [], flows = {};
page.on('pageerror', error => errors.push(error.message));
const passed = name => checks.push(name);
const angle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.reduce((sum, value, i) => sum + value * b[i], 0))));
const largestStep = (a, b) => Math.max(...Object.keys(a.quaternions).map(name => angle(a.quaternions[name], b.quaternions[name])));
const snapshot = () => page.evaluate(() => window.poseHarness.snapshot());
const capture = async filename => {
  await page.evaluate(async () => {
    await window.poseHarness.ctx.suspend();
    await new Promise(resolve => {
      let count = 0;
      const tick = () => ++count === 12 ? resolve() : requestAnimationFrame(tick);
      requestAnimationFrame(tick);
    });
  });
  const before = await snapshot();
  await page.locator('#viewport').screenshot({ path: `${out}/${filename}.png` });
  const after = await snapshot();
  assert.equal(after.at, before.at, 'capture keeps native audio clock frozen');
  assert.equal(after.performance.phase, before.performance.phase, 'gesture phase follows audio clock during renderer/capture work');
  await page.evaluate(() => window.poseHarness.ctx.resume());
  return after;
};

try {
  await mkdir(out, { recursive: true });
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') {
      forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`);
      return route.abort();
    }
    return new URL(route.request().url()).pathname === '/api/avatar' ? route.continue()
      : route.fulfill({ json: { count: 0, items: [], context: '' } });
  });
  await page.addInitScript(() => {
    const h = window.poseHarness = { microphoneRequests: 0, trace: [], observing: false };
    const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
    window.AudioContext = class extends NativeAudioContext {
      constructor(...args) { super(...args); h.ctx = this; }
    };
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
      h.microphoneRequests++;
      return Promise.reject(new Error('No microphone access in offline pose QA'));
    };
  });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  await page.evaluate(() => {
    const a = window.__avatar, h = window.poseHarness;
    h.snapshot = () => {
      const state = a.svg._state;
      const quaternions = Object.fromEntries(Object.entries(state.arms).map(([name, bone]) => [name, bone.node.quaternion.toArray()]));
      const angles = Object.fromEntries(Object.entries(state.arms).map(([name, bone]) => [name, bone.node.quaternion.angleTo(bone.rest)]));
      return { quaternions, angles, performance: a.performance.sample(), transition: a.svg.getPerformanceTransition(),
        at: h.ctx?.currentTime || 0, finite: Object.values(quaternions).flat().every(Number.isFinite) };
    };
    h.stopRender = () => { cancelAnimationFrame(a._raf); a._raf = 0; };
    h.startRender = () => { if (!a._raf) a._start(); };
    h.draw = (frame, dt) => { a.svg.setPerformance(frame, a.reducedMotion); a.svg.draw(a.store.current, dt); return h.snapshot(); };
    h.preview = name => { a.performance.preview(name, h.ctx); return a.performance.sample(); };
    const watch = () => {
      if (h.observing) {
        const sample = h.snapshot();
        if (!h.trace.length || h.trace.at(-1).performance.name !== sample.performance.name) h.trace.push(sample);
      }
      requestAnimationFrame(watch);
    };
    watch();
  });
  assert(await page.locator('#poseTransitions').isChecked(), 'transition control defaults enabled');
  assert.equal(await page.locator('#poseTransitions').isDisabled(), false);
  await page.locator('#eyeFocus').uncheck();
  await page.locator('#gestureSelf').click();
  await page.waitForFunction(() => {
    const value = window.poseHarness.snapshot();
    return value.performance.name === 'self' && value.performance.weight > .99 && value.angles.rightLowerArm > .9;
  });
  await page.evaluate(async () => { await window.poseHarness.ctx.suspend(); window.poseHarness.stopRender(); });
  const replacement = await page.evaluate(() => {
    const h = window.poseHarness, before = h.snapshot(), frame = h.preview('palm');
    const atZero = h.draw(frame, 0), afterStep = h.draw(frame, .016);
    return { before, atZero, afterStep };
  });
  assert.deepEqual(replacement.atZero.quaternions, replacement.before.quaternions, 'replacement at dt0 preserves every actual arm quaternion exactly');
  assert.equal(replacement.atZero.transition.from, 'self');
  assert.equal(replacement.atZero.transition.to, 'palm');
  assert.equal(replacement.atZero.transition.reason, 'replace');
  assert.equal(replacement.atZero.transition.progress, 0);
  assert(replacement.atZero.transition.distance > .5, 'distance measured from the rendered pose');
  assert(replacement.afterStep.transition.progress > 0 && replacement.afterStep.transition.progress < 1);
  assert(largestStep(replacement.atZero, replacement.afterStep) < .08, 'first 16ms step is continuous');
  await page.evaluate(async () => { await window.poseHarness.ctx.resume(); window.poseHarness.startRender(); });
  await page.waitForFunction(() => {
    const value = window.poseHarness.snapshot();
    return value.performance.name === 'palm' && value.performance.weight > .99
      && value.transition?.to === 'palm' && !value.transition.active && value.angles.rightLowerArm > .8;
  });
  const settledPalm = await capture('actual-self-to-palm');
  assert(settledPalm.finite);
  passed('actual self replacement preserves dt0 quaternions, progresses continuously and settles on palm');

  // A queued, not-yet-audible semantic cue must disappear with interruption.
  const pausedRecovery = await page.evaluate(async () => {
    const a = window.__avatar, h = window.poseHarness;
    await h.ctx.suspend(); h.stopRender();
    const text = '我觉得，这个办法可以试试。';
    a.performance.speak(text, h.ctx, h.ctx.currentTime + 4, 4, {
      performance: { version: 1, source: 'llm', cues: [
        { intent: 'self_reference', gesture: 'self', anchor: '我觉得', start_char: 0, end_char: 3, strength: .4 },
      ] },
    });
    const queued = a.performance.queue.length, before = h.snapshot();
    a.onBargeIn();
    const canceled = { action: a.performance.action, queueLength: a.performance.queue.length, speaking: a.lip.speaking };
    const atZero = h.draw(a.performance.sample(), 0);
    h.startRender();
    return { queued, before, canceled, atZero };
  });
  assert(pausedRecovery.queued >= 1, 'fixture includes queued semantic cue');
  assert.equal(pausedRecovery.canceled.action, null);
  assert.equal(pausedRecovery.canceled.queueLength, 0);
  assert.equal(pausedRecovery.canceled.speaking, false);
  assert.deepEqual(pausedRecovery.atZero.quaternions, pausedRecovery.before.quaternions, 'stop starts recovery at actual pose without teleporting');
  assert.equal(pausedRecovery.atZero.transition.to, 'rest');
  await page.waitForFunction(() => {
    const value = window.poseHarness.snapshot();
    return value.performance.name === null && !value.transition.active && Object.values(value.angles).every(angle => angle < .002);
  });
  const recovered = await snapshot();
  assert.equal(recovered.at, pausedRecovery.before.at, 'native audio clock remains suspended throughout pose recovery');
  assert.equal(await page.evaluate(() => window.poseHarness.ctx.state), 'suspended');
  await page.locator('#viewport').screenshot({ path: `${out}/interrupted-suspended-rest.png` });
  passed('interrupt clears active/queued semantics immediately and recovers all arms while native audio clock is suspended');

  // Deterministic renderer frame control checks mid-transition replacement and
  // repeated same-name actions without advancing an unrelated audio schedule.
  const rapid = await page.evaluate(() => {
    const h = window.poseHarness; h.stopRender();
    const frame = name => ({ ...h.preview(name), weight: 1, phase: .4 });
    const self = frame('self'); h.draw(self, 0);
    for (let i = 0; i < 30; i++) h.draw(self, .016);
    const beforePalm = h.snapshot(), palm = frame('palm'), palmZero = h.draw(palm, 0);
    const midPalm = h.draw(palm, .016), wave = frame('wave'), waveZero = h.draw(wave, 0);
    const midWave = h.draw(wave, .016), restartWave = frame('wave'), restartZero = h.draw(restartWave, 0);
    const steps = [restartZero];
    for (let i = 0; i < 30; i++) steps.push(h.draw(restartWave, .016));
    window.__avatar.onBargeIn(); h.draw(window.__avatar.performance.sample(), 0);
    h.startRender();
    return { beforePalm, palmZero, midPalm, waveZero, midWave, restartZero, steps };
  });
  assert.deepEqual(rapid.palmZero.quaternions, rapid.beforePalm.quaternions);
  assert.deepEqual(rapid.waveZero.quaternions, rapid.midPalm.quaternions, 'rapid replacement snapshots partially moved pose');
  assert.deepEqual(rapid.restartZero.quaternions, rapid.midWave.quaternions, 'same-name restart also snapshots actual current pose');
  assert(rapid.waveZero.transition.id > rapid.palmZero.transition.id && rapid.restartZero.transition.id > rapid.waveZero.transition.id);
  assert.equal(rapid.restartZero.transition.reason, 'replace');
  assert.equal(rapid.restartZero.transition.from, 'wave');
  assert.equal(rapid.restartZero.transition.to, 'wave');
  const maximumControlledStep = Math.max(...rapid.steps.slice(1).map((value, i) => largestStep(rapid.steps[i], value)));
  assert(maximumControlledStep < .35 && maximumControlledStep > 0, 'controlled 16ms frames remain continuous and actually move');
  assert(rapid.steps.every(value => value.finite));
  assert.equal(rapid.steps.at(-1).transition.progress, 1);
  assert.equal(rapid.steps.at(-1).transition.active, false);
  passed('rapid different and same-name replacements recapture current pose with finite continuous quaternion steps');

  await page.waitForFunction(() => Object.values(window.poseHarness.snapshot().angles).every(angle => angle < .002));
  await page.evaluate(() => window.poseHarness.ctx.resume());
  for (const enabled of [false, true]) {
    const mode = enabled ? 'enabled' : 'baseline';
    await page.locator('#poseTransitions').setChecked(enabled);
    await page.selectOption('#transitionExample', 'self-palm');
    await page.evaluate(() => { window.poseHarness.trace = []; window.poseHarness.observing = true; });
    await page.locator('#transitionDemo').click();
    await page.waitForFunction(() => {
      const frame = window.__avatar.performance.sample();
      return frame.name === 'self' && frame.weight > .99;
    });
    const self = await capture(`${mode}-self`);
    assert.equal(enabled ? typeof self.transition : self.transition, enabled ? 'object' : null);
    await page.waitForFunction(() => {
      const frame = window.__avatar.performance.sample();
      return frame.name === 'palm' && frame.weight > .99;
    });
    const palm = await capture(`${mode}-palm`);
    await page.waitForFunction(() => document.querySelector('#transitionStatus').textContent.includes('流程结束'));
    await page.waitForFunction(() => Object.values(window.poseHarness.snapshot().angles).every(angle => angle < .002));
    const rest = await snapshot(), status = await page.locator('#transitionStatus').textContent();
    const trace = await page.evaluate(() => { window.poseHarness.observing = false; return window.poseHarness.trace; });
    const names = trace.map(value => value.performance.name);
    assert(names.includes('self') && names.includes('palm') && names.at(-1) === null, `${mode}: fixed flow executes self→palm→rest`);
    assert(enabled ? /自然姿态.*毫秒.*已到达/.test(status) : /原有逐帧平滑/.test(status), `${mode}: visible diagnostic matches checkbox`);
    assert.equal(enabled ? typeof rest.transition : rest.transition, enabled ? 'object' : null);
    assert(trace.every(value => value.finite));
    await page.locator('#transitionPanel').screenshot({ path: `${out}/${mode}-status.png` });
    flows[mode] = { self, palm, rest, status, trace: trace.map(value => ({ name: value.performance.name, at: value.at, transition: value.transition })) };
  }
  passed('checkbox before/after uses identical self→palm→rest preset, visible status and actual resting bones');

  await page.selectOption('#transitionExample', 'wave-stop');
  await page.locator('#transitionDemo').click();
  await page.waitForFunction(() => window.__avatar.performance.sample().name === 'wave');
  await page.locator('#transitionStop').click();
  assert.equal(await page.evaluate(() => window.__avatar.performance.action), null);
  assert.equal(await page.evaluate(() => window.__avatar.performance.queue.length), 0);
  await page.waitForFunction(() => Object.values(window.poseHarness.snapshot().angles).every(angle => angle < .002));
  assert.match(await page.locator('#transitionStatus').textContent(), /流程已停止.*后续切换已取消/);
  passed('actual stop control cancels preview flow and prevents later scheduled switches');

  await page.selectOption('#transitionExample', 'self-palm');
  await page.locator('#transitionDemo').click();
  const switchStartedAt = await page.evaluate(() => window.poseHarness.ctx.currentTime);
  await page.selectOption('#renderer', 'svg');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'svg');
  const switchedAt = await page.evaluate(() => window.poseHarness.ctx.currentTime);
  assert(switchedAt - switchStartedAt < .8, 'renderer switches before authored palm step');
  assert(await page.locator('#poseTransitions').isDisabled(), 'SVG exposes no pose-transition control');
  await page.waitForFunction(start => window.poseHarness.ctx.currentTime - start > 1.1, switchStartedAt);
  const afterSwitch = await page.evaluate(() => ({ action: window.__avatar.performance.action,
    queueLength: window.__avatar.performance.queue.length, sample: window.__avatar.performance.sample() }));
  assert.equal(afterSwitch.action, null, 'authored palm cannot resume in SVG after its scheduled time');
  assert.equal(afterSwitch.queueLength, 0);
  assert.equal(afterSwitch.sample.name, null);
  assert.match(await page.locator('#transitionStatus').textContent(), /流程已停止.*后续切换已取消/);
  await page.selectOption('#renderer', 'vrm');
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.rendererKind === 'vrm'
    && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  assert.equal(await page.locator('#poseTransitions').isDisabled(), false, 'daily VRM reload restores transition control');
  assert(await page.locator('#poseTransitions').isChecked(), 'transition preference survives renderer switch');
  passed('renderer switch cancels pending authored palm before its deadline and VRM reload restores transition control');

  assert.deepEqual(errors, []);
  assert.deepEqual(forbiddenRequests, []);
  assert.equal(await page.evaluate(() => window.poseHarness.microphoneRequests), 0);
  passed('no browser errors, API mutations, microphone or live speech service');
  await writeFile(`${out}/results.json`, JSON.stringify({ ok: true, checks, errors, forbiddenRequests, replacement, settledPalm,
    pausedRecovery, recovered, rapid: { beforePalm: rapid.beforePalm, palmZero: rapid.palmZero, midPalm: rapid.midPalm,
      waveZero: rapid.waveZero, midWave: rapid.midWave, restartZero: rapid.restartZero, maximumControlledStep }, flows,
    evidence: 'Real daily VRM, native AudioContext and current laboratory preset controls. Controlled renderer frames verify dt0 quaternions and rapid replacement. No model/TTS/device or persistent data writes.',
    limits: 'Continuity and finite real bone transforms do not establish natural hand anatomy, collision prevention, clothing skin weights or physical device performance. Ordinary semantic gaps still recover to rest; no IK or new gesture library is tested.',
  }, null, 2));
  console.log(`Pose transitions browser regression passed: ${checks.length} check groups. Evidence: ${out}/results.json`);
} finally { await browser.close(); }
