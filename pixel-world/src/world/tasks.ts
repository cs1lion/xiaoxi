/** 任务档位：每类活多长、在哪做、失败长什么样。
 *
 * 长短任务分级就靠这张表：短活原地做，长活才值得派她走进铺子。
 * 时间常量集中在这里，测试可以直接读，不用在断言里重抄一遍数字。
 */

import type { TaskKind, TaskProfile, WorkshopTask } from './types.ts';

export const TASK_PROFILES: Readonly<Record<TaskKind, TaskProfile>> = {
  code: { estimateMs: 6000, venue: 'workshop', label: '写代码' },
  research: { estimateMs: 4200, venue: 'workshop', label: '查资料' },
  writing: { estimateMs: 3200, venue: 'workshop', label: '整理便笺' },
  quick: { estimateMs: 1200, venue: 'in_place', label: '顺手查一件小事' },
};

/** 铺子只接这三种。`quick` 不进铺子，所以这里不能收它。 */
export const WORKSHOP_TASKS: readonly WorkshopTask[] = ['code', 'research', 'writing'];

export function isWorkshopTask(kind: TaskKind): kind is WorkshopTask {
  return kind !== 'quick';
}

export function isLongTask(kind: TaskKind): boolean {
  return TASK_PROFILES[kind].venue === 'workshop';
}

/** 报错率写死为 0：这个原型里没有真实工具，伪造失败会变成假动作。 */
export function shouldOverheat(): boolean {
  return false;
}
