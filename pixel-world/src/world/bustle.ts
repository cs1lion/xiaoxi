/** 镇子的热度：活儿多了，窗就亮着。
 *
 * 时辰决定"几点该点灯"，这里决定"有人在干活所以灯亮着"。
 * 两者叠在一起，镇子才有"活着"的读感：正午本该安静，但工坊在运转，
 * 那扇窗就亮着。
 *
 * 纯函数，不碰渲染。
 */

/** 每栋楼最多两扇窗。 */
const WINDOWS_PER_BUILDING = 2;

/**
 * 这栋楼该亮几扇窗。
 *
 * 规则：`max(时辰给的, 干活给的)`。取最大值而不是相加——
 * 窗只有两扇，相加会算出 3 扇这种不存在的东西。
 *
 * 干活只给 1 扇，不是 2：一栋楼"有人在忙"比"整栋楼都醒着"更可信。
 */
export function litWindowCount(
  phase: { readonly litWindows: number },
  working: boolean,
): number {
  const fromPhase = Math.min(WINDOWS_PER_BUILDING, Math.max(0, phase.litWindows));
  const fromWork = working ? 1 : 0;
  return Math.min(WINDOWS_PER_BUILDING, Math.max(fromPhase, fromWork));
}
