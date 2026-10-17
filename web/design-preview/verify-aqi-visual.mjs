// Aqi-Blocks / 三态胶囊 视觉升维取证：真实页面 + 代表性积木注入。
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
const OUT = 'web/design-preview/shots';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
for (const vp of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'narrow', width: 390, height: 844 }]) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
  const page = await ctx.newPage();
  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'load' });
  await page.waitForTimeout(5000);
  await page.evaluate(() => {
    const deck = document.getElementById('holoDeck');
    deck.hidden = false;
    const mk = (cls, kind, html) => {
      const c = document.createElement('article');
      c.className = `aqi-block ${cls}`;
      c.dataset.kind = kind;
      c.innerHTML = html;
      return c;
    };
    const blocks = [
      mk('aqi-choice', 'choice_pills', `<div class="aqi-block-head"><span class="aqi-block-glyph">◈</span><span class="aqi-block-title">这单派给谁？</span></div><div class="aqi-pills"><button class="aqi-pill">给小风</button><button class="aqi-pill">给小溪</button></div>`),
      mk('aqi-alert is-critical', 'alert_card', `<div class="aqi-block-head"><span class="aqi-block-glyph">⚠</span><span class="aqi-block-title">鬼打墙熔断</span></div><p class="aqi-line">回复第三轮仍在绕圈，已拦下。</p><div class="aqi-actions"><button class="aqi-action">查看判例</button></div>`),
      mk('aqi-swimlane', 'swimlane_strip', `<div class="aqi-block-head"><span class="aqi-block-glyph">≋</span><span class="aqi-block-title">手下正在干活</span></div><div class="aqi-lanes"><div class="aqi-lane" data-status="running"><span class="aqi-lane-agent">小风</span><span class="aqi-lane-title">整理周报素材</span><span class="aqi-lane-status">干活中</span></div><div class="aqi-lane" data-status="blocked"><span class="aqi-lane-agent">小溪</span><span class="aqi-lane-title">纪要措辞</span><span class="aqi-lane-status">等你拍板</span></div></div>`),
      mk('aqi-flame', 'metric_flame', `<div class="aqi-block-head"><span class="aqi-block-glyph">♨</span><span class="aqi-block-title">这一把烧掉的</span></div><div class="aqi-metrics"><div class="aqi-metric"><span class="aqi-metric-label">Token 合计</span><span class="aqi-metric-value">8640</span><span class="aqi-metric-bar"><span class="aqi-metric-fill" style="width:100%"></span></span></div><div class="aqi-metric"><span class="aqi-metric-label">耗时</span><span class="aqi-metric-value">42.6s</span><span class="aqi-metric-bar"><span class="aqi-metric-fill" style="width:38%"></span></span></div><div class="aqi-metric"><span class="aqi-metric-label">步骤</span><span class="aqi-metric-value">3/5</span><span class="aqi-metric-bar"><span class="aqi-metric-fill" style="width:60%"></span></span></div></div>`),
    ];
    blocks.forEach((b, i) => { b.style.setProperty('--aqi-i', String(i)); deck.appendChild(b); });
  });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/aqi-upgrade-${vp.name}.png` });
  const checks = await page.evaluate(() => {
    const cap = getComputedStyle(document.getElementById('presenceCapsule'));
    const card = document.querySelector('#holoDeck .aqi-block');
    const cs = getComputedStyle(card);
    const hit = (() => { const i = document.getElementById('textInput'); const r = i.getBoundingClientRect(); const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return h === i || i.contains(h); })();
    return {
      capBlur: cap.backdropFilter,
      capRadius: cap.borderRadius,
      cardBg: cs.backgroundImage.includes('radial-gradient'),
      cardRadius: cs.borderRadius,
      cardBlur: cs.backdropFilter,
      cardDelay: getComputedStyle(document.querySelectorAll('#holoDeck .aqi-block')[2]).animationDelay,
      overflowX: document.documentElement.scrollWidth - innerWidth,
      inputHit: hit,
    };
  });
  console.log(`[${vp.name}]`, JSON.stringify(checks, null, 1));
  await ctx.close();
}
await browser.close();
