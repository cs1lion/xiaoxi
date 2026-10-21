/** 信仰结算：不发行货币，只让世界多一件东西。
 *
 * 货币一定长出"最优解"和通胀；这里改成不可花掉、不贬值的累积改变。
 * 每交付一单攒一点，攒满就在世界里落一个摆件。
 */

import type { PropKind, WorldProp } from './types.ts';
import type { GridPoint } from './types.ts';

export const POINTS_PER_TASK = 1;
export const MILESTONE_POINTS = 3;

/** 按里程碑轮换，保证世界不会堆成一排同样的东西。 */
const MILESTONE_CYCLE: readonly { kind: PropKind; label: string }[] = [
  { kind: 'lamp', label: '一盏路灯' },
  { kind: 'tree', label: '一棵小树' },
  { kind: 'banner', label: '一面旗' },
];

export function milestoneFor(milestoneIndex: number): { kind: PropKind; label: string } {
  return MILESTONE_CYCLE[milestoneIndex % MILESTONE_CYCLE.length];
}

/** 世界记忆落在祭坛周围，位置由落点决定，不由调用方指定。 */
export function nextProp(
  milestoneIndex: number,
  anchor: GridPoint,
  walkable: (p: GridPoint) => boolean,
): WorldProp | null {
  const { kind, label } = milestoneFor(milestoneIndex);
  const ring = Math.floor(milestoneIndex / 8) + 2;
  const candidates = ringPositions(anchor, ring);
  const spot = candidates.find((p) => walkable(p));
  if (spot === undefined) return null;
  return {
    id: `prop-${milestoneIndex}`,
    kind,
    at: spot,
    label,
  };
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
