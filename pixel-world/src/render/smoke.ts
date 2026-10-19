/** 铺子冒出的东西：代码铺冒烟、书阁飘纸、抄书斋落墨点。
 *
 * 每种 accent 一个发射器，活跃时按帧密度手动 emitParticleAt。
 * 不用 Phaser 的 frequency，因为活干完必须立刻停，不能残留半秒。
 */

import Phaser from 'phaser';
import type { PlacedBuilding, WorkAccent } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';

const TEXTURE_KEYS: Record<WorkAccent, string> = {
  smoke: 'px-smoke',
  pages: 'px-pages',
  ink: 'px-ink',
};

export interface AccentEmitter {
  readonly emitAt: (x: number, y: number, intensity: number) => void;
}

export function ensureAccentTextures(scene: Phaser.Scene): void {
  makeDot(scene, TEXTURE_KEYS.smoke, WORLD.pale, 4);
  makeDot(scene, TEXTURE_KEYS.pages, WORLD.pale, 3);
  makeDot(scene, TEXTURE_KEYS.ink, WORLD.cloakDark, 2);
}

export function createAccentEmitter(
  scene: Phaser.Scene,
  accent: WorkAccent,
): AccentEmitter {
  const emitter = scene.add.particles(0, 0, TEXTURE_KEYS[accent], {
    lifespan: accent === 'smoke' ? 1400 : 1100,
    speedY: { min: -34, max: -18 },
    speedX: accent === 'pages' ? { min: -22, max: 22 } : { min: -6, max: 10 },
    scale: { start: 1, end: accent === 'smoke' ? 2.1 : 0.6 },
    alpha: { start: 0.85, end: 0 },
    gravityY: accent === 'ink' ? 14 : 0,
    rotate: accent === 'pages' ? { min: -180, max: 180 } : 0,
    emitting: false,
  });
  emitter.setDepth(9000);
  return {
    emitAt(x: number, y: number, intensity: number): void {
      const count = Math.max(1, Math.round(intensity));
      emitter.emitParticleAt(x, y, count);
    },
  };
}

export function accentFor(building: PlacedBuilding): WorkAccent {
  return building.accent;
}

function makeDot(scene: Phaser.Scene, key: string, color: number, size: number): void {
  if (scene.textures.exists(key)) return;
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  g.fillStyle(color, 1);
  if (size <= 2) {
    g.fillRect(0, 0, 2, 2);
  } else {
    g.fillRect(0, 1, size, size - 1);
    g.fillRect(1, 0, size - 1, size);
  }
  g.generateTexture(key, size + 1, size + 1);
  g.destroy();
}
