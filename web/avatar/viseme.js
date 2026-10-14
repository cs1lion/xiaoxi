/* 音素 -> 口型。
 *
 * 两级结构：
 *   汉字 --(pinyin_table.js)--> [声母类, 韵母索引]
 *   声母类 --(INITIAL_SHAPES)--> 口型
 *   韵母   --(FINALS)--> 口型序列（含鼻韵尾闭合）
 *
 * 口型用 {open, wide, round} 三维描述，和 Amoner/lipsync-engine 的
 * SVGMouthRenderer 同一组参数：没有贴图也能画出口型。
 */

import { TABLE, INITIAL_CLASS_COUNT, FINAL_COUNT } from "./pinyin_table.js";

const ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";

/* 与 scripts/gen_pinyin_table.py 的 INITIAL_CLASSES 同序，改动要同步。 */
const INITIAL_NAMES = ["SIL", "PP", "FF", "DD", "KK", "SS", "NN", "CH"];

/* 闭合类音素：b/p/m 的双唇闭合、鼻韵尾的收束。
 *
 * 单独标出来不是为了画图（画图只需要 open/wide/round），而是因为
 * 共发音混合有个物理上的死角：多个音素的 dominance 加权平均，永远
 * 达不到"完全闭合"——唇始终留一条缝。听起来和看起来都不像真的 b/p/m。
 * 所以这类音素要额外加一个抗混合项，见 timeline.js 的 resistance。
 * 音素学知识留在本文件，不让 timeline 去猜"open 小于多少算闭合"。 */
export const CLOSURE_INITIALS = new Set(["PP"]);
export const CLOSURE_NUCLEUS = new Set(["SIL"]);

export const INITIAL_SHAPES = {
  SIL: { open: 0.02, wide: 0.50, round: 0.50 },  // 零声母，直接进韵母
  PP:  { open: 0.00, wide: 0.52, round: 0.55 },  // b p m 双唇闭合
  FF:  { open: 0.16, wide: 0.48, round: 0.28 },  // f 唇齿
  DD:  { open: 0.24, wide: 0.62, round: 0.30 },  // d t n l 舌尖
  KK:  { open: 0.36, wide: 0.50, round: 0.46 },  // g k h 舌根
  SS:  { open: 0.13, wide: 0.78, round: 0.16 },  // z c s 齿龈擦音
  NN:  { open: 0.26, wide: 0.44, round: 0.66 },  // zh ch sh r 舌尖后
  CH:  { open: 0.19, wide: 0.82, round: 0.10 },  // j q x 舌面尖，咧口露齿
};

/* 元音核口型（韵母的主体段）。 */
export const NUCLEUS_SHAPES = {
  A:  { open: 0.95, wide: 0.55, round: 0.20 },
  O:  { open: 0.70, wide: 0.34, round: 0.78 },
  E:  { open: 0.50, wide: 0.74, round: 0.18 },
  I:  { open: 0.33, wide: 0.88, round: 0.06 },
  U:  { open: 0.26, wide: 0.20, round: 0.98 },
  FR: { open: 0.38, wide: 0.54, round: 0.48 },  // ü
  MUR: { open: 0.14, wide: 0.52, round: 0.55 },  // 零韵母（嗯/呣），哼音
  SIL: { open: 0.02, wide: 0.50, round: 0.50 },  // 鼻韵尾闭合
};

/* 韵母展开表，顺序/内容必须和 gen_pinyin_table.py 的 FINAL_LIST 一致。
 * 每项 [口型, 相对时长, 是否鼻韵尾闭合]。 */
const FINAL_LIST = [
  "", "a", "ai", "an", "ang", "ao", "e", "ei", "en", "eng", "er",
  "i", "ia", "ian", "iang", "iao", "ie", "in", "ing", "iong", "iou",
  "o", "ong", "ou", "u", "ua", "uai", "uan", "uang", "uei", "uen",
  "ueng", "uo", "v", "van", "ve", "vn",
];

const F = {
  "":    [],
  "a":   [["A", 1]],
  "ai":  [["A", 0.55], ["E", 0.45]],
  "an":  [["A", 0.68], ["SIL", 0.32, 1]],
  "ang": [["A", 0.68], ["SIL", 0.32, 1]],
  "ao":  [["A", 0.45], ["O", 0.55]],
  "e":   [["E", 1]],
  "ei":  [["E", 0.50], ["I", 0.50]],
  "en":  [["E", 0.68], ["SIL", 0.32, 1]],
  "eng": [["E", 0.68], ["SIL", 0.32, 1]],
  "er":  [["E", 0.85], ["U", 0.15]],
  "i":   [["I", 1]],
  "ia":  [["I", 0.40], ["A", 0.60]],
  "ian": [["I", 0.45], ["E", 0.25], ["SIL", 0.30, 1]],
  "iang":[["I", 0.40], ["A", 0.30], ["SIL", 0.30, 1]],
  "iao": [["I", 0.30], ["A", 0.40], ["O", 0.30]],
  "ie":  [["I", 0.40], ["E", 0.60]],
  "in":  [["I", 0.68], ["SIL", 0.32, 1]],
  "ing": [["I", 0.68], ["SIL", 0.32, 1]],
  "iong":[["I", 0.35], ["O", 0.35], ["SIL", 0.30, 1]],
  "iou": [["I", 0.35], ["O", 0.35], ["U", 0.30]],
  "o":   [["O", 1]],
  "ong": [["O", 0.68], ["SIL", 0.32, 1]],
  "ou":  [["O", 0.50], ["U", 0.50]],
  "u":   [["U", 1]],
  "ua":  [["U", 0.42], ["A", 0.58]],
  "uai": [["U", 0.32], ["A", 0.38], ["E", 0.30]],
  "uan": [["U", 0.35], ["A", 0.35], ["SIL", 0.30, 1]],
  "uang":[["U", 0.35], ["A", 0.35], ["SIL", 0.30, 1]],
  "uei": [["U", 0.30], ["E", 0.40], ["I", 0.30]],
  "uen": [["U", 0.35], ["E", 0.35], ["SIL", 0.30, 1]],
  "ueng":[["U", 0.35], ["E", 0.35], ["SIL", 0.30, 1]],
  "uo":  [["U", 0.45], ["O", 0.55]],
  "v":   [["FR", 1]],
  "van": [["FR", 0.40], ["A", 0.30], ["SIL", 0.30, 1]],
  "ve":  [["FR", 0.40], ["E", 0.60]],
  "vn":  [["FR", 0.68], ["SIL", 0.32, 1]],
};

/* 英文/数字的粗略口型。中文不走到这里，但角色偶尔会蹦个英文单词。 */
const LATIN = {
  a: ["A"], e: ["E"], i: ["I"], o: ["O"], u: ["U"], y: ["I"],
  b: ["PP"], p: ["PP"], m: ["PP"],
  f: ["FF"], v: ["FF"],
  d: ["DD"], t: ["DD"], n: ["DD"], l: ["DD"],
  g: ["KK"], k: ["KK"], h: ["KK"],
  z: ["SS"], c: ["SS"], s: ["SS"],
  j: ["CH"], q: ["CH"], x: ["CH"],
  w: ["U"], r: ["NN"],
};

const CJK = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/;
const PUNCT_PAUSE = "，。！？、；：…—～,.!?;:~";
const LONG_PAUSE = "。！？!?";

/* 解 3 码元表：汉字 + 声母类(base62) + 韵母索引(base62)。 */
const SYLLABLE = new Map();
for (let i = 0; i < TABLE.length; i += 3) {
  const initial = INITIAL_NAMES[ALPHABET.indexOf(TABLE[i + 1])];
  const final = FINAL_LIST[ALPHABET.indexOf(TABLE[i + 2])];
  if (initial && final !== undefined) {
    SYLLABLE.set(TABLE[i], [initial, final]);
  }
}

if (INITIAL_CLASS_COUNT !== INITIAL_NAMES.length) {
  throw new Error(
    `viseme.js 的 INITIAL_NAMES(${INITIAL_NAMES.length}) 与 pinyin_table.js 的 ` +
    `INITIAL_CLASS_COUNT(${INITIAL_CLASS_COUNT}) 不一致，请重新运行 gen_pinyin_table.py`
  );
}
if (FINAL_COUNT !== FINAL_LIST.length) {
  throw new Error(
    `viseme.js 的 FINAL_LIST(${FINAL_LIST.length}) 与 pinyin_table.js 的 ` +
    `FINAL_COUNT(${FINAL_COUNT}) 不一致，请重新运行 gen_pinyin_table.py`
  );
}
for (const final of FINAL_LIST) {
  if (final !== "" && !F[final]) throw new Error(`FINAL_LIST 里的 "${final}" 没有展开定义`);
}

export function lookup(char) {
  return SYLLABLE.get(char);
}

/* 单个音节展开成口型段。权重是相对值，由 timeline.js 归一化到实际时长。
 *
 * parts 里的每一项：
 *   shape    口型目标
 *   weight   相对时长
 *   closure  是否是"必须真的闭到零"的音素（双唇闭合/鼻韵尾收束）
 *   rubber   共发音弹性。母音核会把嘴拉向自己的形状（跟着走），
 *            闭塞音则几乎不参与形变（抗混合）。这个区分是共发音
 *            能读对的关键：a-i-au-i 这种连读里嘴是一路滑过去的，
 *            而 ba-ba-ba 里每个 b 都要真的闭死再弹开。
 */
export function segmentsFor(char) {
  if (CJK.test(char)) {
    const hit = SYLLABLE.get(char);
    // 未收录的字走中性张口：中文里 -a 类韵母本来就是最大占比
    if (!hit) return { parts: [{ shape: NUCLEUS_SHAPES.A, weight: 1, rubber: 1 }], weight: 1 };
    const [initial, final] = hit;
    const finals = F[final] || [];
    if (!finals.length) {
      // 零韵母（嗯/呣 之类的成音节），嘴基本不动
      return { parts: [{ shape: NUCLEUS_SHAPES.MUR, weight: 1, rubber: 1 }], weight: 1 };
    }
    const parts = [];
    if (initial !== "SIL") {
      parts.push({
        shape: INITIAL_SHAPES[initial],
        weight: 0.18,
        closure: CLOSURE_INITIALS.has(initial),
        // 声母的形变时间短，不该把嘴长时间拽在自己身上
        rubber: 0.35,
      });
    }
    const rest = initial === "SIL" ? 1 : 0.82;
    for (const [name, w] of finals) {
      const closure = CLOSURE_NUCLEUS.has(name);
      parts.push({
        shape: NUCLEUS_SHAPES[name],
        weight: w * rest,
        closure,
        // 鼻韵尾收束要真的闭上，不能被前面的元音拉成半开
        rubber: closure ? 0.15 : 1,
      });
    }
    return { parts, weight: 1 };
  }

  const ch = char.toLowerCase();
  if (LATIN[ch]) {
    const name = LATIN[ch];
    const shape = NUCLEUS_SHAPES[name] || INITIAL_SHAPES[name] || INITIAL_SHAPES.DD;
    const closure = CLOSURE_INITIALS.has(name);
    return {
      parts: [{ shape, weight: 1, closure, rubber: closure ? 0.35 : 1 }],
      weight: 0.6,
    };
  }
  if (PUNCT_PAUSE.includes(ch)) {
    return {
      parts: [{ shape: NUCLEUS_SHAPES.SIL, weight: 1, closure: true, rubber: 0.2 }],
      weight: 0.55,
      pause: LONG_PAUSE.includes(ch) ? 1.8 : 1,
    };
  }
  if (/\s/.test(char)) {
    return { parts: [], weight: 0.28, pause: 1 };
  }
  return { parts: [], weight: 0.35 };
}