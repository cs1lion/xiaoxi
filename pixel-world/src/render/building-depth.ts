/** 建筑三层深度的算术：纯函数，不碰 Phaser，可在 Node 里直接验算。
 *
 * 为什么单独一个文件
 * ------------------
 * `buildings.ts` import 了 Phaser，Node 里跑不起来，于是这套遮挡算术在提交时
 * **没有任何回归保护** —— 当时那 15 项断言是临时的、没入库。QA 指出这是真问题。
 * 这里把算术抽出来，`building-depth.test.ts` 就能直接锁住它。
 *
 * 角色一侧的口径（villager.ts:52,55）：`depth = ALTITUDE + py + 8`，
 * 其中 `py = (pos.y + 1) * TILE_PX`。
 */

import { TILE_PX } from '../world/setup.ts';

/** 角色 depth 的偏移量，与 villager.ts 保持一致。改一处必须改两处。 */
export const ACTOR_DEPTH_OFFSET = 8;

/** 三层相对建筑落地线 baseY 的像素偏移。front 的推导见下方 frontOffset 说明。 */
export const BUILDING_DEPTH = { back: -2, body: 0, front: 32 } as const;

/** 建筑落地线：占地底边的世界 y。 */
export function buildingBaseY(originY: number, footprintH: number): number {
  return originY + footprintH * TILE_PX;
}

/** 角色站在某个格子上时的 depth。 */
export function actorDepthAt(gridY: number, altitude: number): number {
  return altitude + (gridY + 1) * TILE_PX + ACTOR_DEPTH_OFFSET;
}

/** 建筑某一层的 depth。 */
export function layerDepth(
  layer: keyof typeof BUILDING_DEPTH,
  baseY: number,
  altitude: number,
): number {
  return altitude + baseY + BUILDING_DEPTH[layer];
}

/**
 * front 为什么取 32（而不是美术文档 §1.3 写的 12）
 * ------------------------------------------------
 * 门口格恒在占地底边**下一行**（prefabs.json 里 `door.y === footprint.h`），所以：
 *
 *   baseY            = door.y * 16                  ← 门口格的上边
 *   站门口的角色      = baseY + 16 + 8 = baseY + 24
 *   门口下 1 格的角色 = baseY + 32 + 8 = baseY + 40
 *
 * 要让前檐压住站门口的人，需要 `front > baseY + 24`；同时不压到再下一格的人，
 * 需要 `front < baseY + 40`。取区间中点 **+32**。
 *
 * 文档的 +12 落在 baseY+12 < baseY+24，檐完全在人后面，等于这一层白做。
 */
export const FRONT_OFFSET_MIN_EXCLUSIVE = 24;
export const FRONT_OFFSET_MAX_EXCLUSIVE = 40;
