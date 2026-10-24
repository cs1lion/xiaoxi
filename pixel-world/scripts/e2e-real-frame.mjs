/* 真帧端到端（S1-6）：临时实例 8001 + 临时 Vite 5189 + 真浏览器。
 *
 * 一次性覆盖三个未验证项：
 *   D4 主服务冷启动（lifespan ON，全新临时数据目录，等同第一次开机）
 *   D1 无 Key 自动 Mock（health offline:true，且派单链路真能跑起来）
 *   D3 真实 WS commission_update 推送（从服务端真实的帧出发，不用 pushLedger 注入）
 *
 * 链路：/ws 文本 → 慢环 delegate → 台账落库 → WS commission_update
 *      → Vite 代理 /ws → 浏览器里的世界收到 → 她走向对应铺子开工。
 *
 * 隔离纪律：
 *   - 只用 8001，绝不启动/停止/写入 8000 的真实实例；
 *   - 把 server/、config/、web/index.html 复制到系统临时目录，DB_PATH 也指到那里，
 *     台账库（data/commissions.db）因此是全新的；仓库 data/ 的 mtime 会前后比对；
 *   - 临时 Vite 配置写在系统临时目录（纯对象导出，不 import 'vite'），不落仓库；
 *   - 结束（含失败）在 finally 里关进程、删临时目录，并确认 8001/5189 已释放。
 *
 * 用法：
 *   node pixel-world/scripts/e2e-real-frame.mjs            # 跑完清理
 *   node pixel-world/scripts/e2e-real-frame.mjs --keep     # 保留临时目录便于排查
 *   证据 JSON 默认写到系统临时目录，可用 E2E_EVIDENCE 覆盖路径。
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, openSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '../..');
const PIXEL = join(ROOT, 'pixel-world');
const PY = join(ROOT, '.venv', 'Scripts', 'python.exe');
const API = 'http://127.0.0.1:8001';
const PAGE_URL = 'http://127.0.0.1:5189/?ledger=on&debug=1';
const KEEP = process.argv.includes('--keep');
const EVIDENCE_PATH = process.env.E2E_EVIDENCE
  ?? join(tmpdir(), 'aqi-e2e-real-frame-evidence.json');

const checks = [];
const failed = [];
function check(ok, label, detail = '') {
  checks.push({ ok, label, detail });
  if (ok) console.log(`  ok   ${label}`);
  else {
    failed.push(label);
    console.log(`  FAIL ${label}${detail ? ` — ${detail}` : ''}`);
  }
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitFor(predicate, label, timeoutMs = 30_000, intervalMs = 200) {
  const started = Date.now();
  for (;;) {
    if (await predicate()) return Date.now() - started;
    if (Date.now() - started > timeoutMs) throw new Error(`超时：${label}`);
    await sleep(intervalMs);
  }
}

async function waitHttp(url, timeoutMs = 30_000) {
  return waitFor(async () => {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2000) });
      return response.ok;
    } catch {
      return false;
    }
  }, `等待 ${url}`, timeoutMs);
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

async function api(path, method = 'GET', body = undefined) {
  const response = await fetch(API + path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  const text = await response.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    json = text;
  }
  return { status: response.status, json };
}

function fileMtime(path) {
  try {
    return statSync(path).mtimeMs;
  } catch {
    return null;
  }
}

function copyTree(source, target, { filter } = {}) {
  cpSync(source, target, {
    recursive: true,
    filter: (path) => (filter === undefined ? true : filter(path)),
  });
}

function killTree(child) {
  if (child === null || child.exitCode !== null || child.signalCode !== null) return;
  try {
    child.kill();
  } catch {
    // 已经退出就无所谓
  }
  if (process.platform === 'win32') {
    sleep(1200).then(() => {
      if (child.exitCode === null) {
        try {
          spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' });
        } catch {
          // 收尾尽力而为
        }
      }
    });
  }
}

async function main() {
  const evidence = {
    startedAt: new Date().toISOString(),
    repo: ROOT,
    ports: { tempServer: 8001, tempVite: 5189, realServer: 8000 },
    coldStart: {},
    offlineMock: {},
    wsFrames: [],
    otherEvents: {},
    browser: {},
    d7: {},
    isolation: {},
    checks,
  };
  let tempRoot = null;
  let server = null;
  let vite = null;
  let browser = null;
  let capture = null;

  // 换 8000 之前/之后的健康快照（只读）：证明这轮没有碰真实实例。
  const realBefore = await (async () => {
    try {
      const response = await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(3000) });
      return { status: response.status };
    } catch (error) {
      return { status: null, error: String(error) };
    }
  })();
  evidence.realServerBefore = realBefore;
  const repoCommissions = fileMtime(join(ROOT, 'data', 'commissions.db'));
  const repoMemory = fileMtime(join(ROOT, 'data', 'memory.db'));

  try {
    check((await portOpen(8001)) === false, '临时端口 8001 空闲');
    check((await portOpen(5189)) === false, '临时端口 5189 空闲');
    check(existsSync(PY), '项目 venv 的 python 存在');
    if (failed.length > 0) throw new Error('前置条件不满足');

    // ---- 1) 最小隔离树：server/ + config/ + 空 data/ + web/index.html
    tempRoot = mkdtempSync(join(tmpdir(), 'aqi-e2e-real-frame-'));
    copyTree(join(ROOT, 'server'), join(tempRoot, 'server'), {
      filter: (path) => !path.includes('__pycache__'),
    });
    copyTree(join(ROOT, 'config'), join(tempRoot, 'config'));
    mkdirSync(join(tempRoot, 'data'), { recursive: true });
    mkdirSync(join(tempRoot, 'web'), { recursive: true });
    copyTree(join(ROOT, 'web', 'index.html'), join(tempRoot, 'web', 'index.html'));

    // ---- 2) 冷启动 8001（lifespan ON；无 Key；DB_PATH 指到临时目录）
    const serverLog = join(tempRoot, 'server.log');
    const serverFd = openSync(serverLog, 'a');
    const serverEnv = {
      ...process.env,
      PYTHONPATH: tempRoot,
      DB_PATH: join(tempRoot, 'data', 'memory.db'),
      LLM_API_KEY: '',
    };
    delete serverEnv.PYTHONSTARTUP;
    const bootStarted = Date.now();
    server = spawn(
      PY,
      ['-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '8001', '--log-level', 'info'],
      { cwd: tempRoot, env: serverEnv, stdio: ['ignore', serverFd, serverFd] },
    );
    const bootMs = await waitHttp(`${API}/api/health`, 60_000);
    const health = await (await fetch(`${API}/api/health`, { signal: AbortSignal.timeout(5000) })).json();
    evidence.coldStart = {
      bootMs,
      wallMs: Date.now() - bootStarted,
      health,
      logHasStartupComplete: readFileSync(serverLog, 'utf8').includes('Application startup complete'),
      tempRoot,
    };
    check(evidence.coldStart.logHasStartupComplete, '冷启动：lifespan startup 正常完成');
    check(health?.ok === true, '冷启动：/api/health 200');
    check(health?.llm?.offline === true, '无 Key：health.llm.offline === true', JSON.stringify(health?.llm));
    evidence.offlineMock.health = health;

    // ---- 3) 临时 Vite（纯对象配置，不落仓库），代理 /api /ws 到 8001
    const viteConfig = join(tempRoot, 'vite.config.mjs');
    writeFileSync(viteConfig, `// 临时配置（脚本生成，不进仓库）\nexport default {
  root: ${JSON.stringify(PIXEL)},
  base: './',
  server: {
    port: 5189,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: false },
      '/ws': { target: 'ws://127.0.0.1:8001', ws: true, changeOrigin: false },
    },
  },
};
`, 'utf8');
    const viteLog = join(tempRoot, 'vite.log');
    const viteFd = openSync(viteLog, 'a');
    vite = spawn(
      process.execPath,
      [join(PIXEL, 'node_modules', 'vite', 'bin', 'vite.js'), '--config', viteConfig, '--clearScreen', 'false'],
      { cwd: PIXEL, stdio: ['ignore', viteFd, viteFd] },
    );
    await waitHttp('http://127.0.0.1:5189/', 60_000);
    check(true, '临时 Vite 5189 已就绪（代理 /api、/ws → 8001）');

    // ---- 4) 真实 WS 帧采集连接（独立于浏览器；不发送 pushLedger）
    const frames = evidence.wsFrames;
    const otherEvents = evidence.otherEvents;
    const openedAt = Date.now();
    const captureOpen = new Promise((done, reject) => {
      capture = new WebSocket('ws://127.0.0.1:8001/ws');
      const timer = setTimeout(() => reject(new Error('WS 采集连接超时')), 10_000);
      capture.addEventListener('open', () => {
        clearTimeout(timer);
        done();
      });
      capture.addEventListener('error', () => {
        clearTimeout(timer);
        reject(new Error('WS 采集连接失败'));
      });
    });
    capture.addEventListener('message', (event) => {
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
          blocked_question: message.commission?.blocked_question,
        });
      } else {
        otherEvents[message?.type] = (otherEvents[message?.type] ?? 0) + 1;
      }
    });
    await captureOpen;
    check(true, '已建立 /ws 采集连接（真实推送通道）');

    // ---- 5) 真浏览器：世界经 Vite 代理接 8001
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const jsErrors = [];
    page.on('pageerror', (error) => jsErrors.push(error.message));
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 20_000 });
    // 记录 pushLedger 的调用次数：本脚本禁止用它注入帧。
    await page.evaluate(() => {
      window.__pushLedgerCalls = 0;
      const original = window.Oracle.pushLedger;
      window.Oracle.pushLedger = (...args) => {
        window.__pushLedgerCalls += 1;
        return original(...args);
      };
    });
    await page.waitForFunction(
      () => window.Oracle.view().ledger.connected === true,
      null,
      { timeout: 30_000 },
    );
    check(true, '浏览器世界经代理建立台账连接（REST + WS）');

    // ---- 6) 通过 /ws 文本触发真实慢环派单（无 Key 走 Mock）
    const delegationText = '去把便笺整理成三版标题';
    capture.send(JSON.stringify({ type: 'text', text: delegationText, turn_id: 'e2e-real-frame' }));
    const blockedWait = await waitFor(
      () => frames.some((frame) => frame.status === 'blocked'),
      '真实 WS 帧到达 blocked',
      60_000,
    ).catch((error) => error);
    const blockedFrame = frames.find((frame) => frame.status === 'blocked');
    evidence.wsFrames = frames;
    evidence.offlineMock.delegationText = delegationText;
    evidence.offlineMock.blockedWaitMs = typeof blockedWait === 'number' ? blockedWait : null;
    check(Boolean(blockedFrame), '无 Key 下派单链路跑通，WS 收到 blocked 帧',
      blockedFrame === undefined ? String(blockedWait) : '');
    if (blockedFrame === undefined) throw new Error('没拿到真实 blocked 帧');
    const statuses = frames.map((frame) => frame.status);
    check(statuses.includes('drafting') && statuses.includes('briefing')
      && statuses.includes('running') && statuses.includes('blocked'),
    '真实帧覆盖 drafting→briefing→running→blocked', statuses.join('→'));
    check(blockedFrame.agent_id === 'scribe', '慢环把单派给了花名册里的 scribe', blockedFrame.agent_id);
    check((blockedFrame.blocked_question ?? '').length > 0, 'blocked_question 随真实帧到达',
      (blockedFrame.blocked_question ?? '').slice(0, 40));

    // ---- 7) 世界响应：真帧进世界（没有 pushLedger），她离开祭坛去对应铺子
    const worldReady = await page.waitForFunction(
      (id) => {
        const snapshot = window.Oracle.view();
        return snapshot.tasks.some((task) => task.id === id)
          && snapshot.villager.mode !== 'idle';
      },
      blockedFrame.id,
      { timeout: 40_000 },
    ).then(() => true).catch(() => false);
    await sleep(800);
    const snapshot = await page.evaluate(() => ({
      ledger: window.Oracle.view().ledger,
      tasks: window.Oracle.view().tasks.map((task) => ({
        id: task.id, kind: task.kind, status: task.status,
      })),
      villagerMode: window.Oracle.view().villager.mode,
      villagerTaskId: window.Oracle.view().villager.taskId,
      status: document.querySelector('#status')?.textContent ?? '',
      pushLedgerCalls: window.__pushLedgerCalls,
    }));
    evidence.browser = { snapshot, jsErrors };
    check(worldReady, '世界收到真实帧：任务进清单且她离开 idle');
    const worldTask = snapshot.tasks.find((task) => task.id === blockedFrame.id);
    check(worldTask !== undefined, '真实帧里的督办 id 出现在世界任务清单', blockedFrame.id);
    check(worldTask?.kind === 'writing', '按 /roster 能力关键词归到写作类（抄书斋）', worldTask?.kind);
    check(['saluting', 'toWork', 'working'].includes(snapshot.villagerMode),
      '她按真实帧走向铺子', snapshot.villagerMode);
    check(snapshot.pushLedgerCalls === 0, '全程未调用 pushLedger（禁止注入）', String(snapshot.pushLedgerCalls));
    check(jsErrors.length === 0, '浏览器无 JS 报错', jsErrors.join('; '));

    // ---- 8) D7 探测：无 Key + manual 适配器能推进到哪一步
    const answered = await api(`/api/executive/commissions/${blockedFrame.id}/answer`, 'POST',
      { answer: '按短句来' });
    await sleep(4000);
    const ledgerAfter = await api('/api/executive/commissions?limit=5');
    const finalItem = (ledgerAfter.json?.items ?? []).find((item) => item.id === blockedFrame.id);
    evidence.d7 = {
      attempted: true,
      answerStatus: answered.status,
      finalStatus: finalItem?.status ?? null,
      finalBlockedQuestion: finalItem?.blocked_question ?? '',
      reason: '无 Key + 全部 Worker 适配器为 manual，没有执行体把单跑到 ready_to_report；'
        + 'D7（真机执行体交付）本次未验证，只覆盖到真实台账状态流。',
      verified: false,
    };
    check(true, `D7 标注未验证（最终状态 ${evidence.d7.finalStatus}，answer=${answered.status}）`);
  } catch (error) {
    console.error(`\n失败：${error.message}`);
    failed.push(error.message);
  } finally {
    if (browser !== null) await browser.close().catch(() => {});
    if (capture !== null) {
      try {
        capture.close();
      } catch {
        // 已断开
      }
    }
    killTree(vite);
    killTree(server);
    await sleep(2500);
    const port8001 = await portOpen(8001);
    const port5189 = await portOpen(5189);
    evidence.portsReleased = { '8001': !port8001, '5189': !port5189 };
    if (port8001 || port5189) {
      failed.push('临时端口未释放');
      console.log(`  FAIL 临时端口未释放 (8001=${port8001} 5189=${port5189})`);
    } else {
      console.log('  ok   临时端口 8001/5189 已释放');
    }
    // 隔离证据：仓库真实 data/ 的台账库/记忆库不应被这轮碰过。
    evidence.isolation = {
      repoCommissionsMtimeBefore: repoCommissions,
      repoCommissionsMtimeAfter: fileMtime(join(ROOT, 'data', 'commissions.db')),
      repoMemoryMtimeBefore: repoMemory,
      repoMemoryMtimeAfter: fileMtime(join(ROOT, 'data', 'memory.db')),
    };
    check(evidence.isolation.repoCommissionsMtimeBefore === evidence.isolation.repoCommissionsMtimeAfter,
      '仓库 data/commissions.db 未被触碰');
    check(evidence.isolation.repoMemoryMtimeBefore === evidence.isolation.repoMemoryMtimeAfter,
      '仓库 data/memory.db 未被触碰');
    const realAfter = await (async () => {
      try {
        const response = await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(3000) });
        return { status: response.status };
      } catch (error) {
        return { status: null, error: String(error) };
      }
    })();
    evidence.realServerAfter = realAfter;
    check(realAfter.status === realBefore.status, '真实 8000 健康状态前后一致（未重启/未 kill）',
      `${realBefore.status} → ${realAfter.status}`);
    evidence.finishedAt = new Date().toISOString();
    evidence.summary = {
      passed: checks.filter((item) => item.ok).length,
      failed: checks.filter((item) => !item.ok).length,
    };
    writeFileSync(EVIDENCE_PATH, JSON.stringify(evidence, null, 2), 'utf8');
    console.log(`\n证据：${EVIDENCE_PATH}`);
    if (tempRoot !== null) {
      if (KEEP) console.log(`临时目录（--keep）：${tempRoot}`);
      else rmSync(tempRoot, { recursive: true, force: true });
    }
  }

  console.log(`\n=== ${checks.filter((item) => item.ok).length} passed, ${failed.length} failed ===`);
  if (failed.length > 0) for (const label of failed) console.log('FAIL:', label);
  process.exitCode = failed.length === 0 ? 0 : 1;
}

await main();
