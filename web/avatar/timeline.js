/* 分句文本 -> 带时间轴的口型序列。
 *
 * 关键点：我们知道这句音频的真实时长（decodeAudioData 之后的 buffer.duration），
 * 而口型的相对权重来自文本。两者相除就把整句摊平到真实时间上，
 * 所以不需要 TTS 提供任何音素时间戳。
 *
 * 这是 text-driven 路线的核心，也是比纯音频 MFCC 分类更适合中文的地方：
 * 中文的口型本来就是由已知字形决定的，没必要再从波形里猜。
 *
 * ---------- 共发音（coarticulation）----------
 *
 * 简单地"一个音素一个口型，段内保持，段间线性插值"是不够的，因为真实
 * 发音里**嘴的动作比声音早**。经典例子是 school：/k/ 甚至 /s/ 还在发，
 * 嘴唇已经开始为后面的 /u/ 收圆了（pre-rounding）。
 * 声学特征与视觉特征的最大相关点平均落在 120ms 的过去——
 * 也就是说，人眼看到的口型对应的是"已经过去一会儿的声音"。
 *
 * 硬切或线性插值的做法完全没有这个提前量，于是整段对话虽然对得上嘴，
 * 却总差一口气：嘴在"追"声音，而不是在"预告"它。
 *
 * 正确的模型是 Cohen-Massaro 的 dominance function：每个音素带一个目标
 * 口型和一条随时间变化的影响力曲线，多条曲线重叠后按权重混合。
 *
 *   influence_i(t) = 前段（anticipation，向目标平滑上升）
 *                  + 平台（本音素区间内为 1）
 *                  + 后段（retention，向外平滑衰减）
 *
 * 本实现用升余弦而不是指数：余弦两端一阶导为零，混合出来的口型不会在
 * 音素边界留下速度跳变；指数尾巴会在边界产生折角，密集音节下看着像抖。
 *
 * 三个必须显式建模的点，缺一个就会读成"机器人念稿"：
 *
 * 1) 提前量不能太大。lead 取本段时长的比例而不是固定毫秒——
 *    固定毫秒在快速连读时会让相邻音素的窗口大面积重叠到失控。
 *
 * 2) 抗混合（rubber）。不同音素对共发音的"让步程度"不同：
 *    元音核是橡皮筋，会把嘴一路拉过去（a-i-au-i 一路顺滑）；
 *    双唇闭合 b/p/m 则几乎不让步，必须真的闭死再弹开。
 *    一视同仁地混合会把 ba-ba-ba 拉成一串半开的糊口型——
 *    这正是"只做 dominance 不做 resistance"被反复批评的那个问题。
 *
 * 3) 混合之后要按闭合强度把 open 压回去。加权平均在数学上永远到不了 0，
 *    唇会一直留一条缝；而真人发 b/p/m 时唇是真的合上的。
 *    所以先算出混合结果，再用闭合音素的 dominance 强度把它拉向 0。
 */

import { segmentsFor } from "./viseme.js";
import { clamp01 } from "./params.js";

/* edge-tts 开头/结尾通常各有一小段静音，别让嘴在静音里张开。 */
const HEAD_TRIM = 0.06;
const TAIL_TRIM = 0.05;

/* 短于这个时长的口型段直接丢弃。
 * 注意不能靠"跳过"来防止抽搐——那是旧做法，用硬边界掩盖问题。
 * 现在有了 dominance 重叠和 rubber，共发音本来就该把高频音素化开；
 * 这里只挡住极端情况（比如 durationSec 被估错导致单段被压到 5ms）。 */
const MIN_SEGMENT = 0.012;

/* 共发音参数。这些是"可调的表演风格"，不是物理常数：
 * 放慢一点更像在认真说话，调快一点更像在随口接话。 */
export const COARTICULATION = {
  /* 提前量：口型在自己音素开始前多久就开始成形（相对本段时长的倍数），
   * 但同时有绝对上限。绝对上限不是摆设：
   * 一个 365ms 的 "a" 若按比例提前 153ms，它会在前面 80ms 的 b/p/m
   * 还没闭完时就把嘴拉开了——于是 ba 的双唇永远合不拢。
   * 真人提前共发音大约在 50~120ms，取 90ms。 */
  lead: 0.42,
  lead_cap: 0.090,
  /* 保持量：音素结束后影响力还留多久 */
  trail: 0.34,
  trail_cap: 0.070,
  /* 闭合音素对混合的抵抗力。
   * 0.98 而非 1.0：b/p/m 的双唇要真的合上（否则看起来像弱化音），
   * 但留一点点弹性——完全不让步会让闭合变成一个生硬的开关，
   * 反而读成机械音。 */
  resistance: 0.98,
  /* 闭合时把 open 压向 0 的强度上限 */
  closure_lock: 1,
};

/* 静止口型。不参与任何音素，只用来给整句的首尾一个"起点"和"终点"。
 *
 * 没有它的话，共发音在句子开头是看不见的：第一个音素前面没有邻居可以混合，
 * 权重平均的结果就等于它自己的形状，于是嘴在 t0 那一刻"啪"地到位——
 * 而真实发音里嘴是从静止位慢慢过去的。句尾同理，说完最后一个字嘴要回到静止。
 *
 * 显式补一个 rest 段，比在 sampleTimeline 里特判边界要干净：
 * 特判只能让边界变平滑，却造不出"从静止位起动"这个真实存在的动作。
 */
const REST_SHAPE = { open: 0.06, wide: 0.50, round: 0.50 };

/* 静止段必须自带上升/下降沿，不能只给一个 [t0,t1] 方块。
 * 方块段的 influence 会在 a0 处从 0 直接跳到 1，那一下就是断点；
 * 而真实发音里嘴是从静止位"渐变"过去的。
 *
 * 所以头部静止段让它的**下降沿**压在第一个音素的上升沿上：
 *   静止 ──╲
 *          ╲ 第一个音素
 *           ╱
 * 两段 influence 重叠混合，嘴就自然地从静止起动。
 * 反过来头部不给上升沿是有意的：起动动作发生在说话之前，
 * 而说话之前嘴本来就该是静止的，没有东西需要"过渡过来"。
 */
function restSegment(a0, t0, t1, a1) {
  return {
    t0, t1, a0, a1,
    open: REST_SHAPE.open, wide: REST_SHAPE.wide, round: REST_SHAPE.round,
    rubber: 1, closure: false, rest: true,
  };
}

export function buildTimeline(text, durationSec, opts = {}) {
  const co = { ...COARTICULATION, ...(opts.coarticulation || {}) };
  const spans = [];
  let total = 0;

  for (const char of text || "") {
    const { parts, weight, pause } = segmentsFor(char);
    if (!parts.length && !pause) continue;
    const scale = pause || 1;
    for (const part of parts) {
      const w = part.weight * weight * scale;
      spans.push({
        shape: part.shape,
        weight: w,
        closure: !!part.closure,
        rubber: part.rubber === undefined ? 1 : part.rubber,
      });
      total += w;
    }
  }

  const duration = Math.max(0.12, durationSec || 0.12);
  const tail = {
    duration,
    lead: co.lead, trail: co.trail, resistance: co.resistance,
    closureLock: co.closure_lock, leadCap: co.lead_cap, trailCap: co.trail_cap,
  };
  if (!spans.length || total <= 0) {
    // 整句没有可发音的内容（纯标点/空白）：嘴全程静止。
    return { segments: [restSegment(0, 0, duration, duration)], ...tail };
  }

  const usable = duration * (1 - HEAD_TRIM - TAIL_TRIM);
  const unit = usable / total;
  const segments = [];
  let t = duration * HEAD_TRIM;

  for (const span of spans) {
    const dt = span.weight * unit;
    const prev = segments[segments.length - 1];
    if (dt < MIN_SEGMENT) {
      // 太短的段不新开一个音节，而是并进上一段。
      // 直接丢弃会让这一小段时间的权重凭空消失，
      // 后面所有音节的时间起点跟着往前挪，整句就对不上音频了。
      if (prev) prev.t1 += dt;
      else segments.push({ t0: t, t1: t + dt, ...shapeOf(span), closure: span.closure });
      t += dt;
      continue;
    }
    segments.push({ t0: t, t1: t + dt, ...shapeOf(span), closure: span.closure });
    t += dt;
  }

  if (!segments.length) {
    return { segments: [restSegment(0, 0, duration, duration)], ...tail };
  }

  // 把每段的提前/保持窗口算好，采样时就不用反复乘
  for (const s of segments) {
    const dt = s.t1 - s.t0;
    // 比例与绝对上限取小的那个。缺了绝对上限，长元音的提前量
    // 会反过来把前面短促的闭合音素顶开，双唇就永远合不上了。
    s.a0 = s.t0 - Math.min(dt * co.lead, co.lead_cap);
    s.a1 = s.t1 + Math.min(dt * co.trail, co.trail_cap);
  }

  /* 补首尾静止段。
   * 头部/尾部静音（HEAD_TRIM/TAIL_TRIM）本来就是嘴静止的时间，
   * 直接把它们让给 rest 段正好一举两得：首尾静音不再浪费，
   * 而且句子开头/结尾的过渡有了真实的起点和终点。
   *
   * 首段 rest 的起点允许是负的（提前量可能越过 0）：
   * 采样只在 [0, duration] 内进行，负时间只是让 influence 曲线
   * 在 t=0 处已经爬到一半，起动动作因此是"已经在进行"而不是
   * "从这一刻才开始"，后者会读成卡了一下。 */
  const first = segments[0];
  const last = segments[segments.length - 1];
  /* 头部：下降沿 [first.a0, first.a0 + trail_cap] 与首音素的上升沿重叠，
   * 嘴就自然地从静止起动。起动动作本身发生在说话之前，所以头部不给上升沿，
   * 那一段 a0 <= 0 落在采样范围之外（a0 取 min(0, ...) 保证这一点），
   * 不会在采样时产生阶跃。 */
  segments.unshift(restSegment(
    Math.min(0, first.a0 - co.lead_cap),
    first.a0 - co.lead_cap,
    first.a0,
    first.a0 + co.trail_cap
  ));
  /* 尾部：上升沿 [last.a1 - trail_cap, last.a1] 与末音素的下降沿重叠，
   * 说完之后嘴才收得回去。
   *
   * 这里的 a0/t0 必须分开。早先写成 a0 == t0（方块段），influence 会在
   * a0 处从 0 硬跳到 1 —— 那正是一个断点，而且它落在时间轴末尾，
   * 表现为"说完话嘴突然一抖"。
   * 采样范围收到 duration：时间轴里出现超出时长的段没有意义。 */
  const tailA0 = last.a1 - co.trail_cap;
  const tailT0 = Math.min(last.a1, duration);
  segments.push(restSegment(tailA0, tailT0, duration, duration));

  return { segments, ...tail };
}

function shapeOf(span) {
  return {
    open: span.shape.open,
    wide: span.shape.wide,
    round: span.shape.round,
    rubber: span.rubber,
  };
}

/* 对外统一的采样结果形状。刻意只给这三个字段——
 * 调用方只该看到"口型长什么样"，不该看到"这是哪个音素"。 */
function frameOf(s) {
  return { open: s.open, wide: s.wide, round: s.round };
}

/* 单条 dominance 曲线。升余弦，三段：上升 / 平台 / 下降。
 * 两端一阶导为零，所以和邻居相加时边界不会出现速度折角。 */
function influence(t, s) {
  if (t <= s.a0 || t >= s.a1) return 0;
  if (t < s.t0) {
    const k = (t - s.a0) / (s.t0 - s.a0);
    return 0.5 * (1 - Math.cos(Math.PI * k));
  }
  if (t <= s.t1) return 1;
  const k = (t - s.t1) / (s.a1 - s.t1);
  return 0.5 * (1 + Math.cos(Math.PI * k));
}

/* 在按 key 升序排列的段数组里，找第一个 key > x 的下标。
 * 段是按时间先后建的，t0/t1 都严格递增，所以可以直接二分。 */
function lowerBound(segs, x, key) {
  let lo = 0, hi = segs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid][key] > x) hi = mid; else lo = mid + 1;
  }
  return lo;
}

/* 在 t 时刻采样：把窗口内所有音素按 dominance 加权混合。
 *
 * 这是"共振峰/口型混合"而不是"查表取值"，也是共发音唯一正确的做法：
 * 嘴在任意时刻都同时受好几个音素影响，只是权重不同。
 *
 * 复杂度：正常情况下窗口内只有 2~4 段（lead/trail 决定），
 * 所以即使一句话有上百段，代价也只是常数级。 */
export function sampleTimeline(timeline, t) {
  const segs = timeline.segments;
  if (!segs.length) return null;

  const first = segs[0];
  const last = segs[segs.length - 1];
  // 越界时返回首/尾段的"静止形状"，而不是把段对象本身漏出去。
  // 段对象上带着 a0/a1/t0/closure 等内部字段，调用方（LipDriver）
  // 只认 open/wide/round，漏出去等于给了一个形状不确定的契约。
  if (t <= first.a0) return frameOf(first);
  if (t >= last.a1) return frameOf(last);

  /* 定位可能有影响的区间。
   *
   * 这里必须二分 t0/t1，不能二分 a0/a1——a0 = t0 - lead 而 lead 有绝对
   * 上限，于是短音素的 lead 更小、a0 更靠近 t0，a0 数组并不单调。
   * 在不单调的数组上二分找"最后一个 a0 <= t"，会漏掉正在提前成形的段，
   * 那段正是共发音的来源，漏掉它等于把提前量又抹掉了。
   *
   * t0 和 t1 天然有序，而 a0 >= t0 - lead_cap、a1 <= t1 + trail_cap，
   * 所以可以先在有序数组上切出"肯定还活着"和"肯定还没起势"的粗窗口：
   *   t1 <= t - trail_cap  ->  influence 必为 0（连保持窗口都出不去）
   *   t0 >  t + lead_cap   ->  influence 必为 0（连提前窗口都进不去）
   * 粗窗口之外一律不参与，也就不需要 a0/a1 单调了。
   */
  const leadCap = timeline.leadCap ?? COARTICULATION.lead_cap;
  const trailCap = timeline.trailCap ?? COARTICULATION.trail_cap;
  const start = lowerBound(segs, t - trailCap, "t1");
  const end = lowerBound(segs, t + leadCap, "t0") - 1;

  let wOpen = 0, wWide = 0, wRound = 0, wSum = 0;
  let closureBest = 0, peak = 0;

  for (let i = Math.max(0, start); i <= end && i < segs.length; i++) {
    const s = segs[i];
    const d = influence(t, s);
    if (d <= 0) continue;
    wOpen += s.open * d;
    wWide += s.wide * d;
    wRound += s.round * d;
    wSum += d;
    if (d > peak) peak = d;
    /* 取"当前最占主导的闭合音素"，而不是所有闭合段权重之和。
     * 求和会让连着好几个 b 的句子（爸爸爸爸）把强度堆到远超 1，
     * 于是在两个 b 之间的元音区也被死死压住——嘴张不开，
     * 整串听起来像含在嘴里说话。 */
    if (s.closure && d > closureBest) closureBest = d;
  }

  if (wSum <= 1e-9) {
    // 窗口理论上不该空：只要 t 在 [first.a0, last.a1] 内就至少一段有影响。
    // 真空了就退回离 t 最近的一段，避免除零。
    let nearest = 0, bestDist = Infinity;
    for (let i = 0; i < segs.length; i++) {
      const d = Math.abs((segs[i].t0 + segs[i].t1) / 2 - t);
      if (d < bestDist) { bestDist = d; nearest = i; }
    }
    return frameOf(segs[nearest]);
  }

  let open = wOpen / wSum;
  const wide = wWide / wSum;
  const round = wRound / wSum;

  /* 抗混合：闭合音素在自己区间内把 open 压向 0。
   *
   * 加权平均永远到不了 0（除非所有参与段都是闭合），
   * 而唇上留一条缝的 "b" 看起来不像 b，只像弱化音。
   *
   * 强度用"闭合音素的主导度 / 全局峰值主导度"：
   * 刚进入闭合区间时它还没到峰值，只轻微收；
   * 真的闭上时它就是峰值，唇完全合拢。
   * 用这个比值而不是绝对权重，才能让 ba-ba-ba 里的 a 重新张得开。 */
  if (closureBest > 0 && peak > 0 && timeline.resistance > 0) {
    const strength = clamp01((closureBest / peak) * timeline.resistance);
    open = open * (1 - strength * timeline.closureLock);
  }

  return { open, wide, round };
}
