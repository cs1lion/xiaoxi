/** 郊野的辨识信号：让「在野外」这件事看得见。
 *
 * 郊野铺子原本只是「换了配色的同款建筑，走过去更久」——多走的六秒没换来任何
 * 不同的体验。这里补上一条：**郊野铺子常亮一盏灯**。
 *
 * 为什么是灯，而不是新建筑或新动画：
 *   - 荒地里的一个人造光点，本身就说明「这里有人」——这是郊野该有的读感；
 *   - 只用现有 24 色里的 `warmLight` 与 `amber`，不扩色；
 *   - 不新增 `WorkAccent` 枚举，不动存档与既有一批测试。
 *
 * 纯函数，不碰渲染。
 */

import { WILD_PREFABS } from './setup.ts';
import type { PlacedBuilding, WorldSnapshot } from './types.ts';

/**
 * 这栋楼是否亮着常明灯。
 *
 * 只对郊野铺子成立。镇内铺子的窗灯由时相系统统一控制（黄昏与夜才亮），
 * 两者不是同一套——镇内是"作息"，郊野是"有人在野外守着"。
 */
export function isWildLanternOn(
  building: PlacedBuilding,
  _snapshot: WorldSnapshot,
): boolean {
  return WILD_PREFABS.has(building.prefabId);
}

/** 郊野铺子里有几栋亮着灯。给纸面/调试面板用。 */
export function litWildLanterns(snapshot: WorldSnapshot): readonly PlacedBuilding[] {
  return snapshot.buildings.filter((building) => isWildLanternOn(building, snapshot));
}
