import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const output = 'eval/avatar/calibration-ui';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 }, acceptDownloads: true });
const checks = [], errors = [];
page.on('pageerror', error => errors.push(error.message));
const check = (ok, label) => { assert.ok(ok, label); checks.push(label); };
const snapshot = () => page.evaluate(() => window.__calibrationSnapshot);
const enter = async () => {
  await page.locator('#calibrationEnter').click();
  await page.waitForFunction(() => window.__avatarCalibration?.active && !document.querySelector('#calibrationControls').hidden);
};
async function slider(id, value) {
  await page.locator('#' + id).evaluate((element, value) => {
    element.value = value; element.dispatchEvent(new Event('input', { bubbles: true }));
  }, String(value));
}
async function download(id, file) {
  const event = page.waitForEvent('download');
  await page.locator('#' + id).click();
  const item = await event;
  await item.saveAs(output + '/' + file);
  return readFile(output + '/' + file);
}
try {
  await page.goto('http://127.0.0.1:8000/static/avatar_lab.html?model=daily');
  await page.waitForFunction(() => window.__avatarReady && !document.querySelector('#calibrationEnter').disabled, {}, { timeout: 90000 });
  const restoredControls = await page.evaluate(() => Object.fromEntries(
    [...document.querySelectorAll('button,input,select')].filter(el => el.id && !el.closest('#calibrationPanel')).map(el => [el.id, el.disabled])));
  await page.locator('#scenario').click();
  await enter();
  const initial = await snapshot();
  check(initial.registration.compatible, 'initial fixed scene compatible');
  check(initial.viewport.width === initial.viewport.height, 'manual calibration uses square comparison canvas');
  check(await page.evaluate(() => window.__avatar._raf === 0 && !window.__avatar.lip.speaking), 'enter stops playback and pauses Avatar loop');
  check(await page.evaluate(() => [...document.querySelectorAll('button,input,select')].filter(el => !el.closest('#calibrationPanel')).every(el => el.disabled)), 'other controls locked while scene frozen');
  await page.waitForTimeout(1200);
  check((await snapshot()).fingerprint.id === initial.fingerprint.id, 'pending scenario timers cannot change frozen pose');
  await page.selectOption('#calibrationExpression', 'happy');
  const happy = await snapshot();
  await page.locator('#calibrationBaseline').check();
  const before = await snapshot();
  check(before.params.ParamEyeLOpen < happy.params.ParamEyeLOpen && before.params.ParamEyeLSmile > happy.params.ParamEyeLSmile, 'happy baseline and candidate preserve distinct values');
  await slider('calibrationIntensity', 0);
  const zero = await snapshot();
  const neutral = await page.evaluate(() => ({ ...window.__avatar.store.neutral }));
  check(zero.params.ParamEyeLOpen === neutral.ParamEyeLOpen && zero.params.ParamEyeLSmile === neutral.ParamEyeLSmile, 'baseline intensity also blends to neutral');
  await slider('calibrationIntensity', 100);
  await page.locator('#calibrationBaseline').uncheck();
  await slider('calibrationBlink', 100);
  const closed = await snapshot();
  check(closed.params.ParamEyeLOpen === 0 && closed.params.ParamEyeROpen === 0 && closed.nativeMorphs.eye_smile === 0, 'UI full blink uses native closure and removes smile stacking');
  await slider('calibrationBlink', 0);
  check((await snapshot()).params.ParamEyeLOpen === happy.params.ParamEyeLOpen, 'UI blink recovery is exact');
  await page.selectOption('#calibrationGaze', 'left');
  await slider('calibrationMouth', 65);
  const composed = await snapshot();
  check(composed.params.ParamEyeBallX === -.8 && composed.params.ParamMouthOpenY === .65, 'UI gaze and mouth compose with expression');
  check(composed.fingerprint.id === initial.fingerprint.id && JSON.stringify(composed.headAnchor) === JSON.stringify(initial.headAnchor), 'expression changes preserve camera and independent head anchor');
  await page.locator('#calibrationShowRegions').check();
  check(await page.locator('#calibrationRegions').isVisible(), 'Daily manual regions can be shown');
  const data = JSON.parse((await download('calibrationExport', 'snapshot.json')).toString('utf8'));
  check(data.snapshot.registration.compatible && data.manifest.model === 'daily' && data.snapshot.pose.mouthOpen === .65, 'JSON export includes manifest and current pose');
  const bytes = await download('calibrationImage', 'frame.png');
  check(bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])), 'PNG export has PNG signature');
  const image = await page.evaluate(async bytes => {
    const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: 'image/png' }));
    const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
    const context = canvas.getContext('2d'); context.drawImage(bitmap, 0, 0);
    const values = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let visible = 0; const colors = new Set();
    for (let i = 0; i < values.length; i += 64) { if (values[i+3]) visible++; colors.add(values[i]+','+values[i+1]+','+values[i+2]); }
    bitmap.close(); return { width: canvas.width, height: canvas.height, visible, colors: colors.size };
  }, [...bytes]);
  check(image.width === image.height && image.visible > 100 && image.colors > 100, 'exported WebGL PNG contains rendered character pixels');
  await page.locator('#viewport').screenshot({ path: output + '/panel-pose.png' });
  await page.locator('#calibrationExit').click();
  await page.waitForFunction(() => !window.__avatarCalibration && window.__avatar._raf > 0);
  check(await page.evaluate(expected => Object.entries(expected).every(([id, disabled]) => document.getElementById(id).disabled === disabled), restoredControls), 'exit restores original control capabilities');
  await enter();
  await page.setViewportSize({ width: 700, height: 900 });
  await page.waitForFunction(() => !window.__avatarCalibration && !document.querySelector('#viewport').classList.contains('calibrating'));
  const size = await page.evaluate(() => {
    const state = window.__avatar.svg._state, rect = state.renderer.domElement.getBoundingClientRect();
    return { aspect: state.camera.aspect, ratio: rect.width / rect.height, running: !!window.__avatar._raf };
  });
  check(Math.abs(size.aspect-size.ratio) < 1e-6 && size.running, 'resize invalidates session and restores current camera aspect and animation');
  await page.setViewportSize({ width: 430, height: 900 });
  await enter();
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile calibration has no horizontal overflow');
  await page.locator('#viewport').screenshot({ path: output + '/mobile-pose.png' });
  await page.locator('#calibrationExit').click();
  await page.locator('#sample').click();
  await page.waitForFunction(() => window.__avatarReady && !document.querySelector('#renderer').disabled && !document.querySelector('#calibrationEnter').disabled, {}, { timeout: 90000 });
  await enter();
  check(await page.locator('#calibrationBaseline').isDisabled() && await page.locator('#calibrationShowRegions').isDisabled(), 'other models cannot use Daily baselines or regions');
  await page.locator('#calibrationExit').click();
  await page.selectOption('#renderer', 'svg');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'svg');
  check(await page.locator('#calibrationEnter').isDisabled(), 'SVG switch disables VRM calibration panel');
  check(errors.length === 0, 'no browser page errors');
  await writeFile(output + '/results.json', JSON.stringify({ ok: true, checks, image, errors }, null, 2));
  console.log(`Calibration UI passed ${checks.length} checks`);
} finally { await browser.close(); }
