// S5 世界端真帧 e2e：隔离实例 8001（真实 LLM_API_KEY）+ 临时 Vite 5189 代理到 8001。
// 服务端代码取自主仓库（含 S5 桥接 fdacd41）；像素世界取自 worktree（含全部视觉升级）。
// 子进程 detached，父进程退出后继续存活。不碰 8000/8010。
import { spawn } from 'node:child_process';
import net from 'node:net';
import { cpSync, existsSync, mkdirSync, mkdtempSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const SERVER_SRC = 'D:/project/neurosama';
const PIXEL = 'D:/project/neurosama/tmp/wt-pixel-world/pixel-world';
const PY = 'D:/project/neurosama/.venv/Scripts/python.exe';
const API = 'http://127.0.0.1:8001';
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitHttp(url, timeoutMs = 150_000) {
  const started = Date.now();
  for (;;) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
      if (response.ok) return Date.now() - started;
    } catch { /* 等 */ }
    if (Date.now() - started > timeoutMs) throw new Error('timeout waiting ' + url);
    await sleep(300);
  }
}

async function portOpen(port) {
  return new Promise((done) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const finish = (open) => { socket.destroy(); done(open); };
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    setTimeout(() => finish(false), 400);
  });
}

if (await portOpen(8001)) throw new Error('8001 occupied, refusing to take over');
if (await portOpen(5189)) throw new Error('5189 occupied, refusing to take over');

const tempRoot = mkdtempSync(join(tmpdir(), 'aqi-s5-e2e-'));
cpSync(join(SERVER_SRC, 'server'), join(tempRoot, 'server'), { recursive: true, filter: (p) => !p.includes('__pycache__') });
cpSync(join(SERVER_SRC, 'config'), join(tempRoot, 'config'), { recursive: true });
// worker（cli_subproc）按仓库相对路径跑 scripts/worker_shims/*.py，必须一起复制，
// 否则子进程找不到脚本直接退出码 2（round-03 实测踩到）。
cpSync(join(SERVER_SRC, 'scripts'), join(tempRoot, 'scripts'), { recursive: true, filter: (q) => !q.includes('__pycache__') });
// worker 的网关地址改指向隔离网关（15725，直连 StepFun）。
// 用户自己的 15722 上游 15721 是关闭的，worker 会立刻失败；这里换成一个等价但上游可用的
// 网关，让 D（交付）和 H（并发）能在 e2e 里真验。只改临时副本，不动主仓库。
const rosterPath = join(tempRoot, 'config', 'agents_roster.yaml');
if (existsSync(rosterPath)) {
  const before = readFileSync(rosterPath, 'utf8');
  const after = before.replace(/127\.0\.0\.1:15722/g, '127.0.0.1:15725');
  if (after !== before) writeFileSync(rosterPath, after, 'utf8');
}
mkdirSync(join(tempRoot, 'data'), { recursive: true });
mkdirSync(join(tempRoot, 'web'), { recursive: true });
cpSync(join(SERVER_SRC, 'web', 'index.html'), join(tempRoot, 'web', 'index.html'));

const serverLog = join(tempRoot, 'server.log');
const server = spawn(PY, ['-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '8001', '--log-level', 'info'], {
  cwd: tempRoot,
  env: {
    ...process.env,
    PYTHONPATH: tempRoot,
    DB_PATH: join(tempRoot, 'data', 'memory.db'),
    LLM_API_KEY: process.env.LLM_API_KEY || '',
    // 与用户 .env 一致的真实生产配置（否则会用 DeepSeek 默认值，Key 会 401）
    LLM_PROVIDER: 'openai',
    LLM_BASE_URL: 'https://api.stepfun.com/step_plan/v1',
    LLM_MODEL: 'step-3.7-flash',
    LLM_EXTRACT_MODEL: 'step-3.7-flash',
    LLM_REASONING_EFFORT: 'low',
  },
  stdio: ['ignore', openSync(serverLog, 'a'), openSync(serverLog, 'a')],
  detached: true,
});
server.unref();

const bootMs = await waitHttp(API + '/api/health');
const health = await (await fetch(API + '/api/health')).json();

const viteConfig = join(tempRoot, 'vite.config.mjs');
writeFileSync(viteConfig, 'export default {\n  root: ' + JSON.stringify(PIXEL) + ',\n  server: {\n    port: 5189,\n    strictPort: true,\n    proxy: {\n      "/api": { target: "http://127.0.0.1:8001", changeOrigin: false },\n      "/ws": { target: "ws://127.0.0.1:8001", ws: true, changeOrigin: false },\n    },\n  },\n};\n', 'utf8');
const viteLog = join(tempRoot, 'vite.log');
const vite = spawn(process.execPath, [join(PIXEL, 'node_modules', 'vite', 'bin', 'vite.js'), '--config', viteConfig, '--clearScreen', 'false'], {
  cwd: PIXEL,
  stdio: ['ignore', openSync(viteLog, 'a'), openSync(viteLog, 'a')],
  detached: true,
});
vite.unref();
await waitHttp('http://127.0.0.1:5189/');

const info = { startedAt: new Date().toISOString(), tempRoot, serverSrc: SERVER_SRC, pixelSrc: PIXEL, serverPid: server.pid, vitePid: vite.pid, ledgerPort: 8001, pagePort: 5189, pageUrl: 'http://127.0.0.1:5189/?ledger=on', bootMs, health, serverLog, viteLog, note: 'S5 e2e isolated env; real LLM key; 8000/8010 untouched; children detached' };
writeFileSync(join(PIXEL, 'eval', 'screens', 's5-e2e-env.json'), JSON.stringify(info, null, 2));
console.log(JSON.stringify(info, null, 2));
