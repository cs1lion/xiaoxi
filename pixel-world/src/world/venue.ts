/** 去哪栋铺子干活：镇内还是郊野。
 *
 * 这条规则是「空间映射能力」的落点：
 *   - **台账的活**是本地确定性工作（改文件、跑脚本、整理），该在镇内做；
 *   - **神谕派的长活**是探索性的（查资料、爬取、长文档），去郊野。
 *
 * 为什么不按活类型硬分：同一个 `research` 既可能是台账里的一条督办，
 * 也可能是你对世界说的一句话。区别在**谁派的**，不在做什么。
 *
 * 纯函数，不碰网格也不碰渲染。
 */

import type { TaskKind } from './types.ts';

/** 谁派的活。`sandbox` 与 `oracle` 都算「你派的」，走探索侧。 */
export type VenueSource = 'ledger' | 'oracle';

export interface VenueCandidate {
  readonly instanceId: string;
  /** null 表示这栋楼不接活。 */
  readonly boundTask: TaskKind | null;
  /** 是否在郊野（核心镇与工坊环之外）。 */
  readonly isWild: boolean;
  /** 铺子已被占、在施工、或故障中。 */
  readonly busy: boolean;
}

/**
 * 挑一栋铺子。
 *
 * 规则：
 *   - 台账的活只认镇内；镇内忙就返回 null，让它排队，而不是塞给郊野；
 *   - 你派的活优先郊野，郊野忙再退回镇内；
 *   - 同类多个候选按传入顺序取第一个可用的，保证确定性。
 */
export function pickVenue(
  candidates: readonly VenueCandidate[],
  kind: TaskKind,
  source: VenueSource,
): VenueCandidate | null {
  const matching = candidates.filter((item) => item.boundTask === kind && !item.busy);
  if (matching.length === 0) return null;

  if (source === 'ledger') {
    return matching.find((item) => !item.isWild) ?? null;
  }
  return matching.find((item) => item.isWild) ?? matching.find((item) => !item.isWild) ?? null;
}
