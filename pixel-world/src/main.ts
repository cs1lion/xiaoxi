/** 原型入口：装载世界，起 Phaser。 */

import Phaser from 'phaser';
import prefabs from './world/prefabs.json';
import { parseBlueprintTable } from './world/blueprints.ts';
import { buildWorld, buildWorldFromSave, GRID_WIDTH, GRID_HEIGHT, TILE_PX } from './world/setup.ts';
import { SandboxScene } from './scenes/SandboxScene.ts';
import { bindOraclePanel, type OracleApi } from './ui/oracle-panel.ts';
import { SaveStore } from './ui/save-store.ts';
import { UI } from './render/palette.ts';

const blueprints = parseBlueprintTable(prefabs);
// 读档必须在建世界/起 Phaser 之前：有档按存档 buildings 顺序重放网格（ADR_SAVE §1.2）。
const saveNotices: string[] = [];
const saveStore = new SaveStore({ onWarning: (message) => saveNotices.push(message) });
const loadedSave = saveStore.read();
const world = loadedSave.ok
  ? buildWorldFromSave(loadedSave.save.world.buildings)
  : buildWorld(blueprints);

const WIDTH = GRID_WIDTH * TILE_PX;
const HEIGHT = GRID_HEIGHT * TILE_PX;

const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: 'app',
  width: WIDTH,
  height: HEIGHT,
  backgroundColor: UI.sky,
  pixelArt: true,
  roundPixels: true,
  // 逻辑分辨率保持 480×320（30×20 格 ×16px），只放大画布显示尺寸。非整数缩放会让像素边缘渗色，
  // 所以固定 2 倍，不跟随窗口。
  scale: {
    mode: Phaser.Scale.NONE,
    zoom: 2,
    autoCenter: Phaser.Scale.CENTER_HORIZONTALLY,
  },
  scene: [],
});

// 场景配置要等 Game 建好之后才能 push：payload 在 init 里读。
game.scene.add('SandboxScene', SandboxScene, true, {
  world,
  blueprints,
  villagerName: '莞尔',
  saveStore,
  save: loadedSave.ok ? loadedSave.save : null,
  saveNotice: loadedSave.ok ? saveNotices[0] ?? null : loadedSave.message,
});

// 按钮等场景 create() 挂上 window.Oracle；期间点击直接忽略。
bindOraclePanel(() => (window as unknown as { Oracle?: OracleApi }).Oracle);
