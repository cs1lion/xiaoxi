/** 群落：世界按离祭坛的远近分层，越往外越"野"。
 *
 * 为什么要分层，而不是随便铺草地：这个世界的空间要映射助理在做什么。
 * 镇中心是接神谕、写日报的地方；往外是工坊与农田，本地确定性的活；
 * 再往外是郊野，外部探索性质的活；最外圈是还没长出来的地方。
 *
 * 纯函数，不依赖网格，也不 import Phaser——渲染与逻辑都能直接调。
 */

import type { GridPoint } from './types.ts';

export type BiomeId = 'townCore' | 'craftRing' | 'wilderness' | 'frontier';

/** 由内向外。顺序本身就是「离中心多远」的语义。 */
export const BIOME_ORDER: readonly BiomeId[] = [
  'townCore',
  'craftRing',
  'wilderness',
  'frontier',
];

/** 纸面与调试面板用的名字。 */
export const BIOME_LABEL: Readonly<Record<BiomeId, string>> = {
  townCore: '核心小镇',
  craftRing: '工坊与农田',
  wilderness: '郊野',
  frontier: '未探索',
};

/**
 * 每一圈的外半径（单位：格，从中心量）。
 *
 * 数值来自大世界规格 96×72：中心到边界最远处约 60 格。
 * 四圈按 10 / 21 / 37 / 60 切。37 之外留给迷雾——这才是「未探索」该有的地方，
 * 而不是像 80×60 那样被郊野占满、只剩边角可雾。
 */
export const BIOME_RADII: Readonly<Record<Exclude<BiomeId, 'frontier'>, number>> = {
  townCore: 10,
  // 21：镇内四块分区最远 20.6 格，20 会把最外一角的铺子判成郊野。
  craftRing: 21,
  // 37：郊野四块分区最远 35.0 格。再往外是迷雾，那才是真正的未探索。
  wilderness: 37,
};

/**
 * 某点属于哪一圈。
 *
 * 用欧氏距离而不是曼哈顿：曼哈顿距离画出来是菱形，四角会突然变成外圈，
 * 与「同心圆群落」的说法对不上。
 */
export function biomeAt(point: GridPoint, center: GridPoint): BiomeId {
  const distance = Math.hypot(point.x - center.x, point.y - center.y);
  if (distance <= BIOME_RADII.townCore) return 'townCore';
  if (distance <= BIOME_RADII.craftRing) return 'craftRing';
  if (distance <= BIOME_RADII.wilderness) return 'wilderness';
  return 'frontier';
}

/** 群落是否属于"镇内"（核心与工坊环）。郊野与未探索不算。 */
export function isInsideTown(biome: BiomeId): boolean {
  return biome === 'townCore' || biome === 'craftRing';
}
