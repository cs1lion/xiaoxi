/* S3 演练①（issue #29）：断网/服务不可达——页面按钮态、提示、服务回来后的恢复。
 *
 * 自起独立端口真实服务（默认 8033，拒绝 8000/8010，Mock 模式），真页面；
 * 中途 taskkill 整棵服务进程树，观察页面状态；再同端口重启服务，观察是否自动恢复。
 *
 * 两种跑法：
 *   node scripts/drill-s1-network.mjs --observe   # 演练记录模式：只记录不断言恢复
 *   node scripts/drill-s1-network.mjs             # 回归模式：断言修复后的自动重连与状态复位
 *
 * 观察点（对应 docs/DRIFT_DRILLS.md 场景①）：
 *   A 断开瞬间：status/subtitle/圆点类名、页面错误
 *   B 断开期间：文本发送按钮行为（消息应留在输入框）
 *   C 服务回来：WS 是否自动重连（status 回"在线"、subtitle 被服务端 ready 帧复位）
 */
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const PORT = Number(process.env.DRILL_PORT || 8033);
if (PORT === 8000 || PORT === 8010) { console.error(`端口 ${PORT} 是用户在用的，拒绝`); process.exit(2); }
const BASE = `http://127.0.0.1:${PORT}`;
/* worktree 里没有 .venv：用主仓的虚拟环境解释器（只读），cwd 仍是本 worktree。 */
const localVenv = path.join(root, '.venv', 'Scripts', 'python.exe');
const PY = process.env.DRILL_PYTHON
  || (await import('node:fs').then((fs) => fs.existsSync(localVenv) ? localVenv
    : 'D:\\project\\neurosama\\.venv\\Scripts\\python.exe'));
const OBSERVE = process.argv.includes('--observe');

const stamp = Date.now().toString(36);
const DB = path.join(root, 'tmp', `drill-s1-${stamp}`, 'memory.db');

const log = (...a) => console.log(...a);

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
    } catch { return true; }   // 连不上 = 进程树已死
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

const pageState = (page) => page.evaluate(() => ({
  status: document.getElementById('status')?.textContent || '',
  dot: document.getElementById('statusDot')?.className || '',
  subtitle: document.getElementById('subtitle')?.textContent || '',
  micDisabled: document.getElementById('micBtn')?.disabled ?? null,
  micLabel: document.getElementById('micBtn')?.getAttribute('aria-label') || '',
}));

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
  log('ok - 页面在线（status=在线）');

  // ---- A. 断开瞬间 ----
  await killTree(server);
  ok(await waitGone(), '服务进程树已死（健康检查连不上）');
  await page.waitForFunction(() => (document.getElementById('status')?.textContent || '') === '断开',
    null, { timeout: 15000 }).then(() => true).catch(() => false);
  const afterDrop = await pageState(page);
  log('observe - 断开后页面状态:', JSON.stringify(afterDrop));
  ok(afterDrop.status === '断开', 'A1 断开后 status 显示"断开"', JSON.stringify(afterDrop));
  ok(afterDrop.subtitle.length > 0, 'A2 断开后 subtitle 有提示文案', afterDrop.subtitle);

  // ---- B. 断开期间发消息 ----
  await page.fill('#textInput', '断网期间发出的一条消息');
  await page.click('#sendBtn');
  const afterSend = await pageState(page);
  const draftKept = await page.inputValue('#textInput');
  log('observe - 断开期间发送:', JSON.stringify({ ...afterSend, draftKept }));
  ok(draftKept === '断网期间发出的一条消息', 'B1 消息留在输入框，未静默丢失', draftKept);

  // ---- C. 服务回来 ----
  server = startServer();
  ok(await waitHealth(), `同端口 ${PORT} 重启服务成功`, server.errText().slice(-200));
  const backOnline = await page.waitForFunction(
    () => (document.getElementById('status')?.textContent || '') === '在线',
    null, { timeout: 35000 }).then(() => true).catch(() => false);
  const afterBack = await pageState(page);
  log('observe - 服务回来后:', JSON.stringify(afterBack), 'reconnected=', backOnline);
  if (OBSERVE) {
    log(`observe - 自动重连${backOnline ? '发生' : '未发生（记录，不判失败）'}`);
  } else {
    ok(backOnline, 'C1 服务回来后页面自动重连（status 回"在线"）');
    if (backOnline) {
      ok(/按住左下角|打字/.test(afterBack.subtitle),
        'C2 subtitle 被服务端 ready 帧复位为初始引导', afterBack.subtitle);
    }
  }

  ok(pageErrors.length === 0, '全程无未捕获页面错误', pageErrors.join(';').slice(0, 300));
  await browser.close();
} catch (error) {
  failed += 1;
  log('FAIL - 演练主流程异常', error && error.stack ? error.stack : String(error));
} finally {
  if (browser) await browser.close().catch(() => {});
  if (server.exitCode === null) spawn('taskkill', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' });
}

log(`\n${OBSERVE ? 'observe mode' : 'assert mode'}: ${failed} failed`);
process.exit(!OBSERVE && failed ? 1 : 0);
