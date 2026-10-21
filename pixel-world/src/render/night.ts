/** 夜景：纯渲染态，不写入世界快照，也不改变台账。
 *
 * 做法：
 *   - 用 `deepShade` 罩一层冷暗（0.42 alpha），保留 1px 轮廓与 24 色材质差；
 *   - 窗、门、路灯、祭坛再叠 `warmLight`/`amber` 的 ADD 光晕，让暖光从冷夜里透出来；
 *   - 夜雨时隐藏萤火，避免和雨滴抢亮点；
 *   - 开关只影响渲染，`?night=1` 或页面按 N 切换。
 *
 * 不用整屏 CSS filter：那会把暖窗一起压灰，夜景会变成“白天调暗”，不是夜景。
 */

import Phaser from 'phaser';
import { GRID_WIDTH, GRID_HEIGHT, TILE_PX } from '../world/setup.ts';
import type { WorldSnapshot } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { propFootY } from './prop-depth.ts';

const WIDTH = GRID_WIDTH * TILE_PX;
const HEIGHT = GRID_HEIGHT * TILE_PX;
const DIM_DEPTH = 8009;
const GLOW_DEPTH = 8010;
const FIREFLY_DEPTH = 8011;
const FADE_MS = 650;

/** 固定萤火位置：只放在空地上，不跟着仿真走，避免夜景变成随机噪声。 */
const FIREFLIES: readonly { readonly x: number; readonly y: number }[] = [
  { x: 64, y: 190 },
  { x: 118, y: 258 },
  { x: 184, y: 118 },
  { x: 220, y: 300 },
  { x: 296, y: 186 },
  { x: 352, y: 252 },
  { x: 412, y: 300 },
  { x: 430, y: 148 },
];

export interface NightView {
  setEnabled(enabled: boolean): void;
  toggle(): boolean;
  isEnabled(): boolean;
  update(snapshot: WorldSnapshot, delta: number): void;
}

export function createNightView(scene: Phaser.Scene): NightView {
  const dim = scene.add.rectangle(0, 0, WIDTH, HEIGHT, WORLD.deepShade, 0).setOrigin(0, 0);
  dim.setDepth(DIM_DEPTH);

  const glows = scene.add.graphics();
  glows.setDepth(GLOW_DEPTH);
  glows.setBlendMode(Phaser.BlendModes.ADD);

  const fireflies = scene.add.graphics();
  fireflies.setDepth(FIREFLY_DEPTH);
  fireflies.setBlendMode(Phaser.BlendModes.ADD);

  let enabled = false;
  let amount = 0;
  let glowKey = '';

  return {
    setEnabled(next: boolean): void {
      enabled = next;
    },
    toggle(): boolean {
      enabled = !enabled;
      return enabled;
    },
    isEnabled(): boolean {
      return enabled;
    },
    update(snapshot: WorldSnapshot, delta: number): void {
      const target = enabled ? 1 : 0;
      const step = Math.min(1, delta / FADE_MS);
      if (amount < target) amount = Math.min(target, amount + step);
      else if (amount > target) amount = Math.max(target, amount - step);

      const visible = amount > 0.001;
      dim.setVisible(visible);
      glows.setVisible(visible);
      fireflies.setVisible(visible);
      dim.setFillStyle(WORLD.deepShade, 0.3 * amount);
      glows.setAlpha(amount);
      fireflies.setAlpha(amount);

      if (!visible) {
        glows.clear();
        fireflies.clear();
        // 关闭时清掉缓存，下次打开才会重新画光晕。
        glowKey = '';
        return;
      }

      const key = snapshot.buildings
        .map(
          (b) =>
            `${b.instanceId}:${b.origin.x},${b.origin.y},${b.footprint.w},${b.footprint.h},${b.door.x},${b.door.y}`,
        )
        .join('|') +
        '#' +
        snapshot.props.map((p) => `${p.id}:${p.at.x},${p.at.y},${p.kind}`).join('|');
      if (key !== glowKey) {
        glowKey = key;
        drawGlows(glows, snapshot);
      }
      drawFireflies(fireflies, snapshot);
    },
  };
}

/** 画暖光光晕：外圈 warmLight、内芯 amber；全部是 1px 阶梯矩形，不用 blur。 */
function drawGlows(g: Phaser.GameObjects.Graphics, snapshot: WorldSnapshot): void {
  const addGlow = (cx: number, cy: number, w: number, h: number, alpha: number): void => {
    // 外圈 amber、内芯 warmLight：矩形比 warmLight 平铺更暖，不会在暗绿上泛青。
    g.fillStyle(WORLD.amber, alpha);
    g.fillRect(Math.round(cx - w / 2), Math.round(cy - h / 2), Math.round(w), Math.round(h));
    g.fillStyle(WORLD.amber, alpha * 0.55);
    g.fillRect(
      Math.round(cx - w / 2) + 1,
      Math.round(cy - h / 2) + 1,
      Math.max(1, Math.round(w) - 2),
      Math.max(1, Math.round(h) - 2),
    );
    g.fillStyle(WORLD.warmLight, alpha * 1.15);
    g.fillRect(
      Math.round(cx - w / 4),
      Math.round(cy - h / 4),
      Math.max(1, Math.round(w / 2)),
      Math.max(1, Math.round(h / 2)),
    );
  };

  for (const building of snapshot.buildings) {
    const px = building.origin.x * TILE_PX;
    const py = building.origin.y * TILE_PX;
    const w = building.footprint.w * TILE_PX;
    const h = building.footprint.h * TILE_PX;
    const roofH = Math.max(10, Math.round(h * 0.34));

    // 两扇窗：光晕落在窗框外沿，不改变窗本身的像素。
    for (const wx of [px + 6, px + w - 18]) {
      addGlow(wx + 4, py + roofH + 11, 13, 9, 0.11);
    }
    // 门口光池：略低到地面，读成“屋里的光漏到门前”。
    const dx = building.door.x * TILE_PX + 8;
    const dy = building.door.y * TILE_PX + 8;
    addGlow(dx, dy + 9, 18, 11, 0.1);
  }

  // 祭坛：视觉中心比逻辑格右偏 12px，光晕跟着视觉走。
  const ax = snapshot.altar.x * TILE_PX + 8 + 12;
  const ay = snapshot.altar.y * TILE_PX + 8;
  addGlow(ax, ay + 6, 20, 13, 0.12);

  // 路灯：灯芯在 propFootY 上方 14px。
  for (const prop of snapshot.props) {
    if (prop.kind !== 'lamp') continue;
    const lx = (prop.at.x + 0.5) * TILE_PX;
    const ly = propFootY(prop, 0) - 14;
    addGlow(lx, ly, 12, 9, 0.11);
  }
}

/** 萤火：1px 暖点，按仿真时间闪烁；夜雨时隐藏。 */
function drawFireflies(g: Phaser.GameObjects.Graphics, snapshot: WorldSnapshot): void {
  g.clear();
  if (snapshot.weather === 'rain') return;
  for (let i = 0; i < FIREFLIES.length; i += 1) {
    const point = FIREFLIES[i]!;
    const phase = Math.sin(snapshot.now / 700 + i * 1.9);
    if (phase < -0.15) continue;
    const alpha = 0.22 + 0.5 * ((phase + 1) / 2);
    g.fillStyle(WORLD.warmLight, alpha);
    g.fillRect(point.x, point.y, 1, 1);
    if (i % 3 === 0) {
      g.fillStyle(WORLD.amber, alpha * 0.55);
      g.fillRect(point.x + 1, point.y, 1, 1);
    }
  }
}
