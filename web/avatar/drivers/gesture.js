/* 手势驱动：关键帧轨道 + 触发器。
 *
 * 手势是"自由发挥"的部分：思考时歪头、被惊讶到挑眉、被怼了耸肩。
 * 轨道全部写在 config/avatar.yaml 里，加一个新动作不用改代码。
 *
 * 关键帧按 stop 语义展开：每个关键帧的 at+dur 是一个停止点，段内线性插值，
 * 段内某键的起点取上一停止点的值；更早没有值就取"开始那一刻的当前值"，
 * 所以接任何姿势都不会跳变。
 */

import { clamp, lerp, smoothstep } from "../params.js";

function toStops(track) {
  return (track.keys || []).map((k) => ({
    t: Number(k.at) + Number(k.dur),
    values: k.params || {},
  }));
}

export class GestureDriver {
  constructor(triggers = {}) {
    this.tracks = {};
    this.triggers = triggers || {};
    this.idleLookAwayEvery = 11.0;
    this.active = null;        // { name, stops, elapsed, hold, priority }
    this.cooldownUntil = {};
    this.nextIdleLook = 11.0;
    this.rng = Math.random;
  }

  setTracks(tracks) {
    this.tracks = tracks || {};
  }

  setTriggers(triggers) {
    this.triggers = triggers || {};
    this.idleLookAwayEvery = Number(this.triggers.idle_look_away_every ?? 11.0);
    this.nextIdleLook = this.idleLookAwayEvery;
  }

  canPlay(name, now) {
    const track = this.tracks[name];
    if (!track) return false;
    if ((this.cooldownUntil[name] || 0) > now) return false;
    if (!this.active) return true;
    // 有轨在放时，只有更高优先级能打断；hold 轨优先级相同也可以被换掉
    return Number(track.priority || 0) >= this.active.priority;
  }

  play(name, now) {
    if (!this.canPlay(name, now)) return false;
    const track = this.tracks[name];
    
    // 计算各阶段时长
    const stops = toStops(track);
    const total = stops.length ? stops[stops.length - 1].t : 0;
    const prepDur = Math.min(0.15, total * 0.2);   // 准备期：最多 150ms，占总时长 20%
    const retDur = Math.min(0.2, total * 0.15);    // 收回期：最多 200ms，占总时长 15%
    const strokeDur = Math.max(0.05, total - prepDur - retDur); // 笔划期：剩余时间
    const holdDur = track.hold ? 999 : 0;           // hold 轨无限保持
    
    this.active = {
      name,
      stops,
      elapsed: -prepDur,  // 从负时间开始，表示准备期
      hold: !!track.hold,
      priority: Number(track.priority || 0),
      prepDur,
      strokeDur,
      retDur,
      holdDur,
      phase: 'preparation',  // preparation | stroke | hold | retraction
    };
    this.cooldownUntil[name] = now + Number(track.cooldown || 0);
    return true;
  }

  /* hold 轨要显式收掉，比如文本到了就松开"在想…" */
  stop(name) {
    if (this.active && this.active.name === name) this.active = null;
  }

  stopAll() {
    this.active = null;
  }

/* 每帧：推进时间，返回本帧要写入的参数。
 * store 用来在缺少前置停止点时抓"当前值"当起点。
 *
 * 手势四阶段（Kendon）：
 *   preparation  - 准备期：肢体移向起始姿态，幅度从 0 增至 1
 *   stroke       - 笔划期：核心动作执行，幅度 = 1
 *   hold         - 保持期：维持终点姿态（hold 轨）
 *   retraction   - 收回期：从终点平滑回中位
 */
update(dt, store, now) {
  if (!this.active) {
    this.nextIdleLook -= dt;
    if (this.nextIdleLook <= 0) {
      this.nextIdleLook = this.idleLookAwayEvery * (0.7 + this.rng() * 0.6);
      this.play("look_away", now);
    }
    return null;
  }

  const act = this.active;
  act.elapsed += dt;

  // 阶段判定
  const prepEnd = act.prepDur || 0;
  const strokeEnd = prepEnd + (act.strokeDur || 0);
  const holdEnd = act.holdDur > 0 ? strokeEnd + act.holdDur : strokeEnd;
  const retEnd = holdEnd + (act.retDur || 0);

  let phase;
  if (act.elapsed < prepEnd) phase = 'preparation';
  else if (act.elapsed < strokeEnd) phase = 'stroke';
  else if (act.holdDur > 0 && act.elapsed < holdEnd) phase = 'hold';
  else if (act.elapsed < holdEnd + (act.retDur || 0)) phase = 'retraction';
  else {
    this.active = null;
    return null;
  }
  act.phase = phase;

  // 各阶段的插值系数
  let phaseK;
  if (phase === 'preparation') {
    phaseK = act.prepDur > 0 ? smoothstep((act.elapsed + act.prepDur) / act.prepDur) : 1;
  } else if (phase === 'stroke') {
    phaseK = smoothstep(clamp((act.elapsed - act.prepDur) / act.strokeDur));
  } else if (phase === 'hold') {
    phaseK = 1;
  } else { // retraction
    phaseK = 1 - smoothstep(clamp((act.elapsed - holdEnd) / act.retDur));
  }

  const stops = act.stops;
  const total = stops.length ? stops[stops.length - 1].t : 0;
  if (!act.hold && act.elapsed >= total + (act.prepDur || 0) + (act.retDur || 0)) {
    this.active = null;
    return null;
  }

  let prevStop = null;
  let curStop = null;
  for (const stop of act.stops) {
    if (stop.t <= act.elapsed) prevStop = stop;
    else { curStop = stop; break; }
  }

  const out = {};
  const prevT = prevStop ? prevStop.t : 0;
  const prevValues = prevStop ? prevStop.values : null;
  const span = (curStop ? curStop.t : prevT) - prevT;
  const strokeK = smoothstep(curStop && span > 1e-9 ? clamp((act.elapsed - prevT) / span) : 1);

  // 根据阶段混合插值
  for (const [key, target] of Object.entries(curStop ? curStop.values : {})) {
    const from = prevValues && prevValues[key] !== undefined ? prevValues[key] : store.get(key);
    const strokeVal = lerp(from, target, strokeK);
    out[key] = lerp(from, target, phaseK);
  }
  // 已经在 curStop 停住的键也要维持，否则会松掉
  for (const [key, value] of Object.entries(prevValues || {})) {
    if (!curStop || curStop.values[key] === undefined) out[key] = value;
  }

  // 为 retraction 阶段准备：记录结束时的值作为回中位的起点
  if (phase === 'retraction' && !this._retractionBase) {
    this._retractionBase = { ...out };
  }

  return out;
  }

  /* 文本触发。needles 按长度倒序匹配，"确实" 要能盖过 "的" 之类的短词。 */
  matchText(text) {
    const table = this.triggers.text;
    if (!text || !table) return [];
    const hits = [];
    for (const [name, needles] of Object.entries(table)) {
      if (!Array.isArray(needles)) continue;
      const sorted = [...needles].filter(Boolean).sort((a, b) => b.length - a.length);
      if (sorted.some((n) => text.includes(n))) hits.push(name);
    }
    return hits;
  }

  /* 情绪触发：命中情绪且掷骰成功才演，避免每轮都来一次 */
  matchEmotion(emotion) {
    const table = this.triggers.on_emotion;
    if (!emotion || !table) return [];
    const hits = [];
    for (const [name, rule] of Object.entries(table)) {
      const emotions = rule?.emotions || [];
      if (!emotions.includes(emotion)) continue;
      if (this.rng() < Number(rule.chance ?? 1)) hits.push(name);
    }
    return hits;
  }
}