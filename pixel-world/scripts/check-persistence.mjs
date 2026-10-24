#!/usr/bin/env node
/**
 * 持久化回归：世界状态刷新后必须 100% 恢复。
 *
 * 注意清理方式：只在**首次导航前**清一次 localStorage。若用
 * `addInitScript(() => localStorage.clear())`，每次导航都会执行，
 * 刷新时会把刚存的档清掉，看起来像「存档丢了」——那是测试的错，不是产品的错。
 *
 * 跑法：
 *   1. npm run dev
 *   2. node scripts/check-persistence.mjs
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';

const BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/';

/** 存档体积上限。当前 80×60 实测约 4KB；给到 100KB 留余量。 */
const MAX_SAVE_BYTES = 100 * 1024;

const reports = [];
const failures = [];
const check = (ok, label, detail = '') => {
  reports.push({ ok, label, detail });
  if (!ok) failures.push(`${label}${detail === '' ? '' : `（${detail}）`}`);
};

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1010, height: 1100 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

await page.addInitScript(() => {
  if (!sessionStorage.getItem('persistence-check-cleared')) {
    localStorage.clear();
    sessionStorage.setItem('persistence-check-cleared', '1');
  }
});

await page.goto(`${BASE}?ledger=off&time=noon&debug=1`, { waitUntil: 'load' });
await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 30000 });
await page.waitForTimeout(800);

// 造出可验证的变化：两件神迹（摆件）+ 一次造物（建筑）。
await page.evaluate(() => {
  window.Oracle.miracle('light');
  window.Oracle.miracle('tree');
});
await page.waitForTimeout(1200);

const before = await page.evaluate(() => {
  window.Oracle.saveNow();
  const raw = localStorage.getItem('xiaoxi.pixel-world.save.v1') ?? '';
  const snapshot = window.Oracle.view();
  return {
    bytes: raw.length,
    buildings: snapshot.buildings.length,
    props: snapshot.props.length,
    radius: snapshot.fog.revealedRadius,
    schemaVersion: raw === '' ? 0 : JSON.parse(raw).schemaVersion,
  };
});

check(before.bytes > 0, '存档写入单槽', `${before.bytes} 字节`);
check(
  before.bytes < MAX_SAVE_BYTES,
  `存档体积低于 ${MAX_SAVE_BYTES / 1024}KB`,
  `${(before.bytes / 1024).toFixed(1)}KB`,
);
check(before.schemaVersion === 2, '存档是当前版本 v2', String(before.schemaVersion));
check(before.props >= 2, '造出的摆件进了存档', String(before.props));

// 刷新：不清 localStorage，验证真实恢复。
await page.reload({ waitUntil: 'load' });
await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 30000 });
await page.waitForTimeout(1500);

const after = await page.evaluate(() => {
  const snapshot = window.Oracle.view();
  return {
    buildings: snapshot.buildings.length,
    props: snapshot.props.length,
    radius: snapshot.fog.revealedRadius,
  };
});

check(
  after.props === before.props,
  '刷新后摆件数量不变',
  `${before.props} → ${after.props}`,
);
check(
  after.buildings === before.buildings,
  '刷新后建筑数量不变',
  `${before.buildings} → ${after.buildings}`,
);
check(
  after.radius === before.radius,
  '刷新后迷雾揭示范围不变',
  `${before.radius} → ${after.radius}`,
);
check(errors.length === 0, '全程无 JS 报错', errors.join('; '));

await browser.close();

for (const item of reports) {
  console.log(`${item.ok ? 'ok  ' : 'FAIL'} ${item.label}${item.detail === '' ? '' : `  ${item.detail}`}`);
}
if (failures.length > 0) {
  console.error(`\n持久化失败 ${failures.length} 项：`);
  for (const line of failures) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`\n持久化：${reports.length} 项通过`);
