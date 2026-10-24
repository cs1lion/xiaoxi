/* S3 演练③（issue #29）：WS 断连重连——服务重启后前端自动恢复、v2 事件不丢序、
 * 任务视图与台账 REST 一致。
 *
 * 自起独立端口真实服务（默认 8035，拒绝 8000/8010，Mock 模式），真页面：
 *   建单A（REST）→ v2 卡渲染 → taskkill 服务 → 同端口重启 → 页面自动重连 →
 *   建单B → 新卡渲染（重连后 seq 从 1 重新起算仍被正确消费）→
 *   台账 REST 对账：A/B 都在且状态正确；页面任务卡与台账一一对应，无重复。
 *
 *   node scripts/drill-s3-ws-reconnect.mjs            # 回归模式（断言修复后行为）
 *   node scripts/drill-s3-ws-reconnect.mjs --observe  # 演练记录模式
 */
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const PORT = Number(process.env.DRILL_PORT || 8035);
if (PORT === 8000 || PORT === 8010) { console.error(`端口 ${PORT} 是用户在用的，拒绝`); process.exit(2); }
const BASE = `http://127.0.0.1:${PORT}`;
/* worktree 里没有 .venv：用主仓的虚拟环境解释器（只读），cwd 仍是本 worktree。 */
const localVenv = path.join(root, '.venv', 'Scripts', 'python.exe');
const PY = process.env.DRILL_PYTHON
  || (await import('node:fs').then((fs) => fs.existsSync(localVenv) ? localVenv
    : 'D:\\project\\neurosama\\.venv\\Scripts\\python.exe'));
const OBSERVE = process.argv.includes('--observe');

const log = (...a) => console.log(...a);
const stamp = Date.now().toString(36);
const DB = path.join(root, 'tmp', `drill-s3-${stamp}`, 'memory.db');

function startServer() {
  const server = spawn(PY, ['-m', 'uvicorn', 'server.app:app',
    '--host', '127.0.0.1', '--port', String(PORT), '--log-level', 'warning'],
    { cwd: root, env: { ...process.env, DB_PATH: DB }, stdio: ['ignore', 'pipe', 'pipe'] });
  let err = '';
  server.stderr.on('data', (chunk) => { err += String(chunk); });
  server.errText = () => err;
  return server;
}

async function waitHealth(deadlineMs = 45000) {
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${BASE}/api/health`);
      if (response.ok) return true;
    } catch { /* not up yet */ }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

async function waitGone(deadlineMs = 15000) {
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    try {
      await fetch(`${BASE}/api/health`, { signal: AbortSignal.timeout(800) });
    } catch { return true; }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
}

function killTree(server) {
  return new Promise((resolve) => {
    if (server.exitCode !== null) { resolve(true); return; }
    const killer = spawn('taskkill', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' });
    killer.on('exit', () => resolve(true));
    killer.on('error', () => resolve(false));
  });
}

const openCommission = async (title, brief = '') => {
  const response = await fetch(`${BASE}/api/executive/commissions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, brief }),
  });
  return response.json();
};
const ledger = async () => (await (await fetch(`${BASE}/api/executive/commissions?limit=50`)).json()).items;

let failed = 0;
const ok = (cond, name, detail = '') => {
  if (cond) log('ok -', name);
  else { failed += 1; log(OBSERVE ? 'observe FAIL -' : 'FAIL -', name, detail ? `｜${detail}` : ''); }
};

let server = startServer();
let browser;
try {
  ok(await waitHealth(), `独立端口 ${PORT} 起真实服务（未碰 8000/8010）`, server.errText().slice(-200));

  browser = await chromium.launch({ headless: true, args: [
    '--enable-unsafe-swiftshader',
    '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream',
  ] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(20000);
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => (document.getElementById('status')?.textContent || '') === '在线');
  await page.click('#drawerToggle');
  await page.waitForSelector('#drawerOps', { state: 'visible' });
  await page.click('#drawerOps');
  await page.waitForSelector('#opsEvents', { state: 'attached' });
  log('ok - 页面在线，ops 视图就绪');

  // ---- A. 断连前：建单 A，v2 事件渲染任务卡 ----
  const a = await openCommission('演练③：重启前的单', 's3 drift drill A');
  const cardA = page.locator('#opsEvents .ops-card').filter({ hasText: a.id });
  await cardA.waitFor({ state: 'visible' });
  log('ok - 建单 A 经 v2 TASK_STATE_CHANGE 渲染任务卡');

  // ---- B. 断连 → 重启 → 自动恢复 ----
  await killTree(server);
  ok(await waitGone(), '服务进程树已死');
  await page.waitForFunction(() => (document.getElementById('status')?.textContent || '') === '断开',
    null, { timeout: 15000 }).then(() => true).catch(() => false);
  const keptCards = await page.locator('#opsEvents .ops-card').filter({ hasText: a.id }).count();
  log('observe - 断开后旧任务卡仍显示（页面内存态）:', keptCards);

  server = startServer();
  ok(await waitHealth(), `同端口 ${PORT} 重启服务成功`, server.errText().slice(-200));
  const backOnline = await page.waitForFunction(
    () => (document.getElementById('status')?.textContent || '') === '在线',
    null, { timeout: 35000 }).then(() => true).catch(() => false);
  if (OBSERVE) {
    log(`observe - 自动重连${backOnline ? '发生' : '未发生（记录，不判失败）'}`);
  } else {
    ok(backOnline, 'B1 服务重启后页面自动重连（status 回"在线"）');
  }

  // ---- C. 重连后：建单 B，v2 事件（新连接 seq 重新起算）仍被正确消费 ----
  const b = await openCommission('演练③：重连后的单', 's3 drift drill B');
  const cardB = page.locator('#opsEvents .ops-card').filter({ hasText: b.id });
  const bRendered = await cardB.waitFor({ state: 'visible', timeout: 15000 }).then(() => true).catch(() => false);
  if (OBSERVE) {
    log('observe - 重连后新单渲染:', bRendered);
  } else {
    ok(bRendered, 'C1 重连后新单的 v2 事件照常渲染（seq 重新起算被正确处理）');
  }

  // ---- D. 页面任务视图与台账 REST 一致 ----
  const rows = await ledger();
  const rowA = rows.find((row) => row.id === a.id);
  const rowB = rows.find((row) => row.id === b.id);
  log('observe - 台账:', JSON.stringify(rows.map((row) => ({ id: row.id.slice(0, 8), status: row.status }))));
  ok(Boolean(rowA && rowB), 'D1 台账 REST 含 A/B 两单（重启后 SQLite 持久）',
    `A=${rowA?.status} B=${rowB?.status}`);
  if (bRendered) {
    const dupA = await page.locator('#opsEvents .ops-card').filter({ hasText: a.id }).count();
    ok(dupA === 1, 'D2 任务卡 A 无重复（id 去重跨重连活着）', `count=${dupA}`);
    const bothVisible = (await cardB.count()) >= 1 && (await page.locator('#opsEvents .ops-card').filter({ hasText: a.id }).count()) >= 1;
    ok(bothVisible, 'D3 页面视图与台账一一对应（A、B 都在屏上）');
  }

  ok(pageErrors.length === 0, '全程无未捕获页面错误', pageErrors.join(';').slice(0, 300));
  await browser.close();
} catch (error) {
  failed += 1;
  log('FAIL - 演练主流程异常', error && error.stack ? error.stack : String(error), '｜server=', server.errText().slice(-300));
} finally {
  if (browser) await browser.close().catch(() => {});
  if (server.exitCode === null) spawn('taskkill', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' });
}

log(`\n${OBSERVE ? 'observe mode' : 'assert mode'}: ${failed} failed`);
process.exit(!OBSERVE && failed ? 1 : 0);
