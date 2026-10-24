/** 世界的自我描述：把原始快照归纳成「世界现在长什么样」。
 *
 * 为什么需要：造物主要做选择，得先看得见世界。原始快照里有建筑数组、任务数组，
 * 但「哪类活还没有郊野铺子」「郊野还剩多少空地」这样的判断，不该让造物主自己
 * 从数组里数——那是把领域知识散到调用方去。
 *
 * 这里是**只读归纳**：不改世界，不另存真相，全部从快照推导。
 * 与 `report.ts` 同一个原则：能派生的不落档。
 */

import { WILD_PREFABS } from './setup.ts';
import { TASK_PROFILES, WORKSHOP_TASKS } from './tasks.ts';
import type { TaskKind, WorldSnapshot } from './types.ts';

/** 某一类活的铺子覆盖情况。 */
export interface KindCoverage {
  readonly kind: TaskKind;
  readonly town: number;
  readonly wild: number;
  readonly total: number;
}

export interface WorldState {
  readonly buildings: {
    readonly total: number;
    readonly town: number;
    readonly wild: number;
  };
  /** 每类长活的铺子覆盖。顺序与 `WORKSHOP_TASKS` 一致。 */
  readonly taskKinds: readonly KindCoverage[];
  readonly space: {
    /** 郊野已建的铺子数。 */
    readonly wildBuilt: number;
    /** 郊野还能建几栋（按分区里剩余的可建格估算，不是精确槽位）。 */
    readonly wildFree: number;
  };
  readonly activity: {
    readonly total: number;
    readonly busy: number;
    readonly idle: number;
  };
  /**
   * 缺口：造物主该补什么。
   *
   * 只列**确定缺的**：某类活没有郊野铺子。不猜「应该再多一栋」——
   * 那是审美判断，不该由归纳层替造物主做。
   */
  readonly gaps: {
    readonly missingWild: readonly TaskKind[];
  };
}

/** 把快照归纳成世界状态。纯函数，可离线测。 */
export function summarizeWorld(snapshot: WorldSnapshot): WorldState {
  const workshops = snapshot.buildings.filter((b) => b.boundTask !== null);

  const isWild = (prefabId: string): boolean => WILD_PREFABS.has(prefabId);
  const town = workshops.filter((b) => !isWild(b.prefabId)).length;
  const wild = workshops.filter((b) => isWild(b.prefabId)).length;

  const taskKinds: KindCoverage[] = WORKSHOP_TASKS.map((kind) => {
    const ofKind = workshops.filter((b) => b.boundTask === kind);
    const wildCount = ofKind.filter((b) => isWild(b.prefabId)).length;
    return {
      kind,
      town: ofKind.length - wildCount,
      wild: wildCount,
      total: ofKind.length,
    };
  });

  // 郊野还剩多少空地：按已建铺子的占地格数反推。这是估算，
  // 不是精确槽位数——精确值要问网格，而归纳层不持有网格。
  const wildFootprint = snapshot.buildings
    .filter((b) => isWild(b.prefabId))
    .reduce((sum, b) => sum + b.footprint.w * b.footprint.h, 0);

  const busy = snapshot.villagers.filter((v) => v.mode !== 'idle').length;

  return {
    buildings: {
      total: snapshot.buildings.length,
      town,
      wild,
    },
    taskKinds,
    space: {
      wildBuilt: wild,
      // 郊野四块分区合计约 384 格；减去已占与留白，按 40 格一栋粗估。
      wildFree: Math.max(0, Math.floor((384 - wildFootprint * 3) / 40)),
    },
    activity: {
      total: snapshot.villagers.length,
      busy,
      idle: snapshot.villagers.length - busy,
    },
    gaps: {
      missingWild: taskKinds.filter((entry) => entry.wild === 0).map((entry) => entry.kind),
    },
  };
}

/**
 * 每类活对应的**郊野**模板 id。
 *
 * 补缺时界面据此决定建哪栋。与 `setup.ts` 的 `WILD_PREFABS` 同源：
 * 那边是"哪些是郊野铺子"，这边是"哪类活该建哪栋"。
 */
export const WILD_PREFAB_OF: Readonly<Record<TaskKind, string>> = {
  code: 'gear_tower',
  research: 'crawler_mine',
  writing: 'ink_mill',
  quick: 'workshop_plot',
};

/**
 * 给界面用的一行摘要。
 *
 * 一行说完：镇内几栋、郊野几栋、还剩多少空地、缺什么。
 * 缺什么用纸面说法（「查资料」），不是内部枚举名——这一行是给人看的。
 */
export function describeWorldState(state: WorldState): string {
  const parts: string[] = [
    `镇内 ${state.buildings.town} 栋`,
    `郊野 ${state.buildings.wild} 栋`,
  ];
  // 每类活各几栋：这一行是玩家判断"世界长什么样"的主要依据，
  // 只说总数等于没说——3 栋可能是三类各一栋，也可能是两类各一栋加一栋空的。
  const perKind = state.taskKinds
    .map((entry) => `${TASK_PROFILES[entry.kind].label} 镇内${entry.town} / 郊野${entry.wild}`)
    .join('、');
  parts.push(`（${perKind}）`);
  parts.push(`空地约 ${state.space.wildFree} 栋`);
  if (state.gaps.missingWild.length > 0) {
    const labels = state.gaps.missingWild.map((kind) => TASK_PROFILES[kind].label);
    parts.push(`缺${labels.join('、')}的郊野铺子`);
  }
  return parts.join('　·　');
}
