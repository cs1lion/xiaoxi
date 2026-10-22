/** 迷雾渲染：把还没长出来的地方压成一片暗。
 *
 * 只压暗、不遮挡：玩家仍能隐约看见地形轮廓，读成「还没去过」而不是「一块黑布」。
 * 这与 ART_SPEC 的 alpha 纪律一致——压暗属于允许的三类用法之一。
 *
 * 绘制手法：按格铺半透明块，格与格之间不描边。迷雾的边界靠**渐隐**处理，
 * 不做硬边圆形，否则会像一圈描边。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import { WORLD } from '../world/palette-data.ts';

export interface FogView {
  update(fog: { readonly center: { readonly x: number; readonly y: number }; readonly revealedRadius: number }): void;
}

const FOG_DEPTH = 7950;
/** 边缘几格渐变，避免迷雾边界是一条硬线。 */
const FADE_CELLS = 14;

export function createFogView(
  scene: Phaser.Scene,
  gridWidth: number,
  gridHeight: number,
): FogView {
  const g = scene.add.graphics();
  g.setDepth(FOG_DEPTH);
  let lastKey = '';

  return {
    update(fog) {
      // 半径不变就不重画：迷雾是静态的，每帧重画纯属浪费。
      const key = `${fog.center.x},${fog.center.y},${fog.revealedRadius}`;
      if (key === lastKey) return;
      lastKey = key;
      g.clear();

      // 按**像素**而不是按格算距离：按格填会在边界留下直角阶梯，
      // 与林地噪声同一个毛病。逐像素铺太慢，所以按 4px 的小块铺——
      // 4px 的边界在 2 倍显示下已经是 8px 的过渡，看不出阶梯。
      const BLOCK = 4;
      for (let py = 0; py < gridHeight * TILE_PX; py += BLOCK) {
        for (let px = 0; px < gridWidth * TILE_PX; px += BLOCK) {
          const cx = px / TILE_PX;
          const cy = py / TILE_PX;
          const distance = Math.hypot(cx - fog.center.x, cy - fog.center.y);
          if (distance <= fog.revealedRadius) continue;

          const into = Math.min(1, (distance - fog.revealedRadius) / FADE_CELLS);
          // alpha 从 0 起跳，不是从 0.12：从 0.12 起会在边界画出一条可见的圆边，
          // 看起来像描边而不是雾。用 smoothstep 让过渡两端都平缓。
          const alpha = 0.44 * (into * into * (3 - 2 * into));
          // 用冷色 water 而不是夜里的 deepShade：两者都压暗，但迷雾要读成
          // "这里还没去过"，不是"天黑了"。冷色带一点蓝，与夜晚的暖窗对比明显。
          g.fillStyle(WORLD.water, alpha);
          g.fillRect(px, py, BLOCK, BLOCK);
        }
      }
    },
  };
}
