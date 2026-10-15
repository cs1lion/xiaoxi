/* V2 三梯度舞台驱动（协议 §16）：micro_act 微动作 + touch_head/touch_face 区域反馈。
 * 纯决策逻辑 + 可注入钩子，零 avatar 导入，便于 node 单测；真正渲染由调用方把
 * applyMicroAct / onTouchLine 接到形象层的 idle/gesture 系统。未知 action 安全降级，
 * 不伪造已播放效果；event_id 去重，重连不重复演出。 */

export const MICRO_ACT_MAP = {
  glance_away: { kind: "gaze", drift: 0.5, duration: 0.6 },
  tilt_head: { kind: "pose", tilt: 0.25, duration: 0.8 },
  stretch: { kind: "pose", stretch: 0.4, duration: 1.0 },
  fidget: { kind: "micro", amount: 0.3, duration: 0.5 },
  thoughtful: { kind: "gaze", up: 0.3, duration: 0.9 },
  soft_blink: { kind: "blink", double: false, duration: 0.3 },
  // V3 监工微动作（协议 §16 / EXECUTIVE_SECRETARY_V3.md §2.3）：后台有督办在跑时，
  // 小溪低头翻手账、瞥监控副屏、笔尖点两下。kind 仍取既有三类，渲染侧无需改动。
  ledger_glance: { kind: "gaze", down: 0.45, duration: 1.2 },
  monitor_glance: { kind: "gaze", right: 0.4, up: 0.12, duration: 1.0 },
  pen_tap: { kind: "micro", amount: 0.22, duration: 0.6 },
};
const MICRO_ACT_FALLBACK = { kind: "micro", amount: 0.2, duration: 0.5 };
const SUPERVISOR_ACTIONS = ["ledger_glance", "monitor_glance", "pen_tap"];
const TOUCH_KEYS = { head: "touch_head", face: "touch_face" };
const MAX_SEEN = 256;

export function resolveMicroAct(action) {
  return Object.prototype.hasOwnProperty.call(MICRO_ACT_MAP, action)
    ? MICRO_ACT_MAP[action] : MICRO_ACT_FALLBACK;
}

// 后台 RUNNING 时的监工微动作轮换：按序号取，避免同一空闲周期里重复同一个姿势。
export function supervisorAction(seq) {
  const index = Math.abs(Math.trunc(Number(seq) || 0)) % SUPERVISOR_ACTIONS.length;
  return SUPERVISOR_ACTIONS[index];
}

export function pickTouchLine(samples, region, rng) {
  const key = TOUCH_KEYS[region];
  if (!key) return "";
  const stage = (samples && samples.stage_samples) || {};
  const list = stage[key];
  if (!Array.isArray(list) || list.length === 0) return "";
  const roll = typeof rng === "function" ? rng() : 0.5;
  const index = Math.min(list.length - 1, Math.max(0, Math.floor(roll * list.length)));
  return typeof list[index] === "string" ? list[index] : "";
}

export class AmbientDriver {
  constructor({ applyMicroAct, onTouchLine, samples } = {}) {
    this.applyMicroAct = typeof applyMicroAct === "function" ? applyMicroAct : () => {};
    this.onTouchLine = typeof onTouchLine === "function" ? onTouchLine : () => {};
    this.samples = samples || { stage_samples: {} };
    this._seen = new Set();
  }

  handleEvent(event) {
    if (!event || typeof event !== "object" || event.type !== "micro_act") return false;
    if (event.event_id) {
      if (this._seen.has(event.event_id)) return false;  // replay / reconnect guard
      this._seen.add(event.event_id);
      if (this._seen.size > MAX_SEEN) this._seen.delete(this._seen.values().next().value);
    }
    this.applyMicroAct(resolveMicroAct(event.action), event);
    return true;
  }

  touch(region) {
    const line = pickTouchLine(this.samples, region);
    if (line) this.onTouchLine(region, line);
    return line;
  }
}
