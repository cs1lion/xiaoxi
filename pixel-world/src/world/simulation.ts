/** 世界运行时。固定步长由调用方给，内部不读时钟，因此可以离线回放。
 *
 * 外部只能从一个口子进来：enqueue()。命令在下次 update() 开头统一结算，
 * 渲染循环里不会出现半更新的中间态。
 *
 * 角色是数组：每个小人各自有状态机、路线、任务与等待队列；命令一次只叫一个人。
 */

import type { WorldGrid } from './grid.ts';
import { TASK_PROFILES, taskDurationFor } from './tasks.ts';
import { pickVenue, type VenueCandidate } from './venue.ts';
import { fogAt, revealedRadiusFor } from './fog.ts';
import { awaitingDecree } from './decree.ts';

import { parseOracle } from './oracle.ts';
import { parseOracleBuild, type BuildIntent } from './oracle-build.ts';
import { parseOracleMiracle } from './oracle-miracle.ts';
import { routeIntent } from './intent-router.ts';
import type { IntentModel } from './intent-router.ts';
import type { ParsedIntent } from './intent-schema.ts';
import { summarizeWorld } from './world-state.ts';
import { summarizeForIntent } from './intent-context.ts';
import { addTrail, visibleTrails, type TrailMap } from './trails.ts';
import { carryKindFor } from './carry.ts';
import { WILD_PREFABS } from './setup.ts';
import {
  nextProp,
  MILESTONE_POINTS,
  describePropSource,
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
  CarryKind,
  Critter,
  Facing,
  GridPoint,
  OracleRecord,
  PendingDispatch,
  PlacedBuilding,
  PrefabBlueprint,
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
import { excerptBlockedQuestion, ORACLE_SLAB_LIMIT } from './types.ts';
import type { SavedVillager, SettledRecord, WorldSave } from './save.ts';
import { SCHEMA_V2_FINGERPRINT } from './save.ts';

/**
 * 台账事件时间 → settled 用的 epoch 毫秒。协议 §19 给的是秒（×1000）；
 * 已经是毫秒（>1e12，历史档/测试直给）就原样。缺失/非法 → null，
 * **禁止用本地时间顶替**（ADR_SAVE §2.2）。
 */
export function ledgerCompletedAtMs(value: number | null | undefined): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Math.round(value > 1e12 ? value : value * 1000);
}

/** 三件神迹。纯世界侧动作，不是你的话。 */
export type MiracleKind = 'light' | 'tree' | 'cat';

/** 猫在世界里待多久。到点自己走开，不占位。 */
const CAT_DWELL_MS = 20_000;

export type WorldCommand =
  /**
   * 一句神谕的原文。世界自己解析，解析结果再落到下面的具体命令上。
   *
   * 只有走这条入口才会进石板：外部 Agent 的 `feed`、台账的 `sync` 都不是
   * 「你对世界说的话」，不该被记成神谕。
   */
  | { readonly kind: 'oracle'; readonly raw: string }
  /**
   * 模型判定的结果，回队结算。
   *
   * 为什么不直接调 `applyIntent`：模型是异步的，回来时世界可能已经走了几拍。
   * 回队让它的时序与玩家同步输入完全一致——石板、事件、排队顺序都不特殊。
   */
  | { readonly kind: 'intent'; readonly intent: ParsedIntent; readonly raw: string }
  | { readonly kind: 'dispatch'; readonly taskKind: TaskKind; readonly summary: string }
  | { readonly kind: 'complete'; readonly target?: string }
  | { readonly kind: 'cancelPending'; readonly id?: string }
  | { readonly kind: 'setWeather'; readonly weather: Weather }
  /**
   * 造物。`at` 省略时自动选址（旧行为）；给了就是玩家在造物模式里指定的位置。
   */
  | {
      readonly kind: 'build';
      readonly prefabId: string;
      readonly instanceId: string;
      readonly at?: GridPoint;
      /** 这次建造的来由，写进建筑的来源字段。省略表示"没有来由"。 */
      readonly source?: { readonly taskId: string; readonly summary: string };
    }
  /**
   * 神迹：纯世界侧动作，不是你的话，所以不刻石板。
   * 三件：点灯、种树、召唤猫（猫只做氛围，会自己走开）。
   */
  /**
   * 神迹。`at` 省略时落在祭坛周围；给了就落在指定位置。
   * 指定位置要过三道：在界内、不在迷雾里、格子空着。
   */
  | { readonly kind: 'miracle'; readonly miracle: MiracleKind; readonly at?: GridPoint }
  /**
   * 拆掉一栋楼。
   *
   * 三条拒绝：找不到、正在施工、有人在里面干活。
   * 后者不是为了保护建筑，是为了保护正在干的活——拆了会让那单悬空。
   */
  | { readonly kind: 'demolish'; readonly instanceId: string }
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
        /** 台账事件时间（epoch 秒，closed_at ?? updated_at）；缺失/省略 = 无台账时间。 */
        readonly completedAt?: number | null;
      }[];
      /** 台账里已经收工的督办；世界据此把她的活收尾。 */
      readonly finished: readonly string[];
      /** 收工条目的台账事件时间（epoch 秒）；settled.completedAt 的唯一来源。 */
      readonly finishedAt?: readonly {
        readonly commissionId: string;
        readonly completedAt: number | null;
      }[];
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
  /**
   * 蓝图注册表。造物神谕（「建一间书阁」）要用它把词换成模板。
   * 省略时造物神谕一律认不出——那是安全的降级，不是错误。
   */
  readonly blueprints?: ReadonlyMap<string, PrefabBlueprint>;
  /**
   * 造物主：把一栋新楼落进世界。返回 null 表示落不下。
   * `at` 省略时自动选址；给了就是玩家指定的位置。
   */
  readonly spawnBuilding?: (
    prefabId: string,
    instanceId: string,
    at?: GridPoint,
    source?: { readonly taskId: string; readonly summary: string },
  ) => NewBuilding | null;
  /** 本地 wall clock（只给 settled.completedAt 的 mock 来源用，不喂逻辑）。 */
  readonly wallClock?: () => number;
  /**
   * 意图模型：规则认不出的话才问它。省略表示纯离线。
   *
   * 这里只放**客户端**，不放提示词与校验——那两样在世界侧已经是确定的了
   * （`intent-prompt.ts` / `intent-schema.ts`），模型只负责产出一份 JSON。
   */
  readonly intentModel?: IntentModel;
  /** 世界尺寸。给意图校验判坐标越界用；省略时从 grid 推。 */
  readonly worldSize?: { readonly width: number; readonly height: number };
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
  /** 手上带着的东西。郊野返程时非空。 */
  carrying: CarryKind | null;
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
  private readonly spawnBuilding:
    | ((
        prefabId: string,
        instanceId: string,
        at?: GridPoint,
        source?: { readonly taskId: string; readonly summary: string },
      ) => NewBuilding | null)
    | undefined;

  private readonly buildings: PlacedBuilding[];
  private readonly villagers: Villager[];
  private readonly tasks: TaskRecord[] = [];
  private readonly activities = new Map<string, WorkstationActivity>();
  private readonly events: WorldEvent[] = [];
  private readonly queue: WorldCommand[] = [];
  private readonly lastResults: CommandResult[] = [];
  private readonly props: WorldProp[] = [];
  /**
   * 走出来的痕迹。**不落档**：能从走动重新累积，存起来只会让存档无限膨胀。
   * 与铺好的 `roads` 是两回事——那个是施工结果，这个是踩踏。
   */
  private readonly trails: TrailMap = new Map();
  /** 神谕石板：只记改变了世界的话。旧在前，封顶 ORACLE_SLAB_LIMIT。 */
  private readonly oracleLog: OracleRecord[] = [];
  /** 氛围实体（猫等）。只影响渲染，不参与任何结算。 */
  private critters: Critter[] = [];
  /** 神迹编号：世界内自增，不用时间戳/随机数，保证可回放。 */
  private nextMiracleNumber = 1;
  /** 正在举急件的那位角色；null 表示没有急件在呈。 */
  private decreeBearer: string | null = null;
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
  /** 私有结算日志：save-only，不入快照（ADR_SAVE §2.1）。 */
  private readonly settledLog: SettledRecord[] = [];
  private readonly wallClock: () => number;
  private readonly blueprints: ReadonlyMap<string, PrefabBlueprint> | undefined;
  private readonly intentModel: IntentModel | undefined;
  private readonly worldSize: { readonly width: number; readonly height: number };
  /** 正在问模型的句子。防止同一句被重复投递时问两次。 */
  private readonly pendingClassify = new Set<string>();
  /** 是否已与台账对过账。读档后为 false：对账前不驱动台账单。 */
  private ledgerReconciled = true;
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
    this.wallClock = deps.wallClock ?? (() => Date.now());
    this.blueprints = deps.blueprints;
    this.intentModel = deps.intentModel;
    this.worldSize = deps.worldSize ?? { width: deps.grid.width, height: deps.grid.height };
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
      carrying: null,
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
    this.stepDecrees();
    this.stepCritters();
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
      oracle: this.oracleLog,
      critters: this.critters,
      // 揭示半径由交付数决定：用得越久，迷雾退得越多。
      fog: {
        center: this.altar,
        revealedRadius: revealedRadiusFor(this.ledgerDeliveries + this.mockDeliveries),
      },
      roads: this.roads,
      trails: visibleTrails(this.trails),
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
        /** 读档后到首次成功对账前为 false；此时台账单只读展示。 */
        reconciled: this.ledgerReconciled,
      },
    };
  }

  /** 天气浓淡给渲染层插值雨幕用。 */
  weatherIntensity(): number {
    return this.weather.intensity;
  }

  /** 调试面板用：取最近 N 条事件（快照只带 12 条，面板要 50 条）。只读。 */
  recentEvents(limit = 50): readonly WorldEvent[] {
    const size = Math.max(0, Math.floor(limit));
    return this.events.slice(-size);
  }

  // ---------------------------------------------------------------- commands

  private apply(command: WorldCommand): CommandResult {
    switch (command.kind) {
      case 'oracle':
        return this.speak(command.raw);
      case 'intent':
        return this.applyIntent(command.intent, command.raw);
      case 'dispatch':
        return this.dispatch(command.taskKind, command.summary);
      case 'complete':
        return this.completeManually(command.target);
      case 'cancelPending':
        return this.cancelPending(command.id);
      case 'setWeather':
        return this.setWeather(command.weather);
      case 'build':
        return this.build(command.prefabId, command.instanceId, command.at, command.source);
      case 'miracle':
        return this.performMiracle(command.miracle, command.at);
      case 'demolish':
        return this.demolish(command.instanceId);
      case 'sync':
        return this.sync(command.tasks, command.finished, command.connected,
          command.complete ?? true, command.order ?? [], command.finishedAt ?? []);
    }
  }

  /**
   * 神迹：直接在世界里多一样东西。不刻石板（这不是你说的那句话），
   * 也不发货币、没有冷却——那会把神迹变成资源管理。
   *
   * 落点规则与信仰摆件一致：祭坛周围的环上找空格，避开已有摆件。
   */
  private performMiracle(kind: MiracleKind, at?: GridPoint): CommandResult {
    const id = `miracle-${this.nextMiracleNumber}`;

    // 指定了位置就先验：在界内、不在迷雾里、格子空着。
    if (at !== undefined) {
      const problem = this.miracleSpotProblem(at);
      if (problem !== null) {
        this.log('warn', problem);
        return { ok: false, message: problem };
      }
    }

    if (kind === 'cat') {
      this.nextMiracleNumber += 1;
      this.critters.push({
        id,
        kind: 'cat',
        // 指定位置时落在格子中央；不给时仍贴着祭坛。
        at:
          at === undefined
            ? { x: this.altar.x + 2.5, y: this.altar.y + 2.5 }
            : { x: at.x + 0.5, y: at.y + 0.5 },
        leavesAt: this.now + CAT_DWELL_MS,
      });
      this.log('info', '神迹：一只猫从街角溜达过来。');
      return { ok: true, message: '一只猫来了，它会自己走开。' };
    }

    const propKind = kind === 'light' ? 'lamp' : 'tree';
    const label = kind === 'light' ? '一盏神迹点的灯' : '一棵神迹种的树';
    const spot = at ?? this.miracleSpot();
    if (spot === null) {
      return { ok: false, message: '祭坛周围没有空地了。' };
    }
    this.nextMiracleNumber += 1;
    this.props.push({
      id,
      kind: propKind,
      at: spot,
      label,
      sourceTaskKind: null,
      sourceTaskId: null,
      // 神迹不是任务换来的；摘要写明来源，纸面据此区分"神迹"与"哪一单"。
      sourceSummary: kind === 'light' ? '神迹：点灯' : '神迹：种树',
    });
    this.log('info', `神迹：${label}落在祭坛边。`);
    return { ok: true, message: `${label}落下了。` };
  }

  /**
   * 指定落点能不能用。返回 null 表示可以，否则返回给玩家看的原因。
   *
   * 三条与建造同一套口径：在界内、不在迷雾里、格子空着。
   * 神迹不占网格（摆件只是装饰），所以只查这三条，不查留白与门口。
   */
  private miracleSpotProblem(at: GridPoint): string | null {
    if (!this.grid.inBounds(at)) return '那里在世界外面。';
    // 判「被占」而不是「不能走」：树与树篱不可走但可以放东西，
    // 建筑不可走且不能放。两者的区别在 occupant。
    const cell = this.grid.at(at);
    if (cell === undefined) return '那里在世界外面。';
    if (cell.occupant !== null) return '那里被建筑占着。';
    if (fogAt(at, {
      center: this.altar,
      revealedRadius: revealedRadiusFor(this.ledgerDeliveries + this.mockDeliveries),
    })) {
      return '那里还没长出来。';
    }
    if (this.props.some((prop) => prop.at.x === at.x && prop.at.y === at.y)) {
      return '那里已经有一件神迹了。';
    }
    return null;
  }

  /** 神迹落点：从祭坛外环开始找，避开已占格与已有摆件。 */
  private miracleSpot(): GridPoint | null {
    const taken = new Set(this.props.map((prop) => `${prop.at.x},${prop.at.y}`));
    for (let radius = 2; radius <= 6; radius += 1) {
      for (let dx = -radius; dx <= radius; dx += 1) {
        for (const dy of [-radius, radius]) {
          const p = { x: this.altar.x + dx, y: this.altar.y + dy };
          if (!this.grid.isWalkable(p)) continue;
          if (this.grid.zoneOf(p) === 'altar') continue;
          if (taken.has(`${p.x},${p.y}`)) continue;
          return p;
        }
      }
      for (let dy = -radius + 1; dy <= radius - 1; dy += 1) {
        for (const dx of [-radius, radius]) {
          const p = { x: this.altar.x + dx, y: this.altar.y + dy };
          if (!this.grid.isWalkable(p)) continue;
          if (this.grid.zoneOf(p) === 'altar') continue;
          if (taken.has(`${p.x},${p.y}`)) continue;
          return p;
        }
      }
    }
    return null;
  }

  /**
   * 一句神谕：解析 → 落到具体命令 → **只有世界真的变了才刻上石板**。
   *
   * 判据是「回执说成功」，而不是「认出了意图」。认得出但没改变世界的
   * （撤销时没有排队单、本来就在下雨）不算世界事件，记下来就是噪音。
   *
   * 认不出的输入不落石板，回执里明说没听懂：石板不是聊天记录。
   */
  private speak(raw: string): CommandResult {
    // 先试造物：它认「建一间书阁」这类话，与派活解析器不重叠
    // （那个的动词表里没有「建 / 盖 / 造」）。
    const buildIntent = this.parseBuildIntent(raw);
    if (buildIntent !== null) return this.buildFromIntent(raw, buildIntent);

    // 神迹：「点灯 / 种树 / 召唤猫」，可带坐标。与造物解析器不抢词。
    const miracleIntent = parseOracleMiracle(raw);
    if (miracleIntent !== null) {
      const result = this.performMiracle(miracleIntent.miracle, miracleIntent.at ?? undefined);
      if (result.ok) this.inscribe(raw, result.message);
      return result;
    }

    const intent = parseOracle(raw);
    if (intent.kind === 'unknown') {
      // 规则认不出。有模型就异步问一次——但**这一拍不能等网络**。
      //
      // 快环纪律：`speak()` 在任何情况下都同步返回。模型的结果回来时
      // 走 `enqueue({kind:'intent'})` 重新排队，由下一个逻辑拍结算。
      // 这样世界的时间线不依赖网络延迟，离线时也只是"这句话没听懂"。
      if (this.intentModel !== undefined) {
        void this.classifyLater(raw);
        return { ok: true, message: '这句话规则认不出，正在问模型……' };
      }
      const message = '没听懂这句，世界没有动。';
      this.log('warn', message);
      return { ok: false, message };
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
    const result = this.apply(command);
    if (result.ok) this.inscribe(raw, result.message);
    return result;
  }

  /**
   * 异步问模型，回来后再排队结算。
   *
   * 三条纪律：
   *
   * 1. **不 await 在快环里。** 调用方是 `speak()`，它必须同步返回。
   * 2. **结果回队，不直接改世界。** 模型的结果和玩家的话走同一条命令队列，
   *    这样时序、石板、事件顺序都与同步输入一致——没有"特权路径"。
   * 3. **失败只是没听懂。** 超时、网络错、JSON 不合法，全部落到 unknown，
   *    绝不退而求其次猜一个。
   */
  private async classifyLater(raw: string): Promise<void> {
    const model = this.intentModel;
    if (model === undefined) return;
    if (this.pendingClassify.has(raw)) return; // 同一句只问一次
    this.pendingClassify.add(raw);
    try {
      const result = await routeIntent(raw, {
        templates: this.blueprints ?? new Map(),
        worldSize: this.worldSize,
        model,
        // 现取快照：这句话进队列时世界的样子。模型据此少犯「已有工坊还要建」这类错。
        context: summarizeForIntent(this.snapshot()),
      });
      if (result.intent.kind === 'unknown') {
        const message = '没听懂这句，世界没有动。';
        this.log('warn', result.modelError ?? message);
        this.enqueue({ kind: 'intent', intent: { kind: 'unknown', reason: message }, raw });
        return;
      }
      this.enqueue({ kind: 'intent', intent: result.intent, raw });
    } finally {
      this.pendingClassify.delete(raw);
    }
  }

  /** 把一份已校验的意图落成世界命令。与 `speak()` 的下半段同一条路。 */
  private applyIntent(intent: ParsedIntent, raw: string): CommandResult {
    switch (intent.kind) {
      case 'dispatch':
        return this.settleIntent({ kind: 'dispatch', taskKind: intent.taskKind, summary: intent.summary }, raw);
      case 'setWeather':
        return this.settleIntent({ kind: 'setWeather', weather: intent.weather }, raw);
      case 'cancelPending':
        return this.settleIntent({ kind: 'cancelPending' }, raw);
      case 'complete':
        return this.settleIntent({ kind: 'complete' }, raw);
      case 'build': {
        const instanceId = `${intent.templateId}#${this.buildings.length + 1}`;
        return this.settleIntent(
          { kind: 'build', prefabId: intent.templateId, instanceId, at: intent.at ?? undefined },
          raw,
        );
      }
      case 'demolish': {
        const target = this.buildings.find((b) => b.prefabId === intent.templateId);
        if (target === undefined) {
          const message = `没有${this.blueprints?.get(intent.templateId)?.displayName ?? intent.templateId}可拆。`;
          this.log('warn', message);
          return { ok: false, message };
        }
        const result = this.demolish(target.instanceId);
        if (result.ok) this.inscribe(raw, result.message);
        return result;
      }
      case 'miracle': {
        const result = this.performMiracle(intent.miracle, intent.at ?? undefined);
        if (result.ok) this.inscribe(raw, result.message);
        return result;
      }
      case 'unknown': {
        this.log('warn', intent.reason);
        return { ok: false, message: '没听懂这句，世界没有动。' };
      }
    }
  }

  /** 走命令、成功就刻石板。`applyIntent` 里几处共用的收尾。 */
  private settleIntent(command: WorldCommand, raw: string): CommandResult {
    const result = this.apply(command);
    if (result.ok) this.inscribe(raw, result.message);
    return result;
  }

  /** 试着把这句话认成造物意图。认不出返回 null，交给别的解析器。 */
  private parseBuildIntent(raw: string): BuildIntent | null {
    if (this.blueprints === undefined) return null;
    return parseOracleBuild(raw, this.blueprints);
  }

  /**
   * 把造物意图落成一次建造。
   *
   * 位置：说了就用说的；没说就由 `findBestSlot` 挑——那是世界的判断，
   * 不是模型的。校验仍走 `build` 那一套（重复检查、施工、铺路）。
   */
  private buildFromIntent(raw: string, intent: BuildIntent): CommandResult {
    // 拆：同一份意图，方向相反。找到那栋楼再走 demolish 的三条拒绝。
    if (intent.action === 'demolish') {
      const target = this.buildings.find((b) => b.prefabId === intent.templateId);
      if (target === undefined) {
        const message = `没有${this.blueprints?.get(intent.templateId)?.displayName ?? intent.templateId}可拆。`;
        this.log('warn', message);
        return { ok: false, message };
      }
      const result = this.demolish(target.instanceId);
      if (result.ok) this.inscribe(raw, result.message);
      return result;
    }

    const blueprint = this.blueprints?.get(intent.templateId);
    if (blueprint === undefined) {
      const message = `没有 ${intent.templateId} 这份蓝图。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    // 世界里的每种铺子只有一栋。认出了意图，但要说清"已经有了"——
    // 这比认不出有用：用户知道自己被听懂了，也知道为什么没建。
    const existing = this.buildings.find((b) => b.prefabId === intent.templateId);
    if (existing !== undefined) {
      // 说清"已经有了"，并且如果世界有明确缺口，顺带指出该补什么。
      const gaps = summarizeWorld(this.snapshot()).gaps.missingWild;
      // 用纸面上的说法（「查资料」），不是内部枚举名（research）。
      const hint =
        gaps.length === 0
          ? '想扩建的话说「建个工坊」。'
          : `世界还缺${gaps.map((kind) => TASK_PROFILES[kind].label).join('、')}的郊野铺子。`;
      const message = `已经有${existing.displayName}了。${hint}`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const instanceId = `${intent.templateId}#${this.buildings.length + 1}`;
    const result = this.build(intent.templateId, instanceId, intent.at ?? undefined, {
      taskId: `oracle-build-${instanceId}`,
      summary: `按神谕建了${blueprint.displayName}`,
    });
    if (result.ok) this.inscribe(raw, result.message);
    return result;
  }

  /** 刻石板。只记真正改变了世界的那句话；超出上限丢最旧。 */
  private inscribe(raw: string, receipt: string): void {
    this.oracleLog.push({ raw, receipt, at: this.now });
    if (this.oracleLog.length > ORACLE_SLAB_LIMIT) {
      this.oracleLog.splice(0, this.oracleLog.length - ORACLE_SLAB_LIMIT);
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
      readonly completedAt?: number | null;
    }[],
    finished: readonly string[],
    connected: boolean,
    complete: boolean,
    order: readonly string[],
    finishedAt: readonly { readonly commissionId: string; readonly completedAt: number | null }[] = [],
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
    // 读档后的第一次成功 sync：先对账；对账前台账单只读展示，不派活/不交付/不撤单。
    if (!this.ledgerReconciled) {
      return this.reconcile(incoming, finished, complete, order, finishedAt);
    }

    const finishedIds = new Set(finished);
    const finishedAtById = new Map(
      finishedAt.map((item) => [item.commissionId, item.completedAt]),
    );
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
        // 先记下这单的台账事件时间，settled 只认它（缺失就是 null）。
        active.ledgerCompletedAt = finishedAtById.get(active.id) ?? active.ledgerCompletedAt ?? null;
        if (this.isSettled(active.id)) this.removeTask(active.id);
        else this.finishActiveTask(villager);
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
      // 已结算的 id 不复活（ADR_SAVE §2.1）。
      if (this.isSettled(item.commissionId)) continue;
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
        ledgerCompletedAt: item.completedAt ?? null,
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
    // 读档后对账前：台账单只读展示，不派活。
    if (!this.ledgerReconciled) return;
    for (const task of this.nextLedgerTasks()) {
      // 台账活只去镇内铺子：那是本地确定性工作。郊野留给探索性的神谕活。
      const chosen = pickVenue(this.venueCandidates(), task.kind, 'ledger');
      if (chosen === null) {
        this.log('warn', `没有空闲的镇内铺子承接 ${task.kind}，「${task.summary}」先等着。`);
        continue;
      }
      const building = this.buildings.find((item) => item.instanceId === chosen.instanceId);
      if (building === undefined) continue;
      const path = this.grid.path(this.cellOf(villager.pos), building.door);
      if (path.length === 0) continue;
      task.buildingId = building.instanceId;
      task.workerId = villager.id;
      this.routeToBuilding(villager, task, building, path);
      this.log('oracle', `台账派活：${task.summary} → ${building.displayName}（${villager.name}）`);
      return;
    }
  }

  /**
   * 当前所有铺子的候选视图，供 `pickVenue` 挑。
   *
   * 「郊野」按离祭坛的距离判，而不是按分区名：镇内与郊野的铺子可能同属
   * `industry`，只有位置能区分。
   */
  private venueCandidates(): readonly VenueCandidate[] {
    return this.buildings.map((building) => ({
      instanceId: building.instanceId,
      boundTask: building.boundTask,
      // 按**身份**判郊野，不按距离：镇内书斋实测落在 23 格外，按距离会被
      // 误判成郊野，于是神谕活全派给了镇内铺子。prefab id 是可靠的来源。
      isWild: WILD_PREFABS.has(building.prefabId),
      busy:
        this.constructions.has(building.instanceId) ||
        this.activities.get(building.instanceId)?.kind === 'overheating' ||
        this.buildingBusy(building),
    }));
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

  /**
   * 让停手的角色回到「人能看见、也能走动」的状态（D-1）。
   *
   * 两件事缺一不可：
   *   1. visible = true —— 干活时 visible=false（arriveAtWork），停手后不恢复就**不渲染**。
   *   2. 位置移出建筑 —— 干活时 pos = building.workSpot，那是**占地内**的不可走格；
   *      而 grid.path() 在起点不可走时直接返回空数组（grid.ts path()），
   *      于是她永远迈不出第一步：人看得见，但钉在屋里不动。
   *
   * 两条「单没了」的路径都必须调它：live-sync 走 abandonActiveTask，
   * 读档对账走 removeTask —— 后者原先只重置 mode/taskId/path，两条都没做，
   * 角色会变成 idle + visible=false + 卡在工位，且该状态入档、跨会话持久。
   *
   * 落点选 building.door：assertWorldInvariants 强校验「门口不可通行」会直接拒绝开工，
   * 所以 door 一定是占地之外的可走格，是这里唯一不需要再找空地的落点。
   * 角色本来就在可走格（例如户外被撤单）时不动它，避免无谓位移。
   */
  private restoreOutOfBuilding(
    villager: Villager,
    building: PlacedBuilding | undefined,
  ): void {
    villager.visible = true;
    if (building === undefined) return;
    if (this.grid.isWalkable(this.cellOf(villager.pos))) return;
    villager.pos = { x: building.door.x, y: building.door.y };
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
    this.restoreOutOfBuilding(villager, building);
    villager.path = [];
    villager.bubble = '这单被撤了。';
    villager.bubbleUntil = this.now + 1600;
    villager.nextRoutineAt = this.now + 1800;
    villager.routineLabel = null;
    this.log('warn', `${task.summary} 被上游撤回，${villager.name}停手了。`);
  }

  private setWeather(weather: Weather): CommandResult {
    if (weather === this.weather.kind && this.weather.remainingMs !== null) {
      // `ok: false` 表示"世界没变"，与"命令被拒"是两回事；调用方只把 ok 当"变了"用。
      return { ok: false, message: '天已经在下雨了。' };
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

    // 你派的活优先去郊野：那是探索性的外部工作。
    const chosen = pickVenue(this.venueCandidates(), taskKind, 'oracle');
    if (chosen === null) {
      const message = `没有空闲的 ${taskKind} 铺子。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const building = this.buildings.find((item) => item.instanceId === chosen.instanceId);
    if (building === undefined) {
      return { ok: false, message: `找不到那栋铺子。` };
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

  private build(
    prefabId: string,
    instanceId: string,
    at?: GridPoint,
    source?: { readonly taskId: string; readonly summary: string },
  ): CommandResult {
    if (this.spawnBuilding === undefined) {
      return { ok: false, message: '这个世界没有造物主。' };
    }
    if (this.buildings.some((item) => item.prefabId === prefabId)) {
      return { ok: false, message: '这种铺子已经有一栋了。' };
    }
    const spawned = this.spawnBuilding(prefabId, instanceId, at, source);
    if (spawned === null) {
      return {
        ok: false,
        // 指定位置时要说清是"这里不行"，不是"没地方了"。
        message: at === undefined ? '分区里已经没有合规空地。' : '这个位置放不下。',
      };
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
      carrying: villager.carrying,
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
        if (villager.path.length === 0) {
          // 到祭坛就把东西放下。
          villager.carrying = null;
          this.arriveAtAltar(villager);
        }
        return;
      case 'bearingDecree':
        this.walk(villager, deltaMs);
        // 走到祭坛就停下举牌，等用户点开。**不自己回去**：
        // 问题没答完之前，急件一直呈在那里。
        if (villager.path.length === 0) {
          villager.mode = 'idle';
          villager.bubble = '有急件请旨';
          villager.bubbleUntil = this.now + 4000;
        }
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
    if (!this.isSettled(task.id)) this.recordDelivery(villager.id, task);
    villager.mode = 'idle';
    villager.taskId = null;
    villager.bubble = '办好了。';
    villager.bubbleUntil = this.now + QUICK_DONE_MS;
    villager.nextRoutineAt = this.now + 1800;
    villager.routineLabel = null;
    this.accrueFaith(task);
    this.log('done', `${task.summary} 办完了（${villager.name}）。`);
  }

  private stepIdle(villager: Villager, deltaMs: number): void {
    if (villager.path.length > 0) {
      this.walk(villager, deltaMs);
      return;
    }
    // 举着急件的人不走作息：他得守在祭坛等答复。
    // 不加这条，他一到祭坛就被作息带走，急件呈不出来（实测 v1 在祭坛
    // 停一拍就溜去农田）。
    if (this.decreeBearer === villager.id && this.hasDecree()) return;
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
        // 走过一格就踩一次。重复走的格子强度累加，草被踩实。
        addTrail(this.trails, next);
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
      // 郊野的活更久：走那么远，做的活也该更重。
      estimateMs: taskDurationFor(task.kind, WILD_PREFABS.has(building.prefabId)),
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
    if (!this.isSettled(task.id)) this.recordDelivery(villager.id, task);
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
    // 郊野干完活：手上多个东西。它是"跑了一趟"的证据，不是奖励。
    villager.carrying =
      building !== undefined && WILD_PREFABS.has(building.prefabId)
        ? carryKindFor(task.kind)
        : null;
    villager.bubble = '任务交付！';
    villager.bubbleUntil = this.now + DELIVER_MS;
    villager.deliverUntil = this.now + DELIVER_MS;
    villager.path = [];
    this.accrueFaith(task);
    this.log('done', `${task.summary} 已交付（${villager.name}）。`);
  }

  /** 交付按人和来源分开记账：台账/沙盒各自分开，角色之间也不合并。 */
  private recordDelivery(villagerId: string, task: TaskRecord): void {
    const source = task.source;
    if (source === 'ledger') this.ledgerDeliveries += 1;
    else this.mockDeliveries += 1;
    const tally = this.deliveryTally.get(villagerId) ?? this.deliveryTally.get(this.villagers[0].id);
    if (tally !== undefined) {
      if (source === 'ledger') tally.ledger += 1;
      else tally.mock += 1;
    }
    this.settledLog.push({
      id: task.id,
      source: source === 'ledger' ? 'ledger' : 'mock',
      workerId: villagerId,
      // 台账时间只认 closed_at ?? updated_at（×1000）；缺失保持 null，绝不本地时间顶替。
      completedAt: source === 'ledger'
        ? ledgerCompletedAtMs(task.ledgerCompletedAt)
        : this.wallClock(),
      completedAtClock: source === 'ledger' ? 'ledger' : 'local',
    });
  }

  private isSettled(taskId: string): boolean {
    return this.settledLog.some((record) => record.id === taskId);
  }

  /** 攒够一个里程碑，世界多一件东西；不够就只是点数。
   *
   * `task` 是刚刚交付、把点数推过线的那一单：摆件的来源就记它。
   * 落不下（周围没空地）时点数保留，不吞掉。
   */
  private accrueFaith(task: TaskRecord): void {
    this.faithPoints += POINTS_PER_TASK;
    if (this.faithPoints < FAITH_MILESTONE) return;
    this.faithPoints -= FAITH_MILESTONE;
    const prop = nextProp(
      this.milestones,
      this.altar,
      (p) => this.grid.isWalkable(p),
      { kind: task.kind, taskId: task.id, summary: task.summary },
      this.props.map((item) => item.at),
    );
    if (prop === null) return;
    this.props.push(prop);
    this.milestones += 1;
    // 日志写清是哪一单换的：这是"被记住"在事件流里的第一次落地。
    this.log('info', `神恩落地：${describePropSource(prop)}。`);
  }

  /**
   * 有 blocked 单就派一个人把急件送到祭坛。
   *
   * 只在没人已经举着的时候派：一次只呈一张，同时三个人举牌会把祭坛变收件箱。
   * 送件的人挑第一个空闲的；都忙就等下一拍——不打断正在干的活。
   */
  private stepDecrees(): void {
    const decree = awaitingDecree(this.snapshotForDecree());
    if (decree === null) {
      this.decreeBearer = null;
      return;
    }
    if (this.decreeBearer !== null) {
      const bearer = this.villagers.find((item) => item.id === this.decreeBearer);
      // 已在位：正在跑，或者已经到了并站在祭坛旁。
      // 判「到没到」用位置而不是 bubble——bubble 会过期，过期后这里会
      // 误判成"没人送"再派一次，把已经举着的人重新派出去。
      if (bearer !== undefined) {
        const atAltar =
          Math.abs(bearer.pos.x - this.altar.x) < 1.5 &&
          Math.abs(bearer.pos.y - this.altar.y) < 1.5;
        if (bearer.mode === 'bearingDecree' || atAltar) return;
      }
      this.decreeBearer = null;
    }

    const free = this.villagers.find((item) => item.mode === 'idle' && item.path.length === 0);
    if (free === undefined) return;

    const path = this.grid.path(this.cellOf(free.pos), this.altar);
    if (path.length === 0) return;
    free.mode = 'bearingDecree';
    free.path = path;
    free.bubble = '急件！';
    free.bubbleUntil = this.now + 3000;
    this.decreeBearer = free.id;
    this.log('warn', `急件请旨：${decree.title}`);
  }

  /** 当前是否有待答复的急件。 */
  private hasDecree(): boolean {
    return awaitingDecree(this.snapshotForDecree()) !== null;
  }

  /** 造一个只含台账单的视图给 `awaitingDecree` 用。 */
  private snapshotForDecree(): WorldSnapshot {
    return { ...this.snapshot(), tasks: this.tasks };
  }

  /** 到点的猫自己走开。猫不参与任何结算，这里只做退场。 */
  private stepCritters(): void {
    if (this.critters.length === 0) return;
    this.critters = this.critters.filter((critter) => critter.leavesAt > this.now);
  }

  /** 拆一栋楼。见 `WorldCommand.demolish` 的三条拒绝。 */
  private demolish(instanceId: string): CommandResult {
    const building = this.buildings.find((item) => item.instanceId === instanceId);
    if (building === undefined) {
      const message = `没有 ${instanceId} 这栋楼。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    if (this.constructions.has(instanceId)) {
      const message = `${building.displayName} 还在施工，拆不了。`;
      this.log('warn', message);
      return { ok: false, message };
    }
    const busy = this.activities.get(instanceId);
    if (busy !== undefined && busy.kind === 'operating') {
      const message = `${building.displayName} 里有人在干活，等这单收工再拆。`;
      this.log('warn', message);
      return { ok: false, message };
    }

    // 释放网格占用。门口的路保留——那是铺过的路，拆楼不铲路。
    this.grid.remove(instanceId, building.origin, building.footprint.w, building.footprint.h);
    const index = this.buildings.findIndex((item) => item.instanceId === instanceId);
    if (index >= 0) this.buildings.splice(index, 1);
    this.activities.delete(instanceId);

    const message = `${building.displayName} 拆掉了。`;
    this.log('info', message);
    return { ok: true, message };
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

  // ---------------------------------------------------------------- save

  /** 序列化世界自有事实；台账链接态与派生计数不入档（ADR_SAVE §1）。 */
  toSave(): WorldSave {
    const villagers: SavedVillager[] = this.villagers.map((villager) => ({
      id: villager.id,
      name: villager.name,
      mode: villager.mode,
      pos: { x: villager.pos.x, y: villager.pos.y },
      facing: villager.facing,
      visible: villager.visible,
      path: villager.path.map((cell) => ({ x: cell.x, y: cell.y })),
      taskId: villager.taskId,
      bubble: villager.bubble,
      bubbleUntil: villager.bubbleUntil,
      saluteUntil: villager.saluteUntil,
      deliverUntil: villager.deliverUntil,
      nextRoutineAt: villager.nextRoutineAt,
      routineIndex: villager.routineIndex,
      routineLabel: villager.routineLabel,
      inPlaceUntil: villager.inPlaceUntil,
      pendingRoute: villager.pendingRoute.map((cell) => ({ x: cell.x, y: cell.y })),
      pendingBuildingId: villager.pendingBuilding?.instanceId ?? null,
    }));
    return {
      schemaVersion: 2,
      world: {
        now: this.now,
        villagers,
        buildings: this.buildings.map((building) => ({ ...building })),
        roads: this.roads.map((cell) => ({ x: cell.x, y: cell.y })),
        props: this.props.map((prop) => ({ ...prop })),
        oracle: this.oracleLog.map((record) => ({ ...record })),
        critters: this.critters.map((critter) => ({ ...critter, at: { ...critter.at } })),
        // 运行时字段不落盘：ledgerCompletedAt 只服务 settled；v1 schema 已冻结、样例无此键。
        tasks: this.tasks.map((task) => {
          const saved: TaskRecord = { ...task };
          delete saved.ledgerCompletedAt;
          return saved;
        }),
        pending: this.allPending(),
        activities: [...this.activities.entries()].map(([buildingId, activity]) => ({
          buildingId,
          activity,
        })),
        constructions: [...this.constructions.entries()].map(([buildingId, state]) => ({
          buildingId,
          remainingMs: state.remainingMs,
          totalMs: state.totalMs,
        })),
        weather: { ...this.weather },
        faith: { points: this.faithPoints, milestones: this.milestones },
        settled: this.settledLog.map((record) => ({ ...record })),
        nextTaskNumber: this.nextTaskNumber,
        nextPendingNumber: this.nextPendingNumber,
      },
      meta: {
        savedAtWallClock: this.wallClock(),
        savedReconciled: this.ledgerReconciled,
        schemaFingerprint: SCHEMA_V2_FINGERPRINT,
      },
    };
  }

  /**
   * 从存档重建世界。deps.grid 必须已按存档 buildings 顺序重放
   * （`rebuildGridFromPlacements`），deps.buildings 用存档里的那批。
   * 读档后台账一律标记未对账，等首个成功 sync 走 reconcile。
   */
  static fromSave(save: WorldSave, deps: SimulationDeps): Simulation {
    const names = save.world.villagers.map((villager) => villager.name);
    const sim = new Simulation({
      ...deps,
      villagerName: names[0] ?? deps.villagerName,
      villagerNames: names,
    });
    const world = save.world;
    sim.now = world.now;
    sim.buildings.length = 0;
    sim.buildings.push(...world.buildings.map((building) => ({ ...building })));

    for (const saved of world.villagers) {
      const villager = sim.villagers.find((item) => item.id === saved.id);
      if (villager === undefined) continue;
      villager.mode = saved.mode;
      villager.pos = { x: saved.pos.x, y: saved.pos.y };
      villager.facing = saved.facing;
      villager.visible = saved.visible;
      villager.path = saved.path.map((cell) => ({ x: cell.x, y: cell.y }));
      villager.taskId = saved.taskId;
      villager.bubble = saved.bubble;
      villager.bubbleUntil = saved.bubbleUntil;
      villager.saluteUntil = saved.saluteUntil;
      villager.deliverUntil = saved.deliverUntil;
      villager.nextRoutineAt = saved.nextRoutineAt;
      villager.routineIndex = saved.routineIndex;
      villager.routineLabel = saved.routineLabel;
      villager.inPlaceUntil = saved.inPlaceUntil;
      villager.pendingRoute = saved.pendingRoute.map((cell) => ({ x: cell.x, y: cell.y }));
      villager.pendingBuilding =
        saved.pendingBuildingId === null
          ? null
          : (sim.buildings.find((item) => item.instanceId === saved.pendingBuildingId) ?? null);
    }

    for (const queue of sim.pending.values()) queue.length = 0;
    for (const item of world.pending) {
      const queue =
        sim.pending.get(item.villagerId) ?? sim.pending.get(sim.villagers[0].id);
      queue?.push({ ...item });
    }

    sim.tasks.length = 0;
    sim.tasks.push(...world.tasks.map((task) => ({ ...task })));

    sim.roads.length = 0;
    sim.roads.push(...world.roads.map((cell) => ({ x: cell.x, y: cell.y })));
    sim.props.length = 0;
    sim.props.push(...world.props.map((prop) => ({ ...prop })));

    sim.activities.clear();
    for (const saved of world.activities) {
      if (sim.buildings.some((building) => building.instanceId === saved.buildingId)) {
        sim.activities.set(saved.buildingId, saved.activity);
      }
    }
    for (const building of sim.buildings) {
      if (!sim.activities.has(building.instanceId)) {
        sim.activities.set(building.instanceId, { kind: 'idle' });
      }
    }

    sim.constructions.clear();
    for (const saved of world.constructions) {
      if (!sim.buildings.some((building) => building.instanceId === saved.buildingId)) continue;
      sim.constructions.set(saved.buildingId, {
        remainingMs: saved.remainingMs,
        totalMs: saved.totalMs,
      });
    }

    sim.weather = { ...world.weather };
    sim.faithPoints = world.faith.points;
    sim.milestones = world.faith.milestones;

    sim.oracleLog.length = 0;
    for (const record of world.oracle) sim.oracleLog.push({ ...record });

    sim.critters = world.critters.map((critter) => ({ ...critter, at: { ...critter.at } }));

    // settled 是分账唯一真相：由它重建私有日志与交付统计（不持久化计数）。
    sim.settledLog.length = 0;
    for (const record of world.settled) {
      if (sim.settledLog.some((item) => item.id === record.id)) continue;
      sim.settledLog.push({ ...record });
    }
    for (const tally of sim.deliveryTally.values()) {
      tally.ledger = 0;
      tally.mock = 0;
    }
    sim.ledgerDeliveries = 0;
    sim.mockDeliveries = 0;
    for (const record of sim.settledLog) {
      if (record.source === 'ledger') sim.ledgerDeliveries += 1;
      else sim.mockDeliveries += 1;
      const tally =
        sim.deliveryTally.get(record.workerId) ?? sim.deliveryTally.get(sim.villagers[0].id);
      if (tally === undefined) continue;
      if (record.source === 'ledger') tally.ledger += 1;
      else tally.mock += 1;
    }

    sim.nextTaskNumber = world.nextTaskNumber;
    sim.nextPendingNumber = world.nextPendingNumber;
    sim.ledgerConnected = false;
    sim.ledgerSyncedAt = null;
    sim.ledgerComplete = true;
    sim.ledgerOrder = [];
    sim.ledgerReconciled = false;
    sim.log('info', '读档回来：世界从关页那一刻继续。');
    return sim;
  }

  /** 读档后的首次对账：台账覆盖本地、已结算不重复、缺席且完整不复活。 */
  private reconcile(
    incoming: readonly {
      readonly commissionId: string;
      readonly kind: TaskKind;
      readonly title: string;
      readonly agentName: string;
      readonly status?: string;
      readonly blockedQuestion?: string;
      readonly completedAt?: number | null;
    }[],
    finished: readonly string[],
    complete: boolean,
    order: readonly string[],
    finishedAt: readonly { readonly commissionId: string; readonly completedAt: number | null }[] = [],
  ): CommandResult {
    this.ledgerOrder = order;
    this.ledgerComplete = complete;
    const finishedIds = new Set(finished);
    const finishedAtById = new Map(
      finishedAt.map((item) => [item.commissionId, item.completedAt]),
    );
    const incomingById = new Map(incoming.map((item) => [item.commissionId, item]));
    const questionOf = (item: {
      readonly status?: string;
      readonly blockedQuestion?: string;
    }): string | null => {
      if (item.status !== 'blocked') return null;
      const question = (item.blockedQuestion ?? '').trim();
      return question === '' ? null : question;
    };

    for (const task of [...this.tasks]) {
      if (task.source !== 'ledger') continue;
      const item = incomingById.get(task.id);
      if (item !== undefined) {
        const index = this.tasks.indexOf(task);
        this.tasks[index] = {
          ...task,
          kind: item.kind,
          summary: item.title,
          blockedQuestion: questionOf(item),
          // 台账在跑的单也会带 updated_at：先记住，settle 时没有 closed_at 就用它。
          ledgerCompletedAt: item.completedAt ?? task.ledgerCompletedAt ?? null,
        };
        if (!this.villagers.some((villager) => villager.taskId === task.id)) {
          this.tasks[index].workerId = null;
        }
        continue;
      }
      if (finishedIds.has(task.id)) {
        // 台账事件时间从 finishedAt 映射取；缺失就保留上次见到的 updated_at，再没有就 null。
        task.ledgerCompletedAt =
          finishedAtById.get(task.id) ?? task.ledgerCompletedAt ?? null;
        if (!this.isSettled(task.id)) {
          const owner = this.villagers.find(
            (villager) =>
              villager.taskId === task.id &&
              (villager.mode === 'working' ||
                villager.mode === 'saluting' ||
                villager.mode === 'toWork' ||
                villager.mode === 'delivering'),
          );
          if (owner !== undefined) this.finishActiveTask(owner);
          else this.settleDetachedLedgerTask(task);
        }
        this.removeTask(task.id);
        continue;
      }
      // 台账完整快照里缺席：丢弃，不复活（沿用撤单语义）。
      if (complete) this.removeTask(task.id);
    }

    let added = 0;
    for (const item of incoming) {
      if (this.tasks.some((task) => task.id === item.commissionId)) continue;
      // 已结算的 id 不复活（ADR_SAVE §2.1）。
      if (this.isSettled(item.commissionId)) continue;
      this.tasks.push({
        id: item.commissionId,
        kind: item.kind,
        summary: item.title,
        buildingId: null,
        source: 'ledger',
        status: 'assigned',
        createdAt: this.now,
        completedAt: null,
        blockedQuestion: questionOf(item),
        ledgerCompletedAt: item.completedAt ?? null,
        workerId: null,
      });
      added += 1;
    }

    this.ledgerReconciled = true;
    this.ledgerConnected = true;
    const message = `台账已对账，${this.tasks.length} 条在册。`;
    this.log('info', message);
    if (added > 0) this.log('info', `对账补进 ${added} 条新督办。`);
    return { ok: true, message };
  }

  private settleDetachedLedgerTask(task: TaskRecord): void {
    if (this.isSettled(task.id)) return;
    task.status = 'done';
    task.completedAt = this.now;
    task.blockedQuestion = null;
    const workerId = task.workerId ?? this.villagers[0].id;
    this.recordDelivery(workerId, task);
    this.log('done', `${task.summary} 按台账对账收尾。`);
  }

  private removeTask(taskId: string): void {
    // 先记下这单挂在哪栋楼，删单后 task 没了、buildingOf 就查不到了；
    // 角色若正在屋里干活，需要这栋楼来算「挪到哪个门口」。
    const owningBuilding = this.buildingOfId(taskId);
    const index = this.tasks.findIndex((task) => task.id === taskId);
    if (index >= 0) this.tasks.splice(index, 1);
    for (const villager of this.villagers) {
      if (villager.taskId !== taskId) continue;
      villager.mode = 'idle';
      villager.taskId = null;
      // 与 abandonActiveTask 共用同一套收尾：恢复可见 + 移出建筑。
      // 少了这一步，读档对账撤单后角色会 idle + visible=false + 卡在工位（D-1）。
      this.restoreOutOfBuilding(villager, owningBuilding);
      villager.path = [];
      villager.pendingRoute = [];
      villager.pendingBuilding = null;
      villager.bubble = null;
      villager.nextRoutineAt = this.now + 1600;
    }
    this.idleWorkstationFor(taskId);
  }

  /** 按 taskId 找它挂靠的楼（删单前调用；删完 task 就查不到了）。 */
  private buildingOfId(taskId: string): PlacedBuilding | undefined {
    const task = this.tasks.find((item) => item.id === taskId);
    return task === undefined ? undefined : this.buildingOf(task);
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
