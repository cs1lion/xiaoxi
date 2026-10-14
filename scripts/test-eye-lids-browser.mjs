/* V3 regression: actual shipped asset, frozen pose and ordinary playback.
 * No screenshot threshold is treated as artistic approval; masks/contours are
 * validated separately by the calibration evidence pipeline. */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const output = `eval/avatar/eye-lids-ui/${new Date().toISOString().replace(/[:.]/g, '-')}`;
await mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true, args: process.platform === 'win32' ? ['--use-angle=d3d11'] : []});
const page = await browser.newPage({viewport: {width: 1280, height: 920}, acceptDownloads: true});
const checks = [], errors = [], evidence = {};
page.on('pageerror', error => errors.push(error.message));
const check = (value, label) => { assert.ok(value, label); checks.push(label); };
const near = (actual, expected) => Number.isFinite(actual) && Math.abs(actual - expected) < 1e-6;
const gate = value => Math.max(0, Math.min(1, value));
const snapshot = () => page.evaluate(() => window.__calibrationSnapshot);
async function slider(id, value) {
  await page.locator('#' + id).evaluate((el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles: true})); }, String(value));
}
async function ready(profile) {
  await page.waitForFunction(profile => window.__avatarReady && window.__avatar?.svg.getEyeCapabilities?.().profile === profile,
    profile, {timeout: 90000});
}
async function setManual({lowerLeft = 0, lowerRight = 0, smileLeft = 0, smileRight = 0}) {
  for (const [side, value] of Object.entries({LowerLeft: lowerLeft, LowerRight: lowerRight, SmileLeft: smileLeft, SmileRight: smileRight}))
    await slider('calibration' + side, value);
}

try {
  await page.goto(origin + '/static/avatar_lab.html?model=daily-lids'); await ready('seed-lids');
  const capabilities = await page.evaluate(() => window.__avatar.svg.getEyeCapabilities());
  evidence.capabilities = capabilities;
  check(capabilities.channels.lowerLid.available && capabilities.channels.lowerLid.mode === 'independent', 'real V3 asset advertises two independent lower lids');
  check(capabilities.channels.smile.mode === 'independent', 'real V3 asset advertises two independent smile shapes');
  check(await page.evaluate(() => window.__avatar.eyeExpression.profile === 'seed-lids'), 'normal playback selects enhanced presets for V3');
  const controlsBefore = await page.locator('#dailyLids,#eyeFocus,#performanceProbeStart,#eyeBlink').evaluateAll(els => els.map(el => [el.id, el.disabled]));
  await page.locator('#calibrationEnter').click(); await page.waitForFunction(() => window.__avatarCalibration?.active);
  const initial = await snapshot();
  await page.selectOption('#calibrationExpression', 'happy');
  const enhancedHappy = await snapshot();
  check(enhancedHappy.params.ParamEyeLLower > 0 && enhancedHappy.params.ParamEyeRLower > 0, 'enhanced happy preset engages actual lower-lid channels');
  await page.locator('#calibrationManualLids').check();
  await setManual({lowerLeft: 60, smileLeft: 40});
  const viewerLeft = await snapshot();
  check(viewerLeft.params.ParamEyeRLower === .6 && viewerLeft.params.ParamEyeLLower === 0, 'viewer-left manual lower control maps to character-right parameter');
  check(near(viewerLeft.nativeMorphs.neuro_lower_lid_raise_R, .6 * gate(viewerLeft.params.ParamEyeROpen)) && viewerLeft.nativeMorphs.neuro_lower_lid_raise_L === 0, 'viewer-left lower target is isolated from the opposite eye');
  check(near(viewerLeft.nativeMorphs.neuro_eye_smile_R, .4 * gate(viewerLeft.params.ParamEyeROpen)) && viewerLeft.nativeMorphs.neuro_eye_smile_L === 0, 'viewer-left smile target is isolated from the opposite eye');
  check(viewerLeft.nativeMorphs.eye_smile === 0, 'enhanced smile never stacks the old bilateral smile');
  await setManual({lowerRight: 60, smileRight: 40});
  const viewerRight = await snapshot();
  check(near(viewerRight.nativeMorphs.neuro_lower_lid_raise_L, .6 * gate(viewerRight.params.ParamEyeLOpen)) && viewerRight.nativeMorphs.neuro_lower_lid_raise_R === 0, 'viewer-right controls drive character-left targets');
  check(near(viewerRight.nativeMorphs.neuro_eye_smile_L, .4 * gate(viewerRight.params.ParamEyeLOpen)) && viewerRight.nativeMorphs.neuro_eye_smile_R === 0, 'viewer-right smile is independent');
  await setManual({lowerLeft: 60, lowerRight: 30, smileLeft: 40, smileRight: 20});
  const open = await snapshot();
  await slider('calibrationBlink', 50); const halfBlink = await snapshot();
  check(near(halfBlink.nativeMorphs.neuro_lower_lid_raise_L, .3 * gate(halfBlink.params.ParamEyeLOpen))
    && near(halfBlink.nativeMorphs.neuro_lower_lid_raise_R, .6 * gate(halfBlink.params.ParamEyeROpen)), 'half blink fades each lower lid against its own openness');
  await slider('calibrationBlink', 100); await slider('calibrationMouth', 65); await page.selectOption('#calibrationGaze', 'right');
  const closed = await snapshot();
  check(['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R', 'neuro_eye_smile_L', 'neuro_eye_smile_R'].every(name => closed.nativeMorphs[name] === 0), 'full blink suppresses both lower lids and both smiles');
  check(closed.expressionWeights.blinkLeft === 1 && closed.expressionWeights.blinkRight === 1, 'standard VRM blink owns complete closure');
  check(closed.params.ParamMouthOpenY === .65 && closed.params.ParamEyeBallX === .8 && closed.nativeMorphs.lip_a > 0, 'lower lids coexist with actual speech and gaze routing');
  await page.locator('#viewport').screenshot({path: output + '/full-blink-gaze-mouth.png'});
  await slider('calibrationBlink', 0); const reopened = await snapshot();
  check(['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R', 'neuro_eye_smile_L', 'neuro_eye_smile_R']
    .every(name => near(reopened.nativeMorphs[name], open.nativeMorphs[name])), 'blink release restores the exact lower-lid and smile intent');
  await page.locator('#calibrationBaseline').check(); const native = await snapshot();
  check(native.pose.lidMode === 'native', 'native baseline chooses old lid deformation independently from new targets');
  check(['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R', 'neuro_eye_smile_L', 'neuro_eye_smile_R'].every(name => native.nativeMorphs[name] === 0)
    && native.nativeMorphs.eye_smile > 0, 'native mode clears added channels and restores original bilateral smile');
  await page.locator('#calibrationBaseline').uncheck(); const restored = await snapshot();
  check(restored.pose.lidMode === 'calibrated' && restored.nativeMorphs.eye_smile === 0
    && ['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R', 'neuro_eye_smile_L', 'neuro_eye_smile_R']
      .every(name => near(restored.nativeMorphs[name], reopened.nativeMorphs[name])), 'returning from native mode restores new targets with no stale stacking');
  check(restored.fingerprint.id === initial.fingerprint.id && restored.registration.compatible, 'lid-mode comparisons retain fixed camera and head registration');
  await page.locator('#viewport').screenshot({path: output + '/independent-lids.png'});
  const downloadEvent = page.waitForEvent('download'); await page.locator('#calibrationExport').click();
  await (await downloadEvent).saveAs(output + '/calibration.json');
  const exported = JSON.parse(await readFile(output + '/calibration.json', 'utf8'));
  check(exported.snapshot.pose.lidMode === 'calibrated' && exported.snapshot.nativeMorphs.neuro_lower_lid_raise_R > 0, 'calibration JSON preserves deformation mode and actual weights');
  await setManual({}); const zeroed = await snapshot();
  check(['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R', 'neuro_eye_smile_L', 'neuro_eye_smile_R'].every(name => zeroed.nativeMorphs[name] === 0), 'manual zero restores neutral lids with no residual targets');
  await page.locator('#calibrationManualLids').uncheck();
  check(await page.locator('#calibrationLowerLeft').isDisabled() && await page.locator('#calibrationSmileRight').isDisabled(), 'leaving manual mode disables isolated controls');
  evidence.poses = {initial, enhancedHappy, viewerLeft, viewerRight, open, halfBlink, closed, reopened, native, restored, zeroed};
  await page.locator('#calibrationExit').click(); await page.waitForFunction(() => !window.__avatarCalibration && window.__avatar._raf > 0);
  check(await page.evaluate(() => !window.__avatar.svg.eyeLidMode || window.__avatar.svg.eyeLidMode === 'calibrated'), 'calibration exit restores ordinary lid routing and playback');
  check(JSON.stringify(await page.locator('#dailyLids,#eyeFocus,#performanceProbeStart,#eyeBlink').evaluateAll(els => els.map(el => [el.id, el.disabled]))) === JSON.stringify(controlsBefore), 'calibration exit restores previous UI enable states');
  await page.locator('[data-eye-expression="happy"]').click();
  await page.waitForFunction(() => window.__avatar.eyeExpression.sample().values.ParamEyeLLower > 0);
  await page.locator('#eyeInherit').click();
  await page.waitForFunction(() => !window.__avatar.eyeExpression.sample().active);
  check(await page.evaluate(() => window.__avatar.eyeExpression.sample().values.ParamEyeLLower === 0
    && window.__avatar.eyeExpression.sample().values.ParamEyeRLower === 0), 'releasing the enhanced expression returns lower-lid intent to zero');
  await page.evaluate(() => window.__avatar.setEmotion('得意'));
  await page.waitForFunction(() => {
    const avatar = window.__avatar;
    if (avatar.eyeExpression.sample().active || !(avatar.eyeFrame?.params.ParamEyeLLower > .01)) return false;
    let active = false;
    avatar.svg._state.vrm.scene.traverse(mesh => {
      const index = mesh.morphTargetDictionary?.neuro_lower_lid_raise_L;
      if (index !== undefined && mesh.morphTargetInfluences[index] > .01) active = true;
    });
    return active;
  }, {}, {timeout: 15000});
  evidence.naturalEmotion = await page.evaluate(() => ({params: {...window.__avatar.eyeFrame.params},
    preview: window.__avatar.eyeExpression.sample(), emotion: window.__avatar.emotion.current}));
  check(evidence.naturalEmotion.preview.active === false && evidence.naturalEmotion.params.ParamEyeLLower > 0,
    'ordinary configured chat emotion drives an actual lower lid while preview is released');
  await page.evaluate(() => window.__avatar.svg.setEyeLidMode('native'));
  await page.waitForFunction(() => window.__avatar.eyeExpression.profile === 'native'
    && window.__avatar.store.target.ParamEyeLLower === 0 && window.__avatar.store.target.ParamEyeRLower === 0);
  check(await page.evaluate(() => {
    let zero = true;
    window.__avatar.svg._state.vrm.scene.traverse(mesh => {
      for (const name of ['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R']) {
        const index = mesh.morphTargetDictionary?.[name];
        if (index !== undefined && mesh.morphTargetInfluences[index] !== 0) zero = false;
      }
    });
    return zero;
  }), 'normal native mode retires added lower targets without calibration');
  await page.evaluate(() => { window.__avatar.svg.setEyeLidMode('calibrated'); window.__avatar.setEmotion('base'); });
  await page.waitForFunction(() => window.__avatar.eyeExpression.profile === 'seed-lids'
    && window.__avatar.store.target.ParamEyeLLower < 1e-4 && window.__avatar.store.target.ParamEyeRLower < 1e-4);
  check(await page.evaluate(() => !window.__avatar.eyeExpression.sample().active), 'neutral emotion releases lower-lid intent with normal playback still active');
  for (const [button, profile] of [['daily', 'seed'], ['dailyEyes', 'seed-eyes']]) {
    await page.locator('#' + button).click(); await ready(profile);
    check(await page.evaluate(() => !window.__avatar.svg.getEyeCapabilities().channels.lowerLid.available && window.__avatar.eyeExpression.profile === 'native'), `${profile} fallback reports absent lower lids and restores original presets`);
  }
  await page.locator('#dailyLids').click(); await ready('seed-lids');
  check(await page.evaluate(() => window.__avatar.eyeExpression.profile === 'seed-lids'), 'switching back selects enhanced presets again');
  await page.setViewportSize({width: 430, height: 900}); await page.locator('#calibrationEnter').click();
  await page.waitForFunction(() => window.__avatarCalibration?.active);
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'independent lid controls fit a mobile viewport');
  await page.locator('#calibrationExit').click();

  await page.setViewportSize({width: 1280, height: 920});
  await page.goto(origin + '/?avatar=seed-daily-lids');
  await page.waitForFunction(() => document.querySelector('#avatarRenderer')?.value === 'seed-daily-lids'
    && document.querySelector('#avatarRendererStatus')?.textContent.includes('已启用'), {}, {timeout: 90000});
  await page.evaluate(async () => { window.__chatTestAvatar = (await import('/static/avatar/index.js')).avatar; });
  check(await page.locator('#avatarStage canvas').count() === 1 && await page.evaluate(() => window.__chatTestAvatar.svg.getEyeCapabilities().profile === 'seed-lids'), 'chat URL entry mounts the actual lid candidate');
  await page.locator('details.preferences > summary').click();
  await page.evaluate(() => window.__chatTestAvatar.setEyeExpression('happy'));
  await page.waitForFunction(() => window.__chatTestAvatar.eyeExpression.sample().values.ParamEyeLLower > 0);
  await page.selectOption('#avatarRenderer', 'seed-daily-eyes');
  await page.waitForFunction(() => document.querySelector('#avatarRenderer')?.disabled === false
    && window.__chatTestAvatar.svg.getEyeCapabilities().profile === 'seed-eyes');
  check(await page.evaluate(() => window.__chatTestAvatar.eyeExpression.profile === 'native'), 'chat switching from V3 to V2 releases enhanced presets');
  await page.selectOption('#avatarRenderer', 'seed-daily-lids');
  await page.waitForFunction(() => document.querySelector('#avatarRenderer')?.disabled === false
    && window.__chatTestAvatar.svg.getEyeCapabilities().profile === 'seed-lids');
  check(await page.evaluate(() => window.__chatTestAvatar.eyeExpression.profile === 'seed-lids' && window.__chatTestAvatar.eyeExpression.name === 'happy'), 'chat switching back preserves expression selection with enhanced profile');
  check(errors.length === 0, `no browser exceptions: ${errors.join('; ')}`);
  await writeFile(output + '/results.json', JSON.stringify({ok: true, checks, errors, evidence}, null, 2));
  console.log(`${checks.length} independent lower-lid browser checks passed; ${output}`);
} catch (error) {
  await writeFile(output + '/results.json', JSON.stringify({ok: false, checks, errors, failure: error.message, evidence}, null, 2));
  throw error;
} finally { await browser.close(); }
