import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const assert = (ok, message) => { if (!ok) throw new Error(message); };
try {
  await page.goto('http://127.0.0.1:8000/static/avatar_lab.html');
  await page.waitForFunction(() => window.__avatar?.rendererKind === 'vrm', { }, { timeout: 90000 });
  assert(await page.locator('#avatarStage canvas').count() === 1, 'one VRM canvas');
  await page.getByRole('button', { name: '得意', exact:true }).click();
  await page.getByRole('button', { name: '静音口型演示' }).click();
  await page.waitForFunction(() => window.__avatar.store.current.ParamMouthOpenY > .1);
  await page.getByRole('button', { name: '打断 / 闭嘴' }).click();
  assert(await page.evaluate(() => !window.__avatar.lip.speaking), 'interrupt stops lips');
  await mkdir('eval/avatar', { recursive:true });
  await page.screenshot({ path: 'eval/avatar/vrm-lab.png', fullPage:true });
  const pose = await page.evaluate(() => {
    const s = window.__avatar.svg._state;
    return { camera:s.camera.position.toArray(), head:s.bones.head.node.getWorldPosition(s.target.position.clone()).toArray(), expressions:Object.keys(s.expressions), drawCalls:s.renderer.info.render.calls };
  });
  await page.selectOption('#renderer', 'svg');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'svg');
  assert(await page.locator('#avatarStage canvas').count() === 0, 'canvas disposed on SVG switch');
  await page.selectOption('#renderer','vrm');
  await page.waitForFunction(() => window.__avatar.rendererKind === 'vrm');
  const fallback = await page.evaluate(async () => {
    try { await window.__avatar.setRenderer('vrm','/static/models/missing.vrm'); return false; }
    catch { return window.__avatar.rendererKind === 'vrm' && document.querySelectorAll('#avatarStage canvas').length === 1; }
  });
  assert(fallback, 'failed load preserves active renderer');
  await page.setViewportSize({ width: 430, height: 900 });
  await page.screenshot({ path:'eval/avatar/vrm-mobile.png', fullPage:true });
  await page.goto('http://127.0.0.1:8000/?avatar=vrm');
  await page.waitForFunction(() => document.querySelector('#avatarRendererStatus')?.textContent.includes('已启用'), {}, {timeout:90000});
  await page.screenshot({path:'eval/avatar/vrm-chat.png',fullPage:true});
  assert(errors.length === 0, `browser errors: ${errors.join('; ')}`);
  await writeFile('eval/avatar/browser-results.json', JSON.stringify({ok:true,checks:['VRM mounted','emotion control','lip clock','interrupt','SVG disposal','VRM reload','failed load preserved','mobile resize','chat integration','no page errors'],pose,errors},null,2));
  console.log('Avatar browser verification passed', JSON.stringify(pose));
} finally { await browser.close(); }
