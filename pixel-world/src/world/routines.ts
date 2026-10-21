/** 日常作息：没活干的时候她去哪、待多久。
 *
 * 这里是"世界不惩罚你"的那一半——她过日子不花 token，也不因为你没来而退化。
 * 纯函数决定下一个落点，模拟层只负责走过去。
 */

import type { GridPoint, PlacedBuilding, Weather } from './types.ts';

export type RoutineId = 'altar' | 'garden' | 'promenade' | 'shelter';

export interface RoutineBeat {
  readonly id: RoutineId;
  readonly label: string;
  readonly target: GridPoint;
  readonly dwellMs: number;
}

const DWELL_MS = 5200;

/** 一圈日常：祭坛 → 农田边 → 街角 → 回祭坛。不涉及铺子，铺子只在干活时进。 */
export function routineBeats(altar: GridPoint, grid: RoutineMap): readonly RoutineBeat[] {
  const garden = grid.nearestWalkable({ x: altar.x - 8, y: altar.y + 4 });
  const promenade = grid.nearestWalkable({ x: altar.x + 8, y: altar.y - 3 });
  return [
    { id: 'altar', label: '在祭坛边坐着', target: altar, dwellMs: DWELL_MS },
    {
      id: 'garden',
      label: '去看了一眼农田',
      target: garden ?? altar,
      dwellMs: DWELL_MS * 1.4,
    },
    {
      id: 'promenade',
      label: '在街口站着看了一会儿',
      target: promenade ?? altar,
      dwellMs: DWELL_MS,
    },
    { id: 'altar', label: '回到祭坛等神谕', target: altar, dwellMs: DWELL_MS },
  ];
}

export interface RoutineMap {
  nearestWalkable(target: GridPoint): GridPoint | null;
}

/** 下雨时她只想去能避雨的地方：最近的门廊。 */
export function shelterTarget(
  buildings: readonly PlacedBuilding[],
  from: GridPoint,
): GridPoint | null {
  let best: GridPoint | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const building of buildings) {
    const distance = Math.abs(building.door.x - from.x) + Math.abs(building.door.y - from.y);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = building.door;
    }
  }
  return best;
}

export function routineFor(
  weather: Weather,
  beats: readonly RoutineBeat[],
  index: number,
): RoutineBeat {
  if (weather === 'rain') {
    const altar = beats[0];
    return {
      id: 'shelter',
      label: '躲到屋檐下看雨',
      target: altar.target,
      dwellMs: DWELL_MS,
    };
  }
  return beats[index % beats.length];
}
