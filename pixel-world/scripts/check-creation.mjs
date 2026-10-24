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
await page.goto(`${BASE}?ledger=off&time=noon&debug=1`, { waitUntil: 'load' });
await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 20000 });
await page.waitForTimeout(600);

const box = await page.locator('canvas').first().boundingBox();
const tile = box.width / 30;

/**
 * 把**世界格坐标**换成页面坐标，移动真实鼠标。
 *
 * 大世界里画布只是世界的一小块，必须减掉相机偏移；否则点的是屏幕上那个位置，
 * 世界坐标却完全在别处（实测建成判定全部失败）。
 */
const hover = async (x, y) => {
  // 先把镜头对准目标格：目标可能在视野之外（工业区离祭坛有十几格）。
  // 不这么做，换算出来的屏幕坐标会是负数，鼠标落在画布外。
  await page.evaluate(
    (cell) => window.Oracle.centerCameraOn(cell.x * 16, cell.y * 16),
    { x, y },
  );
  await page.waitForTimeout(120);
  const cam = await page.evaluate(() => window.Oracle.camera());
  const worldX = (x + 0.5) * 16 - cam.scrollX;
  const worldY = (y + 0.5) * 16 - cam.scrollY;
  await page.mouse.move(box.x + worldX * (box.width / 480), box.y + worldY * (box.height / 320));
  await page.waitForTimeout(180);
};

/** 祭坛坐标：大世界里它是世界中心 (40,30)，不再是 (15,9)。 */
const ALTAR = await page.evaluate(() => window.Oracle.view().altar);

/**
 * 找一个真的能建的位置。
 *
 * 大世界里 `workshop_plot` 属于工业区，而工业区不在祭坛旁边——小世界那套
 * 「往祭坛旁边一放就行」的假设不成立了。这里直接扫描工业区，用真实校验找点。
 */
const LEGAL = await page.evaluate(() => {
  window.Oracle.beginCreation('workshop_plot');
  let found = null;
  for (let y = 8; y < 26 && found === null; y += 1) {
    for (let x = 30; x < 52; x += 1) {
      // 只查询，不确认：确认会真的建一栋楼，把后面的用例全部污染。
      if (window.Oracle.canPlaceAt({ x, y })) {
        found = { x, y };
        break;
      }
    }
  }
  window.Oracle.cancelCreation();
  return found;
});
if (LEGAL === null) throw new Error('大世界里找不到合法的造物位置');

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
await hover(ALTAR.x - 1, ALTAR.y - 1);
const hovered = await page.evaluate(() => window.Oracle.creation());
check(
  hovered !== null && hovered.ghostAt !== null &&
    hovered.ghostAt.x === ALTAR.x - 1 && hovered.ghostAt.y === ALTAR.y - 1,
  '鼠标移动更新幽灵位置',
  JSON.stringify(hovered?.ghostAt),
);

// ------------------------------------------------------------------ 非法位置
await hover(ALTAR.x, ALTAR.y); // 祭坛正中：不可建
const rejected = await page.evaluate(() => window.Oracle.confirmCreation());
check(rejected.ok === false, '非法位置确认被拒', rejected.message);
check(
  typeof rejected.message === 'string' && rejected.message.length > 0,
  '拒绝时给出可读理由',
  rejected.message,
);
await page.screenshot({ path: `${OUT}/creation-rejected.png` });

// ------------------------------------------------------------------ Esc 取消
// 先记下进入模式前的建筑数，Esc 之后必须还是这个数。
const buildingsBeforeEsc = await page.evaluate(() => window.Oracle.view().buildings.length);
await page.keyboard.press('Escape');
await page.waitForTimeout(400);
const afterEsc = await page.evaluate(() => ({
  creating: window.Oracle.creation(),
  buildings: window.Oracle.view().buildings.length,
  now: window.Oracle.view().now,
}));
check(afterEsc.creating === null, 'Esc 退出造物模式');
check(
  afterEsc.buildings === buildingsBeforeEsc,
  '取消后没有新建筑',
  `${afterEsc.buildings} vs ${buildingsBeforeEsc}`,
);
await page.waitForTimeout(800);
check(
  (await page.evaluate(() => window.Oracle.view().now)) > afterEsc.now,
  '取消后世界恢复推进',
);

// ------------------------------------------------------------------ 回车建成
await page.click(`button[data-create="${PREFAB}"]`);
await page.waitForTimeout(300);
await hover(LEGAL.x, LEGAL.y);
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
  plot !== undefined && plot.origin.x === LEGAL.x && plot.origin.y === LEGAL.y,
  '新楼落在鼠标指定的位置',
  JSON.stringify(plot?.origin),
);
check(built.creating === null, '建成后自动退出造物模式');

// 迷雾区不能建：那是还没长出来的地方。
const fogCheck = await page.evaluate(() => {
  const fog = window.Oracle.view().fog;
  window.Oracle.beginCreation('workshop_plot');
  // 取一个明确在迷雾深处的位置：中心往外很远。
  const far = { x: fog.center.x, y: fog.center.y + fog.revealedRadius + 12 };
  const allowed = window.Oracle.canPlaceAt(far);
  window.Oracle.cancelCreation();
  return { allowed, far, radius: fog.revealedRadius };
});
check(fogCheck.allowed === false, '迷雾区不能建', JSON.stringify(fogCheck));

// 急件请旨：blocked 单必须完整呈现在纸面上。
const decreeQuestion = '要执行 rm -rf ./temp_build 吗？这个目录里有 3 个未提交的文件。';
await page.evaluate(
  (q) => window.Oracle.injectBlocked('c_decree', '清理临时目录', q),
  decreeQuestion,
);
await page.waitForTimeout(800);
const decreeState = await page.evaluate(() => ({
  decree: window.Oracle.decree(),
  panelHidden: document.querySelector('#decree')?.hidden,
  shown: document.querySelector('#decree-question')?.textContent ?? '',
}));
check(
  decreeState.decree?.question === decreeQuestion,
  '急件保留完整问题原文',
  decreeState.decree?.question ?? 'null',
);
check(decreeState.panelHidden === false, '急件面板显示出来');
check(
  decreeState.shown === decreeQuestion,
  '纸面显示的是完整原文，没有截断',
  decreeState.shown,
);

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
