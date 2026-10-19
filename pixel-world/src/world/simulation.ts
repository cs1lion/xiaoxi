/** 世界运行时。固定步长由调用方给，内部不读时钟，因此可以离线回放。
 *
 * 外部只能从一个口子进来：enqueue()。命令在下次 update() 开头统一结算，
 * 渲染循环里不会出现半更新的中间态。
 *
 * 角色是数组：每个小人各自有状态机、路线、任务与等待队列；命令一次只叫一个人。
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
  PendingDispatch,
  PlacedBuilding,
  TaskRecord,
  TaskKind,
  Vec2,
  VillagerMode,
  VillagerSnapshot,
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
  | { readonly kind: 'complete'; readonly target?: string }
  | { readonly kind: 'cancelPending'; readonly id?: string }
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
  /**
   * 角色名。至少 1 个；省略时是「主角色 + 助手」两人。
   * 传 3 个名字即可扩到 3 人，逻辑不需要再改。
   */
  readonly villagerNames?: readonly string[];
  /** 造物主：把一栋新楼落进世界。返回 null 表示空间不够。 */
  readonly spawnBuilding?: (prefabId: string, instanceId: string) => NewBuilding | null;
}

/** 一个角色的完整本地状态。渲染层只读快照，不碰这里。 */
interface Villager {
  readonly id: string;
  readonly name: string;
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
  /** 神谕下达时算好的路线，等行礼结束再交给她走。 */
  pendingRoute: GridPoint[];
  /** 这一趟要去哪栋楼。台账同步派活时同样走这条路。 */
  pendingBuilding: PlacedBuilding | null;
}

interface WorkstationState {
  readonly remainingMs: number;
  readonly totalMs: number;
}

const SPEED_CELLS_PER_SECOND = 3;
const SALUTE_MS = 700;
const DELIVER_MS = 1400;
const COOLING_MS = 900;
const EVENT_LIMIT = 60;
const CONSTRUCTION_MS = 3200;
const QUICK_DONE_MS = 900;
const DEFAULT_ASSISTANT_NAME = '阿雾';
/** 每个角色自己的等待队列上限。两位都忙且各自满了才拒单。 */
export const MAX_PENDING_DISPATCHES = 3;

/** 交付攒到里程碑时，世界多一件东西。 */
const FAITH_MILESTONE = MILESTONE_POINTS;

export class Simulation {
  private readonly grid: WorldGrid;
  private readonly altar: GridPoint;
  private readonly spawnBuilding: ((prefabId: string, instanceId: string) => NewBuilding | null) | undefined;

  private readonly buildings: PlacedBuilding[];
  private readonly villagers: Villager[];
  private readonly tasks: TaskRecord[] = [];
  private readonly activities = new Map<string, WorkstationActivity>();
  private readonly events: WorldEvent[] = [];
  private readonly queue: WorldCommand[] = [];
  private readonly lastResults: CommandResult[] = [];
  private readonly props: WorldProp[] = [];
  private readonly roads: GridPoint[] = [];
  private readonly constructions = new Map<string, WorkstationState>();

  private readonly beats: readonly RoutineBeat[];
  private weather: WeatherState = clearWeather();
  private faithPoints = 0;
  private milestones = 0;
  /** 交付统计按来源分开：台账的活只有台账能收，沙盒/离线 Mock 另计。 */
  private ledgerDeliveries = 0;
  private mockDeliveries = 0;
  /** 每个角色各自的交付归属（台账/Mock 分开），日报按人分账用。 */
  private readonly deliveryTally = new Map<string, { ledger: number; mock: number }>();
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

  /** 每个角色自己的等待队列（FIFO）。只存在 Simulation 内，随快照暴露。 */
  private readonly pending = new Map<string, PendingDispatch[]>();
  private nextPendingNumber = 1;

  private now = 0;
  private nextTaskNumber = 1;

  constructor(deps: SimulationDeps) {
    this.grid = deps.grid;
    this.altar = deps.altar;
    this.spawnBuilding = deps.spawnBuilding;
    this.buildings = [...deps.buildings];
    this.beats = routineBeats(deps.altar, deps.grid);
    const names = deps.villagerNames ?? [deps.villagerName, DEFAULT_ASSISTANT_NAME];
    this.villagers = names.map((name, index) => ({
      id: `v${index}`,
      name,
      mode: 'idle' as const,
      pos: { x: deps.altar.x, y: deps.altar.y },
      facing: 'down' as const,
      visible: true,
      path: [],
      taskId: null,
      bubble: null,
      bubbleUntil: 0,
      saluteUntil: 0,
      deliverUntil: 0,
      // 错开作息起点，两个人不会像复制粘贴一样同进同出。
      nextRoutineAt: 1200 + index * 1400,
      routineIndex: index * 2,
      routineLabel: null,
      inPlaceUntil: 0,
      pendingRoute: [],
      pendingBuilding: null,
    }));
    for (const villager of this.villagers) this.pending.set(villager.id, []);
    for (const villager of this.villagers) {
      this.deliveryTally.set(villager.id, { ledger: 0, mock: 0 });
    }
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
    for (const villager of this.villagers) this.stepVillager(villager, deltaMs);
    this.stepWorkstations();
    for (const villager of this.villagers) this.expireBubble(villager);
    // 固定步长的最后一拍：每个空闲角色先接台账的活，再轮到自己队列里的神谕。
    this.pumpPending();
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
    const villagers = this.villagers.map((villager) => this.snapshotVillager(villager));
    return {
      now: this.now,
      altar: this.altar,
      /** 兼容旧调用方：villager 等于 villagers[0]（主角色）。 */
      villager: villagers[0] as VillagerSnapshot,
      villagers,
      buildings: this.buildings,
      workstations,
      constructions,
      tasks: this.tasks.map((task) => ({ ...task })),
      pending: this.allPending(),
      props: this.props,
      roads: this.roads,
      events: this.events.slice(-12),
      weather: this.weather.kind,
      faith: { points: this.faithPoints, milestones: this.milestones },
      /** 台账交付与 Mock 交付分开计，不合并成一个数。 */
      deliveries: { ledger: this.ledgerDeliveries, mock: this.mockDeliveries },
      /** 按角色分账：日报与状态行据此写清是谁交付的。 */
      deliveriesByVillager: this.villagers.map((villager) => {
        const tally = this.deliveryTally.get(villager.id) ?? { ledger: 0, mock: 0 };
        return {
          villagerId: villager.id,
          name: villager.name,
          ledger: tally.ledger,
          mock: tally.mock,
        };
      }),
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
        return this.completeManually(command.target);
      case 'cancelPending':
        return this.cancelPending(command.id);
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
   */
  private sync(
    incoming: readonly {
      readonly commissionId: string;
      readonly kind: TaskKind;
      readonly title: string;
      readonly agentName: string;
      readonly status?: string;
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

    // 台账说收工了：由实际接下这单的角色走正常交付；被撤则停手。
    for (const villager of this.villagers) {
      const active = this.activeTaskOf(villager);
      if (active === null || active.source !== 'ledger') continue;
      if (finishedIds.has(active.id)) {
        this.finishActiveTask(villager);
      } else if (complete && !next.has(active.id)) {
        this.abandonActiveTask(villager);
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
        workerId: null,
      });
      added += 1;
    }

    if (added > 0) this.log('info', `台账来了 ${added} 条新督办。`);
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
   * 给一个空闲角色派台账的活：按台账 order 挑第一条接得下的。
   * 一个角色一次只接一单；铺子被占/施工/走不通就跳过，等下一拍。
   */
  private assignFromLedgerTo(villager: Villager): void {
    if (!this.isFree(villager)) return;
    for (const task of this.nextLedgerTasks()) {
      const building = this.buildings.find((item) => item.boundTask === task.kind);
      if (building === undefined) {
        this.log('warn', `没有承接 ${task.kind} 的铺子，「${task.summary}」派不下去。`);
        continue;
      }
      if (this.constructions.has(building.instanceId)) continue;
      if (this.activities.get(building.instanceId)?.kind === 'overheating') continue;
      if (this.buildingBusy(building)) continue;
      const path = this.grid.path(this.cellOf(villager.pos), building.door);
      if (path.length === 0) continue;
      task.buildingId = building.instanceId;
      task.workerId = villager.id;
      this.routeToBuilding(villager, task, building, path);
      this.log('oracle', `台账派活：${task.summary} → ${building.displayName}（${villager.name}）`);
      return;
    }
  }

  /** 按台账给的优先级排好的、还没人接的台账单。 */
  private nextLedgerTasks(): readonly TaskRecord[] {
    const candidates = this.tasks.filter(
      (item) =>
        item.source === 'ledger' && item.status === 'assigned' && item.workerId === null,
    );
    if (candidates.length === 0) return [];
    const rank = (task: TaskRecord): number => {
      const index = this.ledgerOrder.indexOf(task.id);
      return index < 0 ? Number.MAX_SAFE_INTEGER : index;
    };
    return [...candidates].sort((a, b) => rank(a) - rank(b));
  }

  /** 这栋铺子是否已经被一单活着的工作占着（含正在赶路的那一单）。 */
  private buildingBusy(building: PlacedBuilding): boolean {
    const activity = this.activities.get(building.instanceId);
    if (activity !== undefined && activity.kind !== 'idle') return true;
    return this.tasks.some(
      (task) =>
        task.buildingId === building.instanceId &&
        task.status !== 'done' &&
        task.status !== 'failed',
    );
  }

  /** 她手上的活被上游撤回：状态机收回 idle，铺子熄火。 */
  private abandonActiveTask(villager: Villager): void {
    const task = this.activeTaskOf(villager);
    if (task === null) return;
    this.clearDeparture(villager);
    task.blockedQuestion = null;
    const building = this.buildingOf(task);
    if (building !== undefined) {
      this.activities.set(building.instanceId, { kind: 'idle' });
    }
    villager.mode = 'idle';
    villager.taskId = null;
    villager.visible = true;
    villager.path = [];
    villager.bubble = '这单被撤了。';
    villager.bubbleUntil = this.now + 1600;
    villager.nextRoutineAt = this.now + 1800;
    villager.routineLabel = null;
    this.log('warn', `${task.summary} 被上游撤回，${villager.name}停手了。`);
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

  /** 下雨的那一刻，户外还闲着的角色立刻改主意，不等到下一个作息拍。 */
  private scatterToShelter(): void {
    for (const villager of this.villagers) {
      if (villager.mode !== 'idle') continue;
      const cell = this.cellOf(villager.pos);
      const door = shelterTarget(this.buildings, cell);
      if (door === null) continue;
      const path = this.grid.path(cell, door);
      if (path.length === 0) continue;
      villager.mode = 'sheltering';
      villager.path = path;
      villager.bubble = '下雨了，快躲！';
      villager.bubbleUntil = this.now + 2200;
      villager.routineLabel = '跑到屋檐下躲雨';
    }
  }

  private dispatch(taskKind: TaskKind, summary: string): CommandResult {
    // 有人空着就叫一个人；全都在忙才进等待队列。
    const free = this.freeVillager();
    if (free === null) return this.enqueuePendingDispatch(taskKind, summary);
    return this.startDispatch(free, taskKind, summary);
  }

  /** 真正派活给某个角色。调用方保证这个角色空闲。 */
  private startDispatch(villager: Villager, taskKind: TaskKind, summary: string): CommandResult {
    const profile = TASK_PROFILES[taskKind];
    if (profile.venue === 'in_place') {
      const task = this.openTask(taskKind, summary, null, villager.id);
      villager.mode = 'working';
      villager.taskId = task.id;
      villager.visible = true;
      villager.bubble = '这个我顺手就办了。';
      villager.bubbleUntil = this.now + 1400;
      villager.routineLabel = profile.label;
      villager.inPlaceUntil = this.now + profile.estimateMs;
      villager.path = [];
      task.status = 'in_progress';
      this.log('oracle', `神谕：${summary}（${villager.name}原地办）`);
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
    if (this.buildingBusy(building)) {
      const message = `${building.displayName} 正忙，这单得等它空出来。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const path = this.grid.path(this.cellOf(villager.pos), building.door);
    if (path.length === 0) {
      const message = `${building.displayName} 门口走不过去。`;
      this.log('warn', message);
      return { ok: false, message };
    }

    const task = this.openTask(taskKind, summary, building.instanceId, villager.id);
    this.routeToBuilding(villager, task, building, path);

    this.log('oracle', `神谕：${summary} → ${building.displayName}（${villager.name}）`);
    return { ok: true, message: `神谕已送达 ${building.displayName}。` };
  }

  /** 所有人都忙时把神谕放进等待队列；按角色分，选排队最少的那位。 */
  private enqueuePendingDispatch(taskKind: TaskKind, summary: string): CommandResult {
    let target = this.villagers[0] as Villager;
    for (const villager of this.villagers) {
      if (this.pendingOf(villager).length < this.pendingOf(target).length) target = villager;
    }
    const queue = this.pendingOf(target);
    if (queue.length >= MAX_PENDING_DISPATCHES) {
      const message = `她手上有活，队列也满了（每人最多 ${MAX_PENDING_DISPATCHES} 条）；这条先不接。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    queue.push({
      id: `p${this.nextPendingNumber}`,
      kind: taskKind,
      summary,
      requestedAt: this.now,
      source: 'oracle',
      villagerId: target.id,
    });
    this.nextPendingNumber += 1;
    const position = queue.length;
    this.log('info', `神谕排队：${summary}（${target.name}，第 ${position} 位）`);
    return { ok: true, message: `她手上有活，神谕已排队（第 ${position} 位）。` };
  }

  /**
   * 每个空闲角色各自推进：先接台账的活（真实活优先），再按 FIFO 开自己的排队单。
   * 固定步长末尾调用一次；只依赖 this.now 与命令顺序，回放可复现。
   */
  private pumpPending(): void {
    for (const villager of this.villagers) {
      if (!this.isFree(villager)) continue;
      this.assignFromLedgerTo(villager);
      if (!this.isFree(villager)) continue;
      const queue = this.pendingOf(villager);
      const next = queue[0];
      if (next === undefined) continue;
      // 场地原因暂时派不下去就留在队首，下一拍再试，不丢单。
      if (!this.startDispatch(villager, next.kind, next.summary).ok) continue;
      queue.shift();
      this.log('oracle', `排队的神谕开工：${next.summary}（${villager.name}）`);
    }
  }

  /** 撤销排队中的神谕；省略 id 撤最后一条。只动等待队列，不动正在干的活。 */
  private cancelPending(id?: string): CommandResult {
    const queues = this.villagers.flatMap((villager) => this.pendingOf(villager));
    if (queues.length === 0) {
      const message = '没有排队的单可撤销。';
      this.log('warn', message);
      return { ok: false, message };
    }
    if (id === undefined) {
      // 撤全局最后一条：从后往前找第一个非空队列。
      for (let index = this.villagers.length - 1; index >= 0; index -= 1) {
        const villager = this.villagers[index] as Villager;
        const queue = this.pendingOf(villager);
        const removed = queue.pop();
        if (removed !== undefined) return this.finishCancel(removed);
      }
      const message = '没有排队的单可撤销。';
      this.log('warn', message);
      return { ok: false, message };
    }
    const target = queues.find((item) => item.id === id);
    if (target === undefined) {
      const message = `队列里没有 ${id}。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const villager = this.villagers.find((item) => item.id === target.villagerId);
    if (villager === undefined) {
      const message = `队列里没有 ${id}。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const queue = this.pendingOf(villager);
    const index = queue.findIndex((item) => item.id === id);
    const removed = queue.splice(index, 1)[0];
    if (removed === undefined) {
      const message = `队列里没有 ${id}。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    return this.finishCancel(removed);
  }

  private finishCancel(removed: PendingDispatch): CommandResult {
    const remaining = this.villagers.reduce(
      (sum, villager) => sum + this.pendingOf(villager).length,
      0,
    );
    this.log('info', `撤销排队：${removed.summary}（剩 ${remaining} 条）`);
    return { ok: true, message: `已撤销排队单「${removed.summary}」，队列还剩 ${remaining} 条。` };
  }

  /** 任务在出发前就结束了：取消行礼计时器、清掉还没交出去的路线与目标楼。 */
  private clearDeparture(villager: Villager): void {
    villager.saluteUntil = 0;
    villager.pendingRoute = [];
    villager.pendingBuilding = null;
  }

  /** 让她停下手上的事、行礼、走进这栋铺子。派活与台账同步共用这一条路。 */
  private routeToBuilding(
    villager: Villager,
    task: TaskRecord,
    building: PlacedBuilding,
    path: readonly GridPoint[],
  ): void {
    villager.mode = 'saluting';
    villager.path = [];
    villager.taskId = task.id;
    villager.bubble = '！吾神降旨！';
    villager.bubbleUntil = this.now + 1500;
    villager.saluteUntil = this.now + SALUTE_MS;
    villager.facing = 'down';
    villager.routineLabel = null;
    villager.pendingRoute = [...path];
    villager.pendingBuilding = building;
  }

  private openTask(
    kind: TaskKind,
    summary: string,
    buildingId: string | null,
    workerId: string,
  ): TaskRecord {
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
      workerId,
    };
    this.nextTaskNumber += 1;
    this.tasks.push(task);
    return task;
  }

  private completeManually(target?: string): CommandResult {
    let villager: Villager | undefined;
    if (target === undefined) {
      // 省略目标：保持旧行为，收第一个在干活的人的单。
      villager = this.villagers.find(
        (item) => item.mode === 'working' && this.activeTaskOf(item) !== null,
      );
      if (villager === undefined) {
        const message = '她现在没有在干活，无需交付。';
        this.log('warn', message);
        return { ok: false, message };
      }
    } else {
      // 指定目标：按 id 或名字精确匹配，未知目标明确拒绝。
      villager = this.villagers.find((item) => item.id === target || item.name === target);
      if (villager === undefined) {
        const message = `没有叫「${target}」的角色，世界没有动。`;
        this.log('warn', message);
        return { ok: false, message };
      }
      if (villager.mode !== 'working' || this.activeTaskOf(villager) === null) {
        const message = `${villager.name}现在没有在干活，无需交付。`;
        this.log('warn', message);
        return { ok: false, message };
      }
    }
    const task = this.activeTaskOf(villager);
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
      this.finishInPlace(villager, task);
      return { ok: true, message: `这单办了（${villager.name}，${label}，不计入台账）。` };
    }
    this.finishActiveTask(villager);
    return { ok: true, message: `成果已交付（${villager.name}，${label}，不计入台账）。` };
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

  // ---------------------------------------------------------------- villagers

  private pendingOf(villager: Villager): PendingDispatch[] {
    return this.pending.get(villager.id) ?? [];
  }

  private allPending(): PendingDispatch[] {
    return this.villagers.flatMap((villager) =>
      this.pendingOf(villager).map((item) => ({ ...item })),
    );
  }

  private isFree(villager: Villager): boolean {
    return villager.mode === 'idle' || villager.mode === 'sheltering';
  }

  private freeVillager(): Villager | null {
    return this.villagers.find((villager) => this.isFree(villager)) ?? null;
  }

  private snapshotVillager(villager: Villager): VillagerSnapshot {
    return {
      id: villager.id,
      name: villager.name,
      mode: villager.mode,
      pos: villager.pos,
      facing: villager.facing,
      visible: villager.visible,
      taskId: villager.taskId,
      bubble: villager.bubble,
      routine: villager.routineLabel,
      umbrella: this.weather.kind === 'rain' && villager.mode !== 'working',
    };
  }

  private stepVillager(villager: Villager, deltaMs: number): void {
    switch (villager.mode) {
      case 'saluting':
        if (this.now >= villager.saluteUntil) {
          villager.mode = 'toWork';
          villager.path = villager.pendingRoute;
          villager.pendingRoute = [];
          if (villager.path.length === 0) this.arriveAtWork(villager);
        }
        return;
      case 'toWork':
        this.walk(villager, deltaMs);
        if (villager.path.length === 0) this.arriveAtWork(villager);
        return;
      case 'working':
        this.stepWorking(villager);
        return;
      case 'delivering':
        if (this.now >= villager.deliverUntil) {
          villager.mode = 'toAltar';
          villager.path = this.grid.path(this.cellOf(villager.pos), this.altar);
          villager.bubble = null;
          if (villager.path.length === 0) this.arriveAtAltar(villager);
        }
        return;
      case 'toAltar':
        this.walk(villager, deltaMs);
        if (villager.path.length === 0) this.arriveAtAltar(villager);
        return;
      case 'sheltering':
        this.walk(villager, deltaMs);
        if (villager.path.length === 0) {
          villager.mode = 'idle';
          villager.nextRoutineAt = this.now + 2500;
          villager.routineLabel =
            this.weather.kind === 'rain' ? '在屋檐下看雨' : '雨停了，抖了抖身上的水';
        }
        return;
      case 'idle':
        this.stepIdle(villager, deltaMs);
        return;
    }
  }

  /** 原地办的短活：不占铺子，时间一到自己结束。 */
  private stepWorking(villager: Villager): void {
    const task = this.activeTaskOf(villager);
    if (task === null || task.buildingId !== null) return;
    if (this.now < villager.inPlaceUntil) return;
    this.finishInPlace(villager, task);
  }

  private finishInPlace(villager: Villager, task: TaskRecord): void {
    if (task.status === 'done') return;
    this.clearDeparture(villager);
    task.status = 'done';
    task.completedAt = this.now;
    task.blockedQuestion = null;
    this.recordDelivery(villager.id, task.source);
    villager.mode = 'idle';
    villager.taskId = null;
    villager.bubble = '办好了。';
    villager.bubbleUntil = this.now + QUICK_DONE_MS;
    villager.nextRoutineAt = this.now + 1800;
    villager.routineLabel = null;
    this.accrueFaith();
    this.log('done', `${task.summary} 办完了（${villager.name}）。`);
  }

  private stepIdle(villager: Villager, deltaMs: number): void {
    if (villager.path.length > 0) {
      this.walk(villager, deltaMs);
      return;
    }
    if (this.now < villager.nextRoutineAt) return;

    // 已经站在这一拍的落点上就跳过它，否则开局会在祭坛上干坐一轮。
    const here = this.cellOf(villager.pos);
    for (let attempt = 0; attempt < this.beats.length; attempt += 1) {
      const beat = routineFor(this.weather.kind, this.beats, villager.routineIndex);
      villager.routineIndex += 1;
      const target = this.resolvedTarget(beat, villager);
      if (target === null) continue;
      if (target.x === here.x && target.y === here.y) continue;
      villager.routineLabel = beat.label;
      villager.nextRoutineAt = this.now + beat.dwellMs;
      villager.path = this.grid.path(here, target);
      if (villager.path.length > 0) return;
    }
    villager.nextRoutineAt = this.now + 1600;
  }

  private resolvedTarget(beat: RoutineBeat, villager: Villager): GridPoint | null {
    if (beat.id === 'shelter') {
      return shelterTarget(this.buildings, this.cellOf(villager.pos));
    }
    return this.grid.isWalkable(beat.target) ? beat.target : null;
  }

  private walk(villager: Villager, deltaMs: number): void {
    let budget = (SPEED_CELLS_PER_SECOND * deltaMs) / 1000;
    while (budget > 0 && villager.path.length > 0) {
      const next = villager.path[0] as GridPoint;
      const dx = next.x - villager.pos.x;
      const dy = next.y - villager.pos.y;
      const distance = Math.hypot(dx, dy);
      if (distance <= budget) {
        villager.pos = { x: next.x, y: next.y };
        budget -= distance;
        villager.path.shift();
        continue;
      }
      const scale = budget / distance;
      villager.pos = {
        x: villager.pos.x + dx * scale,
        y: villager.pos.y + dy * scale,
      };
      villager.facing = facingFor(dx, dy);
      budget = 0;
    }
  }

  private arriveAtWork(villager: Villager): void {
    const task = this.activeTaskOf(villager);
    if (task === null) {
      villager.mode = 'idle';
      return;
    }
    // 台账的活没有 buildingId（上游不知道我们的铺子），用派活时记下的那栋。
    const building = this.buildingOf(task) ?? villager.pendingBuilding ?? undefined;
    villager.pendingBuilding = null;
    if (building === undefined) {
      // 楼没了（被拆/被撤），这单退回空闲，不让她对着空气干活。
      villager.mode = 'idle';
      villager.taskId = null;
      return;
    }
    task.status = 'in_progress';
    villager.mode = 'working';
    villager.visible = false;
    villager.bubble = null;
    villager.routineLabel = null;
    villager.pos = { x: building.workSpot.x, y: building.workSpot.y };
    this.activities.set(building.instanceId, {
      kind: 'operating',
      taskId: task.id,
      startedAt: this.now,
      estimateMs: TASK_PROFILES[task.kind].estimateMs,
      // 台账的活只有台账知道还要多久；沙盒的活由计时器收尾。
      determinate: task.source === 'sandbox',
    });
    this.log('work', `${building.displayName} 开工（${villager.name}）。`);
  }

  private arriveAtAltar(villager: Villager): void {
    this.clearDeparture(villager);
    villager.mode = 'idle';
    villager.taskId = null;
    villager.routineLabel = null;
    villager.nextRoutineAt = this.now + 1600;
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
        const owner = this.villagers.find(
          (villager) => villager.taskId === activity.taskId && villager.mode === 'working',
        );
        if (task !== undefined && task.status === 'in_progress' && owner !== undefined) {
          this.finishActiveTask(owner);
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

  private finishActiveTask(villager: Villager): void {
    const task = this.activeTaskOf(villager);
    if (task === null || task.status === 'done') return;
    this.clearDeparture(villager);
    task.status = 'done';
    task.completedAt = this.now;
    task.blockedQuestion = null;
    this.recordDelivery(villager.id, task.source);
    const building = this.buildingOf(task);
    if (building !== undefined) {
      this.activities.set(building.instanceId, {
        kind: 'cooling',
        taskId: task.id,
        until: this.now + COOLING_MS,
      });
      villager.pos = { x: building.door.x, y: building.door.y };
    }
    villager.mode = 'delivering';
    villager.visible = true;
    villager.facing = 'down';
    villager.bubble = '任务交付！';
    villager.bubbleUntil = this.now + DELIVER_MS;
    villager.deliverUntil = this.now + DELIVER_MS;
    villager.path = [];
    this.accrueFaith();
    this.log('done', `${task.summary} 已交付（${villager.name}）。`);
  }

  /** 交付按人和来源分开记账：台账/沙盒各自分开，角色之间也不合并。 */
  private recordDelivery(villagerId: string, source: TaskRecord['source']): void {
    if (source === 'ledger') this.ledgerDeliveries += 1;
    else this.mockDeliveries += 1;
    const tally = this.deliveryTally.get(villagerId);
    if (tally === undefined) return;
    if (source === 'ledger') tally.ledger += 1;
    else tally.mock += 1;
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

  private activeTaskOf(villager: Villager): TaskRecord | null {
    if (villager.taskId === null) return null;
    return this.tasks.find((item) => item.id === villager.taskId) ?? null;
  }

  private buildingOf(task: TaskRecord): PlacedBuilding | undefined {
    if (task.buildingId === null) return undefined;
    return this.buildings.find((item) => item.instanceId === task.buildingId);
  }

  private expireBubble(villager: Villager): void {
    if (villager.bubble !== null && this.now >= villager.bubbleUntil) {
      villager.bubble = null;
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
