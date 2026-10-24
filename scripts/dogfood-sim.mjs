/* Phase 4 自动化 dogfood（用户 2026-10-24 授权以合成语音模拟真实使用）：
 * 把 tmp/dogfood/day.wav（Huihui 合成的「一天」）作为假麦克风输入，打进
 * 用户常驻的真实服务（默认 8000，本脚本不起不杀服务，只消费），
 * 按住一次麦克风说完全天：VAD 自动断句成轮，检验真实语音全链
 * （VAD→ASR→快环→TTS 回放→记忆→委托→打断→重连续会话）。
 *
 * 产出：eval/dogfood/ 下的帧时间线、截图、report.json；不改任何服务端状态
 * 之外的东西（产生的对话/记忆/委托即 dogfood 数据，报告里注明）。
 *
 *   node scripts/dogfood-sim.mjs            # 正常跑
 *   node scripts/dogfood-sim.mjs --observe  # 只记录不判定（本轮默认即如此）
 */
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const dayDir = path.join(root, 'tmp', 'dogfood');
const outDir = path.join(root, 'eval', 'dogfood');
await mkdir(outDir, { recursive: true });

const plan = JSON.parse(await readFile(path.join(dayDir, 'day_plan.json'), 'utf-8'));
const dayWav = path.join(dayDir, 'day.wav');

const log = (...a) => console.log(...a);
const frames = [];   // {t, dir, type, turn_id, note}
const t0 = Date.now();
const note = (dir, type, extra = {}) => frames.push({ t: Number(((Date.now() - t0) / 1000).toFixed(2)), dir, type, ...extra });

const browser = await chromium.launch({ headless: true, args: [
  '--enable-unsafe-swiftshader',
  '--use-fake-device-for-media-stream',
  '--use-fake-ui-for-media-stream',
  `--use-file-for-fake-audio-capture=${dayWav}`,
] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(20000);
const pageErrors = [];
page.on('pageerror', (error) => pageErrors.push(error.message));
page.on('websocket', (socket) => {
  socket.on('framereceived', (frame) => {
    try {
      const data = JSON.parse(frame.payload);
      note('recv', data.type, { turn_id: data.turn_id || '', session_id: data.session_id || '',
        text: (data.type === 'asr' || data.type === 'done') ? String(data.text || '').slice(0, 80) : '' });
    } catch { /* 二进制音频帧 */ note('recv', 'binary'); }
  });
  socket.on('framesent', (frame) => {
    try { const d = JSON.parse(frame.payload); note('send', d.type, { turn_id: d.turn_id || '' }); }
    catch { note('send', 'binary'); }
  });
});

log(`[1] 打开 ${BASE}/（常驻真实服务）`);
await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => (document.getElementById('status')?.textContent || '') === '在线');

log(`[2] 按住麦克风，逐句说话（${plan.utterances.length} 句，按 day.wav 时间轴同步按下/松开——产品交互模型是按住说一句松开，长按会被 30s 上限强切且不自动续录，v1 轮实测）`);
const FILE_START_LAG = 2.5;   /* v1 实测：麦克风在按下瞬间起播文件（start≈2.5s，audio_end≈32s 对上 29.5s 音频） */
for (const u of plan.utterances) {
  const pressAt = u.start + FILE_START_LAG - 0.3;
  const holdFor = u.duration + 0.9;
  await page.waitForTimeout(Math.max(0, pressAt - (Date.now() - t0) / 1000) * 1000);
  await page.locator('#micBtn').focus();
  await page.keyboard.down('Space');
  await page.waitForFunction(() => document.querySelector('#micBtn')?.classList.contains('recording'))
    .catch(() => log(`  (warn) 第 ${plan.utterances.indexOf(u) + 1} 句未进 recording`));
  await page.waitForTimeout(holdFor * 1000);
  await page.keyboard.up('Space');
}
log('  （语音段完毕，等最后一轮回复收尾）');
await page.waitForTimeout(12000);

/* 对照表：每个预期话段 → 它之后的第一个 asr 帧（按时间轴配对） */
const asrEvents = frames.filter((f) => f.type === 'asr' && f.text);
const pairs = plan.utterances.map((u) => {
  const startT = u.start + 2.0;                 /* day.wav 里 2s 起播 */
  const hit = asrEvents.find((f) => f.t >= startT && f.t <= startT + u.duration + 8);
  return { intended: u.text, got: hit ? hit.text : null, at: hit ? hit.t : null };
});

/* 每 turn 首音频延迟：turn_started → 第一个 audio */
const turns = [];
for (const f of frames) {
  if (f.type === 'turn_started') turns.push({ turn_id: f.turn_id, start: f.t, firstAudio: null, done: null });
  else if (f.type === 'audio' && turns.length && turns[turns.length - 1].firstAudio === null)
    turns[turns.length - 1].firstAudio = f.t;
  else if (f.type === 'done' && turns.length && turns[turns.length - 1].done === null)
    turns[turns.length - 1].done = f.t;
}
const interrupted = frames.filter((f) => f.type === 'interrupted').length;
const errors = frames.filter((f) => f.type === 'error');
const sessionIds = [...new Set(frames.filter((f) => f.type === 'ready' && f.session_id).map((f) => f.session_id))];

/* [4] 断网重连 + resume 续会话：setOffline 逼出 onclose，恢复后自动重连，
 * 再打一句文本问上文，验证对话线接上（同一 session 有前后两条）。 */
log('[4] 断网 3 秒 → 恢复 → 文本问上文');
await page.context().setOffline(true);
await page.waitForTimeout(3000);
await page.context().setOffline(false);
const backOnline = await page.waitForFunction(
  () => (document.getElementById('status')?.textContent || '') === '在线', null, { timeout: 30000 })
  .then(() => true).catch(() => false);
const afterOfflineReady = frames.filter((f) => f.type === 'ready' && f.session_id).map((f) => f.session_id);
await page.fill('#textInput', '我们刚才都聊了些什么？');
await page.press('#textInput', 'Enter');
await page.waitForTimeout(6000);

/* [5] 对账：转录 / 记忆 / 台账（全部容错：dogfood 的职责是记录，不是崩） */
const safeJson = async (url) => fetch(url)
  .then(async (r) => (r.ok ? { status: r.status, body: await r.json() }
    : { status: r.status, body: null, text: (await r.text()).slice(0, 120) }))
  .catch((e) => ({ status: 0, body: null, text: String(e) }));
const sid = sessionIds[0];
const transcriptResp = sid ? await safeJson(`${BASE}/api/session/${sid}/transcript`) : { status: 0, body: { items: [] } };
const transcript = (transcriptResp.body && transcriptResp.body.items) || [];
const memoriesResp = await safeJson(`${BASE}/api/memories`);
const memList = (memoriesResp.body && (memoriesResp.body.items || [])) || [];
const walkMemo = memList.filter((m) => String(m.value || '').includes('傍晚') && String(m.value || '').includes('散步'));
const commissionsResp = await safeJson(`${BASE}/api/executive/commissions?limit=20`);
const commissions = (commissionsResp.body && commissionsResp.body.items) || [];

/* [6] 截图与报告 */
await page.screenshot({ path: path.join(outDir, 'main.png'), fullPage: false });
await page.click('#drawerToggle').catch(() => {});
await page.waitForSelector('#drawerOps', { state: 'visible' }).catch(() => {});
await page.click('#drawerOps').catch(() => {});
await page.waitForTimeout(1200);
await page.screenshot({ path: path.join(outDir, 'ops.png'), fullPage: false });

const report = {
  when: new Date().toISOString(), base: BASE,
  utterances: pairs, turns, interrupted, errors: errors.map((e) => e.text || ''),
  sessionIds, readyIdsAfterOffline: afterOfflineReady, backOnline,
  transcriptCount: transcript.length,
  apiStatus: { transcript: transcriptResp.status, memories: memoriesResp.status,
    commissions: commissionsResp.status, memoriesErr: memoriesResp.text || '' },
  transcriptTail: transcript.slice(-4).map((m) => ({ role: m.role, text: String(m.text || '').slice(0, 60) })),
  walkMemoryWritten: walkMemo.length > 0,
  commissionsToday: commissions.slice(0, 3).map((c) => ({ id: c.id, title: c.title, status: c.status })),
  pageErrors,
};
await writeFile(path.join(outDir, 'report.json'), JSON.stringify(report, null, 1), 'utf-8');
await writeFile(path.join(outDir, 'frames.json'), JSON.stringify(frames, null, 1), 'utf-8');
log('\n===== dogfood 摘要 =====');
for (const p of pairs) log(`asr: ${p.got ?? '(未收到)'}   <-预期: ${p.intended}`);
log(`turns=${turns.length} interrupted=${interrupted} errors=${errors.length} pageErrors=${pageErrors.length}`);
log(`sessions=${sessionIds.join(',')} backOnline=${backOnline} transcript=${transcript.length} 条`);
log(`傍晚散步记忆已写入=${report.walkMemoryWritten} 委托=${JSON.stringify(report.commissionsToday)}`);
await browser.close();
