/** 从郊野带回来的东西。
 *
 * 纯视觉：它不影响任务结算，只表达「跑了一趟，带了东西回来」。
 *
 * 为什么按活类型分：去矿洞里查资料和去磨坊整理文稿，带回来的东西不该一样。
 * 这只是读感，不是资源——世界没有货币，也不会因为这些"东西"多出什么数值。
 */

import type { CarryKind, TaskKind } from './types.ts';

const CARRY_BY_KIND: Readonly<Record<TaskKind, CarryKind>> = {
  /** 写代码：从齿轮塔带回来一块磨好的石料。 */
  code: 'stone',
  /** 查资料：从矿洞里挖出来的卷轴。 */
  research: 'scroll',
  /** 整理文稿：从磨坊带回来的纸。 */
  writing: 'paper',
  /** 短活不进铺子，也就不会带东西；保险起见给个默认。 */
  quick: 'paper',
};

export function carryKindFor(kind: TaskKind): CarryKind {
  return CARRY_BY_KIND[kind];
}
