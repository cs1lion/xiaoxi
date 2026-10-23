/** 时相渲染层：把四刻配方画到世界上。纯渲染态，不写世界快照、不写台账。
 *
 * 与旧 `night.ts` 的关系：那个是二值开关（夜 / 非夜），这里是连续的四刻。
 * 绘制手法沿用它的三件事——`deepShade` 压暗、窗/门/灯/祭坛的暖光 ADD 叠加、
 * 萤火；新增的是每刻的压暗色、贴地雾带、屋脊受光。
 *
 * 不用整屏 CSS filter：那会把暖窗一起压灰，夜里会变成「白天调暗」。
 *
 * 切换是渐变的：两刻之间插值而非秒切，否则画面会像开关灯一样跳。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { WorldSnapshot } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { propFootY } from './prop-depth.ts';
import { PHASES, type PhaseId, type PhaseRecipe } from './day-phase.ts';
import { isWildLanternOn } from '../world/wilderness-signal.ts';
import { litWindowCount } from '../world/bustle.ts';

const DIM_DEPTH = 8009;
const GLOW_DEPTH = 8010;
const FIREFLY_DEPTH = 8011;
const MIST_DEPTH = 7998;
const FADE_MS = 900;

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

/** 插值后的实际观感。颜色不插值，只插数值；两刻的色差靠切换瞬间的重绘体现。 */
interface Blend {
  readonly dimColor: number;
  readonly dim: number;
  readonly glow: number;
  readonly ridge: number;
  readonly litWindows: number;
  readonly mist: number;
  readonly mistBand: number;
  readonly fireflies: boolean;
}

export interface PhaseView {
  /** 切换目标相位；实际观感会在 FADE_MS 内渐变过去。 */
  setPhase(phase: PhaseId): void;
  /** 当前目标相位。 */
  currentPhase(): PhaseId;
  /** 是否由真实时间自动推进；关掉后停在 setPhase 给的那一刻。 */
  setAutoAdvance(enabled: boolean): void;
  isAutoAdvancing(): boolean;
  update(snapshot: WorldSnapshot, delta: number): void;
}

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

function blend(from: PhaseRecipe, to: PhaseRecipe, t: number): Blend {
  return {
    // 颜色没有中间态可插（插了就是第 25 色），过半直接换到目标色。
    dimColor: t < 0.5 ? from.dimColor : to.dimColor,
    dim: lerp(from.dim, to.dim, t),
    glow: lerp(from.glow, to.glow, t),
    ridge: lerp(from.ridge, to.ridge, t),
    litWindows: t < 0.5 ? from.litWindows : to.litWindows,
    mist: lerp(from.mist, to.mist, t),
    mistBand: lerp(from.mistBand, to.mistBand, t),
    fireflies: t < 0.5 ? from.fireflies : to.fireflies,
  };
}

/**
 * 时相层覆盖**整个世界**，不是画布。
 *
 * 单屏时代两者相等，所以写死常量没问题；大世界里画布只是世界的一小块，
 * 若还用画布尺寸，压暗只覆盖视野、雾带贴在视野底部——走两步就露馅。
 */
export function createPhaseView(
  scene: Phaser.Scene,
  worldWidth: number,
  worldHeight: number,
): PhaseView {
  const WIDTH = worldWidth;
  const HEIGHT = worldHeight;
  const dim = scene.add.rectangle(0, 0, WIDTH, HEIGHT, WORLD.deepShade, 0).setOrigin(0, 0);
  dim.setDepth(DIM_DEPTH);

  const glows = scene.add.graphics();
  glows.setDepth(GLOW_DEPTH);
  glows.setBlendMode(Phaser.BlendModes.ADD);

  const fireflies = scene.add.graphics();
  fireflies.setDepth(FIREFLY_DEPTH);
  fireflies.setBlendMode(Phaser.BlendModes.ADD);

  const mist = scene.add.graphics();
  mist.setDepth(MIST_DEPTH);

  let targetPhase: PhaseId = 'noon';
  let fromPhase: PhaseId = 'noon';
  let t = 1;
  let autoAdvance = true;
  let glowKey = '';

  const drawGlows = (snapshot: WorldSnapshot, view: Blend): void => {
    glows.clear();
    if (view.glow <= 0.001) return;
    const addGlow = (cx: number, cy: number, w: number, h: number, alpha: number): void => {
      const a = alpha * view.glow;
      glows.fillStyle(WORLD.amber, a);
      glows.fillRect(Math.round(cx - w / 2), Math.round(cy - h / 2), Math.round(w), Math.round(h));
      glows.fillStyle(WORLD.amber, a * 0.55);
      glows.fillRect(
        Math.round(cx - w / 2) + 1,
        Math.round(cy - h / 2) + 1,
        Math.max(1, Math.round(w) - 2),
        Math.max(1, Math.round(h) - 2),
      );
      glows.fillStyle(WORLD.warmLight, a * 1.15);
      glows.fillRect(
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

      // 亮窗：时辰给的 + 干活给的，取最大值。
      // 正午本该安静，但工坊在运转，那扇窗就亮着——这是"镇子活着"的读感。
      const working = snapshot.workstations.some(
        (station) =>
          station.buildingId === building.instanceId && station.activity.kind === 'operating',
      );
      const lit = litWindowCount(view, working);
      const windowXs = [px + 6, px + w - 18];
      for (let i = 0; i < lit && i < windowXs.length; i += 1) {
        addGlow(windowXs[i]! + 4, py + roofH + 11, 13, 9, 0.11);
      }
      // 门口光池：有窗亮着，就有屋里的光漏到门前。
      if (lit > 0) {
        const dx = building.door.x * TILE_PX + 8;
        const dy = building.door.y * TILE_PX + 8;
        addGlow(dx, dy + 9, 18, 11, 0.1);
      }

      // 郊野常明灯：**不分时辰**，白天也亮。
      // 这是郊野的识别信号——荒地里的一个人造光点，说明这里有人在守着。
      // 与镇内窗灯是两套语义：镇内跟作息走，郊野跟「有人在野外」走。
      if (isWildLanternOn(building, snapshot)) {
        // 灯笼挂在门旁，光池比窗灯小一圈：它是一盏灯，不是一屋子光。
        const lx = building.door.x * TILE_PX + 8;
        const ly = building.door.y * TILE_PX - 4;
        addGlow(lx, ly, 10, 8, 0.16 * (0.85 + 0.15 * Math.sin(snapshot.now / 620)));
      }
      // 屋脊受光：黄昏独有，把黄昏和正午分开的主要线索。
      if (view.ridge > 0.001) {
        const alpha = view.ridge * 0.5;
        glows.fillStyle(WORLD.warmLight, alpha);
        glows.fillRect(Math.round(px - 6), Math.round(py + roofH - 12), Math.round(w + 12), 2);
        glows.fillStyle(WORLD.amber, alpha * 0.6);
        glows.fillRect(Math.round(px - 4), Math.round(py + roofH - 9), Math.round(w + 8), 4);
      }
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
  };

  const drawFireflies = (snapshot: WorldSnapshot, view: Blend): void => {
    fireflies.clear();
    if (!view.fireflies || snapshot.weather === 'rain') return;
    for (let i = 0; i < FIREFLIES.length; i += 1) {
      const point = FIREFLIES[i]!;
      const phase = Math.sin(snapshot.now / 700 + i * 1.9);
      if (phase < -0.15) continue;
      const alpha = 0.22 + 0.5 * ((phase + 1) / 2);
      fireflies.fillStyle(WORLD.warmLight, alpha);
      fireflies.fillRect(point.x, point.y, 1, 1);
      if (i % 3 === 0) {
        fireflies.fillStyle(WORLD.amber, alpha * 0.55);
        fireflies.fillRect(point.x + 1, point.y, 1, 1);
      }
    }
  };

  /**
   * 晨雾：低空雾带，两端淡、中间浓。
   *
   * 试过贴底的实带（像画面被裁了一条）和「远处冷雾 + 近处厚雾」（草地被洗成灰条）。
   * 都失败的原因是雾贴到画布边缘。现在把雾放在画面中下段，上下都留出草地，
   * 读成「雾浮在田地上」，而不是「画面缺了一块」。
   */
  const drawMist = (view: Blend, cameraY: number, viewHeight: number): void => {
    mist.clear();
    const band = Math.round(view.mistBand);
    if (band <= 0 || view.mist <= 0.001) return;

    // 雾跟着**镜头**走，不跟着世界底边：大世界里世界高 960px，视野只有 320px，
    // 按世界高度算的雾带会永远落在视野之外（实测晨与午只剩 0.8% 差异）。
    // 雾是"近地面的空气"，镜头看到哪片地，雾就在那片地的下段。
    const top = Math.round(cameraY + viewHeight * 0.62);
    const bottom = Math.min(cameraY + viewHeight, top + band);
    const span = Math.max(1, bottom - top);
    for (let y = top; y < bottom; y += 1) {
      // 中间最浓，两端趋零，避免出现硬边。
      const t = (y - top) / span;
      const weight = Math.sin(Math.PI * t);
      mist.fillStyle(WORLD.pale, view.mist * weight * 0.5);
      mist.fillRect(0, y, WIDTH, 1);
    }
  };

  return {
    setPhase(next: PhaseId): void {
      if (next === targetPhase) return;
      // 从当前**观感**出发，而不是从上一个目标出发，否则连按会跳。
      fromPhase = currentBlendPhase();
      targetPhase = next;
      t = 0;
      glowKey = '';
    },
    currentPhase(): PhaseId {
      return targetPhase;
    },
    setAutoAdvance(enabled: boolean): void {
      autoAdvance = enabled;
    },
    isAutoAdvancing(): boolean {
      return autoAdvance;
    },
    update(snapshot: WorldSnapshot, delta: number): void {
      if (t < 1) t = Math.min(1, t + delta / FADE_MS);
      const view = blend(PHASES[fromPhase], PHASES[targetPhase], t);

      dim.setVisible(true);
      dim.setFillStyle(view.dimColor, view.dim);

      const key =
        view.litWindows +
        ':' +
        Math.round(view.ridge * 100) +
        ':' +
        Math.round(view.glow * 100) +
        ':' +
        snapshot.buildings
          .map(
            (b) =>
              `${b.instanceId}:${b.origin.x},${b.origin.y},${b.footprint.w},${b.footprint.h},${b.door.x},${b.door.y}:` +
              // 干活状态进 key：开工/收工时窗灯要跟着变，否则不会重绘。
              (snapshot.workstations.some(
                (st) => st.buildingId === b.instanceId && st.activity.kind === 'operating',
              )
                ? 'w'
                : 'i'),
          )
          .join('|') +
        '#' +
        snapshot.props.map((p) => `${p.id}:${p.at.x},${p.at.y},${p.kind}`).join('|') +
        // 郊野灯的呼吸：按 300ms 一档。不每帧重画——光晕是整片重绘，
        // 而呼吸只需要看起来在动，300ms 的粒度已经够了。
        '#' +
        Math.round(snapshot.now / 300);
      if (key !== glowKey) {
        glowKey = key;
        drawGlows(snapshot, view);
      }
      drawFireflies(snapshot, view);
      drawMist(view, scene.cameras.main.scrollY, scene.cameras.main.height / scene.cameras.main.zoom);
    },
  };

  /** 渐变进程过半时，观感已经更接近目标刻；用于连按时不回跳。 */
  function currentBlendPhase(): PhaseId {
    return t < 0.5 ? fromPhase : targetPhase;
  }
}
