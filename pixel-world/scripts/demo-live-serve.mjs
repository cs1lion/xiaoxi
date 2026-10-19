// 给 QA 留一个可跨会话观察的隔离 live 环境（不碰 8000；8001 数据副本 + 5189 代理）。
// 用法：node scripts/demo-live-serve.mjs [--stop]
import { spawn } from 'node:child_process';
import net from 'node:net';
import { cpSync, existsSync, mkdirSync, mkdtempSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '../..');
const PIXEL = join(ROOT, 'pixel-world');
const PY = join(ROOT, '.venv', 'Scripts', 'python.exe');
const API = 'http://127.0.0.1:8001';
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitHttp(url, timeoutMs = 90_000) {
  const started = Date.now();
  for (;;) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
      if (response.ok) return Date.now() - started;
    } catch {
      /* 等 */
    }
    if (Date.now() - started > timeoutMs) throw new Error(`超时等待 ${url}`);
    await sleep(200);
  }
}

async function portOpen(port) {
  return new Promise((done) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const finish = (open) => {
      socket.destroy();
      done(open);
    };
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    setTimeout(() => finish(false), 400);
  });
}

if (process.argv.includes('--stop')) {
  const infoPath = join(PIXEL, 'eval', 'screens', 'demo-live-env.json');
  if (existsSync(infoPath)) {
    const info = JSON.parse(readFileSync(infoPath, 'utf8'));
    for (const pid of [info.serverPid, info.vitePid]) {
      if (pid !== undefined && pid !== null) {
        spawn('taskkill', ['/pid', String(pid), '/t', '/f'], { stdio: 'ignore' });
      }
    }
    console.log(`stopped ${info.serverPid}/${info.vitePid}`);
  }
  process.exit(0);
}

const tempRoot = mkdtempSync(join(tmpdir(), 'aqi-s4-demo-qa-'));
cpSync(join(ROOT, 'server'), join(tempRoot, 'server'), {
  recursive: true,
  filter: (path) => !path.includes('__pycache__'),
});
cpSync(join(ROOT, 'config'), join(tempRoot, 'config'), { recursive: true });
mkdirSync(join(tempRoot, 'data'), { recursive: true });
mkdirSync(join(tempRoot, 'web'), { recursive: true });
cpSync(join(ROOT, 'web', 'index.html'), join(tempRoot, 'web', 'index.html'));

const serverLog = join(tempRoot, 'server.log');
const serverFd = openSync(serverLog, 'a');
const server = spawn(
  PY,
  ['-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '8001', '--log-level', 'info'],
  {
    cwd: tempRoot,
    env: {
      ...process.env,
      PYTHONPATH: tempRoot,
      DB_PATH: join(tempRoot, 'data', 'memory.db'),
      LLM_API_KEY: '',
    },
    stdio: ['ignore', serverFd, serverFd],
  },
);
const bootMs = await waitHttp(`${API}/api/health`);
const health = await (await fetch(`${API}/api/health`)).json();

const viteConfig = join(tempRoot, 'vite.config.mjs');
writeFileSync(
  viteConfig,
  `export default {\n  root: ${JSON.stringify(PIXEL)},\n  server: {\n    port: 5189,\n    strictPort: true,\n    proxy: {\n      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: false },\n      '/ws': { target: 'ws://127.0.0.1:8001', ws: true, changeOrigin: false },\n    },\n  },\n};\n`,
  'utf8',
);
const viteLog = join(tempRoot, 'vite.log');
const viteFd = openSync(viteLog, 'a');
const vite = spawn(
  process.execPath,
  [join(PIXEL, 'node_modules', 'vite', 'bin', 'vite.js'), '--config', viteConfig, '--clearScreen', 'false'],
  { cwd: PIXEL, stdio: ['ignore', viteFd, viteFd] },
);
await waitHttp('http://127.0.0.1:5189/');

const info = {
  startedAt: new Date().toISOString(),
  tempRoot,
  serverPid: server.pid,
  vitePid: vite.pid,
  ledgerPort: 8001,
  pagePort: 5189,
  pageUrl: 'http://127.0.0.1:5189/?ledger=on',
  bootMs,
  health,
  serverLog,
  viteLog,
  note: '数据副本在临时根，data/ 为空；8000 未触碰。停止：node scripts/demo-live-serve.mjs --stop',
};
writeFileSync(join(PIXEL, 'eval', 'screens', 'demo-live-env.json'), JSON.stringify(info, null, 2));
console.log(JSON.stringify(info, null, 2));
process.exit(0);
