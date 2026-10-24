#!/usr/bin/env node
/* S5 D 腿（完成/交付）真帧 e2e 驱动。
 *
 * 前置：先跑 scripts/s5-e2e-serve.mjs 起隔离实例（8001 + 5189），本脚本只驱动浏览器。
 * 跑法（Windows 无 heredoc，写文件再 import）：
 *   ego-browser nodejs -e "await import('file:///.../pixel-world/scripts/s5-e2e-drive.mjs')"
 *
 * 采集纪律（issue #9 / #10 的直接修复）：
 *   - 每帧记录台账连接态（connected / mock / complete / lastSyncAt）。旧版不记，
 *     于是「这单是真实台账下行」只能靠 URL 里的 ?ledger=on 推断。
 *   - 台账末态出现后**不立即 break**：交付动作比末态晚一步
 *     （delivering 走出来 → toAltar 走到祭坛）。再等最多 45s 把 toAltar 收进来。
 *     旧版在这一步 break，采集窗口永远停在 delivering，检查器的 OR 判据掩盖了缺口。
 */
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = process.env.S5_EVIDENCE_DIR || join(HERE, '..', 'eval', 'screens');
mkdirSync(OUT, { recursive: true });
const URL = (process.env.S5_WORLD_URL || 'http://127.0.0.1:5189') + '/?ledger=on&debug=1';
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

const RAF_PATCH = `(() => {
  const realNow = performance.now.bind(performance);
  const channel = new MessageChannel();
  const cbs = []; let scheduled = false; let last = -1e9;
  const FRAME = 1000 / 60;
  const pump = () => { const t = realNow(); if (t - last < FRAME) { channel.port2.postMessage(0); return; } last = t; scheduled = false; const batch = cbs.splice(0, cbs.length); for (const cb of batch) { try { cb(t); } catch (e) {} } };
  channel.port1.onmessage = () => { pump(); };
  window.requestAnimationFrame = (cb) => { cbs.push(cb); if (!scheduled) { scheduled = true; channel.port2.postMessage(0); } return cbs.length; };
  window.cancelAnimationFrame = () => {};
})();`;

const task = await taskSpace('s5-world-e2e-20261020');
const page = task.page('p1');
await page.cdp('Page.addScriptToEvaluateOnNewDocument', { source: RAF_PATCH });
try { await page.cdp('Storage.clearDataForOrigin', { origin: 'http://127.0.0.1:5189', storageTypes: 'all' }); } catch (e) {}
await page.goto(URL);
await page.waitForFunction(() => window.Oracle && document.querySelector('#app canvas'), undefined, { timeout: 60000 });
console.log('world ready');

const ledger = () => page.evaluate(async () => (await fetch('/api/executive/commissions')).json());
const worldView = () => page.evaluate(() => {
  const v = window.Oracle.view();
  return { modes: v.villagers.map((x) => x.id + ':' + x.mode + ':vis=' + x.visible),
           tasks: v.tasks.map((t) => ({ id: t.id, status: t.status, source: t.source, kind: t.kind })),
           ledgerDeliveries: v.deliveries.ledger,
           // issue #10：台账连接态必须进证据，否则「真实台账下行」只能靠 URL 推断。
           ledger: { connected: v.ledger.connected, mock: v.ledger.mock,
                     complete: v.ledger.complete, lastSyncAt: v.ledger.lastSyncAt },
           workstations: v.workstations.map((w) => w.buildingId + ':' + w.activity.kind) };
});

// 派单：代码任务（修复后应落 dsh）
await page.evaluate(() => {
  window.__d = { phase: 'starting', confirmation: null, receipt: null };
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  const rec = window.__d;
  ws.onopen = () => ws.send(JSON.stringify({ type: 'text', text: '让像素世界接一单：写一个 Python 脚本，统计某个目录下所有 .py 文件的行数', turn_id: 's5-d-fix' }));
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === 'assistant_result' && m.result && m.result.confirmation) {
      rec.confirmation = { tool: m.result.confirmation.tool, args: m.result.confirmation.arguments, id: m.result.confirmation.id, run_id: m.result.run_id };
      rec.phase = 'confirming';
      ws.send(JSON.stringify({ type: 'assistant_confirm', approve: true, run_id: m.result.run_id, confirmation_id: m.result.confirmation.id, turn_id: 's5-d-fixa' }));
      rec.phase = 'approving';
      return;
    }
    if (m.type === 'assistant_result' && m.result) {
      const rs = m.result.receipts || [];
      const w = rs.find((r) => String(r.tool || '').startsWith('world.'));
      if (w) rec.receipt = { tool: w.tool, status: w.status, data: w.data };
    }
    if (m.type === 'done') { rec.phase = 'done'; try { ws.close(); } catch (e) {} }
  };
});
console.log('驱动已启动');

const t0 = Date.now();
let terminal = null;
let terminalAt = 0;
const tl = [];
for (let i = 0; i < 500; i += 1) {
  await pause(1000);
  const d = await page.evaluate(() => window.__d);
  const w = await worldView();
  tl.push({ t: Math.round((Date.now() - t0) / 1000), phase: d.phase, ...w });
  // issue #9：末态一出现就 break，采集窗口停在 delivering，toAltar 永远拍不到。
  // 交付动作有先后：delivering（走出来）→ toAltar（走到祭坛）。所以末态判定后
  // 再补一小段采样窗口，把 toAltar 收进来，而不是靠 worldFinal=idle 反推。
  if (d.receipt && terminal === null) {
    const led = await ledger();
    const mine = led.items.find((x) => x.id === d.receipt.data.commission_id);
    if (mine && (mine.status === 'done' || mine.status === 'ready_to_report' || mine.status === 'blocked')) {
      terminalAt = (Date.now() - t0) / 1000;
      terminal = { status: mine.status, id: mine.id, agent: mine.agent_id, retry: mine.retry_count,
                   q: String(mine.blocked_question || '').slice(0, 160),
                   usage: mine.audit ? mine.audit.usage : null };
      console.log('TERMINAL t=' + ((Date.now() - t0) / 1000).toFixed(0) + 's:', JSON.stringify(terminal));
    }
  }
  if (i % 30 === 0) console.log('t=' + ((Date.now() - t0) / 1000).toFixed(0) + 's phase=' + d.phase, JSON.stringify(w).slice(0, 160));
  // 末态之后继续采样，直到看见 toAltar 或超时 45s——交付动作比台账末态晚一步。
  if (terminal) {
    const seenModes = tl.flatMap((entry) => entry.modes || []);
    const sawToAltar = seenModes.some((mode) => String(mode).includes(':toAltar'));
    if (sawToAltar) {
      console.log('POST-TERMINAL toAltar 已采集，采样窗口结束');
      break;
    }
    if ((Date.now() - t0) / 1000 - terminalAt > 45) {
      console.log('POST-TERMINAL 等 toAltar 超时 45s，按实际采集结果写出');
      break;
    }
  }
}

// ready_to_report → 老板批准
let approved = null;
if (terminal && terminal.status === 'ready_to_report') {
  approved = await page.evaluate(async (cid) => {
    try { const r = await fetch('/api/executive/commissions/' + cid + '/accept', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); return await r.json(); } catch (e) { return { error: String(e) }; }
  }, terminal.id);
  await pause(6000);
}
const ledFinal = await ledger();
const wFinal = await worldView();
const verdict = { commissionId: terminal ? terminal.id : null, terminalStatus: terminal ? terminal.status : null,
                  agent: terminal ? terminal.agent : null, retry: terminal ? terminal.retry : null,
                  auditUsage: terminal ? terminal.usage : null, q: terminal ? terminal.q : null,
                  approved: approved ? approved.status : null,
                  ledgerFinal: ledFinal.items.map((x) => x.id + '=' + x.status),
                  // issue #10：末态台账连接态单独留一份，别让检查器只能从 timeline 摸。
                  ledger: { connected: wFinal.ledger.connected, mock: wFinal.ledger.mock,
                            complete: wFinal.ledger.complete, lastSyncAt: wFinal.ledger.lastSyncAt },
                  worldFinal: wFinal, durationS: Math.round((Date.now() - t0) / 1000) };
console.log('D verdict:', JSON.stringify(verdict, null, 1));
const evidencePath = join(OUT, 's5-e2e-evidence-d5.json');
writeFileSync(evidencePath, JSON.stringify({ round: 'r12-D-toAltar-and-ledger', at: new Date().toISOString(), terminal, approved, timeline: tl, verdict }, null, 2));
console.log('D5 EVIDENCE WRITTEN: ' + evidencePath);
