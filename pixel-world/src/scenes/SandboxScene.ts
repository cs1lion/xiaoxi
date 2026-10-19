/** 唯一的 Phaser 场景。它只做三件事：把逻辑 tick 起来、把快照画出来、把输入转成命令。
 *
 * 世界逻辑全在 src/world/ 里，与 Phaser 无关；这里不判断"该不该开工"。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import { Simulation, type NewBuilding, type WorldCommand } from '../world/simulation.ts';
import type { CommandResult, WorldSnapshot } from '../world/types.ts';
import { parseOracle } from '../world/oracle.ts';
import { placeBlueprint, type BuiltWorld } from '../world/setup.ts';
import type { PrefabBlueprint } from '../world/types.ts';
import { UI } from '../render/palette.ts';
import { drawTerrain } from '../render/tilemap.ts';
import { createBuilding, updateBuilding, type BuildingView } from '../render/buildings.ts';
import { createVillager, updateVillager, type VillagerView } from '../render/villager.ts';
import { createAccentEmitter, ensureAccentTextures, type AccentEmitter } from '../render/smoke.ts';
import { createWeatherView, type WeatherView } from '../render/weather.ts';
import { createDecorView, type DecorView } from '../render/decor.ts';
import { composeReport, type DailyReport } from '../world/report.ts';
import { createLazyAnimalese, type Animalese } from '../audio/animalese.ts';
import { toWorldCommand, type AgentToWorld } from '../world/bridge.ts';
import { LedgerClient } from '../world/ledger.ts';

export interface ScenePayload {
  readonly world: BuiltWorld;
  readonly blueprints: ReadonlyMap<string, PrefabBlueprint>;
  readonly villagerName: string;
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
  private villagerView!: VillagerView;
  private weatherView!: WeatherView;
  private decor!: DecorView;
  private entries: BuildingEntry[] = [];
  private accumulator = 0;
  private tick = 0;
  private villagerName = '莞尔';
  private lastStatus = '';
  private lastRoadCount = -1;
  private lastConstructionKey = '';
  private lastBubble: string | null = null;
  private readonly animalese: Animalese = createLazyAnimalese();
  private ledger: LedgerClient | null = null;
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
  }

  create(): void {
    const originX = 0;
    const originY = 0;
    this.cameras.main.setBackgroundColor(UI.sky);

    drawTerrain(this, this.world.grid, originX, originY);
    ensureAccentTextures(this);
    this.decor = createDecorView(this, originX, originY);
    this.decor.redrawRoads([]);

    this.entries = this.world.buildings.map((building) => ({
      building,
      view: createBuilding(this, building, originX, originY),
      emitter: createAccentEmitter(this, building.accent),
    }));
    this.villagerView = createVillager(this, this.villagerName, originX, originY);
    this.weatherView = createWeatherView(this);

    this.drawAltar(originX, originY);

    this.sim = new Simulation({
      grid: this.world.grid,
      buildings: this.world.buildings,
      altar: this.world.altar,
      villagerName: this.villagerName,
      spawnBuilding: (prefabId, instanceId): NewBuilding | null => {
        const blueprint = this.blueprints.get(prefabId);
        if (blueprint === undefined) return null;
        return placeBlueprint(this.world.grid, blueprint, instanceId, this.world.altar);
      },
    });
    this.snapshot = this.sim.snapshot();
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
          connected: view.connected,
        });
      },
    });
    this.ledger = client;
    void client.start();
  }

  shutdown(): void {
    this.ledger?.stop();
    this.ledger = null;
  }

  private drawAltar(originX: number, originY: number): void {
    const altarPx = this.world.altar;
    const ax = originX + altarPx.x * TILE_PX;
    const ay = originY + altarPx.y * TILE_PX;
    const altar = this.add.graphics();
    altar.fillStyle(0x5a5f4c, 1);
    altar.fillRect(ax - 6, ay - 6, 44, 44);
    altar.lineStyle(1, 0x8d8f74, 0.8);
    altar.strokeRect(ax - 6, ay - 6, 44, 44);
    altar.fillStyle(0x2c302a, 1);
    altar.fillRect(ax + 3, ay + 3, 26, 26);
    altar.fillStyle(0xd9c27a, 0.9);
    altar.fillRect(ax + 9, ay + 9, 14, 2);
    altar.fillRect(ax + 15, ay + 13, 2, 8);
    altar.setDepth(ALTITUDE + ay + 40);
    this.add
      .text(ax + 16, ay - 10, '神谕祭坛', {
        fontFamily: 'ui-monospace, monospace',
        fontSize: '10px',
        color: '#cfc7ae',
      })
      .setOrigin(0.5, 1)
      .setDepth(ALTITUDE + ay + 41);
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
  }

  // ------------------------------------------------------------------ input

  /** 网页与终端共用这一个口子。文本 → 意图 → 命令，全部在这里收敛。 */
  say(text: string): CommandResult {
    const intent = parseOracle(text);
    if (intent.kind === 'unknown') {
      return { ok: false, message: '没听懂这句，世界没有动。' };
    }
    const command: WorldCommand =
      intent.kind === 'dispatch'
        ? { kind: 'dispatch', taskKind: intent.taskKind, summary: intent.summary }
        : intent.kind === 'weather'
          ? { kind: 'setWeather', weather: intent.weather }
          : { kind: 'complete' };
    // 命令不直接改世界，排进队列，由下一个逻辑步统一结算。
    this.sim.enqueue(command);
    return { ok: true, message: '神谕已投递，随下一拍生效。' };
  }

  dispatch(kind: 'code' | 'research' | 'writing' | 'quick', summary: string): void {
    this.sim.enqueue({ kind: 'dispatch', taskKind: kind, summary });
  }

  finish(): void {
    this.sim.enqueue({ kind: 'complete' });
  }

  build(prefabId: string): void {
    const instanceId = `${prefabId}#${this.snapshot.buildings.length + 1}`;
    this.sim.enqueue({ kind: 'build', prefabId, instanceId });
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

  private registerOracleApi(): void {
    const api = {
      say: (text: string): CommandResult => this.say(text),
      dispatch: (kind: 'code' | 'research' | 'writing' | 'quick', summary: string): void =>
        this.dispatch(kind, summary),
      finish: (): void => this.finish(),
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

    updateVillager(this.villagerView, snapshot.villager, 0, 0, ALTITUDE);
    this.weatherView.update(this.sim.weatherIntensity(), delta);

    // 气泡一出现就出声；同一句话不重复念。
    const bubble = snapshot.villager.bubble;
    if (bubble !== null && bubble !== this.lastBubble) {
      this.lastBubble = bubble;
      this.animalese.speak(bubble);
      for (const listener of this.bubbleListeners) listener(bubble);
    } else if (bubble === null) {
      this.lastBubble = null;
    }

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
      g.lineStyle(1, 0xc9a86a, 0.85);
      g.strokeRect(x + 1, y + 1, w - 2, h - 2);
      for (let px = x + 4; px < x + w; px += 8) {
        g.lineBetween(px, y + 2, px, y + h - 2);
      }
      g.lineStyle(1, 0x8f7a4c, 0.6);
      g.lineBetween(x + 1, y + h * progress, x + w - 1, y + h * progress);
      g.fillStyle(0x2a241c, 0.55);
      g.fillRect(x + 1, y + 1, w - 2, h - 2);
      container.add(g);
      const label = this.add.text(x + w / 2, y - 4, `施工中 ${Math.round(progress * 100)}%`, {
        fontFamily: 'ui-monospace, monospace',
        fontSize: '10px',
        color: '#e0c98b',
      });
      label.setOrigin(0.5, 1);
      container.add(label);
    }
  }

  private statusLine(): string {
    const villager = this.snapshot.villager;
    const task = this.snapshot.tasks.find((item) => item.id === villager.taskId);
    const where = task === null || task === undefined ? null : this.buildingName(task.buildingId);
    const modeLabel: Record<typeof villager.mode, string> = {
      idle: villager.routine ?? '在祭坛边闲逛',
      saluting: '正向神谕行礼',
      toWork: where === null ? '赶路' : `赶往 ${where}`,
      working: where === null ? '在祭坛边当场办' : `在 ${where} 干活`,
      delivering: '正在交付',
      toAltar: '回到祭坛',
      sheltering: '跑到屋檐下躲雨',
    };
    const weather = this.snapshot.weather === 'rain' ? '雨' : '晴';
    const faith = this.snapshot.faith;
    const ledger = this.snapshot.ledger.connected ? '台账在线' : '台账离线';
    return `${villager.name}：${modeLabel[villager.mode]}　　${weather}　　${ledger}　　已交付 ${this.completedCount()} 单　　神恩 ${faith.points}/${3}`;
  }

  private buildingName(id: string | null): string | null {
    if (id === null) return null;
    return this.snapshot.buildings.find((item) => item.instanceId === id)?.displayName ?? id;
  }

  private completedCount(): number {
    return this.snapshot.tasks.filter((task) => task.status === 'done').length;
  }
}
