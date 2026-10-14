/* Observe the public 30.6-second comparison with ordinary RAF and AudioContext.
 * Screenshots and clean video use separate passes, without frozen poses. */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const videoOnly = process.argv.includes('--video-only');
const output = `eval/avatar/dialogue-gaze-v2-preview/${new Date().toISOString().replace(/[:.]/g, '-')}`;
const sourceFiles = ['scripts/capture-dialogue-gaze-v2-preview.mjs', 'web/avatar_lab.js',
  'web/avatar_lab.html', 'web/avatar/index.js', 'web/app.js', 'web/avatar/calibration.js',
  'web/avatar/drivers/dialogue-gaze.js', 'web/avatar/drivers/dialogue-expression.js',
  'web/avatar/drivers/idle.js', 'web/avatar/drivers/emotion.js', 'web/avatar/drivers/face.js',
  'web/avatar/drivers/gesture.js', 'web/avatar/drivers/discourseGesture.js',
  'web/avatar/drivers/eye-expression.js', 'web/avatar/drivers/pose-transition.js', 'web/avatar/params.js',
  'web/avatar/render/eye-morph.js', 'web/avatar/render/vrm.js', 'web/avatar/render/vrm-mapping.js',
  'web/avatar/render/finger-poses.js', 'web/avatar/render/hand-poses.js', 'web/avatar/render/gesture-poses.js',
  'web/avatar/render/body-poses.js', 'config/avatar.yaml'];
const hash = buffer => createHash('sha256').update(buffer).digest('hex');
await mkdir(output + '/sources', { recursive: true });
const sources = [];
for (const path of sourceFiles) {
  const buffer = await readFile(path), destination = output + '/sources/' + path;
  await mkdir(destination.slice(0, destination.lastIndexOf('/')), { recursive: true });
  await writeFile(destination, buffer);
  sources.push({ path, sha256: hash(buffer) });
}
const model = await readFile('web/models/seed-daily-lids.vrm');
const browser = await chromium.launch({ headless: true,
  args: process.platform === 'win32' ? ['--use-angle=d3d11'] : [] });
const context = await browser.newContext({ viewport: { width: 1280, height: 920 },
  ...(videoOnly ? { recordVideo: { dir: output + '/video', size: { width: 1280, height: 920 } } } : {}) });
const page = await context.newPage(), errors = [], frames = [];
page.on('pageerror', error => errors.push(error.message));
const sample = () => page.evaluate(() => {
  const a = window.__avatar;
  return { now: performance.now(), audioTime: a.audioCtx?.currentTime,
    comparison: { ...window.__dialogueComparison }, dialogue: { ...a.dialogueFrame },
    expression: a.dialogueExpressionFrame, params: { ...a.eyeFrame?.params },
    blink: a.eyeFrame?.blink, lip: a.lip.speaking,
    status: document.querySelector('#dialogueStatus').textContent };
});
let metadata;
try {
  await page.goto(origin + '/static/avatar_lab.html?model=daily-lids&dialogue=1');
  await page.waitForFunction(() => window.__avatarReady &&
    window.__avatar.svg.getEyeCapabilities?.().profile === 'seed-lids', {}, { timeout: 90000 });
  await page.mouse.move(1200, 10);
  metadata = await page.evaluate(() => {
    const a = window.__avatar, gl = a.svg._state.renderer.getContext();
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    return { userAgent: navigator.userAgent, viewport: { width: innerWidth, height: innerHeight },
      renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      cfg: { ...a.dialogueGaze.cfg }, reducedMotion: a.reducedMotion };
  });
  await page.evaluate(() => {
    window.__v2CaptureTrace = [];
    let previous = -Infinity;
    const observe = now => {
      if (now - previous >= 60) {
        const a = window.__avatar;
        window.__v2CaptureTrace.push({ now, audioTime: a.audioCtx?.currentTime,
          comparison: { ...window.__dialogueComparison }, state: a.dialogueFrame?.state,
          phase: a.dialogueFrame?.phase, owner: a.dialogueFrame?.owner,
          glance: a.dialogueFrame?.glance, plan: a.dialogueFrame?.plan,
          expression: a.dialogueExpressionFrame,
          x: a.eyeFrame?.params.ParamEyeBallX, y: a.eyeFrame?.params.ParamEyeBallY,
          mouth: a.eyeFrame?.params.ParamMouthOpenY,
          headX: a.eyeFrame?.params.ParamAngleX, headY: a.eyeFrame?.params.ParamAngleY,
          blink: a.eyeFrame?.blink });
        previous = now;
      }
      window.__v2CaptureRAF = requestAnimationFrame(observe);
    };
    window.__v2CaptureRAF = requestAnimationFrame(observe);
  });
  await page.click('#dialogueCompare');
  const styles = ['restrained', 'natural', 'lively'];
  const moments = [{ time: 1.3, state: 'listening', phase: 'attending', label: 'listening' },
    { time: 3.02, state: 'thinking', phase: 'averted', label: 'thinking' },
    { time: 6.4, state: 'speaking', phase: 'speaking', label: 'response' }];
  for (let index = 0; index < styles.length; index++) {
    for (const moment of moments) {
      await page.waitForFunction(({ index, time }) => {
        const c = window.__dialogueComparison;
        return c.active && c.index === index && c.elapsed >= time;
      }, { index, time: index * 10.2 + moment.time }, { timeout: 35000 });
      const before = await sample();
      const filename = videoOnly ? null : `${styles[index]}-${moment.label}.png`;
      if (filename) await page.locator('#viewport').screenshot({ path: output + '/' + filename });
      const after = await sample();
      frames.push({ style: styles[index], index, moment, filename, before, after });
      for (const boundary of [before, after]) {
        assert.equal(boundary.comparison.style, styles[index]);
        // Emotion/text may rename thinking to preparing inside one continuous
        // aversion. Its absolute glance and expression cue remain unchanged.
        if (moment.state === 'thinking') assert.ok(['thinking', 'preparing'].includes(boundary.dialogue.state));
        else assert.equal(boundary.dialogue.state, moment.state);
        assert.equal(boundary.dialogue.phase, moment.phase);
      }
    }
  }
  await page.waitForFunction(() => !window.__dialogueComparison.active, {}, { timeout: 20000 });
  const trace = await page.evaluate(() => {
    cancelAnimationFrame(window.__v2CaptureRAF);
    return window.__v2CaptureTrace;
  });
  const blueprints = styles.map(style => trace.find(entry =>
    entry.comparison.style === style && entry.phase === 'averted')?.plan?.glances);
  assert.ok(blueprints.every(Array.isArray), 'all styles reach the planned glance');
  assert.deepEqual(blueprints[0], blueprints[1]);
  assert.deepEqual(blueprints[1], blueprints[2]);
  assert.equal(errors.length, 0, errors.join('\n'));
  for (const source of sources) assert.equal(hash(await readFile(source.path)), source.sha256,
    source.path + ' changed during capture');
  assert.equal(hash(await readFile('web/models/seed-daily-lids.vrm')), hash(model));
  await writeFile(output + '/results.json', JSON.stringify({ capturedAt: new Date().toISOString(),
    purpose: 'Real continuous playback of the public profile comparison; visual QA, not a naturalness or FPS benchmark.',
    captureMode: videoOnly ? 'clean-video-without-screenshots' : 'timed-screenshots-without-video',
    browserVersion: browser.version(), url: page.url(), metadata, sources,
    model: { path: 'web/models/seed-daily-lids.vrm', bytes: model.length, sha256: hash(model) },
    video: videoOnly ? 'continuous.webm' : null, frames, trace, errors }, null, 2));
} catch (error) {
  await writeFile(output + '/failure.json', JSON.stringify({ error: error.stack, errors, frames }, null, 2));
  throw error;
} finally {
  const video = page.video();
  await page.close();
  if (video) await video.saveAs(output + '/continuous.webm');
  await context.close(); await browser.close();
}
console.log(JSON.stringify({ output, frames: frames.length, errors }, null, 2));
