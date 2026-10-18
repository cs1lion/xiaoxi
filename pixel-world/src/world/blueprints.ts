/** 蓝图注册表。JSON 是外部数据，在边界解析成强类型；解析失败直接抛，不带着半个世界开工。 */

import type {
  PrefabBlueprint,
  Silhouette,
  ZoneId,
  TaskKind,
  WorkAccent,
} from './types.ts';

const ZONES: readonly ZoneId[] = [
  'street',
  'altar',
  'industry',
  'scholarly',
  'agriculture',
  'living',
];
const TASKS: readonly TaskKind[] = ['code', 'research', 'writing'];
const ACCENTS: readonly WorkAccent[] = ['smoke', 'pages', 'ink'];
const SILHOUETTES: readonly Silhouette[] = ['gable', 'tower', 'flat'];

export class BlueprintError extends Error {}

export function parseBlueprint(id: string, raw: unknown): PrefabBlueprint {
  const fail = (why: string): never => {
    throw new BlueprintError(`蓝图 ${id}: ${why}`);
  };
  if (!isRecord(raw)) return fail('不是对象');
  if (raw.id !== id) fail(`id 字段 ${String(raw.id)} 与键名不符`);

  const displayName = requireString(raw, 'displayName', fail);
  const zone = requireOneOf(raw, 'zone', ZONES, fail);
  const accent = requireOneOf(raw, 'accent', ACCENTS, fail);
  const silhouette = requireOneOf(raw, 'silhouette', SILHOUETTES, fail);
  const boundTask = requireOneOf(raw, 'boundTask', TASKS, fail);
  const footprint = requireSize(raw, 'footprint', fail);
  const clearMargin = requireNonNegativeInt(raw, 'clearMargin', fail);
  const door = requirePoint(raw, 'door', fail);
  const chimney = requireVec(raw, 'chimney', fail);
  const workSpots = requirePoints(raw, 'workSpots', fail);
  const palette = requirePalette(raw, 'palette', fail);

  if (workSpots.length === 0) fail('workSpots 不能为空');
  if (inFootprint(door, footprint)) fail('门口落在占地内，人走不进去');
  for (const spot of workSpots) {
    if (!inFootprint(spot, footprint)) fail('工位落在占地外');
  }
  if (boundTask === 'code' && accent !== 'smoke') fail('代码活必须冒烟');
  if (boundTask === 'research' && accent !== 'pages') fail('查资料必须翻纸');
  if (boundTask === 'writing' && accent !== 'ink') fail('写字必须落墨');

  const upgradeTo = raw.upgradeTo;
  if (upgradeTo !== undefined && typeof upgradeTo !== 'string') {
    return fail('upgradeTo 必须是字符串');
  }

  return {
    id,
    displayName,
    zone,
    footprint,
    clearMargin,
    door,
    workSpots,
    chimney,
    accent,
    silhouette,
    boundTask,
    palette,
    ...(upgradeTo === undefined ? {} : { upgradeTo }),
  };
}

export function parseBlueprintTable(raw: unknown): ReadonlyMap<string, PrefabBlueprint> {
  if (!isRecord(raw)) throw new BlueprintError('蓝图表不是对象');
  const out = new Map<string, PrefabBlueprint>();
  for (const [id, entry] of Object.entries(raw)) {
    out.set(id, parseBlueprint(id, entry));
  }
  if (out.size === 0) throw new BlueprintError('蓝图表为空');
  return out;
}

/** 每种活都必须有且只有一栋铺子，否则神谕落不下去。 */
export function validateCoverage(
  table: ReadonlyMap<string, PrefabBlueprint>,
): readonly string[] {
  const problems: string[] = [];
  for (const task of TASKS) {
    const matches = [...table.values()].filter((bp) => bp.boundTask === task);
    if (matches.length !== 1) {
      problems.push(`任务 ${task} 绑定了 ${matches.length} 栋铺子，必须恰好 1 栋`);
    }
  }
  for (const bp of table.values()) {
    if (bp.upgradeTo !== undefined && !table.has(bp.upgradeTo)) {
      problems.push(`蓝图 ${bp.id} 的 upgradeTo ${bp.upgradeTo} 不在表里`);
    }
  }
  return problems;
}

type Fail = (why: string) => never;
type Size = { readonly w: number; readonly h: number };
type Point = { readonly x: number; readonly y: number };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(raw: Record<string, unknown>, key: string, fail: Fail): string {
  const value = raw[key];
  if (typeof value !== 'string' || value.length === 0) return fail(`${key} 必须是非空字符串`);
  return value;
}

function requireOneOf<T extends string>(
  raw: Record<string, unknown>,
  key: string,
  allowed: readonly T[],
  fail: Fail,
): T {
  const value = raw[key];
  if (typeof value !== 'string') return fail(`${key} 必须是字符串`);
  const match = allowed.find((item) => item === value);
  if (match === undefined) return fail(`${key} 取值 ${value} 不在 ${allowed.join('/')} 里`);
  return match;
}

function requireNonNegativeInt(
  raw: Record<string, unknown>,
  key: string,
  fail: Fail,
): number {
  const value = raw[key];
  if (!Number.isInteger(value) || (value as number) < 0) {
    return fail(`${key} 必须是非负整数`);
  }
  return value as number;
}

function requireSize(raw: Record<string, unknown>, key: string, fail: Fail): Size {
  const value = raw[key];
  if (!isRecord(value)) return fail(`${key} 必须是对象`);
  const w = value.w;
  const h = value.h;
  if (!Number.isInteger(w) || (w as number) < 1) fail(`${key}.w 必须是正整数`);
  if (!Number.isInteger(h) || (h as number) < 1) fail(`${key}.h 必须是正整数`);
  return { w: w as number, h: h as number };
}

function requirePoint(raw: Record<string, unknown>, key: string, fail: Fail): Point {
  const value = raw[key];
  if (!isRecord(value)) return fail(`${key} 必须是对象`);
  const x = value.x;
  const y = value.y;
  if (!Number.isInteger(x) || !Number.isInteger(y)) fail(`${key} 必须是整数格`);
  return { x: x as number, y: y as number };
}

function requireVec(raw: Record<string, unknown>, key: string, fail: Fail): Point {
  const value = raw[key];
  if (!isRecord(value)) return fail(`${key} 必须是对象`);
  const x = value.x;
  const y = value.y;
  if (typeof x !== 'number' || typeof y !== 'number') fail(`${key} 必须是数字`);
  if (!Number.isFinite(x) || !Number.isFinite(y)) fail(`${key} 不能是 NaN/Infinity`);
  return { x: x as number, y: y as number };
}

function requirePoints(raw: Record<string, unknown>, key: string, fail: Fail): Point[] {
  const value = raw[key];
  if (!Array.isArray(value)) return fail(`${key} 必须是数组`);
  return value.map((entry, index) => {
    if (!isRecord(entry)) return fail(`${key}[${index}] 必须是对象`);
    const x = entry.x;
    const y = entry.y;
    if (!Number.isInteger(x) || !Number.isInteger(y)) {
      return fail(`${key}[${index}] 必须是整数格`);
    }
    return { x: x as number, y: y as number };
  });
}

function requirePalette(
  raw: Record<string, unknown>,
  key: string,
  fail: Fail,
): PrefabBlueprint['palette'] {
  const value = raw[key];
  if (!isRecord(value)) return fail(`${key} 必须是对象`);
  const read = (name: string): number => {
    const channel = value[name];
    if (!Number.isInteger(channel) || (channel as number) < 0 || (channel as number) > 0xffffff) {
      return fail(`${key}.${name} 必须是 0..0xffffff 的整数`);
    }
    return channel as number;
  };
  return { body: read('body'), roof: read('roof'), trim: read('trim'), window: read('window') };
}

function inFootprint(p: Point, size: Size): boolean {
  return p.x >= 0 && p.y >= 0 && p.x < size.w && p.y < size.h;
}
