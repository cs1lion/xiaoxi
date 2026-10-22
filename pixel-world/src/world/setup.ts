/** 把蓝图、网格和固定分区拼成一个可跑的世界。
 *
 * 分区是写死的世界规格（造物主的 L0 层），建筑位置由空间分配器算，
 * 不允许手填坐标。这样换一张蓝图表，世界仍然成立。
 */

import { WorldGrid, type GridSpec } from './grid.ts';
import { validateCoverage } from './blueprints.ts';
import { WORKSHOP_TASKS } from './tasks.ts';
import type { GridPoint, PlacedBuilding, PrefabBlueprint } from './types.ts';

export const TILE_PX = 16;
export const BORDER = 1;

/**
 * 图块尺寸不动，只扩大地图。
 *
 * 「把图块放大到 32px」与「扩大世界」互相抵消：同样一块屏幕，图块翻倍就只看得到
 * 一半的地图内容，还得重画全部地形纹理。所以大世界靠更多格子，不靠更大的格子。
 */

/**
 * 旧规格：30×20。**保留为测试夹具**——20 处既有单测依赖它，
 * 换掉等于把已有验收数字全部作废。生产入口用 `LARGE_WORLD_SPEC`。
 */
export const SMALL_WORLD_SPEC: GridSpec = {
  width: 30,
  height: 20,
  border: BORDER,
  zones: [
    { id: 'industry', x: 1, y: 1, w: 12, h: 8 },
    { id: 'scholarly', x: 17, y: 1, w: 12, h: 8 },
    { id: 'street', x: 13, y: 1, w: 4, h: 6 },
    { id: 'altar', x: 13, y: 7, w: 4, h: 4 },
    { id: 'street', x: 13, y: 11, w: 4, h: 8 },
    { id: 'living', x: 1, y: 13, w: 12, h: 6 },
    { id: 'agriculture', x: 17, y: 13, w: 12, h: 6 },
  ],
};

/** 兼容旧调用点与旧测试：默认规格仍是 30×20。 */
export const WORLD_SPEC: GridSpec = SMALL_WORLD_SPEC;
export const GRID_WIDTH = SMALL_WORLD_SPEC.width;
export const GRID_HEIGHT = SMALL_WORLD_SPEC.height;

/**
 * 大世界：80×60 格，1280×960 像素，面积是旧世界的 8 倍。
 *
 * 分区是同心圆的近似：中心是祭坛，四角方向各留一条通向郊野的路。
 * 这里只给矩形分区，群落（离中心多远）由 `biomes.ts` 单独判定——
 * 两者职责不同：分区决定能建什么，群落决定看起来像哪一带。
 */
export const LARGE_WORLD_SPEC: GridSpec = {
  width: 80,
  height: 60,
  border: BORDER,
  zones: [
    // 核心：祭坛 + 中心广场。
    // 街道只是**路**，不是广场：旧规格里 street 是 4 格宽的十字，
    // 大世界照抄比例会让整片中心变成铺装，草地被切碎。
    { id: 'altar', x: 37, y: 27, w: 6, h: 6 },
    // 十字主干道：3 格宽，从镇中心一直铺到地图边缘，
    // 这样 A* 才有"先上大路"的落点，玩家也看得出哪里是路。
    { id: 'street', x: 38, y: 1, w: 4, h: 26 },
    { id: 'street', x: 38, y: 33, w: 4, h: 26 },
    { id: 'street', x: 1, y: 28, w: 36, h: 4 },
    { id: 'street', x: 43, y: 28, w: 36, h: 4 },
    // 工坊环：北工业、东书斋、南农田、西生活。
    // 保持旧规格的相对大小（每块约 12×8），不按地图面积等比放大——
    // 放大会把铺子之间的空地全吃掉，世界看起来像一块工地。
    { id: 'industry', x: 32, y: 12, w: 16, h: 9 },
    { id: 'scholarly', x: 53, y: 25, w: 12, h: 10 },
    { id: 'agriculture', x: 32, y: 40, w: 16, h: 9 },
    { id: 'living', x: 15, y: 25, w: 12, h: 10 },
    // 郊野：外围可建区，比镇内小，让草地与林子占主导
    { id: 'industry', x: 9, y: 9, w: 14, h: 9 },
    { id: 'scholarly', x: 57, y: 9, w: 14, h: 9 },
    { id: 'agriculture', x: 9, y: 42, w: 14, h: 9 },
    { id: 'living', x: 57, y: 42, w: 14, h: 9 },
  ],
};

/** 大世界的祭坛：规格正中心。 */
export const LARGE_ALTAR: GridPoint = { x: 40, y: 30 };

export const ALTAR: GridPoint = { x: 15, y: 9 };

/**
 * 常驻铺子：镇内三栋（本地确定性工作）+ 郊野三栋（探索性工作）。
 * 扩建样板 `workshop_plot` 不参与开局落位。
 */
const TOWN_PREFABS = new Set<string>(['code_forge', 'star_archive', 'ink_house']);
const WILD_PREFABS = new Set<string>(['crawler_mine', 'ink_mill', 'gear_tower']);
const RESIDENT_PREFABS = new Set<string>([...TOWN_PREFABS, ...WILD_PREFABS]);

export class WorldSetupError extends Error {}

export interface BuiltWorld {
  readonly grid: WorldGrid;
  readonly buildings: readonly PlacedBuilding[];
  readonly altar: GridPoint;
}

export interface SpawnResult {
  readonly building: PlacedBuilding;
  /** 从门口铺到主路的小径，由造物主顺手铺好。 */
  readonly road: readonly GridPoint[];
}

/** 某处为什么不能建。造物模式的幽灵预览要把原因说给玩家听。 */
export type PlacementProblem =
  | 'edge'
  | 'outOfZone'
  | 'occupied'
  | 'margin'
  | 'doorBlocked';

export type PlacementCheck =
  | { readonly ok: true }
  | { readonly ok: false; readonly reason: PlacementProblem };

/** 把拒绝理由翻译成玩家读得懂的一句话。 */
export function hintFor(reason: PlacementProblem): string {
  switch (reason) {
    case 'edge':
      return '太靠边了，楼会出界。';
    case 'outOfZone':
      return '这种铺子不能建在这个区。';
    case 'occupied':
      return '这里已经有东西了。';
    case 'margin':
      return '离别的建筑太近，得留出空地。';
    case 'doorBlocked':
      return '门口被堵住了，人走不进去。';
  }
}

/**
 * 能不能把这份蓝图落在 `origin`。
 *
 * 规则与 `findBestSlot` 内部逐条一致——幽灵预览与真实落位必须用同一套判定，
 * 否则玩家会看到"显示可以、确认却被拒"。
 *
 * 先后顺序按"玩家最容易理解"排：先出界，再分区，再占地，再留白，最后门口。
 */
export function checkPlacement(
  grid: WorldGrid,
  blueprint: PrefabBlueprint,
  origin: GridPoint,
): PlacementCheck {
  const { w, h } = blueprint.footprint;
  const margin = blueprint.clearMargin;

  // 出界：占地本身必须完整落在世界里。
  if (
    origin.x < 0 ||
    origin.y < 0 ||
    origin.x + w > grid.width ||
    origin.y + h > grid.height
  ) {
    return { ok: false, reason: 'edge' };
  }

  // 分区：整块占地都必须在蓝图声明的分区里。
  for (let y = origin.y; y < origin.y + h; y += 1) {
    for (let x = origin.x; x < origin.x + w; x += 1) {
      if (grid.zoneOf({ x, y }) !== blueprint.zone) {
        return { ok: false, reason: 'outOfZone' };
      }
    }
  }

  // 占地被占：先单独判一次，好给出比"留白不足"更准确的原因。
  for (let y = origin.y; y < origin.y + h; y += 1) {
    for (let x = origin.x; x < origin.x + w; x += 1) {
      if (!grid.isBuildable({ x, y })) return { ok: false, reason: 'occupied' };
    }
  }

  // 留白：四周 margin 圈内也必须空着（含越界，边界外不可建）。
  if (!grid.canPlace(origin, w, h, margin)) {
    return { ok: false, reason: 'margin' };
  }

  // 门口：必须可建，否则人走不进去。
  const door = { x: origin.x + blueprint.door.x, y: origin.y + blueprint.door.y };
  if (!grid.isBuildable(door)) return { ok: false, reason: 'doorBlocked' };

  return { ok: true };
}

/** 常驻铺子开局落位。扩建样板不在这里出现。 */
/**
 * 建一个世界。
 *
 * `spec` 与 `altar` 都省略时用旧规格 30×20——20 处既有测试依赖这个默认值，
 * 不改它们。生产入口传 `LARGE_WORLD_SPEC` + `LARGE_ALTAR`（见 `buildLargeWorld`）。
 */
export function buildWorld(
  blueprints: ReadonlyMap<string, PrefabBlueprint>,
  spec: GridSpec = SMALL_WORLD_SPEC,
  altar: GridPoint = ALTAR,
): BuiltWorld {
  const coverage = validateCoverage(blueprints);
  if (coverage.length > 0) throw new WorldSetupError(coverage.join('；'));

  // 大世界才生成郊野林地：旧规格没有"郊野"，也不该突然长树。
  const grid = new WorldGrid(
    spec,
    spec === LARGE_WORLD_SPEC ? { center: altar } : undefined,
  );
  const buildings: PlacedBuilding[] = [];

  // 小世界放不下六栋：郊野铺子只在大世界里落位。
  // 小规格继续服务 20 处既有测试，行为与以前完全一致。
  const wildAllowed = spec === LARGE_WORLD_SPEC;
  for (const blueprint of blueprints.values()) {
    if (!RESIDENT_PREFABS.has(blueprint.id)) continue;
    if (WILD_PREFABS.has(blueprint.id) && !wildAllowed) continue;
    // 郊野铺子锚到地图外侧，镇内铺子锚到祭坛：否则六栋全挤在中心，
    // 「郊野」只是名义上的（实测离祭坛只有 12–18 格，仍在工坊环里）。
    const anchor = WILD_PREFABS.has(blueprint.id)
      ? { x: altar.x + Math.sign(blueprint.zone === 'living' ? -1 : 1) * 26, y: altar.y }
      : altar;
    const placed = placeBlueprint(grid, blueprint, `${blueprint.id}#1`, anchor);
    if (placed === null) {
      throw new WorldSetupError(`${blueprint.displayName} 在 ${blueprint.zone} 找不到合规空地`);
    }
    buildings.push(placed.building);
  }

  assertWorldInvariants(grid, buildings, altar);
  return { grid, buildings, altar };
}

/** 生产用的大世界：80×60，常驻铺子按郊野锚点重新落位。 */
export function buildLargeWorld(
  blueprints: ReadonlyMap<string, PrefabBlueprint>,
): BuiltWorld {
  return buildWorld(blueprints, LARGE_WORLD_SPEC, LARGE_ALTAR);
}

/**
 * 把一份**已通过 schema 校验**的造物意图落进世界。
 *
 * 这是 schema 与世界的唯一接缝：意图里的 `templateId` 换成蓝图，
 * `palette` 覆盖模板配色，位置交给 `placeBlueprint` 走同一套空间校验。
 *
 * 调它之前必须先 `parseCreationPlan`。这里不重复校验形状——
 * 两道校验分开，是因为它们回答的问题不同：形状 vs 空间。
 */
export function placeFromPlan(
  world: BuiltWorld,
  plan: {
    readonly templateId: string;
    readonly at: GridPoint;
    readonly palette: PrefabBlueprint['palette'];
  },
  instanceId: string,
  blueprints: ReadonlyMap<string, PrefabBlueprint>,
): SpawnResult | null {
  const template = blueprints.get(plan.templateId);
  if (template === undefined) return null;

  // 意图里的配色覆盖模板：模板只提供形状，颜色由造物主决定。
  return placeBlueprint(
    world.grid,
    { ...template, palette: plan.palette },
    instanceId,
    world.altar,
    plan.at,
  );
}

/**
 * 造物主的一次落位：选址、占格、铺路。空间不足时返回 null，不改账本。
 *
 * `at` 省略时走 `findBestSlot`（自动选最靠近锚点的合规空地）；
 * 给了 `at` 就是玩家指定的位置，先过 `checkPlacement`，不合法直接返回 null。
 * 两条路径最终都经过同一套判定。
 */
export function placeBlueprint(
  grid: WorldGrid,
  blueprint: PrefabBlueprint,
  instanceId: string,
  anchor: GridPoint,
  at?: GridPoint,
  source?: { readonly taskId: string; readonly summary: string },
): SpawnResult | null {
  let origin: GridPoint | null;
  if (at === undefined) {
    origin = grid.findBestSlot(blueprint, anchor);
  } else {
    const check = checkPlacement(grid, blueprint, at);
    origin = check.ok ? at : null;
  }
  if (origin === null) return null;

  const door = { x: origin.x + blueprint.door.x, y: origin.y + blueprint.door.y };
  const placed = grid.place({
    instanceId,
    origin,
    w: blueprint.footprint.w,
    h: blueprint.footprint.h,
    door,
  });
  if (!placed) return null;

  const workSpot = blueprint.workSpots[0];
  const building: PlacedBuilding = {
    instanceId,
    prefabId: blueprint.id,
    displayName: blueprint.displayName,
    zone: blueprint.zone,
    origin,
    footprint: blueprint.footprint,
    door,
    workSpot: { x: origin.x + workSpot.x, y: origin.y + workSpot.y },
    chimney: { x: origin.x + blueprint.chimney.x, y: origin.y + blueprint.chimney.y },
    accent: blueprint.accent,
    silhouette: blueprint.silhouette,
    boundTask: blueprint.boundTask,
    palette: blueprint.palette,
    sourceTaskId: source?.taskId ?? null,
    sourceSummary: source?.summary ?? null,
  };

  const road = paveFrom(grid, door, anchor);
  return { building, road };
}

/** 从门口往祭坛铺一条小径，只铺没被占的空地。 */
export function paveFrom(
  grid: WorldGrid,
  door: GridPoint,
  anchor: GridPoint,
): readonly GridPoint[] {
  const route = grid.path(door, anchor);
  const road: GridPoint[] = [];
  for (const cell of route) {
    if (grid.zoneOf(cell) === 'altar') continue;
    if (grid.at(cell)?.occupant !== null) continue;
    road.push(cell);
  }
  return road;
}

/** 读档重建网格：只按存档 buildings 顺序重放落位，不存 cells（ADR_SAVE §1.2）。 */
export function rebuildGridFromPlacements(
  buildings: readonly PlacedBuilding[],
  spec: GridSpec = SMALL_WORLD_SPEC,
): WorldGrid {
  const grid = new WorldGrid(spec);
  for (const building of buildings) {
    const placed = grid.place({
      instanceId: building.instanceId,
      origin: building.origin,
      w: building.footprint.w,
      h: building.footprint.h,
      door: building.door,
    });
    if (!placed) {
      throw new WorldSetupError(`读档重建失败：${building.displayName} 的落位放不进网格`);
    }
  }
  return grid;
}

/** 蓝图表的两种形态（数组或 Map）都能收：调用方不必先转一手。 */
export type BlueprintSource =
  | readonly PrefabBlueprint[]
  | ReadonlyMap<string, PrefabBlueprint>;

function indexBlueprints(blueprints: BlueprintSource): Map<string, PrefabBlueprint> {
  if (blueprints instanceof Map) return new Map(blueprints);
  return new Map((blueprints as readonly PrefabBlueprint[]).map((item) => [item.id, item]));
}

/** 读档后的世界：网格按存档重放，分区/铺子不变量仍要过一遍。
 *
 * 颜色是“显示事实”，以当前 prefab 表为准：存档里的 palette 可能是旧色表留下的，
 * 直接拿来渲染会让老档永远停在旧配色（D6-A P1）。所以按 prefabId 重解，
 * 找不到 prefab（比如蓝图被删）才回退存量 palette 并告警。
 * 存档 schema 不动：v1 已冻结，读档只做内存里的显示修正。
 */
export function buildWorldFromSave(
  buildings: readonly PlacedBuilding[],
  blueprints?: BlueprintSource,
  spec: GridSpec = SMALL_WORLD_SPEC,
  altar: GridPoint = ALTAR,
): BuiltWorld {
  const resolved = blueprints === undefined ? [...buildings] : rehydratePalettes(buildings, blueprints);
  const grid = rebuildGridFromPlacements(resolved, spec);
  assertWorldInvariants(grid, resolved, altar);
  return { grid, buildings: resolved, altar };
}

/** 按当前 prefab 表重解每栋楼的 palette；prefab 缺失时保留存档值并告警。 */
export function rehydratePalettes(
  buildings: readonly PlacedBuilding[],
  blueprints: BlueprintSource,
): PlacedBuilding[] {
  const byId = indexBlueprints(blueprints);
  const seenMissing = new Set<string>();
  return buildings.map((building) => {
    const blueprint = byId.get(building.prefabId);
    if (blueprint === undefined) {
      if (!seenMissing.has(building.prefabId)) {
        seenMissing.add(building.prefabId);
        console.warn(
          `[setup] 读档时找不到蓝图 ${building.prefabId}，${building.displayName} 沿用存档里的颜色（可能是旧版/外部存档）。`,
        );
      }
      return building;
    }
    return { ...building, palette: blueprint.palette };
  });
}

/** 世界预算校验：像 check_protocol 一样，不合规就不许开工。 */
export function assertWorldInvariants(
  grid: WorldGrid,
  buildings: readonly PlacedBuilding[],
  altar: GridPoint = ALTAR,
): void {
  const problems: string[] = [];
  const workshops = buildings.filter((item) => item.boundTask !== null);
  // 每种活 1–2 栋：镇内 + 郊野。总预算随世界规格变化，不再写死。
  const byKind = new Map<string, number>();
  for (const building of workshops) {
    if (building.boundTask === null) continue;
    byKind.set(building.boundTask, (byKind.get(building.boundTask) ?? 0) + 1);
  }
  for (const kind of WORKSHOP_TASKS) {
    const count = byKind.get(kind) ?? 0;
    if (count < 1 || count > 2) {
      problems.push(`承接 ${kind} 的铺子 ${count} 栋，必须是 1 或 2 栋`);
    }
  }
  // 同分区的两栋必须是「一栋镇内、一栋郊野」：同类铺子挨着会互相抢活，
  // 而镇内与郊野本就该分开。
  const zones = new Map<string, number>();
  for (const building of workshops) {
    zones.set(building.zone, (zones.get(building.zone) ?? 0) + 1);
  }
  for (const [zone, count] of zones) {
    if (count > 2) problems.push(`分区 ${zone} 里有 ${count} 栋铺子，最多 2 栋`);
  }

  for (const building of buildings) {
    if (grid.zoneOf(building.door) === 'altar') {
      problems.push(`${building.displayName} 的门口开在祭坛区`);
    }
    if (!grid.isWalkable(building.door)) {
      problems.push(`${building.displayName} 的门口不可通行`);
    }
    const reachable = grid.path(altar, building.door);
    if (reachable.length === 0) {
      problems.push(`${building.displayName} 与祭坛之间没有通路`);
    }
  }
  if (problems.length > 0) throw new WorldSetupError(problems.join('；'));
}
