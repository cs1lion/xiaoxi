/** 建筑层：每栋铺子拆成三层容器（地面 / 楼体 / 前檐），状态变化只改部件颜色与可见性。
 *
 * 为什么拆三层（ART_STYLE_TARGET §1.3）
 * ------------------------------------
 * 单容器只有一个 depth，整栋楼要么整体压在角色前面、要么整体在角色后面，做不出
 * 「站在门口时头顶被屋檐压住」这种「进屋」的读法。拆成三层各给一个 depth 之后：
 *
 *   back  地面投影 + 门口光池，压在楼体之下 → 楼「坐」在地上，不是浮在草地上
 *   body  墙体 / 屋面 / 窗 / 烟囱 / 招牌 / 进度 / 门口台阶，depth = ALTITUDE + baseY（现状）
 *   front 前檐 2px + 门框外侧 1px，depth 比站门口的角色高一档 → 檐压人
 *
 * 台阶归 body 而不归 front：它落在门口格上沿，角色站门口时脚在该格底边，
 * 人是站在台阶「前面」的，放进 front 会横穿她的身体。理由写在 drawDoorStep 上。
 *
 * 三层都放在同一个 (px, py)，所以内部坐标与改造前完全一致，只是各自的上层容器不同。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { PlacedBuilding, WorkstationSnapshot } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { BUILDING_DEPTH, buildingBaseY, layerDepth } from './building-depth.ts';

export { BUILDING_DEPTH };


export interface BuildingView {
  /** 地面层：三步投影 + 门口光池，depth = ALTITUDE + baseY - 2。 */
  readonly back: Phaser.GameObjects.Container;
  /** 楼体层：墙 / 屋面 / 窗 / 烟囱 / 招牌 / 进度，depth = ALTITUDE + baseY。 */
  readonly container: Phaser.GameObjects.Container;
  /** 前檐层：前檐 + 门框外侧，depth = ALTITUDE + baseY + 32，压住站在门口的人。 */
  readonly front: Phaser.GameObjects.Container;
  readonly chimney: Phaser.GameObjects.Graphics;
  readonly window: Phaser.GameObjects.Graphics;
  readonly progress: Phaser.GameObjects.Graphics;
  /** 招牌图标（静态文字已改图标，D5-A）。 */
  readonly sign: Phaser.GameObjects.Graphics;
  /** 烟囱口的场景坐标，粒子发射器挂在这里。 */
  readonly chimneyWorld: Phaser.Math.Vector2;
  /** 屋顶上沿，Y-Sort 用。 */
  readonly baseY: number;
}

export function createBuilding(
  scene: Phaser.Scene,
  building: PlacedBuilding,
  originX: number,
  originY: number,
): BuildingView {
  const px = originX + building.origin.x * TILE_PX;
  const py = originY + building.origin.y * TILE_PX;
  const w = building.footprint.w * TILE_PX;
  const h = building.footprint.h * TILE_PX;
  // 三层容器同一个 (px, py)：内部坐标与单容器时代完全一致，只有 depth 不同。
  const back = scene.add.container(px, py);
  const container = scene.add.container(px, py);
  const front = scene.add.container(px, py);

  const g = scene.add.graphics();
  const { body, roof, trim, window: windowColor } = building.palette;
  const roofH = Math.max(8, Math.round(h * 0.34));

  drawGroundShadow(scene, back, building, w, h, px, py);

  g.fillStyle(body, 1);
  g.fillRect(0, roofH, w, h - roofH);
  g.fillStyle(trim, 1);
  g.fillRect(0, roofH, w, 2);
  g.fillRect(0, h - 4, w, 4);

  drawRoof(g, building.silhouette, w, roofH, roof, trim);

  for (let x = 4; x < w - 4; x += 7) {
    g.fillStyle(roof, 0.6);
    g.fillRect(x, roofH + 4, 1, h - roofH - 8);
  }

  const chimney = scene.add.graphics();
  chimney.fillStyle(trim, 1);
  chimney.fillRect(w - 14, -6, 7, 10);
  chimney.fillStyle(WORLD.ink, 1);
  chimney.fillRect(w - 13, -6, 5, 2);

  const windowG = scene.add.graphics();
  const doorX = building.door.x * TILE_PX - px;
  const doorY = building.door.y * TILE_PX - py;
  windowG.fillStyle(WORLD.soil, 1);
  windowG.fillRect(doorX - 4, doorY - 14, 10, 14);
  windowG.fillStyle(trim, 1);
  windowG.fillRect(doorX - 4, doorY - 14, 1, 14);
  windowG.fillRect(doorX + 5, doorY - 14, 1, 14);
  windowG.fillStyle(windowColor, 0.85);
  windowG.fillRect(6, roofH + 8, 8, 6);
  windowG.fillRect(w - 18, roofH + 8, 8, 6);
  windowG.fillStyle(WORLD.ink, 0.4);
  windowG.fillRect(10, roofH + 8, 1, 6);
  windowG.fillRect(w - 14, roofH + 8, 1, 6);

  const progress = scene.add.graphics();
  progress.setVisible(false);

  // 静态招牌：不再画中文，按接的活画一枚像素图标（D5-A）。
  const sign = scene.add.graphics();
  drawSignIcon(sign, building, w);

  container.add([g, chimney, windowG, progress, sign]);

  // 台阶：门洞口外侧 2px，落在门口格最上面 2px。**放在 body 层**而不是 front：
  // 角色站门口时脚在门口格底边（baseY+16），台阶在 baseY..baseY+2，物理上角色离
  // 观察者更近，台阶必须在他之后；放进 front（depth 比角色高）会盖住他的胸口。
  const stepG = scene.add.graphics();
  drawDoorStep(stepG, doorX, h);
  container.add([stepG]);

  // 前檐层：前檐 2px + 门框外侧 1px —— 这两样才该压在角色身上。
  const frontG = scene.add.graphics();
  drawFrontLayer(frontG, w, h, doorX, roof, trim);
  front.add([frontG]);

  return {
    back,
    container,
    front,
    chimney,
    window: windowG,
    progress,
    sign,
    chimneyWorld: new Phaser.Math.Vector2(px + w - 10, py - 2),
    // baseY 用纯函数算，测试与实现共用同一个算式，不会各写一套而漂移。
    baseY: buildingBaseY(py, building.footprint.h),
  };
}

/**
 * 地面层：三步阶梯投影 + 门口光池。光从左上打，投影落右下（与 §1.5 角色阴影同向）。
 *
 * 三步 α 0.16 / 0.32 / 0.55：越靠墙越深、越往外越淡，读成「楼坐在地上」而不是
 * 底下垫了一块灰板。不用 blur——像素画里模糊边缘会变成脏灰带。
 */
function drawGroundShadow(
  scene: Phaser.Scene,
  back: Phaser.GameObjects.Container,
  building: PlacedBuilding,
  w: number,
  h: number,
  px: number,
  py: number,
): void {
  const g = scene.add.graphics();
  // 最贴地的一步最深、最窄；往外两层逐级变淡变宽，整体向右下错开 2px。
  g.fillStyle(WORLD.ink, 0.55);
  g.fillRect(2, h - 2, w - 2, 2);
  g.fillStyle(WORLD.ink, 0.32);
  g.fillRect(3, h, w - 1, 2);
  g.fillStyle(WORLD.ink, 0.16);
  g.fillRect(4, h + 2, w, 2);

  // 门口光池：屋里有灯，光落在门前。两层 ── 外圈铺开、内芯更亮，都是琥珀的低透明度。
  const doorX = building.door.x * TILE_PX - px;
  const doorY = building.door.y * TILE_PX - py;
  g.fillStyle(WORLD.amber, 0.08);
  g.fillRect(doorX - 10, doorY - 1, 20, 5);
  g.fillStyle(WORLD.amber, 0.14);
  g.fillRect(doorX - 6, doorY, 12, 3);

  back.add([g]);
}

/**
 * 前檐层：前檐 2px（roof）+ 门框外侧 1px（trim）。
 *
 * depth = baseY + 32，而站在门口的角色是 baseY + 24（见 BUILDING_DEPTH 的推导），
 * 所以屋檐会压住她头顶 1–2px、门框会压住她身体两侧 1px —— 这就是「进屋」的读法；
 * 而站在门口再往下一格的人是 baseY + 40，不会被压到。
 * **不加色**：檐用该栋的 roof token，门框用 trim，全是现有 16 色。
 */
function drawFrontLayer(
  g: Phaser.GameObjects.Graphics,
  w: number,
  h: number,
  doorX: number,
  roof: number,
  trim: number,
): void {
  // 前檐：楼体底边最后 2px，左右各出 2px（出檐），读成压在墙前的一道檐。
  // 位置在 y = h-2..h，而站门口的角色 sprite 占 y = h-4..h+16，所以这 2px 正好压在
  // 她头顶 1–2px 上（front depth 比角色高，见 BUILDING_DEPTH 的推导）。
  g.fillStyle(roof, 1);
  g.fillRect(-2, h - 2, w + 4, 2);

  // 门框外侧 1px：贴着现有门框（doorX-4 / doorX+5）再往外一列，加厚门洞。
  g.fillStyle(trim, 1);
  g.fillRect(doorX - 5, h - 14, 1, 14);
  g.fillRect(doorX + 6, h - 14, 1, 14);
}

/**
 * 台阶：门口格最上面 2px，两级（plaza + stone）。
 *
 * **归 body 层，不进 front**：角色站门口时脚在门口格底边（baseY+16）、sprite 覆盖
 * baseY-4..baseY+16，台阶在 baseY..baseY+2 正落在她腰部高度。若放 front 层
 * （depth 比角色高）会横穿她的身体；放 body 层则她站在台阶「前面」，物理正确。
 */
function drawDoorStep(g: Phaser.GameObjects.Graphics, doorX: number, h: number): void {
  g.fillStyle(WORLD.plaza, 1);
  g.fillRect(doorX - 5, h, 12, 1);
  g.fillStyle(WORLD.stone, 1);
  g.fillRect(doorX - 4, h + 1, 10, 1);
}

/** 招牌图标：<>（代码）/ 书（研究）/ 笔（写作）/ 锤（空工坊）。只用现有色。 */
function drawSignIcon(g: Phaser.GameObjects.Graphics, building: PlacedBuilding, w: number): void {
  const x = Math.round(w / 2) - 7;
  const y = -22;
  g.fillStyle(WORLD.ink, 0.55);
  g.fillRect(x, y, 15, 12);
  g.fillStyle(building.palette.trim, 1);
  g.fillRect(x + 1, y + 1, 13, 1);
  g.fillRect(x + 1, y + 10, 13, 1);
  g.fillStyle(WORLD.amber, 0.95);
  if (building.boundTask === 'code') {
    // 两个折角：< >
    for (let step = 0; step < 3; step += 1) {
      g.fillRect(x + 4 - step, y + 4 + step, 1, 1);
      g.fillRect(x + 7 - step, y + 4 + step, 1, 1);
      g.fillRect(x + 10 - step, y + 4 + step, 1, 1);
    }
    for (let step = 0; step < 3; step += 1) {
      g.fillRect(x + 4 + step, y + 8 - step, 1, 1);
      g.fillRect(x + 7 + step, y + 8 - step, 1, 1);
      g.fillRect(x + 10 + step, y + 8 - step, 1, 1);
    }
  } else if (building.boundTask === 'research') {
    // 一本书：外框 + 书脊
    g.fillRect(x + 4, y + 3, 6, 1);
    g.fillRect(x + 4, y + 8, 6, 1);
    g.fillRect(x + 4, y + 3, 1, 6);
    g.fillRect(x + 9, y + 3, 1, 6);
    g.fillRect(x + 7, y + 3, 1, 6);
  } else if (building.boundTask === 'writing') {
    // 一支笔：斜杆 + 笔尖
    for (let step = 0; step < 5; step += 1) g.fillRect(x + 4 + step, y + 8 - step, 1, 2);
    g.fillRect(x + 9, y + 3, 2, 2);
  } else {
    // 空工坊：锤子
    g.fillRect(x + 5, y + 3, 6, 3);
    g.fillRect(x + 7, y + 5, 2, 5);
  }
}

/** 三种剪影：尖顶、塔楼、平顶。远看就能分出是哪一栋。 */
function drawRoof(
  g: Phaser.GameObjects.Graphics,
  silhouette: PlacedBuilding['silhouette'],
  w: number,
  roofH: number,
  roof: number,
  trim: number,
): void {
  if (silhouette === 'gable') {
    g.fillStyle(roof, 1);
    // 一阶一阶往上收，尖顶落在顶边中点。
    const rows = Math.max(3, Math.round(roofH / 3));
    for (let row = 0; row < rows; row += 1) {
      const inset = Math.round(((rows - 1 - row) / rows) * (w / 2));
      g.fillRect(-3 + inset, row * (roofH / rows), w + 6 - inset * 2, roofH / rows + 1);
    }
    g.fillStyle(trim, 1);
    g.fillRect(w / 2 - 1, 0, 2, roofH);
  } else if (silhouette === 'tower') {
    g.fillStyle(roof, 1);
    g.fillRect(-3, roofH * 0.45, w + 6, roofH * 0.55);
    g.fillRect(w * 0.3, 0, w * 0.4, roofH);
    g.fillStyle(trim, 1);
    g.fillRect(w * 0.3 - 1, roofH * 0.45, w * 0.4 + 2, 2);
    g.fillRect(w * 0.5 - 1, -4, 2, 5);
  } else {
    g.fillStyle(roof, 1);
    g.fillRect(-3, 0, w + 6, roofH);
    g.fillStyle(trim, 1);
    for (let x = 1; x < w + 4; x += 4) g.fillRect(x - 3, 2, 2, roofH - 4);
    g.fillRect(-3, roofH - 2, w + 6, 2);
  }
}

/** 干活时窗户亮起、门口挂上脉动的进度条；冷却时灯仍亮一小会儿。 */
export function updateBuilding(
  scene: Phaser.Scene,
  view: BuildingView,
  building: PlacedBuilding,
  workstation: WorkstationSnapshot | undefined,
  altitude: number,
): void {
  const activity = workstation?.activity ?? { kind: 'idle' as const };
  const working = activity.kind === 'operating';
  const cooling = activity.kind === 'cooling';
  const broken = activity.kind === 'overheating';
  const busy = working || broken;

  // 三层各自一个 depth（相对数值见 BUILDING_DEPTH）：
  //   back  < body  < 站门口的角色(+24) < front(+32) < 门口下 1 格的人(+40)
  // 于是只有「站在门口」这一格会被檐压住，再往外走一格就恢复正常。
  view.back.setDepth(layerDepth('back', view.baseY, altitude));
  view.container.setDepth(layerDepth('body', view.baseY, altitude));
  view.front.setDepth(layerDepth('front', view.baseY, altitude));
  view.sign.setAlpha(busy ? 1 : 0.72);
  view.chimney.setAlpha(busy ? 1 : 0);

  view.progress.clear();
  view.progress.setVisible(working || cooling || broken);
  const width = building.footprint.w * TILE_PX - 8;
  if (working) {
    const filled = Math.round((width - 2) * (workstation?.progress ?? 0));
    view.progress.fillStyle(WORLD.ink, 0.8);
    view.progress.fillRect(4, -8, width, 3);
    view.progress.fillStyle(WORLD.amber, 0.9);
    view.progress.fillRect(5, -7, Math.max(1, filled), 1);
    // 进度线在走之外再加一层脉动，远处也能看出这台机器还活着。
    view.progress.fillStyle(WORLD.amber, 0.35 + 0.35 * Math.sin(scene.time.now / 240));
    view.progress.fillRect(5, -7, Math.max(1, filled), 1);
  } else if (cooling) {
    view.progress.fillStyle(WORLD.amber, 0.8);
    view.progress.fillRect(4, -8, width, 3);
  } else if (broken) {
    // 故障：整条线闪红，不表示进度，只表示"这台机器坏了"。
    view.progress.fillStyle(WORLD.ember, 0.9);
    view.progress.fillRect(4, -8, width, 3);
    view.progress.fillStyle(WORLD.amber, 0.5 + 0.5 * Math.sin(scene.time.now / 120));
    view.progress.fillRect(4, -8, width, 3);
  }
}
