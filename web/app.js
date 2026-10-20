/* ---------------------------------------------------------------
   前端：录音(PCM16@16k) -> WebSocket -> 流式字幕 + 分句音频播放
   播放期间可被打断（barge-in），这是语音角色的基本礼貌。
   形象（web/avatar）由 Avatar 驱动，本文件只喂事件和音频。
   --------------------------------------------------------------- */

import { avatar, boot } from "./avatar/index.js";
import { interactionState, reflectInteractionState } from './voice/interaction-state.js';
import { StreamingResampler, SampleFramer, pcm16 } from "./voice/pcm.js";
import { EnergyVad } from "./voice/vad.js";
import { PlaybackQueue } from "./voice/playback.js";
import { TurnGate } from "./voice/turns.js";
import { MicrophoneLease } from "./voice/microphone.js";
import { createCapture } from "./voice/capture.js";
import { DuplexGate } from "./voice/duplex.js";
import { PlaybackCheckpoint, VoiceMetrics } from "./voice/checkpoint.js";
import { mountVoiceBaseline } from './voice/baseline-ui.js';
import { mountDialogueReview } from './avatar/dialogue-review-ui.js';
import { mountEditorInsightVoice } from './voice/editor-insight.js';
import { mountEditorDiscussion } from './voice/editor-discussion.js';
import { bootAssistant, showAssistantResult } from "./assistant.js";
import { bootCompanion } from './companion.js';
import { mountStarmap } from './starmap/index.js';
import { mountExperience } from './experience.js';
import { appendContextUsage } from './context-usage.js';
import { mountModeStrip } from './mode-strip.js';
import { mountWorkDrawer } from './drawer.js';
import { mountExternalModel } from './external-model.js';
import { mountContextBudget } from './context-budget.js';
import { AmbientDriver } from "./avatar/drivers/ambient.js";
import { createGlimmer } from "./glimmer_bubble.js";
import { createHoloDeck, parseCmd, summarizeAqiBlocks } from "./blocks.js";
import { createAqiFlow, aqiPendingMessage } from "./aqi-flow.js";
import { normalizeUiNav, drawerViewFor, shouldCloseDrawer, shouldOpenSidePanel,
  shouldOpenStarmap, gazeAfter } from "./ui_nav.js";
bootAssistant().catch(error => console.warn('助手面板启动失败', error));
bootCompanion().catch(error => console.warn('共同事项启动失败', error));

const TARGET_RATE = 16000;
const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);

const el = (id) => document.getElementById(id);
const chat = el("chat");
const subtitle = el("subtitle");
const statusText = el("status");
const statusDot = el("statusDot");
const micBtn = el("micBtn");
const meterFill = el("meterFill");

let audioCtx = null;
let sourceNode = null; // Output only; never shared with the microphone.
let capture = null;
let capturePending = null;
let playAnalyser = null;
let outputGain = null;
let resampler = null;
let referenceResampler = null;
let framer = null;
let referenceFramer = null;
let playbackState = null;
let playbackBaselineTurn = null;
let ducked = false;
let candidateSince = null;
let submittedAt = null;
let firstAudioPending = false;
const checkpoint = new PlaybackCheckpoint({ onExpire: () => updateResume() });
const metrics = new VoiceMetrics();
let duplexMode = 'headset';
const duplex = new DuplexGate();
let recording = false;
let recordingMode = null;
let manualHeld = false;
let manualRequest = 0;
let manualTimer = null;
let sentSamples = 0;
let alwaysListen = false;
let disposed = false;
let micGeneration = 0;
let turnCounter = 0;
const turns = new TurnGate();
let resumeReviewId = null;
const dialogueReview = mountDialogueReview({ mount: el('dialogueReviewMount'), avatar,
  metadata: () => ({ model: avatar.rendererKind, page: 'chat' }) });
let editorInsightVoice = null;
let editorDiscussion = null;
let editorDiscussionTurn = null;
let editorSubmittedText = null;
const vad = new EnergyVad();
const microphone = new MicrophoneLease(() => navigator.mediaDevices.getUserMedia({
  audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
}));
let practiceMode = 'off';
let experienceMode = 'off';
const baselineTurns = new Map();
let baselineChunk = 0;
let baselineEnergy = null;
let baselineGateOnset = false;
const baseline = mountVoiceBaseline({ root: el('voiceBaselineMount'), getContext: () => {
  const settings = microphone.stream?.getAudioTracks?.()[0]?.getSettings?.() || {};
  return { mode: duplexMode, captureKind: capture?.kind || 'none',
    timingSource: capture?.timingSource || null,
    referenceAvailable: capture ? !!capture.hasReference : null,
    activity: experienceMode !== 'off' ? experienceMode : practiceMode,
    endpointWaitMs: vad.pauseMs,
    echoCancellation: typeof settings.echoCancellation === 'boolean' ? settings.echoCancellation : null,
    noiseSuppression: typeof settings.noiseSuppression === 'boolean' ? settings.noiseSuppression : null,
    autoGainControl: typeof settings.autoGainControl === 'boolean' ? settings.autoGainControl : null };
} });
let emotionOverride = null;
let pendingText = "";
let currentBubble = null;
let pendingConfirmation = null;
const experienceUI = mountExperience({
  sendConfig: payload => {
    if (ws.readyState !== WebSocket.OPEN) throw new Error('连接未就绪');
    ws.send(JSON.stringify(payload));
  },
  onSwitch: () => {
    if (ws.readyState !== WebSocket.OPEN) throw new Error('连接未就绪');
    baseline.invalidate('mode_changed');
    editorDiscussion?.end();
    manualHeld = false; manualRequest++; cancelRecording(); cancelTurn(); resetDuplex(); vad.reset();
    checkpoint.clear(); updateResume();
    if (!alwaysListen) teardownMic();
  },
  sendText: text => {
    if (ws.readyState !== WebSocket.OPEN) throw new Error('连接未就绪');
    el('textInput').value = text;
    el('sendBtn').click();
  },
});
experienceUI.importSelection();

/* ---------------- 模式状态条 / 工作台抽屉 / 设置面板（打磨轮 U-1~U-5） ---------------- */
const EXPERIENCE_LABELS = { listen: "听懂原句", expression: "表达练习", theatre: "虚构小剧场",
  focus: "专注陪伴", walk: "散步陪伴", winddown: "睡前放松", vent: "倾诉", discuss: "一起讨论" };
const PRACTICE_LABELS = { free: "英语自由聊", cafe: "咖啡店点单", interview: "面试练习" };
let microFlow = false;          // 本轮回复走了微句流（audio.chunk / speech_plan）
let narrationActive = false;    // 星图讲述轮
let proactiveActive = false;    // 主动开口 / 自进化话题轮
let selfEvolutionUntil = 0;
let selfEvolutionTimer = null;
let modeSignature = "";
const modeStrip = mountModeStrip(el("modeStrip"));
const contextBudget = mountContextBudget(el("contextBudgetStatus"));

function syncModeStrip() {
  if (!modeStrip) return;
  const stopActive = recording || manualHeld || !!turns.id || playback.busy;
  const signature = JSON.stringify([alwaysListen, experienceMode, practiceMode, microFlow,
    narrationActive, proactiveActive, Date.now() < selfEvolutionUntil, stopActive]);
  if (signature === modeSignature) return;
  modeSignature = signature;
  modeStrip.update({
    continuous: alwaysListen,
    experience: experienceMode !== "off" ? (EXPERIENCE_LABELS[experienceMode] || experienceMode) : "",
    practice: practiceMode !== "off" ? (PRACTICE_LABELS[practiceMode] || practiceMode) : "",
    micro: microFlow,
    starmap: narrationActive,
    self: proactiveActive ? "主动开口" : (Date.now() < selfEvolutionUntil ? "自进化" : ""),
  });
  const stop = el("stopBtn");
  if (stop) stop.classList.toggle("is-active", stopActive);
}

function noteSelfEvolution() {
  selfEvolutionUntil = Date.now() + 20000;
  if (selfEvolutionTimer !== null) clearTimeout(selfEvolutionTimer);
  selfEvolutionTimer = setTimeout(() => { selfEvolutionTimer = null; syncModeStrip(); }, 20000);
  syncModeStrip();
}

const settingsPanel = el("settingsPanel");
if (settingsPanel) {
  settingsPanel.addEventListener("toggle", () => {
    if (!settingsPanel.open) return;
    drawer?.close(false);
    const side = el("sidePanel");
    if (side?.classList.contains("is-open")) el("sidebarToggle")?.click();
    const taste = el("voiceTasteFrame");
    if (taste && !taste.getAttribute("src")) taste.src = taste.dataset.src;
  });
  document.addEventListener("keydown", (event) => {
    if (settingsPanel.open && event.key === "Escape" && !event.isComposing) {
      event.preventDefault();
      settingsPanel.open = false;
      el("settingsToggle")?.focus({ preventScroll: true });
    }
  });
  // 全屏视图（一起写 / 星图）接管舞台时收起设置面板，避免遮住宽屏编辑器。
  window.addEventListener("aqi-fullview", () => {
    if (settingsPanel.open) settingsPanel.open = false;
  });
}

const drawer = mountWorkDrawer(el("workDrawer"), {
  onWriting: () => el("writingToggle")?.click(),
  onCompanion: () => {
    const mount = el("companionMount");
    if (!mount) return;
    mount.scrollIntoView({ behavior: "smooth", block: "nearest" });
    mount.tabIndex = -1;
    mount.focus({ preventScroll: true });
  },
  onPractice: () => {
    const section = el("practiceSection");
    if (settingsPanel && !settingsPanel.open) settingsPanel.open = true;
    section?.scrollIntoView({ block: "nearest" });
    el("practiceMode")?.focus({ preventScroll: true });
  },
});

let selectedExternalModel = "";
const externalModel = mountExternalModel({
  input: el("externalModelUrl"),
  button: el("externalModelLoad"),
  status: el("externalModelStatus"),
  load: async (url) => {
    await avatar.setRenderer("vrm", url);
    const select = el("avatarRenderer");
    if (select && !select.querySelector('option[value="external"]')) {
      select.add(new Option("外部 .vrm（自定义）", "external"));
    }
    if (select) select.value = "external";
    selectedExternalModel = url;
    const status = el("avatarRendererStatus");
    if (status) status.textContent = "外部 .vrm 已启用；失败时会自动回到上一个形象。";
  },
});

/* ---------------- 星座连线（对话=星座：星点与星点之间连线） ---------------- */
let constellationQueued = false;
function drawConstellation() {
  try {
    const svgNS = "http://www.w3.org/2000/svg";
    let svg = chat.querySelector(":scope > .chat-constellation");
    if (!svg) {
      svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("class", "chat-constellation");
      svg.setAttribute("aria-hidden", "true");
      chat.appendChild(svg);
    }
    const w = chat.clientWidth;
    const h = chat.scrollHeight;
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.style.height = `${h}px`;
    const pts = [];
    for (const m of chat.querySelectorAll(".msg")) {
      if (m.classList.contains("thinking")) continue;
      const y = m.offsetTop + 21.5;
      const x = m.classList.contains("user") ? m.offsetLeft + m.offsetWidth + 12.5 : m.offsetLeft - 12.5;
      if (Number.isFinite(x) && Number.isFinite(y)) pts.push({ x, y });
    }
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const g = document.createElementNS(svgNS, "g");
    g.setAttribute("stroke", "#e8c26a33");
    g.setAttribute("stroke-width", "1");
    for (let i = 0; i + 1 < pts.length; i++) {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", pts[i].x); line.setAttribute("y1", pts[i].y);
      line.setAttribute("x2", pts[i + 1].x); line.setAttribute("y2", pts[i + 1].y);
      g.appendChild(line);
    }
    svg.appendChild(g);
  } catch { /* 连线失败不影响对话本身 */ }
}
function scheduleConstellation() {
  if (constellationQueued) return;
  constellationQueued = true;
  requestAnimationFrame(() => { constellationQueued = false; drawConstellation(); });
}
scheduleConstellation();
window.addEventListener("resize", scheduleConstellation);
new MutationObserver(muts => {
  for (const m of muts) {
    const t = m.target.nodeType === Node.TEXT_NODE ? m.target.parentElement : m.target;
    if (t && t.closest && t.closest(".chat-constellation")) continue;
    scheduleConstellation();
    return;
  }
}).observe(chat, { childList: true, subtree: true, characterData: true });

/* ---------------- 文本气泡 ---------------- */
function addBubble(role, text) {
  const node = document.createElement("div");
  node.className = `msg ${role === "user" ? "user" : "bot"}`;
  node.textContent = text;
  if (editorDiscussionTurn && turns.id === editorDiscussionTurn) node.classList.add('editor-discussion-message');
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  node.appendChild(time);
  chat.appendChild(node);
  scheduleConstellation();
  return node;
}

function setStatus(text, state, interaction = state === 'busy' ? 'preparing' : state === 'on' ? 'idle' : 'unavailable') {
  statusText.textContent = text;
  statusDot.className = `dot ${state || ""}`;
  reflectInteractionState(interaction, text);
  avatar.setDialogueState(interaction);
}

/* ---------------- 音频 / 轮次 ---------------- */
function send(message) {
  if (ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(message)); return true;
}
function ensureCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    playAnalyser = audioCtx.createAnalyser();
    playAnalyser.fftSize = 512;
    playAnalyser.smoothingTimeConstant = 0.3;
    outputGain = audioCtx.createGain();
    playAnalyser.connect(outputGain);
    outputGain.connect(audioCtx.destination);
    avatar.attachOutputAnalyser(playAnalyser);
  }
  if (audioCtx.state === "suspended") audioCtx.resume().catch(error => voiceError(error));
  return audioCtx;
}
function updateVoiceDiagnostics() {
  const node = el('voiceDiagnostics');
  if (!node) return;
  const data = metrics.snapshot();
  const latency = data.timings.submitToFirstAudio;
  node.textContent = `${capture?.kind || '未采集'} · ${capture?.hasReference ? '有播放参照' : '无播放参照'} · 候选 ${data.counts.candidate || 0} / 抢话 ${data.counts.barge || 0} / 回声帧 ${data.counts.echo || 0}` +
    (latency ? ` · 提交→浏览器播放启动 p50 ${latency.p50}ms / p95 ${latency.p95}ms（${latency.n}次）` : '');
}
function setDucked(value) {
  if (ducked === value) return;
  ducked = value;
  if (outputGain) {
    const gain = outputGain.gain;
    if (gain.setTargetAtTime) { gain.cancelScheduledValues(audioCtx.currentTime); gain.setTargetAtTime(value ? .22 : 1, audioCtx.currentTime, .025); }
    else gain.value = value ? .22 : 1;
  }
}
function updateResume() {
  const button = el('resumeSpeechBtn');
  if (button) { button.hidden = !checkpoint.available; button.disabled = recording || manualHeld || !!turns.id || playback.busy; }
}
function resetDuplex(options) { duplex.reset(options); candidateSince = null; baselineGateOnset = false; setDucked(false); }
function voiceError(error) {
  dialogueReview?.event({ type: 'error', turn_id: resumeReviewId || turns.id, meta: { reason: 'playback-error' } });
  if (resumeReviewId) { dialogueReview?.endTurn(resumeReviewId, 'error'); resumeReviewId = null; }
  editorInsightVoice?.fail(error);
  subtitle.textContent = `语音提示：${error.message || error}`;
  setStatus("语音暂不可用", "", currentInteraction().id);
}
function currentInteraction() {
  return interactionState({recording, manual:recordingMode==='manual', playing:!!sourceNode, queued:playback.busy, turn:!!turns.id, continuous:alwaysListen, ducked});
}
function idleStatus() {
  updateResume();
  const state = currentInteraction();
  setStatus(state.text, state.tone, state.id);
  syncModeStrip();
}
function finishTurn() {
  if (!turns.done || playback.busy || recording) return;
  dialogueReview?.endTurn(turns.id, 'complete');
  editorInsightVoice?.finish(turns.id);
  turns.clear(); currentBubble = null; pendingText = "";
  avatar.stopSpeaking(); idleStatus();
}
const playback = new PlaybackQueue({
  decode: async item => {
    recordBaselineItem('decode_start', item);
    try {
      let buffer = item.buffer;
      if (!buffer) {
        if (item.data.length > 12 * 1024 * 1024) throw new Error("音频片段过大，已跳过");
        const bytes = Uint8Array.from(atob(item.data), c => c.charCodeAt(0));
        buffer = await ensureCtx().decodeAudioData(bytes.buffer);
      }
      recordBaselineItem('decode_end', item); return buffer;
    } catch (error) { recordBaselineItem('decode_error', item); throw error; }
  },
  play: (buffer, item, done) => {
    const ctx = ensureCtx();
    const source = ctx.createBufferSource(); sourceNode = source;
    source.buffer = buffer; source.connect(playAnalyser);
    const cleanup = () => {
      source.onended = null;
      try { source.disconnect(); } catch (_) {}
      if (sourceNode === source) { sourceNode = null; playbackState = null; playbackBaselineTurn = null; }
    };
    source.onended = () => {
      dialogueReview?.event({ type: 'playback-end', turn_id: item.turn_id || item._baselineTurn, meta: { reason: 'natural', audioTime: ctx.currentTime } });
      recordBaselineItem('playback_end', item, { reason: 'natural' }); cleanup(); done();
    };
    try {
      const at = ctx.currentTime;
      const offset = Math.max(0, Math.min(item.offset || 0, buffer.duration));
      playbackState = { buffer, offset, startedAt: at, text: item.text || '', emotion: item.emotion || '' };
      playbackBaselineTurn = item.turn_id || item._baselineTurn || null;
      source.start(at, offset);
      dialogueReview?.event({ type: 'playback-start', turn_id: item.turn_id || item._baselineTurn,
        text: item.resumed ? '' : item.text || '', meta: { source: item.resumed ? 'resume' : 'playback',
          reason: item.resumed ? 'audio-only-resume-no-text-alignment' : 'audio-start', durationMs: (buffer.duration - offset) * 1000, audioTime: at } });
      editorInsightVoice?.playing(item.turn_id);
      recordBaselineItem('playback_start', item, { durationMs: (buffer.duration - offset) * 1000 });
      if (!item.resumed && firstAudioPending && submittedAt !== null) {
        metrics.timing('submitToFirstAudio', performance.now() - submittedAt); firstAudioPending = false; updateVoiceDiagnostics();
      }
      if (item.text) subtitle.textContent = item.text;
      avatar.setEmotion(item.emotion || "");
      avatar.setAudioContext(ctx);
      avatar.speak(item.resumed ? '' : (item.text || ''), ctx, at, buffer.duration - offset, {
        // Audio-only continuation intentionally does not replay prior gestures.
        performance: item.resumed ? { version: 1, source: 'llm', cues: [] } : item.performance,
        wordTimings: item.resumed ? undefined : item.word_timings,
        chunk: item.resumed ? undefined : item.chunk,
      });
      idleStatus();
    } catch (error) { cleanup(); try { source.stop(); } catch (_) {} throw error; }
    return () => {
      dialogueReview?.event({ type: 'playback-end', turn_id: item.turn_id || item._baselineTurn, meta: { reason: 'cancelled', audioTime: ctx.currentTime } });
      cleanup(); recordBaselineItem('playback_stop_requested', item, { reason: 'cancelled' }); try { source.stop(); } catch (_) {}
    };
  },
  onIdle: () => {
    if (resumeReviewId) { dialogueReview?.endTurn(resumeReviewId, 'complete'); resumeReviewId = null; }
    avatar.stopSpeaking({turnPending: !!turns.id && !turns.done}); finishTurn(); idleStatus();
  },
  onError: voiceError,
});
editorInsightVoice = mountEditorInsightVoice({
  available: () => !disposed && !document.hidden,
  ensureAudio: ensureCtx,
  beginLocal: () => {
    manualHeld = false; manualRequest++; cancelRecording(); cancelTurn(); resetDuplex(); vad.reset();
    if (!alwaysListen) teardownMic();
    checkpoint.clear(); updateResume();
    const id = `editor-insight-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${++turnCounter}`}`;
    turns.begin(id);
    dialogueReview?.beginTurn({ id, input: '观点语音播报' });
    submittedAt = null; firstAudioPending = false;
    setStatus('正在准备这条观点…', 'busy');
    return id;
  },
  enqueueAudio: item => {
    const accepted = playback.enqueue(item);
    if (accepted && turns.id === item.turn_id) dialogueReview?.event({ type: 'done', turn_id: item.turn_id, text: item.text || '' });
    if (accepted && turns.id === item.turn_id) { turns.done = true; finishTurn(); }
    return accepted;
  },
  stopLocal: id => {
    if (!id || turns.id !== id) return;
    stopPlayback(); dialogueReview?.endTurn(id, 'interrupted'); turns.clear(); currentBubble = null; pendingText = '';
    resetDuplex(); idleStatus();
  },
  retireServerTurn: id => turns.retire(id),
});
const starmap = mountStarmap(el('starmapMount'), {
  send,
  stopTurn: () => cancelTurn(),
  isIdle: () => !turns.id && !playback.busy && !recording,
});

/* ---------------- V2 三梯度主动在场：micro_act / mumble / 微光气泡（协议 §16） ---------------- */
const AMBIENT_SAMPLES = window.__PERSONA_SAMPLES__ || { stage_samples: {} };
let mumbleActive = false;
let voicedMumbleText = "";   // 当前有声搭话的文本：它的气泡不随 mumble 轮次开始被收回

function applyMicroAct(descriptor) {
  // 复用真实 Avatar 动作：眨眼 / idle 微动层唤醒；未知或缺失 descriptor 安静降级，不伪造效果。
  if (!descriptor) return;
  (window.__microActLog = window.__microActLog || []).push(descriptor.kind);
  try {
    if (descriptor.kind === "blink") avatar.blinkEyes();
    else avatar.idle?.wake(Math.min(1, 0.4 + (Number(descriptor.duration) || 0.5) * 0.4));
  } catch (_) { /* 舞台降级：不影响页面其余部分 */ }
}

const glimmerEl = document.createElement("div");
glimmerEl.className = "glimmer-bubble";
glimmerEl.setAttribute("role", "status");
glimmerEl.hidden = true;
document.body.append(glimmerEl);

function renderGlimmer() {
  const current = glimmer.current;
  if (!current || glimmer.status === "hidden") { glimmerEl.hidden = true; glimmerEl.textContent = ""; return; }
  glimmerEl.hidden = false;
  glimmerEl.textContent = "";
  const text = document.createElement("span");
  text.className = "glimmer-text";
  text.textContent = current.text;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "glimmer-close";
  close.textContent = "×";
  close.setAttribute("aria-label", "关闭这个念头");
  close.addEventListener("click", (event) => { event.stopPropagation(); glimmer.dismiss(); renderGlimmer(); });
  glimmerEl.append(text, close);
}

const glimmer = createGlimmer({
  onExpand: (ctx) => {
    // 把真实上下文带进普通对话：经既有输入框发起一轮，让阿栖接着这个念头讲。
    try { el("textInput").value = ctx.text; el("sendBtn").click(); } catch (_) {}
  },
  onDismiss: () => renderGlimmer(),
  onExpire: () => renderGlimmer(),
});
glimmerEl.addEventListener("click", () => { if (glimmer.expand()) renderGlimmer(); });

const ambientDriver = new AmbientDriver({ applyMicroAct, samples: AMBIENT_SAMPLES });

function handleAmbient(msg) {
  if (!proactiveLocallyIdle()) return;   // 用户在用时不打扰
  if (msg.type === "micro_act") ambientDriver.handleEvent(msg);
  else if (msg.type === "mumble") {
    // 服务端 expires_at 是服务端 epoch 秒，不能直接和 Date.now() 比；这里转成本端相对过期。
    const ttl = (msg.bubble && typeof msg.bubble.ttl_ms === "number") ? msg.bubble.ttl_ms : 12000;
    // 有声微气泡：voice=true 的搭话随后会走 mumble 轮次真声播出，
    // 记住这段文本，让轮次开始时不收回属于它自己的气泡。
    if (msg.voice) voicedMumbleText = msg.text || "";
    if (glimmer.show({ ...msg, expires_at: Date.now() + ttl })) renderGlimmer();
  }
}

// 微光气泡自然消散：只在可见时轮询，避免常驻开销。
setInterval(() => { if (glimmer.status !== "hidden") { glimmer.poll(Date.now()); renderGlimmer(); } }, 1000);

if (!document.getElementById("glimmer-style")) {
  const style = document.createElement("style");
  style.id = "glimmer-style";
  style.textContent = ".glimmer-bubble{position:fixed;right:20px;bottom:112px;z-index:60;max-width:min(20rem,72vw);display:flex;gap:8px;align-items:flex-start;padding:10px 12px;border-radius:12px;background:rgba(24,28,24,.85);color:#eef1ea;font-size:13px;line-height:1.5;box-shadow:0 6px 24px rgba(0,0,0,.3);cursor:pointer}"
    + ".glimmer-bubble[hidden]{display:none!important}"
    + ".glimmer-text{flex:1;white-space:pre-wrap}"
    + ".glimmer-close{flex:none;border:0;background:transparent;color:#9cc7a9;font-size:16px;line-height:1;cursor:pointer;padding:0 2px}"
    + ".glimmer-close:hover{color:#eef1ea}";
  document.head.append(style);
}

/* ---------------- 协议 §23：动态积木（Voice-First, Tail-Block） ----------------
 * 小溪开口只说一两句口语；状态、选项、卡片数据以 aqi_blocks 消息随路下发，
 * 落在舞台侧翼 Holo-Deck；layout=drawer_ops 时自动拉开战况抽屉渲染。
 * 去文字框化：bot 聊天气泡不再出场——口头台词住在头顶微光气泡里，说完自然淡出，
 * 视觉重心交给卡片与联动滑出的工作台抽屉。 */
const VOICE_FIRST = window.__AQI_VOICE_FIRST__ !== false;   // 默认开启；测试可显式关闭
const holoDeck = createHoloDeck(el("holoDeck"), { onAction: handleAqiAction });
const opsDeck = createHoloDeck(el("opsAqiBlocks"), { onAction: handleAqiAction });
const AQI_NAV_VIEWS = { ops: "ops", notes: "writing", threads: "companion", starmap: "starmap" };

function navDrawerView(nav) {
  return AQI_NAV_VIEWS[nav] || null;
}

function gazeAtCard(direction) {
  // 她亲手把卡片递给你看：视线微微转向卡片方向；舞台降级时安全忽略。
  try {
    const point = direction === "drawer" ? { x: -0.15, y: 0.8 } : { x: 0.9, y: 0.05 };
    avatar.idle?.setGazeTarget?.(point.x, point.y);
    (window.__aqiGazeLog = window.__aqiGazeLog || []).push(direction || "holo_right");
  } catch (_) { /* 形象层降级不影响积木本身 */ }
}

function renderAqiBlocksNow(msg) {
  const summary = summarizeAqiBlocks(msg);
  if (!summary || !summary.count) return;
  const deck = summary.layout === "drawer_ops" ? opsDeck : holoDeck;
  deck.render(msg);
  gazeAtCard(summary.gaze || (summary.layout === "drawer_ops" ? "drawer" : "holo_right"));
  if (summary.layout === "drawer_ops") { drawer?.open(); drawer?.selectView("ops"); }
  if (summary.nav) {
    const view = navDrawerView(summary.nav);
    if (view) { drawer?.open(); drawer?.selectView(view); }
  }
}

/* 协议 §23.6 心流防打扰：打字/写作时卡片不弹，只点亮战况胶囊报数；
 * 停手过一个静默窗后按原序补弹，内容不改。守卫只延迟，不丢弃。
 * 注意：只认真实 input（IME 的主力路径），不认快捷键；程序化赋值也不算。 */
const aqiFlow = createAqiFlow({
  isWriting: () => document.body.classList.contains("is-writing"),
});
let aqiDrainTimer = null;

function showAqiHeld(count) {
  const box = el("opsPending");
  if (!box) return;
  box.replaceChildren();
  box.hidden = false;
  box.dataset.quiet = "true";
  const dot = document.createElement("span");
  dot.className = "ops-breathe";
  const text = document.createElement("span");
  text.className = "ops-pending-text";
  text.textContent = aqiPendingMessage(count, true);
  box.append(dot, text);
}

function hideAqiHeld() {
  const box = el("opsPending");
  if (!box || box.hidden) return;
  // 不抢 executive.js 的“待验收”报数：只熄灭自己写的那句。
  if (!(box.textContent || "").includes("她递了")) return;
  box.hidden = true;
  box.replaceChildren();
}

function scheduleAqiDrain() {
  if (aqiDrainTimer !== null) return;
  const tick = () => {
    if (!aqiFlow.count) { aqiDrainTimer = null; return; }
    if (aqiFlow.isQuiet()) { aqiDrainTimer = setTimeout(tick, 120); return; }
    aqiDrainTimer = null;
    for (const held of aqiFlow.drain()) renderAqiBlocksNow(held);
    hideAqiHeld();
  };
  aqiDrainTimer = setTimeout(tick, 120);
}

function handleAqiBlocks(msg) {
  if (aqiFlow.isQuiet()) {
    aqiFlow.hold(msg);
    showAqiHeld(aqiFlow.count);
    scheduleAqiDrain();
    return;
  }
  renderAqiBlocksNow(msg);
}

el("textInput")?.addEventListener("input", (event) => {
  if (event.isTrusted) aqiFlow.markTyping();
});
window.addEventListener("message", (event) => {
  if (event.origin !== location.origin) return;
  if (event.data?.type === "aqi-notes-typing") aqiFlow.markTyping();
});

function handleAqiAction(cmd, block) {
  if (typeof cmd !== "string" || !cmd) return;
  const { verb, payload } = parseCmd(cmd);
  if (!verb) return;
  if (verb === "ui") {
    if (payload === "starmap") { el("starmapToggle")?.click(); return; }
    if (payload.startsWith("drawer:")) {
      const wanted = payload.slice(7);
      const view = navDrawerView(wanted) || wanted;
      drawer?.open(); drawer?.selectView(view);
      return;
    }
    if (payload.startsWith("preview:")) {
      // 本地预览：拉开战况抽屉并高亮对应成果 ref（executive.js 消费）。
      drawer?.open(); drawer?.selectView("ops");
      window.dispatchEvent(new CustomEvent("aqi-block-preview", { detail: { ref: payload.slice(8) } }));
      return;
    }
    return;   // 未知 ui 动词：协议 §23.3 规定忽略，无副作用
  }
  if (verb === "say") {
    // 作为一轮普通用户话语发回后端：走既有 text 链路，小溪语音确认。
    const text = payload.trim();
    if (!text) return;
    const input = el("textInput");
    if (input) { input.value = text; el("sendBtn")?.click(); }
    return;
  }
  if (verb === "choice") {
    // 协议 §23.3：点同一张卡的第 <index> 个胶囊，等价于直接点那颗胶囊。
    // 越界、坏 index、没有 options 一律忽略，不抛错。
    const index = Number.parseInt(payload, 10);
    const options = Array.isArray(block?.options) ? block.options : [];
    if (!Number.isInteger(index) || index < 0 || index >= options.length) return;
    const cmd = options[index]?.cmd;
    if (typeof cmd === "string" && cmd) handleAqiAction(cmd, block);
    return;
  }
  if (verb === "dispatch") {
    if (ws.readyState !== WebSocket.OPEN) {
      subtitle.textContent = "连接暂不可用，这单没收工；稍后再点。";
      return;
    }
    send({ type: "aqi_action", cmd });
    return;
  }
  // 未知业务动词：忽略。
}

/* 去文字框化：小溪的口头台词住在头顶微光气泡里（同 event_id 换文 + 顺延消散）。 */
let spokenLineId = null;
function showSpokenLine(text, ttlMs = 9000) {
  if (!VOICE_FIRST) return;
  const value = typeof text === "string" ? text.trim() : "";
  if (!value) return;
  const id = `turn_${turns.id || "ambient"}`;
  if (glimmer.current && glimmer.current.event_id === id) glimmer.update(value, ttlMs);
  else if (glimmer.show({ event_id: id, text: value, expires_at: Date.now() + ttlMs })) spokenLineId = id;
  renderGlimmer();
}

/* ---------------- 陪伴心流三态胶囊（Presence Mode Capsule）---------------- * 替代原来平铺的两个工业风 Switch。协议零破坏：底层布尔位仍是
 * #memoryToggle（记住）/ #proactiveToggle（主动开口）两个隐藏 checkbox，
 * WS config 的 memory / proactivity 字段与 /api/memory/toggle 契约原样复用；
 * 本模块只把两颗生硬滑块收敛为一枚循环切换的高颜值胶囊。 */
const PRESENCE_MODES = [
  { id: "lively", glyph: "✨", label: "灵动伴随", memory: true, proactive: true,
    bubble: "那我看到好玩的、或者有想法了就随时跟你说咯！",
    react: () => { avatar.gesture?.play?.("nod", avatar.clock); avatar.setEyeExpression?.("happy"); } },
  { id: "quiet", glyph: "🌙", label: "静默旁观", memory: true, proactive: false,
    bubble: "好嘞，我安安静静陪着你，不打岔~",
    react: () => { avatar.gesture?.play?.("nod", avatar.clock); } },
  { id: "off", glyph: "🍃", label: "无痕闲聊", memory: false, proactive: false,
    bubble: "明白，现在开始说的悄悄话，我绝不往小本本上记。",
    react: () => { avatar.blinkEyes?.(); } },
];
const presenceCapsule = el("presenceCapsule");

/* 胶囊是布尔位的纯视图：隐藏 checkbox 永远是唯一状态源，
 * 测试或脚本直接拨动 checkbox 时，视觉与循环顺序都不会漂移。 */
function currentPresenceMode() {
  const memory = !!el("memoryToggle")?.checked;
  const proactive = !!el("proactiveToggle")?.checked;
  return PRESENCE_MODES.find((mode) => mode.memory === memory && mode.proactive === proactive)
    || PRESENCE_MODES[0];
}

function renderPresenceCapsule() {
  if (!presenceCapsule) return;
  const mode = currentPresenceMode();
  presenceCapsule.classList.toggle("is-lively", mode.id === "lively");
  presenceCapsule.classList.toggle("is-quiet", mode.id === "quiet");
  presenceCapsule.classList.toggle("is-off", mode.id === "off");
  const glyph = presenceCapsule.querySelector(".presence-glyph");
  const label = presenceCapsule.querySelector(".presence-label");
  if (glyph) glyph.textContent = mode.glyph;
  if (label) label.textContent = mode.label;
  presenceCapsule.setAttribute("aria-label", `陪伴方式：${mode.label}，点击切换`);
  presenceCapsule.title = `陪伴方式：${mode.label}（点击在 灵动伴随 / 静默旁观 / 无痕闲聊 间切换）`;
}

function applyPresenceMode(index) {
  if (!presenceCapsule) return;
  const next = PRESENCE_MODES[(index + PRESENCE_MODES.length) % PRESENCE_MODES.length];
  const memoryBox = el("memoryToggle");
  const proactiveBox = el("proactiveToggle");
  if (!memoryBox || !proactiveBox) return;
  const memoryChanged = memoryBox.checked !== next.memory;
  memoryBox.checked = next.memory;          // 底层布尔位：协议字段由此派生
  proactiveBox.checked = next.proactive;
  renderPresenceCapsule();
  pushConfig();                              // 既有同步通道，零新协议
  if (memoryChanged) persistMemoryToggle(next.memory);
  // 切换瞬间反馈：微表情 + 头顶微光气泡确认
  try { next.react(); } catch (_) { /* 形象层降级不影响设置本身 */ }
  if (glimmer.show({ event_id: `presence_${next.id}_${Date.now()}`, text: next.bubble,
                     expires_at: Date.now() + 9000 })) {
    renderGlimmer();
  }
}

if (presenceCapsule) {
  presenceCapsule.addEventListener("click", () => {
    const current = PRESENCE_MODES.indexOf(currentPresenceMode());
    applyPresenceMode(current + 1);
  });
  renderPresenceCapsule();
}
const discussionBanner = document.createElement('section');
discussionBanner.id = 'editorDiscussion'; discussionBanner.className = 'editor-discussion';
discussionBanner.hidden = true; discussionBanner.setAttribute('aria-label', '围绕稿件观点继续讨论');
const discussionPoint = document.createElement('p');
const discussionHint = document.createElement('small');
const discussionActions = document.createElement('div'); discussionActions.className = 'editor-discussion-actions';
const returnToDraft = document.createElement('button'); returnToDraft.type = 'button'; returnToDraft.textContent = '回到稿子';
const endDiscussion = document.createElement('button'); endDiscussion.type = 'button'; endDiscussion.textContent = '结束讨论';
discussionActions.append(returnToDraft, endDiscussion);
discussionBanner.append(discussionPoint, discussionHint, discussionActions);
el('textInput').closest('.composer').prepend(discussionBanner);
const ordinaryPlaceholder = el('textInput').placeholder;
const ordinaryMaxLength = el('textInput').getAttribute('maxlength');
function stopDiscussionTurn() {
  if (editorDiscussionTurn && turns.id === editorDiscussionTurn) {
    manualHeld = false; manualRequest++; cancelRecording(); cancelTurn(); resetDuplex(); vad.reset();
    checkpoint.clear(); updateResume();
    if (!alwaysListen) teardownMic();
  }
}
editorDiscussion = mountEditorDiscussion({
  available: () => !disposed && !document.hidden && ws.readyState === WebSocket.OPEN,
  onChange: context => {
    discussionBanner.hidden = !context;
    document.body.classList.toggle('has-editor-discussion', !!context);
    discussionBanner.classList.toggle('is-invalid', !!context?.invalid);
    discussionPoint.textContent = context ? (context.invalid || `正在聊这一点：${context.point}`) : '';
    discussionHint.textContent = context?.invalid ? '回到稿子重新看看，或结束讨论继续聊天。' : '可以问“为什么”，也可以不同意或一起想怎么改。';
    el('textInput').placeholder = context ? '围绕这条观点，继续说…' : ordinaryPlaceholder;
    if (context) el('textInput').setAttribute('maxlength', '1000');
    else if (ordinaryMaxLength === null) el('textInput').removeAttribute('maxlength');
    else el('textInput').setAttribute('maxlength', ordinaryMaxLength);
  },
  onStart: () => {
    manualHeld = false; manualRequest++; cancelRecording(); cancelTurn(); resetDuplex(); vad.reset();
    checkpoint.clear(); updateResume(); if (!alwaysListen) teardownMic();
    window.dispatchEvent(new Event('aqi-editor-discussion-start'));
    subtitle.textContent = '接着聊刚才这一点。按住说话，或者直接打字。';
    el('textInput').focus({ preventScroll: true });
  },
  onInvalidate: stopDiscussionTurn,
  onEnd: stopDiscussionTurn,
});
returnToDraft.onclick = () => window.dispatchEvent(new Event('aqi-editor-discussion-return'));
endDiscussion.onclick = () => { editorDiscussion.end(); subtitle.textContent = '已结束这条观点的讨论，继续聊。'; };
function stopPlayback() {
  playback.stop();
  if (resumeReviewId) { dialogueReview?.endTurn(resumeReviewId, 'interrupted'); resumeReviewId = null; }
  setDucked(false); avatar.stopSpeaking();
}
function cancelTurn(preserveAudio = false) {
  const cancelledId = turns.id;
  if (cancelledId) dialogueReview?.event({ type: 'interrupted', turn_id: cancelledId });
  editorInsightVoice?.cancel();
  rejectConfirmation('播报已打断，请查看任务面板中的实际结果；不会自动重试操作。');
  if (preserveAudio && playbackState && audioCtx) {
    checkpoint.save({ ...playbackState, offset: playbackState.offset + Math.max(0, audioCtx.currentTime - playbackState.startedAt) });
  }
  if (turns.id) { recordBaselineTurn('turn_cancel', turns.id); send({ type: "barge_in", turn_id: turns.id }); }
  stopPlayback();
  if (cancelledId) dialogueReview?.endTurn(cancelledId, 'interrupted');
  turns.clear(); avatar.onBargeIn(); currentBubble = null; pendingText = "";
  // 去文字框化：打断时收回这一轮的口头微光，不留下半句闪着。
  if (VOICE_FIRST) { glimmer.dismiss(); renderGlimmer(); }
  editorDiscussionTurn = null;
  editorSubmittedText = null;
}
function registerBaselineTurn(id, input) {
  dialogueReview?.beginTurn({ id, input: input === 'resume' ? '续播被打断句 · 无剩余文字对齐' : input === 'proactive' ? '主动回应' : '' });
  const token = baseline.captureToken();
  baselineTurns.set(id, { token, input });
  if (baselineTurns.size > 128) baselineTurns.delete(baselineTurns.keys().next().value);
  baseline.record({ type: 'turn_begin', turn_id: id, meta: { input } }, { token });
}
function recordBaselineTurn(type, id = turns.id, meta = {}, at = performance.now()) {
  const observed = baselineTurns.get(id);
  baseline.record({ type, turn_id: id, at, meta: { input: observed?.input, ...meta } }, { token: observed?.token });
}
function recordBaselineItem(type, item, meta = {}) {
  baseline.record({ type, turn_id: item.turn_id || item._baselineTurn,
    meta: { chunkIndex: item._baselineChunk, resumed: !!item.resumed, source: 'playback', ...meta } },
  { token: item._baselineToken });
}
function observeBaselineEnergy(voiced, at, source, keepQuiet = false) {
  const token = baseline.captureToken();
  if (!token) { baselineEnergy = null; return; }
  if (baselineEnergy?.token !== token) baselineEnergy = null;
  if (voiced) {
    if (!baselineEnergy) baselineEnergy = { token, first: at, last: at, source, recordedTurn: null };
    baselineEnergy.last = at;
    if (recording) recordBaselineSpeech();
  } else if (!recording && !keepQuiet) baselineEnergy = null;
}
function recordBaselineSpeech() {
  const observed = baselineTurns.get(turns.id);
  if (!baselineEnergy || observed?.token !== baselineEnergy.token) return;
  if (baselineEnergy.recordedTurn !== turns.id) {
    recordBaselineTurn('speech_first_energy', turns.id, { source: baselineEnergy.source }, baselineEnergy.first);
    baselineEnergy.recordedTurn = turns.id;
  }
  recordBaselineTurn('speech_last_energy', turns.id, { source: baselineEnergy.source }, baselineEnergy.last);
}
function beginTurn(type, extra = {}, baselineInput = type === 'text' ? 'text' : 'confirmation') {
  let editorContext = null;
  if (type === 'text' || type === 'start') {
    try { editorContext = editorDiscussion?.requestContext(); }
    catch (error) { subtitle.textContent = error.message; return null; }
  }
  microFlow = false; narrationActive = false; proactiveActive = false; mumbleActive = false;
  syncModeStrip();
  const interrupted = !!turns.id || playback.busy;
  cancelTurn(true);
  if (interrupted) avatar.onBargeIn();
  const id = `client-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${++turnCounter}`}`;
  turns.begin(id); registerBaselineTurn(id, baselineInput);
  if (typeof extra.text === 'string') dialogueReview?.event({ type: 'input', turn_id: id, text: extra.text });
  editorDiscussionTurn = editorContext ? id : null;
  editorSubmittedText = editorContext && type === 'text' ? { id, text: extra.text } : null;
  send({ type, turn_id: id, ...extra, ...(editorContext ? { editor_insight: editorContext } : {}) });
  return id;
}
function rejectConfirmation(message) {
  if (!pendingConfirmation) return;
  const pending = pendingConfirmation; pendingConfirmation = null;
  clearTimeout(pending.timer); pending.reject(new Error(message));
}
function confirmAssistant(result, approve) {
  if (ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error('连接未就绪，请刷新后核对实际结果。'));
  // Unlock output during the button gesture, before waiting for the action result.
  ensureCtx(); cancelRecording(); resetDuplex(); vad.reset();
  const id = beginTurn('assistant_confirm', {
    run_id: result.run_id, confirmation_id: result.confirmation.id, approve,
  });
  submittedAt = performance.now(); firstAudioPending = true;
  return new Promise((resolve, reject) => {
    pendingConfirmation = { id, resolve, reject, timer: setTimeout(() => {
      rejectConfirmation('确认结果等待超时，请查看任务面板；不会自动重试操作。');
    }, 45000) };
  });
}

/* ---------------- 麦克风：静音监听不上传 ---------------- */
function recordingUi() {
  micBtn.classList.toggle("recording", recording);
  const label = recordingMode === "manual" ? "松开结束" : "按住说话";
  const micLabel = micBtn.querySelector(".mic-label");
  if (micLabel) micLabel.textContent = label;
  micBtn.title = recording ? label : "按住说话；键盘可按住空格";
  micBtn.setAttribute("aria-pressed", String(recording));
}
function startUtterance(mode) {
  if (ws.readyState !== WebSocket.OPEN) return false;
  if (!beginTurn("start", {}, mode === 'auto' ? 'voice_auto' : 'voice_manual')) {
    if (!alwaysListen) teardownMic();
    return false;
  }
  recording = true; recordingMode = mode; sentSamples = 0;
  recordBaselineSpeech();
  recordingUi(); idleStatus();
  if (mode === "manual") manualTimer = setTimeout(() => {
    if (recordingMode === "manual") {
      endRecording(); subtitle.textContent = "已达到单次录音上限，请松开后再说。";
    }
  }, 29500);
  return true;
}
function sendPcm(samples) {
  if (!recording || !samples.length || ws.readyState !== WebSocket.OPEN) return;
  if (ws.bufferedAmount > 1024 * 1024) {
    cancelRecording(); cancelTurn(); vad.reset({ cooldown: true });
    if (!alwaysListen) teardownMic();
    voiceError(new Error("网络发送拥堵，本次录音已取消")); return;
  }
  const remaining = 472000 - sentSamples; // 29.5 seconds including pre-roll.
  const part = samples.subarray(0, Math.max(0, remaining));
  if (part.length) { ws.send(pcm16(part)); sentSamples += part.length; }
}
function processFrame(frame, reference = null, frameAt = performance.now()) {
  if (recordingMode === "manual") {
    const rms = Math.sqrt(frame.reduce((sum, sample) => sum + sample * sample, 0) / frame.length);
    observeBaselineEnergy(rms >= Math.max(.012, vad.noise * 3), frameAt, 'manual');
    meterFill.style.width = `${Math.min(100, rms * 380)}%`;
    sendPcm(frame);
    if (sentSamples >= 472000) { endRecording(); subtitle.textContent = "已达到单次录音上限，请松开后再说。"; }
    return;
  }
  if (!alwaysListen || manualHeld) return;
  if (!recording && vad.cooldown) {
    vad.push(frame, false); resetDuplex(); return;
  }
  if (!recording) {
    const gateTurn = turns.id || playbackBaselineTurn;
    const decision = duplex.push(frame, { reference, playback: !!sourceNode, referenceAvailable: !!capture?.hasReference, mode: duplexMode });
    if (decision.metrics.protected) {
      observeBaselineEnergy(decision.metrics.voiced, frameAt, 'duplex', decision.metrics.candidateMs > 0);
      if (decision.metrics.candidateMs > 0 && !baselineGateOnset) {
        recordBaselineTurn('gate_onset', gateTurn, { source: 'duplex' }, frameAt - Math.max(0, decision.metrics.candidateMs - 20));
      }
      baselineGateOnset = decision.metrics.candidateMs > 0;
    }
    if (decision.reason === 'echo') metrics.count('echo');
    if (decision.candidate && candidateSince === null) { candidateSince = performance.now(); metrics.count('candidate'); recordBaselineTurn('gate_candidate', gateTurn, { source: 'duplex' }); }
    if (!decision.candidate && !decision.commit) candidateSince = null;
    setDucked(!!decision.duck); idleStatus();
    if (decision.commit) {
      recordBaselineTurn('gate_commit', gateTurn, { source: 'duplex', voicedMs: decision.metrics.voicedMs, candidateMs: decision.metrics.candidateMs });
      metrics.count('barge');
      if (candidateSince !== null) metrics.timing('candidateToInterrupt', performance.now() - candidateSince);
      if (!startUtterance('auto')) { resetDuplex(); return; }
      const frames = decision.frames || [frame];
      vad.adopt(frames, decision.metrics.voicedMs);
      for (const part of frames) sendPcm(part);
      candidateSince = null; setDucked(false); updateVoiceDiagnostics(); return;
    }
    if (decision.suppress || decision.metrics?.protected || decision.candidate || sourceNode) {
      vad.reset(); updateVoiceDiagnostics(); return;
    }
  }
  if (recordingMode === 'auto') {
    const tail = duplex.push(frame, { reference, playback: !!sourceNode, referenceAvailable: !!capture?.hasReference, mode: duplexMode });
    if (tail.suppress) { metrics.count('echo'); frame = new Float32Array(frame.length); }
  }
  const energy = Math.sqrt(frame.reduce((sum, sample) => sum + sample * sample, 0) / frame.length);
  observeBaselineEnergy(energy >= Math.max(.012, vad.noise * 3), frameAt, 'vad');
  const result = vad.push(frame, false);
  meterFill.style.width = `${Math.min(100, result.rms * 380)}%`;
  if (result.start && !startUtterance("auto")) { vad.reset(); return; }
  for (const samples of result.frames || []) sendPcm(samples);
  if (result.end && recordingMode === "auto") {
    if (result.valid) endRecording(false);
    else { cancelRecording(); cancelTurn(); idleStatus(); }
    resetDuplex();
    if (result.limit) subtitle.textContent = "已达到单次录音上限，安静片刻后可继续。";
  }
}
async function ensureMic() {
  if (capture) return true;
  if (capturePending) return capturePending;
  const generation = micGeneration;
  const request = (async () => {
    const ctx = ensureCtx();
    const stream = await microphone.open();
    if (!stream || generation !== micGeneration || disposed || (!manualHeld && !alwaysListen)) return false;
    if (ws.readyState !== WebSocket.OPEN) { teardownMic(); return false; }
    const micResampler = new StreamingResampler(ctx.sampleRate, TARGET_RATE);
    const refResampler = new StreamingResampler(ctx.sampleRate, TARGET_RATE);
    const micFrames = new SampleFramer(), refFrames = new SampleFramer();
    try {
      const handle = await createCapture({ context: ctx, stream, referenceNode: outputGain,
        onError: error => {
          if (generation !== micGeneration) return;
          manualHeld = false; alwaysListen = false; cancelRecording(); cancelTurn(); teardownMic(); updateContinuousVoice(); voiceError(error);
        },
        onChunk: chunk => {
          if (generation !== micGeneration) return;
          // Map the first sample's capture clock, including resampler/framer carry,
          // to performance time. This is a software clock proxy, not acoustic onset.
          const clockOffset = performance.now() - ctx.currentTime * 1000;
          const carryMs = micFrames.pending.length / TARGET_RATE * 1000 + micResampler.weight / ctx.sampleRate * 1000;
          const firstFrameAt = Number.isFinite(chunk.audioTime) ? clockOffset + chunk.audioTime * 1000 - carryMs : performance.now() - carryMs;
          const frames = micFrames.push(micResampler.push(chunk.microphone));
          const references = refFrames.push(refResampler.push(chunk.reference));
          for (let i = 0; i < frames.length; i++) {
            if (generation !== micGeneration) break;
            processFrame(frames[i], references[i], firstFrameAt + i * 20);
          }
        },
      });
      if (generation !== micGeneration || disposed || (!alwaysListen && !manualHeld)) { handle.stop(); return false; }
      capture = handle; resampler = micResampler; referenceResampler = refResampler;
      framer = micFrames; referenceFramer = refFrames;
      stream.getAudioTracks?.()[0]?.addEventListener?.('ended', () => {
        if (generation !== micGeneration) return;
        baseline.invalidate('capture_lost');
        manualHeld = false; alwaysListen = false; cancelRecording(); cancelTurn(); teardownMic(); updateContinuousVoice(); idleStatus();
      }, { once: true });
      updateVoiceDiagnostics(); idleStatus(); return true;
    } catch (error) { if (generation === micGeneration) teardownMic(); throw error; }
  })();
  capturePending = request;
  try { return await request; } finally { if (capturePending === request) capturePending = null; }
}
function endRecording(flush = true) {
  if (!recording) return;
  if (flush && resampler && framer) {
    for (const frame of framer.push(resampler.flush())) sendPcm(frame);
    if (recording && framer) sendPcm(framer.flush());
    if (referenceResampler && referenceFramer) { referenceResampler.flush(); referenceFramer.flush(); }
  }
  if (!recording) return;
  submittedAt = performance.now(); firstAudioPending = true;
  recordBaselineTurn('audio_submit', turns.id, { reason: recordingMode === 'manual' ? 'manual' : 'automatic' });
  send({ type: "audio_end", turn_id: turns.id });
  recording = false; recordingMode = null; clearTimeout(manualTimer); manualTimer = null;
  baselineEnergy = null;
  recordingUi(); if (!alwaysListen) teardownMic({ planned: true }); idleStatus();
}
function cancelRecording() {
  baselineEnergy = null;
  recording = false; recordingMode = null; clearTimeout(manualTimer); manualTimer = null;
  recordingUi();
}
function releaseManual() {
  manualHeld = false; manualRequest++;
  if (recordingMode === "manual") { endRecording(); vad.reset(); }
  if (!alwaysListen && !recording) teardownMic({ planned: true });
}
function teardownMic({ planned = false } = {}) {
  if (!planned && (capture || capturePending || microphone.stream)) baseline.invalidate('capture_lost');
  micGeneration++;
  capture?.stop(); capture = null; capturePending = null;
  resampler = framer = referenceResampler = referenceFramer = null;
  resetDuplex(); microphone.close(); meterFill.style.width = "0"; updateVoiceDiagnostics();
}
function cleanupVoice() {
  dialogueReview?.stop('disconnected');
  editorDiscussion?.dispose();
  editorInsightVoice?.cancel();
  baseline.invalidate('disconnected');
  disposed = true; manualHeld = false; alwaysListen = false;
  cancelRecording(); cancelTurn(); teardownMic(); updateContinuousVoice();
  try { playAnalyser?.disconnect(); } catch (_) {}
  checkpoint.clear(); updateResume();
  if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; playAnalyser = outputGain = null; }
}
window.addEventListener("pagehide", cleanupVoice);
window.addEventListener("beforeunload", cleanupVoice);
navigator.mediaDevices?.addEventListener?.('devicechange', () => baseline.invalidate('device_changed'));

/* ---------------- WebSocket ---------------- */
/* 主动搭话心跳（协议第六节）：空闲时周期性请求服务端做主动发言/自进化检查。
 * 本地只做最轻门控（授权 + 页面可见 + 本地空闲 + 输入框没有未发送草稿）；
 * practice/experience/忙碌守卫以服务端为准。
 * 没有这条线，proactive_check 永不被触发，主动开口与自进化都不可达。
 * 打磨轮：45s → 15s 一轮，配合 pacing.ambient.mumble_seconds=28，
 * 让"安静半分钟就被搭理"在真机上真实可感（每次 tick 都有服务端门控兜底）。 */
const PROACTIVE_HEARTBEAT_MS = 15000;
let proactiveHeartbeat = null;
function proactiveLocallyIdle() {
  const draft = el("textInput");
  return document.visibilityState === "visible" && !recording && !manualHeld
    && !turns.id && !playback.busy && !microphone.pending
    && !(draft && draft.value.trim());   // 心流打字静音：草稿还在框里就不打扰
}
function proactiveHeartbeatTick() {
  if (el("proactiveToggle")?.checked && proactiveLocallyIdle()) {
    send({ type: "proactive_check" });
  }
}
ws.onopen = () => {
  setStatus("在线", "on");
  ws.send(JSON.stringify({ type: "config", proactivity: el("proactiveToggle").checked, memory: el("memoryToggle").checked }));
  if (!proactiveHeartbeat) proactiveHeartbeat = setInterval(proactiveHeartbeatTick, PROACTIVE_HEARTBEAT_MS);
};

ws.onmessage = async (event) => {
  let msg;
  try {
    msg = JSON.parse(event.data);
  } catch (_) {
    return;                       // 后端发了非 JSON，不该让整个前端挂掉
  }
  if (!msg || typeof msg !== "object" || typeof msg.type !== "string") return;
  if (editorInsightVoice?.blocksServer(msg)) return;
  starmap?.onMessage?.(msg);
  /* 连接级提示先于 TurnGate 消费：speech_plan / self_evolution 不带 turn_id，
   * 只会用来点亮模式状态条（U-5），不改变任何轮次行为。 */
  if (msg.type === "speech_plan") { microFlow = true; syncModeStrip(); }
  else if (msg.type === "self_evolution") noteSelfEvolution();
  else if (msg.type === "micro_act" || msg.type === "mumble") handleAmbient(msg);
  if (!turns.accept(msg, !recording && !manualHeld && !microphone.pending && !playback.busy)) return;
  if (msg.type === 'turn_started' && !baselineTurns.has(msg.turn_id)) registerBaselineTurn(msg.turn_id, msg.source === 'narration' ? 'narration' : 'proactive');
  dialogueReview?.event({ ...msg, meta: msg.type === 'done' ? { textStatus: msg.delivery?.text_status, audioStatus: msg.delivery?.audio_status } : undefined });
  avatar.onMessage(msg);
  switch (msg.type) {
    case "editor_discussion":
      if (!editorDiscussion?.acceptInsight(msg.insight)) {
        editorDiscussion?.invalidate('讨论对应的观点已经变化，请回到稿子重新选择。');
        cancelTurn();
      }
      break;
    case 'config_ack':
      if (typeof msg.emotion === 'string' && (emotionOverride === null || msg.emotion_error)) {
        emotionOverride = msg.emotion;
        el('emotionSelect').value = msg.emotion;
      }
      if (msg.emotion_error) setStatus(msg.emotion_error, 'error', currentInteraction().id);
      const experienceAccepted = experienceUI.onAck(msg);
      const staleExperienceAck = Object.hasOwn(msg, 'experience_request_id') && !experienceAccepted;
      if (!staleExperienceAck && msg.practice_error) {
        el('practiceStatus').textContent = msg.practice_error;
      } else if (!staleExperienceAck && msg.practice) {
        practiceMode = msg.practice.mode;
        el('practiceReview').disabled = practiceMode === 'off';
      }
      // The panel validates request IDs. A late rejected snapshot must not
      // change the effective endpoint or invalidate an unrelated voice trial.
      experienceMode = experienceUI.activeMode();
      vad.pauseMs = experienceMode === 'off'
        ? (practiceMode === 'off' ? 900 : Number(el('practicePause').value))
        : (experienceMode === 'expression' ? Number(el('practicePause').value) : 1200);
      baseline.setContext({ activity: experienceMode !== 'off' ? experienceMode : practiceMode, endpointWaitMs: vad.pauseMs });
      syncModeStrip();
      break;
    case "turn_started":
      narrationActive = msg.source === "narration";
      proactiveActive = msg.source === "proactive" || msg.source === "self_topic";
      mumbleActive = msg.source === "mumble";
      // 有声微气泡：当前气泡正是这一轮轻声搭话的文本时留着它（气泡 + 真声），
      // 否则按原行为收回，避免两种气泡叠在一起。
      if (mumbleActive && glimmer.current?.text !== voicedMumbleText) { glimmer.dismiss(); renderGlimmer(); }
      // 去文字框化（协议 §23）：新轮次开始收回上一轮的口头微光，让这一轮的
      // 台词重新住进气泡；mumble 自己的气泡上面已经放过。
      else if (VOICE_FIRST && !mumbleActive) { glimmer.dismiss(); renderGlimmer(); }
      microFlow = false;
      syncModeStrip();
      idleStatus();
      break;
    case "ui_nav": {
      /* §二十四 界面自动领航：随路的 ui_nav 载荷（ui_nav.js 是纯逻辑，
       * 未登记取值全部安全降级，未知视图不影响其它领航字段）。
       *
       * 护栏：auto_open 由 normalizeUiNav 决定——带 turn_id 的用户轮次领航
       * 立即拉开抽屉；后台事件（缺 turn_id）一律降级为"只指不拉"，
       * close 类领航是降噪方向、缺 turn_id 也放行。
       * 心流防打扰语义照旧（executive-state.js 的 typing() 守卫不动）。 */
      const nav = normalizeUiNav(msg);
      if (nav.auto_open) {
        const view = drawerViewFor(nav);
        if (view) {
          /* 先开抽屉（若未开），再切到目标标签页；顺序不能反，
           * 否则首次领航只开不切，停在默认标签。 */
          if (!drawer?.opened) drawer?.open(false);
          drawer?.selectView(view);
        } else if (shouldOpenSidePanel(nav)) {
          /* 侧栏行动面板是主页面既有区域：收起抽屉让位，点亮最近一张共同事项卡。 */
          if (drawer?.opened) drawer.close(false);
          const side = el("sidePanel");
          if (side && !side.classList.contains("is-open")) el("sidebarToggle")?.click();
        } else if (shouldOpenStarmap(nav)) {
          /* 星图全屏视图由 starmap/index.js 的入口绑开关：收起抽屉后触发既有 toggle。
           * 触发走既有 toggle（含焦点管理、loadCurrent、aqi-fullview 广播），
           * 不直接改 #starmapMount 的显隐，避免绕过该模块的状态机。 */
          if (drawer?.opened) drawer.close(false);
          el("starmapToggle")?.click();
        }
        /* open 类领航（ops/threads/starmap）：广播 aqi-ui-nav，消费方据此
         * 定位卡片、展开子视图、点亮 .nav-spotlight 动效。 */
        window.dispatchEvent(new CustomEvent("aqi-ui-nav", { detail: nav }));
      }
      /* close 类领航允许后台放行（降噪方向）：auto_open 未覆盖的 close 在这里收手。 */
      if (shouldCloseDrawer(nav) && drawer?.opened) drawer.close(false);
      /* 视线微转：她亲手帮你拉开面板指给你看。gaze 是既有通道，无形象层新增触发器。 */
      avatar.lookAtGaze?.(gazeAfter(nav), { settleMs: 1600 });
      break;
    }
    case "ready":
      setStatus("在线", "on");
      subtitle.textContent = "按住左下角说话，或者打字。";
      break;
    case "recording":
      setStatus("录音中", "busy", "listening");
      break;
    case "asr":
      recordBaselineTurn('asr_received', msg.turn_id, { empty: !msg.text, source: 'ws' });
      if (msg.text) addBubble("user", msg.text);
      break;
    case "thinking":
      pendingText = "";
      // 去文字框化：小溪说话不再起 bot 文字气泡，头顶微光 + Holo-Deck 接手。
      if (!VOICE_FIRST && !mumbleActive) { currentBubble = addBubble("bot", "…"); currentBubble.classList.add("thinking"); }
      setStatus("在想…", "busy", "thinking");
      break;
    case "emotion":
      // 情绪先于正文到达，正好用来提前准备音色/立绘表情
      if (msg.emotion) avatar.emotion.set(msg.emotion);
      break;
    case "text_delta":
      // 流式增量：字幕跟着生成走，不用等整句
      if (!VOICE_FIRST && currentBubble && currentBubble.classList.contains("thinking")) {
        const t0 = currentBubble.querySelector(":scope > .time");
        currentBubble.textContent = "";
        if (t0) currentBubble.appendChild(t0);
        currentBubble.classList.remove("thinking");
      }
      pendingText = (pendingText || "") + (msg.delta || "");
      if (VOICE_FIRST) showSpokenLine(pendingText);   // 口头台词进头顶微光气泡
      else if (currentBubble) {
        // 流式覆写正文时保留 .time，时间戳属于有正文的气泡
        const timeEl = currentBubble.querySelector(":scope > .time");
        currentBubble.textContent = pendingText;
        if (timeEl) currentBubble.appendChild(timeEl);
        scheduleConstellation();
      }
      // Keep the subtitle on the audible sentence while later text streams ahead.
      if (!sourceNode) subtitle.textContent = pendingText;
      break;
    case "audio":
      if (typeof msg.data === "string" && msg.data.length <= 12 * 1024 * 1024) {
        recordBaselineTurn('audio_received', msg.turn_id, { source: 'ws' });
        const item = { ...msg, _baselineToken: baselineTurns.get(msg.turn_id)?.token, _baselineChunk: ++baselineChunk };
        if (item.chunk) { microFlow = true; syncModeStrip(); }
        if (!playback.enqueue(item)) recordBaselineTurn('queue_rejected', msg.turn_id);
        idleStatus();
      }
      else if (msg.data) voiceError(new Error("音频片段无效或过大，已跳过"));
      break;
    case "done":
      recordBaselineTurn('turn_done', msg.turn_id, { textStatus: msg.delivery?.text_status, audioStatus: msg.delivery?.audio_status });
      if (pendingConfirmation?.id === msg.turn_id) rejectConfirmation('未收到行动结果，请查看任务面板；不会自动重试操作。');
      if (VOICE_FIRST) showSpokenLine(msg.text || pendingText);   // 完整口头句入住微光，说完自然淡出
      if (!VOICE_FIRST && currentBubble && msg.text) {
        const timeDone = currentBubble.querySelector(":scope > .time");
        currentBubble.textContent = msg.text;
        if (timeDone) currentBubble.appendChild(timeDone);
        scheduleConstellation();
      }
      if (!VOICE_FIRST && msg.delivery && currentBubble) {
        const notes = [];
        if (msg.delivery.text_status === 'limit_reached') notes.push('本轮已达长度上限，可能尚未展开完整；可以让我继续。');
        if (msg.delivery.text_status === 'partial') notes.push('生成中断，这里只保留了部分回答。');
        if (['partial', 'failed'].includes(msg.delivery.audio_status)) notes.push('部分或全部语音合成失败，请阅读文字。');
        if (notes.length) {
          const note = document.createElement('small');
          note.className = 'delivery-note';
          note.setAttribute('role', 'status');
          note.textContent = notes.join(' ');
          currentBubble.append(document.createElement('br'), note);
        }
      }
      if (!VOICE_FIRST && currentBubble) appendContextUsage(currentBubble, msg.context_usage);
      contextBudget?.note(msg.context_usage);
      microFlow = false; narrationActive = false; proactiveActive = false; mumbleActive = false; syncModeStrip();
      turns.done = true;
      finishTurn();
      break;
    case "interrupted":
    case "playback_stopped":
      rejectConfirmation('播报已打断，请查看任务面板中的实际结果；不会自动重试操作。');
      cancelRecording(); stopPlayback(); dialogueReview?.endTurn(turns.id, 'interrupted'); turns.clear(); resetDuplex(); vad.reset();
      if (!alwaysListen) teardownMic();
      microFlow = false; narrationActive = false; proactiveActive = false; mumbleActive = false; syncModeStrip();
      idleStatus();
      break;
    case "assistant_result":
      if (pendingConfirmation?.id === msg.turn_id) {
        const pending = pendingConfirmation; pendingConfirmation = null;
        clearTimeout(pending.timer); pending.resolve(msg.result);
      } else showAssistantResult(msg.result, { confirm: confirmAssistant });
      contextBudget?.note(msg.result?.context_usage);
      break;
    case "aqi_blocks":
      // 协议 §23：动态积木随路下发——渲染到 Holo-Deck 或战况抽屉，
      // 并按 layout/nav/gaze 联动抽屉与视线。
      handleAqiBlocks(msg);
      break;
    case "memory_notice":
      if (msg.items) {
        showMemoryNotice(msg.items);
        refreshMemories();
      }
      break;
    case "error":
      recordBaselineTurn('turn_error', msg.turn_id, { source: 'ws' });
      if (msg.recoverable) {
        subtitle.textContent = msg.message || '部分交付失败，文字仍可阅读。';
        break;
      }
      rejectConfirmation(msg.message || '确认结果无法取得，请查看任务面板；不会自动重试操作。');
      if (msg.scope === 'editor_insight' && editorSubmittedText?.id === msg.turn_id && !el('textInput').value) {
        el('textInput').value = editorSubmittedText.text;
      }
      if (msg.scope === 'editor_insight' && msg.editor_context_valid === false) editorDiscussion?.invalidate(msg.message);
      cancelRecording(); stopPlayback(); dialogueReview?.endTurn(turns.id, 'error'); turns.clear(); resetDuplex(); vad.reset();
      if (!alwaysListen) teardownMic();
      setStatus("出错了", "");
      subtitle.textContent = msg.message || "出错了";
      break;
    default:
      // 新增的消息类型会落到这里。以后加类型时记得同步 avatar.onMessage，
      // 否则形象层会漏掉这一类事件——scripts/check_protocol.py 会查出来。
      break;
  }
};

ws.onclose = () => {
  experienceUI.onDisconnect();
  if (proactiveHeartbeat) { clearInterval(proactiveHeartbeat); proactiveHeartbeat = null; }
  cleanupVoice();
  setStatus("断开", "");
  subtitle.textContent = "连接已断开，麦克风已释放。请刷新后重试。";
};
ws.onerror = () => { subtitle.textContent = "连接出现错误，请检查网络。"; };
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    manualHeld = false; alwaysListen = false; cancelRecording(); cancelTurn(); teardownMic();
    vad.reset(); updateContinuousVoice(); setStatus("已暂停监听", "");
  }
});

/* ---------------- 记忆面板 ---------------- */
const KIND_LABEL = { fact: "事实", episode: "经历", todo: "待办", self_state: "自我" };
const SRC_LABEL = { stated: "你说过", inferred: "我推测", uncertain: "不确定" };
const STATUS_LABEL = { active: "当前", superseded: "历史", done: "已完成", conflict: "待核对，不自动使用" };
const PREFERENCE_LEVEL_LABEL = { like: '喜欢', favorite: '最喜欢', dislike: '不喜欢' };
const PREFERENCE_SCOPE_LABEL = { long_term: '长期自述', recent: '最近自述', current: '当时自述', unspecified: '时间范围未明确' };
function memorySourceLabel(memory) {
  if (memory.source !== "stated") return SRC_LABEL[memory.source] || "未知来源";
  return memory.evidence_state === "linked" ? "有原话证据"
    : memory.evidence_state === "manual" ? "手动保存" : "旧记忆，未关联原话";
}

async function mutateMemory(control, path, options, message) {
  control.disabled = true;
  try {
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(`操作未完成（${response.status}），请重试。`);
    el("memoryFeedback").textContent = message;
    try { await loadMemories(); }
    catch { el("memoryFeedback").textContent = `${message}列表刷新失败，请点击刷新。`; }
  } catch (error) { el("memoryFeedback").textContent = error.message; }
  finally { control.disabled = false; }
}
function refreshMemories() {
  return loadMemories().catch(error => { el("memoryFeedback").textContent = `记忆未刷新：${error.message}`; });
}
/* 星点点亮：只给本次新出现的记忆加 mem-new，让星点闪一下金光（纯表现层，不进状态） */
const seenMemoryIds = new Set();
async function loadMemories() {
  const res = await fetch(el("memHistory")?.checked ? "/api/memories?include_history=true" : "/api/memories");
  if (!res.ok) throw new Error(`无法加载记忆（${res.status}）`);
  const data = await res.json();
  if (!Array.isArray(data.items)) throw new Error("记忆列表格式异常");
  const list = el("memList");
  list.replaceChildren();
  el("memCount").textContent = `${data.count} 条当前记忆`;
  data.items.forEach((m) => {
    const div = document.createElement("div");
    div.className = seenMemoryIds.has(String(m.id)) ? "mem" : "mem mem-new";
    seenMemoryIds.add(String(m.id));
    div.dataset.memoryId = String(m.id);
    const val = document.createElement("button");
    val.type = "button";
    val.className = "val ghost";
    val.textContent = m.value;
    val.setAttribute("aria-label", `编辑记忆：${m.value}`);
    val.title = "点击可以改";
    val.onclick = async () => {
      const next = prompt("改成：", m.value);
      if (next && next !== m.value) {
        await mutateMemory(val, `/api/memories/${m.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: next }),
        }, "记忆已更新。");
      }
    };
    const meta = document.createElement("div");
    meta.className = "meta";
    // key 与 value 相同（手动记忆）时不再重复渲染同一句话
    const metaItems = [[KIND_LABEL[m.kind] || m.kind, "tag"], [memorySourceLabel(m), "tag"],
      [STATUS_LABEL[m.status] || "当前", "tag"]];
    if (m.key && m.key !== m.value) metaItems.push([m.key, ""]);
    for (const [text, className] of metaItems) {
      const label = document.createElement("span");
      label.className = className;
      label.textContent = text;
      meta.append(label);
    }
    if (m.source === 'stated' && m.evidence_state === 'linked' && typeof m.preference_target === 'string' && m.preference_target &&
        Object.hasOwn(PREFERENCE_LEVEL_LABEL, m.preference_level) && Object.hasOwn(PREFERENCE_SCOPE_LABEL, m.preference_scope)) {
      const preference = document.createElement('span'); preference.className = 'tag';
      preference.textContent = `${PREFERENCE_LEVEL_LABEL[m.preference_level]}：${m.preference_target}`;
      const scope = document.createElement('span'); scope.className = 'tag';
      scope.textContent = PREFERENCE_SCOPE_LABEL[m.preference_scope];
      meta.append(preference, scope);
      if (Number.isFinite(m.preference_as_of)) {
        const date = new Date(m.preference_as_of * 1000);
        if (Number.isFinite(date.getTime())) {
          const label = document.createElement('span');
          label.textContent = `原话日期 ${date.toISOString().slice(0, 10)}（UTC）`;
          meta.append(label);
        }
      }
      if (m.preference_scope === 'recent' || m.preference_scope === 'current') {
        const caution = document.createElement('span');
        caution.textContent = '仅原话当时自述，未确认延续至今；未设定过期日';
        meta.append(caution);
      }
    }
    const del = document.createElement("button");
    del.className = "ghost danger";
    del.textContent = "忘掉";
    del.onclick = async () => {
      await mutateMemory(del, `/api/memories/${m.id}`, { method: "DELETE" }, "已忘掉这条记忆。");
    };
    meta.appendChild(del);
    div.append(val, meta);
    if (m.evidence_state === "linked" && typeof m.evidence_quote === "string" && m.evidence_quote) {
      const evidence = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "查看原话";
      const quote = document.createElement("blockquote");
      quote.textContent = m.evidence_quote;
      evidence.append(summary, quote);
      div.append(evidence);
    }
    list.appendChild(div);
  });
}

window.addEventListener('context-reference-manage', event => {
  const {kind, id} = event.detail || {};
  if (!['memory','preference','task'].includes(kind) || typeof id !== 'string' || !id || id.length > 256) return;
  if (document.body.classList.contains('is-writing')) el('writingToggle')?.click();
  el('sidePanel')?.classList.add('is-open');
  el('sidebarToggle')?.setAttribute('aria-expanded', 'true');
  if (kind !== 'task' && !el('memoryToggle')?.checked) {
    el('memoryFeedback').textContent = '当前已关闭记忆。没有读取或刷新这条记录；可以在记忆与偏好中自行管理。';
    el('memoryFeedback').scrollIntoView({block:'nearest'}); return;
  }
  if (kind !== 'memory') el('work-tasks-tab')?.click();
  const panel = kind === 'preference' ? document.querySelector('[data-interaction-preferences]') : null;
  if (panel) panel.open = true;
  const selector = {memory:'[data-memory-id]',preference:'[data-claim-id]',task:'[data-task-id]'}[kind];
  const key = {memory:'memoryId',preference:'claimId',task:'taskId'}[kind];
  const match = [...document.querySelectorAll(selector)].find(element => element.dataset[key] === id);
  const fallback = kind === 'memory' ? el('memList') : panel || document.querySelector('#assistantMount');
  const target = match || fallback;
  if (target) {
    for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    }
    target.tabIndex = -1; target.scrollIntoView({block:'nearest'}); target.focus({preventScroll:true});
  }
  if (!match) {
    const feedback = kind === 'memory' ? el('memoryFeedback') : panel?.querySelector('[role="alert"]') || el('work-feedback');
    if (feedback) feedback.textContent = '这条记录不在当前管理列表中，可能已更改、删除或尚未刷新。请在这里核对；没有自动更换记录。';
  }
});

function showMemoryNotice(items) {
  if (!items || !items.length) return;
  const active = items.filter(item => item.status === "active");
  const conflicts = items.filter(item => item.status === "conflict");
  if (active.length) subtitle.textContent = `（记下了：${active.map(item => item.value).join("；")}）`;
  if (conflicts.length) el("memoryFeedback").textContent = "有新说法待核对，原记忆仍保留。勾选显示历史和待核对记录可查看并修改。";
}

/* ---------------- 绑定 ---------------- */
async function requestRecording() {
  if (manualHeld || disposed) return;
  if (ws.readyState !== WebSocket.OPEN) {
    subtitle.textContent = "连接还没准备好，请稍后再试。"; return;
  }
  editorInsightVoice?.cancel();
  manualHeld = true;
  const request = ++manualRequest;
  if (recordingMode === "auto") { cancelRecording(); cancelTurn(); }
  resetDuplex(); vad.reset();
  const generation = micGeneration;
  try {
    if (await ensureMic() && manualHeld && request === manualRequest && generation === micGeneration) startUtterance("manual");
  } catch (error) {
    if (generation !== micGeneration || request !== manualRequest) return;
    manualHeld = false; alwaysListen = false; teardownMic(); updateContinuousVoice();
    voiceError(new Error(`无法使用麦克风：${error.message}`));
  }
}
micBtn.addEventListener("pointerdown", async (e) => {
  e.preventDefault();
  await requestRecording();
});
micBtn.addEventListener("keydown", (event) => {
  if ((event.code === "Space" || event.code === "Enter") && !event.repeat) {
    event.preventDefault(); requestRecording();
  }
});
micBtn.addEventListener("keyup", (event) => {
  if (event.code === "Space" || event.code === "Enter") {
    event.preventDefault(); releaseManual();
  }
});
micBtn.addEventListener("blur", releaseManual);
document.addEventListener("pointerup", releaseManual);
document.addEventListener("pointercancel", releaseManual);

el('practiceApply').onclick = () => {
  if (ws.readyState !== WebSocket.OPEN) { subtitle.textContent = '连接未就绪，练习设置未应用。'; return; }
  if (!window.confirm('应用设置将结束当前回复；改变场景、难度或纠错方式会清空本次练习上下文。继续吗？')) return;
  baseline.invalidate('mode_changed');
  editorDiscussion?.end();
  manualHeld = false; manualRequest++; cancelRecording(); cancelTurn(); resetDuplex(); vad.reset();
  checkpoint.clear(); updateResume();
  if (!alwaysListen) teardownMic();
  practiceMode = el('practiceMode').value;
  vad.pauseMs = practiceMode === 'off' ? 900 : Number(el('practicePause').value);
  ws.send(JSON.stringify({type:'config',practice:{mode:practiceMode,level:el('practiceLevel').value,correction:el('practiceCorrection').value}}));
  el('practiceReview').disabled = practiceMode === 'off';
  el('practiceStatus').textContent = practiceMode === 'off' ? '已回到普通陪聊。' : '练习已开启：可以用英语打招呼，卡住时用中文求助。收句后回复；不执行任务，不写长期记忆。';
  syncModeStrip();
};
el('practicePause').onchange = () => {
  baseline.invalidate('mode_changed');
  if (practiceMode !== 'off' && !recording) vad.pauseMs = Number(el('practicePause').value);
};
el('practiceReview').onclick = () => {
  if (practiceMode === 'off' || ws.readyState !== WebSocket.OPEN) return;
  manualHeld = false; manualRequest++; cancelRecording(); resetDuplex(); vad.reset();
  if (!alwaysListen) teardownMic();
  beginTurn('practice_review');
};

el("sendBtn").onclick = async () => {
  const input = el("textInput");
  const text = input.value.trim();
  if (!text) return;
  if (ws.readyState !== WebSocket.OPEN) {
    subtitle.textContent = "连接暂不可用，消息已留在输入框里。";
    return;
  }
  manualHeld = false; cancelRecording(); vad.reset();
  if (!alwaysListen) teardownMic();
  resetDuplex(); if (!beginTurn("text", { text })) return;
  submittedAt = performance.now(); firstAudioPending = true; idleStatus();
  input.value = "";
  input.focus();
};
el("textInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) el("sendBtn").click();
});

async function saveMemoryRequest(path, body) {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`服务暂不可用（${response.status}），请重试。`);
}
async function persistMemoryToggle(enabled) {
  const control = el("memoryToggle");
  if (!control) return;
  control.disabled = true;
  try {
    await saveMemoryRequest("/api/memory/toggle", { enabled });
    el("memoryFeedback").textContent = enabled ? "已允许记录新的记忆。" : "已暂停记录，已有记忆不会删除。";
    if (!enabled) editorDiscussion?.invalidate('已暂停看稿，结束讨论后可以继续聊天。');
  } catch (error) {
    control.checked = !enabled;
    el("memoryFeedback").textContent = `设置未保存：${error.message}`;
    // 回滚布尔位后重新同步 WS 配置与胶囊视觉，避免服务端与界面各说各话。
    pushConfig();
    renderPresenceCapsule();
  } finally { control.disabled = false; }
}
el("memoryToggle").onchange = async (event) => {
  await persistMemoryToggle(event.target.checked);
  renderPresenceCapsule();   // 脚本/测试直接拨动隐藏布尔位时，胶囊视觉跟上
};

el("manualAdd").onclick = async () => {
  const input = el("manualInput");
  const value = input.value.trim();
  if (!value) { input.focus(); return; }
  const button = el("manualAdd");
  button.disabled = true;
  try {
    await saveMemoryRequest("/api/memory/manual", { kind: "fact", subject: "user", value, importance: 0.7 });
    if (input.value.trim() === value) input.value = "";
    el("memoryFeedback").textContent = "记下了。你随时可以修改或忘掉它。";
    try { await loadMemories(); }
    catch { el("memoryFeedback").textContent = "已保存，但列表刷新失败，请点击刷新。"; }
  } catch (error) {
    el("memoryFeedback").textContent = `没有记下，输入已保留：${error.message}`;
  } finally { button.disabled = false; }
};
el("manualInput").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229) el("manualAdd").click();
});
el("memRefresh").onclick = refreshMemories;
el("memHistory")?.addEventListener("change", refreshMemories);
el("memClear").onclick = async () => {
  if (!confirm("清空后无法恢复，确定删除全部记忆吗？")) return;
  await mutateMemory(el("memClear"), "/api/memory/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }, "全部记忆已清空。");
};

function updateContinuousVoice() {
  el("bargeToggle").textContent = `连续对话：${alwaysListen ? "开" : "关"}`;
  el("bargeToggle").setAttribute("aria-pressed", String(alwaysListen));
  syncModeStrip();
}
function disableContinuousVoice() {
  alwaysListen = false;
  if (recordingMode === "auto") { cancelRecording(); cancelTurn(); }
  if (!manualHeld && recordingMode !== "manual") teardownMic();
  vad.reset(); updateContinuousVoice(); idleStatus();
}
el("bargeToggle").onclick = async () => {
  if (alwaysListen) { disableContinuousVoice(); return; }
  if (disposed || ws.readyState !== WebSocket.OPEN) { voiceError(new Error("连接还没准备好")); return; }
  alwaysListen = true; vad.reset(); updateContinuousVoice();
  setStatus("正在请求麦克风…", "busy");
  const generation = micGeneration;
  try { await ensureMic(); }
  catch (error) {
    if (generation !== micGeneration) return;
    alwaysListen = false; manualHeld = false; teardownMic(); updateContinuousVoice();
    voiceError(new Error(`无法使用麦克风：${error.message}`));
  }
};
el("stopBtn").onclick = () => {
  manualHeld = false; cancelRecording(); cancelTurn(true); resetDuplex(); vad.reset({ cooldown: true });
  if (!alwaysListen) teardownMic();
  idleStatus();
  subtitle.textContent = alwaysListen
    ? "已停止回复，仍在本地监听；安静片刻后可继续说。已执行的操作不会撤销。"
    : "已停止录音与回复。已执行的操作不会撤销。";
};

el('duplexMode').onchange = event => {
  duplexMode = event.target.value;
  baseline.setContext({ mode: duplexMode });
  if (!recording) { resetDuplex(); vad.reset({ cooldown: vad.cooldown }); }
  updateVoiceDiagnostics();
};
el('resetVoiceMetrics').onclick = () => { metrics.reset(); updateVoiceDiagnostics(); };
el('resumeSpeechBtn').onclick = () => {
  if (recording || manualHeld || turns.id || playback.busy) return;
  const saved = checkpoint.take(); updateResume();
  if (!saved) return;
  const id = `baseline-resume-${++turnCounter}`;
  registerBaselineTurn(id, 'resume');
  resumeReviewId = id;
  resetDuplex();
  if (!playback.enqueue({ ...saved, resumed: true, _baselineTurn: id, _baselineToken: baselineTurns.get(id)?.token, _baselineChunk: ++baselineChunk })) {
    dialogueReview?.endTurn(id, 'error'); resumeReviewId = null;
  }
  subtitle.textContent = '只续播被打断的当前句剩余音频，不重做任务。'; idleStatus();
};

/* 面部反应：角色镜像用户的表情/视线/头部动作。
   默认关闭（隐私），需要用户显式授权摄像头。 */
let faceActive = false;
let faceVideo = null;
el("faceToggle").onclick = async () => {
  if (faceActive) {
    avatar.face.stop();
    faceActive = false;
    if (faceVideo) { faceVideo.remove(); faceVideo = null; }
    el("faceToggle").textContent = "面部反应：关";
    return;
  }
  faceVideo = document.createElement("video");
  faceVideo.style.display = "none";
  document.body.appendChild(faceVideo);
  try {
    await avatar.face.start(faceVideo);
    faceActive = true;
    el("faceToggle").textContent = "面部反应：开";
  } catch (err) {
    console.warn("面部反应启动失败：", err);
    if (faceVideo) { faceVideo.remove(); faceVideo = null; }
    el("faceToggle").textContent = "面部反应：关（摄像头不可用）";
  }
};

let followActive = true;   // 默认开启视线跟随（打磨计划修正 #2）：眼神跟着光标，身体不抽搐
el("followToggle").onclick = async () => {
  if (followActive) {
    avatar.setMouseFollow(false);
    followActive = false;
    el("followToggle").textContent = "视线跟随：关";
    return;
  }
  avatar.setMouseFollow(true);
  followActive = true;
  el("followToggle").textContent = "视线跟随：开";
};

el("followToggle").textContent = "视线跟随：开";

/* 用户互动反馈：角色对用户的操作做出反应。
 * 鼠标移动时角色看向鼠标，点击时角色有反应。 */
document.addEventListener("mousemove", (e) => {
  const stage = document.querySelector(".avatar-stage");
  if (!stage) return;
  const rect = stage.getBoundingClientRect();
  const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  const y = ((e.clientY - rect.top) / rect.height) * 2 - 1;
  avatar.onUserInteraction("mousemove", x, y);
});
document.addEventListener("click", (e) => {
  const stage = document.querySelector(".avatar-stage");
  if (!stage) return;
  const rect = stage.getBoundingClientRect();
  const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  const y = ((e.clientY - rect.top) / rect.height) * 2 - 1;
  avatar.onUserInteraction("click", x, y);
});

function pushConfig() {
  ws.send(
    JSON.stringify({
      type: "config",
      proactivity: el("proactiveToggle").checked,
      memory: el("memoryToggle").checked,
      ...(emotionOverride !== null ? { emotion: emotionOverride } : {}),
    })
  );
}
el("proactiveToggle").onchange = pushConfig;
el("emotionSelect").onchange = () => {
  emotionOverride = el("emotionSelect").value;
  pushConfig();
};

fetch("/api/persona")
  .then((r) => r.json())
  .then((p) => {
    el("personaName").textContent = p.name;
    el("personaTag").textContent = p.tagline;
    document.querySelector(".avatar").textContent = p.name[0];
    const sel = el("emotionSelect");
    sel.innerHTML = '<option value="">自动（随内容变化）</option>' +
      p.voices.map((v) => `<option value="${v}">${v === 'base' ? '自然' : v}</option>`).join("");
    if (emotionOverride === null) {
      emotionOverride = p.voices.includes(p.default_voice) ? p.default_voice : (p.voices.includes('base') ? 'base' : '');
    }
    sel.value = emotionOverride;
    sel.onchange = () => {
      emotionOverride = sel.value;
      pushConfig();
    };
  });
refreshMemories();

/* 形象启动失败不应该拖垮聊天，所以单独兜住 */
boot().then(() => {
  const select = el("avatarRenderer");
  const status = el("avatarRendererStatus");
  if (!select.querySelector('option[value="seed"]')) select.add(new Option("半身陪聊 · Seed-san", "seed"));
  if (!select.querySelector('option[value="seed-warm"]')) select.add(new Option("半身陪聊 · 暖杏奶油", "seed-warm"));
  if (!select.querySelector('option[value="seed-daily"]')) select.add(new Option("及肩短发 · 日常外套", "seed-daily"));
  if (!select.querySelector('option[value="seed-daily-eyes"]')) select.add(new Option("日常造型 · 眉眼升级", "seed-daily-eyes"));
  if (!select.querySelector('option[value="seed-daily-lids"]')) select.add(new Option("日常造型 · 细腻笑眼", "seed-daily-lids"));
  if (!select.querySelector('option[value="seed-daily-hair"]')) select.add(new Option("日常造型 · 发丝物理", "seed-daily-hair"));
  if (!select.querySelector('option[value="external"]')) select.add(new Option("外部 .vrm（自定义）", "external"));
  let selectedModel = "svg";
  select.onchange = async () => {
    if (select.value === "external") {
      // 外部模型由设置面板的加载控件负责；这里只做一次兜底重载。
      if (!selectedExternalModel) {
        select.value = selectedModel;
        status.textContent = "还没有加载过外部 .vrm；请先在上方填写地址并点击加载。";
        return;
      }
      select.disabled = true;
      status.textContent = "正在加载外部形象，聊天和语音不受影响…";
      try {
        await avatar.setRenderer("vrm", selectedExternalModel);
        selectedModel = "external";
        status.textContent = "外部 .vrm 已启用；失败时会自动回到上一个形象。";
      } catch (error) {
        select.value = selectedModel;
        status.textContent = `加载失败，保留当前形象：${error.message}`;
      } finally { select.disabled = false; }
      return;
    }
    select.disabled = true;
    status.textContent = "正在加载形象，聊天和语音不受影响…";
    try {
      const choice = select.value;
      const isDaily = choice === "seed-daily" || choice === "seed-daily-eyes" || choice === "seed-daily-lids" || choice === "seed-daily-hair";
      const isSeed = choice === "seed" || choice === "seed-warm" || isDaily;
      await avatar.setRenderer(isSeed ? "vrm" : choice,
        choice === "seed-daily-lids" ? "/static/models/seed-daily-lids.vrm" : choice === "seed-daily-eyes" ? "/static/models/seed-daily-eyes.vrm" : choice === "seed-daily-hair" ? "/static/models/seed-daily-hair.vrm" : isDaily ? "/static/models/seed-daily.vrm" : isSeed ? "/static/models/seed-san.vrm" : "/static/models/sample.vrm");
      avatar.svg.setPresentation?.(isSeed);
      avatar.svg.setPalette?.(choice === "seed-warm" ? "warm" : "original");
      selectedModel = choice;
      status.textContent = isDaily
        ? "及肩短发与日常外套已启用 · Seed-san 衍生造型 · 原模型 © VirtualCast, Inc. · VRM Public License 1.0"
        : isSeed
        ? `半身表演已启用${choice === "seed-warm" ? " · 暖杏奶油配色" : ""} · Seed-san © VirtualCast, Inc. · VRM Public License 1.0`
        : avatar.rendererKind === "vrm" ? "3 渲 2 已启用 · 官方测试模型，非最终角色。" : "已切回原版 SVG。";
    } catch (error) {
      select.value = selectedModel;
      status.textContent = `加载失败，保留当前形象：${error.message}`;
    } finally { select.disabled = false; }
  };
  const requestedAvatar = new URLSearchParams(location.search).get("avatar");
  if (["vrm", "seed", "seed-warm", "seed-daily", "seed-daily-eyes", "seed-daily-lids"].includes(requestedAvatar)) {
    select.value = requestedAvatar;
    select.onchange();
    return;
  }
  // 默认推荐造型：发丝物理 + 眼睑校准的日常造型（打磨计划修正 #3）；加载失败自动回退 SVG。
  select.value = "seed-daily-hair";
  select.onchange();
}).catch((err) => {
  console.warn("形象模块启动失败：", err);
  el("avatarRenderer").disabled = true;
  el("avatarRendererStatus").textContent = `形象启动失败：${err.message}`;
  document.getElementById("avatarStage")?.remove();
});
// 微光气泡自然消散：只在可见时轮询，避免常驻开销。
setInterval(() => { if (glimmer.status !== "hidden") { glimmer.poll(Date.now()); renderGlimmer(); } }, 1000);

// 触摸 head/face：把真实触摸事件交给服务端选台词样本并走语音链路（遵循授权/静音/去重/打断）。
window.addEventListener("aqi-touch", (event) => {
  const region = event.detail?.region;
  if (region !== "head" && region !== "face") return;
  if (!el("proactiveToggle")?.checked) return;   // 未授权/静音时不主动出声
  if (!proactiveLocallyIdle()) return;
  send({ type: "touch", region });
});
