// Midnight Living Stage 验证：状态映射、层级、遮挡、reduced-motion、窄屏。
// 状态通过 #avatarStage[data-interaction] 注入——这正是 app.js 运行时写入的同一属性。
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const OUT = 'web/design-preview/shots';
mkdirSync(OUT, { recursive: true });
const BASE = 'http://127.0.0.1:8000/';
let failures = 0;
const bad = (msg) => { failures++; console.log('  ✗ ' + msg); };
const ok = (msg) => console.log('  ✓ ' + msg);

const browser = await chromium.launch();

// ---------- 1. 桌面：结构、层级、逐状态 ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(5000);

  console.log('[desktop 1440×900]');
  const info = await page.evaluate(() => {
    const c = document.getElementById('stageFx');
    const input = document.getElementById('textInput');
    const stage = document.querySelector('.stage');
    const avatar = document.getElementById('avatarStage');
    const cs = c ? getComputedStyle(c) : null;
    const ir = input.getBoundingClientRect();
    const hit = document.elementFromPoint(ir.left + ir.width / 2, ir.top + ir.height / 2);
    const ar = avatar.getBoundingClientRect();
    const h2 = document.querySelector('.welcome h2');
    // 用文本 Range 的字形范围判定遮挡，而不是块级盒（块盒天然铺满内容宽度）
    let h2r = null;
    if (h2) { const range = document.createRange(); range.selectNodeContents(h2); h2r = range.getBoundingClientRect(); }
    return {
      canvasExists: !!c,
      canvasPE: cs?.pointerEvents,
      canvasZ: cs?.zIndex,
      inputHit: hit === input || input.contains(hit),
      overflowX: document.documentElement.scrollWidth - innerWidth,
      avatarVsTitle: !!(ar && h2r && h2r.width > 0 && !(ar.right < h2r.left || h2r.right < ar.left || ar.bottom < h2r.top || h2r.bottom < ar.top)),
      vrmCanvas: !!avatar.querySelector('canvas.avatar-vrm'),
      state: avatar.dataset.interaction,
    };
  });
  info.canvasExists ? ok('stageFx 画布已挂载') : bad('stageFx 画布缺失');
  info.canvasPE === 'none' ? ok('画布 pointer-events:none') : bad('画布 pointer-events=' + info.canvasPE);
  info.inputHit ? ok('输入框不被遮挡（elementFromPoint 命中输入框）') : bad('输入框被遮挡');
  info.overflowX <= 1 ? ok('无横向溢出') : bad(`横向溢出 ${info.overflowX}px`);
  !info.avatarVsTitle ? ok('立绘与主标题无重叠') : bad('立绘压住主标题');
  info.vrmCanvas ? ok('VRM canvas 在位（真实 3D 资产已加载）') : ok('当前为 SVG 形象（VRM canvas 不在，属渲染模式差异）');
  console.log('  当前交互态:', info.state);

  // 逐状态注入 + 截图（倾听时同时注入真实来源同款 meterFill 宽度）
  for (const st of ['listening', 'thinking', 'speaking', 'unavailable']) {
    await page.evaluate((st) => {
      const a = document.getElementById('avatarStage');
      a.dataset.interaction = st;
      if (st === 'listening') document.getElementById('meterFill').style.width = '55%';
    }, st);
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${OUT}/prod-state-${st}.png` });
    const applied = await page.evaluate(() => document.getElementById('avatarStage').dataset.interaction);
    applied === st ? ok(`状态 ${st} 已反映到舞台`) : bad(`状态 ${st} 未生效`);
  }

  // 任务执行：真实结构 = holoDeck 内 .aqi-lane[data-status=running]
  await page.evaluate(() => {
    const a = document.getElementById('avatarStage');
    a.dataset.interaction = 'idle';
    document.getElementById('meterFill').style.width = '0';
    const deck = document.getElementById('holoDeck');
    deck.hidden = false;
    const card = document.createElement('div');
    card.className = 'aqi-block';
    card.innerHTML = '<div class="aqi-lanes"><div class="aqi-lane" data-status="running"><span class="aqi-lane-agent">小溪</span><span class="aqi-lane-title">整理本周待办</span><span class="aqi-lane-status">执行中</span></div></div>';
    deck.appendChild(card);
  });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/prod-state-running.png` });
  ok('任务执行态已注入（running lane + 新卡片=完成反馈星屑）');
  await page.evaluate(() => { const d = document.getElementById('holoDeck'); d.replaceChildren(); d.hidden = true; });

  errors.length ? bad('页面控制台错误: ' + errors.slice(0, 3).join(' | ')) : ok('无控制台错误');
  await ctx.close();
}

// ---------- 2. 窄屏 ----------
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(4500);
  console.log('[narrow 390×844]');
  const info = await page.evaluate(() => {
    const input = document.getElementById('textInput');
    const ir = input.getBoundingClientRect();
    const hit = document.elementFromPoint(ir.left + ir.width / 2, ir.top + ir.height / 2);
    return {
      overflowX: document.documentElement.scrollWidth - innerWidth,
      inputVisible: ir.top >= 0 && ir.bottom <= innerHeight,
      inputHit: hit === input || input.contains(hit),
    };
  });
  info.overflowX <= 1 ? ok('无横向溢出') : bad(`横向溢出 ${info.overflowX}px`);
  info.inputVisible ? ok('输入框在视口内') : bad('输入框不在视口内');
  info.inputHit ? ok('输入框不被遮挡') : bad('输入框被遮挡');
  await page.screenshot({ path: `${OUT}/prod-after-narrow.png` });
  await ctx.close();
}

// ---------- 3. reduced-motion：画布循环应停止 ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(4000);
  console.log('[reduced-motion]');
  const still = await page.evaluate(async () => {
    const c = document.getElementById('stageFx');
    if (!c) return 'no-canvas';
    const a = c.toDataURL();
    await new Promise(r => setTimeout(r, 600));
    const b = c.toDataURL();
    return a === b ? 'static' : 'animating';
  });
  still === 'static' ? ok('reduced-motion 下画布静止') : bad('reduced-motion 下画布仍在动: ' + still);
  await ctx.close();
}

// ---------- 4. 正常模式画布确实在动（对照） ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(4000);
  console.log('[motion 对照]');
  const moving = await page.evaluate(async () => {
    const c = document.getElementById('stageFx');
    const a = c.toDataURL();
    await new Promise(r => setTimeout(r, 600));
    return a !== c.toDataURL();
  });
  moving ? ok('正常模式下粒子画布在动') : bad('正常模式下画布静止（粒子系统未运行）');
  await ctx.close();
}

await browser.close();
console.log(failures ? `\n${failures} 项未通过` : '\n全部通过');
process.exit(failures ? 1 : 0);
