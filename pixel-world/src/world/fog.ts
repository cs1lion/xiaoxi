/** 迷雾：世界还没长出来的部分。
 *
 * 一条硬约束：**迷雾不影响通行，只影响建造与观感**。
 *
 * 为什么：存档里没有网格数据，读档时按建筑坐标重建网格。如果迷雾让格子不可走，
 * 一栋落在迷雾里的建筑读档后就成了孤岛——玩家会看到"铺子在，但人走不进去"。
 * 所以迷雾只做两件事：压暗画面、拦住新建造。
 *
 * 纯函数，不碰网格也不碰渲染。
 */

import type { GridPoint } from './types.ts';

/** 开局点亮的半径：盖住核心镇与一部分工坊环。 */
export const FOG_INITIAL_RADIUS = 16;

/** 交付多少单扩一格。太密会让画面一直在变。 */
const DELIVERIES_PER_STEP = 4;

/** 揭示半径的上限：超过地图半宽就没有意义了。 */
const MAX_RADIUS = 44;

export interface FogState {
  readonly center: GridPoint;
  readonly revealedRadius: number;
}

/**
 * 这一格是否还在迷雾里。
 *
 * 用欧氏距离，与群落判定同一把尺子：迷雾边界和"郊野"边界读起来该是一致的。
 */
export function fogAt(point: GridPoint, state: FogState): boolean {
  return Math.hypot(point.x - state.center.x, point.y - state.center.y) > state.revealedRadius;
}

/**
 * 交付单数 → 揭示半径。
 *
 * 每 `DELIVERIES_PER_STEP` 单扩一格，且不会小于初始半径。
 * 传进 NaN 时退回初始值：宁可少揭，不要因为一个坏数字把整张图点亮。
 */
export function revealedRadiusFor(deliveries: number): number {
  if (!Number.isFinite(deliveries) || deliveries <= 0) return FOG_INITIAL_RADIUS;
  const steps = Math.floor(deliveries / DELIVERIES_PER_STEP);
  return Math.min(MAX_RADIUS, FOG_INITIAL_RADIUS + steps);
}
