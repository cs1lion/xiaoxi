/** 郊野林地：在离镇子足够远的地方长出树，让"辽阔"不等于"空旷"。
 *
 * 两条硬约束：
 *   1. **确定性**。不用 Math.random，也不用时间。同一个坐标永远长出同样的树，
 *      截图可复现、读档可重建。
 *   2. **不挡路**。林地只长在郊野圈，且避开主干道与郊野分区的可建区；
 *      否则 A* 会把镇子封死。
 *
 * 手法是低频值噪声（哈希 + 平滑插值），不是柏林噪声：我们只需要"成片的疏密"，
 * 不需要完美的分形。少一个依赖，也少一处要证明它确定性的地方。
 */

import type { GridPoint, TerrainId } from './types.ts';
import { biomeAt, BIOME_RADII, type BiomeId } from './biomes.ts';

/** 噪声的格子尺寸：每 8×8 格一个控制点，插值出成片的疏密。 */
const NOISE_CELL = 6;

/** 林地的疏密阈值。越大树越少。 */
const FOREST_THRESHOLD = 0.58;

/** 确定性哈希：返回 0..1。 */
function hash01(x: number, y: number): number {
  let h = Math.imul(x, 374761393) ^ Math.imul(y, 668265263);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** 平滑插值，让控制点之间的过渡不是折线。 */
function smooth(t: number): number {
  return t * t * (3 - 2 * t);
}

/**
 * 值噪声：0..1。
 *
 * 两层叠加而不是一层：单层 8×8 控制点插值出来的边界仍然带着网格的直角，
 * 林子会读成"一堆方块"而不是"一片林子"。加一层 3×3 的高频扰动打碎边缘，
 * 低频那层仍负责成片的疏密。
 */
export function forestNoise(x: number, y: number): number {
  // 低频定疏密、高频打碎边界。高频权重给到 0.3 才压得住 6×6 控制点的直角。
  return valueNoise(x, y, NOISE_CELL) * 0.7 + valueNoise(x, y, 2.5) * 0.3;
}

/** 单层值噪声。cell 越小，细节越碎。 */
function valueNoise(x: number, y: number, cell: number): number {
  const cx = Math.floor(x / cell);
  const cy = Math.floor(y / cell);
  const fx = smooth((x - cx * cell) / cell);
  const fy = smooth((y - cy * cell) / cell);

  const a = hash01(cx, cy);
  const b = hash01(cx + 1, cy);
  const c = hash01(cx, cy + 1);
  const d = hash01(cx + 1, cy + 1);

  const top = a + (b - a) * fx;
  const bottom = c + (d - c) * fx;
  return top + (bottom - top) * fy;
}

/**
 * 这一格该不该长成树。
 *
 * `protectedZones` 是可建区：那里不长树，否则造物主无处落位。
 * 镇内（核心与工坊环）也不长，那里是人待的地方。
 */
export function shouldGrowTree(
  point: GridPoint,
  center: GridPoint,
  protectedZones: readonly { readonly x: number; readonly y: number; readonly w: number; readonly h: number }[],
): boolean {
  const biome: BiomeId = biomeAt(point, center);
  if (biome === 'townCore' || biome === 'craftRing') return false;

  // 可建区与主干道两侧留空。
  for (const zone of protectedZones) {
    if (
      point.x >= zone.x - 1 &&
      point.x < zone.x + zone.w + 1 &&
      point.y >= zone.y - 1 &&
      point.y < zone.y + zone.h + 1
    ) {
      return false;
    }
  }

  // 郊野比边界更密：越往外越荒。
  const density =
    biome === 'frontier' ? FOREST_THRESHOLD - 0.12 : FOREST_THRESHOLD;
  return forestNoise(point.x, point.y) > density;
}

/** 郊野里可通行的草地与林地。林地可走但代价高（见 terrain-cost）。 */
export function wildernessTerrain(
  point: GridPoint,
  center: GridPoint,
  protectedZones: readonly { readonly x: number; readonly y: number; readonly w: number; readonly h: number }[],
): TerrainId | null {
  return shouldGrowTree(point, center, protectedZones) ? 'tree' : null;
}

/** 群落半径的再导出：渲染层与测试都要用，避免两处各写一份。 */
export { BIOME_RADII };
