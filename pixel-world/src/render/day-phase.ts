/** 一天四刻：只决定怎么打光，不决定世界怎么运转。
 *
 * 这一层是纯函数，不碰 Phaser，也不读世界状态。`Simulation` 永远不调用它——
 * 时辰只喂渲染层，否则固定步长与确定性回放就不再成立（ADR_WORLD_ALIVE §2）。
 *
 * 四刻不是「四档压暗浓度」。实测只改浓度时，晨与昏只差 3.43 luma、1.3% 像素，
 * 屏幕上分不出来。所以每刻同时改三样：压暗的色与浓、亮光的强度、内容态
 * （亮窗数量、屋脊受光、贴地雾带、萤火）。依据见 proto/day-phase.html 与 ADR 附录 A。
 */

import { WORLD } from '../world/palette-data.ts';

export type PhaseId = 'dawn' | 'noon' | 'dusk' | 'night';

/** 一天的顺序。用于判断「下一拍该往哪一刻走」。 */
export const PHASE_ORDER: readonly PhaseId[] = ['dawn', 'noon', 'dusk', 'night'];

export interface PhaseRecipe {
  readonly label: string;
  /** 压暗层的颜色：每刻不同，冷暖是四刻可辨的主要来源。 */
  readonly dimColor: number;
  /** 压暗浓度 0..1。 */
  readonly dim: number;
  /** 窗、门、灯、祭坛的暖光强度倍数。 */
  readonly glow: number;
  /** 亮着的窗数（0 或 2）。白天不亮灯，黄昏与夜里亮。 */
  readonly litWindows: number;
  /** 屋脊与檐口的受光强度。黄昏独有，用来把黄昏和正午分开。 */
  readonly ridge: number;
  /** 贴地雾带高度（像素）与浓度。晨雾独有。 */
  readonly mistBand: number;
  readonly mist: number;
  /** 萤火只在夜里出现，且下雨时隐藏。 */
  readonly fireflies: boolean;
}

/**
 * 四刻配方。颜色全部取自现有 24 色，不新增第 25 色。
 *
 * 为什么黄昏用 `ink` 而不是 `ember`：暖色叠在草地上会**提亮**画面，
 * 用 ember 压暗会让黄昏比正午还亮，那是错的。`ink` 是 24 色里唯一暗且偏暖的一档。
 */
export const PHASES: Readonly<Record<PhaseId, PhaseRecipe>> = {
  dawn: {
    label: '晨雾',
    dimColor: WORLD.cloakDark,
    // 晨光比正午暗一档，但要留住「天亮着」的读感，所以不超过 0.2。
    dim: 0.2,
    glow: 0.25,
    litWindows: 0,
    ridge: 0,
    mistBand: 96,
    mist: 0.5,
    fireflies: false,
  },
  noon: {
    label: '正午',
    dimColor: WORLD.deepShade,
    dim: 0,
    glow: 0,
    litWindows: 0,
    ridge: 0,
    mistBand: 0,
    mist: 0,
    fireflies: false,
  },
  dusk: {
    label: '黄昏',
    dimColor: WORLD.ink,
    // 0.08 太轻：视野里没有建筑时，黄昏与正午几乎一样（实测 0.6%）。
    // 提到 0.16 仍明显轻于夜（0.46），但足够让两刻分开。
    dim: 0.16,
    glow: 1.2,
    litWindows: 2,
    ridge: 0.55,
    mistBand: 0,
    mist: 0,
    fireflies: false,
  },
  night: {
    label: '夜',
    dimColor: WORLD.deepShade,
    dim: 0.46,
    glow: 1,
    litWindows: 2,
    ridge: 0,
    mistBand: 0,
    mist: 0,
    fireflies: true,
  },
};

/** 每刻的起止小时（本地时间，左闭右开）。凌晨 5 点前仍算昨夜。 */
const BOUNDARIES: readonly { readonly phase: PhaseId; readonly fromHour: number }[] = [
  { phase: 'night', fromHour: 0 },
  { phase: 'dawn', fromHour: 5 },
  { phase: 'noon', fromHour: 9 },
  { phase: 'dusk', fromHour: 17 },
  { phase: 'night', fromHour: 20 },
];

/**
 * 本地时刻 → 相位。纯函数：同样的 epoch 毫秒永远得到同一刻。
 *
 * 用本地时间是因为这个世界的规则是「跟着你的作息走」，不是 UTC。
 */
export function phaseAt(epochMs: number): PhaseId {
  if (!Number.isFinite(epochMs)) return 'noon';
  const hour = new Date(epochMs).getHours();
  let current: PhaseId = 'night';
  for (const boundary of BOUNDARIES) {
    if (hour >= boundary.fromHour) current = boundary.phase;
  }
  return current;
}

/** 解析 URL 里的时相参数。认不出就返回 null，让调用方退回真实时间。 */
export function parsePhaseParam(raw: string | null): PhaseId | null {
  if (raw === null) return null;
  const value = raw.trim().toLowerCase();
  if (value === 'dawn' || value === 'morning') return 'dawn';
  if (value === 'noon' || value === 'day') return 'noon';
  if (value === 'dusk' || value === 'evening') return 'dusk';
  if (value === 'night') return 'night';
  return null;
}
