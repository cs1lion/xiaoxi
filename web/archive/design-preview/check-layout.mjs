// 预览页布局体检：无视觉模型环境下的客观检查——横向溢出、输入框可见性、立绘与文字重叠。
import { chromium } from '@playwright/test';

const pages = ['baseline.html', 'dir-a-midnight.html', 'dir-b-ambient.html', 'dir-c-cockpit.html'];
const sizes = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'narrow', width: 390, height: 844 },
];
const FIG = ['.preview-figure', '.a-figure', '.b-figure', '.c-anchor svg'];
const TITLE = ['.welcome h2', '.a-welcome h2', '.b-welcome h2', '.c-welcome h2'];
const INPUT = ['#textInput', '.a-inputwrap input', '.b-inputwrap input', '.c-inputwrap input'];

const browser = await chromium.launch();
let failures = 0;
for (const size of sizes) {
  for (const page of pages) {
    const ctx = await browser.newContext({ viewport: { width: size.width, height: size.height } });
    const tab = await ctx.newPage();
    await tab.goto(`http://127.0.0.1:8123/design-preview/${page}`, { waitUntil: 'load' });
    await tab.waitForTimeout(400);
    const result = await tab.evaluate(([FIG, TITLE, INPUT]) => {
      const pick = (sels) => { for (const s of sels) { const el = document.querySelector(s); if (el) return el; } return null; };
      const rect = (el) => el ? el.getBoundingClientRect() : null;
      const overlap = (a, b) => a && b && !(a.right < b.left || b.right < a.left || a.bottom < b.top || b.bottom < a.top);
      const out = { overflowX: document.documentElement.scrollWidth - innerWidth };
      const fig = rect(pick(FIG)), title = rect(pick(TITLE)), input = rect(pick(INPUT));
      out.figVsTitle = overlap(fig, title);
      out.inputVisible = !!input && input.top >= 0 && input.bottom <= innerHeight && input.left >= 0 && input.right <= innerWidth;
      // 文字截断：标题是否被裁（scrollHeight 远超 clientHeight 的可见容器由 overflow 检查兜底，这里看标题自身）
      const h2 = pick(TITLE);
      out.titleClipped = h2 ? (h2.scrollWidth > h2.clientWidth + 1 || h2.scrollHeight > h2.clientHeight + 1) : 'no-title';
      return out;
    }, [FIG, TITLE, INPUT]);
    const bad = [];
    if (result.overflowX > 1) bad.push(`横向溢出 ${result.overflowX}px`);
    if (result.figVsTitle) bad.push('立绘与主标题重叠');
    if (!result.inputVisible) bad.push('输入框不在视口内');
    if (result.titleClipped === true) bad.push('主标题被截断');
    console.log(`${size.name.padEnd(7)} ${page.padEnd(22)} ${bad.length ? '✗ ' + bad.join('；') : '✓'}`);
    failures += bad.length;
    await ctx.close();
  }
}
await browser.close();
process.exit(failures ? 1 : 0);
