/** 雨幕与压暗。浓淡来自世界快照的 0..1，渲染层只插值，不判断天气。 */

import Phaser from 'phaser';
import { GRID_HEIGHT, GRID_WIDTH, TILE_PX } from '../world/setup.ts';

const WIDTH = GRID_WIDTH * TILE_PX;
const HEIGHT = GRID_HEIGHT * TILE_PX;
const DROPS = 140;

export interface WeatherView {
  readonly drops: Phaser.GameObjects.Graphics;
  readonly dim: Phaser.GameObjects.Rectangle;
  readonly splash: Phaser.GameObjects.Graphics;
  readonly update: (intensity: number, delta: number) => void;
}

export function createWeatherView(scene: Phaser.Scene): WeatherView {
  const dim = scene.add.rectangle(0, 0, WIDTH, HEIGHT, 0x0a1420, 0).setOrigin(0, 0);
  dim.setDepth(8000);

  const drops = scene.add.graphics();
  drops.setDepth(8001);

  const splash = scene.add.graphics();
  splash.setDepth(8002);

  const xs: number[] = [];
  const ys: number[] = [];
  for (let i = 0; i < DROPS; i += 1) {
    xs.push(Math.random() * WIDTH);
    ys.push(Math.random() * HEIGHT);
  }

  return {
    drops,
    dim,
    splash,
    update(intensity, delta) {
      const visible = intensity > 0.02;
      drops.setVisible(visible);
      splash.setVisible(visible);
      dim.setFillStyle(0x0a1420, intensity * 0.28);
      if (!visible) {
        drops.clear();
        splash.clear();
        return;
      }
      const fall = (delta / 1000) * 260;
      drops.clear();
      drops.lineStyle(1, 0xb9cbd6, 0.35 + intensity * 0.35);
      for (let i = 0; i < DROPS; i += 1) {
        ys[i] = ys[i] === undefined ? 0 : ys[i] + fall;
        if (ys[i] > HEIGHT) {
          ys[i] = -Math.random() * 40;
          xs[i] = Math.random() * WIDTH;
        }
        const x = xs[i] ?? 0;
        const y = ys[i] ?? 0;
        if (i % 3 === 0) drops.lineBetween(x, y, x - 1, y + 5);
      }
      splash.clear();
      splash.fillStyle(0xc9dae3, 0.22 * intensity);
      for (let i = 0; i < 18; i += 1) {
        const x = (Math.sin(i * 12.9898) * 43758.5453) % 1;
        const px = Math.abs(x) * WIDTH;
        const py = ((i * 97) % (HEIGHT / TILE_PX)) * TILE_PX;
        splash.fillRect(px, py, 3, 1);
      }
    },
  };
}
