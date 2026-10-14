/* Observe the public intent selector and normal demo with ordinary RAF and
 * the real AudioContext. Run screenshots and video as separate processes.
 * Presentation CSS in video mode only changes the visible layout.
 */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const videoOnly = process.argv.includes('--video-only');
const output = `eval/avatar/dialogue-gaze-v3-preview/${new Date().toISOString().replace(/[:.]/g, '-')}`;
const sourceFiles = ['scripts/capture-dialogue-gaze-v3-preview.mjs', 'web/avatar_lab.js',
  'web/avatar_lab.html', 'web/avatar/index.js', 'web/app.js', 'web/avatar/calibration.js',
  'web/avatar/dialogue-review.js', 'web/avatar/dialogue-review-ui.js', 'web/avatar/dialogue-review.css',
  'web/avatar/drivers/dialogue-intent.js', 'web/avatar/drivers/dialogue-gaze.js',
  'web/avatar/drivers/dialogue-expression.js', 'web/avatar/drivers/idle.js',
  'web/avatar/drivers/emotion.js', 'web/avatar/drivers/face.js', 'web/avatar/drivers/gesture.js',
  'web/avatar/drivers/discourseGesture.js', 'web/avatar/drivers/eye-expression.js',
  'web/avatar/drivers/pose-transition.js', 'web/avatar/params.js', 'web/avatar/render/eye-morph.js',
  'web/avatar/render/vrm.js', 'web/avatar/render/vrm-mapping.js',
  'web/avatar/render/finger-poses.js', 'web/avatar/render/hand-poses.js',
  'web/avatar/render/gesture-poses.js', 'web/avatar/render/body-poses.js', 'config/avatar.yaml'];
const hash = buffer => createHash('sha256').update(buffer).digest('hex');
const intents = [{ id: 'serious', label: '认真', browAt: .34 },
  { id: 'hesitant', label: '犹豫', browAt: .47 }, { id: 'teasing', label: '调侃', browAt: .34 }];
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
const viewport = { width: 1280, height: 920 };
const context = await browser.newContext({ viewport,
  ...(videoOnly ? { recordVideo: { dir: output + '/video', size: viewport } } : {}) });
const videoCSS = `header,aside{display:none!important}body{overflow:hidden!important}
  main{display:block!important;max-width:none!important;padding:0!important;margin:0!important}
  #viewport{position:fixed!important;inset:0!important;top:0!important;width:100vw!important;
  height:100vh!important;border:0!important;border-radius:0!important}
  #viewport .badge,#viewport .caption{display:none!important}`;
if (videoOnly) await context.addInitScript(css => {
  const style = document.createElement('style'); style.textContent = css;
  if (document.documentElement) document.documentElement.append(style);
  else {
    const observer = new MutationObserver(() => {
      if (document.documentElement) { document.documentElement.append(style); observer.disconnect(); }
    });
    observer.observe(document, { childList: true });
  }
}, videoCSS);
const page = await context.newPage(), errors = [], frames = [], segments = [];
page.on('pageerror', error => errors.push(error.message));
const sample = () => page.evaluate(() => {
  const a = window.__avatar;
  return { now: performance.now(), audioTime: a.audioCtx?.currentTime,
    demo: { ...window.__dialogueComparison }, dialogue: { ...a.dialogueFrame },
    expression: a.dialogueExpressionFrame, params: { ...a.eyeFrame?.params },
    blink: a.eyeFrame?.blink, lip: a.lip.speaking,
    emotion: a.emotion.matchingMode ? a.emotion.matchingTarget : a.emotion.current,
    intentMode: a.dialogueIntentMode, reviewEnabled: a.dialogueReview.recording,
    reviewTurn: window.__dialogueReviewUI.activeTurn,
    status: document.querySelector('#dialogueStatus').textContent };
});
const assertMoment = (recorded, intent, moment) => {
  assert.equal(recorded.intentMode, intent);
  assert.equal(recorded.dialogue.intent, intent);
  assert.equal(recorded.expression.intent, intent);
  assert.equal(recorded.dialogue.style, 'natural');
  assert.equal(recorded.emotion, 'base');
  assert.equal(recorded.dialogue.owner, 'dialogue');
  assert.equal(recorded.reviewEnabled, true);
  if (moment.state === 'thinking') assert.ok(['thinking', 'preparing'].includes(recorded.dialogue.state));
  else assert.equal(recorded.dialogue.state, moment.state);
  assert.equal(recorded.dialogue.phase, moment.phase);
};
let metadata, trace = [], review;
try {
  await page.goto(origin + '/static/avatar_lab.html?model=daily-lids&dialogue=1');
  await page.waitForFunction(() => window.__avatarReady && window.__dialogueReviewUI &&
    window.__avatar.svg.getEyeCapabilities?.().profile === 'seed-lids', {}, { timeout: 90000 });
  await page.selectOption('#dialogueStyle', 'natural', { force: videoOnly });
  await page.selectOption('#dialogueExample', 'normal', { force: videoOnly });
  if (videoOnly) await page.locator('#dialogueReviewToggle').dispatchEvent('click');
  else await page.click('#dialogueReviewToggle');
  if (videoOnly) {
    await page.waitForFunction(() => {
      const rect = document.querySelector('#viewport').getBoundingClientRect();
      const canvas = window.__avatar.svg._state.renderer.domElement;
      return rect.width === innerWidth && rect.height === innerHeight &&
        Math.abs(canvas.clientWidth - innerWidth) < 2 && Math.abs(canvas.clientHeight - innerHeight) < 2;
    });
  }
  await page.mouse.move(1279, 1);
  // Supply ordinary trusted user activation for AudioContext.resume while
  // the clean-video public controls are visually hidden by presentation CSS.
  if (videoOnly) await page.mouse.click(1279, 1);
  metadata = await page.evaluate(() => {
    const a = window.__avatar, gl = a.svg._state.renderer.getContext();
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    const rect = document.querySelector('#viewport').getBoundingClientRect();
    return { userAgent: navigator.userAgent, viewport: { width: innerWidth, height: innerHeight },
      avatarViewport: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      cfg: { ...a.dialogueGaze.cfg }, reducedMotion: a.reducedMotion,
      presentation: a.svg._state.presentation, eyeFocus: a.svg._state.eyeFocus };
  });
  await page.evaluate(() => {
    window.__v3CaptureTrace = [];
    let previous = -Infinity;
    const observe = now => {
      if (now - previous >= 60) {
        const a = window.__avatar;
        window.__v3CaptureTrace.push({ now, audioTime: a.audioCtx?.currentTime,
          captureIntent: window.__v3CaptureIntent, demo: { ...window.__dialogueComparison },
          state: a.dialogueFrame?.state, phase: a.dialogueFrame?.phase, phaseAge: a.dialogueFrame?.phaseAge,
          owner: a.dialogueFrame?.owner, intent: a.dialogueFrame?.intent,
          glance: a.dialogueFrame?.glance, plan: a.dialogueFrame?.plan,
          expression: a.dialogueExpressionFrame, reviewEnabled: a.dialogueReview.recording,
          params: { ...a.eyeFrame?.params }, blink: a.eyeFrame?.blink });
        previous = now;
      }
      window.__v3CaptureRAF = requestAnimationFrame(observe);
    };
    window.__v3CaptureRAF = requestAnimationFrame(observe);
  });
  for (const intent of intents) {
    await page.selectOption('#dialogueIntent', intent.id, { force: videoOnly });
    await page.evaluate(id => { window.__v3CaptureIntent = id; }, intent.id);
    const began = await page.evaluate(() => performance.now());
    // The same public button handler is used in both passes. dispatchEvent
    // avoids scrolling a hidden control into the clean video viewport.
    if (videoOnly) await page.locator('#dialogueDemo').dispatchEvent('click');
    else await page.click('#dialogueDemo');
    await page.waitForFunction(id => window.__avatar.dialogueFrame?.state === 'listening' &&
      window.__avatar.dialogueFrame.intent === id && window.__dialogueReviewUI.activeTurn,
    intent.id, { timeout: 15000 });
    const turn = await page.evaluate(() => window.__dialogueReviewUI.activeTurn.id);
    const moments = [{ state: 'listening', phase: 'attending', label: 'listening', minAge: 1 },
      { state: 'thinking', phase: 'averted', label: 'thinking', minAge: intent.browAt },
      { state: 'speaking', phase: 'speaking', label: 'response', minAge: .95 }];
    for (const moment of moments) {
      await page.waitForFunction(({ id, moment, videoOnly }) => {
        const a = window.__avatar, f = a.dialogueFrame;
        const state = moment.state === 'thinking' ? ['thinking', 'preparing'].includes(f?.state) : f?.state === moment.state;
        return f?.intent === id && state && f.phase === moment.phase && f.phaseAge >= moment.minAge &&
          f.owner === 'dialogue' &&
          (videoOnly || a.eyeFrame?.blink < .05);
      }, { id: intent.id, moment, videoOnly }, { timeout: 15000 });
      const before = await sample();
      const filename = videoOnly ? null : `${intent.id}-${moment.label}.png`;
      if (filename) await page.locator('#viewport').screenshot({ path: output + '/' + filename });
      const after = await sample();
      frames.push({ intent: intent.id, label: intent.label, moment, filename, before, after });
      assertMoment(before, intent.id, moment); assertMoment(after, intent.id, moment);
    }
    await page.waitForFunction(() => document.querySelector('#dialogueStatus').textContent.startsWith('流程完成'),
      {}, { timeout: 15000 });
    const ended = await page.evaluate(() => performance.now());
    segments.push({ intent: intent.id, label: intent.label, turn, began, ended });
    if (!videoOnly) {
      await page.locator('#dialogueReviewMount').screenshot({ path: output + `/${intent.id}-review.png` });
    }
  }
  ({ trace, review } = await page.evaluate(() => {
    cancelAnimationFrame(window.__v3CaptureRAF);
    return { trace: window.__v3CaptureTrace, review: window.__dialogueReview.snapshot() };
  }));
  assert.equal(review.turns.length, 3, 'public recorder retains all three complete demonstration turns');
  assert.equal(review.current, null);
  const plans = intents.map(intent => trace.find(entry =>
    entry.captureIntent === intent.id && entry.phase === 'averted')?.plan);
  assert.ok(plans.every(plan => Array.isArray(plan?.glances)), 'every intent reached its actual glance');
  assert.deepEqual(plans[0].glances, plans[1].glances);
  assert.deepEqual(plans[1].glances, plans[2].glances);
  for (const [index, plan] of plans.entries()) assert.equal(plan.intentAtStart, intents[index].id);
  assert.equal(errors.length, 0, errors.join('\n'));
  for (const source of sources) assert.equal(hash(await readFile(source.path)), source.sha256,
    source.path + ' changed during capture');
  assert.equal(hash(await readFile('web/models/seed-daily-lids.vrm')), hash(model));
  await writeFile(output + '/review.json', JSON.stringify(review, null, 2));
  await writeFile(output + '/results.json', JSON.stringify({ capturedAt: new Date().toISOString(),
    purpose: 'Public manual intent comparison under base emotion, normal RAF and silent-mouth audio-clock demo; visual QA, not a naturalness or FPS benchmark.',
    captureMode: videoOnly ? 'clean-video-without-screenshots' : 'timed-screenshots-without-video',
    videoPresentation: videoOnly ? 'CSS hides controls and fills the frame; no driver or model settings changed.' : null,
    browserVersion: browser.version(), url: page.url(), metadata, sources,
    model: { path: 'web/models/seed-daily-lids.vrm', bytes: model.length, sha256: hash(model) },
    video: videoOnly ? 'continuous.webm' : null, intents, segments, frames, trace, review: 'review.json', errors }, null, 2));
} catch (error) {
  const observed = await page.evaluate(() => window.__v3CaptureTrace || []).catch(() => []);
  await writeFile(output + '/failure-trace.json', JSON.stringify(observed, null, 2));
  await writeFile(output + '/failure.json', JSON.stringify({ error: error.stack, errors, segments, frames,
    sources, trace: 'failure-trace.json', model: { bytes: model.length, sha256: hash(model) } }, null, 2));
  throw error;
} finally {
  const video = page.video();
  await page.close();
  if (video) await video.saveAs(output + '/continuous.webm');
  await context.close(); await browser.close();
}
console.log(JSON.stringify({ output, frames: frames.length, errors }, null, 2));
