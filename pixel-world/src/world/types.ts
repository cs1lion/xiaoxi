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
  | 'sheltering';

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

/** 累积式改变：交付多了，世界多一件东西。不可花掉、不贬值。 */
export type PropKind = 'lamp' | 'tree' | 'banner';

export interface WorldProp {
  readonly id: string;
  readonly kind: PropKind;
  readonly at: GridPoint;
  readonly label: string;
}

export interface VillagerSnapshot {
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
}

export interface WorldSnapshot {
  readonly now: number;
  readonly villager: VillagerSnapshot;
  readonly buildings: readonly PlacedBuilding[];
  readonly workstations: readonly WorkstationSnapshot[];
  readonly constructions: readonly ConstructionSnapshot[];
  readonly tasks: readonly TaskRecord[];
  readonly props: readonly WorldProp[];
  readonly roads: readonly GridPoint[];
  readonly altar: GridPoint;
  readonly events: readonly WorldEvent[];
  readonly weather: Weather;
  /** 交付积累的信仰点数与已达成的世界记忆数。 */
  readonly faith: { readonly points: number; readonly milestones: number };
  /** 上游台账的在线情况。离线时世界照常过日子，但不再谎报"没有活"。 */
  readonly ledger: { readonly connected: boolean; readonly lastSyncAt: number | null };
}

/** 神谕解析结果。世界只认这几种动词，别的都落到 unknown。 */
export type OracleIntent =
  | { readonly kind: 'dispatch'; readonly taskKind: TaskKind; readonly summary: string }
  | { readonly kind: 'complete' }
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
