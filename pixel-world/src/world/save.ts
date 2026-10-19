/** 存档 v1：只序列化世界自有事实（ADR_SAVE §1.2，final / frozen）。
 *
 * 这里刻意不 import Simulation：存档形状与运行时类解耦，测试可离线校验。
 * 字段冻结规则：WorldSaveV1 与 docs/save-v1.sample.json 必须一致；
 * 字段变了就要升 schemaVersion + 写迁移，指纹测试会拦。
 */

import type {
  Facing,
  GridPoint,
  PendingDispatch,
  PlacedBuilding,
  TaskRecord,
  VillagerMode,
  WorldProp,
  WorkstationActivity,
} from './types.ts';
import type { WeatherState } from './weather.ts';

export const CURRENT_SCHEMA_VERSION = 1;
export const SAVE_STORAGE_KEY = 'xiaoxi.pixel-world.save.v1';
/** 已结算记录超过这个数就告警一次（每会话一次）。 */
export const SETTLED_WARN_THRESHOLD = 5000;
/** 冻结指纹（ADR_SAVE §1，frozen）。样例 meta.schemaFingerprint 同值。 */
export const SCHEMA_V1_FINGERPRINT = 'f53a0736d782d657';

/**
 * v1 冻结的 `world.tasks[]` 键集合（必须与 docs/save-v1.sample.json 逐键一致）。
 * 运行时字段（如 ledgerCompletedAt）不得落盘；字段漂移必须升版本，不能裸加。
 */
export const SAVE_TASK_KEYS = [
  'blockedQuestion',
  'buildingId',
  'completedAt',
  'createdAt',
  'id',
  'kind',
  'source',
  'status',
  'summary',
  'workerId',
] as const;

/** v1 冻结的 `world.settled[]` 键集合（ADR_SAVE §2.1）。 */
export const SAVE_SETTLED_KEYS = [
  'completedAt',
  'completedAtClock',
  'id',
  'source',
  'workerId',
] as const;

/** 存档里的角色：快照缺的续跑字段由 toSave() 补齐（ADR_SAVE §1.2）。 */
export interface SavedVillager {
  readonly id: string;
  readonly name: string;
  readonly mode: VillagerMode;
  readonly pos: { readonly x: number; readonly y: number };
  readonly facing: Facing;
  readonly visible: boolean;
  readonly path: readonly GridPoint[];
  readonly taskId: string | null;
  readonly bubble: string | null;
  readonly bubbleUntil: number;
  readonly saluteUntil: number;
  readonly deliverUntil: number;
  readonly nextRoutineAt: number;
  readonly routineIndex: number;
  readonly routineLabel: string | null;
  readonly inPlaceUntil: number;
  readonly pendingRoute: readonly GridPoint[];
  readonly pendingBuildingId: string | null;
}

/** 已结算记录：save-only 的去重与分账唯一真相（ADR_SAVE §2.1/§2.2）。 */
export interface SettledRecord {
  readonly id: string;
  readonly source: 'ledger' | 'mock';
  readonly workerId: string;
  /** ledger 用台账事件时间；缺失记 null，禁止本地时间顶替。mock 用本地 wall clock。 */
  readonly completedAt: number | null;
  readonly completedAtClock: 'ledger' | 'local';
}

export interface SavedActivity {
  readonly buildingId: string;
  readonly activity: WorkstationActivity;
}

export interface SavedConstruction {
  readonly buildingId: string;
  readonly remainingMs: number;
  readonly totalMs: number;
}

export interface SaveWorld {
  readonly now: number;
  readonly villagers: readonly SavedVillager[];
  readonly buildings: readonly PlacedBuilding[];
  readonly roads: readonly GridPoint[];
  readonly props: readonly WorldProp[];
  readonly tasks: readonly TaskRecord[];
  readonly pending: readonly PendingDispatch[];
  readonly activities: readonly SavedActivity[];
  readonly constructions: readonly SavedConstruction[];
  readonly weather: WeatherState;
  readonly faith: { readonly points: number; readonly milestones: number };
  readonly settled: readonly SettledRecord[];
  readonly nextTaskNumber: number;
  readonly nextPendingNumber: number;
}

export interface WorldSaveV1 {
  readonly schemaVersion: 1;
  readonly world: SaveWorld;
  readonly meta: {
    /** 仅诊断，永不喂逻辑（ADR_SAVE §5）。 */
    readonly savedAtWallClock: number;
    /** 写档时是否已与台账对账（仅诊断）。 */
    readonly savedReconciled: boolean;
    /** 冻结指纹；加载时存在且不符 → 按坏档处理。 */
    readonly schemaFingerprint: string;
  };
}

export type RawSave = Record<string, unknown>;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * 取叶子路径：数组记 `[]`（数组本身也记一条），元素内的键继承 `[]`。
 * 同路径去重；`meta.schemaFingerprint` 不参与（ADR_SAVE §1 冻结规则）。
 */
export function collectSavePaths(value: unknown, prefix = '', out: string[] = []): string[] {
  if (Array.isArray(value)) {
    out.push(`${prefix}[]`);
    for (const item of value) collectSavePaths(item, `${prefix}[]`, out);
    return out;
  }
  if (isObject(value)) {
    for (const key of Object.keys(value)) {
      if (prefix === 'meta' && key === 'schemaFingerprint') continue;
      collectSavePaths(value[key], prefix === '' ? key : `${prefix}.${key}`, out);
    }
    return out;
  }
  out.push(prefix);
  return out;
}

/** FNV-1a 64-bit over UTF-8，16 位小写 hex。 */
export function fnv1a64Hex(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let hash = 14695981039346656037n;
  const prime = 1099511628211n;
  const mask = (1n << 64n) - 1n;
  for (const byte of bytes) {
    hash = (hash ^ BigInt(byte)) & mask;
    hash = (hash * prime) & mask;
  }
  return hash.toString(16).padStart(16, '0');
}

/** 路径集合 → 排序、`\n` 连接、FNV-1a 64-bit。 */
export function fingerprintOfPaths(paths: readonly string[]): string {
  return fnv1a64Hex([...new Set(paths)].sort().join('\n'));
}

/** 对任意存档对象取指纹（跳过 meta.schemaFingerprint）。 */
export function saveFingerprint(save: unknown): string {
  return fingerprintOfPaths(collectSavePaths(save));
}

/** 键集合必须与冻结样例逐键一致：多一个运行时字段就是字段漂移。 */
function assertKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  label: string,
): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new Error(`${label} 键集合与 v1 样例不一致：实际 [${actual.join(',')}]`);
  }
}

/** 校验 v1：形状 + 嵌套键集合 + 冻结指纹。指纹不符 = 字段漂移，按坏档处理。 */
export function assertSaveV1(raw: unknown): asserts raw is WorldSaveV1 {
  if (!isObject(raw)) throw new Error('存档不是对象');
  if (raw.schemaVersion !== CURRENT_SCHEMA_VERSION) {
    throw new Error(`schemaVersion 不是 ${CURRENT_SCHEMA_VERSION}`);
  }
  const world = raw.world;
  if (!isObject(world)) throw new Error('存档缺 world');
  for (const key of [
    'villagers',
    'buildings',
    'roads',
    'props',
    'tasks',
    'pending',
    'activities',
    'constructions',
    'settled',
  ]) {
    if (!Array.isArray(world[key])) throw new Error(`world.${key} 必须是数组`);
  }
  for (const key of ['now', 'nextTaskNumber', 'nextPendingNumber']) {
    if (typeof world[key] !== 'number' || !Number.isFinite(world[key])) {
      throw new Error(`world.${key} 必须是数字`);
    }
  }
  if (!isObject(world.weather)) throw new Error('world.weather 必须是对象');
  if (!isObject(world.faith)) throw new Error('world.faith 必须是对象');
  if (!Array.isArray(world.villagers) || world.villagers.length === 0) {
    throw new Error('存档没有角色');
  }
  const meta = raw.meta;
  if (!isObject(meta)) throw new Error('存档缺 meta');
  if (typeof meta.schemaFingerprint === 'string' && meta.schemaFingerprint !== SCHEMA_V1_FINGERPRINT) {
    throw new Error('存档指纹不符（字段变了但版本没升，或存档损坏）');
  }
  const settledIds = new Set<string>();
  for (const record of world.settled as unknown[]) {
    if (!isObject(record) || typeof record.id !== 'string' || typeof record.workerId !== 'string') {
      throw new Error('settled 记录缺 id/workerId');
    }
    assertKeys(record, SAVE_SETTLED_KEYS, 'world.settled[]');
    if (record.source !== 'ledger' && record.source !== 'mock') {
      throw new Error('settled.source 只能是 ledger/mock');
    }
    if (record.completedAt !== null && typeof record.completedAt !== 'number') {
      throw new Error('settled.completedAt 必须是数字或 null');
    }
    if (record.completedAtClock !== 'ledger' && record.completedAtClock !== 'local') {
      throw new Error('settled.completedAtClock 只能是 ledger/local');
    }
    settledIds.add(record.id);
  }
  // 嵌套字段漂移防线：tasks[] 键集合固定，且 done 必须都能在 settled 找到（去重真相）。
  for (const task of world.tasks as unknown[]) {
    if (!isObject(task)) throw new Error('world.tasks 元素必须是对象');
    assertKeys(task, SAVE_TASK_KEYS, 'world.tasks[]');
    if (task.status === 'done' && !settledIds.has(String(task.id))) {
      throw new Error(`done 任务 ${String(task.id)} 缺少 settled 记录`);
    }
  }
}

/** 逐级迁移链入口；任一步抛错都拒绝加载（ADR_SAVE §3）。 */
export type Migration = (raw: RawSave) => RawSave;

export const MIGRATIONS: Record<number, Migration> = {
  // v1→v2 样例：settled 记录补 completedAtClock（历史记录按 local，不伪造时间）。
  1: (raw) => {
    const world = isObject(raw.world) ? raw.world : {};
    const settled = Array.isArray(world.settled) ? world.settled : [];
    return {
      ...raw,
      schemaVersion: 2,
      world: {
        ...world,
        settled: settled.map((record) =>
          isObject(record) ? { ...record, completedAtClock: record.completedAtClock ?? 'local' } : record,
        ),
      },
    };
  },
};

export function migrateSave(raw: RawSave): RawSave {
  const version = typeof raw.schemaVersion === 'number' ? raw.schemaVersion : 0;
  let current = raw;
  let cursor = version;
  while (cursor < CURRENT_SCHEMA_VERSION) {
    const migration = MIGRATIONS[cursor];
    if (migration === undefined) throw new Error(`没有从 v${cursor} 升级的迁移`);
    current = migration(current);
    const next = typeof current.schemaVersion === 'number' ? current.schemaVersion : cursor + 1;
    if (next <= cursor) throw new Error('迁移没有推进版本号');
    cursor = next;
  }
  return current;
}
