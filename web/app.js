/* ---------------------------------------------------------------
   前端：录音(PCM16@16k) -> WebSocket -> 流式字幕 + 分句音频播放
   播放期间可被打断（barge-in），这是语音角色的基本礼貌。
   形象（web/avatar）由 Avatar 驱动，本文件只喂事件和音频。
   --------------------------------------------------------------- */

import { avatar, boot } from "./avatar/index.js";

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
let playQueue = [];
let playing = false;
let stream = null;
let sourceNode = null;
let procNode = null;
let analyser = null;
let playAnalyser = null;
let recorderNode = null;
let recording = false;
let alwaysListen = false;
let speakingHeard = false;
let emotionOverride = "";
let pendingText = "";
let currentBubble = null;

/* ---------------- 文本气泡 ---------------- */
function addBubble(role, text) {
  const node = document.createElement("div");
  node.className = `msg ${role === "user" ? "user" : "bot"}`;
  node.textContent = text;
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = new Date().toLocaleTimeString();
  node.appendChild(time);
  chat.appendChild(node);
  chat.scrollTop = chat.scrollHeight;
  return node;
}

function setStatus(text, state) {
  statusText.textContent = text;
  statusDot.className = `dot ${state || ""}`;
}

/* ---------------- 音频 ---------------- */
function ensureCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    // 播放路上的分析器：给口型做实时包络，不只是"能不能出声"
    playAnalyser = audioCtx.createAnalyser();
    playAnalyser.fftSize = 512;
    playAnalyser.smoothingTimeConstant = 0.3;
    playAnalyser.connect(audioCtx.destination);
    avatar.attachOutputAnalyser(playAnalyser);
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

async function enqueueAudio(b64, text, emotion) {
  const ctx = ensureCtx();
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const buffer = await ctx.decodeAudioData(bytes.buffer);
  playQueue.push({ buffer, text: text || "", emotion: emotion || "" });
  if (!playing) drainQueue();
}

function drainQueue() {
  if (!playQueue.length) {
    playing = false;
    avatar.stopSpeaking();
    return;
  }
  playing = true;
  const ctx = ensureCtx();
  const { buffer, text, emotion } = playQueue.shift();
  sourceNode = ctx.createBufferSource();
  sourceNode.buffer = buffer;
  sourceNode.connect(playAnalyser);
  sourceNode.onended = () => {
    sourceNode = null;
    drainQueue();
  };
  // 记下真实的播放起点，口型时间轴才对得齐（不能用 setTimeout 的时刻）
  const startedAt = ctx.currentTime;
  sourceNode.start(startedAt);
  avatar.setAudioContext(ctx);
  avatar.speak(text, ctx, startedAt, buffer.duration);
  // 情绪跟着声音走而不是跟着文本走：目前后端整轮只给一个情绪，
  // 这里等于无操作；一旦它逐句给不同情绪，表情会自己跟着切换。
  avatar.setEmotion(emotion);
}

function stopPlayback() {
  playQueue = [];
  if (sourceNode) {
    try { sourceNode.stop(); } catch (_) {}
    sourceNode = null;
  }
  playing = false;
  avatar.stopSpeaking();
}

/* ---------------- 录音 ---------------- */
async function startRecording() {
  if (recording) return;
  ensureCtx();
  stopPlayback();
  stream = stream || (await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  }));
  const ctx = audioCtx;
  sourceNode = ctx.createMediaStreamSource(stream);
  analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  procNode = ctx.createScriptProcessor(4096, 1, 1);
  recorderNode = ctx.createBuffer(1, 2048, ctx.sampleRate);
  let filled = 0;

  procNode.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    if (analyser) {
      const buf = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      meterFill.style.width = `${Math.min(100, rms * 380)}%`;
      if (rms > 0.02) speakingHeard = true;
    }
    if (!recording) return;
    recorderNode.getChannelData(filled).set(input.subarray(0, recorderNode.length - filled));
    filled += input.length;
    if (filled >= recorderNode.length) {
      const chunk = downsample(recorderNode.getChannelData(0).slice(), ctx.sampleRate, TARGET_RATE);
      ws.send(int16ToArrayBuffer(chunk));
      filled = 0;
    }
  };

  sourceNode.connect(analyser);
  sourceNode.connect(procNode);
  procNode.connect(ctx.destination);
  recording = true;
  speakingHeard = false;
  micBtn.classList.add("recording");
  micBtn.textContent = "松开/点击结束";
  ws.send(JSON.stringify({ type: "start" }));
}

function endRecording() {
  if (!recording) return;
  recording = false;
  meterFill.style.width = "0";
  micBtn.classList.remove("recording");
  micBtn.textContent = "按住说话";
  ws.send(JSON.stringify({ type: "audio_end" }));
  if (!alwaysListen) teardownMic();
}

function teardownMic() {
  if (procNode) { procNode.onaudioprocess = null; procNode.disconnect(); procNode = null; }
  if (sourceNode) { try { sourceNode.disconnect(); } catch (_) {} sourceNode = null; }
  recorderNode = null;
}

function downsample(buffer, inRate, outRate) {
  if (inRate === outRate) return buffer;
  const ratio = inRate / outRate;
  const length = Math.round(buffer.length / ratio);
  const output = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), buffer.length);
    let sum = 0;
    for (let j = start; j < end; j++) sum += buffer[j];
    output[i] = sum / Math.max(1, end - start);
  }
  return output;
}

function int16ToArrayBuffer(float32) {
  const buf = new ArrayBuffer(float32.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

/* ---------------- 打断 ---------------- */
let lastRms = 0, bargeStreak = 0;
function watchForBargeIn() {
  if (!analyser || !playing) { bargeStreak = 0; return; }
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  const rms = Math.sqrt(sum / buf.length);
  if (rms > 0.035 && rms > lastRms) {
    bargeStreak++;
    if (bargeStreak > 6) {
      stopPlayback();
      avatar.onBargeIn();
      ws.send(JSON.stringify({ type: "barge_in" }));
      bargeStreak = 0;
    }
  } else {
    bargeStreak = Math.max(0, bargeStreak - 1);
  }
  lastRms = rms;
}
setInterval(watchForBargeIn, 60);

/* ---------------- WebSocket ---------------- */
ws.onopen = () => {
  setStatus("在线", "on");
  ws.send(JSON.stringify({ type: "config", proactivity: el("proactiveToggle").checked, memory: el("memoryToggle").checked }));
};

ws.onmessage = async (event) => {
  let msg;
  try {
    msg = JSON.parse(event.data);
  } catch (_) {
    return;                       // 后端发了非 JSON，不该让整个前端挂掉
  }
  if (!msg || typeof msg !== "object" || typeof msg.type !== "string") return;
  avatar.onMessage(msg);
  switch (msg.type) {
    case "ready":
      setStatus("在线", "on");
      subtitle.textContent = "按住左下角说话，或者打字。";
      break;
    case "recording":
      setStatus("录音中", "busy");
      break;
    case "asr":
      if (msg.text) addBubble("user", msg.text);
      break;
    case "thinking":
      pendingText = "";
      currentBubble = addBubble("bot", "…");
      currentBubble.classList.add("thinking");
      setStatus("在想…", "busy");
      break;
    case "emotion":
      // 情绪先于正文到达，正好用来提前准备音色/立绘表情
      if (msg.emotion) avatar.emotion.set(msg.emotion);
      break;
    case "text_delta":
      // 流式增量：字幕跟着生成走，不用等整句
      if (currentBubble && currentBubble.classList.contains("thinking")) {
        currentBubble.textContent = "";
        currentBubble.classList.remove("thinking");
      }
      pendingText = (pendingText || "") + (msg.delta || "");
      if (currentBubble) {
        currentBubble.textContent = pendingText;
        chat.scrollTop = chat.scrollHeight;
      }
      subtitle.textContent = pendingText;
      break;
    case "audio":
      if (msg.data) await enqueueAudio(msg.data, msg.text, msg.emotion);
      break;
    case "done":
      setStatus("在线", "on");
      subtitle.textContent = "";
      if (currentBubble && msg.text) currentBubble.textContent = msg.text;
      setTimeout(() => { if (!playing && !recording && alwaysListen) startRecording(); }, 400);
      break;
    case "interrupted":
    case "playback_stopped":
      setStatus("在线", "on");
      break;
    case "memory_notice":
      if (msg.items) {
        showMemoryNotice(msg.items);
        loadMemories();
      }
      break;
    case "error":
      setStatus("出错了", "");
      subtitle.textContent = msg.message || "出错了";
      break;
    default:
      // 新增的消息类型会落到这里。以后加类型时记得同步 avatar.onMessage，
      // 否则形象层会漏掉这一类事件——scripts/check_protocol.py 会查出来。
      break;
  }
};

ws.onclose = () => setStatus("断开", "");

/* ---------------- 记忆面板 ---------------- */
const KIND_LABEL = { fact: "事实", episode: "经历", todo: "待办", self_state: "自我" };
const SRC_LABEL = { stated: "你说过", inferred: "我推测", uncertain: "不确定" };

async function loadMemories() {
  const res = await fetch("/api/memories");
  const data = await res.json();
  const list = el("memList");
  list.innerHTML = "";
  el("memCount").textContent = `${data.count} 条`;
  data.items.forEach((m) => {
    const div = document.createElement("div");
    div.className = "mem";
    const val = document.createElement("div");
    val.className = "val";
    val.textContent = m.value;
    val.title = "点击可以改";
    val.onclick = async () => {
      const next = prompt("改成：", m.value);
      if (next && next !== m.value) {
        await fetch(`/api/memories/${m.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: next }),
        });
        loadMemories();
      }
    };
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `<span class="tag">${KIND_LABEL[m.kind] || m.kind}</span>` +
      `<span class="tag ${m.source}">${SRC_LABEL[m.source] || m.source}</span>` +
      `<span>${m.key}</span>`;
    const del = document.createElement("button");
    del.className = "ghost danger";
    del.textContent = "忘掉";
    del.onclick = async () => {
      await fetch(`/api/memories/${m.id}`, { method: "DELETE" });
      loadMemories();
    };
    meta.appendChild(del);
    div.append(val, meta);
    list.appendChild(div);
  });
}

function showMemoryNotice(items) {
  if (!items || !items.length) return;
  const names = items.map((i) => SRC_LABEL[i.source]).join("、");
  subtitle.textContent = `（它${names}：${items.map((i) => i.value).join("；")}）`;
}

/* ---------------- 绑定 ---------------- */
micBtn.addEventListener("pointerdown", async (e) => {
  e.preventDefault();
  await startRecording();
});
document.addEventListener("pointerup", () => {
  if (recording) endRecording();
});

el("sendBtn").onclick = async () => {
  const input = el("textInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  ws.send(JSON.stringify({ type: "text", text }));
};
el("textInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") el("sendBtn").click();
});

el("memoryToggle").onchange = (e) =>
  fetch("/api/memory/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: e.target.checked }),
  });

el("manualAdd").onclick = async () => {
  const value = el("manualInput").value.trim();
  if (!value) return;
  await fetch("/api/memory/manual", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "fact", subject: "user", value, importance: 0.7 }),
  });
  el("manualInput").value = "";
  loadMemories();
};
el("memRefresh").onclick = loadMemories;
el("memClear").onclick = async () => {
  if (!confirm("会硬删除全部记忆（行、索引、派生统计一起清），确定？")) return;
  await fetch("/api/memory/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  loadMemories();
};

el("bargeToggle").onclick = () => {
  alwaysListen = !alwaysListen;
  el("bargeToggle").textContent = `始终监听：${alwaysListen ? "开" : "关"}`;
  if (alwaysListen && !recording) startRecording();
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

function pushConfig() {
  ws.send(
    JSON.stringify({
      type: "config",
      proactivity: el("proactiveToggle").checked,
      memory: el("memoryToggle").checked,
      emotion: emotionOverride,
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
    sel.innerHTML = '<option value="">自动</option>' +
      p.voices.map((v) => `<option value="${v}">${v}</option>`).join("");
    sel.onchange = () => {
      emotionOverride = sel.value;
      pushConfig();
    };
  });
loadMemories();

/* 形象启动失败不应该拖垮聊天，所以单独兜住 */
boot().catch((err) => {
  console.warn("形象模块启动失败：", err);
  document.getElementById("avatarStage")?.remove();
});