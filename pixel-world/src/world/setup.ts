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
export const GRID_WIDTH = 30;
export const GRID_HEIGHT = 20;
export const BORDER = 1;

/** 世界尺寸与分区。改这里等于改世界规格，测试会跟着报警。 */
export const WORLD_SPEC: GridSpec = {
  width: GRID_WIDTH,
  height: GRID_HEIGHT,
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

export const ALTAR: GridPoint = { x: 15, y: 9 };

/** 常驻铺子按活绑定，扩建样板不参与开局落位。 */
const RESIDENT_PREFABS = new Set<string>(['code_forge', 'star_archive', 'ink_house']);

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

/** 常驻铺子开局落位。扩建样板不在这里出现。 */
export function buildWorld(
  blueprints: ReadonlyMap<string, PrefabBlueprint>,
): BuiltWorld {
  const coverage = validateCoverage(blueprints);
  if (coverage.length > 0) throw new WorldSetupError(coverage.join('；'));

  const grid = new WorldGrid(WORLD_SPEC);
  const buildings: PlacedBuilding[] = [];

  for (const blueprint of blueprints.values()) {
    if (!RESIDENT_PREFABS.has(blueprint.id)) continue;
    const placed = placeBlueprint(grid, blueprint, `${blueprint.id}#1`, ALTAR);
    if (placed === null) {
      throw new WorldSetupError(`${blueprint.displayName} 在 ${blueprint.zone} 找不到合规空地`);
    }
    buildings.push(placed.building);
  }

  assertWorldInvariants(grid, buildings);
  return { grid, buildings, altar: ALTAR };
}

/** 造物主的一次落位：选址、占格、铺路。空间不足时返回 null，不改账本。 */
export function placeBlueprint(
  grid: WorldGrid,
  blueprint: PrefabBlueprint,
  instanceId: string,
  anchor: GridPoint,
): SpawnResult | null {
  const origin = grid.findBestSlot(blueprint, anchor);
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
export function rebuildGridFromPlacements(buildings: readonly PlacedBuilding[]): WorldGrid {
  const grid = new WorldGrid(WORLD_SPEC);
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
): BuiltWorld {
  const resolved = blueprints === undefined ? [...buildings] : rehydratePalettes(buildings, blueprints);
  const grid = rebuildGridFromPlacements(resolved);
  assertWorldInvariants(grid, resolved);
  return { grid, buildings: resolved, altar: ALTAR };
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
): void {
  const problems: string[] = [];
  const workshops = buildings.filter((item) => item.boundTask !== null);
  if (workshops.length !== WORKSHOP_TASKS.length) {
    problems.push(`接活的铺子 ${workshops.length} 栋，预算为 ${WORKSHOP_TASKS.length}`);
  }
  const zones = new Set(workshops.map((item) => item.zone));
  if (zones.size !== workshops.length) problems.push('两栋铺子挤在同一个分区');

  for (const building of buildings) {
    if (grid.zoneOf(building.door) === 'altar') {
      problems.push(`${building.displayName} 的门口开在祭坛区`);
    }
    if (!grid.isWalkable(building.door)) {
      problems.push(`${building.displayName} 的门口不可通行`);
    }
    const reachable = grid.path(ALTAR, building.door);
    if (reachable.length === 0) {
      problems.push(`${building.displayName} 与祭坛之间没有通路`);
    }
  }
  if (problems.length > 0) throw new WorldSetupError(problems.join('；'));
}
