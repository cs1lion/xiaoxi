import { DialogueReviewRecorder } from './dialogue-review.js';
import { DIALOGUE_INTENTS } from './drivers/dialogue-intent.js';

const INTENT_LABELS = Object.fromEntries(DIALOGUE_INTENTS.map(item => [item.id, item.label]));
const STATES = { idle: '待机', monitoring: '等待你说话', listening: '倾听', thinking: '思考', preparing: '准备回应',
  speaking: '回应', returning: '回看', returned: '回看', waiting: '等待', averted: '移视', attending: '保持关注',
  yielding: '让出话轮', interrupted: '已打断' };
const STYLES = { restrained: '克制', natural: '自然', lively: '活泼' };
const REASONS = { complete: '完成', interrupted: '打断', stopped: '停止', superseded: '切换回合', 'model-switch': '切换模型', calibration: '进入校准', 'duration-limit': '达到时长上限', disconnected: '连接断开', error: '错误', pagehide: '关闭页面' };
const SCENARIOS = { short: '短回答', long: '长思考', multi: '多段语音', interrupt: '中途打断' };

export function mountDialogueReview({ mount, avatar, source = 'chat', metadata = () => ({}), onIntentChange } = {}) {
  if (!mount || !avatar) return null;
  const recorder = new DialogueReviewRecorder();
  avatar.dialogueReview = recorder;
  // Static structure only; all recorded/user text below uses textContent.
  mount.innerHTML = `<section class="dialogue-review" aria-label="交流眼神与回合复盘">
    <label for="dialogueIntent">眉眼节奏</label><select id="dialogueIntent"><option value="auto">自动随情绪</option></select>
    <p class="dialogue-review-hint">认真更稳，犹豫稍晚抬眉，调侃在回看后轻带笑眼。</p>
    <details id="dialogueReviewPanel" open><summary>回合复盘 · 实验</summary>
      <p class="dialogue-review-hint">开启后记录本页文字与眼神变化，关闭页面即清除。不保存音频；记录默认关闭。</p>
      <label for="dialogueReviewScenario">本轮关注</label><select id="dialogueReviewScenario"><option value="short">短回答</option><option value="long">长思考</option><option value="multi">多段语音</option><option value="interrupt">中途打断</option></select>
      <div class="dialogue-review-actions"><button id="dialogueReviewToggle" type="button">开始记录</button><button id="dialogueReviewClear" type="button">清空</button><button id="dialogueReviewExport" type="button" disabled>导出 JSON</button></div>
      <p id="dialogueReviewStatus" role="status">尚未记录</p>
      <label for="dialogueReviewTurns">查看回合</label><select id="dialogueReviewTurns" disabled></select>
      <svg id="dialogueReviewTimeline" viewBox="0 0 300 88" role="img" aria-label="视线横向与纵向随时间变化"></svg>
      <label for="dialogueReviewScrub">查看时点 <output id="dialogueReviewTime">0.0 秒</output></label><input id="dialogueReviewScrub" type="range" min="0" max="0" step="100" value="0" disabled>
      <div id="dialogueReviewDetails"><p>开始记录后，正常交流或播放实验室流程。</p></div>
    </details></section>`;
  const $ = id => mount.querySelector(`#${id}`);
  const intent = $('dialogueIntent'), turns = $('dialogueReviewTurns'), scrub = $('dialogueReviewScrub');
  for (const item of DIALOGUE_INTENTS) {
    const option = document.createElement('option'); option.value = item.id; option.textContent = item.label; intent.append(option);
  }
  intent.value = avatar.dialogueIntentMode;
  let active = null, selected = null, follow = true, disposed = false, partialId = null, reviewId = null, segment = 0;
  const event = data => {
    if (!active || (data.turn_id != null && data.turn_id !== active.id)) return false;
    const routed = { ...data, turn_id: reviewId };
    if (data.type === 'done' && (data.turn_id ?? active?.id) === partialId) {
      const { text, ...partialEvent } = routed; return recorder.recordEvent(partialEvent);
    }
    return recorder.recordEvent(routed);
  };
  const beginTurn = turn => {
    active = { id: turn.id, source, scenario: turn.scenario || $('dialogueReviewScenario').value };
    reviewId = turn.id;
    partialId = null;
    const result = recorder.beginTurn({ ...active, input: turn.input || '' }); selected = null; follow = true; refresh(); return result;
  };
  const endTurn = (id, reason = 'complete') => {
    if (active?.id !== id) return false;
    const result = recorder.endTurn(reviewId, reason);
    active = null; reviewId = null;
    refresh(); return result;
  };
  const stop = reason => { recorder.stop(reason); refresh(); };
  intent.onchange = () => {
    avatar.setDialogueIntent(intent.value);
    event({ type: 'intent', turn_id: active?.id, meta: { intent: intent.value } });
    onIntentChange?.(intent.value); refresh();
  };
  function resumePartial() {
    if (active) {
      partialId = active.id;
      reviewId = `${String(active.id).slice(0, 96)}:review-${++segment}`;
      recorder.beginTurn({ ...active, id: reviewId, input: '' });
      event({ type: 'state', turn_id: active.id, meta: { reason: 'partial-recording', state: '从当前时点开始；前文未记录' } });
    }
  }
  $('dialogueReviewToggle').onclick = () => {
    if (recorder.recording) recorder.stop('stopped');
    else { recorder.start({ ...metadata(), source, scenario: $('dialogueReviewScenario').value,
      style: avatar.dialogueGaze.style, intent: avatar.dialogueIntentMode, version: '3' }); resumePartial(); }
    selected = null; follow = true; refresh();
  };
  $('dialogueReviewClear').onclick = () => {
    recorder.clear(); if (recorder.recording) resumePartial(); selected = null; follow = true; refresh();
  };
  $('dialogueReviewExport').onclick = () => {
    const snapshot = recorder.snapshot();
    if (!snapshot.turns.length && !snapshot.current) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `dialogue-review-${Date.now()}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  turns.onchange = () => { selected = turns.value; follow = true; refresh(); };
  scrub.oninput = () => { follow = false; refresh(); };
  $('dialogueReviewPanel').ontoggle = () => refresh();
  function textBlock(title, value) {
    const p = document.createElement('p'), strong = document.createElement('strong'), body = document.createElement('span');
    strong.textContent = title; body.textContent = value; p.append(strong, body); return p;
  }
  function timeline(turn, t) {
    const svg = $('dialogueReviewTimeline'), ns = 'http://www.w3.org/2000/svg'; svg.replaceChildren();
    const length = Math.max(1, turn.durationMs);
    function node(tag, attributes) {
      const n = document.createElementNS(ns, tag); for (const [key, value] of Object.entries(attributes)) n.setAttribute(key, value); svg.append(n); return n;
    }
    node('line', { x1: 5, y1: 36, x2: 295, y2: 36, stroke: 'currentColor', opacity: '.2' });
    for (const [key, color] of [['ParamEyeBallX', '#51aeaa'], ['ParamEyeBallY', '#d99d70']]) {
      const points = turn.frames.filter(frame => Number.isFinite(frame.params[key])).map(frame =>
        `${5 + frame.t / length * 290},${36 - Math.max(-1, Math.min(1, frame.params[key])) * 27}`).join(' ');
      node('polyline', { points, fill: 'none', stroke: color, 'stroke-width': '1.5' });
    }
    for (const event of turn.events.filter(e => ['playback-start', 'playback-end', 'interrupted'].includes(e.type))) {
      const x = 5 + event.t / length * 290;
      node('line', { x1: x, x2: x, y1: 5, y2: 66, stroke: event.type === 'interrupted' ? '#c96c76' : '#8298b7', opacity: '.5' });
    }
    const x = 5 + t / length * 290; node('line', { x1: x, x2: x, y1: 3, y2: 66, stroke: 'currentColor' });
    const legend = node('text', { x: 5, y: 82, fill: 'currentColor', 'font-size': 10 }); legend.textContent = '青：水平视线   杏：垂直视线   竖线：播放 / 打断';
  }
  function refresh() {
    if (disposed) return;
    const catalog = recorder.listTurns();
    $('dialogueReviewToggle').textContent = recorder.recording ? '停止记录' : '开始记录';
    $('dialogueReviewToggle').setAttribute('aria-pressed', String(recorder.recording));
    $('dialogueReviewExport').disabled = !catalog.length;
    $('dialogueReviewStatus').textContent = `${recorder.recording ? '正在记录' : '记录已关闭'} · ${catalog.length} / ${recorder.limits.maxTurns} 回合 · 每回合最多 120 秒`;
    if (selected == null || !catalog.some(item => String(item.id) === String(selected))) selected = catalog.at(-1)?.id ?? null;
    const options = catalog.map((item, index) => {
      const option = document.createElement('option'); option.value = item.id;
      option.textContent = `${index + 1}. ${SCENARIOS[item.scenario] || item.scenario} · ${item.current ? '进行中' : REASONS[item.reason] || item.reason} · ${item.input.slice(0, 18) || '本轮交流'}`; return option;
    });
    turns.replaceChildren(...options); turns.disabled = !catalog.length; turns.value = selected ?? '';
    const item = catalog.find(item => String(item.id) === String(selected));
    const turn = item ? recorder.getTurn(item.id) : null;
    scrub.disabled = !turn || !turn.frames.length;
    if (!turn) { $('dialogueReviewTimeline').replaceChildren(); $('dialogueReviewDetails').replaceChildren(textBlock('', '开始记录后，正常交流或播放实验室流程。')); return; }
    scrub.max = Math.ceil(turn.durationMs); if (follow) scrub.value = scrub.max;
    const t = Math.min(Number(scrub.value), turn.durationMs);
    $('dialogueReviewTime').textContent = `${(t / 1000).toFixed(1)} 秒`;
    const frame = turn.frames.findLast(frame => frame.t <= t);
    const played = turn.events.filter(event => event.type === 'playback-start' && event.t <= t).map(event => event.text ||
      (event.meta?.source === 'resume' ? '续播音频 · 无法定位剩余文字' : '')).join('\n');
    const description = frame ? `${STATES[frame.state] || frame.state} · ${STATES[frame.phase] || frame.phase} · ${INTENT_LABELS[frame.intent] || frame.intent} / ${STYLES[frame.style] || frame.style}${frame.suspended ? ' · 手动/表情控制优先' : ''}` : '此时尚未采到眼神画面';
    const blocks = [textBlock('眼神：', description), textBlock('本轮输入：', turn.input || '未记录'),
      textBlock('生成文字（本轮总计）：', turn.generatedText || '尚无'),
      textBlock('已开始播放片段：', played || '此时尚未开始播放'),
      textBlock('', source === 'lab' ? '实验室为静音演示；片段开始不代表整句已经播完。' : '打断时，已开始的片段不代表整句已经播完。')];
    blocks[2].id = 'dialogueReviewGenerated'; blocks[3].id = 'dialogueReviewSpoken';
    if (turn.events.some(event => event.meta?.reason === 'partial-recording')) blocks.push(textBlock('', '本轮从开启记录的时点开始，前文未记录。'));
    if (Object.values(turn.truncated).some(Boolean)) blocks.push(textBlock('', '本轮达到记录上限，部分内容已截断。'));
    $('dialogueReviewDetails').replaceChildren(...blocks); timeline(turn, t);
  }
  function visible() {
    if (document.hidden || !$('dialogueReviewPanel').open) return false;
    for (let element = mount.parentElement; element; element = element.parentElement) if (element.tagName === 'DETAILS' && !element.open) return false;
    return mount.getClientRects().length > 0;
  }
  const timer = setInterval(() => { if (visible()) refresh(); }, 500);
  const cleanup = () => { disposed = true; clearInterval(timer); recorder.stop('pagehide'); };
  window.addEventListener('pagehide', cleanup, { once: true });
  refresh();
  const ui = { recorder, beginTurn, event, endTurn, stop, refresh, get activeTurn() { return active ? { ...active } : null; }, dispose: cleanup };
  window.__dialogueReview = recorder; window.__dialogueReviewUI = ui;
  return ui;
}
