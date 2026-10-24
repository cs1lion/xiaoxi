/* 抽屉「陪伴体验」入口（N14）浏览器契约：只验"入口把她带到既有区域"，
 * 不代选模式、不代应用、不发一帧。真实首页 DOM + 离线路由 + 受管 WebSocket，
 * 不起 8000、不请求模型、不写用户数据。
 *
 * 覆盖五条：
 *   ① 抽屉导航切到说明视图：只切视图，零 config 帧，面板仍收起
 *   ② 「打开陪伴体验面板」：收起抽屉、展开并聚焦侧栏面板、状态文本不被改写
 *   ③ 移动端 390x844 走同一条路：自动拉开侧栏浮层，聊天区宽度不变
 *   ④ 主舞台零新增按钮：#modeExperience 仍是只读 span，.stage 内无新落点
 *   ⑤ Escape 收起侧栏并归还焦点，全程无未捕获页面错误
 */
import { chromium } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const origin = 'http://experience-entry.test';
const out = 'eval/ui/experience-entry';
await mkdir(out, { recursive: true });

const browser = await chromium.launch({ headless: true,
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
const errors = [];
const checks = [];
const pass = (name) => { checks.push(name); console.log('ok -', name); };

const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(10000);
page.on('pageerror', error => errors.push(error.message));

await page.route(`${origin}/**`, async route => {
  const url = new URL(route.request().url());
  // 形象运行时整块剥掉：夹具不验 VRM，只验入口行为与几何。
  if (url.pathname === '/static/avatar/index.js') {
    return route.fulfill({ contentType: 'text/javascript', body: `
      const noop = () => {};
      export const avatar = new Proxy({ emotion: {set: noop}, rendererKind:'svg', onMessage: noop,
        stopSpeaking: noop }, { get: (o,k) => (k in o ? o[k] : noop) });
      export const boot = async () => {};
    ` });
  }
  if (url.pathname.startsWith('/api/')) {
    const data = url.pathname === '/api/persona'
      ? { name: '阿栖', tagline: '离线测试', voices: ['base'], default_voice: 'base' }
      : { count: 0, items: [] };
    return route.fulfill({ json: data });
  }
  const relative = url.pathname === '/' ? 'web/index.html' : 'web/' + url.pathname.replace(/^\/static\//, '');
  const absolute = path.resolve(root, relative);
  if (!absolute.startsWith(root + path.sep)) return route.abort();
  try {
    const body = await readFile(absolute);
    const contentType = absolute.endsWith('.js') || absolute.endsWith('.mjs') ? 'text/javascript'
      : absolute.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({ body, contentType });
  } catch { return route.fulfill({ status: 404, body: 'not found' }); }
});

// 受管 WebSocket：全权接管握手，发出的每一帧都记进 __harness.sent。
await page.addInitScript(() => {
  const h = window.__harness = { sent: [] };
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() {
      h.ws = this; this.readyState = 0;
      setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: 'ready', sample_rate: 16000, session_id: 'exp-entry', persona: '阿栖' }); }, 0);
    }
    send(data) { h.sent.push(JSON.parse(data)); }
    emit(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
});

const sent = () => page.evaluate(() => window.__harness.sent);
const configFrames = async () => (await sent()).filter(frame => frame.type === 'config').length;
const statusText = () => page.evaluate(() => document.getElementById('experienceStatus').textContent);
const online = () => page.waitForFunction(() => document.querySelector('#status')?.textContent === '在线');

try {
  await page.goto(origin);
  await online();

  // ---- ① 抽屉导航切到说明视图：只切视图，不应用任何模式 ----
  const configsBefore = await configFrames();
  await page.locator('#drawerToggle').click();
  assert.equal(await page.evaluate(() => document.getElementById('workDrawer').hidden), false, '抽屉展开');
  await page.locator('#drawerExperience').click();
  assert.equal(await page.evaluate(() => document.getElementById('drawerViewExperience').hidden), false, '陪伴体验说明视图显示');
  assert.equal(await page.evaluate(() => document.getElementById('drawerViewWriting').hidden), true, '默认一起写视图让位');
  assert.equal(await page.evaluate(() => document.getElementById('drawerExperience').getAttribute('aria-pressed')), 'true', '导航按钮呈选中态');
  assert.equal(await page.evaluate(() => document.querySelector('.experience-panel').open), false, '侧栏陪伴面板仍是收起的 details');
  assert.equal(await configFrames(), configsBefore, '切到说明视图没有发出任何 config 帧');
  pass('抽屉导航切到说明视图：零 config 帧、不应用任何模式');

  // ---- ② 「打开陪伴体验面板」：收起抽屉、展开并聚焦侧栏面板 ----
  // 面板状态文本在挂载时被 importSelection 的"无待导入原句"提示覆写，
  // 所以这里断言"入口不改写状态"（且仍处于未生效状态），而不是某一句固定文案。
  const statusBefore = await statusText();
  await page.locator('#drawerExperienceOpen').click();
  assert.equal(await page.evaluate(() => document.getElementById('workDrawer').hidden), true, '抽屉收起');
  assert.equal(await page.evaluate(() => document.querySelector('.experience-panel').open), true, '侧栏陪伴面板展开');
  assert.equal(await page.evaluate(() => document.activeElement === document.querySelector('.experience-panel > summary')), true, '焦点落在面板摘要上');
  assert.equal(await statusText(), statusBefore, '入口没有改写面板状态文本');
  assert.match(statusBefore, /尚未应用|没有待导入/, '面板仍处于尚未生效状态');
  assert.equal(await configFrames(), configsBefore, '打开面板没有发出任何 config 帧');
  assert.equal((await sent()).filter(frame => frame.type === 'text').length, 0, '入口没有代发任何消息');
  pass(`打开陪伴体验面板：收起抽屉、展开聚焦侧栏面板、零 config 帧（状态文本保持“${statusBefore}”）`);

  // ---- ③ 移动端 390x844 走同一条路 ----
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await online();
  const stageBefore = await page.evaluate(() => document.querySelector('.stage').getBoundingClientRect().width);
  await page.locator('#drawerToggle').click();
  await page.locator('#drawerExperience').click();
  await page.locator('#drawerExperienceOpen').click();
  assert.equal(await page.evaluate(() => document.getElementById('sidePanel').classList.contains('is-open')), true, '移动端自动拉开侧栏浮层');
  assert.equal(await page.evaluate(() => document.getElementById('sidebarToggle').getAttribute('aria-expanded')), 'true', '侧栏开关状态同步');
  assert.equal(await page.locator('.experience-panel').isVisible(), true, '陪伴面板在侧栏浮层里可见');
  const stageAfter = await page.evaluate(() => document.querySelector('.stage').getBoundingClientRect().width);
  assert.equal(stageAfter, stageBefore, '侧栏浮层不挤压聊天区宽度');
  pass('移动端同一条路：自动拉开侧栏浮层，聊天区宽度不变');

  // ---- ④ 主舞台零新增按钮，模式片仍是只读 span ----
  assert.equal(await page.evaluate(() => document.getElementById('modeExperience').tagName), 'SPAN', '模式片仍是只读 span，没有变成主舞台按钮');
  assert.equal(await page.evaluate(() => document.querySelectorAll('.stage #drawerExperience, .stage #drawerExperienceOpen').length), 0, '主舞台里没有陪伴体验的新 DOM 落点');
  pass('主舞台零新增按钮：模式片仍为只读 span');

  // ---- ⑤ Escape 收起侧栏并归还焦点，全程无页面错误 ----
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.getElementById('sidePanel').classList.contains('is-open')), false, 'Escape 收起侧栏浮层');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'sidebarToggle', '焦点归还侧栏开关');
  assert.deepEqual(errors, [], `全流程无未捕获页面错误：${errors.join('; ')}`);
  pass('Escape 收起侧栏并归还焦点，全程无未捕获页面错误');

  await page.screenshot({ path: `${out}/experience-entry-mobile.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(origin);
  await online();
  await page.locator('#drawerToggle').click();
  await page.locator('#drawerExperience').click();
  await page.screenshot({ path: `${out}/experience-entry-desktop.png` });

  console.log(JSON.stringify({ result: 'PASS', scope: 'Offline Playwright; real homepage DOM, synthetic routes and WebSocket; no live server, model, DB or mic', checks }, null, 2));
} finally {
  await browser.close();
}
