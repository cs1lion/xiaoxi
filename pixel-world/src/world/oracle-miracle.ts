/** 把一句神谕变成**神迹意图**：「点灯」「种树」「召唤猫」，可带坐标。
 *
 * 与 `oracle-build.ts` 的分工：那个认「建 / 盖 / 造 / 修 / 开 / 起」（造楼房），
 * 这个认「点 / 种 / 召唤」（在世界里放小东西）。两张动词表不重叠。
 *
 * 与 `oracle.ts` 的分工：那个认派活 / 天气 / 交付。三张表互不抢词。
 *
 * 只认祈使句：叙述与疑问不触发。与另外两个解析器同一套判定。
 */

import type { GridPoint } from './types.ts';
import {
  NARRATIVE_SUBJECTS,
  hasNarrativeMarker,
  readPoint,
  stripLeadIns,
} from './imperative.ts';

export type MiracleKind = 'light' | 'tree' | 'cat';

export interface MiracleIntent {
  readonly miracle: MiracleKind;
  /** null 表示"由世界挑地方"。 */
  readonly at: GridPoint | null;
}

/** 每个神迹的触发词。长的优先匹配。 */
const MIRACLE_WORDS: readonly { readonly kind: MiracleKind; readonly words: readonly string[] }[] = [
  { kind: 'light', words: ['点灯', '点一盏灯', '亮灯', '开灯'] },
  { kind: 'tree', words: ['种树', '种一棵树', '栽树'] },
  { kind: 'cat', words: ['召唤猫', '来只猫', '叫只猫', '召唤一只猫'] },
];

/** 神迹的祈使动词：句首是这些词才算命令。 */
const MIRACLE_VERBS = ['点', '亮', '开', '种', '栽', '召唤', '来', '叫'];

export function parseOracleMiracle(raw: string): MiracleIntent | null {
  const text = raw.trim();
  if (text.length === 0) return null;
  const lower = text.toLowerCase();

  if (hasNarrativeMarker(lower)) return null;

  // 位置常写在动词前面（「在 (40,45) 种一棵树」），先剥掉。
  const body = stripLeadIns(lower);
  if (NARRATIVE_SUBJECTS.some((subject) => body.startsWith(subject))) return null;
  if (!MIRACLE_VERBS.some((verb) => body.startsWith(verb))) return null;

  // 长的词优先：「召唤一只猫」不该被「召唤猫」以外的词抢，也不该被「猫」单独匹配。
  const sorted = [...MIRACLE_WORDS].sort((a, b) => {
    const longestA = Math.max(...a.words.map((w) => w.length));
    const longestB = Math.max(...b.words.map((w) => w.length));
    return longestB - longestA;
  });
  for (const rule of sorted) {
    if (rule.words.some((word) => lower.includes(word))) {
      return { miracle: rule.kind, at: readPoint(lower) };
    }
  }
  return null;
}
