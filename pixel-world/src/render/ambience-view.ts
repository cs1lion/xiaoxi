/** 把天上的鸟与蝙蝠画出来。
 *
 * 性能约束（`scripts/check-render-budget.mjs` 的 `graphicsObjects < 40`）：
 * 每只鸟单开一个 Graphics 会在多开几只时撑破预算。所以整个氛围层共用
 * **一块** Graphics，每帧 clear 再重画。鸟只有几只、每只几个矩形，
 * 重画的代价远小于一堆 Graphics 对象本身。
 *
 * 视野外的鸟不画（`shouldAnimateAt`）：与烟雾同一个纪律，看不见就不必画。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import { WORLD } from '../world/palette-data.ts';
import { shouldAnimateAt } from './eco-mode.ts';
import { ambienceAt, type AmbienceActor } from './ambience.ts';
import type { PhaseId } from './day-phase.ts';

/** 压在角色之上、时相压暗（8009）之下：鸟在天上，但不该比夜色更亮。 */
const AMBIENCE_DEPTH = 7990;

export interface AmbienceView {
  update(now: number, phase: PhaseId, view: { x: number; y: number; width: number; height: number }): void;
}

export function createAmbienceView(scene: Phaser.Scene): AmbienceView {
  const g = scene.add.graphics();
  g.setDepth(AMBIENCE_DEPTH);

  return {
    update(now, phase, view) {
      g.clear();
      const actors = ambienceAt(now, phase);
      // 相机世界坐标换算成格：shouldAnimateAt 收的是格坐标。
      const cellView = {
        x: view.x / TILE_PX,
        y: view.y / TILE_PX,
        width: view.width / TILE_PX,
        height: view.height / TILE_PX,
      };
      for (const actor of actors) {
        if (!shouldAnimateAt(actor.at, cellView)) continue;
        if (actor.kind === 'bird') drawBird(g, actor);
        else drawBat(g, actor);
      }
    },
  };
}

/**
 * 鸟：两笔翅膀 + 一点身子，12px 左右。
 *
 * **用 pale 做主体而不是深色**。第一版拿 `deepShade` 画翅膀，叠在暗绿的草地上
 * 对比只有一点点，实测在 3x 缩放下完全读不出来——像一块脏像素。天空里的东西
 * 要亮过地面才立得住，这是 `pale` 的用途（烟、纸片、雨都是它）。
 *
 * 像素游戏里鸟的读法全靠翅膀角度：翅膀向上是"抬"，向下是"落"。
 * 两种姿态交替就够读出"在飞"，不需要中间帧。
 */
function drawBird(g: Phaser.GameObjects.Graphics, actor: AmbienceActor): void {
  const px = Math.round(actor.at.x * TILE_PX);
  const py = Math.round(actor.at.y * TILE_PX);
  // 拍翅相位过半时翅膀向下。
  const wingsUp = actor.flap < 0.5;
  const dir = actor.facing;

  // 翅膀：pale 主体 + ink 描边一格，保证在亮草地与暗林地上都能读出来。
  g.fillStyle(WORLD.ink, 1);
  if (wingsUp) {
    // 向上：两段斜挑，外段高一格。
    g.fillRect(px - 4, py - 3, 3, 2);
    g.fillRect(px + 2, py - 3, 3, 2);
  } else {
    // 向下：压平并向两侧展开。
    g.fillRect(px - 5, py + 1, 4, 2);
    g.fillRect(px + 2, py + 1, 4, 2);
  }
  g.fillStyle(WORLD.pale, 1);
  if (wingsUp) {
    g.fillRect(px - 3, py - 3, 2, 1);
    g.fillRect(px + 3, py - 3, 2, 1);
  } else {
    g.fillRect(px - 4, py + 1, 3, 1);
    g.fillRect(px + 3, py + 1, 3, 1);
  }

  // 身子：3px 横条，头朝飞行方向。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(px - 1, py - 1, 4, 3);
  g.fillStyle(WORLD.pale, 1);
  g.fillRect(px, py, 2, 1);
  // 头：往朝向那边凸一格。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(px + dir * 3 - (dir < 0 ? 1 : 0), py - 1, 2, 2);
}

/**
 * 蝙蝠：翅膀是折线扑扇，身子更小。
 *
 * 与鸟的区别靠三点：**更暗**（夜里读成剪影，不抢月光）、翅膀折角（不是平挑）、
 * 高度更低。这三样一凑，同一个位置上的东西就是"夜里飞的"而不是"鸟"。
 *
 * 蝙蝠在黄昏与夜里画，那时整屏被时相层压暗，深色剪影反而清楚；鸟在白天画，
 * 所以两者用相反的颜色是对的，不是不一致。
 */
function drawBat(g: Phaser.GameObjects.Graphics, actor: AmbienceActor): void {
  const px = Math.round(actor.at.x * TILE_PX);
  const py = Math.round(actor.at.y * TILE_PX);
  const wingsUp = actor.flap < 0.5;

  // 翅膀：折线，先外后内两段，形成蝙蝠特有的弯钩。
  g.fillStyle(WORLD.ink, 1);
  if (wingsUp) {
    g.fillRect(px - 5, py - 2, 2, 1);
    g.fillRect(px - 3, py - 1, 2, 1);
    g.fillRect(px + 3, py - 1, 2, 1);
    g.fillRect(px + 5, py - 2, 2, 1);
    g.fillRect(px - 6, py - 3, 2, 1);
    g.fillRect(px + 6, py - 3, 2, 1);
  } else {
    g.fillRect(px - 5, py + 1, 2, 1);
    g.fillRect(px - 3, py, 2, 1);
    g.fillRect(px + 3, py, 2, 1);
    g.fillRect(px + 5, py + 1, 2, 1);
    g.fillRect(px - 6, py + 2, 2, 1);
    g.fillRect(px + 6, py + 2, 2, 1);
  }

  // 身子在翅膀之后画，压住内侧的接缝：1×3 的小黑块 + 两只耳朵。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(px, py - 1, 2, 3);
  g.fillRect(px - 1, py - 2, 1, 1);
  g.fillRect(px + 2, py - 2, 1, 1);
  // 一点冷高光：纯黑剪影在夜里会糊进背景，一格 pale 让它有边界。
  g.fillStyle(WORLD.water, 0.7);
  g.fillRect(px, py, 1, 1);
}
