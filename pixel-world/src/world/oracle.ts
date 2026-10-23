/** 神谕解析：把一句话收敛到世界认识的几个动词。
 *
 * 这是世界自己的意图路由器，不是 LLM。真实助理把结构化指令交给它；
 * 沙盒里用关键词兜底，任何认不出的输入都落到 unknown，世界不动。
 *
 * **只认祈使句，不认叙述。** 这是世界不撒谎的一半：如果一句闲聊也能让世界动起来，
 * 那世界就在替你做你没想到的决定。旧实现只要句子里出现「写」就派活，
 * 「这个 bug 怎么写都不对」会真的派一个写作活——那是误伤。
 *
 * 判定分两步：
 *   1. 先排除明显不是命令的句子：疑问、否定、叙述。
 *   2. 再看句首是不是祈使动词（可带「帮我 / 请 / 麻烦」这类礼貌前缀）。
 *
 * 这套判定与造物 / 神迹两个解析器共用 `imperative.ts`：三条通道对
 * 「这句话是不是命令」只能有一个口径，否则同一句话在不同通道里会有
 * 不同结论，接 LLM 时尤其致命。
 */

import type { OracleIntent, TaskKind, Weather } from './types.ts';
import { hasNarrativeMarker, isImperative, isQuestion } from './imperative.ts';

interface Rule {
  readonly kind: TaskKind;
  readonly words: readonly string[];
}

/** 顺序即优先级：先认「交付」，再认世界法则，最后认活。 */
const COMPLETE_WORDS = ['完成', '交付', '收工', 'done', 'finish', 'complete'];
const RAIN_WORDS = ['下雨', '降雨', '落雨', 'rain'];
const CLEAR_WORDS = ['放晴', '雨停', '天晴', '出太阳', 'clear'];
/** 撤销排队中的神谕；只撤等待队列，不动正在干的活。 */
const CANCEL_WORDS = ['撤销', '取消', 'cancel', 'undo'];
/** 短活：句首就是「顺手 / 随便」这类，整句都在说"小事一桩"。 */
const QUICK_PREFIXES = ['顺手', '随便', '简单', '小事'];

/** 句首的祈使动词。只有出现在**开头**才算命令。 */
const IMPERATIVE_VERBS: readonly string[] = [
  // 中文
  '写', '查', '搜', '整理', '研究', '调研', '做', '跑', '爬', '抓',
  // 英文
  'write', 'search', 'research', 'draft', 'find', 'run', 'build', 'make',
];

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

  // 世界法则与「交付 / 撤销」是固定短语，不受祈使判定影响：
  // 说「撤销」就是在撤销，说「交付」就是在交付。
  if (COMPLETE_WORDS.some((word) => lower === word || lower.includes(word))) {
    // 但叙述性的「已经完成了」不该触发。
    if (!hasNarrativeMarker(lower)) return { kind: 'complete' };
  }
  // 世界法则与撤销是**固定短语**，不套祈使判定：说「下雨吧」就是在改天气，
  // 它没有「写 / 查」那样的动词，硬套祈使规则会把合法输入也挡掉。
  // 只排除叙述性的用法（「外面在下雨吗」是提问，不该改天）。
  if (CLEAR_WORDS.some((word) => lower.includes(word)) && !isQuestion(lower)) {
    return { kind: 'weather', weather: 'clear' };
  }
  if (RAIN_WORDS.some((word) => lower.includes(word)) && !isQuestion(lower)) {
    return { kind: 'weather', weather: 'rain' };
  }
  if (CANCEL_WORDS.some((word) => lower.includes(word)) && !isQuestion(lower)) {
    return { kind: 'cancelPending' };
  }

  // 短活：句首就是「顺手 / 随便」这类，本身就是祈使，不必再套动词表。
  if (QUICK_PREFIXES.some((word) => lower.startsWith(word)) && !hasNarrativeMarker(lower)) {
    return { kind: 'dispatch', taskKind: 'quick', summary: text };
  }

  // 其余派活必须先是祈使句。
  if (!isImperative(lower, IMPERATIVE_VERBS)) return { kind: 'unknown', raw };

  for (const rule of TASK_RULES) {
    // 关键词仍要出现（决定**派哪类活**），但「是不是命令」已由 isImperative 判过。
    if (rule.words.some((word) => lower.includes(word))) {
      return { kind: 'dispatch', taskKind: rule.kind, summary: text };
    }
  }
  return { kind: 'unknown', raw };
}
