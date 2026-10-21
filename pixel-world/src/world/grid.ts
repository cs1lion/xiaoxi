/** 网格账本：分区、占用、选址与 A*。
 *
 * 纯逻辑，不依赖 Phaser。世界的一切空间约束都在这里结算；
 * 渲染层只读快照，不许自己改坐标。
 */

import type { GridPoint, PrefabBlueprint, TileCell, ZoneId } from './types.ts';

const ZONE_DEFAULT: ZoneId = 'street';

export interface ZoneRegion {
  readonly id: ZoneId;
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

export interface GridSpec {
  readonly width: number;
  readonly height: number;
  /** 分区矩形；未覆盖到的格子落 street。 */
  readonly zones: readonly ZoneRegion[];
  /** 四周这几圈是不可通行的树篱。 */
  readonly border: number;
}

export interface PlacementRequest {
  readonly instanceId: string;
  readonly origin: GridPoint;
  readonly w: number;
  readonly h: number;
  /** 门口格，必须落在占地之外。 */
  readonly door: GridPoint;
}

export function inRect(p: GridPoint, x: number, y: number, w: number, h: number): boolean {
  return p.x >= x && p.x < x + w && p.y >= y && p.y < y + h;
}

export class WorldGrid {
  readonly width: number;
  readonly height: number;
  private readonly cells: TileCell[];

  constructor(spec: GridSpec) {
    this.width = spec.width;
    this.height = spec.height;
    this.cells = new Array<TileCell>(spec.width * spec.height);
    for (let y = 0; y < spec.height; y += 1) {
      for (let x = 0; x < spec.width; x += 1) {
        const zone = this.zoneAt(spec, x, y);
        const edge =
          x < spec.border ||
          y < spec.border ||
          x >= spec.width - spec.border ||
          y >= spec.height - spec.border;
        this.cells[y * spec.width + x] = {
          zone,
          terrain: edge ? 'tree' : terrainFor(zone),
          walkable: !edge,
          occupant: null,
        };
      }
    }
  }

  private zoneAt(spec: GridSpec, x: number, y: number): ZoneId {
    for (const z of spec.zones) {
      if (inRect({ x, y }, z.x, z.y, z.w, z.h)) return z.id;
    }
    return ZONE_DEFAULT;
  }

  inBounds(p: GridPoint): boolean {
    return p.x >= 0 && p.y >= 0 && p.x < this.width && p.y < this.height;
  }

  at(p: GridPoint): TileCell | undefined {
    if (!this.inBounds(p)) return undefined;
    return this.cells[p.y * this.width + p.x];
  }

  zoneOf(p: GridPoint): ZoneId {
    return this.at(p)?.zone ?? ZONE_DEFAULT;
  }

  isWalkable(p: GridPoint): boolean {
    return this.at(p)?.walkable === true;
  }

  /** 能不能往上放建筑：在界内、不是树、没人占。 */
  isBuildable(p: GridPoint): boolean {
    const cell = this.at(p);
    if (!cell) return false;
    return cell.occupant === null && cell.terrain !== 'tree';
  }

  /** 树篱与建筑之外的空地数量，给校验器用。 */
  countOccupied(): number {
    return this.cells.filter((cell) => cell.occupant !== null).length;
  }

  /** 把一栋建筑落进账本。门口必须在占地外，否则返回 false。 */
  place(req: PlacementRequest): boolean {
    if (inRect(req.door, req.origin.x, req.origin.y, req.w, req.h)) return false;
    if (!this.inBounds(req.door)) return false;
    if (!this.canPlace(req.origin, req.w, req.h, 0)) return false;
    for (let y = req.origin.y; y < req.origin.y + req.h; y += 1) {
      for (let x = req.origin.x; x < req.origin.x + req.w; x += 1) {
        const cell = this.cells[y * this.width + x];
        cell.occupant = req.instanceId;
        cell.walkable = false;
      }
    }
    const doorCell = this.at(req.door);
    if (!doorCell) return false;
    doorCell.walkable = true;
    doorCell.terrain = 'stone';
    return true;
  }

  /** 占地 + 留白全部没被占才允许落。margin 会把周围几圈也算进检查。 */
  canPlace(origin: GridPoint, w: number, h: number, margin: number): boolean {
    for (let y = origin.y - margin; y < origin.y + h + margin; y += 1) {
      for (let x = origin.x - margin; x < origin.x + w + margin; x += 1) {
        if (!this.isBuildable({ x, y })) return false;
      }
    }
    return true;
  }

  /** 在蓝图允许的分区里按离锚点最近优先找一块合规空地。找不到返回 null。 */
  findBestSlot(prefab: PrefabBlueprint, anchor: GridPoint): GridPoint | null {
    const { w, h } = prefab.footprint;
    const margin = prefab.clearMargin;
    let best: GridPoint | null = null;
    let bestScore = Number.POSITIVE_INFINITY;
    for (let y = 0; y < this.height; y += 1) {
      for (let x = 0; x < this.width; x += 1) {
        const origin = { x, y };
        if (!this.footprintInZone(origin, w, h, prefab.zone)) continue;
        if (!this.canPlace(origin, w, h, margin)) continue;
        const door = { x: origin.x + prefab.door.x, y: origin.y + prefab.door.y };
        if (!this.isBuildable(door)) continue;
        const cx = origin.x + (w - 1) / 2;
        const cy = origin.y + (h - 1) / 2;
        const score = Math.abs(cx - anchor.x) + Math.abs(cy - anchor.y);
        if (score < bestScore) {
          bestScore = score;
          best = origin;
        }
      }
    }
    return best;
  }

  private footprintInZone(origin: GridPoint, w: number, h: number, zone: ZoneId): boolean {
    for (let y = origin.y; y < origin.y + h; y += 1) {
      for (let x = origin.x; x < origin.x + w; x += 1) {
        if (this.zoneOf({ x, y }) !== zone) return false;
      }
    }
    return true;
  }

  /** 4 邻域 A*。终点不可达时返回空数组（调用方自己决定怎么降级）。 */
  path(start: GridPoint, goal: GridPoint): GridPoint[] {
    if (!this.inBounds(start) || !this.inBounds(goal)) return [];
    let target = goal;
    if (!this.isWalkable(target)) {
      const alt = this.nearestWalkable(target);
      if (!alt) return [];
      target = alt;
    }
    if (start.x === target.x && start.y === target.y) return [];

    const n = this.width * this.height;
    const index = (p: GridPoint): number => p.y * this.width + p.x;
    const gScore = new Float64Array(n).fill(Number.POSITIVE_INFINITY);
    const cameFrom = new Int32Array(n).fill(-1);
    const closed = new Uint8Array(n);
    const startIndex = index(start);
    const goalIndex = index(target);
    gScore[startIndex] = 0;
    const open: { point: GridPoint; f: number }[] = [
      { point: start, f: heuristic(start, target) },
    ];

    while (open.length > 0) {
      let bestIndex = 0;
      for (let i = 1; i < open.length; i += 1) {
        if (open[i].f < open[bestIndex].f) bestIndex = i;
      }
      const current = open.splice(bestIndex, 1)[0].point;
      const currentIndex = index(current);
      if (currentIndex === goalIndex) return rebuild(cameFrom, currentIndex, this.width);
      if (closed[currentIndex]) continue;
      closed[currentIndex] = 1;
      for (const step of ORTHOGONAL) {
        const next = { x: current.x + step.x, y: current.y + step.y };
        if (!this.isWalkable(next)) continue;
        const nextIndex = index(next);
        if (closed[nextIndex]) continue;
        const tentative = gScore[currentIndex] + 1;
        if (tentative < gScore[nextIndex]) {
          gScore[nextIndex] = tentative;
          cameFrom[nextIndex] = currentIndex;
          open.push({ point: next, f: tentative + heuristic(next, target) });
        }
      }
    }
    return [];
  }

  /** 半径内随机一块可走空地；找不到返回 null。 */
  randomWalkableNear(
    center: GridPoint,
    radius: number,
    random: () => number,
  ): GridPoint | null {
    const candidates: GridPoint[] = [];
    for (let y = center.y - radius; y <= center.y + radius; y += 1) {
      for (let x = center.x - radius; x <= center.x + radius; x += 1) {
        const p = { x, y };
        if (Math.abs(x - center.x) + Math.abs(y - center.y) > radius) continue;
        if (this.isWalkable(p)) candidates.push(p);
      }
    }
    if (candidates.length === 0) return null;
    return candidates[Math.floor(random() * candidates.length)];
  }

  /** 离目标最近的一块可走空地。作息落点用它兜底。 */
  nearestWalkable(goal: GridPoint): GridPoint | null {
    let best: GridPoint | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (let y = 0; y < this.height; y += 1) {
      for (let x = 0; x < this.width; x += 1) {
        const p = { x, y };
        if (!this.isWalkable(p)) continue;
        const distance = Math.abs(x - goal.x) + Math.abs(y - goal.y);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = p;
        }
      }
    }
    return best;
  }
}

const ORTHOGONAL: readonly GridPoint[] = [
  { x: 0, y: -1 },
  { x: 1, y: 0 },
  { x: 0, y: 1 },
  { x: -1, y: 0 },
];

function heuristic(a: GridPoint, b: GridPoint): number {
  return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
}

function rebuild(cameFrom: Int32Array, goalIndex: number, width: number): GridPoint[] {
  const out: GridPoint[] = [];
  let current = goalIndex;
  while (current !== -1) {
    out.push({ x: current % width, y: Math.floor(current / width) });
    current = cameFrom[current];
  }
  out.reverse();
  return out.slice(1); // 去掉起点：她已经站在那了。
}

function terrainFor(zone: ZoneId): TileCell['terrain'] {
  switch (zone) {
    case 'altar':
      return 'plaza';
    case 'agriculture':
      return 'farm';
    case 'street':
      return 'stone';
    default:
      return 'grass';
  }
}
