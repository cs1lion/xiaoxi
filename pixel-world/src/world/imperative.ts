/** 祈使判定：一句话是不是在**命令**世界。
 *
 * 三个解析器（派活 / 造物 / 神迹）都要回答同一个问题：这句话是在下命令，
 * 还是在聊天？早先每个文件各写一份前缀表与叙述标记，加第四条通道时会变成四份。
 *
 * 这里收敛成一份。各解析器只提供自己的**动词表**，其余判定共用。
 *
 * 判定分三步：
 *   1. 剥掉句首的位置短语（「在 (32,14) ...」）
 *   2. 剥掉礼貌与承接前缀（「帮我」「再」）
 *   3. 句首是给定动词，且不含叙述标记、不以叙述主语开头
 */

import type { GridPoint } from './types.ts';

/**
 * 礼貌与承接前缀。
 *
 * 承接副词（再 / 继续 / 接着）也算：「再整理一份」是在下命令，不是叙述。
 */
export const POLITE_PREFIXES: readonly string[] = [
  '帮我', '帮忙', '麻烦', '请', '给我', '替我',
  '再', '继续', '接着', '顺便', '另外',
];

/** 明确不是命令的标记：疑问、否定、过去叙述、评价收尾。 */
export const NON_IMPERATIVE_MARKERS: readonly string[] = [
  // 疑问
  '？', '?', '吗', '呢', '要不要', '能不能', '可不可以', '是不是',
  // 否定
  '别', '不用', '不要', '不必',
  // 过去/进行
  '昨天', '前天', '刚才', '之前', '已经', '一直', '老是', '总是',
  // 评价性收尾：写代码真累 / 查资料很烦
  '真累', '好累', '很累', '真烦', '好烦', '很烦', '有点累', '有点烦',
];

/**
 * 纯疑问标记。
 *
 * 与 `NON_IMPERATIVE_MARKERS` 分开：世界法则（下雨 / 放晴）只关心
 * "是不是在问"，不关心"是不是过去时"——「昨天已经下雨了」不该改天，
 * 但那是靠叙述标记拦的，不是靠疑问标记。
 */
export const QUESTION_MARKERS: readonly string[] = [
  '？', '?', '吗', '呢', '要不要', '能不能', '可不可以', '是不是',
];

/** 句首的叙述主语：出现它就不是命令。 */
export const NARRATIVE_SUBJECTS: readonly string[] = [
  '我', '他', '她', '它', '这', '那', '这个', '那个',
];

/** 这句话含叙述标记吗。 */
export function hasNarrativeMarker(text: string): boolean {
  return NON_IMPERATIVE_MARKERS.some((marker) => text.includes(marker));
}

/** 这句话是在提问吗。 */
export function isQuestion(text: string): boolean {
  return QUESTION_MARKERS.some((marker) => text.includes(marker));
}

/**
 * 这是一句命令吗。
 *
 * @param verbs 该解析器认的祈使动词（如「写 / 查」或「建 / 盖」）
 */
export function isImperative(text: string, verbs: readonly string[]): boolean {
  if (hasNarrativeMarker(text)) return false;

  const body = stripLeadIns(text);
  if (body.length === 0) return false;
  if (NARRATIVE_SUBJECTS.some((subject) => body.startsWith(subject))) return false;

  return verbs.some((verb) => body.startsWith(verb));
}

/**
 * 剥掉句首的成套开场白：位置短语与礼貌前缀可能以任意顺序叠加。
 *
 * 「在 (3,4) 帮我建」「帮我 在 (3,4) 建」都是命令，剥一轮不够——
 * 所以交替剥到不再变化。
 */
export function stripLeadIns(text: string): string {
  let body = text;
  for (let round = 0; round < 4; round += 1) {
    const next = stripPrefixes(stripLocation(body));
    if (next === body) break;
    body = next;
  }
  return body;
}

/**
 * 剥掉句首的位置短语：「在 (32,14)」「(32,14)」「32,14 那边」。
 *
 * 只剥句首那一处，不动句中的坐标——`readPoint` 仍从整句里找。
 */
export function stripLocation(text: string): string {
  return text
    .replace(/^\s*在?\s*\(?\d{1,3}\s*[,，]\s*\d{1,3}\)?\s*(那里|那边|这儿|这边)?\s*/, '')
    .replace(/^\s*在\s*/, '');
}

/** 剥掉礼貌与承接前缀。可能叠加（「麻烦帮我」），循环剥到不能再剥。 */
export function stripPrefixes(text: string): string {
  let body = text;
  let changed = true;
  while (changed) {
    changed = false;
    for (const prefix of POLITE_PREFIXES) {
      if (body.startsWith(prefix)) {
        body = body.slice(prefix.length).trim();
        changed = true;
      }
    }
  }
  return body;
}

/** 从句子里读坐标。认 `(32,14)`、`32,14`、全角括号与逗号。 */
export function readPoint(text: string): GridPoint | null {
  const matched = /(\d{1,3})\s*[,，]\s*(\d{1,3})/.exec(text);
  if (matched === null) return null;
  const x = Number(matched[1]);
  const y = Number(matched[2]);
  if (!Number.isInteger(x) || !Number.isInteger(y)) return null;
  return { x, y };
}
