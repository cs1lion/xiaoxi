/** 神谕解析：把一句话收敛到世界认识的四个动词。
 *
 * 这是世界自己的意图路由器，不是 LLM。真实助理把结构化指令交给它；
 * 沙盒里用关键词兜底，任何认不出的输入都落到 unknown，世界不动。
 */

import type { OracleIntent, TaskKind, Weather } from './types.ts';

interface Rule {
  readonly kind: TaskKind;
  readonly words: readonly string[];
}

/** 顺序即优先级：先认「交付」，再认世界法则，最后认活。 */
const COMPLETE_WORDS = ['完成', '交付', '收工', 'done', 'finish', 'complete'];
const RAIN_WORDS = ['下雨', '降雨', '落雨', 'rain'];
const CLEAR_WORDS = ['放晴', '雨停', '天晴', '出太阳', 'clear'];
/** 短活必须整句就是"顺手"，不能抢「查一下资料」里的"查"。 */
const QUICK_PREFIXES = ['顺手', '随便', '简单', '小事'];

const TASK_RULES: readonly Rule[] = [
  { kind: 'code', words: ['代码', '写码', '脚本', '程序', '爬虫', '编程', 'code', 'script'] },
  { kind: 'research', words: ['查', '搜', '资料', '研究', '调研', 'search', 'research'] },
  { kind: 'writing', words: ['写', '文稿', '便笺', '整理', '草稿', '稿', 'write', 'draft'] },
];

export type OracleAction =
  | OracleIntent
  | { readonly kind: 'weather'; readonly weather: Weather };

export function parseOracle(raw: string): OracleAction {
  const text = raw.trim();
  if (text.length === 0) return { kind: 'unknown', raw };
  const lower = text.toLowerCase();
  if (COMPLETE_WORDS.some((word) => lower.includes(word))) return { kind: 'complete' };
  if (CLEAR_WORDS.some((word) => lower.includes(word))) {
    return { kind: 'weather', weather: 'clear' };
  }
  if (RAIN_WORDS.some((word) => lower.includes(word))) {
    return { kind: 'weather', weather: 'rain' };
  }
  if (QUICK_PREFIXES.some((word) => lower.includes(word))) {
    return { kind: 'dispatch', taskKind: 'quick', summary: text };
  }
  for (const rule of TASK_RULES) {
    if (rule.words.some((word) => lower.includes(word))) {
      return { kind: 'dispatch', taskKind: rule.kind, summary: text };
    }
  }
  return { kind: 'unknown', raw };
}
