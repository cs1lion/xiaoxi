/** 走出来的痕迹：人反复走过的地方，草会被踩出一条浅痕。
 *
 * 与铺好的路（`roads`）是两回事：
 *   - `roads` 是**施工**的结果，建楼时一次铺好，存档里存着；
 *   - 痕迹是**走出来的**，不落档——它是从角色位置推导的副产品。
 *
 * 为什么不落档：痕迹会一直长，存起来会让存档无限膨胀，而它能从
 * "谁走过哪里"重新累积。这与 `report.ts` 的「能派生的不落档」同一条原则。
 *
 * 但有上限：格子数封顶，超出丢最弱的。否则跑一小时图会变成一张网。
 */

import type { GridPoint } from './types.ts';

/** 最多记多少格的痕迹。超出后丢最弱的那些。 */
export const TRAIL_LIMIT = 400;

/** 强度上限。走再多也只是"踩实了"，不会变成路。 */
export const TRAIL_MAX = 3;

/** 网格坐标 → 强度（1..3）。 */
export type TrailMap = Map<string, number>;

const keyOf = (p: GridPoint): string => `${p.x},${p.y}`;

/** 记一次踩踏。 */
export function addTrail(trails: TrailMap, cell: GridPoint): void {
  const key = keyOf(cell);
  const current = trails.get(key) ?? 0;
  if (current >= TRAIL_MAX) {
    // 已经封顶：把它移到 map 末尾，表示"最近还在走"，避免被淘汰。
    trails.delete(key);
    trails.set(key, TRAIL_MAX);
    return;
  }
  trails.set(key, current + 1);
  if (trails.size > TRAIL_LIMIT) evictWeakest(trails);
}

/** 某格的痕迹强度。没走过是 0。 */
export function trailStrengthAt(trails: TrailMap, cell: GridPoint): number {
  return trails.get(keyOf(cell)) ?? 0;
}

/**
 * 淘汰：先丢最弱的，同强度丢最早记的。
 *
 * `Map` 保持插入顺序，所以同强度时第一个就是最旧的。
 * 封顶的痕迹（强度 = TRAIL_MAX）最后才丢——那是走得最实的一条。
 */
function evictWeakest(trails: TrailMap): void {
  while (trails.size > TRAIL_LIMIT) {
    let weakestKey: string | null = null;
    let weakest = Number.POSITIVE_INFINITY;
    for (const [key, strength] of trails) {
      if (strength < weakest) {
        weakest = strength;
        weakestKey = key;
        if (weakest === 1) break; // 已经是最弱的了，不用再找
      }
    }
    if (weakestKey === null) return;
    trails.delete(weakestKey);
  }
}

/** 值得画出来的痕迹：强度 2 起。走一次不算路，画出来只是噪点。 */
export function visibleTrails(trails: TrailMap): readonly { readonly at: GridPoint; readonly strength: number }[] {
  const out: { at: GridPoint; strength: number }[] = [];
  for (const [key, strength] of trails) {
    if (strength < 2) continue;
    const [x, y] = key.split(',');
    out.push({ at: { x: Number(x), y: Number(y) }, strength });
  }
  return out;
}
