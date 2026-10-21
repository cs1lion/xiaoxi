#!/usr/bin/env node
/**
 * 造物模式回归：进入、悬停、非法拒绝、回车建成、Esc 取消、世界暂停与恢复。
 *
 * 走真实鼠标与键盘，不走 API 捷径——输入层正是这一片最容易坏的地方。
 *
 * 跑法：
 *   1. npm run dev
 *   2. node scripts/check-creation.mjs
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';

const BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/';
const OUT = 'eval/screens';
const PREFAB = 'workshop_plot';

const reports = [];
const failures = [];
const check = (ok, label, detail = '') => {
  reports.push({ ok, label, detail });
  if (!ok) failures.push(`${label}${detail === '' ? '' : `（${detail}）`}`);
};

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1010, height: 1100 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

await page.addInitScript(() => localStorage.clear());
await page.goto(`${BASE}?ledger=off&time=noon`, { waitUntil: 'load' });
await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 20000 });
await page.waitForTimeout(600);

const box = await page.locator('canvas').first().boundingBox();
const tile = box.width / 30;

/** 把格坐标换成页面坐标，移动真实鼠标。 */
const hover = async (x, y) => {
  await page.mouse.move(box.x + (x + 0.5) * tile, box.y + (y + 0.5) * tile);
  await page.waitForTimeout(180);
};

// ------------------------------------------------------------------ 进入
await page.click(`button[data-create="${PREFAB}"]`);
await page.waitForTimeout(300);
check(
  (await page.evaluate(() => window.Oracle.creation())) !== null,
  '点按钮进入造物模式',
);

// ------------------------------------------------------------------ 暂停
const t0 = await page.evaluate(() => window.Oracle.view().now);
await page.waitForTimeout(1200);
const t1 = await page.evaluate(() => window.Oracle.view().now);
check(t0 === t1, '造物模式下世界暂停', `now ${t0} → ${t1}`);

// ------------------------------------------------------------------ 悬停
await hover(3, 3);
const hovered = await page.evaluate(() => window.Oracle.creation());
check(
  hovered !== null && hovered.ghostAt !== null &&
    hovered.ghostAt.x === 3 && hovered.ghostAt.y === 3,
  '鼠标移动更新幽灵位置',
  JSON.stringify(hovered?.ghostAt),
);

// ------------------------------------------------------------------ 非法位置
await hover(15, 8); // 祭坛区
const rejected = await page.evaluate(() => window.Oracle.confirmCreation());
check(rejected.ok === false, '非法位置确认被拒', rejected.message);
check(
  typeof rejected.message === 'string' && rejected.message.length > 0,
  '拒绝时给出可读理由',
  rejected.message,
);
await page.screenshot({ path: `${OUT}/creation-rejected.png` });

// ------------------------------------------------------------------ Esc 取消
await page.keyboard.press('Escape');
await page.waitForTimeout(400);
const afterEsc = await page.evaluate(() => ({
  creating: window.Oracle.creation(),
  buildings: window.Oracle.view().buildings.length,
  now: window.Oracle.view().now,
}));
check(afterEsc.creating === null, 'Esc 退出造物模式');
check(afterEsc.buildings === 3, '取消后没有新建筑', String(afterEsc.buildings));
await page.waitForTimeout(800);
check(
  (await page.evaluate(() => window.Oracle.view().now)) > afterEsc.now,
  '取消后世界恢复推进',
);

// ------------------------------------------------------------------ 回车建成
await page.click(`button[data-create="${PREFAB}"]`);
await page.waitForTimeout(300);
await hover(2, 2);
await page.screenshot({ path: `${OUT}/creation-ghost.png` });
await page.keyboard.press('Enter');
await page.waitForTimeout(900);

const built = await page.evaluate(() => ({
  buildings: window.Oracle.view().buildings.map((b) => ({ id: b.instanceId, origin: b.origin })),
  creating: window.Oracle.creation(),
}));
const plot = built.buildings.find((b) => b.id.startsWith(PREFAB));
check(plot !== undefined, '回车后新楼出现', JSON.stringify(built.buildings.map((b) => b.id)));
check(
  plot !== undefined && plot.origin.x === 2 && plot.origin.y === 2,
  '新楼落在鼠标指定的位置',
  JSON.stringify(plot?.origin),
);
check(built.creating === null, '建成后自动退出造物模式');

check(errors.length === 0, '全程无 JS 报错', errors.join('; '));

await browser.close();

for (const item of reports) {
  console.log(`${item.ok ? 'ok  ' : 'FAIL'} ${item.label}${item.detail === '' ? '' : `  ${item.detail}`}`);
}
if (failures.length > 0) {
  console.error(`\n造物模式失败 ${failures.length} 项：`);
  for (const line of failures) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`\n造物模式：${reports.length} 项通过`);
