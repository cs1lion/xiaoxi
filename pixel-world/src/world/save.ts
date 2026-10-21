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
  PropKind,
  Silhouette,
  TaskKind,
  TaskRecord,
  TaskSource,
  TaskStatus,
  VillagerMode,
  Weather,
  WorkAccent,
  WorkshopTask,
  WorkstationActivity,
  WorldProp,
  ZoneId,
} from './types.ts';
import type { WeatherState } from './weather.ts';

export const CURRENT_SCHEMA_VERSION = 1;
export const SAVE_STORAGE_KEY = 'xiaoxi.pixel-world.save.v1';
/** 已结算记录超过这个数就告警一次（每会话一次）。 */
export const SETTLED_WARN_THRESHOLD = 5000;
/** 冻结指纹（ADR_SAVE §1，frozen）。样例 meta.schemaFingerprint 同值。 */
export const SCHEMA_V1_FINGERPRINT = 'f53a0736d782d657';

/** v1 冻结路径全集（99 条，来自 docs/save-v1.sample.json；指纹见上，单测核对）。
 * `assertSaveV1` 做**集合包含**校验：存档出现的每条叶子路径都必须在这个集合里；
 * 越界即按「字段漂移」拒绝，并指名越界路径。
 *
 * 语义边界（P2）：本校验只防「新增路径」。字段删除（路径合法缩小）、类型变化
 * （number↔string↔null、null↔{} 等）不在这里拦——靠显式形状校验与 v2 迁移处理。
 * 不给存档本身重算指纹再比常量，是因为空数组会让路径集合合法地缩小
 * （fresh save 的重算值本来就不等于常量），那样会把正常存档全判成坏档。
 */
export const SAVE_V1_PATHS: readonly string[] = [
  'meta.savedAtWallClock',
  'meta.savedReconciled',
  'schemaVersion',
  'world.activities[]',
  'world.activities[].activity.determinate',
  'world.activities[].activity.estimateMs',
  'world.activities[].activity.kind',
  'world.activities[].activity.since',
  'world.activities[].activity.startedAt',
  'world.activities[].activity.taskId',
  'world.activities[].activity.until',
  'world.activities[].buildingId',
  'world.buildings[]',
  'world.buildings[].accent',
  'world.buildings[].boundTask',
  'world.buildings[].chimney.x',
  'world.buildings[].chimney.y',
  'world.buildings[].displayName',
  'world.buildings[].door.x',
  'world.buildings[].door.y',
  'world.buildings[].footprint.h',
  'world.buildings[].footprint.w',
  'world.buildings[].instanceId',
  'world.buildings[].origin.x',
  'world.buildings[].origin.y',
  'world.buildings[].palette.body',
  'world.buildings[].palette.roof',
  'world.buildings[].palette.trim',
  'world.buildings[].palette.window',
  'world.buildings[].prefabId',
  'world.buildings[].silhouette',
  'world.buildings[].workSpot.x',
  'world.buildings[].workSpot.y',
  'world.buildings[].zone',
  'world.constructions[]',
  'world.constructions[].buildingId',
  'world.constructions[].remainingMs',
  'world.constructions[].totalMs',
  'world.faith.milestones',
  'world.faith.points',
  'world.nextPendingNumber',
  'world.nextTaskNumber',
  'world.now',
  'world.pending[]',
  'world.pending[].id',
  'world.pending[].kind',
  'world.pending[].requestedAt',
  'world.pending[].source',
  'world.pending[].summary',
  'world.pending[].villagerId',
  'world.props[]',
  'world.props[].at.x',
  'world.props[].at.y',
  'world.props[].id',
  'world.props[].kind',
  'world.props[].label',
  'world.roads[]',
  'world.roads[].x',
  'world.roads[].y',
  'world.settled[]',
  'world.settled[].completedAt',
  'world.settled[].completedAtClock',
  'world.settled[].id',
  'world.settled[].source',
  'world.settled[].workerId',
  'world.tasks[]',
  'world.tasks[].blockedQuestion',
  'world.tasks[].buildingId',
  'world.tasks[].completedAt',
  'world.tasks[].createdAt',
  'world.tasks[].id',
  'world.tasks[].kind',
  'world.tasks[].source',
  'world.tasks[].status',
  'world.tasks[].summary',
  'world.tasks[].workerId',
  'world.villagers[]',
  'world.villagers[].bubble',
  'world.villagers[].bubbleUntil',
  'world.villagers[].deliverUntil',
  'world.villagers[].facing',
  'world.villagers[].id',
  'world.villagers[].inPlaceUntil',
  'world.villagers[].mode',
  'world.villagers[].name',
  'world.villagers[].nextRoutineAt',
  'world.villagers[].path[]',
  'world.villagers[].pendingBuildingId',
  'world.villagers[].pendingRoute[]',
  'world.villagers[].pos.x',
  'world.villagers[].pos.y',
  'world.villagers[].routineIndex',
  'world.villagers[].routineLabel',
  'world.villagers[].saluteUntil',
  'world.villagers[].taskId',
  'world.villagers[].visible',
  'world.weather.intensity',
  'world.weather.kind',
  'world.weather.remainingMs',
];

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

/**
 * 样例里没走到、但运行中合法的 4 条嵌套路径（完整字符串见下方常量）。
 *
 * 原因：`docs/save-v1.sample.json` 里 `world.villagers[].path` 与
 * `world.villagers[].pendingRoute` 都是空数组，`collectSavePaths` 只会记下
 * `world.villagers[].path[]` 这一条数组路径，不会出现元素键；而小人一旦在走路
 * （`mode=toWork/toAltar/sheltering`，或读档时带着待走路径），这两个数组就是
 * 非空的 `{x,y}[]`，属于 v1 的合法现场。不补进允许集合，「正在走路」的存档
 * 会被集合包含校验误判成字段漂移。
 */
export const SAVE_V1_OPTIONAL_PATHS: readonly string[] = [
  'world.villagers[].path[].x',
  'world.villagers[].path[].y',
  'world.villagers[].pendingRoute[].x',
  'world.villagers[].pendingRoute[].y',
];

/** 合法存档允许出现的路径全集 = 冻结样例 99 条 + 上面这些可选嵌套路径。 */
export const SAVE_V1_ALLOWED_PATHS: readonly string[] = [
  ...SAVE_V1_PATHS,
  ...SAVE_V1_OPTIONAL_PATHS,
];

const SAVE_V1_PATH_SET = new Set(SAVE_V1_ALLOWED_PATHS);

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

/* ------------------------------------------------------------------ task-32 字段级校验
 *
 * 两级校验分工（ADR_SAVE §1.3）：
 *   - 路径集合包含校验：只防「新增字段」——存档出现允许集合之外的路径即拒。
 *   - 本节的类型/范围/存在性校验：防「删字段」「换错类型」「值越界」——
 *     路径集合会因删除而合法缩小，所以那一半必须由这里逐字段兜住。
 *
 * 纪律：一律**拒绝**，不静默补默认值。存档缺一个字段就是坏档，不能拿 `??` 猜。
 */

/** 合法枚举值（与 types.ts 的联合类型逐项对应，改一处必须同步）。 */
export const VILLAGER_MODES: readonly VillagerMode[] = [
  'idle', 'saluting', 'toWork', 'working', 'delivering', 'toAltar', 'sheltering',
];
export const TASK_STATUSES: readonly TaskStatus[] = ['assigned', 'in_progress', 'done', 'failed'];
export const TASK_KINDS: readonly TaskKind[] = ['code', 'research', 'writing', 'quick'];
export const TASK_SOURCES: readonly TaskSource[] = ['sandbox', 'ledger'];
export const FACINGS: readonly Facing[] = ['up', 'down', 'left', 'right'];
export const ZONES: readonly ZoneId[] = [
  'street', 'altar', 'industry', 'scholarly', 'agriculture', 'living',
];
export const PROP_KINDS: readonly PropKind[] = ['lamp', 'tree', 'banner'];
export const WORK_ACCENTS: readonly WorkAccent[] = ['smoke', 'pages', 'ink'];
export const SILHOUETTES: readonly Silhouette[] = ['gable', 'tower', 'flat'];
export const WEATHER_KINDS: readonly Weather[] = ['clear', 'rain'];
export const ACTIVITY_KINDS: readonly WorkstationActivity['kind'][] = [
  'idle', 'operating', 'overheating', 'cooling',
];
/** boundTask 只接工地活（quick 原地做，不进铺子）。 */
export const WORKSHOP_TASKS: readonly WorkshopTask[] = ['code', 'research', 'writing'];

const SETTLED_SOURCES = ['ledger', 'mock'] as const;
const SETTLED_CLOCKS = ['ledger', 'local'] as const;
const PENDING_SOURCES = ['oracle'] as const;

function fail(path: string, problem: string): never {
  throw new Error(`存档字段校验不通过：${path} ${problem}`);
}

function asRecord(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    fail(path, '必须是对象');
  }
  return value as Record<string, unknown>;
}

function asArray(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) fail(path, '必须是数组');
  return value as readonly unknown[];
}

/** 必须有这个键（值为 undefined 也算缺）：删字段是坏档，不是"用默认值"。 */
function requireKey(object: Record<string, unknown>, key: string, path: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(object, key) || object[key] === undefined) {
    fail(`${path}.${key}`, '缺失（v1 要求该字段存在）');
  }
  return object[key];
}

function checkNumber(value: unknown, path: string, opts: { min?: number; integer?: boolean } = {}): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) fail(path, '必须是有限数字');
  if (opts.integer === true && !Number.isInteger(value)) fail(path, '必须是整数');
  if (opts.min !== undefined && value < opts.min) fail(path, `必须 ≥ ${opts.min}，实际 ${value}`);
  return value;
}

function checkString(value: unknown, path: string, opts: { allowEmpty?: boolean } = {}): string {
  if (typeof value !== 'string') fail(path, '必须是字符串');
  if (opts.allowEmpty !== true && value === '') fail(path, '必须是非空字符串');
  return value;
}

function checkBoolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') fail(path, '必须是布尔值');
  return value;
}

/** number | null（时间戳类字段）。 */
function checkNumberOrNull(value: unknown, path: string, opts: { min?: number } = {}): number | null {
  if (value === null) return null;
  return checkNumber(value, path, opts);
}

/** string | null（可为空的文本/引用）。 */
function checkStringOrNull(value: unknown, path: string, opts: { allowEmpty?: boolean } = {}): string | null {
  if (value === null) return null;
  return checkString(value, path, opts);
}

function checkEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  path: string,
): T {
  if (typeof value !== 'string' || !allowed.includes(value as T)) {
    fail(path, `只能是 ${allowed.join('/')}，实际 ${JSON.stringify(value)}`);
  }
  return value as T;
}

/** 网格坐标：整数、落在世界内。允许多一个无关键？不——有键集合校验兜着，这里只查存在键。 */
function checkPoint(value: unknown, path: string, opts: { integer?: boolean } = {}): void {
  const point = asRecord(value, path);
  checkNumber(requireKey(point, 'x', path), `${path}.x`, { integer: opts.integer });
  checkNumber(requireKey(point, 'y', path), `${path}.y`, { integer: opts.integer });
}

/** `{x,y}[]`：元素结构必须完整；空数组合法（样例里就是空）。 */
function checkPointList(value: unknown, path: string): void {
  const list = asArray(value, path);
  list.forEach((item, index) => checkPoint(item, `${path}[${index}]`, { integer: true }));
}

/**
 * 逐个字段校验 v1 的类型/范围/存在性。
 * 只做「值」层面的检查；路径越界由 `assertSaveV1` 末尾的集合包含校验负责。
 */
function validateWorldFields(raw: Record<string, unknown>): void {
  const world = asRecord(raw.world, 'world');

  checkNumber(requireKey(world, 'now', 'world'), 'world.now', { min: 0 });
  checkNumber(requireKey(world, 'nextTaskNumber', 'world'), 'world.nextTaskNumber', {
    min: 1, integer: true,
  });
  checkNumber(requireKey(world, 'nextPendingNumber', 'world'), 'world.nextPendingNumber', {
    min: 1, integer: true,
  });

  // ---- 角色
  const villagers = asArray(requireKey(world, 'villagers', 'world'), 'world.villagers');
  if (villagers.length === 0) fail('world.villagers', '至少要有一个角色');
  villagers.forEach((item, index) => {
    const path = `world.villagers[${index}]`;
    const villager = asRecord(item, path);
    checkString(requireKey(villager, 'id', path), `${path}.id`);
    checkString(requireKey(villager, 'name', path), `${path}.name`);
    checkEnum(requireKey(villager, 'mode', path), VILLAGER_MODES, `${path}.mode`);
    checkPoint(requireKey(villager, 'pos', path), `${path}.pos`);
    checkEnum(requireKey(villager, 'facing', path), FACINGS, `${path}.facing`);
    checkBoolean(requireKey(villager, 'visible', path), `${path}.visible`);
    checkPointList(requireKey(villager, 'path', path), `${path}.path`);
    checkStringOrNull(requireKey(villager, 'taskId', path), `${path}.taskId`);
    checkStringOrNull(requireKey(villager, 'bubble', path), `${path}.bubble`, { allowEmpty: true });
    checkNumber(requireKey(villager, 'bubbleUntil', path), `${path}.bubbleUntil`, { min: 0 });
    checkNumber(requireKey(villager, 'saluteUntil', path), `${path}.saluteUntil`, { min: 0 });
    checkNumber(requireKey(villager, 'deliverUntil', path), `${path}.deliverUntil`, { min: 0 });
    checkNumber(requireKey(villager, 'nextRoutineAt', path), `${path}.nextRoutineAt`, { min: 0 });
    checkNumber(requireKey(villager, 'routineIndex', path), `${path}.routineIndex`, {
      min: 0, integer: true,
    });
    checkStringOrNull(requireKey(villager, 'routineLabel', path), `${path}.routineLabel`, {
      allowEmpty: true,
    });
    checkNumber(requireKey(villager, 'inPlaceUntil', path), `${path}.inPlaceUntil`, { min: 0 });
    checkPointList(requireKey(villager, 'pendingRoute', path), `${path}.pendingRoute`);
    checkStringOrNull(requireKey(villager, 'pendingBuildingId', path), `${path}.pendingBuildingId`);
  });

  // ---- 建筑
  const buildings = asArray(requireKey(world, 'buildings', 'world'), 'world.buildings');
  const buildingIds = new Set<string>();
  buildings.forEach((item, index) => {
    const path = `world.buildings[${index}]`;
    const building = asRecord(item, path);
    const instanceId = checkString(requireKey(building, 'instanceId', path), `${path}.instanceId`);
    if (buildingIds.has(instanceId)) fail(`${path}.instanceId`, `重复的实例 id ${instanceId}`);
    buildingIds.add(instanceId);
    checkString(requireKey(building, 'prefabId', path), `${path}.prefabId`);
    checkString(requireKey(building, 'displayName', path), `${path}.displayName`);
    checkEnum(requireKey(building, 'zone', path), ZONES, `${path}.zone`);
    checkPoint(requireKey(building, 'origin', path), `${path}.origin`, { integer: true });
    const footprint = asRecord(requireKey(building, 'footprint', path), `${path}.footprint`);
    checkNumber(requireKey(footprint, 'w', `${path}.footprint`), `${path}.footprint.w`, {
      min: 1, integer: true,
    });
    checkNumber(requireKey(footprint, 'h', `${path}.footprint`), `${path}.footprint.h`, {
      min: 1, integer: true,
    });
    checkPoint(requireKey(building, 'door', path), `${path}.door`, { integer: true });
    checkPoint(requireKey(building, 'workSpot', path), `${path}.workSpot`, { integer: true });
    // 烟囱是浮点格坐标（粒子挂点），允许小数。
    checkPoint(requireKey(building, 'chimney', path), `${path}.chimney`);
    checkEnum(requireKey(building, 'accent', path), WORK_ACCENTS, `${path}.accent`);
    checkEnum(requireKey(building, 'silhouette', path), SILHOUETTES, `${path}.silhouette`);
    const boundTask = requireKey(building, 'boundTask', path);
    if (boundTask !== null) checkEnum(boundTask, WORKSHOP_TASKS, `${path}.boundTask`);
    const palette = asRecord(requireKey(building, 'palette', path), `${path}.palette`);
    for (const slot of ['body', 'roof', 'trim', 'window'] as const) {
      checkNumber(requireKey(palette, slot, `${path}.palette`), `${path}.palette.${slot}`, { min: 0 });
    }
  });

  // ---- 道路 / 摆件
  checkPointList(requireKey(world, 'roads', 'world'), 'world.roads');
  const props = asArray(requireKey(world, 'props', 'world'), 'world.props');
  props.forEach((item, index) => {
    const path = `world.props[${index}]`;
    const prop = asRecord(item, path);
    checkString(requireKey(prop, 'id', path), `${path}.id`);
    checkEnum(requireKey(prop, 'kind', path), PROP_KINDS, `${path}.kind`);
    checkPoint(requireKey(prop, 'at', path), `${path}.at`, { integer: true });
    checkString(requireKey(prop, 'label', path), `${path}.label`, { allowEmpty: true });
  });

  // ---- 任务
  const tasks = asArray(requireKey(world, 'tasks', 'world'), 'world.tasks');
  tasks.forEach((item, index) => {
    const path = `world.tasks[${index}]`;
    const task = asRecord(item, path);
    checkString(requireKey(task, 'id', path), `${path}.id`);
    checkEnum(requireKey(task, 'kind', path), TASK_KINDS, `${path}.kind`);
    checkString(requireKey(task, 'summary', path), `${path}.summary`, { allowEmpty: true });
    checkStringOrNull(requireKey(task, 'buildingId', path), `${path}.buildingId`);
    checkEnum(requireKey(task, 'source', path), TASK_SOURCES, `${path}.source`);
    checkEnum(requireKey(task, 'status', path), TASK_STATUSES, `${path}.status`);
    checkNumber(requireKey(task, 'createdAt', path), `${path}.createdAt`, { min: 0 });
    const completedAt = checkNumberOrNull(requireKey(task, 'completedAt', path), `${path}.completedAt`, {
      min: 0,
    });
    // done 必须有完成时间：状态与时间不能自相矛盾。
    if (task.status === 'done' && completedAt === null) {
      fail(`${path}.completedAt`, 'done 任务必须有完成时间（不能是 null）');
    }
    checkStringOrNull(requireKey(task, 'blockedQuestion', path), `${path}.blockedQuestion`, {
      allowEmpty: true,
    });
    checkStringOrNull(requireKey(task, 'workerId', path), `${path}.workerId`);
  });

  // ---- 排队神谕
  const pending = asArray(requireKey(world, 'pending', 'world'), 'world.pending');
  pending.forEach((item, index) => {
    const path = `world.pending[${index}]`;
    const entry = asRecord(item, path);
    checkString(requireKey(entry, 'id', path), `${path}.id`);
    checkEnum(requireKey(entry, 'kind', path), TASK_KINDS, `${path}.kind`);
    checkString(requireKey(entry, 'summary', path), `${path}.summary`, { allowEmpty: true });
    checkNumber(requireKey(entry, 'requestedAt', path), `${path}.requestedAt`, { min: 0 });
    checkEnum(requireKey(entry, 'source', path), PENDING_SOURCES, `${path}.source`);
    checkString(requireKey(entry, 'villagerId', path), `${path}.villagerId`);
  });

  // ---- 铺子活动：四种 kind 各有不同必填键（联合类型，不能只看 kind）
  const activities = asArray(requireKey(world, 'activities', 'world'), 'world.activities');
  activities.forEach((item, index) => {
    const path = `world.activities[${index}]`;
    const entry = asRecord(item, path);
    checkString(requireKey(entry, 'buildingId', path), `${path}.buildingId`);
    const activity = asRecord(requireKey(entry, 'activity', path), `${path}.activity`);
    const kindPath = `${path}.activity.kind`;
    const kind = checkEnum(requireKey(activity, 'kind', `${path}.activity`), ACTIVITY_KINDS, kindPath);
    if (kind === 'operating') {
      checkString(requireKey(activity, 'taskId', `${path}.activity`), `${path}.activity.taskId`);
      checkNumber(requireKey(activity, 'startedAt', `${path}.activity`), `${path}.activity.startedAt`, {
        min: 0,
      });
      checkNumber(requireKey(activity, 'estimateMs', `${path}.activity`), `${path}.activity.estimateMs`, {
        min: 0,
      });
      checkBoolean(requireKey(activity, 'determinate', `${path}.activity`), `${path}.activity.determinate`);
    } else if (kind === 'overheating') {
      checkString(requireKey(activity, 'taskId', `${path}.activity`), `${path}.activity.taskId`);
      checkNumber(requireKey(activity, 'since', `${path}.activity`), `${path}.activity.since`, { min: 0 });
    } else if (kind === 'cooling') {
      checkString(requireKey(activity, 'taskId', `${path}.activity`), `${path}.activity.taskId`);
      checkNumber(requireKey(activity, 'until', `${path}.activity`), `${path}.activity.until`, { min: 0 });
    }
  });

  // ---- 在建工地
  const constructions = asArray(requireKey(world, 'constructions', 'world'), 'world.constructions');
  constructions.forEach((item, index) => {
    const path = `world.constructions[${index}]`;
    const entry = asRecord(item, path);
    checkString(requireKey(entry, 'buildingId', path), `${path}.buildingId`);
    const remaining = checkNumber(requireKey(entry, 'remainingMs', path), `${path}.remainingMs`, {
      min: 0,
    });
    const total = checkNumber(requireKey(entry, 'totalMs', path), `${path}.totalMs`, { min: 0 });
    if (remaining > total) fail(`${path}.remainingMs`, `不能大于 totalMs（${remaining} > ${total}）`);
  });

  // ---- 天气
  const weather = asRecord(requireKey(world, 'weather', 'world'), 'world.weather');
  checkEnum(requireKey(weather, 'kind', 'world.weather'), WEATHER_KINDS, 'world.weather.kind');
  const intensity = checkNumber(
    requireKey(weather, 'intensity', 'world.weather'), 'world.weather.intensity', { min: 0 },
  );
  if (intensity > 1) fail('world.weather.intensity', `必须在 0..1，实际 ${intensity}`);
  checkNumberOrNull(
    requireKey(weather, 'remainingMs', 'world.weather'), 'world.weather.remainingMs', { min: 0 },
  );

  // ---- 神恩
  const faith = asRecord(requireKey(world, 'faith', 'world'), 'world.faith');
  checkNumber(requireKey(faith, 'points', 'world.faith'), 'world.faith.points', {
    min: 0, integer: true,
  });
  checkNumber(requireKey(faith, 'milestones', 'world.faith'), 'world.faith.milestones', {
    min: 0, integer: true,
  });

  // ---- 已结算（去重/分账真相）
  const settled = asArray(requireKey(world, 'settled', 'world'), 'world.settled');
  const settledIds = new Set<string>();
  settled.forEach((item, index) => {
    const path = `world.settled[${index}]`;
    const record = asRecord(item, path);
    const id = checkString(requireKey(record, 'id', path), `${path}.id`);
    if (settledIds.has(id)) fail(`${path}.id`, `重复的已结算 id ${id}`);
    settledIds.add(id);
    checkEnum(requireKey(record, 'source', path), SETTLED_SOURCES, `${path}.source`);
    checkString(requireKey(record, 'workerId', path), `${path}.workerId`);
    checkNumberOrNull(requireKey(record, 'completedAt', path), `${path}.completedAt`, { min: 0 });
    checkEnum(
      requireKey(record, 'completedAtClock', path), SETTLED_CLOCKS, `${path}.completedAtClock`,
    );
  });

  // ---- done 任务必须在 settled 里（写档不变量，ADR_SAVE §2.1）
  for (const [index, item] of tasks.entries()) {
    const task = asRecord(item, `world.tasks[${index}]`);
    if (task.status === 'done' && !settledIds.has(String(task.id))) {
      fail(`world.tasks[${index}]`, `done 任务 ${String(task.id)} 缺少 settled 记录`);
    }
  }
}

/** meta 三字段的类型/范围（指纹值另在 assertSaveV1 里比常量）。 */
function validateMetaFields(raw: Record<string, unknown>): void {
  const meta = asRecord(raw.meta, 'meta');
  checkNumber(requireKey(meta, 'savedAtWallClock', 'meta'), 'meta.savedAtWallClock', { min: 0 });
  checkBoolean(requireKey(meta, 'savedReconciled', 'meta'), 'meta.savedReconciled');
  checkString(requireKey(meta, 'schemaFingerprint', 'meta'), 'meta.schemaFingerprint');
}

/**
 * 校验 v1 单一入口（task-32）：字段级类型/范围/存在性 → 形状/嵌套键集合 → 路径集合包含。
 * 失败一律抛错，由调用方按坏档处理；**绝不静默补默认值**。
 */
export function validateSaveV1(raw: unknown): asserts raw is WorldSaveV1 {
  if (!isObject(raw)) fail('存档', '不是对象');
  validateMetaFields(raw);
  validateWorldFields(raw);
}

/**
 * 校验 v1 的**唯一入口**（task-32）：三层串联，逐层防不同的东西。
 *
 * 1. **字段级类型/范围/存在性**（`validateSaveV1`）：防「删字段」「换错类型」「值越界」。
 *    路径集合会因删字段而合法缩小，所以这一半必须逐字段点名检查；缺失即拒，
 *    绝不静默补默认值。
 * 2. **形状/嵌套键集合**：`world.tasks[]` / `world.settled[]` 键集合必须与冻结常量逐键一致。
 * 3. **路径集合包含**：存档里出现的每条叶子路径都必须落在允许全集
 *    （冻结 99 条 + 4 条可选）里，越界即拒并指名越界路径——只防「新增字段」。
 *
 * 三层各管一段，合起来才等价于"v1 形状完全没动"；任何一层失败都按坏档处理。
 */
export function assertSaveV1(raw: unknown): asserts raw is WorldSaveV1 {
  if (!isObject(raw)) throw new Error('存档不是对象');
  if (raw.schemaVersion !== CURRENT_SCHEMA_VERSION) {
    throw new Error(`schemaVersion 不是 ${CURRENT_SCHEMA_VERSION}`);
  }
  const world = raw.world;
  if (!isObject(world)) throw new Error('存档缺 world');
  const meta = raw.meta;
  if (!isObject(meta)) throw new Error('存档缺 meta');
  // meta.schemaFingerprint 是冻结产物的自证字段：缺失/不是字符串一律拒绝，避免
  // 「删掉它就能绕过旧检查」；存在但与常量不等也拒绝（被改过或字段漂移）。
  if (typeof meta.schemaFingerprint !== 'string') {
    throw new Error('存档缺 meta.schemaFingerprint（v1 要求该字段存在）');
  }
  if (meta.schemaFingerprint !== SCHEMA_V1_FINGERPRINT) {
    throw new Error('存档指纹不符（字段变了但版本没升，或存档损坏）');
  }
  // 第 1 层：字段级类型/范围/存在性（含 weather/faith、各数组元素、done⊆settled）。
  validateSaveV1(raw);
  // 第 2 层：嵌套键集合冻结（多一个运行时字段就是漂移）。
  for (const record of world.settled as unknown[]) {
    assertKeys(record as Record<string, unknown>, SAVE_SETTLED_KEYS, 'world.settled[]');
  }
  for (const task of world.tasks as unknown[]) {
    assertKeys(task as Record<string, unknown>, SAVE_TASK_KEYS, 'world.tasks[]');
  }
  // 第 3 层：路径集合包含校验——只防「新增路径」（顶层/world/meta/数组元素里多字段）。
  const outside = collectSavePaths(raw).filter((path) => !SAVE_V1_PATH_SET.has(path));
  if (outside.length > 0) {
    throw new Error(
      `存档字段漂移：出现 v1 允许集合之外的路径：${outside.join(', ')}（字段变了但版本没升）`,
    );
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
