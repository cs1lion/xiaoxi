/** 地形拼接边（ART_STYLE_TARGET §1.2）：让材质之间长出台阶边，而不是硬切方块。
 *
 * 为什么需要
 * ----------
 * 每格只铺一块基色 + 2×2 抖动，材质交界就是一条直角硬切。Stardew 靠 47 块 blob 图集
 * 让地面自己长出边和角；我们没有图集（纯 Graphics 手绘），取它的**可用子集**：
 *
 *   4 邻边掩码（N/E/S/W 各一位，16 种）+ 4 个内凹角修正（NW/NE/SW/SE 各一位）= 20 变体
 *
 * 这 20 个是**绘制原语**，不是一张 20 张图拼出来的图集：一格可以同时拥有多条边和多个
 * 角（例如北、西都更低、西北也更高时，三个原语同时生效），所以按格逐位判定直接画。
 *
 * 三条规则（照抄美术文档，不自创）
 * --------------------------------
 * 1. **边只画在高材质一侧**。材质高度序 `tree > grass > farm > stone > plaza`；低的一侧
 *    不画。这样每条交界线上**恰好只有一条 1px 线**，不会出现 2px 双描边。
 * 2. **亮边在上/左，暗边在下/右**。世界的光统一从左上打（与 ART_STYLE_TARGET §1.3 的
 *    建筑投影、§1.5 的角色接触阴影同向）。高台朝上/朝左的立面受光 → 用该材质**亮一档**
 *    的 token；朝下/朝右的立面背光 → 用 `soil`（W02，全场最深的土色，兼作阴影色）。
 * 3. **不加色**。只用 `soil / plaza / grassLit` 三个现有 token，24 色预算不变。
 *
 * 内凹角修正是干什么的
 * --------------------
 * 相邻两格各自画出一条暗边时，两条线是「错开一格」的：上面的格画 y 行、左边的格画 x 列，
 * 交点那个像素不属于任何一条线，会留下 1px 缺口（实测在地图四角的树篱处必然出现）。
 * 修正就是由**斜角更高**的那一格把交点补上 1px，让 L 形转角连续。
 */

import type Phaser from 'phaser';
import type { TerrainId } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { TILE_PX } from '../world/setup.ts';

/** 材质高度序：边只画在高度**更大**的一侧。 */
export const TERRAIN_HEIGHT: Readonly<Record<TerrainId, number>> = {
  tree: 4,
  grass: 3,
  farm: 2,
  stone: 1,
  plaza: 0,
};

/** 亮一档 token：只用现有 24 色里的 `grassLit` / `plaza`（§1.2「不加色」）。 */
export const BRIGHT: Readonly<Record<TerrainId, number>> = {
  tree: WORLD.grassLit,
  // grass 亮边用 plaza（ΔL* 9.5）；原 grassLit 对 grass 只有 3.0，18 条边不可见。
  grass: WORLD.plaza,
  farm: WORLD.plaza,
  stone: WORLD.plaza,
  // plaza 是最低材质，邻格不可能更低，所以它**永远不画边**（实测 16 个 plaza 格
  // 一个都没画）。这里给 trim 而不是自身也是为此：万一将来改了高度序，画出来的
  // 边也不会与底色相同、变成隐形线。
  plaza: WORLD.trim,
};

/**
 * 暗边 token：按材质查表，**不能**全场统一用一个色。
 *
 * 原来全场统一用 soil（W02）。但 `tree` 的底色本身就是 soil
 * （`TERRAIN_COLORS.tree = WORLD.soil`），于是 tree 格朝下/朝右的暗边画出来
 * 与底色完全相同 —— 亮度落差 **0.0**，等于白画。实测：96 个 tree 格里有 48 个
 * （正好一半，朝下/朝右那侧）的边完全看不见，占总写入像素的 25.6%。
 *
 * 这不是风格选择，是 §1.2「暗边比底色更深」这条规则在 tree 上的漏洞：它的底色
 * 已经是最深的土色，没有更深的同色可用。修法是 tree 的暗边改用 ink（W01，比 soil
 * 再深一档），落差 14.0 —— 可辨，且仍在 24 色预算内，不新增颜色。
 *
 * 为什么其他材质不动：grass / farm / stone / plaza 的底色都比 soil 亮，落差
 * 20.9–45.6，本来就清楚。只补 tree 这一处漏洞，不顺手改已有正确的东西。
 */
export const DARK: Readonly<Record<TerrainId, number>> = {
  tree: WORLD.ink,
  grass: WORLD.soil,
  farm: WORLD.soil,
  stone: WORLD.soil,
  // 同 BRIGHT.plaza 的理由：plaza 永不画边；给 stone（比 plaza 暗）以防将来改高度序。
  plaza: WORLD.stone,
};

/** 四邻边掩码的位。 */
export const SIDE_BIT = { north: 1, east: 2, south: 4, west: 8 } as const;
/** 四内凹角修正掩码的位。 */
export const CORNER_BIT = { northWest: 1, northEast: 2, southWest: 4, southEast: 8 } as const;

export const SIDE_VARIANTS = 16;
export const CORNER_VARIANTS = 4;
/** 20 变体 = 16 邻边 + 4 内凹角（§1.2）。 */
export const TERRAIN_EDGE_VARIANTS = SIDE_VARIANTS + CORNER_VARIANTS;

/** 一格周围 8 个邻格的材质；越界为 `null`（世界边界不算材质边界，不画边）。 */
export interface EdgeNeighbors {
  readonly north: TerrainId | null;
  readonly northEast: TerrainId | null;
  readonly east: TerrainId | null;
  readonly southEast: TerrainId | null;
  readonly south: TerrainId | null;
  readonly southWest: TerrainId | null;
  readonly west: TerrainId | null;
  readonly northWest: TerrainId | null;
}

/** 邻格比本格低（严格小于）→ 这一侧要画边。越界 / 同高 / 更高都不画。 */
function isLower(self: TerrainId, other: TerrainId | null): boolean {
  if (other === null) return false;
  return TERRAIN_HEIGHT[other] < TERRAIN_HEIGHT[self];
}

/** 四邻边掩码：某一位为 1 表示该侧的邻格材质更低，需要一条 1px 台阶边。 */
export function sideMask(self: TerrainId, around: EdgeNeighbors): number {
  let mask = 0;
  if (isLower(self, around.north)) mask |= SIDE_BIT.north;
  if (isLower(self, around.east)) mask |= SIDE_BIT.east;
  if (isLower(self, around.south)) mask |= SIDE_BIT.south;
  if (isLower(self, around.west)) mask |= SIDE_BIT.west;
  return mask;
}

/**
 * 四内凹角修正掩码：某个角的两条直角邻边**都不画**、但斜角更低时，补 1px 交点。
 *
 * 两条直角邻边都不画意味着相邻两格不会替我们补这个交点，缺口只能由本格自己补；
 * 若某条直角邻边已经画了线，交点被那条线覆盖，不需要也不应该重复补。
 */
export function cornerMask(self: TerrainId, around: EdgeNeighbors): number {
  const side = sideMask(self, around);
  let mask = 0;
  if (
    (side & (SIDE_BIT.north | SIDE_BIT.west)) === 0 &&
    isLower(self, around.northWest)
  ) {
    mask |= CORNER_BIT.northWest;
  }
  if (
    (side & (SIDE_BIT.north | SIDE_BIT.east)) === 0 &&
    isLower(self, around.northEast)
  ) {
    mask |= CORNER_BIT.northEast;
  }
  if (
    (side & (SIDE_BIT.south | SIDE_BIT.west)) === 0 &&
    isLower(self, around.southWest)
  ) {
    mask |= CORNER_BIT.southWest;
  }
  if (
    (side & (SIDE_BIT.south | SIDE_BIT.east)) === 0 &&
    isLower(self, around.southEast)
  ) {
    mask |= CORNER_BIT.southEast;
  }
  return mask;
}

/** 本格用到几个绘制原语（0 表示这格什么都不画）。调试与单测用。 */
export function primitiveCount(self: TerrainId, around: EdgeNeighbors): number {
  return bitCount(sideMask(self, around)) + bitCount(cornerMask(self, around));
}

function bitCount(value: number): number {
  let rest = value;
  let count = 0;
  while (rest !== 0) {
    count += rest & 1;
    rest >>>= 1;
  }
  return count;
}

/**
 * 画一格的地形边。在底色与抖动纹理之后调用，只写本格内部的 1px 像素，不越界。
 *
 * 绘制顺序：先暗边（下/右）再亮边（上/左），最后补角。暗亮在角上共用同一个像素时
 * 让亮边胜出——光从左上来，转角靠光的那一侧是受光面。
 */
export function drawTileEdge(
  g: Phaser.GameObjects.Graphics,
  self: TerrainId,
  around: EdgeNeighbors,
  px: number,
  py: number,
): void {
  const side = sideMask(self, around);
  const corner = cornerMask(self, around);
  if (side === 0 && corner === 0) return;

  const size = TILE_PX;
  const bright = BRIGHT[self];
  // 暗边按材质取色：tree 用 ink（底色本身是 soil，用 soil 会隐形），其余用 soil。
  const dark = DARK[self];

  if ((side & SIDE_BIT.south) !== 0) {
    g.fillStyle(dark, 1);
    g.fillRect(px, py + size - 1, size, 1);
  }
  if ((side & SIDE_BIT.east) !== 0) {
    g.fillStyle(dark, 1);
    g.fillRect(px + size - 1, py, 1, size);
  }
  if ((side & SIDE_BIT.north) !== 0) {
    g.fillStyle(bright, 1);
    g.fillRect(px, py, size, 1);
  }
  if ((side & SIDE_BIT.west) !== 0) {
    g.fillStyle(bright, 1);
    g.fillRect(px, py, 1, size);
  }

  if ((corner & CORNER_BIT.northWest) !== 0) {
    g.fillStyle(bright, 1);
    g.fillRect(px, py, 1, 1);
  }
  if ((corner & CORNER_BIT.northEast) !== 0) {
    g.fillStyle(bright, 1);
    g.fillRect(px + size - 1, py, 1, 1);
  }
  if ((corner & CORNER_BIT.southWest) !== 0) {
    g.fillStyle(dark, 1);
    g.fillRect(px, py + size - 1, 1, 1);
  }
  if ((corner & CORNER_BIT.southEast) !== 0) {
    g.fillStyle(dark, 1);
    g.fillRect(px + size - 1, py + size - 1, 1, 1);
  }
}
