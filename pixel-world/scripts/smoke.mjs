/* 神谕闭环的浏览器回归。
 *
 * 验的不是"能编译"，而是用户在屏幕上能看到的四件事：
 *   1. 开局小人在祭坛边自己走动
 *   2. 一句话神谕 → 行礼 → 走进对应铺子 → 铺子开工
 *   3. 交付 → 人出现在门口 → 回到祭坛
 *   4. 认不出的话不让世界动
 *
 * 跑法：
 *   1. npm run dev（端口 5188）
 *   2. node scripts/smoke.mjs
 * 浏览器用主仓库根目录的 Playwright；原型目录不带这份依赖。
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';

const URL_BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/';
const OUT = 'eval/screens';

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1010, height: 940 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

const checks = [];
const check = (ok, label) => {
  assert.ok(ok, label);
  checks.push(label);
};

const state = () => page.evaluate(() => window.Oracle.view());
const waitFor = async (predicate, label, timeout = 25000) => {
  const started = Date.now();
  for (;;) {
    const snapshot = await state();
    if (predicate(snapshot)) {
      checks.push(label);
      return snapshot;
    }
    if (Date.now() - started > timeout) {
      throw new Error(`超时：${label}\n最后状态 ${JSON.stringify(snapshot.villager)}`);
    }
    await page.waitForTimeout(120);
  }
};

try {
  await mkdir(OUT, { recursive: true });
  await page.goto(URL_BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 15000 });
  check(errors.length === 0, '页面加载无 JS 报错');

  const opening = await state();
  check(opening.buildings.length === 3, '世界里摆了三栋铺子');
  check(opening.villager.mode === 'idle', '开局她在闲逛');

  // 她自己会动，不是屏保。
  const before = opening.villager.pos;
  await page.waitForTimeout(3200);
  const wandering = await state();
  const moved = Math.hypot(
    wandering.villager.pos.x - before.x,
    wandering.villager.pos.y - before.y,
  );
  check(moved > 0.2 || wandering.villager.mode !== 'idle', '没人管的时候她会自己走动');

  // 认不出的句子不让世界动。
  const unknown = await page.evaluate(() => window.Oracle.say('今天天气不错'));
  check(unknown.ok === false, '认不出的输入被拒');
  check((await state()).villager.mode !== 'saluting', '认不出的输入没有触发神谕');

  // 一句代码活：行礼 → 赶路 → 开工。
  await page.fill('#utterance', '帮我写个爬虫脚本');
  await page.click('button[type="submit"]');
  // 界面不再抢答：文案来自世界结算后的回执。
  await page.waitForFunction(
    () => (document.querySelector('#reply')?.textContent ?? '').includes('神谕已送达'),
    null,
    { timeout: 5000 },
  );
  check(true, '界面回了世界结算后的神谕回执');
  const saluting = await waitFor((s) => s.villager.mode === 'saluting', '她停下手里的事行礼');
  check(saluting.villager.bubble !== null, '行礼时头上有字');

  await page.screenshot({ path: `${OUT}/01-salute.png` });

  const working = await waitFor((s) => s.villager.mode === 'working', '她走进铺子开始干活');
  check(working.villager.visible === false, '进屋后人不在门外');
  const operating = working.workstations.filter((w) => w.activity.kind === 'operating');
  check(operating.length === 1, '恰好一台铺子在运转');
  const activeBuilding = working.buildings.find(
    (b) => b.instanceId === operating[0].buildingId,
  );
  check(activeBuilding?.boundTask === 'code', '开工的是接代码活的那栋');
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/02-working.png` });

  // 自动干完：铺子进入冷却，人出现在门口交付。
  const delivering = await waitFor((s) => s.villager.mode === 'delivering', '活干完，她出门交付');
  check(delivering.villager.visible === true, '交付时人重新出现在画面里');
  check(delivering.tasks[0]?.status === 'done', '台账里这单已完成');
  const cooling = delivering.workstations.find((w) => w.activity.kind === 'cooling');
  check(cooling !== undefined, '铺子进入冷却');
  await page.screenshot({ path: `${OUT}/03-deliver.png` });

  const back = await waitFor((s) => s.villager.mode === 'idle', '她回到祭坛恢复闲逛');
  check(back.villager.taskId === null, '回到祭坛后手上没有单');

  // 手上有活时再派一单：句子认得，但世界不接。拒单理由必须回到界面。
  await page.click('button[data-kind="research"]');
  await waitFor((s) => s.villager.mode === 'working', '第二单能派出去');
  await page.fill('#utterance', '再查点东西');
  await page.click('button[type="submit"]');
  await page.waitForFunction(
    () => document.querySelector('#reply')?.classList.contains('bad') === true,
    null,
    { timeout: 5000 },
  );
  const refusal = await page.textContent('#reply');
  check(refusal.includes('手上有活'), '手上有活时新神谕被拒，理由回到界面');
  check((await state()).tasks.length === 2, '被拒的神谕没有进台账');

  // 「现实完成」是人工交付通道：活还没到时间，也能从外面结束。
  await page.click('#finish');
  const manual = await waitFor((s) => s.villager.mode === 'delivering', '人工交付也能结束这单');
  check(manual.tasks[1]?.status === 'done', '人工交付写进台账');

  check(errors.length === 0, '全程无 JS 报错');
  console.log(`\n神谕闭环：${checks.length} 项通过`);
  for (const label of checks) console.log(`  ok  ${label}`);
} catch (error) {
  console.error(`\n失败：${error.message}`);
  console.error(`已完成 ${checks.length} 项：`);
  for (const label of checks) console.error(`  ok  ${label}`);
  if (errors.length > 0) console.error(`页面报错：\n${errors.join('\n')}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
