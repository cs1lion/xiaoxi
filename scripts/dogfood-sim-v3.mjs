/* dogfood v3 聚焦补缺（v1/v2 已覆盖对话/负向约束/记忆/打断/重连续会话）：
 * 语音委托 → 沙盒工真执行 → 语音验收。每句一个假麦克风文件、一次浏览器
 * 实例（假设备每次按下从文件头重播——v2 实测），会话连续性不需要
 * （台账/记忆连续在服务端）。
 *   node scripts/dogfood-sim-v3.mjs
 */
import { chromium } from '@playwright/test';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const dayDir = path.join(root, 'tmp', 'dogfood');
const log = (...a) => console.log(...a);

async function speak(wavName, label) {
  const frames = [];
  const browser = await chromium.launch({ headless: true, args: [
    '--enable-unsafe-swiftshader', '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream',
    `--use-file-for-fake-audio-capture=${path.join(dayDir, wavName)}`,
  ] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('websocket', (socket) => socket.on('framereceived', (frame) => {
    try { const d = JSON.parse(frame.payload); frames.push(d); } catch { /* audio */ }
  }));
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => (document.getElementById('status')?.textContent || '') === '在线');
  await page.locator('#micBtn').focus();
  await page.keyboard.down('Space');
  await page.waitForFunction(() => document.querySelector('#micBtn')?.classList.contains('recording'));
  await page.waitForTimeout(7000);
  await page.keyboard.up('Space');
  /* 等这轮走完（Node 侧轮询帧数组：recording 之后出现 done），上限 45s */
  const deadline = Date.now() + 75000;
  while (Date.now() < deadline && !frames.some((f) => f.type === 'done')) await sleep(500);
  await page.waitForTimeout(2500);
  const asr = frames.filter((f) => f.type === 'asr').map((f) => f.text);
  const replies = frames.filter((f) => f.type === 'done' || f.type === 'assistant_result').map((f) => f.text);
  await page.screenshot({ path: path.join(root, 'eval', 'dogfood', `${label}.png`) });
  await browser.close();
  return { asr, replies };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

log('[A] 语音委托：沙盒打样（HITL 确认走卡片按钮——协议 §118：不可逆动作须显式点击，语音回复不算确认）');
const a = await speak('utt_04p.wav', 'delegation');
log('  asr:', JSON.stringify(a.asr), 'reply:', JSON.stringify(a.replies));

/* 确认真实用户路径：页面上的确认卡（confirmAssistant 发 assistant_confirm）。 */
const confirmBrowser = await chromium.launch({ headless: true, args: [
  '--enable-unsafe-swiftshader', '--use-fake-device-for-media-stream',
  '--use-fake-ui-for-media-stream',
] });
const cp = await confirmBrowser.newPage({ viewport: { width: 1440, height: 1000 } });
await cp.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
await cp.waitForFunction(() => (document.getElementById('status')?.textContent || '') === '在线');
let clicked = false;
for (let i = 0; i < 20 && !clicked; i += 1) {
  await sleep(1000);
  const button = cp.locator('button', { hasText: /确认|批准|执行/ }).first();
  if (await button.count() && await button.isVisible().catch(() => false)) {
    await button.click({ timeout: 3000 }).catch(() => {});
    clicked = true;
    log('  已点确认卡按钮');
  }
}
if (!clicked) log('  (未找到可见确认卡——记入报告)');
await sleep(3000);
await confirmBrowser.close();

let commission = null;
for (let i = 0; i < 30 && !commission; i += 1) {
  await sleep(2000);
  const items = await fetch(`${BASE}/api/executive/commissions?limit=10`)
    .then((r) => r.json()).then((b) => b.items || []);
  commission = items.find((c) => (c.title || '').includes('沙盒'));
}
log('  委托落账:', commission ? `${commission.id} ${commission.title} ${commission.status}` : '(未建单!)');
if (!commission) {
  log('\n===== v3 摘要 ===== 委托未建单，链路断在确认环节');
  process.exit(1);
}

let ready = null;
for (let i = 0; i < 30 && !ready; i += 1) {
  await sleep(2000);
  const row = await fetch(`${BASE}/api/executive/commissions/${commission.id}`).then((r) => r.json());
  if (row.status === 'ready_to_report') ready = row;
  if (row.status === 'blocked') { log('  阻塞:', row.blocked_question || ''); break; }
}
log('  执行状态:', ready ? 'ready_to_report（真执行完成）' : '(未到待验收)');

log('[B] 语音验收：这单收工');
const b = await speak('utt_05.wav', 'accept');
log('  asr:', JSON.stringify(b.asr), 'reply:', JSON.stringify(b.replies));

let finalRow = null;
if (commission) {
  finalRow = await fetch(`${BASE}/api/executive/commissions/${commission.id}`).then((r) => r.json());
  log('  台账终态:', finalRow.status, finalRow.audit ? `audit.passed=${finalRow.audit.passed}` : '');
}

log('\n===== v3 摘要 =====');
log(`委托链路: ${commission ? '建单✓' : '建单✗'} ${ready ? '真执行✓' : '真执行?'} ${
  finalRow && finalRow.status === 'done' ? '语音验收✓' : '语音验收?'}`);
log('asr 委托句 =', JSON.stringify(a.asr), '｜asr 验收句 =', JSON.stringify(b.asr));
