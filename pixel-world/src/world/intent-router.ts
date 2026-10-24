/** 意图路由：规则优先，模型兜底。
 *
 * 为什么不是"全交给模型"：
 *
 * 1. **离线可跑。** 没有 API Key 时世界必须还能用，测试也必须能跑。
 *    全模型化会让整个原型在没有网络的环境里变成一块砖。
 *
 * 2. **确定性优先。** 「下雨吧」「交付」这类固定短语，规则 100% 认得出，
 *    没有理由为它们付一次网络往返、并承担模型抖动。已经稳定的行为不该
 *    因为接入了模型而改变——这是接缝纪律，不是性能考量。
 *
 * 3. **模型只补规则漏掉的。** 规则的短板是**同义改写**（「把那份报告弄一下」
 *    不含任何动词表里的词）。那才是模型该解决的问题，也只该解决它。
 *
 * 因此路由是**串行**的，不是并行的：
 *
 *   规则认出 ──→ 直接用，不调模型
 *       │
 *       └─ 认不出 ──→ 调模型 ──→ 校验 ──→ 用
 *                        │
 *                        └─ 失败/超时 ──→ unknown（世界不动）
 *
 * **失败必须落到 unknown，不能落到"最像的那个"。** 模型超时、返回非法 JSON、
 * 网络断了——这些都等于"没听懂"。退而求其次猜一个，就是在替用户做决定。
 */

import type { PrefabBlueprint } from './types.ts';
import type { ParsedIntent } from './intent-schema.ts';
import type { IntentContext } from './intent-context.ts';
import { IntentSchemaError, parseIntent } from './intent-schema.ts';
import { parseOracle } from './oracle.ts';
import { parseOracleBuild } from './oracle-build.ts';
import { parseOracleMiracle } from './oracle-miracle.ts';
import type { MiracleKind } from './oracle-miracle.ts';

/** 模型客户端。只有这一个方法——接缝越窄，换实现越容易。 */
export interface IntentModel {
  /**
   * 问一句，拿回原始 JSON（已解析）。失败时抛错，调用方决定怎么兜。
   *
   * `context` 是世界现状（几栋楼、谁在忙）。**只用来让模型少犯错**，
   * 不参与校验：可行性仍由世界在结算时判。省略时客户端不发这个字段。
   */
  readonly classify: (utterance: string, context?: IntentContext) => Promise<unknown>;
}

export interface RouteOptions {
  readonly templates: ReadonlyMap<string, PrefabBlueprint>;
  readonly worldSize: { readonly width: number; readonly height: number };
  /** 省略表示纯离线：只用规则，认不出就是 unknown。 */
  readonly model?: IntentModel;
  /** 世界现状。只喂模型做上下文，不参与校验。 */
  readonly context?: IntentContext;
}

/** 意图从哪来。用于 UI 提示与调试，不参与世界逻辑。 */
export type IntentSource = 'rule' | 'model' | 'none';

export interface RouteResult {
  readonly intent: ParsedIntent;
  readonly source: IntentSource;
  /** 模型那一路失败时的原因。规则命中或成功时为 null。 */
  readonly modelError: string | null;
}

/**
 * 把一句话路由成意图。
 *
 * 永远返回一个 `ParsedIntent`，永不抛错——调用方（`speak()`）不该处理异常。
 * "认不出"是一个正常结果，不是错误。
 */
export async function routeIntent(
  raw: string,
  options: RouteOptions,
): Promise<RouteResult> {
  const text = raw.trim();
  if (text.length === 0) {
    return {
      intent: { kind: 'unknown', reason: '空句子' },
      source: 'none',
      modelError: null,
    };
  }

  // ---------------------------------------------------------- 一、规则
  // 顺序与 `speak()` 完全一致：造物 → 神迹 → 派活。三张动词表互不抢词。
  const ruleIntent = matchRules(text, options.templates);
  if (ruleIntent !== null) {
    return { intent: ruleIntent, source: 'rule', modelError: null };
  }

  // ---------------------------------------------------------- 二、模型
  if (options.model === undefined) {
    return {
      intent: { kind: 'unknown', reason: '这句话没有对应的世界命令' },
      source: 'none',
      modelError: null,
    };
  }

  try {
    const rawIntent = await options.model.classify(text, options.context);
    const intent = parseIntent(rawIntent, options.templates, options.worldSize);
    return { intent, source: 'model', modelError: null };
  } catch (error) {
    // 模型失败 = 没听懂。不退而求其次。
    const why = error instanceof IntentSchemaError
      ? `模型输出的意图不合法：${error.message}`
      : `模型调用失败：${errorMessage(error)}`;
    return {
      intent: { kind: 'unknown', reason: '这句话没有对应的世界命令' },
      source: 'none',
      modelError: why,
    };
  }
}

/** 规则三连。与 `speak()` 的既有顺序保持一致，认不出返回 null。 */
function matchRules(
  text: string,
  templates: ReadonlyMap<string, PrefabBlueprint>,
): ParsedIntent | null {
  const build = parseOracleBuild(text, templates);
  if (build !== null) {
    return build.action === 'build'
      ? { kind: 'build', templateId: build.templateId, at: build.at }
      : { kind: 'demolish', templateId: build.templateId };
  }

  const miracle = parseOracleMiracle(text);
  if (miracle !== null) {
    return { kind: 'miracle', miracle: miracle.miracle, at: miracle.at };
  }

  const oracle = parseOracle(text);
  switch (oracle.kind) {
    case 'dispatch':
      return { kind: 'dispatch', taskKind: oracle.taskKind, summary: oracle.summary };
    case 'complete':
      return { kind: 'complete' };
    case 'cancelPending':
      return { kind: 'cancelPending' };
    case 'weather':
      return { kind: 'setWeather', weather: oracle.weather };
    case 'unknown':
      return null;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

/** 意图的中文短名。给回执用，不参与世界逻辑。 */
export function describeIntent(intent: ParsedIntent): string {
  switch (intent.kind) {
    case 'dispatch':
      return '派活';
    case 'complete':
      return '交付';
    case 'cancelPending':
      return '撤销';
    case 'setWeather':
      return intent.weather === 'rain' ? '下雨' : '放晴';
    case 'build':
      return '造物';
    case 'demolish':
      return '拆除';
    case 'miracle':
      return MIRACLE_LABEL[intent.miracle];
    case 'unknown':
      return '没听懂';
  }
}

const MIRACLE_LABEL: Readonly<Record<MiracleKind, string>> = {
  light: '点灯',
  tree: '种树',
  cat: '召唤猫',
};
