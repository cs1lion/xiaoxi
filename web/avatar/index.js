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

import { ParamStore, Rigger, clamp, clamp01 } from "./params.js";
import { buildTimeline } from "./timeline.js";
import { IdleDriver } from "./drivers/idle.js";
import { LipDriver } from "./drivers/lip.js";
import { EmotionDriver } from "./drivers/emotion.js";
import { GestureDriver } from "./drivers/gesture.js";
import { SvgRig } from "./render/svg.js";

const LAYERS = [
  ["idle", 10],
  ["emotion", 20],
  ["gesture", 30],
  ["lip", 40],
];

export class Avatar {
  constructor() {
    this.cfg = null;
    this.store = null;
    this.rig = null;
    this.svg = new SvgRig();
    this.clock = 0;
    this.audioCtx = null;
    this._raf = 0;
    this._last = 0;
    this._pending = [];
    this.outAnalyser = null;
    this._outBuf = null;
  }

  configure(cfg) {
    this.cfg = cfg || {};
    this.triggers = this.cfg.triggers || {};
    this.store = new ParamStore(this.cfg.neutral || {}, this.cfg.response || {});
    this.rig = new Rigger(this.cfg.neutral || {});
    for (const [name, priority] of LAYERS) this.rig.register(name, priority);

    this.idle = new IdleDriver(this.cfg.idle || {});
    this.lip = new LipDriver();
    this.emotion = new EmotionDriver(this.cfg.emotions || {});
    this.gesture = new GestureDriver(this.cfg.triggers || {});
    this.gesture.setTracks(this.cfg.gestures || {});
    this.gesture.setTriggers(this.cfg.triggers || {});
    this.emotion.set("base");

    // 配置晚于第一条音频帧到达时，这里补播
    for (const item of this._pending.splice(0)) {
      this.lip.speak(buildTimeline(item.text, item.duration), item.startedAt);
    }
    return this;
  }

  mount(container) {
    this.svg.mount(container, this.cfg?.look || {});
    if (!this._raf) this._start();
    return this;
  }

  _start() {
    this._last = performance.now();
    const loop = (now) => {
      // 标签页切回来时 dt 会很大，钳住避免参数瞬移
      const dt = clamp((now - this._last) / 1000, 0, 0.05);
      this._last = now;
      this.clock += dt;
      this._frame(dt);
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

  /* 没有 analyser（静音 TTS、测试驱动）时包络恒为 0，
   口型仍然完全由文本时间轴驱动，不会退化。 */
  _readRms() {
    if (!this.outAnalyser) return 0;
    const buf = this._outBuf;
    this.outAnalyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    // TTS 输出峰值大概在 0.2 上下，×5 之后大约落在 0..1
    return clamp01(Math.sqrt(sum / buf.length) * 5);
  }

  /* 分句开始播放：把文本摊到这段音频的真实时长上 */
  speak(text, ctx, startedAt, durationSec) {
    this.audioCtx = ctx || this.audioCtx;
    if (!this.store) {
      this._pending.push({ text, startedAt, duration: durationSec });
      return;
    }
    this.lip.speak(buildTimeline(text, durationSec), startedAt);
  }

  stopSpeaking() {
    this.lip.stop();
  }

  /* ---- WebSocket 消息入口 ---- */

  onMessage(msg) {
    if (!this.cfg) return;
    const t = this.triggers;
    switch (msg.type) {
      case "thinking":
        // 松开倾听姿势，换成思考
        this.gesture.stop(t.on_user_speech);
        this.gesture.play(t.on_thinking, this.clock);
        break;
      case "recording":
        if (msg.state) this.gesture.play(t.on_user_speech, this.clock);
        else this.gesture.stop(t.on_user_speech);
        break;
      case "asr":
      case "text":
        // 文本到了，思考/倾听姿势收掉
        this.gesture.stop(t.on_thinking);
        this.gesture.stop(t.on_user_speech);
        break;
      case "playback_stopped":
      case "interrupted":
        this.onBargeIn();
        break;
      default:
        break;
    }

    if (msg.type !== "text") return;

    const emotion = msg.emotion || "base";
    if (this.emotion.set(emotion)) {
      for (const name of this.gesture.matchEmotion(emotion)) {
        this.gesture.play(name, this.clock);
      }
    }
    for (const name of this.gesture.matchText(msg.text)) {
      this.gesture.play(name, this.clock);
    }
  }

  /* 被打断：嘴立刻还回去，思考/倾听姿势松掉，表情回中位 */
  onBargeIn() {
    this.lip.stop();
    this.gesture.stopAll();
    this.emotion.set("base");
  }

  /* ---- 每帧 ---- */

  _frame(dt) {
    const { store, rig } = this;
    rig.begin();

    const rms = this._readRms();
    const speaking = this.lip.speaking;
    const idleOut = this.idle.update(dt, { speaking, rms });

    // 眨眼是"扣减"：叠在情绪的睁眼度上，眯眼时眨一次就是真闭眼
    rig.add("idle", "ParamEyeLOpen", -idleOut.blink);
    rig.add("idle", "ParamEyeROpen", -idleOut.blink);
    rig.set("idle", "ParamBreath", idleOut.breath * this.idle.cfg.breath.depth);
    rig.add("idle", "ParamAngleX", idleOut.swayX);
    rig.add("idle", "ParamAngleY", idleOut.swayY);
    rig.add("idle", "ParamAngleZ", idleOut.bob);

    this.emotion.update(dt);
    const gazeShift = this.emotion.shift();
    // 情绪/手势抢了视线就让 idle 别写，否则两个 gaze 会打架
    const gazeLocked = this.emotion.ownsGaze || this.gesture.active;
    this.idle.claimGaze(gazeLocked);
    if (!gazeLocked) {
      rig.set("idle", "ParamEyeBallX", idleOut.gazeX);
      rig.set("idle", "ParamEyeBallY", idleOut.gazeY);
    }
    for (const [key, value] of Object.entries(gazeShift)) {
      rig.set("emotion", key, value);
    }

    const pose = this.gesture.update(dt, store, this.clock);
    if (pose) {
      for (const [key, value] of Object.entries(pose)) rig.set("gesture", key, value);
    }

    // 包络要在采样之前交给 lip，否则这一帧的张口度用的是上一帧的音量
    this.lip.setRms(rms);
    const frame = this.lip.sample(this.audioCtx);
    if (frame) {
      rig.set("lip", "ParamMouthOpenY", frame.open);
      rig.set("lip", "ParamMouthForm", frame.form);
      rig.set("lip", "ParamMouthRound", frame.round);
      rig.mute("lip", "ParamMouthOpenY");
    }

    rig.commit(store);
    store.blend(dt);
    this.svg.draw(store.current, dt);
  }
}

export const avatar = new Avatar();

/* 启动：先拿姿态契约，装载之前不要开始任何驱动。 */
export async function boot(containerId = "avatarStage") {
  const res = await fetch("/api/avatar");
  const cfg = await res.json();
  avatar.configure(cfg);
  const container = document.getElementById(containerId);
  if (container) avatar.mount(container);
  return avatar;
}