/** 唯一的 Phaser 场景。它只做三件事：把逻辑 tick 起来、把快照画出来、把输入转成命令。
 *
 * 世界逻辑全在 src/world/ 里，与 Phaser 无关；这里不判断"该不该开工"。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import { Simulation, type NewBuilding, type WorldCommand } from '../world/simulation.ts';
import type { CommandResult, WorldSnapshot } from '../world/types.ts';
import { excerptBlockedQuestion } from '../world/types.ts';
import { parseOracle } from '../world/oracle.ts';
import { placeBlueprint, type BuiltWorld } from '../world/setup.ts';
import type { PrefabBlueprint } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { drawTerrain } from '../render/tilemap.ts';
import { createBuilding, updateBuilding, type BuildingView } from '../render/buildings.ts';
import { createVillager, updateVillager, type VillagerView } from '../render/villager.ts';
import { createWorldOverlay, type WorldOverlay } from '../render/world-overlay.ts';
import { createAccentEmitter, ensureAccentTextures, type AccentEmitter } from '../render/smoke.ts';
import { createWeatherView, type WeatherView } from '../render/weather.ts';
import { createNightView, type NightView } from '../render/night.ts';
import { createDecorView, type DecorView } from '../render/decor.ts';
import { composeReport, type DailyReport } from '../world/report.ts';
import { createLazyAnimalese, type Animalese } from '../audio/animalese.ts';
import { toWorldCommand, type AgentToWorld } from '../world/bridge.ts';
import { LedgerClient } from '../world/ledger.ts';
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
  private nightView!: NightView;
  private nightKeyListener: ((event: KeyboardEvent) => void) | null = null;
  private decor!: DecorView;
  private entries: BuildingEntry[] = [];
  private accumulator = 0;
  private tick = 0;
  private villagerName = '莞尔';
  private lastStatus = '';
  private lastRoadCount = -1;
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

    drawTerrain(this, this.world.grid, originX, originY);
    ensureAccentTextures(this);
    this.decor = createDecorView(this, originX, originY);
    this.decor.redrawRoads([]);

    this.entries = this.world.buildings.map((building) => ({
      building,
      view: createBuilding(this, building, originX, originY),
      emitter: createAccentEmitter(this, building.accent),
    }));
    this.weatherView = createWeatherView(this);
    this.nightView = createNightView(this);
    this.nightView.setEnabled(new URLSearchParams(window.location.search).get('night') === '1');
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
      this.nightView.toggle();
    };
    window.addEventListener('keydown', this.nightKeyListener);

    this.drawAltar(originX, originY);

    const simDeps = {
      grid: this.world.grid,
      buildings: this.world.buildings,
      altar: this.world.altar,
      villagerName: this.villagerName,
      spawnBuilding: (prefabId: string, instanceId: string): NewBuilding | null => {
        const blueprint = this.blueprints.get(prefabId);
        if (blueprint === undefined) return null;
        return placeBlueprint(this.world.grid, blueprint, instanceId, this.world.altar);
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
    this.ledger?.stop();
    this.ledger = null;
    this.overlay?.dispose();
    this.overlay = null;
    if (this.nightKeyListener !== null) {
      window.removeEventListener('keydown', this.nightKeyListener);
      this.nightKeyListener = null;
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
    this.accumulator += Math.min(delta, 250);
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
    // 只在 update() 返回后写档（命令已结算，ADR_SAVE §4）。
    this.maybeAutoSave();
  }

  // ------------------------------------------------------------------ input

  /** 网页与终端共用这一个口子。文本 → 意图 → 命令，全部在这里收敛。 */
  say(text: string): CommandResult {
    const intent = parseOracle(text);
    if (intent.kind === 'unknown') {
      return { ok: false, message: '没听懂这句，世界没有动。' };
    }
    let command: WorldCommand;
    switch (intent.kind) {
      case 'dispatch':
        command = { kind: 'dispatch', taskKind: intent.taskKind, summary: intent.summary };
        break;
      case 'weather':
        command = { kind: 'setWeather', weather: intent.weather };
        break;
      case 'cancelPending':
        command = { kind: 'cancelPending' };
        break;
      default:
        command = { kind: 'complete' };
    }
    // 命令不直接改世界，排进队列，由下一个逻辑步统一结算。
    this.sim.enqueue(command);
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

  /** 夜景是纯渲染态：不写世界、不写台账；页面按 N 或 ?night=1 切换。 */
  setNight(enabled: boolean): void {
    this.nightView.setEnabled(enabled);
  }

  toggleNight(): boolean {
    return this.nightView.toggle();
  }

  nightEnabled(): boolean {
    return this.nightView.isEnabled();
  }

  private registerOracleApi(): void {
    const api = {
      say: (text: string): CommandResult => this.say(text),
      dispatch: (kind: 'code' | 'research' | 'writing' | 'quick', summary: string): void =>
        this.dispatch(kind, summary),
      finish: (target?: string): void => this.finish(target),
      build: (prefabId: string): void => this.build(prefabId),
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
      view: (): WorldSnapshot => this.view(),
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
        const intensity = operating ? 1 + Math.min(2, (workstation?.progress ?? 0) * 2) : 2.5;
        entry.emitter.emitAt(entry.view.chimneyWorld.x, entry.view.chimneyWorld.y, intensity * 0.7);
      }
    }

    if (snapshot.roads.length !== this.lastRoadCount) {
      this.lastRoadCount = snapshot.roads.length;
      this.decor.redrawRoads(snapshot.roads);
    }
    this.decor.syncProps(snapshot.props);
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
    this.nightView.update(this.snapshot, delta);

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
    return `${summary}　　${weather}　　${ledger}　　台账交付 ${ledgerDelivered} 单 · Mock 交付 ${mockDelivered} 单　　神恩 ${faith.points}/${3}${waiting}${queue}${finishHint}${saveNote}`;
  }

  private buildingName(id: string | null): string | null {
    if (id === null) return null;
    return this.snapshot.buildings.find((item) => item.instanceId === id)?.displayName ?? id;
  }
}
