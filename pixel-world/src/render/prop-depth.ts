/** 道具的落地坐标与 depth：纯函数，不碰 Phaser，可以在 Node 里直接验算。
 *
 * 为什么单独一个文件
 * ------------------
 * 道具 depth 的正确性靠「和角色同一套口径」保证，而这种算术必须能独立验算——
 * 塞进依赖 Phaser 的 decor.ts 里就只能靠肉眼判断。decor.ts 负责画，这里负责算。
 */

import { TILE_PX } from '../world/setup.ts';
import type { WorldProp } from '../world/types.ts';

/**
 * 道具的落地 y（像素）。**必须**与 drawProp 里 setPosition 用的 y 是同一个表达式，
 * 否则 depth 会锚在一个并不存在的落点上——排序看起来「有效」但和画面不符。
 *
 * 0.8 是既有画法给摆件的落点偏移（站在格内偏下），不是随手取的数。
 */
export function propFootY(prop: WorldProp, originY: number): number {
  return originY + (prop.at.y + 0.8) * TILE_PX;
}

/**
 * 角色 depth 的偏移量：`villager.ts` 用 `ALTITUDE + py + 8`。
 * 道具沿用同一档，两者才在同一把尺子上可比；换一个偏移会让排序在某些格子上翻转。
 */
export const ACTOR_DEPTH_OFFSET = 8;

/** 道具 depth：`ALTITUDE + 落地 y + 8`，与角色完全同口径。 */
export function propDepth(prop: WorldProp, originY: number, altitude: number): number {
  return altitude + propFootY(prop, originY) + ACTOR_DEPTH_OFFSET;
}
