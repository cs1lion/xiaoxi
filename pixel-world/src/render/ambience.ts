/** 世界的日常动静：白天的鸟、黄昏的蝙蝠。
 *
 * 为什么放在渲染层而不是 Simulation：
 *   1. **它们不是世界的事实。** 世界只认任务、建筑、交付这些能在存档里重现的
 *      东西。鸟停在枝头不该进存档——读档时鸟还在原处，那才是错的。
 *   2. **确定性靠 `now` 就能成立。** 每只鸟的位置是 `now` 的纯函数，
 *      同一时刻永远同一组位置，截图可复现、回放一致，不需要状态机。
 *   3. **不占快照。** 快照多一个字段就要动 save.ts 的叶子清单，
 *      而这份清单是 v1 冻结的形状。
 *
 * 与 `phase-view.ts` 的萤火虫同一个思路：那里是固定的八个点做呼吸，
 * 这里是几只鸟按固定路径飞。区别是鸟要**移动**，所以位置由 now 算。
 */

import type { GridPoint } from '../world/types.ts';
import type { PhaseId } from './day-phase.ts';

/** 会动的东西的种类。bat 只在黄昏与夜里出现。 */
export type AmbienceKind = 'bird' | 'bat';

export interface AmbienceActor {
  readonly kind: AmbienceKind;
  /** 世界格坐标（浮点）。渲染层自己乘 TILE_PX。 */
  readonly at: { readonly x: number; readonly y: number };
  /** 朝向：1 向右、-1 向左。翅膀与身体按它镜像。 */
  readonly facing: 1 | -1;
  /** 拍翅相位 0..1，用来在两种翅膀姿态之间切。 */
  readonly flap: number;
}

/**
 * 一天的哪些时刻有鸟、有蝙蝠。
 *
 * 鸟在白天（晨雾、正午）飞，黄昏开始变少，入夜就没有了。
 * 蝙蝠反过来：黄昏与夜里。
 */
export const AMBIENCE_BY_PHASE: Readonly<Record<PhaseId, { birds: number; bats: number }>> = {
  dawn: { birds: 3, bats: 0 },
  noon: { birds: 4, bats: 0 },
  dusk: { birds: 1, bats: 2 },
  night: { birds: 0, bats: 2 },
};

/** 鸟的巡航高度：在建筑上方，避免被楼挡住（世界格，向上为负）。 */
const BIRD_ALTITUDE = -0.35;
/** 蝙蝠比鸟低一档：它们贴着屋檐绕。 */
const BAT_ALTITUDE = -0.15;

/**
 * 一条水平来回飞的轨道。
 *
 * 为什么不走复杂曲线：像素世界里三只鸟各飞一条正弦曲线，看起来像三只
 * 被线拴着的风筝。水平来回加一点上下起伏，读起来才像"掠过"。
 */
interface Lane {
  /** 起点格坐标。 */
  readonly x: number;
  readonly y: number;
  /** 来回一次的半周期（毫秒）。越小越快。 */
  readonly spanMs: number;
  /** 水平往返的半幅（格）。 */
  readonly amplitude: number;
  /** 起点相位 0..1，让几条轨道错开。 */
  readonly offset: number;
}

/** 固定的轨道。写死而不是随机：随机种子会让截图不复现。 */
const BIRD_LANES: readonly Lane[] = [
  { x: 46, y: 28, spanMs: 9000, amplitude: 7, offset: 0 },
  { x: 52, y: 44, spanMs: 11000, amplitude: 9, offset: 0.35 },
  { x: 40, y: 38, spanMs: 7600, amplitude: 6, offset: 0.62 },
  { x: 57, y: 33, spanMs: 12400, amplitude: 8, offset: 0.85 },
];

const BAT_LANES: readonly Lane[] = [
  { x: 44, y: 40, spanMs: 5200, amplitude: 5, offset: 0.2 },
  { x: 55, y: 30, spanMs: 6100, amplitude: 6, offset: 0.7 },
];

/**
 * 当前该画哪些会动的东西。
 *
 * 纯函数：只读 `now`（模拟时刻，毫秒）与 `phase`，不读墙钟、不碰 Phaser。
 * `now` 用模拟时刻而不是 `Date.now()`，这样暂停世界时鸟也停——
 * 否则拖动进度或暂停时会看到世界静止而鸟照飞。
 */
export function ambienceAt(now: number, phase: PhaseId): readonly AmbienceActor[] {
  const recipe = AMBIENCE_BY_PHASE[phase];
  const actors: AmbienceActor[] = [];

  for (let i = 0; i < recipe.birds; i += 1) {
    const lane = BIRD_LANES[i % BIRD_LANES.length]!;
    actors.push(actorOn(lane, now, 'bird', BIRD_ALTITUDE));
  }
  for (let i = 0; i < recipe.bats; i += 1) {
    const lane = BAT_LANES[i % BAT_LANES.length]!;
    actors.push(actorOn(lane, now, 'bat', BAT_ALTITUDE));
  }
  return actors;
}

/**
 * 一条轨道上 `now` 时刻的位置。
 *
 * 水平用三角波（`Math.sin` 的绝对值折出来的形状），所以是匀速来回、
 * 到端点立刻掉头，而不是在端点减速。鸟在枝头不会减速，它们掠过去。
 */
function actorOn(lane: Lane, now: number, kind: AmbienceKind, altitude: number): AmbienceActor {
  const t = ((now / lane.spanMs + lane.offset) % 1 + 1) % 1;
  // 三角波：0→1→0，周期 1。
  const wave = t < 0.5 ? t * 2 : 2 - t * 2;
  const x = lane.x - lane.amplitude + wave * lane.amplitude * 2;
  // 朝 1 表示从右往左飞。
  const facing: 1 | -1 = t < 0.5 ? 1 : -1;
  // 上下起伏：高度 ±0.35 格，周期是水平的两倍（一次往返浮沉两次太忙）。
  const bob = Math.sin(t * Math.PI * 4) * 0.35;
  return {
    kind,
    at: { x, y: lane.y + altitude + bob },
    facing,
    // 拍翅 8 次一个往返。整数除法保证同一刻永远同一相位。
    flap: (now / 140 + lane.offset * 8) % 1,
  };
}

/** 一行摘要，给调试面板与测试用。 */
export function describeAmbience(now: number, phase: PhaseId): string {
  const actors = ambienceAt(now, phase);
  const birds = actors.filter((actor) => actor.kind === 'bird').length;
  const bats = actors.filter((actor) => actor.kind === 'bat').length;
  if (birds === 0 && bats === 0) return '天上空着';
  const parts: string[] = [];
  if (birds > 0) parts.push(`${birds} 只鸟`);
  if (bats > 0) parts.push(`${bats} 只蝙蝠`);
  return `天上有 ${parts.join('、')}`;
}

/** 轨道上的一个取样点，测试用它验「鸟真的在动」。 */
export function lanePointAt(
  kind: AmbienceKind,
  index: number,
  now: number,
): GridPoint & { readonly facing: 1 | -1 } {
  const lane = (kind === 'bird' ? BIRD_LANES : BAT_LANES)[index] ?? BIRD_LANES[0]!;
  const actor = actorOn(lane, now, kind, BIRD_ALTITUDE);
  return { x: actor.at.x, y: actor.at.y, facing: actor.facing };
}
