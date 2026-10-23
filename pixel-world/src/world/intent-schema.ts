/** 意图 schema 与校验器：模糊输入与 LLM 之间的那道闸。
 *
 * 这是 LLM 接入的**唯一入口**。模型读一句人话，输出一份 JSON，这里决定它
 * 能不能变成世界命令。
 *
 * 四条设计原则（前三条与 `creator-schema.ts` 同源）：
 *
 * 1. **只接受世界已有的动词。** 模型不能发明 `teleport` 或 `spawn_dragon`。
 *    动词表就是 `WorldCommand` 的投影，少一个它做不到，多一个世界不认。
 *
 * 2. **未知字段直接拒绝。** 模型多写 `confidence` / `explanation`，说明它对
 *    输出形状的理解已经偏了。静默丢掉会让它一直错下去。
 *
 * 3. **`kind: 'unknown'` 是**一等公民**，不是失败。** 听不出意图时，模型必须
 *    能诚实地说"我不知道"，而不是硬猜一个最像的。世界宁可不动，也不替用户
 *    做他没说的事——这条与 `speak()` 的祈使判定是同一条原则的两端：
 *    规则侧靠拒绝闲聊来守，模型侧靠允许 unknown 来守。
 *
 * 4. **只管形状，不管可行性。** "这栋楼能不能建""这个人忙不忙""迷雾揭没揭"
 *    都由世界自己判（`checkPlacement` / `dispatch` 的拒绝理由）。这里只回答
 *    "这份 JSON 是不是一份合法的意图"。
 *
 * 与规则解析器的关系：**规则优先，模型兜底**。`parseOracle*` 认得出就不调模型，
 * 认不出才问。这样既有离线可跑、可测的确定性，又不会因为模型抖动而改变
 * 已经能被规则稳定识别的那部分行为。
 */

import type { GridPoint, TaskKind, Weather } from './types.ts';
import type { MiracleKind } from './oracle-miracle.ts';
import type { PrefabBlueprint } from './types.ts';

/** 模型能表达的意图种类。与 `WorldCommand` 一一对应，不多不少。 */
export type IntentKind =
  | 'dispatch'
  | 'complete'
  | 'cancelPending'
  | 'setWeather'
  | 'build'
  | 'demolish'
  | 'miracle'
  | 'unknown';

export class IntentSchemaError extends Error {}

/** 校验后的意图。判别联合，取值域全部收窄过。 */
export type ParsedIntent =
  | { readonly kind: 'dispatch'; readonly taskKind: TaskKind; readonly summary: string }
  | { readonly kind: 'complete' }
  | { readonly kind: 'cancelPending' }
  | { readonly kind: 'setWeather'; readonly weather: Weather }
  | { readonly kind: 'build'; readonly templateId: string; readonly at: GridPoint | null }
  | { readonly kind: 'demolish'; readonly templateId: string }
  | { readonly kind: 'miracle'; readonly miracle: MiracleKind; readonly at: GridPoint | null }
  /** 明确表示"没听懂"。带一句给用户看的说明。 */
  | { readonly kind: 'unknown'; readonly reason: string };

const TASK_KINDS: readonly TaskKind[] = ['code', 'research', 'writing', 'quick'];
const WEATHERS: readonly Weather[] = ['clear', 'rain'];
const MIRACLES: readonly MiracleKind[] = ['light', 'tree', 'cat'];

/**
 * 每种意图允许的字段。
 *
 * `unknown` 只接受 `reason`——它没有目标可带，带了就是模型在自作主张。
 */
const ALLOWED_KEYS: Readonly<Record<IntentKind, readonly string[]>> = {
  dispatch: ['kind', 'taskKind', 'summary'],
  complete: ['kind'],
  cancelPending: ['kind'],
  setWeather: ['kind', 'weather'],
  build: ['kind', 'templateId', 'at'],
  demolish: ['kind', 'templateId'],
  miracle: ['kind', 'miracle', 'at'],
  unknown: ['kind', 'reason'],
};

/**
 * 校验一份意图。
 *
 * `templates` 是允许的模板表：模型不能发明新建筑。
 * `worldSize` 用来判坐标在不在界内；能不能建是 `checkPlacement` 的事。
 */
export function parseIntent(
  raw: unknown,
  templates: ReadonlyMap<string, PrefabBlueprint>,
  worldSize: { readonly width: number; readonly height: number },
): ParsedIntent {
  const fail = (why: string): never => {
    throw new IntentSchemaError(why);
  };

  if (!isRecord(raw)) fail('意图必须是对象');
  const record = raw as Record<string, unknown>;

  const kind = record.kind;
  if (typeof kind !== 'string' || !(kind in ALLOWED_KEYS)) {
    fail(`未知意图 "${String(kind)}"：可选 ${Object.keys(ALLOWED_KEYS).join(' / ')}`);
  }
  const intentKind = kind as IntentKind;

  // 先查未知字段：它最能暴露"模型以为世界有这个世界没有的能力"。
  const allowed = ALLOWED_KEYS[intentKind];
  for (const key of Object.keys(record)) {
    if (!allowed.includes(key)) {
      fail(
        `意图 "${intentKind}" 不接受字段 "${key}"：只接受 ${allowed.join(' / ')}`,
      );
    }
  }

  switch (intentKind) {
    case 'dispatch': {
      const taskKind = readOneOf(record.taskKind, TASK_KINDS, 'taskKind', fail);
      const summary = readNonEmpty(record.summary, 'summary', fail);
      return { kind: 'dispatch', taskKind, summary };
    }
    case 'complete':
      return { kind: 'complete' };
    case 'cancelPending':
      return { kind: 'cancelPending' };
    case 'setWeather':
      return { kind: 'setWeather', weather: readOneOf(record.weather, WEATHERS, 'weather', fail) };
    case 'build':
      return {
        kind: 'build',
        templateId: readTemplate(record.templateId, templates, fail),
        at: readPoint(record.at, worldSize, fail),
      };
    case 'demolish':
      return { kind: 'demolish', templateId: readTemplate(record.templateId, templates, fail) };
    case 'miracle':
      return {
        kind: 'miracle',
        miracle: readOneOf(record.miracle, MIRACLES, 'miracle', fail),
        at: readPoint(record.at, worldSize, fail),
      };
    case 'unknown':
      return { kind: 'unknown', reason: readNonEmpty(record.reason, 'reason', fail) };
  }
}

function readOneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  field: string,
  fail: (why: string) => never,
): T {
  if (typeof value !== 'string') fail(`${field} 必须是字符串`);
  if (!(allowed as readonly string[]).includes(value as string)) {
    fail(`${field} 的 "${String(value)}" 不在取值域里：可选 ${allowed.join(' / ')}`);
  }
  return value as T;
}

function readNonEmpty(
  value: unknown,
  field: string,
  fail: (why: string) => never,
): string {
  if (typeof value !== 'string') fail(`${field} 必须是字符串`);
  const text = (value as string).trim();
  if (text === '') fail(`${field} 不能为空`);
  return text;
}

/**
 * 模板 id 必须是注册表里有的。
 *
 * 与 `creator-schema.ts` 的差别：那边要求 `at` 必填（造物模式里玩家已经选好位置），
 * 这里允许省略——「建个工坊」没说建在哪，由世界挑，那是世界的判断不是模型的。
 */
function readTemplate(
  value: unknown,
  templates: ReadonlyMap<string, PrefabBlueprint>,
  fail: (why: string) => never,
): string {
  if (typeof value !== 'string' || value.trim() === '') {
    fail('templateId 必须是非空字符串');
  }
  const templateId = (value as string).trim();
  if (!templates.has(templateId)) {
    fail(`未知模板 "${templateId}"：可选 ${[...templates.keys()].join(' / ')}`);
  }
  return templateId;
}

/** 坐标可省略（null/undefined 都算省略）；给了就必须在界内。 */
function readPoint(
  value: unknown,
  worldSize: { readonly width: number; readonly height: number },
  fail: (why: string) => never,
): GridPoint | null {
  if (value === undefined || value === null) return null;
  if (!isRecord(value)) fail('at 必须是 { x, y } 或省略');

  const record = value as Record<string, unknown>;
  for (const key of Object.keys(record)) {
    if (key !== 'x' && key !== 'y') fail(`at 不接受字段 "${key}"`);
  }
  const x = record.x;
  const y = record.y;
  if (typeof x !== 'number' || !Number.isInteger(x)) fail('at.x 必须是整数格坐标');
  if (typeof y !== 'number' || !Number.isInteger(y)) fail('at.y 必须是整数格坐标');

  const px = x as number;
  const py = y as number;
  // 只判"在不在世界范围里"。能不能建（迷雾、占地、留白、门口）由 checkPlacement 判。
  if (px < 0 || py < 0 || px >= worldSize.width || py >= worldSize.height) {
    fail(
      `at (${px}, ${py}) 在世界之外：范围是 0..${worldSize.width - 1} × 0..${worldSize.height - 1}`,
    );
  }
  return { x: px, y: py };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
