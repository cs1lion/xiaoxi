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

/** 神谕能派的三类活；每一类绑定一种铺子。 */
export type TaskKind = 'code' | 'research' | 'writing';

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
  readonly boundTask: TaskKind;
  readonly palette: {
    readonly body: number;
    readonly roof: number;
    readonly trim: number;
    readonly window: number;
  };
  readonly upgradeTo?: string;
}

export type VillagerMode =
  | 'idle'
  | 'saluting'
  | 'toWork'
  | 'working'
  | 'delivering'
  | 'toAltar';

export type TaskStatus = 'assigned' | 'in_progress' | 'done';

export interface TaskRecord {
  readonly id: string;
  readonly kind: TaskKind;
  readonly summary: string;
  readonly buildingId: string;
  status: TaskStatus;
  readonly createdAt: number;
  completedAt: number | null;
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
  readonly boundTask: TaskKind;
  readonly palette: PrefabBlueprint['palette'];
}

export type WorkstationActivity =
  | { readonly kind: 'idle' }
  | {
      readonly kind: 'operating';
      readonly taskId: string;
      readonly startedAt: number;
      readonly estimateMs: number;
    }
  | { readonly kind: 'cooling'; readonly taskId: string; readonly until: number };

export interface WorkstationSnapshot {
  readonly buildingId: string;
  readonly activity: WorkstationActivity;
  /** 本次开工已经过去多久（毫秒）。只驱动烟雾浓淡。 */
  readonly elapsedMs: number;
  /** 0..1。粗估进度，用于铺子上方那条细线；任务仍以状态机终态为准。 */
  readonly progress: number;
}

export interface VillagerSnapshot {
  readonly name: string;
  readonly mode: VillagerMode;
  readonly pos: Vec2;
  readonly facing: Facing;
  readonly visible: boolean;
  readonly taskId: string | null;
  readonly bubble: string | null;
}

export interface WorldSnapshot {
  readonly now: number;
  readonly villager: VillagerSnapshot;
  readonly buildings: readonly PlacedBuilding[];
  readonly workstations: readonly WorkstationSnapshot[];
  readonly tasks: readonly TaskRecord[];
  readonly altar: GridPoint;
  readonly events: readonly WorldEvent[];
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
