/** 网格账本：分区、占用、选址与 A*。
 *
 * 纯逻辑，不依赖 Phaser。世界的一切空间约束都在这里结算；
 * 渲染层只读快照，不许自己改坐标。
 */

import type { GridPoint, PrefabBlueprint, TileCell, ZoneId } from './types.ts';
import { WALK_COST, walkCostOf } from './terrain-cost.ts';
import { shouldGrowTree } from './wilderness.ts';

/**
 * 未被任何分区覆盖的格子落到这里。
 *
 * 旧规格里七个分区恰好铺满 30×20，默认值永远不会生效；大世界只定义若干
 * 矩形，剩下大片是「未覆盖」。默认成 `street` 会让这些格子全部铺成石板——
 * 实测大世界 72% 是石头，整张图看起来像一块工地。默认成草地才对。
 */
const ZONE_DEFAULT: ZoneId = 'living';

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

  constructor(spec: GridSpec, wilderness?: { readonly center: GridPoint }) {
    this.width = spec.width;
    this.height = spec.height;
    this.cells = new Array<TileCell>(spec.width * spec.height);
    // 郊野林地只在给了中心点时生成：旧规格靠边界的树就够了，
    // 不传 center 时行为与以前完全一致。
    const protectedZones = spec.zones.filter((z) => z.id !== 'street' && z.id !== 'altar');
    for (let y = 0; y < spec.height; y += 1) {
      for (let x = 0; x < spec.width; x += 1) {
        const zone = this.zoneAt(spec, x, y);
        const edge =
          x < spec.border ||
          y < spec.border ||
          x >= spec.width - spec.border ||
          y >= spec.height - spec.border;
        let terrain = terrainFor(zone);
        // 郊野长树：让"辽阔"不等于"空旷"。镇内与可建区不长。
        if (!edge && terrain === 'grass' && wilderness !== undefined) {
          if (shouldGrowTree({ x, y }, wilderness.center, protectedZones)) {
            terrain = 'tree';
          }
        }
        this.cells[y * spec.width + x] = {
          zone,
          terrain: edge ? 'tree' : terrain,
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

  /**
   * 把一栋建筑从账本里撤掉：清占用、恢复可走。
   *
   * 门口保留 stone 地形——那是铺过的路，拆楼不该把路也铲了。
   * 返回是否真的撤掉了（找不到占用者时返回 false）。
   */
  remove(instanceId: string, origin: GridPoint, w: number, h: number): boolean {
    let found = false;
    for (let y = origin.y; y < origin.y + h; y += 1) {
      for (let x = origin.x; x < origin.x + w; x += 1) {
        const cell = this.at({ x, y });
        if (cell === undefined) continue;
        if (cell.occupant !== instanceId) continue;
        cell.occupant = null;
        cell.walkable = true;
        found = true;
      }
    }
    return found;
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
  /**
   * 在蓝图允许的分区里按离锚点最近优先找一块合规空地。
   *
   * `accept` 是可选的额外筛选：郊野铺子要用它把落点限制在郊野圈内。
   * 不传时行为与以前完全一致。
   */
  findBestSlot(
    prefab: PrefabBlueprint,
    anchor: GridPoint,
    accept?: (origin: GridPoint) => boolean,
  ): GridPoint | null {
    const { w, h } = prefab.footprint;
    const margin = prefab.clearMargin;
    let best: GridPoint | null = null;
    let bestScore = Number.POSITIVE_INFINITY;
    for (let y = 0; y < this.height; y += 1) {
      for (let x = 0; x < this.width; x += 1) {
        const origin = { x, y };
        if (accept !== undefined && !accept(origin)) continue;
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

    /**
     * 二叉堆取代原来的"每轮线性扫描最小值"。
     *
     * 单屏 30×20 时线性扫描够用；大世界 80×60 有 4800 格，每轮扫描整个 open
     * 列表会让跨图寻路明显变慢。堆的键是 f，同 f 不比较别的，保持确定性。
     */
    const heap = new MinHeap();
    heap.push(startIndex, heuristic(start, target), 0);

    while (heap.size > 0) {
      const { value: currentIndex, g: poppedG } = heap.pop();
      // 陈旧条目：这个格子后来被以更小的代价重新入堆过，旧条目要丢掉。
      // 少了这一步，堆里过期的 f 会把正确路线挤到后面，A* 给出非最优解。
      if (poppedG > gScore[currentIndex]) continue;
      if (closed[currentIndex]) continue;
      if (currentIndex === goalIndex) return rebuild(cameFrom, currentIndex, this.width);
      closed[currentIndex] = 1;

      const current = { x: currentIndex % this.width, y: Math.floor(currentIndex / this.width) };
      for (const step of ORTHOGONAL) {
        const next = { x: current.x + step.x, y: current.y + step.y };
        const cell = this.at(next);
        if (cell === undefined || !cell.walkable) continue;
        const nextIndex = index(next);
        if (closed[nextIndex]) continue;

        // 代价来自地形：石板路便宜、密林贵，路线自己会先上大路。
        const tentative = gScore[currentIndex] + walkCostOf(cell.terrain);
        if (tentative < gScore[nextIndex]) {
          gScore[nextIndex] = tentative;
          cameFrom[nextIndex] = currentIndex;
          heap.push(nextIndex, tentative + heuristic(next, target), tentative);
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

/**
 * 启发式：曼哈顿距离 × 最小通行代价。
 *
 * 必须乘最小代价，否则它就不再是「低估剩余成本」。旧实现用裸曼哈顿距离，
 * 在引入代价后会把 2.5 的密林估成 1，A* 可能给出非最优路线（绕远却不自知）。
 * 用全场最小代价做尺度，保证可采纳，同时仍是有信息的。
 */
const MIN_WALK_COST = Math.min(...Object.values(WALK_COST));

function heuristic(a: GridPoint, b: GridPoint): number {
  return (Math.abs(a.x - b.x) + Math.abs(a.y - b.y)) * MIN_WALK_COST;
}

/**
 * 二叉最小堆，键是 f 值。
 *
 * 不追求通用：只服务 A* 的 open 列表，没有 decrease-key（A* 用重复入堆代替）。
 */
class MinHeap {
  private readonly keys: number[] = [];
  private readonly values: number[] = [];
  /** 入堆时的 g 值，用来识别陈旧条目。 */
  private readonly gs: number[] = [];

  get size(): number {
    return this.keys.length;
  }

  push(value: number, key: number, g: number): void {
    this.keys.push(key);
    this.values.push(value);
    this.gs.push(g);
    let i = this.keys.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (this.keys[parent]! <= this.keys[i]!) break;
      this.swap(i, parent);
      i = parent;
    }
  }

  pop(): { value: number; g: number } {
    const topValue = this.values[0]!;
    const topG = this.gs[0]!;
    const lastKey = this.keys.pop()!;
    const lastValue = this.values.pop()!;
    const lastG = this.gs.pop()!;
    if (this.keys.length > 0) {
      this.keys[0] = lastKey;
      this.values[0] = lastValue;
      this.gs[0] = lastG;
      let i = 0;
      for (;;) {
        const left = i * 2 + 1;
        const right = left + 1;
        let smallest = i;
        if (left < this.keys.length && this.keys[left]! < this.keys[smallest]!) smallest = left;
        if (right < this.keys.length && this.keys[right]! < this.keys[smallest]!) smallest = right;
        if (smallest === i) break;
        this.swap(i, smallest);
        i = smallest;
      }
    }
    return { value: topValue, g: topG };
  }

  private swap(a: number, b: number): void {
    const key = this.keys[a]!;
    this.keys[a] = this.keys[b]!;
    this.keys[b] = key;
    const value = this.values[a]!;
    this.values[a] = this.values[b]!;
    this.values[b] = value;
    const g = this.gs[a]!;
    this.gs[a] = this.gs[b]!;
    this.gs[b] = g;
  }
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
