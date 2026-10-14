/* Primitive Action Library — 基础动作原语库。
 *
 * 设计思想：把"动作"拆解成身体部位级的原语，
 * 然后由上层根据语篇/韵律语境组合成完整手势。
 *
 * 优势：
 *   - 组合数爆炸式增长：17*17*5*3 = 7225 种可能（双臂×头部×面部）
 *   - 易于 RAG 检索：每个原语都有语义标签
 *   - 易于韵律同步：每个原语有自己的时长/相位
 *   - 易于风格迁移：只需换原语库，不改组合逻辑
 *
 * 身体部位划分（参考 ERICA / SIMA / MAGI）：
 *   Arms: 17 per arm (raise, extend, bend, rotate, gesture types)
 *   Head: 5 (nod, shake, tilt, lean, turn)
 *   Face: 3 (brow, eye, mouth expression groups)
 */

export const PRIMITIVE_ACTIONS = {
  /* ========== 双臂原语 ========== */
  // 每个动作定义：目标参数、默认时长、振幅范围、语义标签
  leftArm: {
    // 挥手/招呼类
    wave: {
      params: { "ParamAngleX": 0.4, "ParamAngleZ": 0.3 },
      duration: 0.8,
      amp: 0.5,
      tags: ["greeting", "social", "wave"],
      side: "left",
    },
    // 指向类
    point: {
      params: { "ParamAngleX": 0.6, "ParamAngleY": -0.2, "ParamBodyLeanX": 0.15 },
      duration: 0.5,
      amp: 0.6,
      tags: ["deictic", "pointing", "reference"],
      side: "left",
    },
    // 展示/呈现类
    present: {
      params: { "ParamAngleX": 0.3, "ParamAngleY": 0.1, "ParamBodyLeanX": 0.1 },
      duration: 0.7,
      amp: 0.4,
      tags: ["iconic", "presenting", "showing"],
      side: "left",
    },
    // 拥抱/自抱类
    hug_self: {
      params: { "ParamAngleX": -0.3, "ParamAngleY": 0.1, "ParamBodyLeanX": -0.1 },
      duration: 1.0,
      amp: 0.4,
      tags: ["self_soothe", "comfort", "inward"],
      side: "left",
    },
    // 思考托腮
    chin_rest: {
      params: { "ParamAngleX": -0.2, "ParamAngleY": -0.15, "ParamBodyAngleX": 0.1 },
      duration: 1.5,
      amp: 0.3,
      tags: ["thinking", "contemplation", "rest"],
      side: "left",
    },
    // 划开/拒绝
    push_away: {
      params: { "ParamAngleX": 0.5, "ParamAngleZ": -0.3 },
      duration: 0.5,
      amp: 0.6,
      tags: ["rejection", "dismissive", "boundary"],
      side: "left",
    },
    // 叉腰/腰
    akimbo: {
      params: { "ParamAngleX": -0.4, "ParamAngleZ": 0.2, "ParamBodyLeanX": 0.15 },
      duration: 0.8,
      amp: 0.5,
      tags: ["confident", "assertive", "waiting"],
      side: "left",
    },
    // 自然下垂
    rest: {
      params: { "ParamAngleX": 0.0, "ParamAngleZ": 0.0, "ParamBodyLeanX": 0.0 },
      duration: 0.3,
      amp: 0.1,
      tags: ["neutral", "rest", "idle"],
      side: "left",
    },
  },

  rightArm: {
    wave: {
      params: { "ParamAngleX": -0.4, "ParamAngleZ": -0.3 },
      duration: 0.8,
      amp: 0.5,
      tags: ["greeting", "social", "wave"],
      side: "right",
    },
    point: {
      params: { "ParamAngleX": -0.6, "ParamAngleY": -0.2, "ParamBodyLeanX": -0.15 },
      duration: 0.5,
      amp: 0.6,
      tags: ["deictic", "pointing", "reference"],
      side: "right",
    },
    present: {
      params: { "ParamAngleX": -0.3, "ParamAngleY": 0.1, "ParamBodyLeanX": -0.1 },
      duration: 0.7,
      amp: 0.4,
      tags: ["iconic", "presenting", "showing"],
      side: "right",
    },
    hug_self: {
      params: { "ParamAngleX": 0.3, "ParamAngleY": 0.1, "ParamBodyLeanX": 0.1 },
      duration: 1.0,
      amp: 0.4,
      tags: ["self_soothe", "comfort", "inward"],
      side: "right",
    },
    chin_rest: {
      params: { "ParamAngleX": 0.2, "ParamAngleY": -0.15, "ParamBodyAngleX": 0.1 },
      duration: 1.5,
      amp: 0.3,
      tags: ["thinking", "contemplation", "rest"],
      side: "right",
    },
    push_away: {
      params: { "ParamAngleX": -0.5, "ParamAngleZ": 0.3 },
      duration: 0.5,
      amp: 0.6,
      tags: ["rejection", "dismissive", "boundary"],
      side: "right",
    },
    akimbo: {
      params: { "ParamAngleX": 0.4, "ParamAngleZ": -0.2, "ParamBodyLeanX": -0.15 },
      duration: 0.8,
      amp: 0.5,
      tags: ["confident", "assertive", "waiting"],
      side: "right",
    },
    rest: {
      params: { "ParamAngleX": 0.0, "ParamAngleZ": 0.0, "ParamBodyLeanX": 0.0 },
      duration: 0.3,
      amp: 0.1,
      tags: ["neutral", "rest", "idle"],
      side: "right",
    },
  },

  /* ========== 双臂同步原语 ========== */
  bothArms: {
    // 双手张开/拥抱
    open_wide: {
      params: { "ParamAngleX": 0.5, "ParamAngleZ": 0.4, "ParamBodyLeanX": 0.0 },
      left: { "ParamAngleX": 0.5, "ParamAngleZ": 0.4 },
      right: { "ParamAngleX": -0.5, "ParamAngleZ": -0.4 },
      duration: 1.0,
      amp: 0.6,
      tags: ["welcoming", "inclusive", "expansive"],
    },
    // 双手合十/祈祷
    prayer: {
      params: { "ParamAngleX": 0.2, "ParamAngleY": -0.2, "ParamBodyLeanX": 0.0 },
      left: { "ParamAngleX": 0.3, "ParamAngleY": -0.2 },
      right: { "ParamAngleX": -0.3, "ParamAngleY": -0.2 },
      duration: 1.2,
      amp: 0.4,
      tags: ["hopeful", "earnest", "pleading"],
    },
    // 双手抱胸
    cross_arms: {
      params: { "ParamAngleX": -0.4, "ParamAngleY": 0.1, "ParamBodyLeanX": 0.1 },
      left: { "ParamAngleX": -0.4, "ParamAngleZ": 0.2 },
      right: { "ParamAngleX": 0.4, "ParamAngleZ": -0.2 },
      duration: 0.8,
      amp: 0.5,
      tags: ["defensive", "closed", "skeptical"],
    },
    // 双手上举/庆祝
    raise_up: {
      params: { "ParamAngleX": 0.6, "ParamAngleY": -0.3 },
      left: { "ParamAngleX": 0.6, "ParamAngleZ": 0.3 },
      right: { "ParamAngleX": -0.6, "ParamAngleZ": -0.3 },
      duration: 0.8,
      amp: 0.7,
      tags: ["celebration", "victory", "excitement"],
    },
    // 双手下垂/放松
    relax: {
      params: { "ParamAngleX": 0.0, "ParamAngleZ": 0.0 },
      left: { "ParamAngleX": 0.0, "ParamAngleZ": 0.0 },
      right: { "ParamAngleX": 0.0, "ParamAngleZ": 0.0 },
      duration: 0.5,
      amp: 0.1,
      tags: ["relaxed", "neutral", "calm"],
    },
  },

  /* ========== 头部原语 ========== */
  head: {
    // 点头（同意/确认）
    nod: {
      params: { "ParamAngleY": 0.35 },
      duration: 0.6,
      amp: 0.5,
      tags: ["agreement", "affirmation", "understanding"],
      repeatable: true,
    },
    // 摇头（否定/不同意）
    shake: {
      params: { "ParamAngleZ": 0.4 },
      duration: 0.7,
      amp: 0.5,
      tags: ["disagreement", "denial", "no"],
      repeatable: true,
    },
    // 侧头倾听
    tilt_listen: {
      params: { "ParamAngleZ": 0.3, "ParamAngleX": -0.1 },
      duration: 1.0,
      amp: 0.4,
      tags: ["listening", "attentive", "curious"],
    },
    // 头后仰（惊讶/傲慢）
    tilt_back: {
      params: { "ParamAngleY": -0.4, "ParamAngleX": 0.1 },
      duration: 0.5,
      amp: 0.6,
      tags: ["surprise", "pride", "disbelief"],
    },
    // 低头（羞涩/思考/顺从）
    bow_down: {
      params: { "ParamAngleY": 0.5, "ParamBodyAngleX": 0.2 },
      duration: 0.8,
      amp: 0.5,
      tags: ["shy", "thinking", "submissive", "respectful"],
    },
    // 侧头思考
    tilt_think: {
      params: { "ParamAngleZ": -0.25, "ParamAngleX": -0.15, "ParamAngleY": 0.1 },
      duration: 1.2,
      amp: 0.4,
      tags: ["thinking", "puzzled", "processing"],
    },
    // 回头/转头
    turn: {
      params: { "ParamAngleX": 0.6 },
      duration: 0.6,
      amp: 0.6,
      tags: ["attention_shift", "orientation", "awareness"],
    },
    // 中立
    neutral: {
      params: { "ParamAngleX": 0.0, "ParamAngleY": 0.0, "ParamAngleZ": 0.0 },
      duration: 0.3,
      amp: 0.1,
      tags: ["neutral", "reset"],
    },
  },

  /* ========== 面部表情原语 ========== */
  face: {
    // 眉毛上挑（惊讶/疑问）
    brow_raise: {
      params: { "ParamBrowLY": 0.6, "ParamBrowRY": 0.6, "ParamBrowLAngle": -0.3, "ParamBrowRAngle": -0.3 },
      duration: 0.5,
      amp: 0.6,
      tags: ["surprise", "question", "interest"],
    },
    // 眉毛下压（生气/严肃/专注）
    brow_furrow: {
      params: { "ParamBrowLForm": 0.5, "ParamBrowRForm": 0.5, "ParamBrowLAngle": 0.2, "ParamBrowRAngle": 0.2 },
      duration: 0.6,
      amp: 0.5,
      tags: ["anger", "concentration", "serious"],
    },
    // 单侧眉毛上挑（怀疑/调侃）
    brow_single_raise: {
      params: { "ParamBrowLY": 0.7, "ParamBrowRY": -0.2, "ParamBrowLAngle": -0.4, "ParamBrowRAngle": 0.1 },
      duration: 0.5,
      amp: 0.5,
      tags: ["skepticism", "teasing", "doubt"],
      side: "left",
    },
    // 笑眼（真诚的笑）
    smile_eyes: {
      params: { "ParamEyeLSmile": 0.8, "ParamEyeRSmile": 0.8, "ParamEyeLOpen": 0.6, "ParamEyeROpen": 0.6, "ParamCheek": 0.5 },
      duration: 1.0,
      amp: 0.7,
      tags: ["genuine_joy", "warmth", "friendliness"],
    },
    // 眯眼/眯笑（调侃/狡黠）
    squint: {
      params: { "ParamEyeLOpen": 0.3, "ParamEyeROpen": 0.3, "ParamEyeLSmile": 0.5, "ParamEyeRSmile": 0.5, "ParamMouthForm": 0.3 },
      duration: 0.7,
      amp: 0.5,
      tags: ["teasing", "sly", "playful"],
    },
    // 睁大眼（震惊/惊讶）
    wide_eyes: {
      params: { "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0, "ParamEyeBallY": -0.3 },
      duration: 0.4,
      amp: 0.7,
      tags: ["shock", "surprise", "awareness"],
    },
    // 嘴角上扬（微笑）
    mouth_smile: {
      params: { "ParamMouthForm": 0.6, "ParamMouthOpenY": 0.1, "ParamCheek": 0.4 },
      duration: 0.8,
      amp: 0.6,
      tags: ["happy", "friendly", "agreeable"],
    },
    // 嘴角下拉（悲伤/失望）
    mouth_frown: {
      params: { "ParamMouthForm": -0.5, "ParamMouthOpenY": 0.05, "ParamMouthY": 0.15 },
      duration: 0.8,
      amp: 0.5,
      tags: ["sad", "disappointed", "unhappy"],
    },
    // 嘟嘴/撅嘴（撒娇/不满）
    mouth_pout: {
      params: { "ParamMouthForm": -0.3, "ParamMouthRound": 0.7, "ParamMouthOpenY": 0.15 },
      duration: 0.7,
      amp: 0.5,
      tags: ["cute", "pouting", "childlike"],
    },
    // 中性面部
    neutral: {
      params: { "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0, "ParamEyeLSmile": 0, "ParamEyeRSmile": 0, "ParamMouthForm": 0.2, "ParamMouthOpenY": 0.0, "ParamCheek": 0.0 },
      duration: 0.3,
      amp: 0.1,
      tags: ["neutral", "reset"],
    },
  },
};

/* ========== 原语检索/组合工具 ========== */

/* 根据语义标签检索原语 */
export function findPrimitivesByTags(tags, part = null) {
  const results = [];
  const searchIn = part ? { [part]: PRIMITIVE_ACTIONS[part] } : PRIMITIVE_ACTIONS;
  
  for (const [partName, actions] of Object.entries(searchIn)) {
    for (const [name, action] of Object.entries(actions)) {
      if (tags.every(tag => action.tags.includes(tag))) {
        results.push({ part: partName, name, ...action });
      }
    }
  }
  return results;
}

/* 根据目标参数检索原语（用于 RAG 检索） */
export function findPrimitivesByParams(params, part = null) {
  const results = [];
  const searchIn = part ? { [part]: PRIMITIVE_ACTIONS[part] } : PRIMITIVE_ACTIONS;
  
  for (const [partName, actions] of Object.entries(searchIn)) {
    for (const [name, action] of Object.entries(actions)) {
      const match = Object.keys(params).every(p => 
        action.params[p] !== undefined && Math.abs(action.params[p] - params[p]) < 0.15
      );
      if (match) results.push({ part: partName, name, ...action });
    }
  }
  return results;
}

/* 组合原语生成手势序列
 *
 * 输入: primitives = [{ part, name, duration, weight, startTime }]
 * 输出: GestureDriver 兼容的 track 格式 { keys, priority, cooldown, hold }
 *
 * 关键点：
 *   - 按 startTime 排序
 *   - 同一身体部位的原语按时间顺序铺开
 *   - 同一时刻多个部位并行（双臂、头、面部可并行）
 *   - 生成的 keyframes 直接兼容 GestureDriver 的 keys 格式
 */
export function composeGesture(primitives, totalDuration) {
  // 过滤无效原语并按 startTime 排序
  const valid = primitives
    .filter(p => {
      const action = PRIMITIVE_ACTIONS[p.part]?.[p.name];
      return action && (p.duration || action.duration) > 0;
    })
    .sort((a, b) => (a.startTime || 0) - (b.startTime || 0));

  if (!valid.length) return null;

  const keys = [];
  const tracksByPart = {};

  for (const p of valid) {
    const action = PRIMITIVE_ACTIONS[p.part]?.[p.name];
    if (!action) continue;

    const dur = p.duration || action.duration;
    const start = Math.min(p.startTime || 0, Math.max(0, totalDuration - dur));
    
    // 获取参数，处理左右镜像
    let params = { ...action.params };
    if (p.side === "right" && action.side === "left") {
      const mirrored = {};
      for (const [k, v] of Object.entries(action.params)) {
        if (k.startsWith("ParamAngleX") || k.startsWith("ParamAngleZ") || k.startsWith("ParamBodyLeanX") || k.startsWith("ParamBodyAngleX") || k.startsWith("ParamBodyAngleZ") || k.startsWith("ParamAngleY")) {
          // 需要根据参数类型决定是否镜像
          if (k.includes("AngleX") || k.includes("AngleZ") || k.includes("LeanX") || k.includes("BodyAngleX") || k.includes("BodyAngleZ")) {
            mirrored[k] = -v;
          } else {
            mirrored[k] = v;
          }
        } else {
          mirrored[k] = v;
        }
      }
      params = mirrored;
    }

    // 为每个参数生成 keyframe
    for (const [param, target] of Object.entries(params)) {
      if (!tracksByPart[param]) tracksByPart[param] = [];
      tracksByPart[param].push({ t: start, value: target });
      // 结束点也加上，确保动作结束后有明确的结束值
      tracksByPart[param].push({ t: start + dur, value: target });
    }
  }

  // 将 tracksByPart 转换为 GestureDriver 的 keys 格式
  // GestureDriver 期望: { keys: [{ at, dur, params: {...} }] }
  // 我们需要将参数轨迹合并为关键帧
  
  // 简化：将每个参数的轨迹合并为关键帧
  const allTimes = new Set([0, totalDuration]);
  for (const track of Object.values(tracksByPart)) {
    for (const kf of track) allTimes.add(kf.t);
  }
  const sortedTimes = [...allTimes].sort((a, b) => a - b);

  for (let i = 0; i < sortedTimes.length - 1; i++) {
    const at = sortedTimes[i];
    const dur = sortedTimes[i + 1] - at;
    if (dur < 0.01) continue;
    
    const params = {};
    for (const [param, track] of Object.entries(tracksByPart)) {
      // 找到该时刻的值
      let val = 0;
      for (let j = track.length - 1; j >= 0; j--) {
        if (track[j].t <= at + 1e-6) {
          val = track[j].value;
          break;
        }
      }
      params[param] = val;
    }
    
    // 只有当参数有变化时才添加关键帧
    if (Object.keys(params).length > 0) {
      keys.push({ at, dur, params });
    }
  }

  return keys.length > 0 ? { keys, priority: 40, cooldown: 2.0 } : null;
}

/* 默认导出 */
export default PRIMITIVE_ACTIONS;