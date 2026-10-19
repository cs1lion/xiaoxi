/** 雨幕与压暗。浓淡来自世界快照的 0..1，渲染层只插值，不判断天气。 */

import Phaser from 'phaser';
import { GRID_HEIGHT, GRID_WIDTH, TILE_PX } from '../world/setup.ts';
import { WORLD } from '../world/palette-data.ts';

const WIDTH = GRID_WIDTH * TILE_PX;
const HEIGHT = GRID_HEIGHT * TILE_PX;
const DROPS = 140;

/** 截图/回归用的确定性雨滴种子。默认 null = 线上行为不变（Math.random）。 */
let rainSeed: number | null = null;
let rainState = 0;

/** 打开确定性雨滴：同一个 seed 得到同一串雨滴序列（截图对比、复跑用）。 */
export function setRainSeed(seed: number | null): void {
  rainSeed = seed === null ? null : seed >>> 0;
  rainState = rainSeed ?? 0;
}

/** 关掉确定性种子，回到 Math.random。 */
export function resetRainRng(): void {
  setRainSeed(null);
}

function rainRandom(): number {
  if (rainSeed === null) return Math.random();
  // LCG（数值常数与截图注入的那一份一致）：跨浏览器/跨帧都稳定。
  rainState = (Math.imul(rainState === 0 ? 0x9e3779b9 : rainState, 1664525) + 1013904223) >>> 0;
  return rainState / 4294967296;
}

export interface WeatherView {
  readonly drops: Phaser.GameObjects.Graphics;
  readonly dim: Phaser.GameObjects.Rectangle;
  readonly splash: Phaser.GameObjects.Graphics;
  readonly update: (intensity: number, delta: number) => void;
}

export function createWeatherView(scene: Phaser.Scene): WeatherView {
  // 截图脚本在加载前写 window.__RAIN_SEED__；页面自身不设，只读。
  const injected = (globalThis as { __RAIN_SEED__?: unknown }).__RAIN_SEED__;
  if (typeof injected === 'number' && Number.isFinite(injected)) setRainSeed(injected);

  const dim = scene.add.rectangle(0, 0, WIDTH, HEIGHT, WORLD.cloakDark, 0).setOrigin(0, 0);
  dim.setDepth(8000);

  const drops = scene.add.graphics();
  drops.setDepth(8001);

  const splash = scene.add.graphics();
  splash.setDepth(8002);

  const xs: number[] = [];
  const ys: number[] = [];
  for (let i = 0; i < DROPS; i += 1) {
    xs.push(rainRandom() * WIDTH);
    ys.push(rainRandom() * HEIGHT);
  }

  return {
    drops,
    dim,
    splash,
    update(intensity, delta) {
      const visible = intensity > 0.02;
      drops.setVisible(visible);
      splash.setVisible(visible);
      dim.setFillStyle(WORLD.cloakDark, intensity * 0.28);
      if (!visible) {
        drops.clear();
        splash.clear();
        return;
      }
      // 种子模式按固定步长下落：截图/回归时不受帧 delta 抖动影响，同种子即同画面。
      const fall = (rainSeed === null ? delta / 1000 : 1 / 60) * 260;
      drops.clear();
      drops.lineStyle(1, WORLD.pale, 0.35 + intensity * 0.35);
      for (let i = 0; i < DROPS; i += 1) {
        ys[i] = ys[i] === undefined ? 0 : ys[i] + fall;
        if (ys[i] > HEIGHT) {
          ys[i] = -rainRandom() * 40;
          xs[i] = rainRandom() * WIDTH;
        }
        const x = xs[i] ?? 0;
        const y = ys[i] ?? 0;
        if (i % 3 === 0) drops.lineBetween(x, y, x - 1, y + 5);
      }
      splash.clear();
      splash.fillStyle(WORLD.pale, 0.22 * intensity);
      for (let i = 0; i < 18; i += 1) {
        const x = (Math.sin(i * 12.9898) * 43758.5453) % 1;
        const px = Math.abs(x) * WIDTH;
        const py = ((i * 97) % (HEIGHT / TILE_PX)) * TILE_PX;
        splash.fillRect(px, py, 3, 1);
      }
    },
  };
}
