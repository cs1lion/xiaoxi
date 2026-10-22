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

/**
 * 开局点亮的半径。
 *
 * **必须盖住所有可建区**（镇内 + 郊野）。早先取 16，郊野从 26 格外起，
 * 结果郊野全在迷雾里——拆了铺子想重建时一律被「那里还没长出来」拒绝。
 *
 * 取 44：郊野分区最远 40.5 格，44 留出余量，同时给交付留 8 格的揭示空间
 * （上限 52，地图最远角 50）。
 *
 * 诚实说一句：80×60 的地图容不下「郊野 + 未探索」两层。郊野已经占到 40 格，
 * 最远角才 50 格。迷雾在这里只能盖住边角，做不成真正的"未探索大陆"。
 * 要那一层，得把地图放大到 160×120 以上。
 */
export const FOG_INITIAL_RADIUS = 44;

/** 交付多少单扩一格。太密会让画面一直在变。 */
const DELIVERIES_PER_STEP = 4;

/** 揭示半径的上限：地图最远角 50，超过它就没有意义了。 */
const MAX_RADIUS = 52;

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
