/** 喂给意图模型的**世界现状**。
 *
 * 为什么需要：模型现在只看到一句话与模板表，不知道镇上几栋楼、谁在忙。
 * 「建个工坊」若已有工坊，由世界在结算时拒绝并说清——但那是**事后**才知道，
 * 用户白等一次往返。把现状给它，模型能在事前少犯这类错。
 *
 * 三条纪律，与 `intent-schema.ts` 同源：
 *
 * 1. **只读上下文，不是校验来源。** 模型看到「已有工坊」不代表它有权拒绝；
 *    真正的拒绝仍由世界判（`simulation.ts` 的 `build` 分支）。这里只减少
 *    模型的瞎猜，不接管任何判定。
 *
 * 2. **不含可推断的细节。** 不发完整建筑数组、不发每个格子、不发任务 id。
 *    发一串建筑清单，模型除了复述没有别的用途，还白烧 token。
 *
 * 3. **形状稳定。** 这份结构同时喂 `intent-prompt.ts`（离线推演与测试）与
 *    `world_intent.py`（线上）。两处都从这里的字段渲染，改字段只改一处。
 */

import { WILD_PREFABS } from './setup.ts';
import { TASK_PROFILES, WORKSHOP_TASKS } from './tasks.ts';
import type { PlacedBuilding, TaskKind, VillagerSnapshot, WorldSnapshot } from './types.ts';

/**
 * 喂给模型的世界现状。
 *
 * 刻意做成"一句话能说清的量"：建筑清单、谁在忙、天气、缺口。
 * 每多一个字段就多一处两端要同步的地方。
 */
export interface IntentContext {
  /** 已有的建筑，按郊野 / 镇内分组，只给 id 与显示名。 */
  readonly built: {
    readonly town: readonly { readonly templateId: string; readonly name: string }[];
    readonly wild: readonly { readonly templateId: string; readonly name: string }[];
  };
  /** 每类活有几栋能接。`0` 表示这类活目前没地方做。 */
  readonly capacity: readonly { readonly taskKind: TaskKind; readonly count: number }[];
  /** 正在干活的人。空闲时不列。 */
  readonly busy: readonly { readonly name: string; readonly taskKind: TaskKind }[];
  /** 还有几条神谕在排队。 */
  readonly queued: number;
  readonly weather: 'clear' | 'rain';
  /** 已经建过、不能再建第二栋的模板。与 `simulation.ts` 的 build 拒绝一致。 */
  readonly exhausted: readonly string[];
}

/** 从快照归纳出喂模型的上下文。纯函数。 */
export function summarizeForIntent(snapshot: WorldSnapshot): IntentContext {
  const isWild = (building: PlacedBuilding): boolean => WILD_PREFABS.has(building.prefabId);
  const toEntry = (building: PlacedBuilding) => ({
    templateId: building.prefabId,
    name: building.displayName,
  });

  const town = snapshot.buildings.filter((b) => !isWild(b)).map(toEntry);
  const wild = snapshot.buildings.filter((b) => isWild(b)).map(toEntry);

  // 铺子的产能：按 boundTask 数，未建的不算。
  const capacity = WORKSHOP_TASKS.map((kind) => ({
    taskKind: kind,
    count: snapshot.buildings.filter((b) => b.boundTask === kind).length,
  }));

  // 谁在忙：taskId 指向的任务就是他在做的活。
  const busy: { name: string; taskKind: TaskKind }[] = [];
  for (const villager of snapshot.villagers) {
    const kind = busyKindOf(villager, snapshot);
    if (kind !== null) busy.push({ name: villager.name, taskKind: kind });
  }

  return {
    built: { town, wild },
    capacity,
    busy,
    queued: snapshot.pending.length,
    weather: snapshot.weather,
    // 世界拒绝「同一种铺子建第二栋」，所以每个已建的模板都算耗尽。
    exhausted: [...new Set(snapshot.buildings.map((b) => b.prefabId))],
  };
}

/** 这个人在做什么活；闲着返回 null。 */
function busyKindOf(villager: VillagerSnapshot, snapshot: WorldSnapshot): TaskKind | null {
  if (villager.taskId === null) return null;
  const task = snapshot.tasks.find((item) => item.id === villager.taskId);
  return task?.kind ?? null;
}

/**
 * 把上下文渲染成提示词里的一段。
 *
 * 为什么不用 JSON：模型要读的是"镇上有什么"，不是一份数据。JSON 会更长、
 * 更容易被它误当成输出格式的样例照抄。
 */
export function renderIntentContext(context: IntentContext): string {
  const lines: string[] = [];

  if (context.built.town.length === 0 && context.built.wild.length === 0) {
    lines.push('世界是空的，一栋楼都没有。');
  } else {
    lines.push(`镇内已有：${describeBuildings(context.built.town)}`);
    lines.push(`郊野已有：${describeBuildings(context.built.wild)}`);
  }

  const total = context.capacity.reduce((sum, entry) => sum + entry.count, 0);
  lines.push(
    `能接活的铺子 ${total} 栋（${context.capacity
      .map((entry) => `${TASK_PROFILES[entry.taskKind].label} ${entry.count}`)
      .join(' / ')}）。`,
  );

  if (context.busy.length > 0) {
    lines.push(
      `正在忙：${context.busy
        .map((item) => `${item.name}在做${TASK_PROFILES[item.taskKind].label}`)
        .join('、')}。`,
    );
  } else {
    lines.push('现在没人干活。');
  }

  if (context.queued > 0) lines.push(`还有 ${context.queued} 条神谕在排队。`);
  lines.push(context.weather === 'rain' ? '外面在下雨。' : '天晴。');

  if (context.exhausted.length > 0) {
    lines.push(
      `这些模板已经建过，不能再建第二栋：${context.exhausted.join('、')}。`,
    );
  }

  return lines.join('\n');
}

function describeBuildings(
  list: readonly { readonly name: string }[],
): string {
  if (list.length === 0) return '空着';
  return list.map((entry) => entry.name).join('、');
}
