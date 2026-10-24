/** 蓝图注册表。JSON 是外部数据，在边界解析成强类型；解析失败直接抛，不带着半个世界开工。 */

import { WORLD } from './palette-data.ts';
import { WORKSHOP_TASKS } from './tasks.ts';
import type {
  PrefabBlueprint,
  Silhouette,
  ZoneId,
  WorkshopTask,
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
const TASKS: readonly WorkshopTask[] = WORKSHOP_TASKS;
const ACCENTS: readonly WorkAccent[] = ['smoke', 'pages', 'ink'];
const SILHOUETTES: readonly Silhouette[] = ['gable', 'tower', 'flat'];

/** 能落工坊的分区。住宅区只放不接活的建筑。 */
const workshopZones: readonly ZoneId[] = ['industry', 'scholarly', 'agriculture'];

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
  const boundTask =
    raw.boundTask === null ? null : requireOneOf(raw, 'boundTask', TASKS, fail);
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
  if (boundTask !== null && !workshopZones.includes(zone)) {
    return fail(`${boundTask} 的铺子不该开在 ${zone} 区`);
  }

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

/** 每种活恰好一栋常驻铺子。不接活的建筑不参与这条检查。 */
export function validateCoverage(
  table: ReadonlyMap<string, PrefabBlueprint>,
): readonly string[] {
  const problems: string[] = [];
  const workshops = [...table.values()].filter((bp) => bp.boundTask !== null);
  for (const task of TASKS) {
    const matches = workshops.filter((bp) => bp.boundTask === task);
    // 每种活允许 1–2 栋：镇内一栋（本地确定性工作）+ 郊野一栋（探索性工作）。
    // 0 栋等于这类活无处可去；超过 2 栋则是蓝图表失控，没有对应的调度规则。
    if (matches.length < 1 || matches.length > 2) {
      problems.push(
        `任务 ${task} 绑定了 ${matches.length} 栋铺子，必须是 1 或 2 栋（镇内 + 郊野）`,
      );
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

const WORLD_COLOR_NAMES = Object.keys(WORLD) as (keyof typeof WORLD)[];

/** 旧值只提示一次，避免每套蓝图都刷一遍日志。 */
const legacyWarned = new Set<string>();

function warnLegacy(slot: string, raw: unknown): void {
  const key = `${slot}:${String(raw)}`;
  if (legacyWarned.has(key)) return;
  legacyWarned.add(key);
  console.warn(
    `[blueprints] 调色板槽 ${slot} 用的是旧值 ${String(raw)}；已按原值生效，建议改成世界 24 色的名字（${WORLD_COLOR_NAMES.join(', ')}）。`,
  );
}

/**
 * 调色板槽位：优先用世界 24 色的 token 名（D6-A 后的写法），
 * 同时保留旧值兼容——`0..0xffffff` 整数与 `"#rrggbb"` 字符串仍然能用。
 * 这样做是为了让旧 JSON（以及将来可能导入的外部蓝图）不会因为一次改值就整份失效。
 */
function readPaletteSlot(value: Record<string, unknown>, name: string, fail: Fail): number {
  const raw = value[name];
  if (typeof raw === 'string') {
    const token = raw.trim();
    if (token.startsWith('#')) {
      const match = /^#([0-9a-fA-F]{6})$/.exec(token);
      if (match !== null) {
        warnLegacy(name, token);
        return parseInt(match[1], 16);
      }
      return fail(`调色板槽 ${name} 的旧值 "${token}" 不合法：形如 "#8a5a44" 的 6 位十六进制`);
    }
    if (Object.prototype.hasOwnProperty.call(WORLD, token)) {
      return WORLD[token as keyof typeof WORLD];
    }
    return fail(
      `调色板槽 ${name} 的颜色名 "${token}" 不在世界 24 色里；` +
        `可用：${WORLD_COLOR_NAMES.join(', ')}；也可以直接写旧值（0..0xffffff 整数或 "#rrggbb"）`,
    );
  }
  if (!Number.isInteger(raw) || (raw as number) < 0 || (raw as number) > 0xffffff) {
    return fail(
      `调色板槽 ${name} 必须是世界 24 色的名字（${WORLD_COLOR_NAMES.join(', ')}），` +
        `或旧值 0..0xffffff 整数 / "#rrggbb"`,
    );
  }
  warnLegacy(name, raw);
  return raw as number;
}

function requirePalette(
  raw: Record<string, unknown>,
  key: string,
  fail: Fail,
): PrefabBlueprint['palette'] {
  const value = raw[key];
  if (!isRecord(value)) return fail(`${key} 必须是对象`);
  return {
    body: readPaletteSlot(value, 'body', fail),
    roof: readPaletteSlot(value, 'roof', fail),
    trim: readPaletteSlot(value, 'trim', fail),
    window: readPaletteSlot(value, 'window', fail),
  };
}

function inFootprint(p: Point, size: Size): boolean {
  return p.x >= 0 && p.y >= 0 && p.x < size.w && p.y < size.h;
}
