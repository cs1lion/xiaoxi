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

import { ParamStore, Rigger, clamp, clamp01, lerp } from "./params.js";
import { buildTimeline } from "./timeline.js";
import { IdleDriver } from "./drivers/idle.js";
import { LipDriver } from "./drivers/lip.js";
import { EmotionDriver } from "./drivers/emotion.js";
import { GestureDriver } from "./drivers/gesture.js";
import { FaceDriver } from "./drivers/face.js";
import { BeatDriver } from "./drivers/beat.js";
import { MotionMagic, getMotionProfile } from "./drivers/motion.js";
import { SvgRig } from "./render/svg.js";

const LAYERS = [
  ["face", 5],
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
    this.reducedMotion = false;
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
    this.emotion = new EmotionDriver(this.cfg.emotions || {});
    this.gesture = new GestureDriver(this.cfg.triggers || {});
    this.gesture.setTracks(this.cfg.gestures || {});
    this.gesture.setTriggers(this.cfg.triggers || {});
    this.face = new FaceDriver();
    this.beat = new BeatDriver();
    this.motion = new MotionMagic();
    this.motion.setComponents(getMotionProfile("base"));
    this.emotion.set("base");

    /* 配置晚于第一条音频帧到达时，这里补播 */
    for (const item of this._pending.splice(0)) {
      this.lip.speak(buildTimeline(item.text, item.duration), item.startedAt);
    }
    return this;
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

  /* 在音频真正开始播放时切表情，而不是等文本到达。
   * 目前后端整轮只给一个情绪，所以这等价于空操作；
   * 一旦它逐句给不同情绪，表情会跟着声音自动切换，不需要改前端。 */
  setEmotion(name) {
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
    if (!this.cfg || !msg || typeof msg !== "object") return;
    const t = this.triggers;
    switch (msg.type) {
      case "recording":
        // 你在说话：起倾听姿势；松手了收掉
        if (msg.state) this.gesture.play(t.on_user_speech, this.clock);
        else this.gesture.stop(t.on_user_speech);
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
        this.motion.setComponents(getMotionProfile(msg.emotion || "base"));
        this.gesture.stop(t.on_thinking);
        this.gesture.stop(t.on_user_speech);
        break;
      case "text_delta":
        // 延迟标签：文本中的 [gesture:xxx] 在语音播放到对应位置时触发
        if (msg.delta) {
          const m = msg.delta.match(/\[gesture:(\w+)\]/);
          if (m) {
            const name = m[1];
            if (this.gesture.tracks[name]) {
              this.gesture.play(name, this.clock);
            }
          }
        }
        break;
      case "done":
        // 完整文本到手，这时才有依据做文本特征手势
        for (const name of this.gesture.matchText(msg.text)) {
          this.gesture.play(name, this.clock);
        }
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
    if (msg.type === "recording") {
      this.idle.setGaze(msg.state ? gazeRules.recording : gazeRules.emotion || "viewer");
    } else if (gazeRules[msg.type]) {
      this.idle.setGaze(gazeRules[msg.type]);
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
    const idleOut = this.idle.update(dt, { speaking, rms: rms || 0 });

    // 眨眼是"扣减"：叠在情绪的睁眼度上，眯眼时眨一次就是真闭眼
    rig.add("idle", "ParamEyeLOpen", -idleOut.blink);
    rig.add("idle", "ParamEyeROpen", -idleOut.blink);
    rig.set("idle", "ParamBreath", idleOut.breath * this.idle.cfg.breath.depth);
    rig.add("idle", "ParamAngleX", idleOut.swayX);
    rig.add("idle", "ParamAngleY", idleOut.swayY);
    rig.add("idle", "ParamAngleZ", idleOut.bob);

    // Motion Magic：程序化动作生成，无限不循环
    const motionOut = this.motion.update(dt);
    if (motionOut) {
      for (const [key, value] of Object.entries(motionOut)) {
        rig.add("idle", key, value);
      }
    }

    // 节拍同步：音频节拍驱动弹跳
    const beatOut = this.beat.update(this.outAnalyser, dt);
    if (beatOut.pulse > 0.01) {
      rig.add("idle", "ParamAngleY", beatOut.pulse * 0.12);
      rig.add("idle", "ParamBodyAngleX", beatOut.pulse * 0.08);
    }

    // 用户面部反应（优先级最低，其他层可以覆盖）
    this.face.apply(rig);

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
      // reduced-motion 下压低手势幅度而不是取消手势：
      // 一个幅度减半的点头仍然读得出"在点头"，完全没有动作则读不出。
      const g = this.reducedMotion ? this.gestureScale : 1;
      for (const [key, value] of Object.entries(pose)) {
        rig.set("gesture", key, lerp(store.neutral[key] || 0, value, g));
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