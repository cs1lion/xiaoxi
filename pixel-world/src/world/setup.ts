/** 把蓝图、网格和固定分区拼成一个可跑的世界。
 *
 * 分区是写死的世界规格（造物主的 L0 层），建筑位置由空间分配器算，
 * 不允许手填坐标。这样换一张蓝图表，世界仍然成立。
 */

import { WorldGrid, type GridSpec } from './grid.ts';
import { validateCoverage } from './blueprints.ts';
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
    { id: 'altar', x: 12, y: 8, w: 6, h: 5 },
    { id: 'industry', x: 2, y: 2, w: 8, h: 7 },
    { id: 'scholarly', x: 20, y: 2, w: 8, h: 7 },
    { id: 'living', x: 2, y: 12, w: 8, h: 6 },
    { id: 'agriculture', x: 20, y: 12, w: 8, h: 6 },
    { id: 'street', x: 11, y: 2, w: 8, h: 5 },
  ],
};

export const ALTAR: GridPoint = { x: 15, y: 10 };

export class WorldSetupError extends Error {}

export interface BuiltWorld {
  readonly grid: WorldGrid;
  readonly buildings: readonly PlacedBuilding[];
  readonly altar: GridPoint;
}

/** 每个分区放一栋铺子；位置由 findBestSlot 决定，锚点是祭坛。 */
export function buildWorld(
  blueprints: ReadonlyMap<string, PrefabBlueprint>,
): BuiltWorld {
  const coverage = validateCoverage(blueprints);
  if (coverage.length > 0) throw new WorldSetupError(coverage.join('；'));

  const grid = new WorldGrid(WORLD_SPEC);
  const buildings: PlacedBuilding[] = [];

  for (const blueprint of blueprints.values()) {
    const origin = grid.findBestSlot(blueprint, ALTAR);
    if (origin === null) {
      throw new WorldSetupError(`${blueprint.displayName} 在 ${blueprint.zone} 找不到合规空地`);
    }
    const instanceId = `${blueprint.id}#1`;
    const door = { x: origin.x + blueprint.door.x, y: origin.y + blueprint.door.y };
    const placed = grid.place({
      instanceId,
      origin,
      w: blueprint.footprint.w,
      h: blueprint.footprint.h,
      door,
    });
    if (!placed) throw new WorldSetupError(`${blueprint.displayName} 落位被网格拒绝`);

    const workSpot = blueprint.workSpots[0];
    buildings.push({
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
    });
  }

  assertWorldInvariants(grid, buildings);
  return { grid, buildings, altar: ALTAR };
}

/** 世界预算校验：像 check_protocol 一样，不合规就不许开工。 */
export function assertWorldInvariants(
  grid: WorldGrid,
  buildings: readonly PlacedBuilding[],
): void {
  const problems: string[] = [];
  if (buildings.length !== 3) problems.push(`铺子数量 ${buildings.length}，预算为 3`);
  const zones = new Set(buildings.map((item) => item.zone));
  if (zones.size !== buildings.length) problems.push('两栋铺子挤在同一个分区');

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
