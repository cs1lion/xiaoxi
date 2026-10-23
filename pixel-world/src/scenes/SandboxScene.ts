/** 唯一的 Phaser 场景。它只做三件事：把逻辑 tick 起来、把快照画出来、把输入转成命令。
 *
 * 世界逻辑全在 src/world/ 里，与 Phaser 无关；这里不判断"该不该开工"。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import { Simulation, type MiracleKind, type NewBuilding } from '../world/simulation.ts';
import type { CommandResult, GridPoint, OracleRecord, PrefabBlueprint, WorldSnapshot } from '../world/types.ts';
import { excerptBlockedQuestion } from '../world/types.ts';
import { placeBlueprint, type BuiltWorld } from '../world/setup.ts';
import { WORLD, tokenNameOf } from '../world/palette-data.ts';
import { drawTerrain } from '../render/tilemap.ts';
import { createBuilding, updateBuilding, type BuildingView } from '../render/buildings.ts';
import { createVillager, updateVillager, type VillagerView } from '../render/villager.ts';
import { createWorldOverlay, type WorldOverlay } from '../render/world-overlay.ts';
import { createAccentEmitter, ensureAccentTextures, type AccentEmitter } from '../render/smoke.ts';
import { createWeatherView, type WeatherView } from '../render/weather.ts';
import { createPhaseView, type PhaseView } from '../render/phase-view.ts';
import { createAmbienceView, type AmbienceView } from '../render/ambience-view.ts';
import { createGhostView, type GhostView } from '../render/ghost.ts';
import { createCameraController, type CameraController } from '../render/camera-controller.ts';
import { createMinimapView, type MinimapView } from '../render/minimap.ts';
import { createFogView, type FogView } from '../render/fog-view.ts';
import { bakeLayer, type BakedLayer } from '../render/bake.ts';
import { shouldAnimateAt, targetFpsFor } from '../render/eco-mode.ts';
import { fogAt } from '../world/fog.ts';
import { awaitingDecree } from '../world/decree.ts';
import { describeWorldState, summarizeWorld } from '../world/world-state.ts';
import { BIOME_RADII } from '../world/biomes.ts';
import { WILD_PREFABS } from '../world/setup.ts';
import { parseCreationPlan } from '../world/creator-schema.ts';
import { checkPlacement, hintFor } from '../world/setup.ts';
import { parsePhaseParam, phaseAt, type PhaseId } from '../render/day-phase.ts';
import { createDecorView, type DecorView } from '../render/decor.ts';
import { composeReport, type DailyReport } from '../world/report.ts';
import { describePropSource } from '../world/faith.ts';
import { createLazyAnimalese, type Animalese } from '../audio/animalese.ts';
import { toWorldCommand, type AgentToWorld } from '../world/bridge.ts';
import { LedgerClient } from '../world/ledger.ts';
import { createIntentClient } from '../world/intent-client.ts';
import { SaveStore } from '../ui/save-store.ts';
import type { WorldSave } from '../world/save.ts';
import { CURRENT_SCHEMA_VERSION } from '../world/save.ts';

export interface ScenePayload {
  readonly world: BuiltWorld;
  readonly blueprints: ReadonlyMap<string, PrefabBlueprint>;
  readonly villagerName: string;
  readonly saveStore?: SaveStore;
  readonly save?: WorldSave | null;
  readonly saveNotice?: string | null;
}

const LOGIC_STEP_MS = 100;
const MAX_STEPS_PER_FRAME = 5;
const ALTITUDE = 1000;

interface BuildingEntry {
  readonly building: BuiltWorld['buildings'][number];
  readonly view: BuildingView;
  readonly emitter: AccentEmitter;
}

export class SandboxScene extends Phaser.Scene {
  private world!: BuiltWorld;
  private blueprints!: ReadonlyMap<string, PrefabBlueprint>;
  private sim!: Simulation;
  private snapshot!: WorldSnapshot;
  private readonly villagerViews = new Map<string, VillagerView>();
  private overlay: WorldOverlay | null = null;
  private weatherView!: WeatherView;
  private phaseView!: PhaseView;
  /** 白天的鸟、黄昏的蝙蝠。纯画面，不占快照、不进存档。 */
  private ambienceView!: AmbienceView;
  private nightKeyListener: ((event: KeyboardEvent) => void) | null = null;
  private creationKeyListener: ((event: KeyboardEvent) => void) | null = null;
  /** 真实时间推进用的定时器；?time= 固定某刻时不启动。 */
  private phaseTimer: number | null = null;
  private decor!: DecorView;
  private entries: BuildingEntry[] = [];
  private accumulator = 0;
  private tick = 0;
  private villagerName = '莞尔';
  private lastStatus = '';
  private lastRoadCount = -1;
  /** 造物模式会话。null 表示不在造物模式里。 */
  private creation: { readonly prefabId: string; ghostAt: GridPoint | null } | null = null;
  private ghost!: GhostView;
  private cameraCtl!: CameraController;
  private minimap: MinimapView | null = null;
  private fogView!: FogView;
  private focusListener: (() => void) | null = null;
  /** 诊断用：场景 update 被调用过多少次。降频是否生效看它。 */
  private updateCount = 0;
  /** 真正推进了世界的帧数。降频是否生效看它。 */
  private advanceCount = 0;
  /** 省电模式：窗口不在前台时为 true，渲染与逻辑都按 `minFrameIntervalMs` 跳帧。 */
  private backgroundMode = false;
  /** 两帧之间的最小间隔（毫秒）。前台为 1000/60，后台为 1000/12。 */
  private minFrameIntervalMs = 1000 / 60;
  /** 上次真正推进世界的时刻（Phaser 的 time 参数）。 */
  private lastAdvanceAt = 0;
  private terrainLayer!: BakedLayer;
  /** 拖拽平移的起点（屏幕坐标）；null 表示没在拖。 */
  private dragFrom: { x: number; y: number } | null = null;
  private lastConstructionKey = '';
  private readonly lastBubbles = new Map<string, string | null>();
  private readonly animalese: Animalese = createLazyAnimalese();
  private ledger: LedgerClient | null = null;
  private saveStore!: SaveStore;
  private loadedSave: WorldSave | null = null;
  private saveNotice: string | null = null;
  private lastSaveKey = '';
  private lastSaveAt = Number.NEGATIVE_INFINITY;
  private onPageHide: (() => void) | null = null;
  private readonly resultListeners = new Set<(result: CommandResult) => void>();
  private readonly statusListeners = new Set<(line: string) => void>();
  private readonly bubbleListeners = new Set<(text: string) => void>();

  /** 测试与 UI 都能读到同一份报纸；它从快照推导，不另存。 */
  report(): DailyReport {
    return composeReport(this.snapshot);
  }

  constructor() {
    super({ key: 'SandboxScene' });
  }

  init(payload: ScenePayload): void {
    this.world = payload.world;
    this.blueprints = payload.blueprints;
    this.villagerName = payload.villagerName;
    this.loadedSave = payload.save ?? null;
    this.saveNotice = payload.saveNotice ?? null;
    this.saveStore =
      payload.saveStore ??
      new SaveStore({
        onWarning: (message) => {
          this.saveNotice = message;
        },
      });
  }

  create(): void {
    const originX = 0;
    const originY = 0;
    this.cameras.main.setBackgroundColor(WORLD.ink);

    // 地形烘焙成一张纹理：它每帧要 51 万条绘制指令，而可见区域只占世界 12.5%。
    // 只在开局画一次，之后每帧只贴一个 quad。
    this.terrainLayer = bakeLayer(
      this,
      'world-terrain',
      this.world.grid.width * TILE_PX,
      this.world.grid.height * TILE_PX,
      -10,
      // 传祭坛坐标：地表细节按群落配方分化，镇内与郊野长得不一样。
      (g) => drawTerrain(this, this.world.grid, originX, originY, g, this.world.altar),
    );
    ensureAccentTextures(this);
    this.decor = createDecorView(this, originX, originY);
    this.decor.redrawRoads([]);

    this.entries = this.world.buildings.map((building) => ({
      building,
      view: createBuilding(this, building, originX, originY),
      emitter: createAccentEmitter(this, building.accent),
    }));
    // 覆盖层按**世界**尺寸建，不是画布尺寸：大世界里镜头只是世界的一小块。
    const worldPx = {
      w: this.world.grid.width * TILE_PX,
      h: this.world.grid.height * TILE_PX,
    };
    this.weatherView = createWeatherView(this, worldPx.w, worldPx.h);
    this.phaseView = createPhaseView(this, worldPx.w, worldPx.h);
    this.ambienceView = createAmbienceView(this);
    this.ghost = createGhostView(this);
    this.fogView = createFogView(this, this.world.grid.width, this.world.grid.height);

    // 相机：锁在世界边界内，初始对准祭坛。
    this.cameraCtl = createCameraController(this, worldPx.w, worldPx.h);
    // 开局对准祭坛。放在下一帧做：Phaser 在 create() 结束后的首次 preRender
    // 会用 setBounds 重新夹取镜头，那时 camera.width 才是最终值；
    // 在 create() 里直接 centerOn 会被这次夹取推回左上角。
    const focusX = this.world.altar.x * TILE_PX;
    const focusY = this.world.altar.y * TILE_PX;
    this.time.delayedCall(0, () => this.cameraCtl.panTo(focusX, focusY, 0));
    // 自动导播：闲置 15 秒后接管，有活看正在忙的人，没活看祭坛。
    // 这里只是**登记**目标列表，真正进入导播要等闲置计时到点——
    // 用户一拖一缩就退出，不会跟人抢镜头。
    this.cameraCtl.enableDirector(() => this.directorTargets());

    const minimapHost = document.querySelector<HTMLDivElement>('#minimap');
    if (minimapHost !== null) {
      this.minimap = createMinimapView(minimapHost, worldPx.w, worldPx.h);
      this.minimap.paintTerrain(this.world.grid);
      minimapHost.addEventListener('pointerdown', (event: PointerEvent) => {
        const point = this.minimap?.toWorld(event.clientX, event.clientY);
        if (point === null || point === undefined) return;
        this.cameraCtl.panTo(point.x, point.y, 300);
      });
    }

    // 拖拽平移与滚轮缩放。造物模式优先：那时拖拽不移动镜头。
    this.input.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      if (this.creation !== null) return;
      this.dragFrom = { x: pointer.x, y: pointer.y };
    });
    this.input.on('pointerup', () => {
      this.dragFrom = null;
    });
    this.input.on('pointermove', (pointer: Phaser.Input.Pointer) => {
      if (this.creation !== null || this.dragFrom === null) return;
      if (!pointer.isDown) {
        this.dragFrom = null;
        return;
      }
      const dx = pointer.x - this.dragFrom.x;
      const dy = pointer.y - this.dragFrom.y;
      this.dragFrom = { x: pointer.x, y: pointer.y };
      this.cameraCtl.panBy(dx, dy);
    });
    this.input.on('wheel', (_p: unknown, _o: unknown, _dx: number, dy: number) => {
      if (this.creation !== null) return;
      this.cameraCtl.zoomStep(dy > 0 ? -1 : 1);
    });
    // 时相来源：?time= 显式指定（截图与调试用）> ?night=1（旧入口，等价于夜）> 真实本地时间。
    const params = new URLSearchParams(window.location.search);
    const forced = parsePhaseParam(params.get('time'));
    const legacyNight = params.get('night') === '1';
    const initialPhase: PhaseId = forced ?? (legacyNight ? 'night' : phaseAt(Date.now()));
    this.phaseView.setPhase(initialPhase);
    if (forced !== null || legacyNight) {
      this.phaseView.setAutoAdvance(false);
    }

    // 定时器**无条件创建**，由 `isAutoAdvancing()` 决定要不要真的切换。
    //
    // 早先只在「没有 ?time=」时创建它，于是从 ?time= 进入后再调 setAutoPhase，
    // 标志变 true 却没有定时器在跑——「交还给真实时间」是个空承诺。
    // 15 秒对一次表，不是 60 秒：相位边界是整点，60 秒的粒度意味着最多晚
    // 一分钟才切，而切换本身只有 900ms 渐变——用户会在 17:00 后还看着正午。
    // 15 秒的代价可以忽略（一次 Date.now 与一次比较）。
    this.phaseTimer = window.setInterval(() => {
      if (!this.phaseView.isAutoAdvancing()) return;
      this.phaseView.setPhase(phaseAt(Date.now()));
    }, 15_000);
    this.nightKeyListener = (event: KeyboardEvent): void => {
      if (event.key.toLowerCase() !== 'n') return;
      const target = event.target as HTMLElement | null;
      if (
        target !== null &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }
      // N 键在黄昏/夜之间切换，保留旧的「按 N 看夜景」习惯。
      this.phaseView.setPhase(
        this.phaseView.currentPhase() === 'night' ? 'dusk' : 'night',
      );
      this.phaseView.setAutoAdvance(false);
    };
    window.addEventListener('keydown', this.nightKeyListener);

    // 造物模式的指针与键盘。指针只在造物模式里有用，所以这里不注册常驻
    // 监听器，而是在 beginCreation 里挂、endCreation 里摘——避免平时每帧
    // 做一次无意义的坐标换算。
    this.input.on('pointermove', (pointer: Phaser.Input.Pointer) => {
      if (this.creation === null) return;
      // 用 worldX/worldY 而不是 positionToCamera：造物模式暂停了 update，
      // 相机矩阵可能停在上一帧，换算出来的世界坐标会偏。worldX 是事件派发时
      // 由输入插件按当前相机算好的。
      this.hoverCell({
        x: Math.floor(pointer.worldX / TILE_PX),
        y: Math.floor(pointer.worldY / TILE_PX),
      });
    });
    // 用 DOM 层的 keydown 而不是 Phaser 的键盘插件：Phaser 只在自己获得焦点时
    // 派发按键，而页面里输入框、按钮都可能抢焦点，造物模式会因此"回车没反应"。
    // 这里与 N 键用同一套判定：输入框里打字不算。
    this.creationKeyListener = (event: KeyboardEvent): void => {
      if (this.creation === null) return;
      const target = event.target as HTMLElement | null;
      if (
        target !== null &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }
      if (event.key === 'Enter') {
        // 焦点常在按钮上（刚点完「造物模式」）。此时浏览器的默认行为是
        // 再点一次那个按钮，于是模式刚确认又重新打开。必须在这里拦掉：
        // 造物模式下回车只表示「就建在这里」。
        event.preventDefault();
        (target as HTMLElement | null)?.blur?.();
        const result = this.confirmCreation();
        if (!result.ok) this.publishCreationHint(result.message);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        this.cancelCreation();
      }
    };
    // 捕获阶段先拦：按钮的默认点击发生在冒泡之后，冒泡阶段拦住也晚了。
    window.addEventListener('keydown', this.creationKeyListener, true);

    this.drawAltar(originX, originY);

    const simDeps = {
      grid: this.world.grid,
      buildings: this.world.buildings,
      altar: this.world.altar,
      villagerName: this.villagerName,
      // 造物神谕（「建一间书阁」）要靠它把词换成模板。
      blueprints: this.blueprints,
      // 规则认不出的话才问模型。没有 Key 时它会 503，世界照样离线可用。
      intentModel: createIntentClient(() => this.blueprints),
      worldSize: { width: this.world.grid.width, height: this.world.grid.height },
      spawnBuilding: (
        prefabId: string,
        instanceId: string,
        at?: GridPoint,
        source?: { readonly taskId: string; readonly summary: string },
      ): NewBuilding | null => {
        const blueprint = this.blueprints.get(prefabId);
        if (blueprint === undefined) return null;
        return placeBlueprint(
          this.world.grid,
          blueprint,
          instanceId,
          this.world.altar,
          at,
          source,
        );
      },
    };
    // 有档就重建世界；读档后台账标未对账，等首个 sync 走 reconcile。
    this.sim =
      this.loadedSave !== null
        ? Simulation.fromSave(this.loadedSave, simDeps)
        : new Simulation(simDeps);
    this.snapshot = this.sim.snapshot();
    const overlayRoot = document.querySelector<HTMLDivElement>('#world-overlay');
    if (overlayRoot !== null) this.overlay = createWorldOverlay(this, overlayRoot);
    this.lastSaveKey = this.saveStateKey();
    // 省电模式：切到后台就降频。这是常驻窗口能不能长期开着的前提——
    // 用户写代码时如果它还跑满 60fps，笔记本会发烫。
    // 用 visibilitychange 而不是 hasFocus：后者在无头环境与部分浏览器里
    // 不随标签页切换变化（实测切到后台仍报 focused=true）。
    // 页面被遮挡或切走时 visibilityState 一定会变。
    this.focusListener = (): void => {
      const focused = document.visibilityState === 'visible' && document.hasFocus();
      // 不碰 Phaser 的限帧机制：它的 `step` / `stepLimitFPS` 回调在 `start()`
      // 时就绑定死了，运行时切换要 sleep + start，而 sleep 会停掉 raf、
      // start 又因 `started` 标志不重启——实测结果是直接归零。
      // 改成自己控制：记下后台模式与最小帧间隔，在 `update()` 里跳帧。
      this.backgroundMode = !focused;
      this.minFrameIntervalMs = 1000 / targetFpsFor(focused);
    };
    document.addEventListener('visibilitychange', this.focusListener);
    window.addEventListener('focus', this.focusListener);
    window.addEventListener('blur', this.focusListener);
    // 初始状态也按当前焦点设一次。
    this.focusListener();

    this.onPageHide = () => {
      this.saveStore.write(this.sim.toSave());
    };
    window.addEventListener('pagehide', this.onPageHide);
    this.registerOracleApi();
    this.connectLedger();
  }

  /**
   * 接主服务的台账。连不上不算错误：世界照常过日子，状态行会写明离线。
   * 用 `?ledger=off` 可以显式跑纯沙盒（离线演示与浏览器回归都用它）。
   */
  private connectLedger(): void {
    const params = new URLSearchParams(window.location.search);
    if (params.get('ledger') === 'off') return;
    const client = new LedgerClient({
      onChange: (view) => {
        this.sim.enqueue({
          kind: 'sync',
          tasks: view.tasks,
          finished: view.finished,
          finishedAt: view.finishedAt,
          connected: view.connected,
          complete: view.complete,
          order: view.order,
        });
      },
    });
    this.ledger = client;
    void client.start();
  }

  shutdown(): void {
    // 烘焙的纹理不随场景销毁自动释放，必须手动清：否则热更新或重进场景时
    // 会留下同名旧纹理，下一次 generateTexture 直接复用，画面停在上一版。
    this.terrainLayer?.destroy();
    this.fogView?.destroy();
    this.ledger?.stop();
    this.ledger = null;
    this.overlay?.dispose();
    this.overlay = null;
    if (this.nightKeyListener !== null) {
      window.removeEventListener('keydown', this.nightKeyListener);
      this.nightKeyListener = null;
    }
    if (this.phaseTimer !== null) {
      window.clearInterval(this.phaseTimer);
      this.phaseTimer = null;
    }
    if (this.creationKeyListener !== null) {
      window.removeEventListener('keydown', this.creationKeyListener, true);
      this.creationKeyListener = null;
    }
    if (this.focusListener !== null) {
      document.removeEventListener('visibilitychange', this.focusListener);
      window.removeEventListener('focus', this.focusListener);
      window.removeEventListener('blur', this.focusListener);
      this.focusListener = null;
    }
    if (this.onPageHide !== null) {
      window.removeEventListener('pagehide', this.onPageHide);
      this.onPageHide = null;
    }
  }

  private drawAltar(originX: number, originY: number): void {
    const altarPx = this.world.altar;
    const ax = originX + altarPx.x * TILE_PX;
    const ay = originY + altarPx.y * TILE_PX;
    const cx = ax + TILE_PX / 2;

    // 地面光池：先画、depth 压在所有人之下。用 1px 抖动环，不用大半径 alpha 圆。
    const pool = this.add.graphics();
    pool.setDepth(ALTITUDE + ay - 8);
    pool.fillStyle(WORLD.amber, 0.06);
    pool.fillRect(cx - 24, ay + 12, 48, 10);
    pool.fillStyle(WORLD.amber, 0.1);
    pool.fillRect(cx - 18, ay + 14, 36, 6);
    pool.fillStyle(WORLD.warmLight, 0.14);
    pool.fillRect(cx - 12, ay + 16, 24, 3);

    // 石龛：26×26，比旧的 44×44 小一半；底座两级、四柱、小屋顶、蜡烛与供品。
    // 视觉整体向右偏 8px：她站在祭坛点时不会正好挡住龛内的符文。
    const sx = cx + 12;
    const altar = this.add.graphics();
    // 底座第一步（最宽）
    altar.fillStyle(WORLD.stone, 1);
    altar.fillRect(sx - 14, ay + 18, 28, 5);
    altar.fillStyle(WORLD.plaza, 1);
    altar.fillRect(sx - 14, ay + 18, 28, 1);
    altar.fillStyle(WORLD.soil, 1);
    altar.fillRect(sx - 14, ay + 22, 28, 1);
    // 底座第二步
    altar.fillStyle(WORLD.plaza, 1);
    altar.fillRect(sx - 10, ay + 14, 20, 5);
    altar.fillStyle(WORLD.stone, 1);
    altar.fillRect(sx - 10, ay + 18, 20, 1);
    // 四柱：左右各两根，柱头用 trim。
    for (const px of [sx - 12, sx + 9] as const) {
      altar.fillStyle(WORLD.stone, 1);
      altar.fillRect(px, ay + 2, 3, 13);
      altar.fillStyle(WORLD.plaza, 1);
      altar.fillRect(px, ay + 2, 1, 13);
      altar.fillStyle(WORLD.trim, 1);
      altar.fillRect(px - 1, ay + 1, 5, 1);
    }
    // 小屋顶：两坡 + 屋脊，压在柱子上。
    altar.fillStyle(WORLD.timberDark, 1);
    altar.fillRect(sx - 15, ay - 2, 30, 3);
    altar.fillRect(sx - 12, ay - 5, 24, 3);
    altar.fillStyle(WORLD.trim, 1);
    altar.fillRect(sx - 13, ay - 5, 26, 1);
    altar.fillStyle(WORLD.timberDark, 1);
    altar.fillRect(sx - 1, ay - 8, 2, 4);
    // 龛内暗部 + 符文
    altar.fillStyle(WORLD.ink, 1);
    altar.fillRect(sx - 7, ay + 3, 14, 12);
    // 4 角星：竖/横 + 两条斜线，中央 pale 高光。不是“T”。
    altar.fillStyle(WORLD.amber, 0.95);
    altar.fillRect(sx - 1, ay + 5, 2, 8);
    altar.fillRect(sx - 4, ay + 8, 8, 2);
    for (let step = 0; step < 3; step += 1) {
      altar.fillRect(sx - 3 + step, ay + 6 + step, 1, 1);
      altar.fillRect(sx + 2 - step, ay + 6 + step, 1, 1);
      altar.fillRect(sx - 3 + step, ay + 11 - step, 1, 1);
      altar.fillRect(sx + 2 - step, ay + 11 - step, 1, 1);
    }
    altar.fillStyle(WORLD.warmLight, 1);
    altar.fillRect(sx, ay + 8, 1, 1);
    // 两支蜡烛：pale 烛身 + amber 火焰。
    for (const px of [sx - 12, sx + 11] as const) {
      altar.fillStyle(WORLD.pale, 1);
      altar.fillRect(px, ay + 12, 1, 3);
      altar.fillStyle(WORLD.warmLight, 1);
      altar.fillRect(px, ay + 10, 1, 1);
      altar.fillStyle(WORLD.ember, 1);
      altar.fillRect(px, ay + 11, 1, 1);
    }
    // 供品：两块 trim/ember 小布包 + 苔。
    altar.fillStyle(WORLD.trim, 1);
    altar.fillRect(sx - 6, ay + 18, 3, 2);
    altar.fillStyle(WORLD.ember, 1);
    altar.fillRect(sx + 3, ay + 18, 3, 2);
    altar.fillStyle(WORLD.moss, 1);
    for (let i = 0; i < 6; i += 1) {
      altar.fillRect(sx - 12 + i * 5, ay + 22, 2, 1);
    }
    altar.setDepth(ALTITUDE + ay + 24);
  }

  update(_time: number, delta: number): void {
    this.updateCount += 1;

    // 省电：后台时按最小间隔跳帧。**跳过的是这一帧的渲染与逻辑推进**，
    // 世界本身不会错乱——逻辑时钟按真实经过的时间推进，只是推得粗一些。
    //
    // 用真实经过的时间而不是上一帧的 delta：跳帧后 delta 只有 16ms，
    // 直接累加会让世界时钟在后台越走越慢。
    const elapsed = this.lastAdvanceAt === 0 ? delta : _time - this.lastAdvanceAt;
    if (this.backgroundMode && elapsed < this.minFrameIntervalMs) return;
    this.lastAdvanceAt = _time;
    // 真正推进过的帧才计数：`updateCount` 统计的是 Phaser 的调用次数，
    // 跳帧时它仍然每帧加一，看不出降频。
    this.advanceCount += 1;
    this.accumulator += Math.min(elapsed, 250);
    // 造物模式暂停世界：幽灵预览期间不该有人走动或收工。
    // 只暂停推进，不清空累加器——退出后不会一次性补跑一大段。
    if (this.creation !== null) {
      this.accumulator = 0;
      this.snapshot = this.sim.snapshot();
      this.renderSnapshot(delta);
      return;
    }
    let steps = 0;
    while (this.accumulator >= LOGIC_STEP_MS && steps < MAX_STEPS_PER_FRAME) {
      this.accumulator -= LOGIC_STEP_MS;
      const results = this.sim.update(LOGIC_STEP_MS);
      for (const result of results) {
        for (const listener of this.resultListeners) listener(result);
      }
      this.tick += 1;
      steps += 1;
    }
    this.snapshot = this.sim.snapshot();
    this.renderSnapshot(delta);
    this.cameraCtl.update(delta);
    this.minimap?.update(this.snapshot, this.cameraCtl.viewportRect());
    // 只在 update() 返回后写档（命令已结算，ADR_SAVE §4）。
    this.maybeAutoSave();
  }

  // ------------------------------------------------------------------ input

  /**
   * 网页与终端共用这一个口子。原话整句交给世界，解析与刻石板都在 `Simulation` 里。
   *
   * 这里只负责投递：认不认得出要等下一拍结算，所以本地只挡空句子，
   * 其余一律投递并返回"已投递"。真正的回执走 `onResult`。
   */
  say(text: string): CommandResult {
    if (text.trim() === '') return { ok: false, message: '说点什么吧。' };
    // 命令不直接改世界，排进队列，由下一个逻辑步统一结算。
    this.sim.enqueue({ kind: 'oracle', raw: text });
    return { ok: true, message: '神谕已投递，随下一拍生效。' };
  }

  dispatch(kind: 'code' | 'research' | 'writing' | 'quick', summary: string): void {
    this.sim.enqueue({ kind: 'dispatch', taskKind: kind, summary });
  }

  /** 人工交付；target 可选（id 或名字），省略时收第一个在干活的人的单。 */
  finish(target?: string): void {
    this.sim.enqueue(target === undefined ? { kind: 'complete' } : { kind: 'complete', target });
  }

  build(prefabId: string): void {
    const instanceId = `${prefabId}#${this.snapshot.buildings.length + 1}`;
    this.sim.enqueue({ kind: 'build', prefabId, instanceId });
  }

  // -------------------------------------------------------------- 造物模式

  /**
   * 进入造物模式。世界就此暂停，直到确认或取消。
   *
   * 模式只活在场景层：`Simulation` 不知道有这回事，存档里也没有它的位置。
   */
  beginCreation(prefabId: string): CommandResult {
    const blueprint = this.blueprints.get(prefabId);
    if (blueprint === undefined) return { ok: false, message: '没有这份蓝图。' };
    if (this.snapshot.buildings.some((building) => building.prefabId === prefabId)) {
      return { ok: false, message: '这种铺子已经有一栋了。' };
    }
    this.creation = { prefabId, ghostAt: null };
    this.ghost.hide();
    // 把焦点从按钮上拿走：否则回车会被浏览器当成「再点一次那个按钮」，
    // 模式刚确认又重新打开。焦点交给 body，键盘事件才由页面统一处理。
    (document.activeElement as HTMLElement | null)?.blur?.();
    return { ok: true, message: '选个位置：移动指针，回车确认，Esc 取消。' };
  }

  /** 指针移到某格时更新幽灵。不在造物模式里就什么也不做。 */
  hoverCell(cell: GridPoint): void {
    const session = this.creation;
    if (session === null) return;
    const blueprint = this.blueprints.get(session.prefabId);
    if (blueprint === undefined) return;
    this.creation = { ...session, ghostAt: cell };
    const check = this.canPlaceBlueprintHere(blueprint, cell);
    this.ghost.show(cell, blueprint, check.ok);
    this.publishCreationHint(check.ok ? '可以建在这里，回车确认。' : check.message);
  }

  /** 确认施工。没有幽灵位置时拒绝，不猜一个地方。 */
  confirmCreation(): CommandResult {
    const session = this.creation;
    if (session === null) return { ok: false, message: '不在造物模式。' };
    if (session.ghostAt === null) {
      return { ok: false, message: '先把指针移到想建的位置。' };
    }
    const blueprint = this.blueprints.get(session.prefabId);
    if (blueprint === undefined) return { ok: false, message: '没有这份蓝图。' };
    const check = this.canPlaceBlueprintHere(blueprint, session.ghostAt);
    if (!check.ok) {
      // 留在造物模式里让玩家换个位置，并说明为什么这里不行。
      return { ok: false, message: check.message };
    }
    const instanceId = `${session.prefabId}#${this.snapshot.buildings.length + 1}`;

    // 玩家的选择先拼成一份**造物意图**，过 schema 再落位。
    // 这样手动造物与将来的 LLM 输出走同一条校验，schema 不是死代码。
    const intent = {
      templateId: session.prefabId,
      at: session.ghostAt,
      palette: {
        body: tokenNameOf(blueprint.palette.body),
        roof: tokenNameOf(blueprint.palette.roof),
        trim: tokenNameOf(blueprint.palette.trim),
        window: tokenNameOf(blueprint.palette.window),
      },
    };
    try {
      parseCreationPlan(intent, this.blueprints, {
        width: this.world.grid.width,
        height: this.world.grid.height,
      });
    } catch (error) {
      // schema 不过就是玩家的选择有问题：留在模式里，说清哪里不对。
      return {
        ok: false,
        message: error instanceof Error ? error.message : '造物意图不合法。',
      };
    }

    this.sim.enqueue({
      kind: 'build',
      prefabId: session.prefabId,
      instanceId,
      at: session.ghostAt,
      // 记下来由：这栋楼是你在造物模式里点出来的。纸面据此说得出它的来历。
      source: {
        taskId: `creation-${instanceId}`,
        summary: `在 (${session.ghostAt.x},${session.ghostAt.y}) 建了${blueprint.displayName}`,
      },
    });
    this.endCreation();
    return { ok: true, message: '动工了。' };
  }

  /**
   * 造物落位校验：迷雾、空间、群落三道。
   *
   * 三处调用（hover / confirm / canPlaceAt）必须共用这一份——
   * 各写一遍就会漏掉其中一条，而漏掉的那条会在真机上表现为
   * 「预览说可以，确认却建到了不该建的地方」。
   */
  private canPlaceBlueprintHere(
    blueprint: PrefabBlueprint,
    cell: GridPoint,
  ): { ok: true } | { ok: false; message: string } {
    if (fogAt(cell, this.snapshot.fog)) {
      return { ok: false, message: '那里还没长出来，先让世界走到那儿。' };
    }
    const check = checkPlacement(this.world.grid, blueprint, cell);
    if (!check.ok) return { ok: false, message: hintFor(check.reason) };

    // 群落：郊野铺子必须在郊野圈，镇内铺子在工坊环内。
    // 与开局落位同一套规则（setup.ts 的 accept 谓词），否则拆了重建会把
    // 郊野铺子建到镇里——实测 crawler_mine 曾被建到 15.5 格的 craftRing。
    const distance = Math.hypot(cell.x - this.world.altar.x, cell.y - this.world.altar.y);
    const isWild = WILD_PREFABS.has(blueprint.id);
    if (isWild && distance <= BIOME_RADII.craftRing) {
      return { ok: false, message: '这种铺子要建在郊野，离镇子远一点。' };
    }
    if (!isWild && distance > BIOME_RADII.craftRing) {
      return { ok: false, message: '这种铺子要建在镇子里。' };
    }
    return { ok: true };
  }

  /** 取消：世界没有任何变化，恢复推进。 */
  cancelCreation(): CommandResult {
    if (this.creation === null) return { ok: false, message: '不在造物模式。' };
    this.endCreation();
    return { ok: true, message: '取消了，世界没动。' };
  }

  creating(): { readonly prefabId: string; readonly ghostAt: GridPoint | null } | null {
    return this.creation;
  }

  private endCreation(): void {
    this.creation = null;
    this.ghost.hide();
    this.publishCreationHint('');
  }

  /**
   * 导播要看的地方。
   *
   * 排序即优先级：正在忙的人 → 正在运转的铺子 → 祭坛 → 其它铺子。
   *
   * 为什么要把空闲铺子也列进来：两个角色都闲在祭坛时，候选只剩祭坛一个点，
   * 导播"接管"了却无处可去，镜头一动不动——看起来像坏了。
   * 有铺子做候选，镜头才会真的在镇子里游走。
   */
  private directorTargets(): readonly { x: number; y: number }[] {
    const targets: { x: number; y: number }[] = [];
    for (const villager of this.snapshot.villagers) {
      if (villager.mode === 'idle') continue;
      targets.push({ x: villager.pos.x * TILE_PX, y: villager.pos.y * TILE_PX });
    }

    const operating: { x: number; y: number }[] = [];
    const idle: { x: number; y: number }[] = [];
    for (const building of this.snapshot.buildings) {
      const station = this.snapshot.workstations.find(
        (item) => item.buildingId === building.instanceId,
      );
      const point = {
        x: (building.origin.x + building.footprint.w / 2) * TILE_PX,
        y: (building.origin.y + building.footprint.h / 2) * TILE_PX,
      };
      if (station?.activity.kind === 'operating') operating.push(point);
      else idle.push(point);
    }
    targets.push(...operating);

    // 祭坛是世界的心跳，空镜头也该回到它。
    targets.push({ x: this.world.altar.x * TILE_PX, y: this.world.altar.y * TILE_PX });
    targets.push(...idle);
    return targets;
  }

  private publishCreationHint(line: string): void {
    if (line === '') return;
    for (const listener of this.resultListeners) listener({ ok: true, message: line });
  }

  /** 手动存档：立即把当前世界写进单槽。 */
  saveNow(): CommandResult {
    const result = this.saveStore.write(this.sim.toSave());
    if (result.ok) {
      this.lastSaveKey = this.saveStateKey();
      this.lastSaveAt = this.snapshot.now;
      this.saveNotice = null;
    }
    return { ok: result.ok, message: result.message };
  }

  /** 清档：只删主槽并停自动存档（防止 pagehide 又写回来）；刷新后从新世界开始。 */
  clearSave(): CommandResult {
    this.saveStore.clear();
    this.saveStore.disableAutoSave();
    return { ok: true, message: '存档已清除，刷新后从新世界开始。' };
  }

  private saveStateKey(): string {
    const deliveries = this.snapshot.deliveries;
    return [
      deliveries.ledger,
      deliveries.mock,
      this.snapshot.faith.milestones,
      this.snapshot.constructions.length,
      this.snapshot.props.length,
      this.snapshot.tasks.length,
    ].join('|');
  }

  /** 交付/施工/神恩/30s 节流：只在 update 返回后调用（ADR_SAVE §4）。 */
  private maybeAutoSave(): void {
    if (!this.saveStore.isAutoSaveEnabled()) return;
    const changed = this.saveStateKey() !== this.lastSaveKey;
    const due = this.snapshot.now - this.lastSaveAt >= 30_000;
    if (!changed && !due) return;
    const result = this.saveStore.write(this.sim.toSave());
    if (!result.ok) {
      this.saveNotice = result.message;
      return;
    }
    this.lastSaveKey = this.saveStateKey();
    this.lastSaveAt = this.snapshot.now;
  }

  /** 外部 Agent 的入口。它和打字、按钮走同一条队列。 */
  feed(message: AgentToWorld): CommandResult {
    this.sim.enqueue(toWorldCommand(message));
    return { ok: true, message: '外部消息已投递。' };
  }

  /**
   * 喂一帧台账增量给客户端，等价于服务端真的发了一帧 commission_update。
   * 浏览器回归用它验证"操作台与世界连通"，不写主服务。
   */
  pushLedger(commission: unknown): void {
    this.ledger?.applyPush(commission);
  }

  view(): WorldSnapshot {
    return this.snapshot;
  }

  /** 调试面板用：最近 N 条事件（快照只带 12 条）。避开 Phaser.Scene.events 命名。 */
  recentEvents(limit = 50): ReturnType<Simulation['recentEvents']> {
    return this.sim.recentEvents(limit);
  }

  /** 调试面板用：存档状态（只读，不触发写档）。 */
  saveStatus(): {
    readonly schemaVersion: number;
    readonly savedAtWallClock: number | null;
    readonly autoSavePaused: boolean;
  } {
    const meta = this.saveStore.lastSavedMeta();
    return {
      schemaVersion: meta?.schemaVersion ?? CURRENT_SCHEMA_VERSION,
      savedAtWallClock: meta?.savedAtWallClock ?? null,
      autoSavePaused: !this.saveStore.isAutoSaveEnabled(),
    };
  }

  /** 时相是纯渲染态：不写世界、不写台账。 */
  setPhase(phase: PhaseId): void {
    this.phaseView.setPhase(phase);
    this.phaseView.setAutoAdvance(false);
  }

  currentPhase(): PhaseId {
    return this.phaseView.currentPhase();
  }

  /** 真实时间此刻对应的刻。用于验证「交还给真实时间」真的落对了。 */
  realPhase(): PhaseId {
    return phaseAt(Date.now());
  }

  /** 是否按真实时间自动推进。 */
  isAutoPhase(): boolean {
    return this.phaseView.isAutoAdvancing();
  }

  /** 恢复按真实时间推进。 */
  setAutoPhase(): void {
    this.phaseView.setAutoAdvance(true);
    this.phaseView.setPhase(phaseAt(Date.now()));
  }

  /** 旧入口保留语义：setNight(true) 等于跳到夜，false 等于回到真实时间。 */
  setNight(enabled: boolean): void {
    if (enabled) this.setPhase('night');
    else this.setAutoPhase();
  }

  /**
   * 在夜与昼之间切换，返回切换后是否为夜。
   *
   * 「离开夜」回到的是真实时间那一刻，而不是写死正午——否则早上按一下会跳到中午，
   * 世界就跟你的作息脱节了。
   */
  toggleNight(): boolean {
    if (this.phaseView.currentPhase() === 'night') {
      this.setAutoPhase();
      return this.phaseView.currentPhase() === 'night';
    }
    this.setPhase('night');
    return true;
  }

  nightEnabled(): boolean {
    return this.phaseView.currentPhase() === 'night';
  }

  private registerOracleApi(): void {
    const api = {
      say: (text: string): CommandResult => this.say(text),
      dispatch: (kind: 'code' | 'research' | 'writing' | 'quick', summary: string): void =>
        this.dispatch(kind, summary),
      finish: (target?: string): void => this.finish(target),
      build: (prefabId: string): void => this.build(prefabId),
      beginCreation: (prefabId: string): CommandResult => this.beginCreation(prefabId),
      hoverCell: (cell: GridPoint): void => this.hoverCell(cell),
      confirmCreation: (): CommandResult => this.confirmCreation(),
      cancelCreation: (): CommandResult => this.cancelCreation(),
      creation: () => this.creating(),
      /**
       * 调试注入：造一条 blocked 的台账督办，用来验证急件流程。
       * 只供调试面板与浏览器回归用，正常路径不会有调用方。
       */
      injectBlocked: (commissionId: string, title: string, question: string): void => {
        this.sim.enqueue({
          kind: 'sync',
          tasks: [
            {
              commissionId,
              kind: 'code',
              title,
              agentName: '甲',
              status: 'blocked',
              blockedQuestion: question,
            },
          ],
          finished: [],
          connected: true,
        });
      },
      /** 世界状态摘要：界面据此显示"世界缺什么"。 */
      worldState: () => {
        const state = summarizeWorld(this.snapshot);
        return {
          line: describeWorldState(state),
          missingWild: state.gaps.missingWild,
        };
      },
      /** 当前该呈给用户的急件；没有则为 null。 */
      decree: () => awaitingDecree(this.snapshot),
      /** 省电模式状态：当前帧率上限与焦点。 */
      ecoState: () => ({
        fpsLimit: this.game.loop.fpsLimit,
        hasFpsLimit: this.game.loop.hasFpsLimit,
        /** Phaser 实际渲染帧率：这是降频有没有生效的真正指标。 */
        actualFps: this.game.loop.actualFps,
        updateCount: this.updateCount,
        advanceCount: this.advanceCount,
        backgroundMode: this.backgroundMode,
        minFrameIntervalMs: this.minFrameIntervalMs,
        limitRate: (this.game.loop as unknown as { _limitRate: number })._limitRate,
        hasCallback: (this.game.loop as unknown as { callback?: unknown }).callback !== undefined,
        focused: document.visibilityState === 'visible' && document.hasFocus(),
      }),
      /** 渲染统计：排查性能时看每帧有多少绘制指令。 */
      renderStats: () => {
        const graphics = this.children.list.filter(
          (item) => item.type === 'Graphics',
        ) as Phaser.GameObjects.Graphics[];
        const counts = graphics.map((g) => g.commandBuffer?.length ?? 0);
        return {
          graphicsObjects: graphics.length,
          totalCommands: counts.reduce((sum, n) => sum + n, 0),
          largest: [...counts].sort((a, b) => b - a).slice(0, 5),
        };
      },
      /** 立刻把镜头中心对准世界坐标（不带动画）。小地图跳转与测试都用它。 */
      centerCameraOn: (x: number, y: number): void => this.cameraCtl.panTo(x, y, 0),
      /** 只查询某处能不能建，不改变任何状态。造物模式与测试都用它。 */
      canPlaceAt: (cell: GridPoint): boolean => {
        const session = this.creation;
        if (session === null) return false;
        const blueprint = this.blueprints.get(session.prefabId);
        if (blueprint === undefined) return false;
        return this.canPlaceBlueprintHere(blueprint, cell).ok;
      },
      camera: () => ({
        scrollX: this.cameras.main.scrollX,
        scrollY: this.cameras.main.scrollY,
        zoom: this.cameras.main.zoom,
        width: this.cameras.main.width,
        height: this.cameras.main.height,
        worldViewX: this.cameras.main.worldView.x,
        mode: this.cameraCtl.mode(),
      }),
      miracle: (kind: MiracleKind): void => {
        this.sim.enqueue({ kind: 'miracle', miracle: kind });
      },
      feed: (message: AgentToWorld): CommandResult => this.feed(message),
      pushLedger: (commission: unknown): void => this.pushLedger(commission),
      onResult: (listener: (result: CommandResult) => void): (() => void) => {
        this.resultListeners.add(listener);
        return () => this.resultListeners.delete(listener);
      },
      onStatus: (listener: (line: string) => void): (() => void) => {
        if (this.lastStatus !== '') listener(this.lastStatus);
        this.statusListeners.add(listener);
        return () => this.statusListeners.delete(listener);
      },
      report: (): DailyReport => composeReport(this.snapshot),
      slab: (): readonly OracleRecord[] => this.snapshot.oracle,
      view: (): WorldSnapshot => this.view(),
      setPhase: (phase: PhaseId): void => this.setPhase(phase),
      currentPhase: (): PhaseId => this.currentPhase(),
      realPhase: (): PhaseId => this.realPhase(),
      isAutoPhase: (): boolean => this.isAutoPhase(),
      setAutoPhase: (): void => this.setAutoPhase(),
      setNight: (enabled: boolean): void => this.setNight(enabled),
      toggleNight: (): boolean => this.toggleNight(),
      nightEnabled: (): boolean => this.nightEnabled(),
      events: (limit = 50) => this.recentEvents(limit),
      saveStatus: () => this.saveStatus(),
      saveNow: (): CommandResult => this.saveNow(),
      clearSave: (): CommandResult => this.clearSave(),
      loadSave: (): CommandResult => {
        window.location.reload();
        return { ok: true, message: '正在读档…' };
      },
    };
    (window as unknown as { Oracle: typeof api }).Oracle = api;
  }

  // ----------------------------------------------------------------- render

  private renderSnapshot(delta: number): void {
    const { snapshot } = this;
    this.syncBuildings();

    for (const entry of this.entries) {
      const workstation = snapshot.workstations.find(
        (item) => item.buildingId === entry.building.instanceId,
      );
      updateBuilding(this, entry.view, entry.building, workstation, ALTITUDE);
      const operating = workstation?.activity.kind === 'operating';
      const broken = workstation?.activity.kind === 'overheating';
      if ((operating || broken) && this.tick % 3 === 0) {
        // 视野外的烟不发射：逻辑照常结算，只是看不见就不必画。
        const chimney = entry.view.chimneyWorld;
        const view = this.cameras.main.worldView;
        if (shouldAnimateAt({ x: chimney.x, y: chimney.y }, view)) {
          const intensity = operating ? 1 + Math.min(2, (workstation?.progress ?? 0) * 2) : 2.5;
          entry.emitter.emitAt(chimney.x, chimney.y, intensity * 0.7);
        }
      }
    }

    if (snapshot.roads.length !== this.lastRoadCount) {
      this.lastRoadCount = snapshot.roads.length;
      this.decor.redrawRoads(snapshot.roads);
    }
    this.decor.syncTrails(snapshot.trails);
    this.decor.syncProps(snapshot.props);
    this.decor.syncCritters(snapshot.critters);
    const constructionKey = snapshot.constructions
      .map((item) => `${item.buildingId}:${Math.ceil(item.remainingMs / 100)}`)
      .join('|');
    if (constructionKey !== this.lastConstructionKey) {
      this.lastConstructionKey = constructionKey;
      this.decor.syncScaffolds(snapshot.constructions);
      this.drawScaffolds(snapshot);
    }

    // 多角色：每个 id 一个渲染实例；updateVillager 内部按 y 设 depth，天然 Y-Sort。
    for (const villager of snapshot.villagers) {
      let view = this.villagerViews.get(villager.id);
      if (view === undefined) {
        view = createVillager(this, 0, 0);
        this.villagerViews.set(villager.id, view);
      }
      updateVillager(view, villager, snapshot.now, 0, 0, ALTITUDE);
    }
    this.weatherView.update(this.sim.weatherIntensity(), delta);
    this.phaseView.update(this.snapshot, delta);
    // 鸟与蝙蝠读模拟时刻而不是墙钟：暂停世界时它们也停，不会出现
    // 「世界静止但天上还在飞」的穿帮。
    this.ambienceView.update(
      this.snapshot.now,
      this.phaseView.currentPhase(),
      this.cameras.main.worldView,
    );
    this.fogView.update(this.snapshot.fog, this.snapshot.now);

    // 气泡一出现就出声；同一个角色的同一句话不重复念。
    for (const villager of snapshot.villagers) {
      const bubble = villager.bubble;
      const last = this.lastBubbles.get(villager.id) ?? null;
      if (bubble !== null && bubble !== last) {
        this.lastBubbles.set(villager.id, bubble);
        this.animalese.speak(bubble);
        for (const listener of this.bubbleListeners) listener(bubble);
      } else if (bubble === null) {
        this.lastBubbles.set(villager.id, null);
      }
    }

    // 动态中文（名字/气泡）走 DOM overlay：按 canvas 实际位置每帧同步（D5-A）。
    this.overlay?.sync(snapshot);

    const line = this.statusLine();
    if (line !== this.lastStatus) {
      this.lastStatus = line;
      for (const listener of this.statusListeners) listener(line);
    }
  }

  /** 新落成的楼要补一套视图；按 instanceId 对齐，不假设顺序。 */
  private syncBuildings(): void {
    for (const building of this.snapshot.buildings) {
      if (this.entries.some((entry) => entry.building.instanceId === building.instanceId)) continue;
      this.entries.push({
        building,
        view: createBuilding(this, building, 0, 0),
        emitter: createAccentEmitter(this, building.accent),
      });
    }
  }

  private drawScaffolds(snapshot: WorldSnapshot): void {
    for (const construction of snapshot.constructions) {
      const container = this.decor.scaffolds.get(construction.buildingId);
      if (container === undefined) continue;
      container.removeAll(true);
      const building = snapshot.buildings.find(
        (item) => item.instanceId === construction.buildingId,
      );
      if (building === undefined) continue;
      const x = building.origin.x * TILE_PX;
      const y = building.origin.y * TILE_PX;
      const w = building.footprint.w * TILE_PX;
      const h = building.footprint.h * TILE_PX;
      const g = this.add.graphics();
      const progress = 1 - construction.remainingMs / construction.totalMs;
      g.lineStyle(1, WORLD.trim, 0.85);
      g.strokeRect(x + 1, y + 1, w - 2, h - 2);
      for (let px = x + 4; px < x + w; px += 8) {
        g.lineBetween(px, y + 2, px, y + h - 2);
      }
      g.lineStyle(1, WORLD.trim, 0.6);
      g.lineBetween(x + 1, y + h * progress, x + w - 1, y + h * progress);
      g.fillStyle(WORLD.ink, 0.55);
      g.fillRect(x + 1, y + 1, w - 2, h - 2);
      container.add(g);
      // 施工标签改图标（D5-A）：进度用线，不再画「施工中 xx%」中文。
      const icon = this.add.graphics();
      icon.fillStyle(WORLD.trim, 0.95);
      icon.fillRect(x + w / 2 - 4, y - 10, 9, 3);
      icon.fillRect(x + w / 2 - 1, y - 7, 2, 5);
      icon.fillStyle(WORLD.trim, 0.9);
      icon.fillRect(x + w / 2 - 4, y - 10, 1, 3);
      container.add(icon);
    }
  }

  private statusLine(): string {
    // 多角色摘要：主角色：…　/　助手：…
    const summary = this.snapshot.villagers
      .map((villager) => {
        const task = this.snapshot.tasks.find((item) => item.id === villager.taskId);
        const where = task === null || task === undefined ? null : this.buildingName(task.buildingId);
        const modeLabel: Record<typeof villager.mode, string> = {
          idle: villager.routine ?? '在祭坛边闲逛',
          saluting: '正向神谕行礼',
          toWork: where === null ? '赶路' : `赶往 ${where}`,
          working:
            where === null
              ? `在祭坛边办「${task?.summary ?? ''}」`
              : `在 ${where} 干活「${task?.summary ?? ''}」`,
          delivering: '正在交付',
          toAltar: '回到祭坛',
          sheltering: '跑到屋檐下躲雨',
          bearingDecree: '举着急件跑向祭坛',
        };
        return `${villager.name}：${modeLabel[villager.mode]}`;
      })
      .join('　/　');
    const weather = this.snapshot.weather === 'rain' ? '雨' : '晴';
    const faith = this.snapshot.faith;
    const { ledger: ledgerDelivered, mock: mockDelivered } = this.snapshot.deliveries;
    // 离线 Mock 必须显式标出：状态行不只说"台账离线"，还说现在跑的是 Mock。
    const ledgerState = this.snapshot.ledger.connected ? '台账在线' : '台账离线 · 离线 Mock';
    // 读档后未对账：明说，且此时台账单只读展示。
    const reconciledState =
      this.snapshot.ledger.reconciled ? ledgerState : `读档未对账 · ${ledgerState}`;
    // 分页取满时快照不完整：说出来，别让"少了一条"看起来像"被撤了"。
    const ledger = this.snapshot.ledger.complete
      ? reconciledState
      : `${reconciledState} · 台账可能未取全`;
    // 存档提示（坏档/高版本/配额）只写一次即可，这里保持可见直到下次成功写档。
    const saveNote = this.saveNotice === null ? '' : `　　${this.saveNotice}`;
    // blocked_question 只展示，不在世界内回答：完整值留在快照，这里按 60 字符截断。
    const waitingTask = this.snapshot.tasks.find((item) => item.blockedQuestion !== null);
    const waiting =
      waitingTask === undefined || waitingTask.blockedQuestion === null
        ? ''
        : `　　在等你的答复：${excerptBlockedQuestion(waitingTask.blockedQuestion)}`;
    // 排队中的神谕必须在状态行可见，否则跟"被拒"分不出来。
    const queued = this.snapshot.pending;
    const queue =
      queued.length === 0 ? '' : `　　排队 ${queued.length} 条：${queued[0]?.summary ?? ''}`;
    // 省略 target 的「交付」收谁的单：写清对象，免得两个都在干活时靠猜。
    const defaultWorker = this.snapshot.villagers.find(
      (villager) => villager.mode === 'working' && villager.taskId !== null,
    );
    const finishHint =
      defaultWorker === undefined ? '' : `　　默认交付：${defaultWorker.name}的单`;
    // 最近落下的那件摆件写清来路：这是"被记住"在纸面上唯一的具体落点。
    const latestProp = this.snapshot.props[this.snapshot.props.length - 1];
    const memory = latestProp === undefined ? '' : `　　${describePropSource(latestProp)}`;
    return `${summary}　　${weather}　　${ledger}　　台账交付 ${ledgerDelivered} 单 · Mock 交付 ${mockDelivered} 单　　神恩 ${faith.points}/${3}${memory}${waiting}${queue}${finishHint}${saveNote}`;
  }

  private buildingName(id: string | null): string | null {
    if (id === null) return null;
    return this.snapshot.buildings.find((item) => item.instanceId === id)?.displayName ?? id;
  }
}
