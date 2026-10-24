/** 迷雾的漂移：让雾「在动」，而不是一张静止的灰膜。
 *
 * 关键约束：迷雾本体已经烘焙成一张纹理（`bake.ts`）。**不能**每帧重烘整张图，
 * 那会把视野裁剪省下的性能全吃回去。
 *
 * 所以动画只发生在**边缘带**：雾团是少量独立的小对象，沿边缘缓慢漂移。
 * 它们不参与烘焙，数量固定（十几个），每帧的代价可以忽略。
 *
 * 纯函数：给定时间与位置，返回这一团的偏移与浓度。不用随机数，截图可复现。
 */

import type { GridPoint } from './types.ts';

/** 雾团数量。十几个足够读出「雾在动」，再多就开始像噪点。 */
export const DRIFT_PUFFS = 14;

/**
 * 角速度（弧度/秒）。
 *
 * 注意这是**角**速度不是线速度：轨道半径约 16 格，0.35 rad/s 对应 5.6 格/秒，
 * 那是绕圈不是漂移（实测 1 秒走 2.9 格）。取 0.03 对应约 0.5 格/秒——
 * 慢到不抢注意力，但盯着看能看出在动。
 */
const DRIFT_ANGULAR_SPEED = 0.03;

export interface DriftPuff {
  /** 世界格坐标。 */
  readonly at: { readonly x: number; readonly y: number };
  /** 0..1 的浓度，随时间起伏。 */
  readonly alpha: number;
  /** 半径（格）。 */
  readonly radius: number;
}

/**
 * 某一刻的雾团位置。
 *
 * 每团沿一个固定的圆轨道慢慢绕，轨道半径与相位由序号决定：
 * 这样同一个时间点永远得到同一组位置，截图与回放都稳。
 *
 * 只放在**迷雾边缘附近**：雾团的意义是让边界看起来在呼吸，
 * 铺满整个雾区反而会变成噪点。
 */
export function driftPuffs(
  center: GridPoint,
  revealedRadius: number,
  timeMs: number,
): readonly DriftPuff[] {
  const seconds = timeMs / 1000;
  const puffs: DriftPuff[] = [];
  for (let i = 0; i < DRIFT_PUFFS; i += 1) {
    // 序号决定轨道：角度均分，半径与速度略有差异，避免同步转动像齿轮。
    const baseAngle = (i / DRIFT_PUFFS) * Math.PI * 2;
    const angle = baseAngle + seconds * DRIFT_ANGULAR_SPEED * (0.6 + (i % 5) * 0.12);
    // 轨道贴着揭示边界：内圈略进、外圈略出，让边界两侧都有雾。
    const orbit = revealedRadius + ((i % 4) - 1.5) * 2.5;
    // 起伏频率也要低：0.7 rad/s 配 1.8 格振幅，本身就是 1.26 格/秒的抖动，
    // 比轨道还快。0.12 配 1.2 格，约 0.14 格/秒，读成"雾在舒展"。
    const wobble = Math.sin(seconds * 0.12 + i * 1.3) * 1.2;

    puffs.push({
      at: {
        x: center.x + Math.cos(angle) * (orbit + wobble),
        y: center.y + Math.sin(angle) * (orbit + wobble),
      },
      // 浓度在 0.35..0.75 之间呼吸，周期各不相同。
      alpha: 0.35 + 0.4 * ((Math.sin(seconds * 0.45 + i * 2.1) + 1) / 2),
      radius: 2.5 + (i % 3) * 0.8,
    });
  }
  return puffs;
}
