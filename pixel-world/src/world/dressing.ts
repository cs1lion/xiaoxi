/** 地表细节的密度配方，按群落分。
 *
 * 为什么单独一个文件：`src/render/` 会 `import Phaser`，node --test 加载不了；
 * 而这份配方是**纯数据**，测试要能直接读它断言「镇内与郊野真的不一样」。
 * 放这里，渲染层 value-import，测试也 import，两边同一份。
 *
 * 为什么不放进 `palette-data.ts`：那是颜色表，改颜色要走 ADR。
 * 这张表改的是密度，不是色，两者的审批链条不同，不该混在一个文件里。
 */

import type { BiomeId } from './biomes.ts';

/**
 * 每个值都是 `1/n` 里的 n —— 越小越密。**只在草地格上生效**，
 * 农田与林地各有自己的纹理，不受这张表影响。
 */
export interface DressingRecipe {
  /** 贴边草。 */
  readonly edge: number;
  /** 岩石。 */
  readonly rock: number;
  /** 灌木。 */
  readonly bush: number;
  /** 石板碎屑（镇内的铺装遗迹）。 */
  readonly slab: number;
  /** 野花。郊野多、镇内没有。 */
  readonly flower: number;
}

/**
 * 为什么按群落分而不是全图一个密度：96×72 的图里草地占 50%，全图同一种
 * 「稀疏草簇」读下来就是一块绿色的地毯。镇内该是被人踩过的样子（草稀、
 * 碎石多、石板碎屑多），郊野该是没人管的样子（灌木密、野花成片、乱石少）。
 */
export const DRESSING: Readonly<Record<BiomeId, DressingRecipe>> = {
  // 镇内：踩得多，草稀，碎石与石板碎屑多；没有野花（花在窗台花箱里）。
  townCore: { edge: 5, rock: 61, bush: 89, slab: 23, flower: 0 },
  craftRing: { edge: 6, rock: 57, bush: 79, slab: 31, flower: 0 },
  // 郊野：没人管，草密、野花多、灌木多。
  wilderness: { edge: 9, rock: 67, bush: 43, slab: 0, flower: 29 },
  // 未探索：更荒，碎石比花多。
  frontier: { edge: 11, rock: 41, bush: 53, slab: 0, flower: 47 },
};
