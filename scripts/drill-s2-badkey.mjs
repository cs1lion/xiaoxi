/* S3 演练②（issue #29）：Key 失效——LLM/TTS/ASR 配了坏 Key 时的错误路径。
 *
 * 与 Mock 模式（无 Key 自动 offline）是两条路径：这里 LLM_API_KEY 非空但错误，
 * 服务真的发 HTTP 请求。为可离线复现，base_url 指向本脚本自起的一个
 * 「永远 401」的桩服务（默认 8043）——等价于供应商可达、Key 被拒。
 *
 * 真服务起在独立端口（默认 8034，拒绝 8000/8010），环境变量注入坏配置：
 *   LLM_API_KEY=drill-invalid / LLM_BASE_URL=http://127.0.0.1:8043/v1
 *   ASR_ENGINE=stepaudio / TTS_ENGINE=stepaudio（两者复用 llm 客户端与 base_url）
 *
 * 观察点（对应 docs/DRIFT_DRILLS.md 场景②）：
 *   A 文本轮（type=text）：LLM 401 → 错误帧是否到前端、文案可读性、轮次是否收尾（不卡死）
 *   B 语音轮（start/audio_end 带真实 PCM）：ASR 401 → 错误帧 message 内容
 *   C TTS：文字链路里 TTS 401 是否只降级音频、不拖死整轮
 *
 *   node scripts/drill-s2-badkey.mjs            # 回归模式（断言修复后文案）
 *   node scripts/drill-s2-badkey.mjs --observe  # 演练记录模式（只打印不断言）
 */
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const PORT = Number(process.env.DRILL_PORT || 8034);
const STUB_PORT = Number(process.env.DRILL_STUB_PORT || 8043);
if (PORT === 8000 || PORT === 8010) { console.error(`端口 ${PORT} 是用户在用的，拒绝`); process.exit(2); }
const BASE = `http://127.0.0.1:${PORT}`;
/* worktree 里没有 .venv：用主仓的虚拟环境解释器（只读），cwd 仍是本 worktree。 */
const localVenv = path.join(root, '.venv', 'Scripts', 'python.exe');
const PY = process.env.DRILL_PYTHON
  || (await import('node:fs').then((fs) => fs.existsSync(localVenv) ? localVenv
    : 'D:\\project\\neurosama\\.venv\\Scripts\\python.exe'));
const OBSERVE = process.argv.includes('--observe');

const log = (...a) => console.log(...a);

/* 桩：供应商可达但 Key 无效——一切 API 调用回 401 + 供应商风格错误体。 */
const stub = http.createServer((req, res) => {
  const body = JSON.stringify({ error: { message: 'Incorrect API key provided', type: 'invalid_request_error', code: 'invalid_api_key' } });
  res.writeHead(401, { 'Content-Type': 'application/json' });
  res.end(body);
});

/* 1 秒 440Hz 正弦 PCM16 单声道，喂给 ASR 路径（非静音，逼真走识别）。 */
function sinePcm16(seconds = 1.0, freq = 440, rate = 16000) {
  const count = Math.floor(rate * seconds);
  const buf = Buffer.alloc(count * 2);
  for (let i = 0; i < count; i += 1) {
    buf.writeInt16LE(Math.round(9000 * Math.sin((2 * Math.PI * freq * i) / rate)));
  }
  return buf;
}

const stamp = Date.now().toString(36);
const DB = path.join(root, 'tmp', `drill-s2-${stamp}`, 'memory.db');

/* 台账隔离：executive_route 的每个分支都要真实台账状态才命中（intent 单独
 * 不路由）。不搬开 data/ 的话，前一场演练留下的待验收单会把"还记得…"
 * 截走走台账直出——LLM 根本不被调用，坏 Key 路径就测不到了（实测踩过）。
 * 搬开-恢复只动本 worktree 的演练产物；tmp/ 已在 .gitignore。 */
function isolateDataDir() {
  const data = path.join(root, 'data');
  if (!fs.existsSync(data)) return null;
  const backup = path.join(root, 'tmp', `drill-data-backup-${stamp}`);
  fs.mkdirSync(path.dirname(backup), { recursive: true });
  fs.renameSync(data, backup);
  return backup;
}
function restoreDataDir(backup) {
  if (!backup) return;
  const data = path.join(root, 'data');
  // Windows 上进程退出后文件锁释放有延迟：rm/rename 带重试；彻底失败时
  // 保留备份并大喊，绝不静默吞掉别人的数据。
  for (let i = 0; i < 10; i += 1) {
    try {
      if (fs.existsSync(data)) fs.rmSync(data, { recursive: true, force: true });
      fs.renameSync(backup, data);
      return;
    } catch (error) {
      if (i === 9) {
        console.error(`还原 data/ 失败（${error.code || error.message}）；` +
          `演练数据留在 ${backup}，请手动把备份改名回 data/。`);
        return;
      }
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1000);
    }
  }
}

function startServer() {
  const server = spawn(PY, ['-m', 'uvicorn', 'server.app:app',
    '--host', '127.0.0.1', '--port', String(PORT), '--log-level', 'warning'],
    { cwd: root, env: {
      ...process.env,
      DB_PATH: DB,
      LLM_API_KEY: 'drill-invalid-key',
      LLM_BASE_URL: `http://127.0.0.1:${STUB_PORT}/v1`,
      ASR_ENGINE: 'stepaudio',
      TTS_ENGINE: 'stepaudio',
    }, stdio: ['ignore', 'pipe', 'pipe'] });
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

/* 页内开一条 WS 并按脚本驱动，收集全部帧直到轮次终结（done/error·terminal）。 */
function driveTurn(page, script) {
  return page.evaluate(async (steps) => {
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    const frames = [];
    const open = await new Promise((resolve, reject) => {
      socket.onopen = () => resolve(true);
      socket.onerror = () => reject(new Error('ws error'));
      setTimeout(() => reject(new Error('ws open timeout')), 10000);
    });
    if (!open) return frames;
    let sentAudio = false;
    const done = new Promise((resolve) => {
      socket.onmessage = (message) => {
        let data; try { data = JSON.parse(message.data); } catch { return; }
        frames.push(data);
        if (data.type === 'done' || (data.type === 'error' && !data.recoverable)) resolve();
      };
    });
    for (const step of steps) {
      if (step.cmd === 'text') socket.send(JSON.stringify({ type: 'text', text: step.text }));
      if (step.cmd === 'start') socket.send(JSON.stringify({ type: 'start', turn_id: step.turn_id }));
      if (step.cmd === 'audio' && step.pcmB64) {
        socket.send(Uint8Array.from(atob(step.pcmB64), (c) => c.charCodeAt(0)).buffer);
        sentAudio = true;
      }
      if (step.cmd === 'audio_end') socket.send(JSON.stringify({ type: 'audio_end', turn_id: step.turn_id }));
    }
    const finished = await Promise.race([done, new Promise((r) => setTimeout(() => r('timeout'), 30000))]);
    socket.close();
    return { frames, finished };
  }, script);
}

let failed = 0;
const ok = (cond, name, detail = '') => {
  if (cond) log('ok -', name);
  else { failed += 1; log(OBSERVE ? 'observe FAIL -' : 'FAIL -', name, detail ? `｜${detail}` : ''); }
};

await new Promise((resolve) => stub.listen(STUB_PORT, '127.0.0.1', resolve));
log(`observe - 401 桩就绪 127.0.0.1:${STUB_PORT}`);

const dataBackup = isolateDataDir();
const server = startServer();
let browser;
try {
  ok(await waitHealth(), `独立端口 ${PORT} 起真实服务（坏 Key 指向 401 桩）`, server.errText().slice(-200));

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

  // ---- A. 文本轮：LLM 401 ----
  const t0 = Date.now();
  const textTurn = await driveTurn(page, [{ cmd: 'text', text: '还记得我的猫叫什么名字吗？' }]);
  const textMs = Date.now() - t0;
  const errFrames = textTurn.frames.filter((f) => f.type === 'error');
  const doneFrame = textTurn.frames.find((f) => f.type === 'done');
  log('observe - 文本轮帧序列:',
    textTurn.frames.map((f) => f.type + (f.recoverable ? '(recoverable)' : '')).join(' → '));
  log('observe - 文本轮 error 帧:', JSON.stringify(errFrames));
  log('observe - 文本轮 done.delivery:', JSON.stringify(doneFrame?.delivery || null), `finished=${textTurn.finished} 用时${textMs}ms`);

  ok(textTurn.finished !== 'timeout', 'A1 坏 Key 下轮次正常收尾（快环不卡死）', `${textMs}ms`);
  ok(errFrames.some((f) => f.recoverable), 'A2 错误帧到达前端（recoverable）', JSON.stringify(errFrames).slice(0, 200));
  const emptyGen = errFrames.find((f) => f.recoverable && /生成中断|生成失败|没有收到/.test(f.message || ''));
  ok(Boolean(emptyGen), 'A3 recoverable 文案可读', JSON.stringify(emptyGen || {}).slice(0, 200));
  // 修复后：零文本时不再说"已保留收到的部分文本"
  if (!OBSERVE) {
    const keptClaim = errFrames.some((f) => f.recoverable && /已保留收到的部分文本/.test(f.message || ''));
    const gotText = Boolean(doneFrame && (doneFrame.text || '').trim())
      || textTurn.frames.some((f) => f.type === 'text_delta' && (f.delta || '').trim());
    ok(!keptClaim || gotText, 'A4 零文本时文案不再声称"已保留部分文本"');
  }

  // ---- B. 语音轮：ASR 401 ----
  const pcmB64 = sinePcm16().toString('base64');
  const voiceTurn = await driveTurn(page, [
    { cmd: 'start', turn_id: 'drill-s2-voice' },
    { cmd: 'audio', pcmB64 },
    { cmd: 'audio_end', turn_id: 'drill-s2-voice' },
  ]);
  const voiceErr = voiceTurn.frames.filter((f) => f.type === 'error');
  log('observe - 语音轮帧序列:',
    voiceTurn.frames.map((f) => f.type).join(' → '));
  log('observe - 语音轮 error 帧:', JSON.stringify(voiceErr));
  ok(voiceTurn.finished !== 'timeout', 'B1 ASR 坏 Key 轮次正常收尾（不卡死）');
  ok(voiceErr.length > 0, 'B2 ASR 失败有 error 帧到前端');
  if (!OBSERVE) {
    const readable = voiceErr.every((f) => !/Incorrect API key|invalid_api_key|httpx|ConnectError/.test(f.message || ''));
    ok(readable, 'B3 error.message 无供应商原文/异常内部细节泄漏', JSON.stringify(voiceErr.map((f) => f.message)).slice(0, 300));
  }

  // ---- C. 页面真实发送路径：subtitle 文案可见 ----
  await page.fill('#textInput', '这条走页面输入框，验证前端文案');
  await page.click('#sendBtn');
  await page.waitForTimeout(6000);
  const subtitle = await page.evaluate(() => document.getElementById('subtitle')?.textContent || '');
  log('observe - 页面 subtitle:', subtitle);

  ok(pageErrors.length === 0, '全程无未捕获页面错误', pageErrors.join(';').slice(0, 300));
  await browser.close();
} catch (error) {
  failed += 1;
  log('FAIL - 演练主流程异常', error && error.stack ? error.stack : String(error), '｜server=', server.errText().slice(-300));
} finally {
  if (browser) await browser.close().catch(() => {});
  stub.close();
  // 等服务进程真退出再还 data/：Windows 上文件被占用时 rename 会失败。
  await new Promise((resolve) => {
    if (server.exitCode !== null) { resolve(); return; }
    spawn('taskkill', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' });
    const timer = setTimeout(resolve, 5000);
    server.once('exit', () => { clearTimeout(timer); resolve(); });
  });
  restoreDataDir(dataBackup);
}

log(`\n${OBSERVE ? 'observe mode' : 'assert mode'}: ${failed} failed`);
process.exit(!OBSERVE && failed ? 1 : 0);
