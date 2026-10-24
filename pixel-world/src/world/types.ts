/** 世界数据模型。这一层不依赖 Phaser，可被 node --test 直接加载。 */

export interface GridPoint {
  readonly x: number;
  readonly y: number;
}

/** 浮点世界坐标，单位是格（1 = 一块 16px 图块）。 */
export interface Vec2 {
  readonly x: number;
  readonly y: number;
}

export type Facing = 'up' | 'down' | 'left' | 'right';

export type ZoneId =
  | 'street'
  | 'altar'
  | 'industry'
  | 'scholarly'
  | 'agriculture'
  | 'living';

export type TerrainId = 'grass' | 'stone' | 'plaza' | 'farm' | 'tree';

export interface TileCell {
  zone: ZoneId;
  terrain: TerrainId;
  walkable: boolean;
  /** 占用者 id（建筑实例名），null 表示空。 */
  occupant: string | null;
}

/** 神谕能派的活。前三种入驻铺子，`quick` 在原地做完。 */
export type TaskKind = 'code' | 'research' | 'writing' | 'quick';

/** 需要铺子承接的活。`quick` 故意不在里面。 */
export type WorkshopTask = Exclude<TaskKind, 'quick'>;

/** 铺子干活时冒出来的东西（纯视觉，由渲染层解释）。 */
export type WorkAccent = 'smoke' | 'pages' | 'ink';

/** 建筑轮廓。三栋楼靠剪影就能分开，不用读招牌。 */
export type Silhouette = 'gable' | 'tower' | 'flat';

export interface PrefabBlueprint {
  readonly id: string;
  readonly displayName: string;
  readonly zone: ZoneId;
  readonly footprint: { readonly w: number; readonly h: number };
  /** 建筑四周必须留出的空地格数。防贴脸、保胡同。 */
  readonly clearMargin: number;
  /** 门口相对原点的偏移，A* 终点用它。必须在占地之外。 */
  readonly door: GridPoint;
  /** 干活时小人站的位置（相对原点偏移）。 */
  readonly workSpots: readonly GridPoint[];
  /** 烟囱相对原点的偏移，单位是格；粒子发射器挂在这里。 */
  readonly chimney: Vec2;
  readonly accent: WorkAccent;
  readonly silhouette: Silhouette;
  /** null 表示这栋楼不接活，只占地方（扩建样板、装饰建筑）。 */
  readonly boundTask: WorkshopTask | null;
  readonly palette: {
    readonly body: number;
    readonly roof: number;
    readonly trim: number;
    readonly window: number;
  };
  readonly upgradeTo?: string;
}

export type Weather = 'clear' | 'rain';

export type VillagerMode =
  | 'idle'
  | 'saluting'
  | 'toWork'
  | 'working'
  | 'delivering'
  | 'toAltar'
  | 'sheltering'
  /** 举着急件跑去祭坛：台账有单在等人答复。 */
  | 'bearingDecree';

export type TaskStatus = 'assigned' | 'in_progress' | 'done' | 'failed';

/** 这条活的完成权归谁。沙盒自己造的活由计时器收尾；台账来的活只有台账能收。 */
export type TaskSource = 'sandbox' | 'ledger';

export interface TaskRecord {
  readonly id: string;
  readonly kind: TaskKind;
  readonly summary: string;
  /** 原地做的活没有铺子；台账的活等派下去才写。 */
  buildingId: string | null;
  readonly source: TaskSource;
  status: TaskStatus;
  readonly createdAt: number;
  completedAt: number | null;
  /**
   * 台账事件时间（epoch 秒，closed_at ?? updated_at）。只给 settled 用：
   * 交付/对账时 ×1000 写入 settled.completedAt；缺失保持 null，禁止本地时间顶替。
   */
  ledgerCompletedAt?: number | null;
  /**
   * 台账 blocked 单要请示的问题。只透传、只展示，世界绝不在内部回答；
   * 非 blocked 单（转 running / 完成 / 撤单）必须清成 null，不残留。
   * 这里保留完整原文，展示截断由 excerptBlockedQuestion 负责。
   */
  blockedQuestion: string | null;
  /** 接下这单的角色 id；没人接时 null。交付归属按它算。 */
  workerId: string | null;
}

/** 状态行/回执里展示要请示问题时最多显示多少个字符（按 Unicode 码点计）。 */
export const BLOCKED_QUESTION_LIMIT = 60;

/** 展示用截断：超过 60 字符加省略号；完整值仍留在 TaskRecord.blockedQuestion。 */
export function excerptBlockedQuestion(question: string | null): string {
  if (question === null) return '';
  const text = question.trim();
  if (text === '') return '';
  const points = Array.from(text);
  if (points.length <= BLOCKED_QUESTION_LIMIT) return text;
  return `${points.slice(0, BLOCKED_QUESTION_LIMIT).join('')}…`;
}

/**
 * 排队等待派发的一条神谕。她手上有活时新神谕不丢，先排在这里；
 * 队列只存在 Simulation 内，随快照暴露，保证固定步长下可复现。
 */
export interface PendingDispatch {
  /** 世界内自增 id（p1、p2…），不用时间戳/随机数。 */
  readonly id: string;
  readonly kind: TaskKind;
  readonly summary: string;
  /** 入队时刻（this.now）。 */
  readonly requestedAt: number;
  /** 只排神谕单；台账单走 tasks.assigned，天然排队且不可被神谕撤销。 */
  readonly source: 'oracle';
  /** 预留按角色分队列：当前单角色世界固定用 v0。 */
  readonly villagerId: string;
}

/** 每类活的时长与场地。短的原地做，长的进城。 */
export interface TaskProfile {
  readonly estimateMs: number;
  readonly venue: 'workshop' | 'in_place';
  readonly label: string;
}

export interface PlacedBuilding {
  readonly instanceId: string;
  readonly prefabId: string;
  readonly displayName: string;
  readonly zone: ZoneId;
  readonly origin: GridPoint;
  readonly footprint: { readonly w: number; readonly h: number };
  readonly door: GridPoint;
  readonly workSpot: GridPoint;
  readonly chimney: Vec2;
  readonly accent: WorkAccent;
  readonly silhouette: Silhouette;
  readonly boundTask: WorkshopTask | null;
  readonly palette: PrefabBlueprint['palette'];
  /**
   * 这栋楼是谁建的、为什么建。开局常驻的楼没有来源，记 null。
   *
   * 与摆件同一个理由：没有来源，过两天就没人知道它为什么在那儿。
   * 老档迁移来的楼也没有来源，读作"世界自带的"。
   */
  readonly sourceTaskId?: string | null;
  readonly sourceSummary?: string | null;
}

export type WorkstationActivity =
  | { readonly kind: 'idle' }
  | {
      readonly kind: 'operating';
      readonly taskId: string;
      readonly startedAt: number;
      readonly estimateMs: number;
      /**
       * 世界知不知道这条活还要多久。沙盒的活由计时器估，所以是 true；
       * 台账的活只有台账自己知道，世界不许编一个百分比，所以是 false。
       */
      readonly determinate: boolean;
    }
  | {
      /** 工具报错后的滑稽故障态，黑烟，不会自己恢复。 */
      readonly kind: 'overheating';
      readonly taskId: string;
      readonly since: number;
    }
  | { readonly kind: 'cooling'; readonly taskId: string; readonly until: number };

export interface WorkstationSnapshot {
  readonly buildingId: string;
  readonly activity: WorkstationActivity;
  /** 本次开工已经过去多久（毫秒）。只驱动烟雾浓淡。 */
  readonly elapsedMs: number;
  /**
   * 0..1。粗估进度，用于铺子上方那条细线；任务仍以状态机终态为准。
   * 台账的活是 null：世界不知道真实工作跑到哪了，就不画一条假的进度。
   */
  readonly progress: number | null;
}

/** 在建工地。期间不能接活，画成脚手架。 */
export interface ConstructionSnapshot {
  readonly buildingId: string;
  readonly displayName: string;
  readonly remainingMs: number;
  readonly totalMs: number;
}

/** 交付统计：台账交付与离线 Mock 交付分开计，不合并。 */
export interface DeliveryStats {
  readonly ledger: number;
  readonly mock: number;
}

/** 单个角色的交付归属：按人分账，Mock 与台账仍分开。 */
export interface VillagerDeliveries {
  readonly villagerId: string;
  readonly name: string;
  readonly ledger: number;
  readonly mock: number;
}

/**
 * 从郊野带回来的东西。按活类型分：查资料带卷轴、写代码带石料、
 * 整理文稿带纸。
 */
export type CarryKind = 'scroll' | 'stone' | 'paper';

/** 累积式改变：交付多了，世界多一件东西。不可花掉、不贬值。 */
export type PropKind = 'lamp' | 'tree' | 'banner';

export interface WorldProp {
  readonly id: string;
  readonly kind: PropKind;
  readonly at: GridPoint;
  readonly label: string;
  /**
   * 这个摆件从哪种活换来的。v1 存档迁移过来的老摆件没有来源，记 null；
   * 绘制层据此换外观，纸面据此写「这盏灯是哪一单换来的」。
   */
  readonly sourceTaskKind: TaskKind | null;
  /** 换来摆件的那条活的 id，没有来源时记 null。 */
  readonly sourceTaskId: string | null;
  /**
   * 换来摆件的那条活的摘要。
   *
   * 为什么抄一份：任务结算后会被 `removeTask` 删掉，`settled` 只留 id。
   * 只存 id 的话，纸面永远只能显示一个编号，说不出「那单爬虫」。
   * 老摆件没有来源，记 null。
   */
  readonly sourceSummary: string | null;
}

export interface VillagerSnapshot {
  /** 世界内稳定 id（v0 = 主角色）。渲染按它对齐多个实例。 */
  readonly id: string;
  readonly name: string;
  readonly mode: VillagerMode;
  readonly pos: Vec2;
  readonly facing: Facing;
  readonly visible: boolean;
  readonly taskId: string | null;
  readonly bubble: string | null;
  /** 没活干时她在做什么，给纸面状态行用。 */
  readonly routine: string | null;
  /** 下雨时渲染层给她打把伞；世界不判断"她淋不淋湿"。 */
  readonly umbrella: boolean;
  /**
   * 手上带着的东西。从郊野干完活回来时不为 null。
   * 纯视觉：它不影响任务结算，只是"跑了一趟，带了东西回来"的读感。
   */
  readonly carrying: CarryKind | null;
}

/**
 * 只做氛围的环境实体，不参与任务、不改交付统计。
 * 猫是第一个：它会自己走开，不永久占位。
 */
export interface Critter {
  readonly id: string;
  readonly kind: 'cat';
  readonly at: Vec2;
  /** 离开世界的逻辑时刻。到点自己走，不需要调用方清理。 */
  readonly leavesAt: number;
}

/**
 * 神谕石板上的一条：只记真正改变了世界的那句话，外加世界当时的回执。
 * 认不出的输入不进石板（那是回问，不是世界事件）。封顶见 ORACLE_SLAB_LIMIT。
 */
export interface OracleRecord {
  /** 玩家原话。 */
  readonly raw: string;
  /** 世界执行后的回执原文。 */
  readonly receipt: string;
  /** 逻辑时刻（sim.now），不是墙钟：石板要能确定性回放。 */
  readonly at: number;
}

/** 石板最多留多少条。超出丢最旧：再多就没人读了。 */
export const ORACLE_SLAB_LIMIT = 20;

export interface WorldSnapshot {
  readonly now: number;
  /** 兼容旧调用方：等于 villagers[0]（主角色）。 */
  readonly villager: VillagerSnapshot;
  /** 全部角色（2–3 个），渲染与状态行都读这里。 */
  readonly villagers: readonly VillagerSnapshot[];
  readonly buildings: readonly PlacedBuilding[];
  readonly workstations: readonly WorkstationSnapshot[];
  readonly constructions: readonly ConstructionSnapshot[];
  readonly tasks: readonly TaskRecord[];
  /** 排队等待派发的神谕（FIFO）。只展示/可撤销，不影响台账完成权。 */
  readonly pending: readonly PendingDispatch[];
  readonly props: readonly WorldProp[];
  /** 神谕石板：最近 N 条改变了世界的神谕，旧在前。 */
  readonly oracle: readonly OracleRecord[];
  /** 氛围实体（猫等）。不参与任何世界结算。 */
  readonly critters: readonly Critter[];
  /**
   * 迷雾：世界还没长出来的部分。
   * **不影响通行**，只压暗画面并拦住新建造——否则读档时迷雾里的建筑会变孤岛。
   */
  readonly fog: { readonly center: GridPoint; readonly revealedRadius: number };
  readonly roads: readonly GridPoint[];
  /**
   * 走出来的痕迹：人反复走过的地方。强度 2 起才出现在这里。
   * **不落档**——能从走动重新累积，存起来只会让存档无限膨胀。
   */
  readonly trails: readonly { readonly at: GridPoint; readonly strength: number }[];
  readonly altar: GridPoint;
  readonly events: readonly WorldEvent[];
  readonly weather: Weather;
  /** 交付积累的信仰点数与已达成的世界记忆数。 */
  readonly faith: { readonly points: number; readonly milestones: number };
  /** 交付统计：台账交付与离线 Mock 交付分开计，禁止合并成一个数。 */
  readonly deliveries: DeliveryStats;
  /** 按角色分账的交付归属（顺序同 villagers）；日报据此写「谁交付了几单」。 */
  readonly deliveriesByVillager: readonly VillagerDeliveries[];
  /** 上游台账的在线情况。离线时世界照常过日子，但不再谎报"没有活"。 */
  readonly ledger: {
    readonly connected: boolean;
    readonly lastSyncAt: number | null;
    /** 当前按离线 Mock 运行（没接台账或已断线）。状态行据此显式标注。 */
    readonly mock: boolean;
    /** 最近一次 REST 快照是否完整；false 表示"台账可能未取全"。 */
    readonly complete: boolean;
    /** 读档后首个成功 sync 之前为 false；此时台账单只读展示、不驱动。 */
    readonly reconciled: boolean;
  };
}

/** 神谕解析结果。世界只认这几种动词，别的都落到 unknown。 */
export type OracleIntent =
  | { readonly kind: 'dispatch'; readonly taskKind: TaskKind; readonly summary: string }
  | { readonly kind: 'complete' }
  | { readonly kind: 'cancelPending' }
  | { readonly kind: 'unknown'; readonly raw: string };

export interface CommandResult {
  readonly ok: boolean;
  readonly message: string;
}

export interface WorldEvent {
  readonly at: number;
  readonly tone: 'oracle' | 'work' | 'done' | 'info' | 'warn';
  readonly text: string;
}
