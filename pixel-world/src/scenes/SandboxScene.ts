/** 唯一的 Phaser 场景。它只做三件事：把逻辑 tick 起来、把快照画出来、把输入转成命令。
 *
 * 世界逻辑全在 src/world/ 里，与 Phaser 无关；这里不判断"该不该开工"。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import { Simulation, type WorldCommand } from '../world/simulation.ts';
import type { CommandResult, WorldSnapshot } from '../world/types.ts';
import { parseOracle } from '../world/oracle.ts';
import type { BuiltWorld } from '../world/setup.ts';
import { UI } from '../render/palette.ts';
import { drawTerrain } from '../render/tilemap.ts';
import { createBuilding, updateBuilding, type BuildingView } from '../render/buildings.ts';
import { createVillager, updateVillager, type VillagerView } from '../render/villager.ts';
import { createAccentEmitter, ensureAccentTextures, type AccentEmitter } from '../render/smoke.ts';

export interface ScenePayload {
  readonly world: BuiltWorld;
  readonly villagerName: string;
}

const LOGIC_STEP_MS = 100;
const MAX_STEPS_PER_FRAME = 5;
const ALTITUDE = 1000;

export class SandboxScene extends Phaser.Scene {
  private world!: BuiltWorld;
  private sim!: Simulation;
  private snapshot!: WorldSnapshot;
  private villagerView!: VillagerView;
  private buildings: { view: BuildingView; emitter: AccentEmitter }[] = [];
  private accumulator = 0;
  private tick = 0;
  private villagerName = '莞尔';
  private lastStatus = '';
  private readonly resultListeners = new Set<(result: CommandResult) => void>();
  private readonly statusListeners = new Set<(line: string) => void>();

  constructor() {
    super({ key: 'SandboxScene' });
  }

  init(payload: ScenePayload): void {
    this.world = payload.world;
    this.villagerName = payload.villagerName;
  }

  create(): void {
    const originX = 0;
    const originY = 0;
    this.cameras.main.setBackgroundColor(UI.sky);

    drawTerrain(this, this.world.grid, originX, originY);
    ensureAccentTextures(this);

    this.buildings = this.world.buildings.map((building) => {
      const view = createBuilding(this, building, originX, originY);
      const emitter = createAccentEmitter(this, building.accent);
      return { view, emitter };
    });
    this.villagerView = createVillager(this, this.villagerName, originX, originY);

    const altarPx = this.world.altar;
    const altar = this.add.graphics();
    const ax = originX + altarPx.x * TILE_PX;
    const ay = originY + altarPx.y * TILE_PX;
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

    this.sim = new Simulation({
      grid: this.world.grid,
      buildings: this.world.buildings,
      altar: this.world.altar,
      villagerName: this.villagerName,
      random: () => Math.random(),
    });
    this.snapshot = this.sim.snapshot();
    this.registerOracleApi();
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
    this.renderSnapshot();
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
        : { kind: 'complete' };
    // 命令不直接改世界，排进队列，由下一个逻辑步统一结算。
    this.sim.enqueue(command);
    return { ok: true, message: '神谕已投递，随下一拍生效。' };
  }

  dispatch(kind: 'code' | 'research' | 'writing', summary: string): void {
    this.sim.enqueue({ kind: 'dispatch', taskKind: kind, summary });
  }

  finish(): void {
    this.sim.enqueue({ kind: 'complete' });
  }

  view(): WorldSnapshot {
    return this.snapshot;
  }

  private registerOracleApi(): void {
    const api = {
      say: (text: string): CommandResult => this.say(text),
      dispatch: (kind: 'code' | 'research' | 'writing', summary: string): void =>
        this.dispatch(kind, summary),
      finish: (): void => this.finish(),
      onResult: (listener: (result: CommandResult) => void): (() => void) => {
        this.resultListeners.add(listener);
        return () => this.resultListeners.delete(listener);
      },
      onStatus: (listener: (line: string) => void): (() => void) => {
        if (this.lastStatus !== '') listener(this.lastStatus);
        this.statusListeners.add(listener);
        return () => this.statusListeners.delete(listener);
      },
      view: (): WorldSnapshot => this.view(),
    };
    (window as unknown as { Oracle: typeof api }).Oracle = api;
  }

  // ----------------------------------------------------------------- render

  private renderSnapshot(): void {
    const { snapshot } = this;
    for (const [index, building] of this.world.buildings.entries()) {
      const entry = this.buildings[index];
      const workstation = snapshot.workstations.find(
        (item) => item.buildingId === building.instanceId,
      );
      updateBuilding(this, entry.view, building, workstation, ALTITUDE);
      if (workstation?.activity.kind === 'operating' && this.tick % 3 === 0) {
        const intensity = 1 + Math.min(2, workstation.progress * 2);
        entry.emitter.emitAt(entry.view.chimneyWorld.x, entry.view.chimneyWorld.y, intensity * 0.7);
      }
    }
    updateVillager(this.villagerView, snapshot.villager, 0, 0, ALTITUDE);
    const line = this.statusLine();
    if (line !== this.lastStatus) {
      this.lastStatus = line;
      for (const listener of this.statusListeners) listener(line);
    }
  }

  private statusLine(): string {
    const villager = this.snapshot.villager;
    const working = this.snapshot.tasks.find((task) => task.id === villager.taskId);
    const modeLabel: Record<typeof villager.mode, string> = {
      idle: '在祭坛边闲逛',
      saluting: '正向神谕行礼',
      toWork: working ? `赶往 ${this.buildingName(working.buildingId)}` : '赶路',
      working: working ? `在 ${this.buildingName(working.buildingId)} 干活` : '干活',
      delivering: '正在交付',
      toAltar: '回到祭坛',
    };
    return `${villager.name}：${modeLabel[villager.mode]}　　已交付 ${this.completedCount()} 单`;
  }

  private buildingName(id: string): string {
    return this.world.buildings.find((item) => item.instanceId === id)?.displayName ?? id;
  }

  private completedCount(): number {
    return this.snapshot.tasks.filter((task) => task.status === 'done').length;
  }

}
