/* 形象的运行时：驱动装配、每帧仲裁、以及对外的最小 API。
 *
 * 分层（优先级低 -> 高）：
 *   idle     10  待机微动，永远在最底下打底
 *   emotion  20  情绪表情，叠加在待机之上
 *   gesture  30  手势轨道，可打断低优先级
 *   lip      40  口型，说话时独占 ParamMouthOpenY
 *
 * 对外只暴露几个事件式方法，app.js 那边不需要知道内部有四个驱动。
 */

import { ParamStore, Rigger, clamp, clamp01, lerp, softLimitStack } from "./params.js";
import { buildTimeline } from "./timeline.js";
import { IdleDriver } from "./drivers/idle.js";
import { LipDriver } from "./drivers/lip.js";
import { EmotionDriver } from "./drivers/emotion.js";
import { GestureDriver } from "./drivers/gesture.js";
import { FaceDriver } from "./drivers/face.js";
import { BeatDriver } from "./drivers/beat.js";
import { MotionMagic, getMotionProfile } from "./drivers/motion.js";
import { DiscourseGestureGenerator } from "./drivers/discourseGesture.js";
import { SvgRig } from "./render/svg.js";
import { PerformanceDirector } from "./drivers/performance.js";
import { validatePerformancePlan } from "./drivers/speech-plan.js";
import { composeGesture } from "./drivers/primitives.js";
import { EyeExpressionDriver, combineEyeOpen } from "./drivers/eye-expression.js";
import { DialogueGazeDirector } from "./drivers/dialogue-gaze.js";
import { DialogueExpressionDriver } from "./drivers/dialogue-expression.js";
import { ThinkingPreactDriver } from "./drivers/thinking-pre-act.js";
import { TouchZones, svgRegionHit } from "./drivers/touch-zones.js";
import { DIALOGUE_INTENT_PROFILES, resolveDialogueIntent } from "./drivers/dialogue-intent.js";

const LAYERS = [
  ["face", 5],
  ["idle", 10],
  ["emotion", 20],
  ["gesture", 30],
  ["lip", 40],
];

/* 外部 .vrm 一键加载契约（A-5，交给 Role 3 的设置面板）：
 * 只接受 http / https / blob 与站内相对路径。跨域地址需要目标服务器开
 * CORS，否则 GLTFLoader 的抓取必然失败——那会走加载失败路径，当前形象
 * 保持不动，聊天和语音不受影响。 */
const VRM_URL_SCHEMES = new Set(["http:", "https:", "blob:"]);

export function validateVrmUrl(url) {
  if (typeof url !== "string" || !url.trim()) {
    throw new Error("avatar_renderer_url_invalid: 模型地址不能为空");
  }
  const value = url.trim();
  let parsed = null;
  try { parsed = new URL(value, globalThis.location?.href); } catch (_) { parsed = null; }
  if (parsed && !VRM_URL_SCHEMES.has(parsed.protocol)) {
    throw new Error(`avatar_renderer_url_invalid: 不支持的地址协议 ${parsed.protocol}`);
  }
  return value;
}

/* 失败路径统一成带错误码的消息；设置面板展示 message 即可。 */
export function normalizeRendererError(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (message.startsWith("avatar_renderer_")) {
    return error instanceof Error ? error : new Error(message);
  }
  if (/humanoid/i.test(message)) return new Error(`avatar_renderer_not_humanoid: ${message}`);
  return new Error(`avatar_renderer_load_failed: ${message}`);
}

export class Avatar {
  constructor() {
    this.cfg = null;
    this.store = null;
    this.rig = null;
    this.svg = new SvgRig();
    this.performance = new PerformanceDirector();
    this.dialogueGaze = new DialogueGazeDirector();
    this.dialogueExpression = new DialogueExpressionDriver();
    this.thinkingPreact = new ThinkingPreactDriver();
    this.touchZones = null;
    this.modelInfo = null;
    this._breathBlinkAt = null;
    this._touchPreviewUntil = null;
    this._touchPreviewName = null;
    this._facePokes = 0;
    // idle 层叠加软限幅（A-3）：膝值以下恒等，正常演出逐字节不变。
    this.stackLimit = { enabled: true, knee: 0.45, ceiling: 0.75 };
    this.dialogueIntentMode = 'auto';
    this.dialogueReview = null;
    this.rendererKind = "svg";
    this._rendererRequest = 0;
    this.clock = 0;
    this.audioCtx = null;
    this._raf = 0;
    this._last = 0;
    this._pending = [];
    this.speechPlan = null;
    this.outAnalyser = null;
    this._outBuf = null;
    this.reducedMotion = false;
    this.gazeMode = "natural";
    this.eyeHighlight = 1;
    this._eyeBlinkTime = null;
    this._lookAroundTime = 0;
    this._lookAroundIndex = 0;
this.lastUserText = "";  // 记录最后一次用户文本，用于记忆检索
  }

  configure(cfg) {
    this.cfg = cfg || {};
    this.triggers = this.cfg.triggers || {};
    this.store = new ParamStore(this.cfg.neutral || {}, this.cfg.response || {});
    this.rig = new Rigger(this.cfg.neutral || {});
    for (const [name, priority] of LAYERS) this.rig.register(name, priority);

    // gaze 是顶层契约（"在看哪"不是"待机噪声"），要显式并进去
this.idle = new IdleDriver({ ...this.cfg.idle, gaze: this.cfg.gaze });
    this.lip = new LipDriver();
    this.emotion = new EmotionDriver(this.cfg.emotions || {}, this.cfg.neutral || {});
    this.eyeExpression = new EyeExpressionDriver({ neutral: this.cfg.neutral || {} });
    this.dialogueGaze.configure(this.cfg.gaze?.dialogue || {});
    this.setDialogueIntent(this.cfg.gaze?.dialogue?.intent || 'auto');
    this.gesture = new GestureDriver(this.cfg.triggers || {});
    this.gesture.setTracks(this.cfg.gestures || {});
    this.gesture.setTriggers(this.cfg.triggers || {});
this.face = new FaceDriver();
    this.beat = new BeatDriver();
    // A-3 叠加软限幅参数来自 config/avatar.yaml 的 idle.stack_limit；
    // 缺省即代码里的软膝（0.45 / 0.75），与打磨前的观感一致。
    const limit = this.cfg.idle?.stack_limit;
    if (limit && typeof limit === "object") {
      this.stackLimit = {
        enabled: limit.enabled !== false,
        knee: Number.isFinite(limit.knee) ? limit.knee : 0.45,
        ceiling: Number.isFinite(limit.ceiling) ? limit.ceiling : 0.75,
      };
    }
    this.thinkingPreact.configure(this.cfg.idle?.thinking_preact || {});
    this.motion = new MotionMagic();
    this.motion.setComponents(getMotionProfile("base"));
    this.discourseGesture = new DiscourseGestureGenerator();
    this.emotion.set("base");
    
    // 记忆驱动表情系统
    this.memoryTimer = 0;
    this.memoryInterval = 8000; // 每 8 秒拉取一次相关记忆
    this.lastMemoryContext = "";
    this.activeMemories = [];
    
    /* 配置晚于第一条音频帧到达时，这里补播 */
    for (const item of this._pending.splice(0)) {
      this.speak(item.text, this.audioCtx, item.startedAt, item.duration, item.options);
    }
    return this;
  }

  /* 共发音参数来自 config/avatar.yaml。
   * buildTimeline 每次调用都读一次配置（说话可能横跨一次配置热更），
   * 代价只是几个字段拷贝，不值得为此缓存一份再处理失效。 */
  coarticulationOpts() {
    return this.cfg.coarticulation ? { coarticulation: this.cfg.coarticulation } : undefined;
  }

  /* buildTimeline 的选项：共发音参数来自 config；微句流的 chunk 与长播报
   * 预告（speech_plan）由说话层经 options / onMessage 透传。
   * 两者都有存在性守卫：字段缺失时与逐句播放的现状完全一致。 */
  timelineOpts(options = {}) {
    const opts = this.coarticulationOpts() || {};
    if (options && options.chunk) opts.chunk = options.chunk;
    if (this.speechPlan) opts.speechPlan = this.speechPlan;
    return opts;
  }

  /* 尊重系统的"减少动态效果"。策略是压低幅度和过冲，不是把角色冻住——
   * 眨眼完全保留：去掉眨眼看起来像坏了，而不是安静。
   * 比例见 config/avatar.yaml 的 reduced_motion。 */
  setReducedMotion(enabled) {
    this.reducedMotion = !!enabled;
    const cfg = this.cfg.reduced_motion || {};
    const pick = (key) => (this.reducedMotion ? Number(cfg[key] ?? 0) : 1);
    this.idle.setMotion({
      sway: pick("sway"),
      bob: pick("bob"),
      gazeDrift: pick("gaze_drift"),
    });
    this.svg.setSecondaryMotion(pick("secondary"));
    this.gestureScale = pick("gesture");
  }

  mount(container) {
    this.container = container;
    this.svg.mount(container, this.cfg?.look || {});
    this._mountTouchZones(container);
    if (!this._raf) this._start();
    return this;
  }

  /* A-4 触控热区：自包含挂在容器上。enabled:false 时完全退出，
   * 不留任何监听（设置面板可关）。 */
  _mountTouchZones(container) {
    this._disposeTouchZones();
    const touch = this.cfg?.touch;
    if (!touch || touch.enabled === false) return;
    this.touchZones = new TouchZones(container, {
      slop: touch.tap_slop, tapMs: touch.tap_ms,
      region: (clientX, clientY, x, y) => this._touchRegion(clientX, clientY, x, y),
      onRegion: (region, point) => this.onTouchRegion(region, point),
      onDrag: (x, y) => this.onUserInteraction("mousemove", x, y),
    });
  }

  _disposeTouchZones() {
    this.touchZones?.dispose();
    this.touchZones = null;
  }

  /* 命中判定按渲染器分流：VRM 投影真实骨骼，SVG 用归一化矩形热区。 */
  _touchRegion(clientX, clientY, x, y) {
    if (this.rendererKind === "vrm") return this.svg.hitRegion?.(clientX, clientY) || null;
    return svgRegionHit(this.cfg?.touch?.svg, x, y);
  }

  /* Prepare asynchronously offscreen: loading or failure never stops speech/SVG. */
  async setRenderer(kind, url = "/static/models/sample.vrm") {
    if (!this.container) throw new Error("Avatar must be mounted first");
    if (!["svg", "vrm", "original"].includes(kind)) throw new Error("Unknown renderer");
    if (kind === "vrm") url = validateVrmUrl(url);
    this.dialogueReview?.stop('model-switch');
    const request = ++this._rendererRequest;
    const host = document.createElement("div");
    // 宿主层对指针透明：触控热区的目标是容器本身（见 drivers/touch-zones.js）。
    host.style.cssText = "position:absolute;inset:0;width:100%;height:100%;visibility:hidden;pointer-events:none";
    this.container.appendChild(host);
    let next;
    try {
      if (kind === "original") {
        const { OriginalRig } = await import("./render/original.js");
        next = new OriginalRig();
        await next.mount(host);
      } else if (kind === "vrm") {
        const { VrmRig } = await import("./render/vrm.js");
        next = new VrmRig();
        await next.mount(host, this.cfg?.look || {},
          { url, stage: this.cfg?.stage, touch: this.cfg?.touch });
      } else {
        next = new SvgRig();
        next.mount(host, this.cfg?.look || {});
      }
      if (request !== this._rendererRequest) {
        next.dispose?.(); host.remove(); return false;
      }
      next.setSecondaryMotion(this.reducedMotion ? Number(this.cfg.reduced_motion?.secondary ?? 0.15) : 1);
      this.svg.dispose?.();
      for (const child of [...this.container.children]) if (child !== host) child.remove();
      host.style.visibility = "visible";
      this.svg = next;
      this.rendererKind = kind;
      this.modelInfo = next.getModelInfo?.() || null;
      this.eyeExpression.setProfile(next.getEyeCapabilities?.().profile);
      return true;
    } catch (error) {
      next?.dispose?.();
      host.remove();
      if (request !== this._rendererRequest) return false;
      throw normalizeRendererError(error);
    }
  }

  /* 设置面板（Role 3）读这个展示当前模型与许可提示位。 */
  getRendererInfo() {
    return { kind: this.rendererKind, model: this.modelInfo };
  }

  getModelInfo() {
    return this.modelInfo;
  }

  dispose() {
    ++this._rendererRequest;
    cancelAnimationFrame(this._raf);
    this._raf = 0;
    this.thinkingPreact.stop({ immediate: true });
    this._disposeTouchZones();
    this.face?.stop();
    this.dialogueGaze.stop('dispose');
    this.dialogueExpression.stop({ immediate: true });
    this.dialogueReview?.stop('disposed');
    this.svg.dispose?.();
    this.container?.replaceChildren();
  }

  _start() {
    this._last = performance.now();
    const loop = (now) => {
      const elapsed = Number.isFinite(now - this._last) ? Math.max(0, (now - this._last) / 1000) : 0;
      // Body filters keep a small step after suspension; the attention schedule
      // uses elapsed time so an old glance is not replayed when the tab returns.
      const dt = clamp(elapsed, 0, 0.05);
      this._last = now;
      this.clock += dt;
      this._frame(dt, elapsed);
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  setAudioContext(ctx) {
    this.audioCtx = ctx;
  }

  /* 播放链路上的 analyser：口型的实时包络和说话时的头动都取自这里。
   * 让 Avatar 自己拉，省掉一条独立的 rAF。 */
  attachOutputAnalyser(node) {
    this.outAnalyser = node || null;
    this._outBuf = node ? new Float32Array(node.fftSize) : null;
  }

  /* 用户互动反馈：角色对用户的操作做出反应。
   * type: "mousemove" | "click" | "scroll"
   * x, y: 鼠标位置（归一化到 -1..1） */
  onUserInteraction(type, x, y) {
    // The page is interactive before asynchronous avatar boot finishes.
    if (!this.idle || !this.gesture) return;
    /* 任何互动都把精神状态拉回来。
     * 只按类型给不同的回神量：动鼠标是最轻的信号，点击才是真的在叫她。
     * 不做这一步的话，她会在你一直用鼠标点点点的时候慢慢睡过去。 */
    if (type === "click") this.idle.wake(0.9);
    else this.idle.wake(0.35);

    if (type === "mousemove") {
      if (this.gazeMode !== "follow") return;
      // 角色看向鼠标
      this.idle.setGazeTarget(x, y);
      this.idle.setMousePos(x, y);
    } else if (type === "click") {
      // 点击时角色有反应：快速转头看一眼，然后回来
      this.gesture.play("look_at_user", this.clock);
    }
  }

  /* 开启/关闭鼠标跟随模式。 */
  setMouseFollow(enabled) {
    this.setGazeMode(enabled ? "follow" : "natural");
  }

  /* A-4 区域互动反馈：摸头/戳脸/轻触三种差异化反应。
   * 表情走既有眉眼预览通道，挥手臂走 PerformanceDirector 的预览动作
   * （VRM 的手臂姿势只由它驱动），不新造协议外的动作。被连戳第三次会从
   * "惊讶"升级成"微嗔"——戳多了会不好意思。 */
  onTouchRegion(region, point) {
    if (!region) return;
    this.idle?.wake(1);
    const now = this.clock;
    if (region === "head") {
      this._playTouchPreview("happy", now + 2.2);
      this.gesture.play("nod", now);
    } else if (region === "face") {
      this._facePokes += 1;
      const pout = this._facePokes % 3 === 0;
      this._playTouchPreview(pout ? "tsundere" : "surprised", now + (pout ? 2.6 : 1.6));
      this.gesture.play(pout ? "shake" : "surprise", now);
    } else if (region === "hand") {
      this._playTouchPreview("happy", now + 1.8);
      this.performance.preview("wave", this._previewClock());
    }
    // 通知宿主层发生了一次真实触摸（head/face/hand），供台词样本走语音链路；附加、不改既有反馈。
    try { window.dispatchEvent(new CustomEvent("aqi-touch", { detail: { region } })); } catch (_) {}
  }

  _playTouchPreview(name, until) {
    this.setEyeExpression(name);
    this._touchPreviewName = name;
    this._touchPreviewUntil = until;
  }

  /* 预览动作要时钟：播放中用真实音频时钟（和语音手势同一时间域），
   * 静默时退回帧时钟，否则挥手会永远停在半空。 */
  _previewClock() {
    const ctx = this.audioCtx;
    if (ctx && ctx.state === "running" && Number.isFinite(ctx.currentTime)) return ctx;
    return { currentTime: this.clock };
  }

  setGazeMode(mode) {
    if (!["natural", "follow", "auto", "forward"].includes(mode)) throw new RangeError("Unknown gaze mode");
    this.gazeMode = mode;
    this.idle.setMouseFollow(mode === "follow");
    this._lookAroundTime = 0;
    this.idle.setGaze("viewer", mode === "forward");
  }

  resetGaze() {
    if (!this.idle) return;
    // 指针离开后取消持续跟随的旧坐标，避免下一帧把 viewer 改回 custom。
    this.idle.mousePos = null;
    this.idle.setGaze("viewer");
  }

  /**
   * 协议§二十四 ui_nav：领航时的视线微转。前端在拉开面板的同时调一次，
   * 让形象"侧目看向抽屉那边"——她亲手帮你指给你看的最后一笔。
   *
   * 走既有 idle.setGaze(name) 通道（与 gaze.on_message 规则同一套语义），
   * 未知注视点名由 idle 内部回落 viewer（配置校验由 check_protocol 静态把关）；
   * 模型/config 尚未就绪时安全降级为 no-op，不阻塞界面领航本身。
   */
  lookAtGaze(name, { settleMs = 1600 } = {}) {
    if (!this.idle || !name) return;
    this.idle.setGaze(name, false);
    this.idle.wake(0.6);
    if (this._navGazeReturn) clearTimeout(this._navGazeReturn);
    this._navGazeReturn = setTimeout(() => {
      this._navGazeReturn = null;
      if (this.idle && this.gazeMode === "natural") this.idle.setGaze("viewer");
    }, settleMs);
  }
  setEyeExpression(name) {
    this.idle?.wake(0.9);
    return this.eyeExpression.set(name);
  }
  setEyeExpressionIntensity(value) { this.eyeExpression.setIntensity(value); }
  setEyeHighlight(value) { this.eyeHighlight = clamp01(value); }
  blinkEyes() { this._eyeBlinkTime = 0; }
  setDialogueState(state) {
    // Status failures/offline are runtime facts, not a character emotion.
    this.dialogueGaze.setState(state === 'unavailable' ? 'idle' : state);
  }
  setDialogueGazeEnabled(enabled) {
    this.dialogueGaze.setEnabled(enabled);
    if (!enabled) this.dialogueExpression.stop({ immediate: true });
    this.idle?.setGazeContext();
    if (!enabled && this.gazeMode === 'natural') this.idle?.setGaze('viewer');
  }
  setDialogueStyle(id) { return this.dialogueGaze.setStyle(id); }
  setDialogueIntent(mode) {
    if (typeof mode !== 'string' || (mode !== 'auto' && !Object.hasOwn(DIALOGUE_INTENT_PROFILES, mode))) return false;
    this.dialogueIntentMode = mode;
    const emotion = this.emotion?.matchingMode ? this.emotion.matchingTarget : this.emotion?.current;
    this.dialogueGaze.setIntent(resolveDialogueIntent({ intent: mode, emotion: emotion || 'base' }));
    return true;
  }

  _manualEyeBlink(dt) {
    if (this._eyeBlinkTime === null) return 0;
    this._eyeBlinkTime += dt;
    const t = this._eyeBlinkTime;
    const curve = x => { const v = clamp01(x); return v * v * (3 - 2 * v); };
    if (t < 0.07) return curve(t / 0.07);
    if (t < 0.13) return 1;
    if (t < 0.28) return 1 - curve((t - 0.13) / 0.15);
    this._eyeBlinkTime = null;
    return 0;
  }

  /* 呼吸点眨眼（协议第十一节）：音频时钟走到段末停顿的那一刻补一次轻眨眼。
   * 只在没有眨眼正在进行时发起，否则会把一次眨眼切成两次。 */
  _tickBreathBlink() {
    if (this._breathBlinkAt === null) return;
    const now = this.audioCtx?.currentTime;
    if (!Number.isFinite(now) || now < this._breathBlinkAt) return;
    this._breathBlinkAt = null;
    if (this._eyeBlinkTime === null) this._eyeBlinkTime = 0;
  }

  /* 触控反馈的眉眼预览到点收回。只收回自己设的那一个，
   * 不抢设置面板/实验室可能挂着的预览。 */
  _releaseTouchPreview({ force = false } = {}) {
    if (this._touchPreviewUntil === null) return;
    if (!force && this.clock < this._touchPreviewUntil) return;
    this._touchPreviewUntil = null;
    const name = this._touchPreviewName;
    this._touchPreviewName = null;
    if (name && this.eyeExpression?.name === name) this.setEyeExpression(null);
  }

  /* 没有 analyser（静音 TTS、测试驱动）时返回 null——"没有包络数据"
   和"包络为零"是两回事，不能当成同一件事。 */
  _readRms() {
    if (!this.outAnalyser) return null;
    const buf = this._outBuf;
    this.outAnalyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    // TTS 输出峰值大概在 0.2 上下，×5 之后大约落在 0..1
    return clamp01(Math.sqrt(sum / buf.length) * 5);
  }

  /* 分句开始播放：把文本摊到这段音频的真实时长上 */
  speak(text, ctx, startedAt, durationSec, options = {}) {
    this.audioCtx = ctx || this.audioCtx;
    /* 开讲即回神。角色开口说话却还顶着一副睡眼惺忪的样子，
     * 读起来不是"困"，是"卡了"。 */
    this.idle?.wake(1);
    if (!this.store) {
      this._pending.push({ text, startedAt, duration: durationSec, options });
      return;
    }
    const timeline = buildTimeline(text, durationSec, this.timelineOpts(options));
    this.lip.speak(timeline, startedAt);
    /* F2 呼吸点（协议第十一节）：chunk.breath 为真时，在这段音频结束、
     * 段间停顿开始的那一刻补一次轻眨眼。走既有手动眨眼通道，与 idle 自发
     * 眨眼取 max 合成，不会双重闭合。 */
    this._breathBlinkAt = timeline.breath
      ? startedAt + (Number.isFinite(timeline.breathAt) ? timeline.breathAt : durationSec)
      : null;
    // 首包音频到达：思考期预演收尾（协议：首个 audio 结束等待演出）。
    this.thinkingPreact.stop();
    this.dialogueGaze.startSpeech(this.audioCtx, startedAt, durationSec);
    this.performance.speak(text, this.audioCtx, startedAt, durationSec, options);
    // Legacy head/body text tracks follow playback too. An explicit semantic
    // plan, including a deliberately quiet sentence, owns speech gestures.
    if (!validatePerformancePlan(text, options.performance)) {
      for (const name of this.gesture.matchText(text)) this.gesture.play(name, this.clock);
    }
  }

  stopSpeaking({ turnPending = false, preserveDialogue = false } = {}) {
    // A stop before initialization must also invalidate deferred mouth animation.
    this._pending.length = 0;
    this._breathBlinkAt = null;
    this.performance?.stop();
    this.lip?.stop();
    if (!preserveDialogue) this.dialogueGaze.endSpeech({ turnPending });
  }

  /* 在音频真正开始播放时切表情，而不是等文本到达。
   * 目前后端整轮只给一个情绪，所以这等价于空操作；
   * 一旦它逐句给不同情绪，表情会跟着声音自动切换，不需要改前端。 */
  setEmotion(name) {
    this.performance.setEmotion(name);
    if (name && this.emotion.set(name)) {
      for (const gesture of this.gesture.matchEmotion(name)) {
        this.gesture.play(gesture, this.clock);
      }
    }
  }

  /* ---- WebSocket 消息入口 ---- */

  /* 事件顺序：asr -> thinking -> emotion -> text_delta* -> audio* -> done
   * 情绪标签先于正文到达，所以表情可以比声音早起；hold 轨道必须在
   * emotion 上就松手，否则 thinking 的姿势会一直挂到下一轮。 */
  onMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    /* F2 长播报预告（协议第十一节）：在首个 audio 之前到达。
     * 提前于其它子系统消费，未知事件不会向 performance / dialogueGaze 传播；
     * 缺 mode 字段时保持现状，不进入长播报模式。 */
    if (msg.type === "speech_plan") {
      if (typeof msg.mode === "string") {
        this.speechPlan = { mode: msg.mode, expected_chunks: msg.expected_chunks,
          first_chunk_chars: msg.first_chunk_chars, turn_id: msg.turn_id };
      }
      return;
    }
    // Cancellation can arrive while the model/config is still loading. Clear
    // deferred speech before the configuration guard so it cannot revive later.
    if (msg.type === "thinking" || (msg.type === "recording" && msg.state) ||
        msg.type === "interrupted" || msg.type === "playback_stopped") {
      this.speechPlan = null; // 新轮次开始或被打断：长播报预告随之过期
      this.stopSpeaking({ preserveDialogue: true });
      // A-2 思考期预演：thinking 起、新轮次/打断收。
      if (msg.type === "thinking") this.thinkingPreact.start();
      else this.thinkingPreact.stop();
    }
    this.performance.onEvent(msg);
    this.dialogueGaze.onEvent(msg);
    if (!this.cfg) return;
    const t = this.triggers;
    switch (msg.type) {
      case "recording":
        // 你在说话：起倾听姿势；松手了收掉
        if (msg.state) {
          this.gesture.play(t.on_user_speech, this.clock);
          this.idle.wake(1);   // 有人在跟她说话，她不可能在打瞌睡
          this.face.setMimicryMode(true);   // 镜像用户面部表情（上半脸）
        } else {
          this.gesture.stop(t.on_user_speech);
          this.face.setMimicryMode(false);
        }
        break;
      case "asr":
        this.gesture.stop(t.on_user_speech);
        break;
      case "thinking":
        this.gesture.stop(t.on_user_speech);
        this.gesture.play(t.on_thinking, this.clock);
        break;
case "emotion":
        this.setEmotion(msg.emotion || "base");
        this.emotion.setMatchingTarget(msg.emotion || "base");
        this.motion.setComponents(getMotionProfile(msg.emotion || "base"));
        this.gesture.stop(t.on_thinking);
        this.gesture.stop(t.on_user_speech);
        break;
      case "done":
        // 没有音频的收尾（静默 TTS / 纯文字轮）也要收起思考预演。
        this.thinkingPreact.stop();
        break;
      case "text_delta":
        // Generated text may lead audible speech by several sentences. Only
        // speak(), at the actual playback boundary, schedules speech gestures.
        break;
      case "playback_stopped":
      case "interrupted":
        this.onBargeIn();
        break;
      default:
        // 以后新增的消息类型走这里，忽略即可，不该让形象层崩掉
        break;
    }

    /* 注视点跟着消息走：思考时飘开，开口说话时看回来。 */
    const gazeRules = (this.cfg.gaze && this.cfg.gaze.on_message) || {};
    if (!this.dialogueGaze.enabled && msg.type === "recording") {
      this.idle.setGaze(msg.state ? gazeRules.recording : gazeRules.emotion || "viewer");
    } else if (!this.dialogueGaze.enabled && gazeRules[msg.type]) {
      this.idle.setGaze(gazeRules[msg.type]);
    }
  }

  /* 被打断：嘴立刻还回去，思考/倾听姿势松掉，表情回中位 */
  onBargeIn() {
    this.stopSpeaking();
    this.gesture?.stopAll();
    this.emotion?.set("base");
    this.face?.setMimicryMode(false);
    this.dialogueGaze.stop('interrupted');
    this.dialogueExpression.stop({ immediate: true });
    this.thinkingPreact.stop({ immediate: true });
    this._releaseTouchPreview({ force: true });
  }

  /* ---- 每帧 ---- */

  _frame(dt, dialogueElapsed = dt) {
    const { store, rig } = this;
    rig.begin();

const rms = this._readRms();
    const speaking = this.lip.speaking;
    // The stage close-up follows the lip clock: the same fact that moves the
    // mouth, not a separate UI timer that could drift out of sync.
    this.svg.setSpeaking?.(speaking);
    const usesCalibratedLids = this.svg.eyeLidMode !== 'native' && this.svg.getEyeCapabilities?.().profile === 'seed-lids';
    this.eyeExpression.setProfile(usesCalibratedLids ? 'seed-lids' : 'native');
    this.emotion.setEyeLidProfile(usesCalibratedLids ? 'seed-lids' : 'native');
    const eyePreview = this.eyeExpression.update(dt);
    this.emotion.update(dt);
    const gazeShift = this.emotion.shift();
    const pose = this.gesture.update(dt, store, this.clock);
    const controlledGaze = this.gazeMode !== 'natural' || eyePreview.active;
    // The default thinking track keeps its head/brow poses; its legacy eye
    // coordinates are supplied by the temporary dialogue plan instead.
    const semanticThinking = this.dialogueGaze.enabled && this.gesture.active?.name === this.triggers.on_thinking;
    const gestureGaze = !semanticThinking && pose && ['ParamEyeBallX', 'ParamEyeBallY'].some(key => Object.hasOwn(pose, key));
    const emotionName = this.emotion.matchingMode ? this.emotion.matchingTarget : this.emotion.current;
    this.dialogueGaze.setIntent(resolveDialogueIntent({ intent: this.dialogueIntentMode, emotion: emotionName || 'base' }));
    const emotionConfig = this.emotion.table[emotionName];
    const emotionGaze = this.emotion.ownsGaze || (emotionConfig?.owns_gaze !== false &&
      ['ParamEyeBallX', 'ParamEyeBallY'].some(key => Number.isFinite(emotionConfig?.shift?.[key])));
    const gazeLocked = !controlledGaze && (emotionGaze || gestureGaze);
    this.idle.claimGaze(gazeLocked);
    const dialogue = this.dialogueGaze.update(dialogueElapsed, { suspended: controlledGaze || gazeLocked, reducedMotion: this.reducedMotion });
    if (dialogue.command) {
      const { x, y, drift, intervalScale, headFollowScale, headDelayScale, headRecruitScale } = dialogue.command;
      if (!dialogue.active) {
        this.idle.setGazeContext();
        this.idle.setGaze('viewer');
      } else {
        this.idle.setGazeContext({ drift, intervalScale, headFollowScale, headDelayScale, headRecruitScale });
        this.idle.setGazeTarget(x, y);
      }
    } else if (controlledGaze || gazeLocked || !dialogue.active) this.idle.setGazeContext();
    this.dialogueFrame = { ...dialogue, suspended: controlledGaze || gazeLocked, owner: controlledGaze ? 'manual'
      : gestureGaze ? 'gesture' : emotionGaze ? 'emotion' : dialogue.active ? 'dialogue' : 'idle' };
    const dialogueExpression = this.dialogueExpression.update(dt, { dialogue: this.dialogueFrame,
      enabled: this.dialogueGaze.enabled, suspended: controlledGaze || gazeLocked, reducedMotion: this.reducedMotion,
      profile: usesCalibratedLids ? 'seed-lids' : 'native', emotion: emotionName || 'base' });
    if (this.gazeMode === "auto") {
      this._lookAroundTime -= dt;
      if (this._lookAroundTime <= 0) {
        const points = [[-0.55, -0.12], [0.48, 0.08], [0.1, -0.3], [0, 0]];
        const [x, y] = points[this._lookAroundIndex++ % points.length];
        const scale = this.reducedMotion ? 0.35 : 1;
        this.idle.setGazeTarget(x * scale, y * scale);
        this._lookAroundTime = 2.4;
      }
    }
    const idleOut = this.idle.update(dt, { speaking, rms: rms || 0 });
    this._tickBreathBlink();
    const preact = this.thinkingPreact.update(dt, { reducedMotion: this.reducedMotion });
    // 眨眼三源取 max 合成：idle 自发、手动（blinkEyes / 呼吸点）、思考预演。
    // 相加会双重闭合（眼睑被拉过 0 再钳回来），取 max 保证任何时刻只有一条
    // 在真正闭眼。
    const blink = Math.max(idleOut.blink, this._manualEyeBlink(dt), preact?.blink || 0);
    this._releaseTouchPreview();

    // 语篇感知手势生成器更新（含 backchannel）
    const discourseOut = this.discourseGesture.update(rms || 0, dt, {
      userSpeaking: !speaking && this.face.mimicryMode, // 用户说话时 face.mimicryMode 为 true
      avatarSpeaking: speaking,
    });
    
    // Backchannel 手势（用户说话时自动点头等）
    const bcGesture = this.discourseGesture.getBackchannelGesture(
      !speaking && this.face.mimicryMode, // 用户说话时
      rms || 0,
      dt
    );
    if (bcGesture) {
      // 将 backchannel 原语转换为手势并合并到 discourseOut
      const bcTrack = composeGesture([{
        part: bcGesture.part,
        name: bcGesture.name,
        duration: bcGesture.duration,
        weight: bcGesture.weight,
        startTime: 0,
      }], bcGesture.duration);
      if (discourseOut && bcTrack) {
        // 合并到 discourseOut
        Object.assign(discourseOut, bcTrack);
      } else if (bcTrack) {
        // 如果没有 discourseOut，直接用 bcTrack
        Object.assign(discourseOut || {}, bcTrack);
      }
    }

    // Compose automatic blink after expression openness, rather than adding
    // two full-strength closing shapes to the same eyelid.
    rig.set("idle", "ParamBreath", idleOut.breath * idleOut.breathDepth);
    /* ---- idle 层微动累加 + 软限幅（A-3）----
     * sway / 说话律动 / Motion Magic / beat / 思考预演都是相加进同一个
     * ParamAngle* 的小偏移。相位对齐时总和会冲到单源幅度的两三倍，读起来
     * 就是"头突然抖一下"。先把这一帧的微动源求和，再过软膝压缩：膝值以下
     * 恒等（正常演出逐字节不变），膝值以上平滑压向天花板（C1 连续，不会
     * 在膝盖处引入新的速度折角）。注视带出的转头、犯困垂头、呼吸不参与
     * 限幅——它们是意图明确的姿态，不是噪声源。 */
    const micro = {};
    const microAdd = (key, value) => {
      const n = Number(value);
      if (Number.isFinite(n)) micro[key] = (micro[key] || 0) + n;
    };
    microAdd("ParamAngleX", idleOut.swayX);
    microAdd("ParamAngleY", idleOut.swayY);
    microAdd("ParamAngleZ", idleOut.bob);
    /* 眼-头协调：注视点带出来的转头。
     * 与上面的 sway 是两回事——sway 是与注视无关的底层噪声，
     * 这里是把"她在看哪"真的反映到头的朝向上。
     * 眼-头的分工和时序差都在 IdleDriver 里算好了，
     * 这一层只负责把结果并进仲裁（idle 优先级最低，不压过情绪和手势）。 */
    rig.add("idle", "ParamAngleX", idleOut.headX);
    rig.add("idle", "ParamAngleY", idleOut.headY);
    /* 慢层：没人理她久了，头会一点一点垂下去。
     * 写成"抬一点"而不是"直接设值"，是为了和精神回来的速度解耦：
     * 犯困是慢慢发生的，回神是瞬间的，两者不该是同一条曲线。 */
    if (idleOut.droop > 0) {
      rig.add("idle", "ParamAngleY", idleOut.droop);
    }

    // Motion Magic：程序化动作生成，无限不循环
    const motionOut = this.motion.update(dt);
    if (motionOut) {
      for (const [key, value] of Object.entries(motionOut)) {
        microAdd(key, value);
      }
    }

    // 节拍同步：音频节拍驱动弹跳
    const beatOut = this.beat.update(this.outAnalyser, dt);
    if (beatOut.pulse > 0.01) {
      microAdd("ParamAngleY", beatOut.pulse * 0.12);
      microAdd("ParamBodyAngleX", beatOut.pulse * 0.08);
    }
    if (preact) {
      for (const [key, value] of Object.entries(preact.values)) microAdd(key, value);
    }
    for (const [key, value] of Object.entries(micro)) {
      rig.add("idle", key, this.stackLimit.enabled ? softLimitStack(key, value, this.stackLimit) : value);
    }

    // --- 记忆驱动表情/动作 ---
    this.memoryTimer += dt;
    if (this.memoryTimer >= this.memoryInterval) {
      this.memoryTimer = 0;
      this._fetchRelevantMemories();
    }
    // 应用记忆驱动的表情/动作
    for (const mem of this.activeMemories) {
      this._applyMemoryInfluence(mem, rig);
    }

    // 用户面部反应（优先级最低，其他层可以覆盖）
    this.face.apply(rig);

    // These small cues fill unclaimed face channels. Existing expression,
    // gesture and captured-face intent, including explicit zero, keeps priority.
    const protectedKeys = new Set([...Object.keys(gazeShift), ...Object.keys(pose || {}),
      ...Object.keys(discourseOut || {}), ...Object.keys(rig.layers.get('face').values)]);
    for (const side of ['L', 'R']) {
      const smile = `ParamEye${side}Smile`, lower = `ParamEye${side}Lower`;
      if (protectedKeys.has(smile) || protectedKeys.has(lower)) {
        protectedKeys.add(smile); protectedKeys.add(lower);
      }
    }
    const appliedValues = {};
    for (const [key, value] of Object.entries(dialogueExpression.values)) {
      if (controlledGaze || gazeLocked || protectedKeys.has(key) || !Number.isFinite(value)) continue;
      rig.add('idle', key, value); appliedValues[key] = value;
    }
    this.dialogueExpressionFrame = { ...dialogueExpression, appliedValues, protectedKeys: [...protectedKeys] };


    /* 连续情感维度灌给 idle。
     * 情绪层算情绪，idle 消费情绪并改写自己的呼吸/眼跳/眨眼——
     * 分工在这里划清，是因为"情绪标签"和"呼吸快慢"是不同的因果链。
     * 如果让 idle 自己去猜情绪，两边一定会漂移，
     * 表现是"她嘴上说开心，呼吸却是生气的节奏"。 */
    const affect = this.emotion.activeAffect;
    this.idle.setAffect(affect.arousal, affect.valence);

    /* 瞳孔：唤醒度越高越大。
     * 独立于表情——七个离散标签表达不出"同一种情绪的强弱"，
     * 而瞳孔是一条连续通道，这件事只有它做得到。 */
    rig.set("emotion", "ParamPupil", 1 + affect.arousal * 0.45);


    // 情绪/手势抢了视线就让 idle 别写，否则两个 gaze 会打架
    if (!gazeLocked) {
      rig.set("idle", "ParamEyeBallX", idleOut.gazeX);
      rig.set("idle", "ParamEyeBallY", idleOut.gazeY);
    }
    for (const [key, value] of Object.entries(gazeShift)) {
      if (controlledGaze && ["ParamEyeBallX", "ParamEyeBallY"].includes(key)) continue;
      rig.set("emotion", key, value);
      if (emotionGaze && !controlledGaze && ['ParamEyeBallX', 'ParamEyeBallY'].includes(key)) rig.mute('emotion', key);
    }

    if (pose) {
      // reduced-motion 下压低手势幅度而不是取消手势：
      // 一个幅度减半的点头仍然读得出"在点头"，完全没有动作则读不出。
      const g = this.reducedMotion ? this.gestureScale : 1;
      for (const [key, value] of Object.entries(pose)) {
        if ((semanticThinking || controlledGaze) && ['ParamEyeBallX', 'ParamEyeBallY'].includes(key)) continue;
        rig.set("gesture", key, lerp(store.neutral[key] || 0, value, g));
        if (gestureGaze && ['ParamEyeBallX', 'ParamEyeBallY'].includes(key)) rig.mute('gesture', key);
      }
    }

    if (discourseOut) {
      const g = this.reducedMotion ? this.gestureScale : 1;
      for (const [key, value] of Object.entries(discourseOut)) {
        rig.add("gesture", key, value * g); // 语篇手势用叠加，允许与关键帧手势叠加
      }
    }

    // 包络要在采样之前交给 lip，否则这一帧的张口度用的是上一帧的音量
    this.lip.setEnvelope(rms || 0, rms !== null);
    const frame = this.lip.sample(this.audioCtx);
    if (frame) {
      rig.set("lip", "ParamMouthOpenY", frame.open);
      rig.set("lip", "ParamMouthForm", frame.form);
      rig.set("lip", "ParamMouthRound", frame.round);
      rig.mute("lip", "ParamMouthOpenY");
      // 共发音的眯眼用叠加而不是覆盖，才能和情绪的眯眼/眨眼一起算
      if (frame.squint > 0) {
        rig.add("lip", "ParamEyeLOpen", -frame.squint);
        rig.add("lip", "ParamEyeROpen", -frame.squint);
      }
    }

    rig.commit(store);
    if (eyePreview.active) {
      for (const key of eyePreview.ownedKeys) {
        // Speech retains its mouth shape while the preview owns only its
        // resting mouth corner. Eyelids and brows remain expressive during TTS.
        if (key === "ParamMouthForm" && frame) continue;
        store.target[key] = lerp(store.target[key], eyePreview.values[key], eyePreview.weight);
      }
    }
    if (this.gazeMode === "forward") {
      store.target.ParamEyeBallX = 0;
      store.target.ParamEyeBallY = 0;
      store.target.ParamAngleX = 0;
      store.target.ParamAngleY = 0;
    } else if (eyePreview.active) {
      const scale = this.reducedMotion ? 0.35 : 1;
      store.target.ParamEyeBallX = clamp(store.target.ParamEyeBallX + eyePreview.gazeOffset.x * scale);
      store.target.ParamEyeBallY = clamp(store.target.ParamEyeBallY + eyePreview.gazeOffset.y * scale);
    }
    store.target.ParamEyeHighlight = clamp01((store.target.ParamEyeHighlight ?? 1) * this.eyeHighlight);
    store.target.ParamEyeLOpen = combineEyeOpen(store.target.ParamEyeLOpen, blink);
    store.target.ParamEyeROpen = combineEyeOpen(store.target.ParamEyeROpen, blink);
    store.blend(dt);
    const drawParams = { ...store.current };
    if (blink >= 0.999) {
      drawParams.ParamEyeLOpen = 0;
      drawParams.ParamEyeROpen = 0;
    }
    if (this.gazeMode === "forward") {
      drawParams.ParamEyeBallX = 0;
      drawParams.ParamEyeBallY = 0;
    }
    this.eyeFrame = { blink, preview: eyePreview.name, params: drawParams };
    this.svg.setPerformance?.(this.performance.sample(), this.reducedMotion);
    this.svg.draw(drawParams, dt);
    this.dialogueReview?.recordFrame(this);
  }

  /* --- 记忆驱动表情/动作 --- */
  _fetchRelevantMemories() {
    // 根据当前对话上下文获取相关记忆
    const query = this.lastUserText || "";
    if (!query) return;

    fetch(`/api/memories/context?query=${encodeURIComponent(this.lastUserText)}&limit=5`)
      .then(r => r.json())
      .then(data => {
        this.activeMemories = data.items || [];
        this.lastMemoryContext = data.context || "";
      })
      .catch(err => console.warn("获取记忆失败:", err));
  }

  _applyMemoryInfluence(mem, rig) {
    // 根据记忆内容调整表情/动作
    const kind = mem.kind;
    const value = mem.value.toLowerCase();

    // 根据记忆类型触发相应表情/动作
    if (kind === "preference" && this.emotion.current !== "base") {
      // 偏好记忆：触发相应情绪微表情
      if (value.includes("喜欢") || value.includes("爱") || value.includes("喜欢")) {
        this.emotion.set("得意");
      } else if (value.includes("讨厌") || value.includes("烦") || value.includes("讨厌")) {
        this.emotion.set("轻微不服");
      }
    } else if (kind === "event" && this.emotion.current === "base") {
      // 事件记忆：触发短暂表情
      if (value.includes("生日") || value.includes("纪念")) {
        this.emotion.set("得意");
      } else if (value.includes("难过") || value.includes("伤心")) {
        this.emotion.set("收敛");
      }
    } else if (kind === "fact" && this.emotion.current === "base") {
      // 事实类记忆：微表情
      if (value.includes("聪明") || value.includes("厉害")) {
        this.gesture.play("nod", this.clock);
      }
    }
  }
}

export const avatar = new Avatar();

/* 启动：先拿姿态契约，装载之前不要开始任何驱动。 */
export async function boot(containerId = "avatarStage") {
  /* The stage backdrop sheet is not linked from index.html after the redesign;
   * the avatar owns the stage, so it loads the sheet once here. */
  if (!document.querySelector('link[data-avatar-stage-fx]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/stage-fx.css";
    link.dataset.avatarStageFx = "";
    document.head.appendChild(link);
  }
  const res = await fetch("/api/avatar");
  const cfg = await res.json();
  avatar.configure(cfg);

  // 系统开了"减少动态效果"就跟着降下来，并且允许运行中切换
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    avatar.setReducedMotion(mq.matches);
    mq.addEventListener?.("change", (e) => avatar.setReducedMotion(e.matches));
  }

  const container = document.getElementById(containerId);
  if (container) avatar.mount(container);
  return avatar;
}

