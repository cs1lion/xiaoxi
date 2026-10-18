/** 世界运行时。固定步长由调用方给，内部不读时钟，因此可以离线回放。
 *
 * 外部只能从一个口子进来：enqueue()。命令在下次 update() 开头统一结算，
 * 渲染循环里不会出现半更新的中间态。
 */

import type { WorldGrid } from './grid.ts';
import type {
  CommandResult,
  Facing,
  GridPoint,
  PlacedBuilding,
  TaskRecord,
  TaskKind,
  Vec2,
  VillagerMode,
  WorkstationActivity,
  WorkstationSnapshot,
  WorldEvent,
  WorldSnapshot,
} from './types.ts';

export type WorldCommand =
  | { readonly kind: 'dispatch'; readonly taskKind: TaskKind; readonly summary: string }
  | { readonly kind: 'complete' };

export interface SimulationDeps {
  readonly grid: WorldGrid;
  readonly buildings: readonly PlacedBuilding[];
  readonly altar: GridPoint;
  readonly villagerName: string;
  /** 0..1，注入后世界完全确定。 */
  readonly random: () => number;
}

interface Villager {
  mode: VillagerMode;
  pos: Vec2;
  facing: Facing;
  visible: boolean;
  path: GridPoint[];
  taskId: string | null;
  bubble: string | null;
  bubbleUntil: number;
  saluteUntil: number;
  deliverUntil: number;
  nextWanderAt: number;
}

const SPEED_CELLS_PER_SECOND = 3;
const SALUTE_MS = 700;
const DELIVER_MS = 1400;
const WANDER_INTERVAL_MS = 2400;
const WANDER_RADIUS = 3;
const COOLING_MS = 900;
const EVENT_LIMIT = 40;

const ESTIMATE_MS: Record<TaskKind, number> = {
  code: 6000,
  research: 4200,
  writing: 3200,
};

export class Simulation {
  private readonly grid: WorldGrid;
  private readonly buildings: readonly PlacedBuilding[];
  private readonly altar: GridPoint;
  private readonly random: () => number;
  private readonly villagerName: string;

  private readonly villager: Villager;
  private readonly tasks: TaskRecord[] = [];
  private readonly activities = new Map<string, WorkstationActivity>();
  private readonly events: WorldEvent[] = [];
  private readonly queue: WorldCommand[] = [];
  private readonly lastResults: CommandResult[] = [];

  /** 神谕下达时算好的路线，等行礼结束再交给她走。 */
  private pendingRoute: GridPoint[] = [];

  private now = 0;
  private nextTaskNumber = 1;

  constructor(deps: SimulationDeps) {
    this.grid = deps.grid;
    this.buildings = deps.buildings;
    this.altar = deps.altar;
    this.random = deps.random;
    this.villagerName = deps.villagerName;
    this.villager = {
      mode: 'idle',
      pos: { x: deps.altar.x, y: deps.altar.y },
      facing: 'down',
      visible: true,
      path: [],
      taskId: null,
      bubble: null,
      bubbleUntil: 0,
      saluteUntil: 0,
      deliverUntil: 0,
      nextWanderAt: WANDER_INTERVAL_MS,
    };
    for (const building of this.buildings) {
      this.activities.set(building.instanceId, { kind: 'idle' });
    }
    this.log('info', '世界醒来。祭坛等待神谕。');
  }

  enqueue(command: WorldCommand): void {
    this.queue.push(command);
  }

  /** 返回本次 update 结算的命令结果，供调用方展示。 */
  update(deltaMs: number): readonly CommandResult[] {
    if (deltaMs < 0) throw new Error('deltaMs 不能为负');
    this.now += deltaMs;
    this.lastResults.length = 0;
    while (this.queue.length > 0) {
      const command = this.queue.shift();
      if (command === undefined) break;
      this.lastResults.push(this.apply(command));
    }
    this.stepVillager(deltaMs);
    this.stepWorkstations();
    this.expireBubble();
    return this.lastResults;
  }

  snapshot(): WorldSnapshot {
    const workstations: WorkstationSnapshot[] = this.buildings.map((building) => {
      const activity = this.activities.get(building.instanceId) ?? { kind: 'idle' };
      const elapsedMs =
        activity.kind === 'operating' ? Math.max(0, this.now - activity.startedAt) : 0;
      const progress =
        activity.kind === 'operating'
          ? Math.min(1, elapsedMs / Math.max(1, activity.estimateMs))
          : activity.kind === 'cooling'
            ? 1
            : 0;
      return { buildingId: building.instanceId, activity, elapsedMs, progress };
    });
    return {
      now: this.now,
      altar: this.altar,
      villager: {
        name: this.villagerName,
        mode: this.villager.mode,
        pos: this.villager.pos,
        facing: this.villager.facing,
        visible: this.villager.visible,
        taskId: this.villager.taskId,
        bubble: this.villager.bubble,
      },
      buildings: this.buildings,
      workstations,
      tasks: this.tasks.map((task) => ({ ...task })),
      events: this.events.slice(-12),
    };
  }

  // ---------------------------------------------------------------- commands

  private apply(command: WorldCommand): CommandResult {
    if (command.kind === 'dispatch') return this.dispatch(command.taskKind, command.summary);
    return this.completeManually();
  }

  private dispatch(taskKind: TaskKind, summary: string): CommandResult {
    if (this.villager.mode !== 'idle' && this.villager.mode !== 'saluting') {
      const message = '她手上有活，神谕排在后面。';
      this.log('warn', message);
      return { ok: false, message };
    }
    const building = this.buildings.find((item) => item.boundTask === taskKind);
    if (building === undefined) {
      const message = `没有承接 ${taskKind} 的铺子。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const path = this.grid.path(this.cellOf(this.villager.pos), building.door);
    if (path.length === 0) {
      const message = `${building.displayName} 门口走不过去。`;
      this.log('warn', message);
      return { ok: false, message };
    }

    const task: TaskRecord = {
      id: `t${this.nextTaskNumber}`,
      kind: taskKind,
      summary,
      buildingId: building.instanceId,
      status: 'assigned',
      createdAt: this.now,
      completedAt: null,
    };
    this.nextTaskNumber += 1;
    this.tasks.push(task);

    this.villager.mode = 'saluting';
    this.villager.path = [];
    this.villager.taskId = task.id;
    this.villager.bubble = '！吾神降旨！';
    this.villager.bubbleUntil = this.now + 1500;
    this.villager.saluteUntil = this.now + SALUTE_MS;
    this.villager.facing = 'down';
    this.pendingRoute = path;

    this.log('oracle', `神谕：${summary} → ${building.displayName}`);
    return { ok: true, message: `神谕已送达 ${building.displayName}。` };
  }

  private completeManually(): CommandResult {
    if (this.villager.mode !== 'working') {
      const message = '她现在没有在干活，无需交付。';
      this.log('warn', message);
      return { ok: false, message };
    }
    this.finishActiveTask();
    return { ok: true, message: '成果已交付。' };
  }

  // ---------------------------------------------------------------- villager

  private stepVillager(deltaMs: number): void {
    switch (this.villager.mode) {
      case 'saluting':
        if (this.now >= this.villager.saluteUntil) {
          this.villager.mode = 'toWork';
          this.villager.path = this.pendingRoute;
          this.pendingRoute = [];
          if (this.villager.path.length === 0) this.arriveAtWork();
        }
        return;
      case 'toWork':
        this.walk(deltaMs);
        if (this.villager.path.length === 0) this.arriveAtWork();
        return;
      case 'working':
        return;
      case 'delivering':
        if (this.now >= this.villager.deliverUntil) {
          this.villager.mode = 'toAltar';
          this.villager.path = this.grid.path(
            this.cellOf(this.villager.pos),
            this.altar,
          );
          this.villager.bubble = null;
          if (this.villager.path.length === 0) this.arriveAtAltar();
        }
        return;
      case 'toAltar':
        this.walk(deltaMs);
        if (this.villager.path.length === 0) this.arriveAtAltar();
        return;
      case 'idle':
        this.wander(deltaMs);
        return;
    }
  }

  private wander(deltaMs: number): void {
    if (this.villager.path.length > 0) {
      this.walk(deltaMs);
      return;
    }
    if (this.now < this.villager.nextWanderAt) return;
    this.villager.nextWanderAt = this.now + WANDER_INTERVAL_MS;
    const target = this.grid.randomWalkableNear(this.altar, WANDER_RADIUS, this.random);
    if (target === null) return;
    this.villager.path = this.grid.path(this.cellOf(this.villager.pos), target);
  }

  private walk(deltaMs: number): void {
    let budget = (SPEED_CELLS_PER_SECOND * deltaMs) / 1000;
    while (budget > 0 && this.villager.path.length > 0) {
      const next = this.villager.path[0];
      const dx = next.x - this.villager.pos.x;
      const dy = next.y - this.villager.pos.y;
      const distance = Math.hypot(dx, dy);
      if (distance <= budget) {
        this.villager.pos = { x: next.x, y: next.y };
        budget -= distance;
        this.villager.path.shift();
        continue;
      }
      const scale = budget / distance;
      this.villager.pos = {
        x: this.villager.pos.x + dx * scale,
        y: this.villager.pos.y + dy * scale,
      };
      this.villager.facing = facingFor(dx, dy);
      budget = 0;
    }
  }

  private arriveAtWork(): void {
    const task = this.activeTask();
    if (task === null) {
      this.villager.mode = 'idle';
      return;
    }
    task.status = 'in_progress';
    this.villager.mode = 'working';
    this.villager.visible = false;
    this.villager.bubble = null;
    const building = this.buildingOf(task);
    if (building !== undefined) {
      this.villager.pos = { x: building.workSpot.x, y: building.workSpot.y };
      this.activities.set(building.instanceId, {
        kind: 'operating',
        taskId: task.id,
        startedAt: this.now,
        estimateMs: ESTIMATE_MS[task.kind],
      });
      this.log('work', `${building.displayName} 开工。`);
    }
  }

  private arriveAtAltar(): void {
    this.villager.mode = 'idle';
    this.villager.taskId = null;
    this.villager.nextWanderAt = this.now + WANDER_INTERVAL_MS;
  }

  private stepWorkstations(): void {
    for (const building of this.buildings) {
      const activity = this.activities.get(building.instanceId);
      if (activity === undefined) continue;
      if (activity.kind === 'operating' && this.now - activity.startedAt >= activity.estimateMs) {
        const task = this.tasks.find((item) => item.id === activity.taskId);
        if (
          task !== undefined &&
          task.status === 'in_progress' &&
          this.villager.mode === 'working'
        ) {
          this.finishActiveTask();
        } else {
          this.activities.set(building.instanceId, { kind: 'idle' });
        }
        continue;
      }
      if (activity.kind === 'cooling' && this.now >= activity.until) {
        this.activities.set(building.instanceId, { kind: 'idle' });
      }
    }
  }

  private finishActiveTask(): void {
    const task = this.activeTask();
    if (task === null) return;
    task.status = 'done';
    task.completedAt = this.now;
    const building = this.buildingOf(task);
    if (building !== undefined) {
      this.activities.set(building.instanceId, {
        kind: 'cooling',
        taskId: task.id,
        until: this.now + COOLING_MS,
      });
      this.villager.pos = { x: building.door.x, y: building.door.y };
    }
    this.villager.mode = 'delivering';
    this.villager.visible = true;
    this.villager.facing = 'down';
    this.villager.bubble = '任务交付！';
    this.villager.bubbleUntil = this.now + DELIVER_MS;
    this.villager.deliverUntil = this.now + DELIVER_MS;
    this.villager.path = [];
    this.log('done', `${task.summary} 已交付。`);
  }

  private activeTask(): TaskRecord | null {
    if (this.villager.taskId === null) return null;
    return this.tasks.find((item) => item.id === this.villager.taskId) ?? null;
  }

  private buildingOf(task: TaskRecord): PlacedBuilding | undefined {
    return this.buildings.find((item) => item.instanceId === task.buildingId);
  }

  private expireBubble(): void {
    if (this.villager.bubble !== null && this.now >= this.villager.bubbleUntil) {
      this.villager.bubble = null;
    }
  }

  private cellOf(pos: Vec2): GridPoint {
    return { x: Math.round(pos.x), y: Math.round(pos.y) };
  }

  private log(tone: WorldEvent['tone'], text: string): void {
    this.events.push({ at: this.now, tone, text });
    if (this.events.length > EVENT_LIMIT) this.events.splice(0, this.events.length - EVENT_LIMIT);
  }
}

function facingFor(dx: number, dy: number): Facing {
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'right' : 'left';
  return dy >= 0 ? 'down' : 'up';
}
