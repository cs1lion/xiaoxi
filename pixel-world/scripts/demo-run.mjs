/* S4 演示实跑录音机：按 DEMO_SCRIPT v0.2 逐段跑、从运行日志出计时、留 100%/400% 截图。
 *
 * 两种模式：
 *   --mode mock   连外部 dev server（默认 5188）+ 外部 Chrome（CDP 9333），页面 ?ledger=off
 *   --mode live   自己起隔离实例 8001（数据副本、lifespan ON、真实 WS）+ 临时 Vite 5189 代理，
 *                 页面 ?ledger=on；全程不碰 8000、不写仓库 data/
 *
 * 输出（都在像素世界 eval/screens/，gitignored）：
 *   demo-run-<mode>.jsonl   每段 start/end/耗时/来源/状态行，供证据报告引用（不手填）
 *   demo-<段>-100.png       100% 整页
 *   demo-<段>-400pct.png    400% canvas（隐藏 DOM，仅画布像素）
 *   demo-<mode>-server.log  仅 live：隔离服务日志；vite.log 同理
 *
 * 用法：
 *   node scripts/demo-run.mjs --mode mock
 *   node scripts/demo-run.mjs --mode live [--keep]
 * 环境变量：CDP_URL（默认 http://127.0.0.1:9333）、DEMO_DEV_URL（mock 页面地址）。
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { spawn } from 'node:child_process';
import {
  appendFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '../..');
const PIXEL = join(ROOT, 'pixel-world');
const PY = join(ROOT, '.venv', 'Scripts', 'python.exe');
const OUT = join(PIXEL, 'eval', 'screens');
const CDP_URL = process.env.CDP_URL ?? 'http://127.0.0.1:9333';
const MODE = (process.argv.includes('--mode') ? process.argv[process.argv.indexOf('--mode') + 1] : 'mock');
const KEEP = process.argv.includes('--keep');
// --serve：跑完不杀隔离实例与临时 Vite，留给 QA 独立观察（会打印副本路径/端口/PID）。
const SERVE = process.argv.includes('--serve');
const API = 'http://127.0.0.1:8001';
const LIVE_PAGE = 'http://127.0.0.1:5189/?ledger=on&debug=1';
const MOCK_PAGE = process.env.DEMO_DEV_URL ?? 'http://127.0.0.1:5188/?ledger=off&debug=1';
const LOG_PATH = join(OUT, `demo-run-${MODE}.jsonl`);
const T0 = Date.now();
// live 模式在页面打开前先落一单，seg1 用它取真实帧证据。
let liveMission = null;

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

function log(segment, phase, extra = {}) {
  const line = {
    ts: new Date().toISOString(),
    tMs: Date.now() - T0,
    mode: MODE,
    segment,
    phase,
    ...extra,
  };
  appendFileSync(LOG_PATH, `${JSON.stringify(line)}\n`);
  const brief = Object.entries(extra)
    .map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(' ');
  console.log(`[${line.tMs}ms] ${MODE} ${segment} ${phase}${brief ? ` ${brief}` : ''}`);
  return line;
}


async function waitHttp(url, timeoutMs = 60_000) {
  const started = Date.now();
  for (;;) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
      if (response.ok) return Date.now() - started;
    } catch {
      /* 还没起来 */
    }
    if (Date.now() - started > timeoutMs) throw new Error(`超时等待 ${url}`);
    await sleep(150);
  }
}

function portOpen(port) {
  return new Promise((done) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const finish = (open) => {
      socket.destroy();
      done(open);
    };
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    setTimeout(() => finish(false), 500);
  });
}

function killTree(child) {
  if (child === null || child === undefined) return;
  try {
    child.kill();
  } catch {
    /* 已退出 */
  }
  if (process.platform === 'win32' && child.pid !== undefined) {
    try {
      spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' });
    } catch {
      /* 尽力而为 */
    }
  }
}

function fileMtime(path) {
  try {
    return statSync(path).mtimeMs;
  } catch {
    return null;
  }
}

/* ---------------------------------------------------------------- 截图 */

async function shot(page, name) {
  await page.screenshot({ path: join(OUT, `demo-${MODE}-${name}-100.png`), fullPage: true });
  const style = await page.addStyleTag({
    content:
      'header{display:none!important}body{padding:0!important;align-items:flex-start!important;gap:0!important}' +
      '#world-overlay,#debug-panel,#debug-toggle{visibility:hidden!important}' +
      'canvas{width:1920px!important;height:1280px!important}',
  });
  await page.waitForTimeout(150);
  await page.locator('canvas').screenshot({ path: join(OUT, `demo-${MODE}-${name}-400pct.png`) });
  await style.evaluate((element) => {
    element.remove();
  });
  await page.waitForTimeout(100);
  log(name, 'shot', { file100: `demo-${MODE}-${name}-100.png`, file400: `demo-${MODE}-${name}-400pct.png` });
}

const view = (page) => page.evaluate(() => window.Oracle.view());
const statusText = (page) =>
  page.evaluate(() => document.querySelector('#status')?.textContent ?? '');
const replyText = (page) =>
  page.evaluate(() => document.querySelector('#reply')?.textContent ?? '');

async function waitView(page, predicate, label, timeoutMs = 30_000, intervalMs = 120) {
  const started = Date.now();
  for (;;) {
    const snapshot = await view(page);
    if (predicate(snapshot)) return { snapshot, waitedMs: Date.now() - started };
    if (Date.now() - started > timeoutMs) throw new Error(`超时：${label}`);
    await page.waitForTimeout(intervalMs);
  }
}

/* ---------------------------------------------------------------- 演示段落 */

async function seg0Initial(page, mode) {
  log('seg0-initial', 'start', { source: mode === 'live' ? 'live 台账/真实 WS' : 'Mock/离线' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 30_000 });
  await page.waitForTimeout(1200);
  const snapshot = await view(page);
  const status = await statusText(page);
  await shot(page, '0-initial');
  log('seg0-initial', 'end', {
    buildings: snapshot.buildings.length,
    villagers: snapshot.villagers.map((victim) => victim.mode),
    weather: snapshot.weather,
    ledgerConnected: snapshot.ledger.connected,
    status,
  });
}

async function seg1Mission(page, mode, liveFrames) {
  const source = mode === 'live' ? 'live 台账/真实 WS' : 'Mock/离线';
  log('seg1-mission', 'start', { source });
  let detail = {};
  if (mode === 'live') {
    // live 的派单在页面打开之前已经通过真实 /ws 发出（见 liveRun 的 seg1-pre 段），
    // 这样浏览器首次对账时这一单已经在台账里 → 会被对账补进世界并驱动她开工。
    const blocked = liveMission;
    if (blocked === null) throw new Error('live 派单前置没跑');
    liveFrames.push(...blocked.frames);
    await waitView(page, (snapshot) => snapshot.ledger.reconciled === true, '台账对账完成（此后台账单才驱动世界）', 40_000);
    const appeared = await waitView(page, (snapshot) => snapshot.tasks.some((task) => task.id === blocked.id), '真实台账单进世界', 40_000);
    log('seg1-mission', 'world-task', {
      tasks: appeared.snapshot.tasks.map((task) => ({ id: task.id, status: task.status, blockedQuestion: task.blockedQuestion })),
      villagerMode: appeared.snapshot.villager.mode,
      reconciled: appeared.snapshot.ledger.reconciled,
    });
    try {
      await waitView(page, (snapshot) => snapshot.villager.mode === 'toWork' || snapshot.villager.mode === 'working', '她动起来', 40_000);
    } catch (error) {
      const snapshot = await view(page);
      log('seg1-mission', 'mode-timeout', {
        villager: snapshot.villager.mode,
        taskId: snapshot.villager.taskId,
        reconciled: snapshot.ledger.reconciled,
        ledger: snapshot.ledger,
        tasks: snapshot.tasks.map((task) => ({ id: task.id, status: task.status, blockedQuestion: task.blockedQuestion })),
      });
      throw error;
    }
    await shot(page, '1-mission-toWork');
    const working = await waitView(page, (snapshot) => snapshot.villager.mode === 'working', '进铺开工', 60_000);
    await shot(page, '1-mission-working');
    detail = { ledgerTaskId: blocked.id, waitToWorkMs: working.waitedMs };
  } else {
    await page.click('[data-kind="research"]');
    const salute = await waitView(page, (snapshot) => snapshot.villager.mode === 'saluting', '行礼', 15_000);
    await shot(page, '1-mission-salute');
    const toWork = await waitView(page, (snapshot) => snapshot.villager.mode === 'toWork', '走向书阁', 20_000);
    const working = await waitView(page, (snapshot) => snapshot.villager.mode === 'working', '进铺开工', 25_000);
    await shot(page, '1-mission-working');
    const delivered = await waitView(page, (snapshot) => snapshot.deliveries.mock >= 1, '交付计数 +1', 40_000);
    await shot(page, '1-mission-delivered');
    detail = {
      saluteWaitMs: salute.waitedMs,
      toWorkWaitMs: toWork.waitedMs,
      workingWaitMs: working.waitedMs,
      deliveredWaitMs: delivered.waitedMs,
      deliveries: delivered.snapshot.deliveries,
    };
  }
  log('seg1-mission', 'end', { status: await statusText(page), ...detail });
}

async function seg2Paper(page) {
  log('seg2-paper', 'start', { source: MODE === 'live' ? 'live 台账/真实 WS' : 'Mock/离线' });
  const paper = page.locator('#paper');
  await paper.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const report = await page.evaluate(() => window.Oracle.report());
  await shot(page, '2-paper');
  log('seg2-paper', 'end', {
    headline: report.headline,
    date: report.date,
    lines: report.lines.length,
    domText: (await paper.textContent())?.slice(0, 120) ?? '',
  });
}

async function seg3Rain(page) {
  log('seg3-rain', 'start', { source: MODE === 'live' ? 'live 台账/真实 WS' : 'Mock/离线' });
  await page.evaluate(() => (document.querySelector('#paper')?.scrollIntoView({ block: 'start' })));
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click('#rain');
  const raining = await waitView(page, (snapshot) => snapshot.weather === 'rain', '开始下雨', 20_000);
  await page.waitForTimeout(1800);
  const status = await statusText(page);
  await shot(page, '3-rain');
  await page.click('#clear');
  await waitView(page, (snapshot) => snapshot.weather === 'clear', '放晴', 20_000);
  log('seg3-rain', 'end', { rainWaitMs: raining.waitedMs, status, cleared: true });
}

async function seg4Save(page) {
  log('seg4-save', 'start', { source: MODE === 'live' ? 'live 台账/真实 WS' : 'Mock/离线' });
  const before = await page.evaluate(() => {
    const snapshot = window.Oracle.view();
    return {
      buildings: snapshot.buildings.length,
      roads: snapshot.roads.length,
      milestones: snapshot.faith.milestones,
      deliveries: snapshot.deliveries,
      slot: localStorage.getItem('xiaoxi.pixel-world.save.v1') !== null,
    };
  });
  await page.click('#save-now');
  await page.waitForTimeout(400);
  const slotWritten = await page.evaluate(() => {
    const text = localStorage.getItem('xiaoxi.pixel-world.save.v1');
    if (text === null) return null;
    const save = JSON.parse(text);
    return {
      schemaVersion: save.schemaVersion,
      fingerprint: save.meta.schemaFingerprint,
      buildings: save.world.buildings.length,
      roads: save.world.roads.length,
      milestones: save.world.faith.milestones,
      settled: save.world.settled.length,
    };
  });
  await page.click('#load-save');
  await page.waitForTimeout(1200);
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 30_000 });
  await page.waitForTimeout(800);
  const after = await view(page);
  const status = await statusText(page);
  await shot(page, '4-save-restore');
  log('seg4-save', 'end', {
    before,
    slotWritten,
    after: {
      buildings: after.buildings.length,
      roads: after.roads.length,
      milestones: after.faith.milestones,
      deliveries: after.deliveries,
      reconciled: after.ledger.reconciled,
    },
    status,
  });
}

async function seg5ExportImport(page, mode) {
  log('seg5-export-import', 'start', { source: mode === 'live' ? 'live 台账/真实 WS' : 'Mock/离线' });
  await page.click('#save-export');
  await page.waitForFunction(
    () => (document.querySelector('#save-io-reply')?.textContent ?? '').startsWith('已导出'),
    null,
    { timeout: 15_000 },
  );
  const exportReply = await page.evaluate(() => document.querySelector('#save-io-reply')?.textContent ?? '');
  const slotText = await page.evaluate(() => localStorage.getItem('xiaoxi.pixel-world.save.v1') ?? '');
  const file = join(tmpdir(), `xiaoxi-demo-export-${Date.now()}.json`);
  writeFileSync(file, slotText);
  log('seg5-export-import', 'exported', { reply: exportReply, file, bytes: slotText.length });
  await page.setInputFiles('#save-import-file', file);
  await page.waitForFunction(
    () => (document.querySelector('#save-io-reply')?.textContent ?? '').includes('正在按导入存档重建世界'),
    null,
    { timeout: 15_000 },
  );
  const importReply = await page.evaluate(() => document.querySelector('#save-io-reply')?.textContent ?? '');
  await page.waitForTimeout(1000);
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 30_000 });
  await page.waitForTimeout(700);
  const restored = await view(page);
  const status = await statusText(page);
  await shot(page, '5-export-import');
  log('seg5-export-import', 'end', {
    importReply,
    buildings: restored.buildings.length,
    reconciled: restored.ledger.reconciled,
    status,
  });
}

async function seg6Debug(page) {
  log('seg6-debug', 'start', { source: MODE === 'live' ? 'live 台账/真实 WS' : 'Mock/离线' });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click('#debug-toggle');
  await page.waitForSelector('#debug-panel:not([hidden])', { timeout: 10_000 });
  await page.waitForTimeout(600);
  const counts = await page.evaluate(() => ({
    rows: document.querySelectorAll('#debug-content .dbg-row').length,
    stats: document.querySelector('#debug-stats')?.textContent ?? '',
  }));
  await shot(page, '6-debug');
  await page.click('#debug-close');
  log('seg6-debug', 'end', counts);
}

/* ---------------------------------------------------------------- WS 采集（live） */

async function openCapture() {
  const frames = [];
  const socket = new WebSocket('ws://127.0.0.1:8001/ws');
  const openedAt = Date.now();
  await new Promise((done, reject) => {
    const timer = setTimeout(() => reject(new Error('WS 采集连接超时')), 10_000);
    socket.addEventListener('open', () => {
      clearTimeout(timer);
      done();
    });
    socket.addEventListener('error', () => {
      clearTimeout(timer);
      reject(new Error('WS 采集连接失败'));
    });
  });
  socket.addEventListener('message', (event) => {
    let message;
    try {
      message = JSON.parse(String(event.data));
    } catch {
      return;
    }
    if (message?.type === 'commission_update') {
      frames.push({
        atMs: Date.now() - openedAt,
        id: message.commission?.id,
        status: message.commission?.status,
        agent_id: message.commission?.agent_id,
        title: message.commission?.title,
      });
    }
  });
  return { socket, frames };
}

async function waitForFrame(frames, predicate, timeoutMs) {
  const started = Date.now();
  for (;;) {
    const found = frames.find(predicate);
    if (found !== undefined) return found;
    if (Date.now() - started > timeoutMs) throw new Error('等待 WS 帧超时');
    await sleep(120);
  }
}

/* ---------------------------------------------------------------- 主流程 */

async function runSegments(page, mode, liveFrames) {
  await seg0Initial(page, mode);
  await seg1Mission(page, mode, liveFrames);
  await seg2Paper(page);
  await seg3Rain(page);
  await seg4Save(page);
  await seg5ExportImport(page, mode);
  await seg6Debug(page);
}

async function connectBrowser() {
  const browser = await chromium.connectOverCDP(CDP_URL);
  const context = browser.contexts()[0];
  if (context === undefined) throw new Error('CDP 浏览器没有可用 context');
  const page = await context.newPage();
  await page.setViewportSize({ width: 1010, height: 940 });
  page.on('pageerror', (error) => log('page', 'error', { message: error.message }));
  return { browser, context, page };
}

async function mockRun() {
  const { browser, context, page } = await connectBrowser();
  try {
    // 开跑前用 CDP 清一次原点的 localStorage/sessionStorage（只在开场清，不会影响后续刷新恢复）；
    // 不用 addInitScript 清，否则两次 “读档/导入后 reload” 会被当成首次导航再次清档。
    const cdp = await context.newCDPSession(page);
    await cdp.send('Storage.clearDataForOrigin', {
      origin: 'http://127.0.0.1:5188',
      storageTypes: 'local_storage,session_storage',
    });
    log('setup', 'start', { page: MOCK_PAGE, mode: 'mock', ledger: 'off' });
    await page.goto(MOCK_PAGE, { waitUntil: 'load' });
    await runSegments(page, 'mock', []);
  } finally {
    // 共享 CDP 浏览器不归我们关：只关自己的页面，避免杀掉用户已开的 Chrome。
    await page.close().catch(() => {});
  }
}

async function liveRun() {
  let tempRoot = null;
  let server = null;
  let vite = null;
  let browser = null;
  const wsFrames = [];
  const realBefore = await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(3000) })
    .then((response) => ({ status: response.status }))
    .catch(() => ({ status: null }));
  const commissionsBefore = fileMtime(join(ROOT, 'data', 'commissions.db'));
  const memoryBefore = fileMtime(join(ROOT, 'data', 'memory.db'));

  try {
    if (await portOpen(8001)) throw new Error('8001 已被占用，拒绝抢占');
    if (await portOpen(5189)) throw new Error('5189 已被占用，拒绝抢占');
    if (!existsSync(PY)) throw new Error(`找不到 venv python：${PY}`);

    tempRoot = mkdtempSync(join(tmpdir(), 'aqi-s4-demo-'));
    cpSync(join(ROOT, 'server'), join(tempRoot, 'server'), {
      recursive: true,
      filter: (path) => !path.includes('__pycache__'),
    });
    cpSync(join(ROOT, 'config'), join(tempRoot, 'config'), { recursive: true });
    mkdirSync(join(tempRoot, 'data'), { recursive: true });
    mkdirSync(join(tempRoot, 'web'), { recursive: true });
    cpSync(join(ROOT, 'web', 'index.html'), join(tempRoot, 'web', 'index.html'));

    const serverEnv = {
      ...process.env,
      PYTHONPATH: tempRoot,
      DB_PATH: join(tempRoot, 'data', 'memory.db'),
      LLM_API_KEY: '',
    };
    delete serverEnv.PYTHONSTARTUP;
    const serverLog = join(OUT, 'demo-live-server.log');
    const serverFd = openSync(serverLog, 'a');
    const boot = async (label) => {
      const started = Date.now();
      const child = spawn(
        PY,
        ['-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '8001', '--log-level', 'info'],
        { cwd: tempRoot, env: serverEnv, stdio: ['ignore', serverFd, serverFd] },
      );
      const bootMs = await waitHttp(`${API}/api/health`, 60_000);
      const health = await (await fetch(`${API}/api/health`)).json();
      const logText = readFileSync(serverLog, 'utf8');
      log('live-server', label, {
        bootMs,
        wallMs: Date.now() - started,
        pid: child.pid,
        healthOk: health?.ok === true,
        offlineMock: health?.llm?.offline === true,
        startupComplete: logText.includes('Application startup complete'),
      });
      return child;
    };
    server = await boot('cold-start');

    const viteConfig = join(tempRoot, 'vite.config.mjs');
    writeFileSync(
      viteConfig,
      `export default {\n  root: ${JSON.stringify(PIXEL)},\n  server: {\n    port: 5189,\n    strictPort: true,\n    proxy: {\n      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: false },\n      '/ws': { target: 'ws://127.0.0.1:8001', ws: true, changeOrigin: false },\n    },\n  },\n};\n`,
      'utf8',
    );
    const viteLog = join(OUT, 'demo-live-vite.log');
    const viteFd = openSync(viteLog, 'a');
    vite = spawn(
      process.execPath,
      [join(PIXEL, 'node_modules', 'vite', 'bin', 'vite.js'), '--config', viteConfig, '--clearScreen', 'false'],
      { cwd: PIXEL, stdio: ['ignore', viteFd, viteFd] },
    );
    await waitHttp('http://127.0.0.1:5189/', 60_000);
    log('setup', 'ready', { page: LIVE_PAGE, mode: 'live', ledger: 'on', tempRoot, vitePid: vite.pid });

    // seg1 前置：在打开世界之前，用真实 /ws 文本让慢环落一单。
    // 这样浏览器第一次完整对账就会把它补进世界（对账后才会派活），避免“推送早于对账、单被只读展示”的竞态。
    log('seg1-mission', 'start', { source: 'live 台账/真实 WS', phase: 'pre' });
    const preWs = await openCapture();
    preWs.socket.send(JSON.stringify({ type: 'text', text: '去把便笺整理成三版标题', turn_id: 's4-demo' }));
    log('seg1-mission', 'ws-sent', { atMs: Date.now() - T0 });
    const preBlocked = await waitForFrame(preWs.frames, (frame) => frame.status === 'blocked', 60_000);
    log('seg1-mission', 'ws-blocked', {
      id: preBlocked.id,
      status: preBlocked.status,
      agent_id: preBlocked.agent_id,
      frameCount: preWs.frames.length,
    });
    liveMission = { id: preBlocked.id, frames: [...preWs.frames] };
    preWs.socket.close();

    const connected = await connectBrowser();
    browser = connected.browser;
    const page = connected.page;
    // 同 mock：开场用 CDP 清一次原点存储，之后不再清（导入/读档要能 reload 恢复）。
    const cdp = await connected.context.newCDPSession(page);
    await cdp.send('Storage.clearDataForOrigin', {
      origin: 'http://127.0.0.1:5189',
      storageTypes: 'local_storage,session_storage',
    });
    await page.goto(LIVE_PAGE, { waitUntil: 'load' });
    await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 30_000 });
    const ledgerWait = await page
      .waitForFunction(() => window.Oracle.view().ledger.connected === true, null, { timeout: 40_000 })
      .then(() => true)
      .catch(() => false);
    log('setup', 'ledger', { connected: ledgerWait });
    if (!ledgerWait) throw new Error('浏览器未建立 live 台账连接');

    const liveRunFrames = [];
    await runSegments(page, 'live', liveRunFrames);
    wsFrames.push(...liveRunFrames);

    // 主服务冷启动验证（隔离 8001）：停实例 → 冷启 → health → WS 连上
    log('cold-start', 'stop', { pid: server.pid });
    killTree(server);
    server = null;
    const stoppedAt = Date.now();
    while (await portOpen(8001)) {
      if (Date.now() - stoppedAt > 15_000) throw new Error('8001 未停止');
      await sleep(200);
    }
    log('cold-start', 'stopped', { stoppedMs: Date.now() - stoppedAt });
    server = await boot('restart');
    const ws = await openCapture();
    await waitForFrame(ws.frames, () => true, 10_000).catch(() => {
      log('cold-start', 'ws-no-frame', { note: '连接成功但 10s 内无 commission_update（无新单）' });
    });
    const wsOk = ws.socket.readyState === WebSocket.OPEN;
    ws.socket.close();
    log('cold-start', 'end', { wsConnected: wsOk, port8001: await portOpen(8001) });

    log('isolation', 'check', {
      realServerBefore: realBefore,
      commissionsBefore,
      commissionsAfter: fileMtime(join(ROOT, 'data', 'commissions.db')),
      memoryBefore,
      memoryAfter: fileMtime(join(ROOT, 'data', 'memory.db')),
      wsFrames: wsFrames.length,
      framesSample: wsFrames.slice(0, 6),
    });
    await page.close().catch(() => {});
    browser = null;
  } finally {
    // 共享 CDP 浏览器保持不动（只关页面 / 自己的进程）。
    if (SERVE && server !== null && vite !== null) {
      const health = await fetch(`${API}/api/health`)
        .then((response) => response.json())
        .catch(() => null);
      log('serve', 'kept', {
        tempRoot,
        serverPid: server.pid,
        vitePid: vite.pid,
        ledgerPort: 8001,
        pagePort: 5189,
        pageUrl: LIVE_PAGE,
        health,
      });
    } else {
    killTree(server);
    killTree(vite);
    await sleep(800);
    if (tempRoot !== null && !KEEP) rmSync(tempRoot, { recursive: true, force: true });
    }
    log('teardown', 'end', {
      tempRoot: KEEP || SERVE ? tempRoot : null,
      port8001: await portOpen(8001),
      port5189: await portOpen(5189),
      realServerAfter: await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(3000) })
        .then((response) => ({ status: response.status }))
        .catch(() => ({ status: null })),
    });
  }
}

mkdirSync(OUT, { recursive: true });
if (process.argv.includes('--reset-only')) {
  // 干净重置：只清两个原点的 localStorage/sessionStorage（不关用户的 Chrome、不动 8000）。
  const { context, page } = await connectBrowser();
  for (const origin of ['http://127.0.0.1:5188', 'http://127.0.0.1:5189']) {
    const cdp = await context.newCDPSession(page);
    await cdp.send('Storage.clearDataForOrigin', {
      origin,
      storageTypes: 'local_storage,session_storage',
    });
    console.log(`cleared: ${origin}`);
  }
  await page.close().catch(() => {});
  process.exit(0);
}
writeFileSync(LOG_PATH, '');
try {
  if (MODE === 'live') await liveRun();
  else await mockRun();
  log('done', 'end', { ok: true });
  // 共享 CDP 连接会让事件循环一直活着；跑完显式退出。
  process.exit(0);
} catch (error) {
  log('done', 'error', { message: String(error?.message ?? error) });
  process.exit(1);
}
