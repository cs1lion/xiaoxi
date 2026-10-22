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

/** 建造动词。句首（或去掉礼貌前缀后）是这些词，才当造物。 */
const BUILD_VERBS = ['建', '盖', '造', '修', '开', '起'];

/** 礼貌与承接前缀，与 oracle.ts 同口径。 */
const PREFIXES = [
  '帮我', '帮忙', '麻烦', '请', '给我', '替我',
  '再', '继续', '接着', '顺便', '另外',
];

/** 明显不是命令的标记。 */
const NON_IMPERATIVE = [
  '？', '?', '吗', '呢', '要不要', '能不能', '可不可以', '是不是',
  '别', '不用', '不要', '不必',
  '昨天', '前天', '刚才', '之前', '已经', '一直', '老是', '总是',
];

const NARRATIVE_SUBJECTS = ['我', '他', '她', '它', '这', '那', '这个', '那个'];

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
  readonly templateId: string;
  /** null 表示"由世界挑地方"。 */
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

  if (NON_IMPERATIVE.some((marker) => lower.includes(marker))) return null;

  // 位置常写在动词前面（「在 (32,14) 建一间书阁」）。先把位置短语剥掉，
  // 否则句首是「在」，「建」就不在开头了。
  const body = stripPrefixes(stripLocation(lower));
  if (NARRATIVE_SUBJECTS.some((subject) => body.startsWith(subject))) return null;

  // 句首必须是建造动词。
  if (!BUILD_VERBS.some((verb) => body.startsWith(verb))) return null;

  // 找一个能认的模板。长的别名优先：「爬虫矿洞」不该被「矿洞」抢。
  const aliases = Object.keys(TEMPLATE_ALIASES).sort((a, b) => b.length - a.length);
  for (const alias of aliases) {
    if (!lower.includes(alias)) continue;
    const templateId = TEMPLATE_ALIASES[alias]!;
    if (!templates.has(templateId)) continue;
    return { templateId, at: readPoint(lower) };
  }
  return null;
}

/** 从句子里读坐标。认 `(32,14)` 与 `32,14` 两种写法。 */
function readPoint(lower: string): GridPoint | null {
  const matched = /(\d{1,3})\s*[,，]\s*(\d{1,3})/.exec(lower);
  if (matched === null) return null;
  const x = Number(matched[1]);
  const y = Number(matched[2]);
  if (!Number.isInteger(x) || !Number.isInteger(y)) return null;
  return { x, y };
}

/**
 * 剥掉句首的位置短语：「在 (32,14)」「(32,14)」「32,14 那边」。
 *
 * 只剥句首那一处，不动句中的坐标——`readPoint` 仍从整句里找。
 */
function stripLocation(lower: string): string {
  return lower
    .replace(/^\s*在?\s*\(?\d{1,3}\s*[,，]\s*\d{1,3}\)?\s*(那里|那边|这儿|这边)?\s*/, '')
    .replace(/^\s*在\s*/, '');
}

function stripPrefixes(lower: string): string {
  let body = lower;
  let changed = true;
  while (changed) {
    changed = false;
    for (const prefix of PREFIXES) {
      if (body.startsWith(prefix)) {
        body = body.slice(prefix.length).trim();
        changed = true;
      }
    }
  }
  return body;
}
