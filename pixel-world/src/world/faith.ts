/** 信仰结算：不发行货币，只让世界多一件东西。
 *
 * 货币一定长出"最优解"和通胀；这里改成不可花掉、不贬值的累积改变。
 * 每交付一单攒一点，攒满就在世界里落一个摆件。
 *
 * 摆件不是奖品，是**记忆的形状**：它记着把自己换来的那一单。
 * 所以选型和文案都从那一单派生，而不是按里程碑轮换。
 */

import type { GridPoint, PropKind, TaskKind, WorldProp } from './types.ts';

export const POINTS_PER_TASK = 1;
export const MILESTONE_POINTS = 3;

/** 把点数推过线的那一单。摆件的选型、外观、纸面文案都从它派生。 */
export interface FaithSource {
  readonly kind: TaskKind;
  readonly taskId: string;
  readonly summary: string;
}

/**
 * 按活类型选摆件。钱不写进世界，但形状要说明来源：
 * 写代码换来的是一盏灯（夜里还亮着），查资料换来的是一棵树（长在祭坛边），
 * 整理文稿换来的是一面旗（写着那天的字）。
 *
 * 短活（quick）不进铺子，也就不留痕：顺手办的小事换不来地标。
 */
const PROP_FOR_TASK: Readonly<Record<TaskKind, PropKind>> = {
  code: 'lamp',
  research: 'tree',
  writing: 'banner',
  quick: 'lamp',
};

/** 纸面用的一句话：这件东西是从哪一单来的。 */
const LABEL_FOR_TASK: Readonly<Record<TaskKind, string>> = {
  code: '一盏路灯',
  research: '一棵小树',
  writing: '一面旗',
  quick: '一盏小灯',
};

export function propKindFor(kind: TaskKind): PropKind {
  return PROP_FOR_TASK[kind];
}

export function propLabelFor(kind: TaskKind): string {
  return LABEL_FOR_TASK[kind];
}

/** 世界记忆落在祭坛周围，位置由落点决定，不由调用方指定。
 *
 * `source` 记下换来这件东西的那条活，纸面据此写「哪一单换的」。
 * 老档迁移来的摆件没有来源，三个字段都是 null，绘制层与纸面必须容忍。
 *
 * `taken` 是已经落下的摆件位置。摆件不占网格，光靠 `walkable` 判断会让第二件
 * 落在第一件头上（同一个环、同一个首个空格）——画面上就是两盏灯叠成一盏。
 */
export function nextProp(
  milestoneIndex: number,
  anchor: GridPoint,
  walkable: (p: GridPoint) => boolean,
  source: FaithSource | null = null,
  taken: readonly GridPoint[] = [],
): WorldProp | null {
  const kind = source === null ? 'lamp' : propKindFor(source.kind);
  const label = source === null ? '一盏小灯' : propLabelFor(source.kind);
  const occupied = new Set(taken.map((p) => `${p.x},${p.y}`));
  const ring = Math.floor(milestoneIndex / 8) + 2;
  // 当前环满了就往外扩一圈，而不是放弃落件。
  for (let radius = ring; radius <= ring + 12; radius += 1) {
    const candidates = ringPositions(anchor, radius);
    const spot = candidates.find(
      (p) => walkable(p) && !occupied.has(`${p.x},${p.y}`),
    );
    if (spot !== undefined) {
      return {
        id: `prop-${milestoneIndex}`,
        kind,
        at: spot,
        label,
        sourceTaskKind: source?.kind ?? null,
        sourceTaskId: source?.taskId ?? null,
        sourceSummary: source?.summary ?? null,
      };
    }
  }
  return null;
}

/**
 * 纸面上的一行来源说明。没有来源的老摆件如实说「来路不明」，不编。
 *
 * 只取摘要的前 24 个码点：状态行是一行字，长标题会把其余信息挤掉。
 */
export function describePropSource(prop: WorldProp): string {
  // 神迹落下的东西没有任务来源，但摘要写明是「神迹：点灯」这类；
  // 先看摘要，才不会把神迹误报成「来路不明」。
  if (prop.sourceSummary !== null && prop.sourceSummary.trim().startsWith('神迹')) {
    return `${prop.label}（${prop.sourceSummary.trim()}）`;
  }
  if (prop.sourceTaskKind === null) return `${prop.label}（来路不明）`;
  if (prop.sourceSummary === null || prop.sourceSummary.trim() === '') {
    return `${prop.label}（${propLabelFor(prop.sourceTaskKind)}）`;
  }
  const points = Array.from(prop.sourceSummary.trim());
  const excerpt = points.length <= 24 ? points.join('') : `${points.slice(0, 24).join('')}…`;
  return `${prop.label}（那单「${excerpt}」）`;
}

function ringPositions(anchor: GridPoint, radius: number): GridPoint[] {
  const out: GridPoint[] = [];
  for (let dx = -radius; dx <= radius; dx += 1) {
    out.push({ x: anchor.x + dx, y: anchor.y - radius });
    out.push({ x: anchor.x + dx, y: anchor.y + radius });
  }
  for (let dy = -radius + 1; dy <= radius - 1; dy += 1) {
    out.push({ x: anchor.x - radius, y: anchor.y + dy });
    out.push({ x: anchor.x + radius, y: anchor.y + dy });
  }
  return out;
}
