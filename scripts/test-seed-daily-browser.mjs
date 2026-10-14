import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const out = 'eval/avatar';
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] });
const errors = [];
const checks = [];
const gestures = {};
const passed = name => checks.push(name);
const page = await browser.newPage({ viewport: { width: 1280, height: 920 }, reducedMotion: 'no-preference' });
page.on('pageerror', e => errors.push(e.message));
page.setDefaultTimeout(15000);

async function diagnostics() {
  await page.evaluate(() => {
    window.__dailySnapshot = () => {
      const a = window.__avatar;
      const s = a.svg._state;
      const arms = Object.fromEntries(Object.entries(s.arms).map(([name, bone]) => [name, bone.node.quaternion.angleTo(bone.rest)]));
      const expressions = Object.fromEntries(Object.keys(s.expressions).map(name => [name, s.vrm.expressionManager.getValue(name)]));
      const morphs = [];
      let finite = true;
      s.vrm.scene.traverse(node => {
        if (node.morphTargetInfluences) morphs.push({ name: node.name, values: [...node.morphTargetInfluences] });
        if (!node.matrixWorld.elements.every(Number.isFinite)) finite = false;
      });
      const numbers = [...Object.values(arms), ...Object.values(expressions), ...morphs.flatMap(m => m.values)];
      return {
        name: s.vrm.meta.name, arms, expressions, morphs,
        finite: finite && numbers.every(Number.isFinite),
        performance: a.performance.sample(), speaking: a.lip.speaking,
        mouth: a.store.current.ParamMouthOpenY, reduced: a.reducedMotion,
        secondaryScale: a.svg.secondaryScale, drawCalls: s.renderer.info.render.calls,
      };
    };
  });
}

async function stopAndRest() {
  await page.locator('#interrupt').click();
  await page.waitForFunction(() => {
    const a = window.__avatar;
    const s = window.__dailySnapshot();
    return !a.lip.speaking && a.performance.action === null && a.performance.state === 'listening'
      && a.store.current.ParamMouthOpenY < 0.025 && Object.values(s.arms).every(angle => angle < 0.02);
  });
}

async function activeGesture(id, name) {
  await page.locator(id).click();
  await page.waitForFunction(name => {
    const s = window.__dailySnapshot();
    return s.performance.name === name && s.performance.weight > 0.98
      && s.arms.rightUpperArm > 0.15 && s.arms.rightLowerArm > 0.15;
  }, name);
  // At full weight the upper/lower arm targets stay constant; allow smoothing to settle.
  await page.waitForFunction(() => window.__dailySnapshot().performance.phase > 0.25);
  const snapshot = await page.evaluate(() => window.__dailySnapshot());
  assert(snapshot.finite, `${name}: finite morph weights, arm rotations and transforms`);
  assert(snapshot.arms.leftUpperArm < 0.02 && snapshot.arms.leftLowerArm < 0.02, `${name}: unposed left arm remains at rest`);
  return snapshot;
}

try {
  await mkdir(out, { recursive: true });
  const assetResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/static/models/seed-daily.vrm' && response.status() === 200, { timeout: 90000 });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  const downloaded = await assetResponse;
  const assetBytes = await downloaded.body();
  const assetRevision = { url: downloaded.url(), byteLength: assetBytes.length, sha256: createHash('sha256').update(assetBytes).digest('hex') };
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  await diagnostics();
  assert.equal(await page.locator('#avatarStage canvas').count(), 1);
  assert(await page.locator('#palette').isDisabled(), 'derivative does not use original Seed-san palette/accessories');
  assert.match(await page.locator('#status').textContent(), /VirtualCast, Inc\./);
  assert(await page.evaluate(() => document.querySelector('#viewport').getBoundingClientRect().height <= 760.1), 'lab canvas height stays bounded by viewport rule');
  const baseline = await page.evaluate(() => window.__dailySnapshot());
  assert(baseline.finite && baseline.drawCalls > 0, 'loaded derivative renders finite data');
  for (const name of ['aa', 'ih', 'ou', 'ee', 'oh', 'blinkLeft', 'blinkRight']) assert(name in baseline.expressions, `retains ${name} expression`);
  await page.locator('#viewport').screenshot({ path: `${out}/daily-idle.png` });
  passed('daily model, attribution, retained expressions, disabled palette and bounded viewport');

  for (const [id, name] of [['#gestureWave', 'wave'], ['#gesturePalm', 'palm'], ['#gestureDeny', 'deny'], ['#gestureSelf', 'self']]) {
    gestures[name] = await activeGesture(id, name);
    await page.locator('#viewport').screenshot({ path: `${out}/daily-${name}.png` });
    await stopAndRest();
  }
  passed('all four existing gestures reach active right arm poses and return to rest');

  await page.locator('#speak').click();
  await page.waitForFunction(() => {
    const s = window.__dailySnapshot();
    return s.speaking && s.mouth > 0.1 && ['aa', 'ih', 'ou', 'ee', 'oh'].some(name => s.expressions[name] > 0.04);
  });
  const speech = await page.evaluate(() => window.__dailySnapshot());
  assert(speech.finite && speech.morphs.some(m => m.values.some(v => v > 0)), 'speech reaches actual finite face morph weights');
  // Advance the existing idle driver's next blink; its normal gating and smoothing remain active.
  await page.evaluate(() => { window.__avatar.idle.nextBlink = 0; });
  await page.waitForFunction(() => {
    const s = window.__dailySnapshot();
    return s.expressions.blinkLeft > 0.25 && s.expressions.blinkRight > 0.25 && s.finite;
  });
  const blink = await page.evaluate(() => window.__dailySnapshot());
  assert(blink.morphs.some(m => m.values.some(v => v > 0.1)), 'blink is bound to face geometry');
  await page.locator('#viewport').screenshot({ path: `${out}/daily-speech-blink.png` });
  passed('existing lip and blink drivers reach nonzero finite facial morph weights');

  // Restart the five-second speech demonstration to guarantee live speech during interruption.
  await page.locator('#speak').click();
  await page.waitForFunction(() => window.__avatar.lip.speaking && window.__avatar.store.current.ParamMouthOpenY > 0.1);
  await activeGesture('#gesturePalm', 'palm');
  assert(await page.evaluate(() => window.__avatar.lip.speaking), 'speech is live before interrupt button');
  await stopAndRest();
  const interrupted = await page.evaluate(() => window.__dailySnapshot());
  await page.locator('#viewport').screenshot({ path: `${out}/daily-interrupted.png` });
  passed('real interrupt button clears live lips/action and returns all arms to rest');

  await page.locator('#reduced').check();
  const reduced = await activeGesture('#gestureWave', 'wave');
  assert(reduced.reduced, 'reduced motion enabled through actual checkbox');
  const secondary = await page.evaluate(() => Number(window.__avatar.cfg.reduced_motion.secondary));
  assert.equal(reduced.secondaryScale, secondary, 'secondary motion follows existing reduced motion configuration');
  const ratio = reduced.arms.rightUpperArm / gestures.wave.arms.rightUpperArm;
  assert(ratio > 0.3 && ratio < 0.5, `reduced arm amplitude preserved at about 40%, measured ${ratio}`);
  assert(reduced.arms.rightLowerArm > 0.15, 'reduced motion preserves an active gesture');
  await page.locator('#viewport').screenshot({ path: `${out}/daily-reduced.png` });
  await stopAndRest();
  await page.locator('#reduced').uncheck();
  passed('reduced motion keeps gestures active while reducing arm amplitude and secondary motion');

  await page.selectOption('#renderer', 'svg');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'svg');
  assert.equal(await page.locator('#avatarStage canvas').count(), 0, 'VRM canvas disposed on SVG switch');
  await page.selectOption('#renderer', 'vrm');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'vrm' && window.__avatar.svg._state.vrm.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  assert.equal(await page.locator('#avatarStage canvas').count(), 1, 'exactly one canvas after daily reload');
  const fallback = await page.evaluate(async () => {
    const a = window.__avatar;
    const previous = a.svg;
    const canvas = document.querySelector('#avatarStage canvas');
    try { await a.setRenderer('vrm', '/static/models/__daily_missing__.vrm'); return false; }
    catch { return a.svg === previous && canvas.isConnected && document.querySelectorAll('#avatarStage canvas').length === 1 && a.svg._state.vrm.meta.name === 'Seed-san Daily Study'; }
  });
  assert(fallback, 'failed model load preserves exact active daily rig and canvas');
  passed('SVG disposal, daily reload and failed load preserve active renderer');

  await page.setViewportSize({ width: 430, height: 900 });
  await page.waitForFunction(() => Math.abs(window.__avatar.svg._state.camera.aspect - document.querySelector('#avatarStage').clientWidth / document.querySelector('#avatarStage').clientHeight) < 0.02);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile lab has no horizontal overflow');
  await page.screenshot({ path: `${out}/daily-lab-mobile.png`, fullPage: true });
  passed('mobile laboratory resize and no horizontal overflow');

  await page.setViewportSize({ width: 1280, height: 920 });
  await page.routeWebSocket('**/ws', socket => socket.onMessage(() => {}));
  await page.goto(`${origin}/?avatar=seed-daily`);
  await page.waitForFunction(() => document.querySelector('#avatarRendererStatus')?.textContent.includes('及肩短发与日常外套已启用'), {}, { timeout: 90000 });
  assert.equal(await page.locator('#avatarRenderer').inputValue(), 'seed-daily');
  assert.match(await page.locator('#avatarRendererStatus').textContent(), /Seed-san 衍生造型.*VirtualCast, Inc\./);
  assert.equal(await page.locator('#avatarStage canvas').count(), 1);
  await page.evaluate(async () => { window.__avatar = (await import('/static/avatar/index.js')).avatar; });
  await diagnostics();
  assert.equal((await page.evaluate(() => window.__dailySnapshot())).name, 'Seed-san Daily Study');
  await page.screenshot({ path: `${out}/daily-chat.png`, fullPage: true });
  await page.locator('details.preferences > summary').click();
  await page.selectOption('#avatarRenderer', 'svg');
  await page.waitForFunction(() => document.querySelector('#avatarRendererStatus').textContent.includes('切回'));
  assert.equal(await page.locator('#avatarStage canvas').count(), 0);
  await page.selectOption('#avatarRenderer', 'seed-daily');
  await page.waitForFunction(() => document.querySelector('#avatarRendererStatus').textContent.includes('及肩短发与日常外套已启用'), {}, { timeout: 90000 });
  await page.locator('details.preferences > summary').click();
  await page.setViewportSize({ width: 430, height: 900 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile chat has no horizontal overflow');
  await page.screenshot({ path: `${out}/daily-chat-mobile.png`, fullPage: true });
  passed('chat query, attribution, SVG disposal/reload and mobile no horizontal overflow');
  assert.deepEqual(errors, [], 'no browser page errors');
  passed('no browser page errors');

  const summary = snapshot => ({ name: snapshot.name, arms: snapshot.arms, expressions: snapshot.expressions, finite: snapshot.finite, performance: snapshot.performance, speaking: snapshot.speaking, mouth: snapshot.mouth, reduced: snapshot.reduced, secondaryScale: snapshot.secondaryScale, drawCalls: snapshot.drawCalls });
  await writeFile(`${out}/daily-browser-results.json`, JSON.stringify({
    ok: true, assetRevision, checks, gestures: Object.fromEntries(Object.entries(gestures).map(([name, s]) => [name, summary(s)])),
    speech: summary(speech), blink: summary(blink), interrupted: summary(interrupted), reduced: summary(reduced), reducedArmRatio: ratio, errors,
    visualQuality: 'Screenshots require visual review; finite geometry and passing runtime checks do not establish absence of clothing intersections.',
    device: 'Headless Chromium with SwiftShader; not physical device performance validation.',
  }, null, 2));
  console.log(`Daily runtime browser regression passed: ${checks.length} check groups. Screenshots saved to ${out}; visual inspection remains separate.`);
  console.log(`Reviewed HTTP asset: ${assetRevision.byteLength} bytes; SHA-256 ${assetRevision.sha256}`);
} finally {
  await browser.close();
}
