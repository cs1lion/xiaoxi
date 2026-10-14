/* 语篇感知手势生成器 — Discourse-Aware Gesture Generator。
 *
 * 核心思路（融合 RAG-GESTURE + LLM Gesticulator + Primitive Action Selection）：
 *   1. 语篇分析：从文本中提取话语连接词、修辞结构、焦点/主题
 *   2. 韵律突出检测：从音频包络提取重音位置、语调边界
 *   3. 原语检索：根据语义标签、参数匹配、韵律位置检索原语
 *   4. 原语组合：按时间轴组合原语，同步到 TTS 时长
 *
 * 核心区别于传统规则/数据驱动：
 *   - 不写死"文本 X -> 手势 Y"，而是"语境 X -> 原语池 Y -> 组合 Z"
 *   - 检索时机：推理时（推理时 RAG），无需重训练
 *   - 可解释：每个手势都能追溯到"为什么选这个原语"
 */

import { PRIMITIVE_ACTIONS, findPrimitivesByTags, composeGesture } from "./primitives.js";

/* ========== 话语连接词词典 ==========
 * 每个连接词映射到：
 *   - 语篇关系
 *   - 触发的原语标签
 *   - 默认手势时长倍率
 */
const DISCOURSE_CONNECTIVES = {
  // 因果关系
  "因为": { relation: "cause", tags: ["reasoning", "explanation"], durationMul: 1.0 },
  "所以": { relation: "consequence", tags: ["conclusion", "result"], durationMul: 0.9 },
  "因此": { relation: "consequence", tags: ["conclusion", "result"], durationMul: 0.9 },
  "由于": { relation: "cause", tags: ["reasoning", "explanation"], durationMul: 1.0 },
  "导致": { relation: "cause", tags: ["causation", "mechanism"], durationMul: 1.0 },
  "之所以": { relation: "cause", tags: ["deep_reason", "root_cause"], durationMul: 1.1 },
  "从而": { relation: "consequence", tags: ["result", "outcome"], durationMul: 0.9 },

  // 转折/对比
  "但是": { relation: "contrast", tags: ["contrast", "objection"], durationMul: 1.0 },
  "可是": { relation: "contrast", tags: ["contrast", "mild_objection"], durationMul: 1.0 },
  "不过": { relation: "contrast", tags: ["qualification", "soft_contrast"], durationMul: 0.9 },
  "却": { relation: "contrast", tags: ["unexpected", "counter"], durationMul: 0.9 },
  "相反": { relation: "contrast", tags: ["opposite", "reverse"], durationMul: 1.0 },
  "反之": { relation: "contrast", tags: ["reverse", "inverse"], durationMul: 1.0 },
  "反而": { relation: "contrast", tags: ["surprising", "contrary"], durationMul: 1.0 },

  // 让步
  "虽然": { relation: "concession", tags: ["concession", "despite"], durationMul: 1.0 },
  "尽管": { relation: "concession", tags: ["concession", "stronger"], durationMul: 1.0 },
  "即使": { relation: "concession", tags: ["hypothetical_concession"], durationMul: 1.1 },
  "纵然": { relation: "concession", tags: ["strong_concession"], durationMul: 1.1 },

  // 并列/递进
  "而且": { relation: "addition", tags: ["addition", "emphasis"], durationMul: 0.9 },
  "并且": { relation: "addition", tags: ["addition", "formal"], durationMul: 0.9 },
  "此外": { relation: "addition", tags: ["additional", "furthermore"], durationMul: 1.0 },
  "另外": { relation: "addition", tags: ["additional", "side_point"], durationMul: 0.9 },
  "再者": { relation: "addition", tags: ["further", "moreover"], durationMul: 1.0 },
  "进而": { relation: "progression", tags: ["progression", "deepening"], durationMul: 1.0 },

  // 顺承/承接
  "然后": { relation: "sequence", tags: ["temporal", "next"], durationMul: 0.8 },
  "接着": { relation: "sequence", tags: ["continuation", "immediate"], durationMul: 0.8 },
  "随后": { relation: "sequence", tags: ["sequence", "formal"], durationMul: 0.9 },
  "后来": { relation: "sequence", tags: ["temporal", "later"], durationMul: 0.9 },
  "最终": { relation: "culmination", tags: ["conclusion", "final"], durationMul: 1.0 },
  "最后": { relation: "culmination", tags: ["final", "ending"], durationMul: 1.0 },

  // 条件/假设
  "如果": { relation: "condition", tags: ["hypothesis", "conditional"], durationMul: 1.0 },
  "假如": { relation: "condition", tags: ["hypothetical", "speculative"], durationMul: 1.1 },
  "倘若": { relation: "condition", tags: ["hypothetical", "literary"], durationMul: 1.1 },
  "只要": { relation: "condition", tags: ["sufficient_condition"], durationMul: 1.0 },
  "除非": { relation: "exception", tags: ["exception", "unless"], durationMul: 1.0 },

  // 目的/意图
  "为了": { relation: "purpose", tags: ["goal", "intent"], durationMul: 1.0 },
  "以便": { relation: "purpose", tags: ["facilitation", "means"], durationMul: 1.0 },
  "以免": { relation: "prevention", tags: ["avoidance", "caution"], durationMul: 1.0 },

  // 解释/说明
  "也就是说": { relation: "clarification", tags: ["rephrasing", "clarification"], durationMul: 1.1 },
  "换句话说": { relation: "clarification", tags: ["rephrasing", "paraphrase"], durationMul: 1.1 },
  "具体来说": { relation: "elaboration", tags: ["detail", "specification"], durationMul: 1.1 },
  "例如": { relation: "example", tags: ["illustration", "concrete"], durationMul: 1.0 },
  "比如": { relation: "example", tags: ["illustration", "casual"], durationMul: 1.0 },
  "比如": { relation: "example", tags: ["example", "instance"], durationMul: 1.0 },
  "譬如": { relation: "example", tags: ["example", "formal"], durationMul: 1.0 },

  // 总结/概括
  "总之": { relation: "summary", tags: ["summary", "conclusion"], durationMul: 1.0 },
  "总的来说": { relation: "summary", tags: ["overview", "general"], durationMul: 1.0 },
  "简言之": { relation: "summary", tags: ["brief", "concise"], durationMul: 0.9 },
  "一句话": { relation: "summary", tags: ["gist", "essence"], durationMul: 1.0 },

  // 话题转换/标记
  "说到": { relation: "topic_shift", tags: ["topic_intro", "transition"], durationMul: 1.0 },
  "说回": { relation: "topic_return", tags: ["topic_return", "resume"], durationMul: 0.9 },
  "顺便说": { relation: "digression", tags: ["aside", "parenthetical"], durationMul: 0.8 },
  "顺带": { relation: "digression", tags: ["aside", "minor"], durationMul: 0.8 },

  // 语气/态度标记
  "其实": { relation: "truth_marker", tags: ["revelation", "correction"], durationMul: 1.0 },
  "实际上": { relation: "truth_marker", tags: ["fact", "reality"], durationMul: 1.0 },
  "说实话": { relation: "honesty", tags: ["candor", "honesty"], durationMul: 1.0 },
  "坦白说": { relation: "honesty", tags: ["frankness", "direct"], durationMul: 1.0 },
  "遗憾的是": { relation: "regret", tags: ["regret", "unfortunate"], durationMul: 1.0 },
  "幸运的是": { relation: "fortune", tags: ["luck", "positive"], durationMul: 1.0 },
  "意外地": { relation: "surprise", tags: ["unexpected", "surprising"], durationMul: 1.0 },
  "令人惊讶": { relation: "surprise", tags: ["surprise", "amazement"], durationMul: 1.0 },

  // 强调/聚焦
  "特别": { relation: "focus", tags: ["emphasis", "special"], durationMul: 1.0 },
  "特别地": { relation: "focus", tags: ["emphasis", "particular"], durationMul: 1.0 },
  "尤其": { relation: "focus", tags: ["particular", "especially"], durationMul: 1.0 },
  "最重要": { relation: "focus", tags: ["crucial", "key"], durationMul: 1.1 },
  "核心": { relation: "focus", tags: ["core", "central"], durationMul: 1.1 },

  // 不确定/推测
  "大概": { relation: "uncertainty", tags: ["probable", "estimate"], durationMul: 1.0 },
  "可能": { relation: "uncertainty", tags: ["possibility", "maybe"], durationMul: 1.0 },
  "大概率": { relation: "uncertainty", tags: ["probable", "likely"], durationMul: 1.0 },
  "似乎": { relation: "uncertainty", tags: ["appearance", "seems"], durationMul: 1.0 },
  "好像": { relation: "uncertainty", tags: ["resemblance", "uncertain"], durationMul: 1.0 },
};

/* ========== 修辞结构标记 ========== */
const RHETORICAL_MARKERS = {
  // 并列结构
  "一方面": { structure: "parallel", tags: ["first_part", "parallel_start"], durationMul: 1.0 },
  "另一方面": { structure: "parallel", tags: ["second_part", "parallel_end"], durationMul: 1.0 },
  "既...又...": { structure: "parallel", tags: ["dual_aspect", "both"], durationMul: 1.0 },
  "不仅...而且...": { structure: "progressive", tags: ["escalation", "progressive"], durationMul: 1.0 },
  "不但...而且...": { structure: "progressive", tags: ["escalation", "stronger"], durationMul: 1.0 },

  // 对比结构
  "与其...不如...": { structure: "comparison", tags: ["preference", "better_option"], durationMul: 1.0 },
  "宁可...也...": { structure: "comparison", tags: ["strong_preference", "resolve"], durationMul: 1.0 },

  // 条件-结果
  "只有...才...": { structure: "necessary_condition", tags: ["necessity", "requirement"], durationMul: 1.1 },
  "无论...都...": { structure: "universal", tags: ["universal", "unconditional"], durationMul: 1.0 },
};

/* ========== 韵律突出检测 ==========
 * 从音频包络提取重音位置、语调边界
 * 这里提供接口，实际实现需要音频分析
 */
export class ProsodyAnalyzer {
  constructor() {
    this.energyHistory = [];
    this.pitchHistory = []; // 需要音高检测，简化版用能量代替
    this.maxHistory = 120; // 约 2 秒 @ 60fps
  }

  /* 每帧推入能量值 */
  push(energy, pitch = 0) {
    this.energyHistory.push(energy);
    this.pitchHistory.push(pitch);
    if (this.energyHistory.length > this.maxHistory) {
      this.energyHistory.shift();
      this.pitchHistory.shift();
    }
  }

  /* 检测当前帧是否为韵律突出点（重音/重音核） */
  detectProminence() {
    if (this.energyHistory.length < 10) return { isProminent: false, strength: 0 };
    
    // 简单的峰值检测：当前能量显著大于前后均值
    const n = this.energyHistory.length;
    const curr = this.energyHistory[n - 1];
    const window = 5;
    const before = this.energyHistory.slice(n - 1 - window, n - 1).reduce((a, b) => a + b, 0) / window;
    const after = n >= n ? 0 : this.energyHistory[n].slice(1, 1 + window).reduce((a, b) => a + b, 0) / window; // 简化
    
    const localMean = (before + after) / 2;
    const ratio = localMean > 0 ? curr / localMean : 1;
    
    // 阈值：能量比周围高 1.5 倍以上
    const isProminent = ratio > 1.5;
    const strength = Math.min(1, (ratio - 1) / 2); // 0~1
    
    return { isProminent, strength, ratio };
  }

  /* 检测语调边界（停顿/语调重置） */
  detectBoundary() {
    if (this.energyHistory.length < 20) return { isBoundary: false };
    
    // 能量显著下降且随后上升 = 语调边界
    const n = this.energyHistory.length;
    const recent = this.energyHistory.slice(n - 10);
    const minIdx = recent.indexOf(Math.min(...recent));
    const beforeMin = recent.slice(0, minIdx);
    const afterMin = recent.slice(minIdx + 1);
    
    const dropping = beforeMin.length > 3 && beforeMin[0] > beforeMin[beforeMin.length - 1];
    const rising = afterMin.length > 3 && afterMin[afterMin.length - 1] > afterMin[0];
    
    return { isBoundary: dropping && rising };
  }
}

/* ========== 伴随反应生成器 ==========
 * 
 * 当用户说话时，自动生成点头、微表情等伴随反应
 * 研究表明：听众每 3-5 秒会自然点头一次，重音位置更易触发
 */
export class BackchannelGenerator {
  constructor() {
    this.lastBackchannel = -10;
    this.minInterval = 2.5;  // 最小间隔（秒）
    this.maxInterval = 6.0;  // 最大间隔
    this.probability = 0.15; // 基础触发概率
    this.prominenceBoost = 3.0; // 韵律突出时的概率倍增
  }

  /* 
   * 每帧调用，返回是否应触发 backchannel
   * 返回: { triggered: boolean, type: "nod"|"micro_expression", intensity: 0-1 }
   */
  update(userSpeaking, audioEnergy, dt, prosodyProminence = null) {
    if (!userSpeaking) return { triggered: false };
    
    const now = performance.now() / 1000;
    const sinceLast = now - this.lastBackchannel;
    
    if (sinceLast < this.minInterval) return { triggered: false };
    
    // 计算触发概率
    let prob = this.probability;
    
    // 韵律突出时大幅提升概率
    if (prosodyProminence && prosodyProminence.isProminent) {
      prob *= this.prominenceBoost * prosodyProminence.strength;
    }
    
    // 能量相关：用户音量大时更可能点头
    prob *= 1 + audioEnergy * 0.5;
    
    // 随机触发
    if (Math.random() < prob * dt / this.maxInterval) {
      this.lastBackchannel = now;
      const types = ["nod", "micro_nod", "brow_flash"];
      const type = types[Math.floor(Math.random() * types.length)];
      return { triggered: true, type, intensity: 0.5 + Math.random() * 0.5 };
    }
    
    return { triggered: false };
  }

  reset() {
    this.lastBackchannel = -10;
  }
}

export class DiscourseAnalyzer {
  constructor() {
    // 构建 Trie 加速匹配
    this.connectives = DISCOURSE_CONNECTIVES;
    this.maxLen = Math.max(...Object.keys(this.connectives).map(k => k.length));
  }

  /* 从文本中提取所有话语连接词及其位置 */
  extract(text) {
    const results = [];
    let pos = 0;
    while (pos < text.length) {
      let matched = null;
      let maxLen = 0;
      // 尝试最长匹配
      for (let len = Math.min(this.maxLen, text.length - pos); len >= 1; len--) {
        const substr = text.slice(pos, pos + len);
        if (this.connectives[substr]) {
          matched = substr;
          maxLen = len;
          break;
        }
      }
      if (matched) {
        results.push({
          connective: matched,
          start: pos,
          end: pos + matched.length,
          ...this.connectives[matched],
        });
        pos += maxLen;
      } else {
        pos++;
      }
    }
    return results;
  }

  /* 提取修辞结构标记 */
  extractRhetorical(text) {
    const results = [];
    for (const [marker, info] of Object.entries(RHETORICAL_MARKERS)) {
      const idx = text.indexOf(marker);
      if (idx >= 0) {
        results.push({ marker, start: idx, end: idx + marker.length, ...info });
      }
    }
    return results;
  }
}

/* ========== 语篇感知手势生成器 ========== */
export class DiscourseGestureGenerator {
  constructor() {
    this.discourseAnalyzer = new DiscourseAnalyzer();
    this.prosodyAnalyzer = new ProsodyAnalyzer();
    this.backchannelGenerator = new BackchannelGenerator();
    this.gestureQueue = []; // 待执行手势队列
    this.currentGesture = null;
    this.gestureStartTime = 0;
    this.pendingText = ""; // 流式文本缓冲
    this.prosodySyncEnabled = true; // 韵律-手势同步开关
  }

  /* 主入口：根据文本+音频生成手势队列 */
  generate(text, audioEnergy, ttsDuration) {
    // 1. 语篇分析
    const connectives = this.discourseAnalyzer.extract(text);
    const rhetorical = this.discourseAnalyzer.extractRhetorical(text);
    
    // 2. 韵律分析（简化：用能量峰值近似重音）
    // 实际应该传入音频特征
    
    // 3. 文本分段（按连接词/标点切分）
    const segments = this.segmentText(text, connectives);
    
    // 4. 每段生成手势
    const gestures = [];
    let timeCursor = 0;
    const segmentDuration = ttsDuration / Math.max(1, segments.length);
    
    for (const seg of segments) {
      const gesture = this.generateSegmentGesture(seg, segmentDuration);
      if (gesture) {
        gesture.startTime = timeCursor;
        gesture.endTime = timeCursor + segmentDuration;
        gestures.push(gesture);
        timeCursor += segmentDuration;
      }
    }
    
    return gestures;
  }

  /* 文本分段：按连接词/标点切分，保留连接词归属 */
  segmentText(text, connectives) {
    const segments = [];
    let lastEnd = 0;
    
    for (const conn of connectives) {
      if (conn.start > lastEnd) {
        segments.push({
          text: text.slice(lastEnd, conn.start),
          type: "content",
          connectives: [],
        });
      }
      segments.push({
        text: conn.connective,
        type: "connective",
        connectives: [conn],
      });
      lastEnd = conn.end;
    }
    if (lastEnd < text.length) {
      segments.push({
        text: text.slice(lastEnd),
        type: "content",
        connectives: [],
      });
    }
    return segments;
  }

  /* 为单个片段生成手势 */
  generateSegmentGesture(segment, duration) {
    if (!segment.text.trim()) return null;
    
    const tags = this.inferTags(segment);
    const primitives = this.retrievePrimitives(tags, segment.text.length);
    
    if (!primitives.length) return null;
    
    // 组合原语为手势
    return composeGesture(primitives, duration / 1000); // duration in ms
  }

  /* 推断语义标签 */
  inferTags(segment) {
    const tags = [];
    
    if (segment.type === "connective") {
      for (const conn of segment.connectives) {
        tags.push(...conn.tags);
      }
    } else {
      // 内容词推断：简单关键词匹配
      const text = segment.text;
      if (/不|没|无|非|别|别/.test(text)) tags.push("negation");
      if (/我|我们/.test(text)) tags.push("first_person");
      if (/你|您|你们/.test(text)) tags.push("second_person");
      if (/他|她|它|他们/.test(text)) tags.push("third_person");
      if (/想|要|希望|愿/.test(text)) tags.push("desire");
      if (/知|觉|觉得|感觉/.test(text)) tags.push("cognition");
      if (/好|棒|赞|喜欢|爱/.test(text)) tags.push("positive");
      if (/坏|差|糟|恨|讨厌/.test(text)) tags.push("negative");
      if (/大|小|多|少|高|低/.test(text)) tags.push("quantity");
      if (/快|慢|早|晚/.test(text)) tags.push("temporal");
    }
    return [...new Set(tags)]; // 去重
  }

  /* 原语检索 */
  retrievePrimitives(tags, textLength) {
    const needed = Math.max(1, Math.ceil(textLength / 8)); // 每 8 字符约 1 个原语
    const primitives = findPrimitivesByTags(tags);
    
    if (!primitives.length) {
      // 兜底：中性姿态
      return [{ part: "head", name: "neutral", duration: 1.0 }];
    }
    
    // 打乱并取前 N 个
    return primitives
      .sort(() => Math.random() - 0.5)
      .slice(0, needed);
  }

  /* 增量文本输入：流式 TTS 的文本增量送入 */
  feedTextDelta(delta) {
    if (!delta) return;
    this.pendingText = (this.pendingText || "") + delta;
    // 简单的句子边界检测：遇到句号/问号/感叹号/省略号就生成一个片段
    const sentences = this.pendingText.split(/([。！？…])/);
    for (let i = 0; i < sentences.length - 1; i += 2) {
      const segment = sentences[i] + (sentences[i + 1] || "");
      if (segment.trim()) {
        this.gestureQueue.push({ text: segment, type: "content" });
      }
    }
    this.pendingText = sentences[sentences.length - 1] || "";
  }

  /* 完整文本生成：TTS 完成后调用，带时长信息 */
  generate(text, audioEnergy, ttsDuration) {
    if (!text) return;
    // 重置队列
    this.gestureQueue = [];
    this.gestureStartTime = performance.now() / 1000;
    
    // 复用 generate 逻辑
    const gestures = this._generateInternal(text, ttsDuration);
    this.gestureQueue.push(...gestures);
    if (this.gestureQueue.length) {
      this.currentGesture = this.gestureQueue[0];
      this.gestureStartTime = performance.now() / 1000;
    }
  }

  /* 内部生成逻辑 */
  _generateInternal(text, ttsDuration) {
    // 1. 语篇分析
    const connectives = this.discourseAnalyzer.extract(text);
    const rhetorical = this.discourseAnalyzer.extractRhetorical(text);
    
    // 2. 文本分段
    const segments = this.segmentText(text, connectives);
    
    // 3. 每段生成手势
    const gestures = [];
    let timeCursor = 0;
    const segmentDuration = ttsDuration / Math.max(1, segments.length);
    
    for (const seg of segments) {
      const gesture = this.generateSegmentGesture(seg, segmentDuration);
      if (gesture) {
        gesture.startTime = timeCursor;
        gesture.endTime = timeCursor + segmentDuration;
        gestures.push(gesture);
        timeCursor += segmentDuration;
      }
    }
    return gestures;
  }

  /* 实时更新：用于流式 TTS */
  update(audioEnergy, dt, options = {}) {
    const { userSpeaking = false, avatarSpeaking = false } = options;
    
    // 更新韵律分析
    this.prosodyAnalyzer.push(audioEnergy);
    
    // 检测韵律突出点 -> 手势重音同步
    // 研究表明：手势笔划通常在重音音节前 100-200ms 启动，与重音峰值对齐
    const prom = this.prosodyAnalyzer.detectProminence();
    if (prom.isProminent && this.currentGesture && this.prosodySyncEnabled) {
      // 在重音点触发手势笔划峰值
      this.currentGesture.strokePeak = true;
      this.currentGesture.strokePeakStrength = prom.strength;
      this.currentGesture.strokePeakTime = performance.now() / 1000;
    }
    
    // 检测语调边界 -> 手势分句
    const boundary = this.prosodyAnalyzer.detectBoundary();
    if (boundary.isBoundary && this.gestureQueue.length) {
      // 推进到下一个手势
      this.advanceGesture();
    }
    
    // 处理手势队列推进
    this.processGestureQueue(dt);
  }
  
  /* 处理手势队列推进 */
  processGestureQueue(dt) {
    if (!this.currentGesture || this.gestureQueue.length === 0) return;
    
    const now = performance.now() / 1000;
    const elapsed = now - this.gestureStartTime;
    
    if (elapsed >= this.currentGesture.endTime - this.currentGesture.startTime) {
      // 当前手势结束，推进到下一个
      this.advanceGesture();
    }
  }
  
  /* 获取当前帧的 backchannel 手势（供 Avatar 每帧调用） */
  getBackchannelGesture(userSpeaking, audioEnergy, dt) {
    const prom = this.prosodyAnalyzer.detectProminence();
    const bc = this.backchannelGenerator.update(userSpeaking, audioEnergy, dt, prom);
    
    if (!bc.triggered) return null;
    
    // 根据类型返回原语
    const primitives = {
      nod: { part: "head", name: "nod", duration: 0.4, weight: bc.intensity },
      micro_nod: { part: "head", name: "nod", duration: 0.2, weight: bc.intensity * 0.5 },
      brow_flash: { part: "face", name: "brow_raise", duration: 0.3, weight: bc.intensity },
    };
    
    return primitives[bc.type] || primitives.nod;
  }

  advanceGesture() {
    if (this.gestureQueue.length > 1) {
      this.gestureQueue.shift();
      this.currentGesture = this.gestureQueue[0];
      this.gestureStartTime = performance.now() / 1000;
    }
  }
}

/* 默认导出 */
export default DiscourseGestureGenerator;
export { DISCOURSE_CONNECTIVES, RHETORICAL_MARKERS };