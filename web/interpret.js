import { createCapture } from '/static/voice/capture.js';
import { StreamingResampler, SampleFramer, pcm16 } from '/static/voice/pcm.js';

const $ = id => document.getElementById(id);
const RATE = 16000, FRAME = 320, MIN = RATE, MAX = RATE * 4;
const BUFFER_LIMIT = 256 * 1024;
let epoch = 0, active = null;
const rows = new Map();
const live = s => active === s && s.epoch === epoch;
const text = value => typeof value === 'string' ? value.slice(0, 12000) : '';
function status(message) { $('status').textContent = message; }
function stopTracks(stream) { for (const track of stream?.getTracks() || []) { try { track.stop(); } catch {} } }
function closeContext(context) { try { Promise.resolve(context?.close()).catch(() => {}); } catch {} }
function stop(message = '已停止；未提交尾段已丢弃') {
  const s = active;
  active = null; epoch++;
  if (s) {
    clearTimeout(s.timer);
    s.capture?.stop();
    stopTracks(s.stream);
    closeContext(s.context);
    if (s.ws) {
      s.ws.onopen = s.ws.onmessage = s.ws.onerror = s.ws.onclose = null;
      try { if (s.ws.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify({ type: 'stop', session_id: s.id })); } catch {}
      try { s.ws.close(); } catch {}
    }
    for (const id of s.pending) {
      const entry = rows.get(`${s.id}:${id}`);
      if (entry && (s.mode === 'streaming' || !entry.translation.textContent)) entry.state.textContent = '已取消：停止后不再接收结果';
    }
    s.pending.clear();
  }
  $('start').disabled = false; $('source').disabled = false; $('backend').disabled = false; $('stop').disabled = true;
  status(message);
}
function row(s, id, start, end) {
  const key = `${s.id}:${id}`;
  if (rows.has(key)) return rows.get(key);
  // Never resurrect rows removed by the bounded history or Clear.
  if (id <= s.hideBefore) return null;
  const article = document.createElement('article'); article.className = 'segment';
  const heading = document.createElement('h2');
  heading.textContent = s.mode === 'streaming' ? `条目 ${id} · 流式原文，确认后翻译` : `片段 ${id} · ${(start / 1000).toFixed(2)}–${(end / 1000).toFixed(2)} 秒`;
  const source = document.createElement('p'); source.className = 'source'; source.textContent = '等待识别';
  const translation = document.createElement('p'); translation.className = 'translation';
  const state = document.createElement('span'); state.className = 'segment-state'; state.textContent = '已提交';
  const stash = document.createElement('p'); stash.className = 'source-stash'; stash.hidden = true;
  stash.style.fontStyle = 'italic'; stash.style.opacity = '0.7'; stash.setAttribute('aria-label', '不确定识别暂存');
  if (s.mode === 'streaming') article.append(heading, source, stash, translation, state);
  else article.append(heading, source, translation, state);
  $('rows').append(article);
  const discuss = document.createElement('button'); discuss.type = 'button'; discuss.textContent = '带这句去解释 / 练习'; discuss.disabled = true;
  article.append(discuss);
  const entry = { article, source, stash, translation, state, discuss, id, session: s };
  discuss.onclick = () => {
    if (!entry.confirmed || !source.textContent.trim()) return;
    const sameSession = [...rows.values()].filter(other => other.session === s && other.confirmed && other.id !== id && other.source.textContent.trim());
    const before = sameSession.filter(other => other.id < id).slice(-2);
    const after = sameSession.filter(other => other.id > id).slice(0, 2);
    const context = []; let contextChars = 0;
    for (const other of [...before, ...after]) {
      const clip = {text: other.source.textContent.slice(0, 1400), translation: other.article.classList.contains('gap') ? '' : other.translation.textContent.slice(0, 1400), label: `邻近已确认字幕 · 条目 ${other.id}${other.source.textContent.length > 1400 || other.translation.textContent.length > 1400 ? '（截取）' : ''}`, source_id: `${s.id}:${other.id}`.slice(0, 200)};
      const chars = Object.values(clip).reduce((sum, value) => sum + value.length, 0);
      if (contextChars + chars > 6000) continue;
      context.push(clip); contextChars += chars;
    }
    if (!window.confirm(`将选中原句与 ${context.length} 段邻近已确认字幕带到主页面；先停止共享音频。识别可能有误，进入后仍需核对并手动应用。继续吗？`)) return;
    try {
      sessionStorage.setItem('neurosama:selected-source', JSON.stringify({
        text: source.textContent.slice(0, 3000), translation: translation.textContent.slice(0, 3000),
        label: `共享字幕的识别原文（请核对）${source.textContent.length > 3000 ? '（截取）' : ''}`, source_id: key.slice(0, 200), timestamp: Date.now(),
        context,
      }));
      stop('已停止共享，准备解释或练习'); location.assign('/');
    } catch { status('无法暂存原句；可手动复制到主页面，未跳转。'); }
  };
  rows.set(key, entry);
  while (rows.size > 100) {
    const [oldKey, old] = rows.entries().next().value;
    old.article.remove(); old.session.hideBefore = Math.max(old.session.hideBefore, old.id); rows.delete(oldKey);
  }
  $('empty').hidden = true;
  return entry;
}
function gap(entry, message) {
  if (!entry) return;
  entry.article.classList.add('gap'); entry.state.textContent = '音频缺口';
  entry.translation.textContent = message;
}
function submit(s) {
  const length = s.samples, start = s.segmentStart, voiced = s.voiced;
  const pieces = s.pieces;
  s.samples = 0; s.pieces = []; s.silent = 0; s.voiced = false;
  if (!voiced || length < MIN || !live(s)) return;
  const id = ++s.nextId, start_ms = start / RATE * 1000, end_ms = (start + length) / RATE * 1000;
  const entry = row(s, id, start_ms, end_ms);
  if (s.pending.size >= 3 || s.ws.bufferedAmount + length * 2 + 1024 > BUFFER_LIMIT) {
    gap(entry, '本地缺口：处理队列或网络缓冲已满，此完整片段未上传。'); return;
  }
  const joined = new Float32Array(length); let offset = 0;
  for (const piece of pieces) { joined.set(piece, offset); offset += piece.length; }
  try {
    if (s.ws.readyState !== WebSocket.OPEN) throw new Error('连接已关闭');
    s.pending.add(id);
    s.ws.send(pcm16(joined));
    s.ws.send(JSON.stringify({ type: 'segment', session_id: s.id, segment_id: id, start_ms, end_ms }));
  } catch { stop('上传失败，已释放音频设备；请重新开始'); }
}
function audio(s, chunk) {
  if (!live(s) || !s.ready) return;
  // Bound each local capture batch to one second before allocating/resampling.
  if (s.mode === 'streaming' && (!(chunk.sampleRate > 0) || !Number.isFinite(chunk.sampleRate) || chunk.microphone.length > chunk.sampleRate)) {
    stop('流式音频批次超过 1 秒或采样率无效，已停止；请重新开始'); return;
  }
  if (!s.resampler) s.resampler = new StreamingResampler(chunk.sampleRate, RATE);
  for (const frame of s.framer.push(s.resampler.push(chunk.microphone))) {
    if (s.mode === 'streaming') {
      if (!live(s)) return;
      if (s.ws.bufferedAmount + frame.length * 2 > BUFFER_LIMIT) {
        stop('流式网络缓冲已满，已停止以避免静默丢失音频；请重新开始'); return;
      }
      try {
        if (s.ws.readyState !== WebSocket.OPEN) throw new Error('连接已关闭');
        s.ws.send(pcm16(frame)); // Continuous 100ms PCM, including silence; server owns VAD.
      } catch { stop('上传失败，已释放音频设备；请重新开始'); return; }
      continue;
    }
    if (!live(s)) return;
    const position = s.position; s.position += frame.length;
    let energy = 0; for (const sample of frame) energy += sample * sample;
    const voiced = Math.sqrt(energy / frame.length) >= 0.012;
    if (!s.samples && !voiced) continue; // Pure silence stays local.
    if (!s.samples) s.segmentStart = position;
    s.pieces.push(frame); s.samples += frame.length; s.voiced ||= voiced;
    s.silent = voiced ? 0 : s.silent + frame.length;
    if (s.samples >= MAX || (s.samples >= MIN && s.silent >= RATE * .5)) submit(s);
  }
}
async function maybeCapture(s) {
  if (!live(s) || !s.ready || !s.stream || s.startingCapture) return;
  s.startingCapture = true;
  try {
    await s.context.resume();
    if (!live(s)) return;
    const capture = await createCapture({ context: s.context, stream: s.stream,
      onChunk: chunk => audio(s, chunk), onError: () => { if (live(s)) stop('音频采集失败，已释放设备'); } });
    if (!live(s)) { capture.stop(); return; }
    s.capture = capture; status(s.mode === 'streaming' ? '正在采集 · 流式原文，确认后翻译' : '正在采集 · 短段识别后翻译');
  } catch { if (live(s)) stop('无法启动音频采集，已释放设备'); }
}
function incomingStream(s, m) {
  if (m.type === 'error' && (m.item_id === undefined || m.fatal === true)) {
    stop(`服务错误：${text(m.code)} ${text(m.message)}`); return;
  }
  if (!['source_partial', 'source_final', 'translation', 'gap', 'error'].includes(m.type)) return;
  if (typeof m.item_id !== 'string' || !m.item_id || m.item_id.length > 512) return;
  let item = s.items.get(m.item_id);
  if (!item) {
    if (s.items.size >= 256) { stop('流式会话已达 256 条目上限，请重新开始'); return; }
    item = { id: ++s.nextId, final: false, done: false };
    s.items.set(m.item_id, item); s.pending.add(item.id);
  }
  if (item.done || (item.final && (m.type === 'source_partial' || m.type === 'source_final'))) return;
  const entry = row(s, item.id);
  if (m.type === 'source_partial' || m.type === 'source_final') {
    item.final = m.type === 'source_final';
    if (entry) {
      entry.confirmed = item.final; entry.discuss.disabled = !item.final || !text(m.text).trim();
      entry.source.textContent = text(m.text); // Every update replaces the entire hypothesis.
      const stash = item.final ? '' : text(m.stash);
      entry.stash.textContent = stash ? `不确定暂存：${stash}` : '';
      entry.stash.hidden = !stash;
      entry.state.textContent = item.final ? '原文已确认，待译' : '原文识别中（尚未确认，暂不翻译）';
    }
    return;
  }
  if (m.type === 'translation') {
    if (entry) { entry.translation.textContent = text(m.text); entry.state.textContent = '翻译完成'; }
    if (Number.isFinite(m.latency_ms) && m.latency_ms >= 0) $('latency').textContent = `服务端延迟：${Math.round(m.latency_ms)} ms`;
  } else gap(entry, m.type === 'error' ? `条目错误：${text(m.code)} ${text(m.message)}` : text(m.message) || '服务端缺口：此条目未能完成翻译。');
  item.done = true; s.pending.delete(item.id);
}
function incoming(s, event) {
  if (!live(s)) return;
  let m; try { m = JSON.parse(event.data); } catch { return; }
  if (!m || m.session_id !== s.id) return;
  if (m.type === 'ready') { s.ready = true; clearTimeout(s.timer); status('服务已就绪 · 等待音频授权'); void maybeCapture(s); return; }
  if (m.type === 'stopped') { stop('服务已停止'); return; }
  if (s.mode === 'streaming') { incomingStream(s, m); return; }
  if (m.type === 'error') {
    if (m.segment_id !== undefined) {
      if (s.pending.has(m.segment_id)) gap(rows.get(`${s.id}:${m.segment_id}`), `片段错误：${text(m.code)} ${text(m.message)}`);
    } else stop(`服务错误：${text(m.code)} ${text(m.message)}`);
    return;
  }
  if (!s.pending.has(m.segment_id)) return;
  const entry = rows.get(`${s.id}:${m.segment_id}`);
  if (m.type === 'source' && entry) { entry.source.textContent = text(m.text); entry.state.textContent = '已识别片段，待译'; entry.confirmed = true; entry.discuss.disabled = !text(m.text).trim(); }
  if (m.type === 'translation') {
    if (entry) { entry.translation.textContent = text(m.text); entry.state.textContent = '翻译完成'; }
    if (Number.isFinite(m.latency_ms) && m.latency_ms >= 0) $('latency').textContent = `服务端延迟：${Math.round(m.latency_ms)} ms`;
  }
  if (m.type === 'gap') { gap(entry, text(m.message) || '服务端缺口：此片段未能完成翻译。'); s.pending.delete(m.segment_id); }
  if (m.type === 'segment_done') {
    s.pending.delete(m.segment_id);
    if (entry && !entry.translation.textContent) entry.state.textContent = m.empty ? '未识别到语音' : '片段处理结束';
  }
}
async function start() {
  if (active) return;
  const display = $('source').value === 'display';
  const devices = navigator.mediaDevices, AC = window.AudioContext || window.webkitAudioContext;
  if (!AC || !window.WebSocket || !globalThis.crypto?.randomUUID || !devices || typeof devices[display ? 'getDisplayMedia' : 'getUserMedia'] !== 'function') {
    status('当前浏览器不支持所选音频来源或安全音频 API；请使用支持的浏览器与 HTTPS / localhost'); return;
  }
  const mode = $('backend').value === 'streaming' ? 'streaming' : 'segmented';
  const s = { epoch: ++epoch, id: crypto.randomUUID(), mode, items: new Map(), ready: false, pending: new Set(), hideBefore: 0,
    framer: new SampleFramer(mode === 'streaming' ? RATE / 10 : FRAME), pieces: [], samples: 0, position: 0, silent: 0, voiced: false, nextId: 0 };
  active = s;
  $('start').disabled = true; $('source').disabled = true; $('backend').disabled = true; $('stop').disabled = false;
  $('latency').textContent = '服务端延迟：—'; status('等待授权与服务就绪…');
  try {
    s.context = new AC();
    // Request display permission directly in the user gesture, not after a network await.
    const permission = display ? devices.getDisplayMedia({ video: true, audio: true }) : devices.getUserMedia({ audio: true, video: false });
    Promise.resolve(permission).then(stream => {
      if (!live(s)) { stopTracks(stream); return; }
      s.stream = stream;
      if (!stream.getAudioTracks().length) { stop('未共享音频：请重新选择来源并勾选「共享音频」'); return; }
      for (const track of stream.getTracks()) track.addEventListener('ended', () => { if (live(s)) stop('音频来源或屏幕共享已结束'); }, { once: true });
      void maybeCapture(s);
    }, () => { if (live(s)) stop('音频授权失败或已取消，已释放设备'); });
    s.ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${mode === 'streaming' ? '/ws/interpret-stream' : '/ws/interpret'}`);
    s.ws.onopen = () => {
      if (!live(s)) return;
      try { s.ws.send(JSON.stringify({ type: 'start', session_id: s.id })); } catch { stop('连接启动失败'); }
    };
    s.ws.onmessage = event => incoming(s, event);
    s.ws.onerror = () => { if (live(s)) stop('翻译连接错误，已释放设备'); };
    s.ws.onclose = () => { if (live(s)) stop('翻译连接已关闭，已释放设备'); };
    s.timer = setTimeout(() => { if (live(s)) stop('等待服务就绪超时，已释放设备'); }, 15000);
  } catch { if (live(s)) stop('无法启动音频来源或连接，已释放设备'); }
}
$('start').addEventListener('click', () => { void start(); });
$('stop').addEventListener('click', () => stop());
$('clear').addEventListener('click', () => {
  for (const entry of rows.values()) entry.session.hideBefore = Math.max(entry.session.hideBefore, entry.id);
  rows.clear(); $('rows').replaceChildren(); $('empty').hidden = false;
});
window.addEventListener('pagehide', () => stop('页面已离开，音频已释放'));
