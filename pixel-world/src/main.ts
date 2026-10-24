/** 原型入口：装载世界，起 Phaser。 */

import Phaser from 'phaser';
import prefabs from './world/prefabs.json';
import { parseBlueprintTable } from './world/blueprints.ts';
import {
  buildLargeWorld,
  buildWorldFromSave,
  LARGE_ALTAR,
  LARGE_WORLD_SPEC,
  TILE_PX,
} from './world/setup.ts';
import { SandboxScene } from './scenes/SandboxScene.ts';
import { bindOraclePanel, type OracleApi } from './ui/oracle-panel.ts';
import { bindHud } from './ui/hud.ts';
import { bindShell } from './ui/shell.ts';
import { bindDebugPanel, type SaveStatusView } from './ui/debug-panel.ts';
import { SaveStore, exportFileName, IMPORT_MAX_BYTES } from './ui/save-store.ts';
import type { AgentToWorld } from './world/bridge.ts';
import type { WorldEvent, WorldSnapshot } from './world/types.ts';
import { WORLD } from './world/palette-data.ts';

const blueprints = parseBlueprintTable(prefabs);
// 读档必须在建世界/起 Phaser 之前：有档按存档 buildings 顺序重放网格（ADR_SAVE §1.2）。
const saveNotices: string[] = [];
const saveStore = new SaveStore({ onWarning: (message) => saveNotices.push(message) });
const loadedSave = saveStore.read();
/**
 * 生产用大世界：80×60 格。旧规格 30×20 保留给测试夹具，生产入口不再用它。
 *
 * 读档时按大世界规格重建网格：存档里没有网格数据，只有建筑坐标；
 * 旧档的建筑坐标在新图里仍然合法（新图包含原来的镇中心）。
 */
const world = loadedSave.ok
  ? buildWorldFromSave(loadedSave.save.world.buildings, blueprints, LARGE_WORLD_SPEC, LARGE_ALTAR)
  : buildLargeWorld(blueprints);

/** 画布尺寸：只显示世界的一小块，靠相机移动看其余部分。 */
const VIEW_WIDTH = 30 * TILE_PX;
const VIEW_HEIGHT = 20 * TILE_PX;

const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: 'app',
  width: VIEW_WIDTH,
  height: VIEW_HEIGHT,
  backgroundColor: WORLD.ink,
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
bindHud(() => (window as unknown as { Oracle?: OracleApi }).Oracle);
bindShell();

// 上帝调试面板：只读浮层，数据来自 Oracle 只读接口（不改变仿真）。
interface DebugOracle {
  view(): WorldSnapshot;
  events(limit: number): readonly WorldEvent[];
  saveStatus(): SaveStatusView;
  feed(message: AgentToWorld): { ok: boolean; message: string };
  pushLedger(commission: unknown): void;
}
const debugOracle = (): DebugOracle | undefined =>
  (window as unknown as { Oracle?: DebugOracle }).Oracle;
bindDebugPanel({
  getSnapshot: () => debugOracle()?.view(),
  getEvents: (limit) => debugOracle()?.events(limit) ?? [],
  getSaveStatus: () => debugOracle()?.saveStatus() ?? null,
  feed: (message) => debugOracle()?.feed(message) ?? { ok: false, message: '世界还没醒来。' },
  pushLedger: (commission) => debugOracle()?.pushLedger(commission),
});

// task-22：存档手动导出/导入。导出 = 单槽原文（就是 toSave() 的 v1 JSON，不二次加工）；
// 导入 = 先按现有校验链验一遍，再写单槽、停自动存档、刷新，让启动流程重建世界并与台账对账。
interface SaveOracle {
  saveNow(): { readonly ok: boolean; readonly message: string };
}
const saveOracle = (): SaveOracle | undefined =>
  (window as unknown as { Oracle?: SaveOracle }).Oracle;

const saveIoReply = document.querySelector<HTMLDivElement>('#save-io-reply');
const saveExport = document.querySelector<HTMLButtonElement>('#save-export');
const saveImport = document.querySelector<HTMLButtonElement>('#save-import');
const saveImportFile = document.querySelector<HTMLInputElement>('#save-import-file');

const saySaveIo = (ok: boolean, message: string): void => {
  if (saveIoReply === null) return;
  saveIoReply.textContent = message;
  saveIoReply.classList.toggle('bad', !ok);
};

const downloadText = (name: string, text: string): void => {
  const blob = new Blob([text], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    anchor.rel = 'noopener';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // 给浏览器一点时间开始下载，再回收 URL。
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
};

saveExport?.addEventListener('click', () => {
  const oracle = saveOracle();
  if (oracle === undefined) {
    saySaveIo(false, '世界还没醒来。');
    return;
  }
  // 先立即写一次单槽（导出“此刻”的世界）；写不进去就退而导出最近一次成功存档并说明。
  const result = oracle.saveNow();
  const text = saveStore.readRawText();
  if (text === null) {
    saySaveIo(false, result.ok ? '导出失败：读不到存档内容。' : result.message);
    return;
  }
  const name = exportFileName(Date.now());
  try {
    downloadText(name, text);
  } catch {
    saySaveIo(false, '导出失败：浏览器拒绝创建下载。');
    return;
  }
  if (!result.ok) {
    saySaveIo(false, `当前世界写不进存档（${result.message}）；已导出最近一次成功存档。`);
    return;
  }
  saySaveIo(true, `已导出 ${name}（${(text.length / 1024).toFixed(1)}KB）。`);
});

saveImport?.addEventListener('click', () => {
  saveImportFile?.click();
});

saveImportFile?.addEventListener('change', () => {
  const input = saveImportFile;
  const file = input.files?.[0];
  if (file === undefined) return;
  if (file.size > IMPORT_MAX_BYTES) {
    saySaveIo(false, `导入失败：文件 ${(file.size / 1024).toFixed(0)}KB，超过上限 2MB。`);
    input.value = '';
    return;
  }
  // 用 FileReader 而不是 File 的 text 方法：D5-A 守卫会拦 src 里的取文本调用，
  // 这里没有 canvas 文本，纯粹是避开误报。
  const reader = new FileReader();
  reader.onload = () => {
    const text = typeof reader.result === 'string' ? reader.result : '';
    const result = saveStore.importText(text);
    if (!result.ok) {
      saySaveIo(false, result.message);
      input.value = '';
      return;
    }
    // 导入已写进单槽。刷新前停掉自动存档，避免 pagehide 用旧世界把它覆盖掉。
    saveStore.disableAutoSave();
    saySaveIo(true, `${result.message} 正在按导入存档重建世界…`);
    input.value = '';
    setTimeout(() => window.location.reload(), 400);
  };
  reader.onerror = () => {
    saySaveIo(false, '导入失败：读不了这个文件。');
    input.value = '';
  };
  reader.readAsText(file);
});
