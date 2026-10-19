/** 世界运行时。固定步长由调用方给，内部不读时钟，因此可以离线回放。
 *
 * 外部只能从一个口子进来：enqueue()。命令在下次 update() 开头统一结算，
 * 渲染循环里不会出现半更新的中间态。
 */

import type { WorldGrid } from './grid.ts';
import { TASK_PROFILES } from './tasks.ts';
import {
  nextProp,
  MILESTONE_POINTS,
  milestoneFor,
  POINTS_PER_TASK,
} from './faith.ts';
import { routineBeats, routineFor, shelterTarget, type RoutineBeat } from './routines.ts';
import {
  clearWeather,
  rainWeather,
  stepWeather,
  type WeatherState,
} from './weather.ts';
import type {
  CommandResult,
  ConstructionSnapshot,
  Facing,
  GridPoint,
  PlacedBuilding,
  TaskRecord,
  TaskKind,
  Vec2,
  VillagerMode,
  Weather,
  WorkstationActivity,
  WorkstationSnapshot,
  WorldEvent,
  WorldProp,
  WorldSnapshot,
} from './types.ts';
import { excerptBlockedQuestion } from './types.ts';

export type WorldCommand =
  | { readonly kind: 'dispatch'; readonly taskKind: TaskKind; readonly summary: string }
  | { readonly kind: 'complete' }
  | { readonly kind: 'setWeather'; readonly weather: Weather }
  | { readonly kind: 'build'; readonly prefabId: string; readonly instanceId: string }
  /** 台账快照。这是世界接真实工作的唯一入口。 */
  | {
      readonly kind: 'sync';
      readonly tasks: readonly {
        readonly commissionId: string;
        readonly kind: TaskKind;
        readonly title: string;
        readonly agentName: string;
        /** 台账状态；blocked 表示这单在等用户答复。旧调用方省略时按无 blocked 处理。 */
        readonly status?: string;
        /** blocked 单要请示的问题；只透传，世界不回答。省略/空串即没有。 */
        readonly blockedQuestion?: string;
      }[];
      /** 台账里已经收工的督办；世界据此把她的活收尾。 */
      readonly finished: readonly string[];
      readonly connected: boolean;
      /**
       * 快照是否完整（REST 没取满 limit）。false 时"不在快照里"不能当撤单，
       * 否则台账一次分页截断就会把她手上的活误判成被撤回。省略按完整处理。
       */
      readonly complete?: boolean;
      /**
       * 活动督办的优先级顺序（LedgerView.order）。省略时退回插入序，
       * 保持旧调用方（测试、旧快照）行为不变。
       */
      readonly order?: readonly string[];
    };

export interface NewBuilding {
  readonly building: PlacedBuilding;
  readonly road: readonly GridPoint[];
}

export interface SimulationDeps {
  readonly grid: WorldGrid;
  readonly buildings: readonly PlacedBuilding[];
  readonly altar: GridPoint;
  readonly villagerName: string;
  /** 造物主：把一栋新楼落进世界。返回 null 表示空间不够。 */
  readonly spawnBuilding?: (prefabId: string, instanceId: string) => NewBuilding | null;
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
  nextRoutineAt: number;
  routineIndex: number;
  routineLabel: string | null;
  inPlaceUntil: number;
}

const SPEED_CELLS_PER_SECOND = 3;
const SALUTE_MS = 700;
const DELIVER_MS = 1400;
const COOLING_MS = 900;
const EVENT_LIMIT = 60;
const CONSTRUCTION_MS = 3200;
const QUICK_DONE_MS = 900;

/** 交付攒到里程碑时，世界多一件东西。 */
const FAITH_MILESTONE = MILESTONE_POINTS;

export class Simulation {
  private readonly grid: WorldGrid;
  private readonly altar: GridPoint;
  private readonly villagerName: string;
  private readonly spawnBuilding: ((prefabId: string, instanceId: string) => NewBuilding | null) | undefined;

  private readonly buildings: PlacedBuilding[];
  private readonly villager: Villager;
  private readonly tasks: TaskRecord[] = [];
  private readonly activities = new Map<string, WorkstationActivity>();
  private readonly events: WorldEvent[] = [];
  private readonly queue: WorldCommand[] = [];
  private readonly lastResults: CommandResult[] = [];
  private readonly props: WorldProp[] = [];
  private readonly roads: GridPoint[] = [];
  private readonly constructions = new Map<string, { remainingMs: number; totalMs: number }>();

  private readonly beats: readonly RoutineBeat[];
  private weather: WeatherState = clearWeather();
  private faithPoints = 0;
  private milestones = 0;
  /** 交付统计按来源分开：台账的活只有台账能收，沙盒/离线 Mock 另计。 */
  private ledgerDeliveries = 0;
  private mockDeliveries = 0;
  /** 上游台账是否在线。离线时世界照常过日子，但状态行必须说出来。 */
  private ledgerConnected = false;
  private ledgerSyncedAt: number | null = null;
  /**
   * 最近一次快照是否完整（REST 没取满 limit）。false 时"不在快照里"不能当撤单，
   * 否则一次分页截断就会把她手上的活误判成被上游撤回。
   */
  private ledgerComplete = true;
  /** 台账给的派活优先级（commissionId 从高到低）；空表示没给顺序，退回插入序。 */
  private ledgerOrder: readonly string[] = [];

  /** 神谕下达时算好的路线，等行礼结束再交给她走。 */
  private pendingRoute: GridPoint[] = [];
  /** 这一趟要去哪栋楼。台账同步派活时同样走这条路。 */
  private pendingBuilding: PlacedBuilding | null = null;

  private now = 0;
  private nextTaskNumber = 1;

  constructor(deps: SimulationDeps) {
    this.grid = deps.grid;
    this.altar = deps.altar;
    this.villagerName = deps.villagerName;
    this.spawnBuilding = deps.spawnBuilding;
    this.buildings = [...deps.buildings];
    this.beats = routineBeats(deps.altar, deps.grid);
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
      nextRoutineAt: 1200,
      routineIndex: 0,
      routineLabel: null,
      inPlaceUntil: 0,
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
    this.weather = stepWeather(this.weather, deltaMs);
    this.stepConstructions(deltaMs);
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
          ? activity.determinate
            ? Math.min(1, elapsedMs / Math.max(1, activity.estimateMs))
            : null
          : activity.kind === 'cooling' || activity.kind === 'overheating'
            ? 1
            : 0;
      return { buildingId: building.instanceId, activity, elapsedMs, progress };
    });
    const constructions: ConstructionSnapshot[] = [...this.constructions.entries()].map(
      ([buildingId, state]) => ({
        buildingId,
        displayName:
          this.buildings.find((item) => item.instanceId === buildingId)?.displayName ??
          buildingId,
        remainingMs: state.remainingMs,
        totalMs: state.totalMs,
      }),
    );
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
        routine: this.villager.routineLabel,
        umbrella: this.weather.kind === 'rain' && this.villager.mode !== 'working',
      },
      buildings: this.buildings,
      workstations,
      constructions,
      tasks: this.tasks.map((task) => ({ ...task })),
      props: this.props,
      roads: this.roads,
      events: this.events.slice(-12),
      weather: this.weather.kind,
      faith: { points: this.faithPoints, milestones: this.milestones },
      /** 台账交付与 Mock 交付分开计，不合并成一个数。 */
      deliveries: { ledger: this.ledgerDeliveries, mock: this.mockDeliveries },
      ledger: {
        connected: this.ledgerConnected,
        lastSyncAt: this.ledgerSyncedAt,
        mock: !this.ledgerConnected,
        /** 最近一次快照是否完整；false 时状态行标"台账可能未取全"。 */
        complete: this.ledgerComplete,
      },
    };
  }

  /** 天气浓淡给渲染层插值雨幕用。 */
  weatherIntensity(): number {
    return this.weather.intensity;
  }

  // ---------------------------------------------------------------- commands

  private apply(command: WorldCommand): CommandResult {
    switch (command.kind) {
      case 'dispatch':
        return this.dispatch(command.taskKind, command.summary);
      case 'complete':
        return this.completeManually();
      case 'setWeather':
        return this.setWeather(command.weather);
      case 'build':
        return this.build(command.prefabId, command.instanceId);
      case 'sync':
        return this.sync(command.tasks, command.finished, command.connected,
          command.complete ?? true, command.order ?? []);
    }
  }

  /**
   * 台账快照落地。这是世界接真实工作的入口，也是唯一一条**替换**式命令：
   * 沙盒的活照旧由计时器收尾，台账的活只有台账说完了才算完。
   *
   * 正在跑的活如果被台账撤回（单被取消/归档），她手上那单要收掉，不能
   * 继续对着一个不存在的督办冒烟。
   */
  private sync(
    incoming: readonly {
      readonly commissionId: string;
      readonly kind: TaskKind;
      readonly title: string;
      readonly agentName: string;
      /** 台账状态；blocked 表示这单在等用户答复。旧调用方省略时按无 blocked 处理。 */
      readonly status?: string;
      /** blocked 单要请示的问题；只透传，世界不回答。省略/空串即没有。 */
      readonly blockedQuestion?: string;
    }[],
    finished: readonly string[],
    connected: boolean,
    complete: boolean,
    order: readonly string[],
  ): CommandResult {
    this.ledgerConnected = connected;
    if (!connected) {
      const message = '台账连不上了，世界照常过日子。';
      this.log('warn', message);
      return { ok: false, message };
    }
    this.ledgerSyncedAt = this.now;
    this.ledgerOrder = order;
    const wasComplete = this.ledgerComplete;
    this.ledgerComplete = complete;
    if (!complete && wasComplete) {
      this.log('warn', '台账快照可能未取全：暂不把不在快照里的督办当撤单。');
    }

    const finishedIds = new Set(finished);
    const next = new Set([...incoming.map((item) => item.commissionId), ...finished]);

    // blocked_question 只透传、只展示：只有仍处于 blocked 且问题非空的单才带问题。
    const blockedQuestions = new Map<string, string>();
    for (const item of incoming) {
      if (item.status !== 'blocked') continue;
      if ((item.blockedQuestion ?? '').trim() === '') continue;
      blockedQuestions.set(item.commissionId, item.blockedQuestion ?? '');
    }
    // 已有的台账单立刻对齐：转 running / 空问题 / 完成 / 撤单都清成 null，不残留。
    for (const task of this.tasks) {
      if (task.source !== 'ledger') continue;
      task.blockedQuestion = blockedQuestions.get(task.id) ?? null;
    }

    // 台账说收工了：她手上的这单走正常交付，不是被撤。
    // 快照不完整时，"不在快照里"不等于被撤回：只认明确的收工，其余保持现状。
    const active = this.activeTask();
    if (active !== null && active.source === 'ledger') {
      if (finishedIds.has(active.id)) {
        this.finishActiveTask();
      } else if (complete && !next.has(active.id)) {
        this.abandonActiveTask();
      }
    }

    // 已收工的条不再进活动清单；完整快照里已消失的直接删掉。
    for (const task of [...this.tasks]) {
      if (task.source !== 'ledger') continue;
      if (finishedIds.has(task.id) || (complete && !next.has(task.id))) {
        const index = this.tasks.indexOf(task);
        if (index >= 0) this.tasks.splice(index, 1);
        this.idleWorkstationFor(task.id);
      }
    }

    let added = 0;
    for (const item of incoming) {
      if (this.tasks.some((task) => task.id === item.commissionId)) continue;
      this.tasks.push({
        id: item.commissionId,
        kind: item.kind,
        summary: item.title,
        buildingId: null,
        source: 'ledger',
        status: 'assigned',
        createdAt: this.now,
        completedAt: null,
        blockedQuestion: blockedQuestions.get(item.commissionId) ?? null,
      });
      added += 1;
    }

    if (added > 0) this.log('info', `台账来了 ${added} 条新督办。`);
    this.assignFromLedger();
    let message = `台账已同步，${this.tasks.length} 条在册。`;
    const waiting = this.tasks.find((task) => task.blockedQuestion !== null);
    if (waiting !== undefined && waiting.blockedQuestion !== null) {
      message += `在等你的答复：${excerptBlockedQuestion(waiting.blockedQuestion)}（仅展示，世界不代为回答）`;
    }
    return { ok: true, message };
  }

  /** 把还挂着这条活的铺子熄火。 */
  private idleWorkstationFor(taskId: string): void {
    for (const building of this.buildings) {
      const activity = this.activities.get(building.instanceId);
      if (activity === undefined || activity.kind === 'idle') continue;
      if ('taskId' in activity && activity.taskId === taskId) {
        this.activities.set(building.instanceId, { kind: 'idle' });
      }
    }
  }

  /**
   * 台账有活、她闲着，就挑优先级最高的那条派下去。
   *
   * 这就是「操作台和世界连通」的那一步：你在屏幕外派单，她在屏幕里动起来。
   * 只在她闲着时派；已经在干活就不打断，因为打断是神谕的语义，不是同步器的。
   * 优先级来自台账：LedgerView.order（blocked→running→auditing→其它，同级稳定）；
   * 没带顺序（旧服务/旧快照）时保持插入序，与改造前一致。
   */
  private assignFromLedger(): void {
    if (!this.isFree()) return;
    const task = this.nextLedgerTask();
    if (task === undefined) return;
    const building = this.buildings.find((item) => item.boundTask === task.kind);
    if (building === undefined) {
      this.log('warn', `没有承接 ${task.kind} 的铺子，「${task.summary}」派不下去。`);
      return;
    }
    if (this.constructions.has(building.instanceId)) return;
    if (this.activities.get(building.instanceId)?.kind === 'overheating') return;
    const path = this.grid.path(this.cellOf(this.villager.pos), building.door);
    if (path.length === 0) return;
    task.buildingId = building.instanceId;
    this.routeToBuilding(task, building, path);
    this.log('oracle', `台账派活：${task.summary} → ${building.displayName}`);
  }

  /**
   * 按台账给的优先级挑下一单：不在顺序表里的排后面，同级保持插入序
   * （Array.prototype.sort 在 V8 里是稳定排序）。台账撤单后下一拍就用新顺序重排。
   */
  private nextLedgerTask(): TaskRecord | undefined {
    const candidates = this.tasks.filter(
      (item) => item.source === 'ledger' && item.status === 'assigned',
    );
    if (candidates.length === 0) return undefined;
    const rank = (task: TaskRecord): number => {
      const index = this.ledgerOrder.indexOf(task.id);
      return index < 0 ? Number.MAX_SAFE_INTEGER : index;
    };
    return [...candidates].sort((a, b) => rank(a) - rank(b))[0];
  }

  /** 她手上的活被上游撤回：状态机收回 idle，铺子熄火。 */
  private abandonActiveTask(): void {
    const task = this.activeTask();
    if (task === null) return;
    task.blockedQuestion = null;
    const building = this.buildingOf(task);
    if (building !== undefined) {
      this.activities.set(building.instanceId, { kind: 'idle' });
    }
    this.villager.mode = 'idle';
    this.villager.taskId = null;
    this.villager.visible = true;
    this.villager.path = [];
    this.villager.bubble = '这单被撤了。';
    this.villager.bubbleUntil = this.now + 1600;
    this.villager.nextRoutineAt = this.now + 1800;
    this.villager.routineLabel = null;
    this.log('warn', `${task.summary} 被上游撤回，她停手了。`);
  }

  private setWeather(weather: Weather): CommandResult {
    if (weather === this.weather.kind && this.weather.remainingMs !== null) {
      return { ok: true, message: '天已经在下雨了。' };
    }
    if (weather === 'rain') {
      this.weather = rainWeather(this.now);
      this.log('info', '神谕说：下雨。天暗下来了。');
      this.scatterToShelter();
      return { ok: true, message: '雨落下来了。' };
    }
    this.weather = clearWeather();
    this.log('info', '神谕说：放晴。雨停了。');
    return { ok: true, message: '天放晴了。' };
  }

  /** 下雨的那一刻，户外的她立刻改主意，不等到下一个作息拍。 */
  private scatterToShelter(): void {
    if (this.villager.mode !== 'idle') return;
    const cell = this.cellOf(this.villager.pos);
    const door = shelterTarget(this.buildings, cell);
    if (door === null) return;
    const path = this.grid.path(cell, door);
    if (path.length === 0) return;
    this.villager.mode = 'sheltering';
    this.villager.path = path;
    this.villager.bubble = '下雨了，快躲！';
    this.villager.bubbleUntil = this.now + 2200;
    this.villager.routineLabel = '跑到屋檐下躲雨';
  }

  private dispatch(taskKind: TaskKind, summary: string): CommandResult {
    if (!this.isFree()) {
      const message = '她手上有活，神谕排在后面。';
      this.log('warn', message);
      return { ok: false, message };
    }
    const profile = TASK_PROFILES[taskKind];
    if (profile.venue === 'in_place') {
      const task = this.openTask(taskKind, summary, null);
      this.villager.mode = 'working';
      this.villager.taskId = task.id;
      this.villager.visible = true;
      this.villager.bubble = '这个我顺手就办了。';
      this.villager.bubbleUntil = this.now + 1400;
      this.villager.routineLabel = profile.label;
      this.villager.inPlaceUntil = this.now + profile.estimateMs;
      this.villager.path = [];
      task.status = 'in_progress';
      this.log('oracle', `神谕：${summary}（原地办）`);
      return { ok: true, message: '这点小事她当场就办。' };
    }

    const building = this.buildings.find((item) => item.boundTask === taskKind);
    if (building === undefined) {
      const message = `没有承接 ${taskKind} 的铺子。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    if (this.constructions.has(building.instanceId)) {
      const message = `${building.displayName} 还在施工，接不了活。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    if (this.activities.get(building.instanceId)?.kind === 'overheating') {
      const message = `${building.displayName} 还没修好。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const path = this.grid.path(this.cellOf(this.villager.pos), building.door);
    if (path.length === 0) {
      const message = `${building.displayName} 门口走不过去。`;
      this.log('warn', message);
      return { ok: false, message };
    }

    const task = this.openTask(taskKind, summary, building.instanceId);
    this.routeToBuilding(task, building, path);

    this.log('oracle', `神谕：${summary} → ${building.displayName}`);
    return { ok: true, message: `神谕已送达 ${building.displayName}。` };
  }

  /** 让她停下手上的事、行礼、走进这栋铺子。派活与台账同步共用这一条路。 */
  private routeToBuilding(
    task: TaskRecord,
    building: PlacedBuilding,
    path: readonly GridPoint[],
  ): void {
    this.villager.mode = 'saluting';
    this.villager.path = [];
    this.villager.taskId = task.id;
    this.villager.bubble = '！吾神降旨！';
    this.villager.bubbleUntil = this.now + 1500;
    this.villager.saluteUntil = this.now + SALUTE_MS;
    this.villager.facing = 'down';
    this.villager.routineLabel = null;
    this.pendingRoute = [...path];
    this.pendingBuilding = building;
  }

  private openTask(kind: TaskKind, summary: string, buildingId: string | null): TaskRecord {
    const task: TaskRecord = {
      id: `t${this.nextTaskNumber}`,
      kind,
      summary,
      buildingId,
      source: 'sandbox',
      status: 'assigned',
      createdAt: this.now,
      completedAt: null,
      blockedQuestion: null,
    };
    this.nextTaskNumber += 1;
    this.tasks.push(task);
    return task;
  }

  private completeManually(): CommandResult {
    if (this.villager.mode !== 'working') {
      const message = '她现在没有在干活，无需交付。';
      this.log('warn', message);
      return { ok: false, message };
    }
    const task = this.activeTask();
    if (task === null) {
      const message = '她现在没有在干活，无需交付。';
      this.log('warn', message);
      return { ok: false, message };
    }
    // 完成权唯一来源是台账：台账单必须由台账收尾，人工入口一律拒绝，也不写台账。
    if (task.source === 'ledger') {
      const message = '这单的完成权在台账，人工不能收尾。';
      this.log('warn', message);
      return { ok: false, message };
    }
    const label = this.ledgerConnected ? '沙盒 Mock' : '离线 Mock';
    if (task.buildingId === null) {
      this.finishInPlace(task);
      return { ok: true, message: `这单办了（${label}，不计入台账）。` };
    }
    this.finishActiveTask();
    return { ok: true, message: `成果已交付（${label}，不计入台账）。` };
  }

  private build(prefabId: string, instanceId: string): CommandResult {
    if (this.spawnBuilding === undefined) {
      return { ok: false, message: '这个世界没有造物主。' };
    }
    if (this.buildings.some((item) => item.prefabId === prefabId)) {
      return { ok: false, message: '这种铺子已经有一栋了。' };
    }
    const spawned = this.spawnBuilding(prefabId, instanceId);
    if (spawned === null) {
      return { ok: false, message: '分区里已经没有合规空地。' };
    }
    this.buildings.push(spawned.building);
    this.activities.set(spawned.building.instanceId, { kind: 'idle' });
    this.constructions.set(spawned.building.instanceId, {
      remainingMs: CONSTRUCTION_MS,
      totalMs: CONSTRUCTION_MS,
    });
    for (const cell of spawned.road) {
      if (!this.roads.some((p) => p.x === cell.x && p.y === cell.y)) this.roads.push(cell);
    }
    this.log('info', `造物主动工：${spawned.building.displayName}。`);
    return { ok: true, message: `${spawned.building.displayName} 开工建造。` };
  }

  // ---------------------------------------------------------------- villager

  private isFree(): boolean {
    return this.villager.mode === 'idle' || this.villager.mode === 'sheltering';
  }

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
        this.stepWorking();
        return;
      case 'delivering':
        if (this.now >= this.villager.deliverUntil) {
          this.villager.mode = 'toAltar';
          this.villager.path = this.grid.path(this.cellOf(this.villager.pos), this.altar);
          this.villager.bubble = null;
          if (this.villager.path.length === 0) this.arriveAtAltar();
        }
        return;
      case 'toAltar':
        this.walk(deltaMs);
        if (this.villager.path.length === 0) this.arriveAtAltar();
        return;
      case 'sheltering':
        this.walk(deltaMs);
        if (this.villager.path.length === 0) {
          this.villager.mode = 'idle';
          this.villager.nextRoutineAt = this.now + 2500;
          this.villager.routineLabel =
            this.weather.kind === 'rain' ? '在屋檐下看雨' : '雨停了，抖了抖身上的水';
        }
        return;
      case 'idle':
        this.stepIdle(deltaMs);
        return;
    }
  }

  /** 原地办的短活：不占铺子，时间一到自己结束。 */
  private stepWorking(): void {
    const task = this.activeTask();
    if (task === null || task.buildingId !== null) return;
    if (this.now < this.villager.inPlaceUntil) return;
    this.finishInPlace(task);
  }

  private finishInPlace(task: TaskRecord): void {
    if (task.status === 'done') return;
    task.status = 'done';
    task.completedAt = this.now;
    task.blockedQuestion = null;
    this.recordDelivery(task.source);
    this.villager.mode = 'idle';
    this.villager.taskId = null;
    this.villager.bubble = '办好了。';
    this.villager.bubbleUntil = this.now + QUICK_DONE_MS;
    this.villager.nextRoutineAt = this.now + 1800;
    this.villager.routineLabel = null;
    this.accrueFaith();
    this.log('done', `${task.summary} 办完了。`);
  }

  private stepIdle(deltaMs: number): void {
    if (this.villager.path.length > 0) {
      this.walk(deltaMs);
      return;
    }
    if (this.now < this.villager.nextRoutineAt) return;

    // 已经站在这一拍的落点上就跳过它，否则开局会在祭坛上干坐一轮。
    const here = this.cellOf(this.villager.pos);
    for (let attempt = 0; attempt < this.beats.length; attempt += 1) {
      const beat = routineFor(this.weather.kind, this.beats, this.villager.routineIndex);
      this.villager.routineIndex += 1;
      const target = this.resolvedTarget(beat);
      if (target === null) continue;
      if (target.x === here.x && target.y === here.y) continue;
      this.villager.routineLabel = beat.label;
      this.villager.nextRoutineAt = this.now + beat.dwellMs;
      this.villager.path = this.grid.path(here, target);
      if (this.villager.path.length > 0) return;
    }
    this.villager.nextRoutineAt = this.now + 1600;
  }

  private resolvedTarget(beat: RoutineBeat): GridPoint | null {
    if (beat.id === 'shelter') {
      return shelterTarget(this.buildings, this.cellOf(this.villager.pos));
    }
    return this.grid.isWalkable(beat.target) ? beat.target : null;
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
    // 台账的活没有 buildingId（上游不知道我们的铺子），用派活时记下的那栋。
    const building = this.buildingOf(task) ?? this.pendingBuilding ?? undefined;
    this.pendingBuilding = null;
    if (building === undefined) {
      // 楼没了（被拆/被撤），这单退回空闲，不让她对着空气干活。
      this.villager.mode = 'idle';
      this.villager.taskId = null;
      return;
    }
    task.status = 'in_progress';
    this.villager.mode = 'working';
    this.villager.visible = false;
    this.villager.bubble = null;
    this.villager.routineLabel = null;
    this.villager.pos = { x: building.workSpot.x, y: building.workSpot.y };
    this.activities.set(building.instanceId, {
      kind: 'operating',
      taskId: task.id,
      startedAt: this.now,
      estimateMs: TASK_PROFILES[task.kind].estimateMs,
      // 台账的活只有台账知道还要多久；沙盒的活由计时器收尾。
      determinate: task.source === 'sandbox',
    });
    this.log('work', `${building.displayName} 开工。`);
  }

  private arriveAtAltar(): void {
    this.villager.mode = 'idle';
    this.villager.taskId = null;
    this.villager.routineLabel = null;
    this.villager.nextRoutineAt = this.now + 1600;
  }

  private stepWorkstations(): void {
    for (const building of this.buildings) {
      const activity = this.activities.get(building.instanceId);
      if (activity === undefined) continue;
      if (
        activity.kind === 'operating' &&
        activity.determinate &&
        this.now - activity.startedAt >= activity.estimateMs
      ) {
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
    if (task === null || task.status === 'done') return;
    task.status = 'done';
    task.completedAt = this.now;
    task.blockedQuestion = null;
    this.recordDelivery(task.source);
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
    this.accrueFaith();
    this.log('done', `${task.summary} 已交付。`);
  }

  /** 交付按来源分开记账：台账的活与沙盒/离线 Mock 的活不合并。 */
  private recordDelivery(source: TaskRecord['source']): void {
    if (source === 'ledger') this.ledgerDeliveries += 1;
    else this.mockDeliveries += 1;
  }

  /** 攒够一个里程碑，世界多一件东西；不够就只是点数。 */
  private accrueFaith(): void {
    this.faithPoints += POINTS_PER_TASK;
    if (this.faithPoints < FAITH_MILESTONE) return;
    this.faithPoints -= FAITH_MILESTONE;
    const prop = nextProp(this.milestones, this.altar, (p) => this.grid.isWalkable(p));
    if (prop === null) return;
    this.props.push(prop);
    this.milestones += 1;
    const name = milestoneFor(this.milestones - 1).label;
    this.log('info', `神恩落地：祭坛边多了${name}。`);
  }

  private stepConstructions(deltaMs: number): void {
    for (const [id, state] of [...this.constructions.entries()]) {
      const remaining = state.remainingMs - deltaMs;
      if (remaining > 0) {
        this.constructions.set(id, { ...state, remainingMs: remaining });
        continue;
      }
      this.constructions.delete(id);
      const building = this.buildings.find((item) => item.instanceId === id);
      this.log('info', `${building?.displayName ?? id} 落成了。`);
    }
  }

  private activeTask(): TaskRecord | null {
    if (this.villager.taskId === null) return null;
    return this.tasks.find((item) => item.id === this.villager.taskId) ?? null;
  }

  private buildingOf(task: TaskRecord): PlacedBuilding | undefined {
    if (task.buildingId === null) return undefined;
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
