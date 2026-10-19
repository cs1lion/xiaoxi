/** 小人动画采样：12fps，帧号只由 simulation 时间（`WorldSnapshot.now`）决定。
 *
 * 为什么单独一个文件：这里的逻辑必须是纯函数，测试能直接用 node 加载；
 * `villager.ts` 引了 Phaser，在 node 下加载不了，所以把“帧号/整数像素”摘出来。
 *
 * 纪律：
 *   - 帧号 = floor(now / (1000/12)) % 帧数，与 rAF、帧率、掉帧都无关；
 *   - 所有偏移都是整数像素，没有逐帧浮点晃动；
 *   - 只做姿态，不新增颜色、不 tint、不改仿真。
 */

import type { VillagerMode } from '../world/types.ts';

export const ANIM_FPS = 12;
export const FRAME_MS = 1000 / ANIM_FPS;

/** 待机 2 帧：呼吸一上一下（整数像素）。 */
export const IDLE_FRAMES = 2;
/** 走路 4 帧：左右脚交替前伸 + 1px 起伏。 */
export const WALK_FRAMES = 4;

export interface VillagerPose {
  /** 当前帧号（0..frameCount-1）。 */
  readonly frame: number;
  readonly frameCount: number;
  /** 整体上下偏移，整数像素（呼吸/走路起伏/行礼）。 */
  readonly offsetY: number;
  /** 左脚前伸，整数像素（-1/0/1）。 */
  readonly footLeft: number;
  /** 右脚前伸，整数像素（-1/0/1）。 */
  readonly footRight: number;
}

/** 走路态：到工位、回祭坛、躲雨。其余模式一律按待机采样。 */
export function isWalkingMode(mode: VillagerMode): boolean {
  return mode === 'toWork' || mode === 'toAltar' || mode === 'sheltering';
}

/** simulation 时间 → 12fps 的步号。负数也能稳定回绕。 */
export function animStep(nowMs: number): number {
  if (!Number.isFinite(nowMs)) return 0;
  return Math.floor(nowMs / FRAME_MS);
}

/** simulation 时间 + 帧数 → 帧号。同一时间必得同一帧号，与渲染时机无关。 */
export function animFrame(nowMs: number, frameCount: number): number {
  if (frameCount <= 0) return 0;
  const step = animStep(nowMs);
  return ((step % frameCount) + frameCount) % frameCount;
}

/** 整数像素取整：位置只允许整数像素，禁止子像素抖动。
 *
 * `Math.round(-0.5)` 会返回 `-0`（`Object.is(-0, 0) === false`，`strictEqual` 会红），
 * 这里把 `-0` 归一成 `0`；两种写法在像素上是同一个值。
 */
export function pixelAt(worldPx: number): number {
  const rounded = Math.round(worldPx);
  return rounded === 0 ? 0 : rounded;
}

const IDLE_POSES: readonly Omit<VillagerPose, 'frame' | 'frameCount'>[] = [
  { offsetY: 0, footLeft: 0, footRight: 0 },
  { offsetY: 1, footLeft: 0, footRight: 0 },
];

const WALK_POSES: readonly Omit<VillagerPose, 'frame' | 'frameCount'>[] = [
  { offsetY: 0, footLeft: 1, footRight: 0 },
  { offsetY: 1, footLeft: 0, footRight: 1 },
  { offsetY: 0, footLeft: -1, footRight: 0 },
  { offsetY: 1, footLeft: 0, footRight: -1 },
];

/** 表情/动作本身带的整数偏移：行礼下蹲 2px、交付前倾 1px。 */
function modeLift(mode: VillagerMode): number {
  if (mode === 'saluting') return -2;
  if (mode === 'delivering') return -1;
  return 0;
}

/** 某一时刻的姿态。纯函数：同参数 → 同结果，不读时钟、不写外部状态。 */
export function villagerPose(mode: VillagerMode, nowMs: number): VillagerPose {
  const frames = isWalkingMode(mode) ? WALK_POSES : IDLE_POSES;
  const frame = animFrame(nowMs, frames.length);
  const base = frames[frame] as Omit<VillagerPose, 'frame' | 'frameCount'>;
  return {
    frame,
    frameCount: frames.length,
    offsetY: base.offsetY + modeLift(mode),
    footLeft: base.footLeft,
    footRight: base.footRight,
  };
}
