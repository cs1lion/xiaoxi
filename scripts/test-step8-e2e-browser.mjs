/* T3（issue #23）：§25 慢环垂直切片的浏览器级 e2e tracer——唯一新增测试缝。
 *
 * 一条用例走通全链：页面建单（REST）→ 确认契约命令回传（真 WS，§25.3）→
 * 契约卡渲染（CONTRACT_PROPOSED）→ CLI 沙盒工真子进程执行 → 验收放行 →
 * 台账 done（REST 对账）→ 取消按钮命令回传（UI 按钮 → WS → 服务端取消）→
 * /world 投影的数据源断言。
 *
 * 自起自收真实服务实例（默认 8021，可用 E2E_PORT 覆盖），不碰 8000/8010；
 * 无 Key 自动 Mock。/world 投影不深入 Phaser 内部（那归 pixel-world 自家的
 * smoke.mjs 管辖，window.Oracle.pushLedger 契约已另测）——这里断言世界
 * 消费的两个数据源：REST 全量（/api/executive/commissions）与 WS 增量
 * （commission_update），外加 /world 应用本身可挂载启动。
 */
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const PORT = Number(process.env.E2E_PORT || 8021);
const BASE = `http://127.0.0.1:${PORT}`;
/* 工作台抽屉在聊天页上；`/` 从 2026-10-24 起是像素世界。
 * 世界那一段仍走 /world（静态挂载点没变）。 */
const CHAT = `${BASE}/chat`;
const PY = path.join(root, '.venv', 'Scripts', 'python.exe');

const checks = [];
let failed = 0;
const pass = (name) => { checks.push(name); console.log('ok -', name); };
const fail = (name, detail = '') => { failed += 1; console.log('FAIL -', name, detail ? `｜${detail}` : ''); };
const ok = (cond, name, detail = '') => (cond ? pass(name) : fail(name, detail));

/* 台账隔离由 TEST_SENTINEL=1 + DB_PATH 完成（见下方 spawn 的 env）：
 * 本 e2e 建的 A/B/C 单与它们的运行目录全落在 tmp/ 下自建，真实 data/ 不碰。
 *
 * 原先这里会「把 data/commissions.db 改名到 tmp/，跑完再改回来」——动的是
 * 用户正在用的文件：常驻服务占着就只能 SKIP，中途崩溃则台账留在 tmp/。 */
const stamp = Date.now().toString(36);

const server = spawn(PY, ['-m', 'uvicorn', 'server.app:app',
  '--host', '127.0.0.1', '--port', String(PORT), '--log-level', 'warning'],
  { cwd: root, env: {
    ...process.env,
    /* 本 e2e 要真建单、真跑子进程，A/B/C 三单和它们的运行目录都写进慢环各库。
     * DB_PATH 把记忆库钉到临时根，TEST_SENTINEL=1 让 server/app.py 的
     * _service_data_dir() 跟随它——台账/线程/偏好全落在 tmp/ 里自建，于是
     * **不用去改名用户的 data/commissions.db**（此前没给 env，服务直接开
     * 仓库 data/，只能靠 rename 真实台账隔离；用户常驻服务占着时就只能 SKIP）。 */
    DB_PATH: path.join(root, 'tmp', `step8-e2e-${stamp}`, 'memory.db'),
    TEST_SENTINEL: '1',
  }, stdio: ['ignore', 'pipe', 'pipe'] });
let serverErr = '';
server.stderr.on('data', (chunk) => { serverErr += String(chunk); });

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

const api = {
  open: (page, title, brief = '') => page.evaluate(async (body) => {
    const response = await fetch('/api/executive/commissions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return response.json();
  }, { title, brief }),
  accept: (page, cid) => page.evaluate(async (id) => {
    const response = await fetch(`/api/executive/commissions/${id}/accept`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    return { status: response.status, body: await response.json() };
  }, cid),
  get: (page, cid) => page.evaluate(async (id) =>
    (await (await fetch(`/api/executive/commissions/${id}`)).json()), cid),
};

/* 页内真 WebSocket 发一条 §25 命令。命令没有正向回执（错误才回 error 帧），
 * 发出即返回；效果由后续 REST 状态与事件渲染断言兑现。 */
const wsCommand = (page, command) => page.evaluate(async (cmd) => {
  const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = () => reject(new Error('ws error'));
  });
  socket.send(JSON.stringify(cmd));
  socket.close();
  return true;
}, command);

async function waitForStatus(page, cid, predicate, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await api.get(page, cid);
    if (predicate(last)) return last;
    await page.waitForTimeout(500);
  }
  return last;
}

let browser;
try {
  ok(await waitHealth(), `独立端口 ${PORT} 起真实服务（/api/health 就绪，未碰 8000/8010）`,
    serverErr.slice(-300));

  browser = await chromium.launch({ headless: true, args: [
    '--enable-unsafe-swiftshader',
    '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream',
  ] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15000);
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(CHAT, { waitUntil: 'domcontentloaded' });
  await page.click('#drawerToggle');
  await page.waitForSelector('#drawerOps', { state: 'visible' });
  await page.click('#drawerOps');
  await page.waitForSelector('#drawerViewOps', { state: 'visible' });
  await page.waitForSelector('#opsEvents', { state: 'attached' });
  pass('真实页面打开工作台 ops 视图');

  // ---- A. 全链真执行：建单 → 确认契约（WS 命令）→ 真子进程 → 验收 → done
  const commissionA = await api.open(page, '沙盒打样：生成并校验一个小文件', 'e2e tracer A');
  const idA = commissionA.id;
  ok(Boolean(idA), '页面内建单（POST /api/executive/commissions）',
    JSON.stringify(commissionA).slice(0, 200));
  await page.waitForSelector(`#opsEvents .ops-card`, { timeout: 15000 });
  pass('TASK_STATE_CHANGE（建单迁移）经 WS 到达并渲染出任务卡');

  const eventsBefore = await page.locator('#opsEvents .ops-card').count();
  await wsCommand(page, {
    type: 'CONFIRM_CONTRACT', task_id: idA, contract_id: idA, version: 1,
    confirmed_slots: ['goal'], request_id: `e2e-confirm-${idA}`,
  });
  const contractCard = await page
    .waitForSelector('#opsEvents .ops-card[data-v2="contract"]', { timeout: 15000 })
    .then(() => true).catch(() => false);
  ok(contractCard, '确认契约命令触发 dispatch → CONTRACT_PROPOSED 渲染出契约卡');
  const ready = await waitForStatus(page, idA, (row) => row.status === 'ready_to_report');
  ok(ready.status === 'ready_to_report', 'CLI 沙盒工真子进程跑完进待验收（REST 对账）',
    `status=${ready.status}｜blocked=${ready.blocked_question || ''}`);

  const eventsAfterRun = await page.locator('#opsEvents .ops-card').count();
  ok(eventsAfterRun > eventsBefore,
    '执行/验收类事件持续渲染（TOOL_EXECUTION / VERIFICATION_REPORT）',
    `before=${eventsBefore} after=${eventsAfterRun}`);

  const acceptResult = await api.accept(page, idA);
  const doneA = await api.get(page, idA);
  ok(acceptResult.status === 200 && doneA.status === 'done',
    '验收放行 → 台账 done（REST 对账）',
    `accept=${acceptResult.status} ${JSON.stringify(acceptResult.body).slice(0, 200)} status=${doneA.status}`);

  // ---- B. 命令回传（UI 按钮）：新建一单 → 点「取消任务」→ 服务端真取消
  const commissionB = await api.open(page, '沙盒打样：给取消回传用的一单', 'e2e tracer B');
  const idB = commissionB.id;
  const cardB = page.locator('#opsEvents .ops-card').filter({ hasText: idB }).first();
  await cardB.waitFor({ state: 'visible' });
  await cardB.locator('button', { hasText: '取消任务' }).click();
  const cancelledB = await waitForStatus(page, idB,
    (row) => row.status === 'done' && (row.audit || {}).cancelled, 20000);
  ok(Boolean(cancelledB && cancelledB.audit && cancelledB.audit.cancelled),
    '取消按钮 → §25 命令回传 → 服务端真取消（audit.cancelled 入档）',
    JSON.stringify({ status: cancelledB.status, audit: cancelledB.audit || {} }).slice(0, 200));

  // ---- C. /world 投影：应用可启动 + 世界消费的两个数据源
  const world = await browser.newPage();
  await world.goto(`${BASE}/world`, { waitUntil: 'domcontentloaded' });
  const oracleUp = await world
    .waitForFunction(() => window.Oracle && typeof window.Oracle.view === 'function',
      null, { timeout: 20000 })
    .then(() => true).catch(() => false);
  ok(oracleUp, '/world 挂载且世界应用启动（window.Oracle 就绪）');
  await world.close();

  const commissionsRest = await page.evaluate(async () =>
    (await (await fetch('/api/executive/commissions?limit=50')).json()).items);
  ok(commissionsRest.some((row) => row.id === idA && row.status === 'done'),
    '世界数据源之一（REST 全量）含已完成单');

  const commissionC = await api.open(page, '沙盒打样：给 WS 增量断言用的一单', 'e2e tracer C');
  const idC = commissionC.id;
  const sawUpdate = await page.evaluate((cid) => new Promise((resolve) => {
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    const timer = setTimeout(() => { socket.close(); resolve(false); }, 15000);
    socket.onopen = () => {
      fetch(`/api/executive/commissions/${cid}/cancel`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    };
    socket.onmessage = (message) => {
      try {
        const data = JSON.parse(message.data);
        if (data.type === 'commission_update' && data.commission && data.commission.id === cid) {
          clearTimeout(timer);
          socket.close();
          resolve(true);
        }
      } catch { /* 非 JSON 帧忽略 */ }
    };
  }), idC);
  ok(sawUpdate, '世界数据源之二（WS 增量 commission_update）实时到达');

  ok(pageErrors.length === 0, '全程无未捕获页面错误', pageErrors.join(';').slice(0, 300));

  await browser.close();
} catch (error) {
  fail('e2e 主流程异常', `${error && error.stack ? error.stack : error}｜server=${serverErr.slice(-300)}`);
} finally {
  if (browser) await browser.close().catch(() => {});
  spawn('taskkill', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' });
}

console.log(`\n${checks.length} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
