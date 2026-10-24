/**
 * 量四刻的区分度：同一场景、只换时相层，比平均 luma 与像素直方图距离。
 * 判据不是"看起来不同"，是两个相位之间有多少像素真的变了。
 */
import { chromium } from 'file:///D:/project/neurosama/node_modules/@playwright/test/index.mjs';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 720, height: 1480 } });
const target = process.argv[2] ?? './day-phase.html';
await page.goto(new URL(target, import.meta.url).href);
await page.waitForFunction(() => document.documentElement.dataset.ready === '1');

const stats = await page.evaluate(() => {
  const lum = (r, g, b) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const ids = ['dawn', 'noon', 'dusk', 'night'];
  const canvases = [...document.querySelectorAll('canvas')];
  const out = {};
  const data = {};
  canvases.forEach((c, i) => {
    const g = c.getContext('2d');
    const px = g.getImageData(0, 0, c.width, c.height).data;
    const vals = [];
    for (let k = 0; k < px.length; k += 4) {
      vals.push(lum(px[k], px[k + 1], px[k + 2]));
    }
    data[ids[i]] = vals;
    const sorted = [...vals].sort((a, b) => a - b);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    let r = 0, gg = 0, b = 0, sat = 0;
    for (let k = 0; k < px.length; k += 4) {
      r += px[k]; gg += px[k + 1]; b += px[k + 2];
      const mx = Math.max(px[k], px[k + 1], px[k + 2]);
      const mn = Math.min(px[k], px[k + 1], px[k + 2]);
      sat += mx === 0 ? 0 : (mx - mn) / mx;
    }
    const n = px.length / 4;
    out[ids[i]] = {
      mean: +mean.toFixed(2),
      p10: +sorted[Math.floor(sorted.length * 0.1)].toFixed(2),
      p50: +sorted[Math.floor(sorted.length * 0.5)].toFixed(2),
      p90: +sorted[Math.floor(sorted.length * 0.9)].toFixed(2),
      darkShare: +(vals.filter((v) => v < 60).length / vals.length).toFixed(3),
      rgb: [Math.round(r / n), Math.round(gg / n), Math.round(b / n)],
      warmth: +(((r - b) / n)).toFixed(1),
      sat: +(sat / n).toFixed(3),
    };
  });

  // 两两比较：有多少像素的 luma 差 ≥ 8（约 1 个可辨量化台阶）
  const pairs = [];
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const a = data[ids[i]];
      const b = data[ids[j]];
      let changed = 0;
      let sumAbs = 0;
      for (let k = 0; k < a.length; k += 1) {
        const d = Math.abs(a[k] - b[k]);
        sumAbs += d;
        if (d >= 8) changed += 1;
      }
      // 只看建筑与街道所在的中段（y 88..234），全屏草地会淹没局部差异。
      const W = 480;
      let regionChanged = 0;
      let regionTotal = 0;
      for (let y = 88; y < 234; y += 1) {
        for (let x = 0; x < W; x += 1) {
          const k = y * W + x;
          regionTotal += 1;
          if (Math.abs(a[k] - b[k]) >= 8) regionChanged += 1;
        }
      }
      pairs.push({
        pair: `${ids[i]}/${ids[j]}`,
        meanAbsDelta: +(sumAbs / a.length).toFixed(2),
        changedShare: +(changed / a.length).toFixed(3),
        regionChangedShare: +(regionChanged / regionTotal).toFixed(3),
      });
    }
  }
  return { perPhase: out, pairs };
});

console.log(JSON.stringify(stats, null, 2));
await browser.close();
