/** 把一句神谕变成**造物意图**。
 *
 * 这是 L3 的第一版，而且刻意做小：不让模型猜你想要什么，只把你说的话
 * 翻译成 `{ templateId, at }`。模型的自由度为零——模板必须在注册表里，
 * 位置要么你说了，要么世界挑。
 *
 * 为什么不做「LLM 自由生成」：那需要两个不存在的输入（「这周该长什么样」
 * 「神谕说了什么」）。现在能做的、也真正有用的，是把「建一间书阁」这种
 * 明确的话接上已有的造物链路。
 *
 * 与 `oracle.ts` 的分工：那个认**派活 / 天气 / 交付**，这个认**造物**。
 * 两者都在祈使判定之下，不抢叙述句。
 */

import type { GridPoint, PrefabBlueprint } from './types.ts';
import {
  NARRATIVE_SUBJECTS,
  hasNarrativeMarker,
  readPoint,
  stripLeadIns,
} from './imperative.ts';

/** 建造动词。句首（或去掉礼貌前缀后）是这些词，才当造物。 */
const BUILD_VERBS = ['建', '盖', '造', '修', '开', '起'];
/** 拆除动词。与建造共用别名表，方向相反。 */
const DEMOLISH_VERBS = ['拆', '拆掉', '拆除', '推倒', '铲平'];

/**
 * 模板别名：用户嘴里的词 → prefab id。
 *
 * 只收**明确指向某一种建筑**的词。像「工坊」这种既可能是新工坊也可能是
 * 已有的炼码工坊，指 workshop_plot（扩建样板）——那是唯一"可新建"的。
 */
const TEMPLATE_ALIASES: Readonly<Record<string, string>> = {
  书阁: 'star_archive',
  星图书阁: 'star_archive',
  图书馆: 'star_archive',
  抄书斋: 'ink_house',
  书斋: 'ink_house',
  工坊: 'workshop_plot',
  新工坊: 'workshop_plot',
  矿洞: 'crawler_mine',
  爬虫矿洞: 'crawler_mine',
  磨坊: 'ink_mill',
  文档磨坊: 'ink_mill',
  齿轮塔: 'gear_tower',
  炼码工坊: 'code_forge',
};

export interface BuildIntent {
  /** build 或 demolish：同一个模板，方向相反。 */
  readonly action: 'build' | 'demolish';
  readonly templateId: string;
  /** null 表示"由世界挑地方"。拆的时候无意义。 */
  readonly at: GridPoint | null;
}

/**
 * 解析一句造物神谕。
 *
 * 返回 null 表示"这不是造物"——调用方应当让别的解析器去试，而不是当成失败。
 */
export function parseOracleBuild(
  raw: string,
  templates: ReadonlyMap<string, PrefabBlueprint>,
): BuildIntent | null {
  const text = raw.trim();
  if (text.length === 0) return null;
  const lower = text.toLowerCase();

  if (hasNarrativeMarker(lower)) return null;

  // 位置常写在动词前面（「在 (32,14) 建一间书阁」）。先把位置短语剥掉，
  // 否则句首是「在」，「建」就不在开头了。
  const body = stripLeadIns(lower);
  if (NARRATIVE_SUBJECTS.some((subject) => body.startsWith(subject))) return null;

  // 句首必须是建造或拆除动词。拆优先：`拆掉` 也以 `拆` 开头，
  // 但两个表不重叠，顺序只影响可读性。
  let action: 'build' | 'demolish' | null = null;
  if (DEMOLISH_VERBS.some((verb) => body.startsWith(verb))) action = 'demolish';
  else if (BUILD_VERBS.some((verb) => body.startsWith(verb))) action = 'build';
  if (action === null) return null;

  // 找一个能认的模板。长的别名优先：「爬虫矿洞」不该被「矿洞」抢。
  const aliases = Object.keys(TEMPLATE_ALIASES).sort((a, b) => b.length - a.length);
  for (const alias of aliases) {
    if (!lower.includes(alias)) continue;
    const templateId = TEMPLATE_ALIASES[alias]!;
    if (!templates.has(templateId)) continue;
    return { action, templateId, at: action === 'build' ? readPoint(lower) : null };
  }
  return null;
}
