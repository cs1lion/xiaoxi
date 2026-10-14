import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const output = 'eval/avatar/eyes';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
const errors = [], checks = [], observations = [];
page.on('pageerror', error => errors.push(error.message));
const check = (condition, label) => { assert.ok(condition, label); checks.push(label); };

// Advance the actual Avatar + VRM update chain deterministically. Skip only
// rasterization during intermediate frames; render the final pose for screenshots.
async function advance(seconds) {
  await page.evaluate(seconds => {
    const a = window.__avatar, state = a.svg._state;
    const render = state?.renderer?.render;
    if (render) state.renderer.render = () => {};
    try {
      const steps = Math.ceil(seconds * 60);
      for (let i = 0; i < steps; i++) a._frame(1 / 60);
    } finally {
      if (render) {
        state.renderer.render = render;
        state.renderer.render(state.scene, state.camera);
      }
    }
  }, seconds);
}

async function snapshot() {
  return page.evaluate(() => {
    const a = window.__avatar, s = a.svg._state, manager = s.vrm.expressionManager;
    const native = {};
    s.vrm.scene.traverse(mesh => {
      for (const [name, index] of Object.entries(mesh.morphTargetDictionary || {})) {
        const short = name.slice(name.lastIndexOf('.') + 1);
        native[short] ??= mesh.morphTargetInfluences[index];
      }
    });
    return {
      frame: a.eyeFrame, native,
      blinkLeft: manager.getValue('blinkLeft'), blinkRight: manager.getValue('blinkRight'),
      vowels: ['aa', 'ih', 'ou', 'ee', 'oh'].map(name => manager.getValue(name) || 0),
      yaw: s.vrm.lookAt.yaw, targetBound: s.vrm.lookAt.target === s.target,
      gaze: { mode: a.gazeMode, targetName: a.idle.targetName,
        anchor: { ...a.idle.customAnchor }, mousePos: a.idle.mousePos },
      capabilities: a.svg.getEyeCapabilities(),
    };
  });
}

async function freeze() {
  await page.evaluate(() => {
    const a = window.__avatar;
    cancelAnimationFrame(a._raf); a._raf = 0;
    a.idle.energy = 1; a.idle.cfg.energy.drain_per_sec = 0;
    a.idle.nextBlink = 100000; a.idle.blinkPhase = -1;
    a.idle.blinkHold = 0; a.idle.eyeLevel = 1;
    a.gesture.stopAll(); a.setReducedMotion(true);
  });
}

try {
  await page.goto('http://127.0.0.1:8000/static/avatar_lab.html?model=seed');
  await page.waitForFunction(() => window.__avatar?.rendererKind === 'vrm', {}, { timeout: 90000 });
  await freeze();
  check(await page.locator('[data-eye-expression]').count() === 8, 'eight expression controls');
  check((await snapshot()).targetBound, 'VRM LookAt target attached');

  for (const name of ['daily', 'happy', 'curious', 'shy', 'tsundere', 'sleepy', 'focused', 'surprised']) {
    await page.locator(`[data-eye-expression="${name}"]`).click();
    await advance(1.6);
    const pose = await snapshot();
    check(pose.frame.preview === name, `${name}: preview selected`);
    check(Object.values(pose.frame.params).every(Number.isFinite), `${name}: finite output`);
    observations.push({ name, ...pose });
    await page.locator('#viewport').screenshot({ path: `${output}/seed-${name}.png` });
  }
  const happy = observations.find(pose => pose.name === 'happy');
  const curious = observations.find(pose => pose.name === 'curious');
  check(happy.native.eye_smile > 0.2, 'happy drives actual native smile morph');
  check(curious.native.eye_brow_up_L > curious.native.eye_brow_up_R + 0.1,
    'curious drives independent native brow shape weights');
  check(observations.find(pose => pose.name === 'surprised').native.eye_open > 0.3,
    'surprised drives widening morph');

  await page.locator('[data-eye-expression="sleepy"]').click();
  await advance(1);
  const sleepy = await snapshot();
  await page.locator('#eyeBlink').click();
  await advance(0.09);
  const closed = await snapshot();
  check(closed.frame.params.ParamEyeLOpen === 0 && closed.frame.params.ParamEyeROpen === 0,
    'sleepy blink completely closes both eyes');
  check(closed.blinkLeft === 1 && closed.blinkRight === 1 && closed.native.eye_smile === 0,
    'native full blink keeps closing shapes within budget');
  await page.locator('#viewport').screenshot({ path: `${output}/seed-sleepy-blink.png` });
  await advance(0.8);
  const restored = await snapshot();
  check(Math.abs(restored.frame.params.ParamEyeLOpen - sleepy.frame.params.ParamEyeLOpen) < 0.02,
    'sleepy blink restores expression opening');
  const automaticBlinks = await page.evaluate(() => {
    const a = window.__avatar, state = a.svg._state;
    const render = state.renderer.render;
    const samples = [];
    state.renderer.render = () => {};
    try {
      for (const fps of [20, 30, 60]) {
        a.idle.blinkPhase = -1; a.idle.blinkQueued = 0; a.idle.blinkHold = 0;
        a.idle.nextBlink = 0; a.idle.eyeLevel = 1;
        a.idle.cfg.blink.double_prob = 0;
        let fullyClosed = false;
        for (let frame = 0; frame < fps * 0.7; frame++) {
          a._frame(1 / fps);
          const p = a.eyeFrame.params, manager = state.vrm.expressionManager;
          if (p.ParamEyeLOpen === 0 && p.ParamEyeROpen === 0) {
            fullyClosed ||= manager.getValue('blinkLeft') === 1 && manager.getValue('blinkRight') === 1;
          }
        }
        samples.push({ fps, fullyClosed, restored: a.eyeFrame.params.ParamEyeLOpen });
        a.idle.nextBlink = 100000;
      }
    } finally {
      state.renderer.render = render;
      state.renderer.render(state.scene, state.camera);
    }
    return samples;
  });
  for (const sample of automaticBlinks) {
    check(sample.fullyClosed && Math.abs(sample.restored - sleepy.frame.params.ParamEyeLOpen) < 0.02,
      `automatic sleepy blink fully closes native eyes and restores at ${sample.fps} FPS`);
  }

  await page.locator('[data-gaze="follow"]').click();
  await page.evaluate(() => window.__avatar.onUserInteraction('mousemove', -0.8, -0.1));
  await advance(0.8);
  const left = await snapshot();
  await page.evaluate(() => window.__avatar.onUserInteraction('mousemove', 0.8, -0.1));
  await advance(0.8);
  const right = await snapshot();
  check(right.yaw - left.yaw > 5, `follow mode changes actual lookAt across expression (${JSON.stringify({ left: left.yaw, right: right.yaw, leftInput: left.frame.params.ParamEyeBallX, rightInput: right.frame.params.ParamEyeBallX, leftHead: left.frame.params.ParamAngleX, rightHead: right.frame.params.ParamAngleX })})`);
  check(Object.entries(right.native).filter(([key]) => /^look_/.test(key)).some(([, value]) => value > 0.01),
    'Seed expression gaze remains driven alongside native eye expression');
  await page.locator('#viewport').dispatchEvent('pointerleave');
  await advance(1);
  const reset = await snapshot();
  check(reset.gaze.mousePos === null && reset.gaze.targetName === 'viewer'
    && Math.abs(reset.frame.params.ParamEyeBallX) < 0.08,
    'pointer leave releases cached mouse target and returns gaze to viewer');
  await page.locator('[data-gaze="forward"]').click();
  await advance(0.5);
  await page.evaluate(() => window.__avatar.onUserInteraction('mousemove', 1, 1));
  await advance(0.5);
  const front = await snapshot();
  check(front.frame.params.ParamEyeBallX === 0 && front.frame.params.ParamEyeBallY === 0,
    'forward mode locks gaze while preserving expression');
  await page.locator('[data-gaze="auto"]').click();
  await advance(1.2);
  const auto1 = await snapshot();
  await advance(2.4);
  const auto2 = await snapshot();
  check(Math.abs(auto1.frame.params.ParamEyeBallX - auto2.frame.params.ParamEyeBallX) > 0.1,
    'auto mode changes attention target');
  check(Math.abs(auto2.gaze.anchor.x - 0.48 * 0.35) < 0.001,
    'auto mode moves micro-saccade anchor to the current attention target');
  await advance(0.6);
  const autoHeld = await snapshot();
  check(autoHeld.gaze.anchor.x === auto2.gaze.anchor.x
    && Math.abs(autoHeld.frame.params.ParamEyeBallX - auto2.frame.params.ParamEyeBallX) < 0.08,
    'auto mode holds its new target through subsequent micro-saccades');

  await page.locator('#eyeHighlight').press('Home');
  await advance(0.8);
  check((await snapshot()).native.eye_no_hilight > 0.99, 'highlight slider hides native highlight');
  await page.locator('#eyeHighlight').press('End');
  await page.locator('[data-eye-expression="happy"]').click();
  await page.locator('#eyeIntensity').press('Home');
  await advance(0.8);
  const zero = await snapshot();
  check(zero.native.eye_smile < 0.001 && zero.frame.params.ParamEyeLOpen > 0.99,
    'zero intensity returns eyes to neutral without making them half closed');
  await page.locator('#eyeIntensity').press('End');
  await advance(0.8);

  await page.evaluate(async () => {
    const a = window.__avatar;
    const ctx = new AudioContext(); await ctx.resume(); window.__eyeTestAudio = ctx;
    a.attachOutputAnalyser(null);
    a.speak('你好，今天心情很好。', ctx, ctx.currentTime, 3);
  });
  await page.waitForTimeout(200);
  await advance(0.08);
  const talking = await snapshot();
  check(talking.vowels.reduce((sum, value) => sum + value, 0) > 0.01 && talking.native.eye_smile > 0.1,
    'audio clock mouth and native happy eyes work simultaneously');
  await page.evaluate(() => window.__avatar.onBargeIn());
  await advance(0.8);
  check((await snapshot()).vowels.every(value => value < 0.001), 'interrupt closes speech mouth');
  await page.evaluate(() => window.__eyeTestAudio.close());

  await page.locator('#eyeInherit').click();
  await advance(0.8);
  check(!(await page.evaluate(() => window.__avatar.eyeExpression.sample().active)),
    'inherit releases preview to backend emotion');
  await page.locator('#daily').click();
  await page.waitForFunction(() => window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study'
    && !document.getElementById('renderer').disabled);
  await freeze();
  for (const name of ['daily', 'happy', 'curious', 'shy', 'tsundere', 'sleepy', 'focused', 'surprised']) {
    await page.locator(`[data-eye-expression="${name}"]`).click();
    await advance(1.6);
    const pose = await snapshot();
    check(pose.capabilities.profile === 'seed' && pose.targetBound
      && Object.values(pose.frame.params).every(Number.isFinite), `${name}: daily derivative retains eye adapter and gaze`);
    if (name === 'happy') check(pose.native.eye_smile > 0.2, 'daily derivative native smile works');
    observations.push({ name: `daily-${name}`, ...pose });
    await page.locator('#viewport').screenshot({ path: `${output}/daily-${name}.png` });
  }
  await page.locator('[data-eye-expression="happy"]').click();
  await advance(1);
  await page.screenshot({ path: `${output}/implemented-lab.png`, fullPage: true });
  await page.locator('#sample').click();
  await page.waitForFunction(() => window.__avatar.svg.getEyeCapabilities?.().profile === 'sample'
    && !document.getElementById('renderer').disabled);
  await freeze();
  await page.locator('[data-eye-expression="happy"]').click();
  await advance(0.8);
  const sample = await snapshot();
  check(sample.capabilities.profile === 'sample' && sample.targetBound, 'sample remount keeps adapter and gaze');
  check(sample.native.Fcl_EYE_Joy_L > 0.1 && sample.native.Fcl_EYE_Joy_R > 0.1,
    'sample native per-eye smile works');
  await page.locator('#viewport').screenshot({ path: `${output}/sample-happy.png` });

  await page.selectOption('#renderer', 'svg');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'svg');
  await page.evaluate(() => { cancelAnimationFrame(window.__avatar._raf); window.__avatar._raf = 0; });
  check(await page.locator('#avatarStage canvas').count() === 0, 'SVG fallback disposes VRM');
  check(await page.locator('#eyeHighlight').isDisabled(), 'unsupported highlight control disabled');
  await page.selectOption('#renderer', 'vrm');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'vrm'
    && !document.getElementById('renderer').disabled);
  await freeze();
  await advance(0.4);
  await page.setViewportSize({ width: 430, height: 900 });
  await page.locator('#viewport').screenshot({ path: `${output}/mobile.png` });
  check(await page.locator('#avatarStage canvas').count() === 1, 'mobile viewport has one canvas');
  check(errors.length === 0, `no page errors: ${errors.join('; ')}`);

  await writeFile(`${output}/browser-results.json`, JSON.stringify({ ok: true, checks, observations, automaticBlinks, errors,
    device: 'Headless Chromium with SwiftShader; not physical device performance validation.' }, null, 2));
  console.log(`Eye browser checks passed (${checks.length})`);
} finally {
  await browser.close();
}
