/** 台账客户端：把主服务的督办台账接进世界。
 *
 * 运输层只做三件事：取数据、校验形状、报告在线状态。它不认识"铺子"，
 * 也不判断"该不该派活"——那是 projection 与 simulation 的事。
 *
 * 两条来源：
 *   - 初次连接用 REST 拉一份全量（GET /api/executive/commissions）；
 *   - 之后靠 WS 的 commission_update 增量推送。断线先按指数退避重连，
 *     每 4s 的 REST 对账保留作兜底——两者互不替代。
 */

import { parseAgentRow, parseCommissionRow, project, type AgentRow, type CommissionRow } from './projection.ts';

/** 上报给世界的台账视图。 */
export interface LedgerView {
  readonly tasks: readonly {
    readonly commissionId: string;
    readonly kind: import('./types.ts').TaskKind;
    readonly title: string;
    readonly agentName: string;
    /** 台账状态。世界只关心是不是 blocked（要请示）。 */
    readonly status: string;
    /** blocked 单要请示的问题；空串表示没有。只透传，世界不回答。 */
    readonly blockedQuestion: string;
    /** 台账事件时间（epoch 秒，closed_at ?? updated_at）；缺失 null。settled 只认它。 */
    readonly completedAt?: number | null;
  }[];
  readonly finished: readonly string[];
  /** 收工条目的台账事件时间（epoch 秒）；settled.completedAt 的唯一来源，缺就 null。 */
  readonly finishedAt: readonly {
    readonly commissionId: string;
    readonly completedAt: number | null;
  }[];
  readonly connected: boolean;
  /** 这份快照是否完整（REST 没取满 limit）。false 时世界不得把"不在快照里"当撤单。 */
  readonly complete: boolean;
  /** 活动督办按优先级排好的 commissionId（blocked→running→auditing→其它，同级稳定）。 */
  readonly order: readonly string[];
}

export interface LedgerClientOptions {
  /** 轮询兜底间隔。WS 断了之后用它续命。 */
  readonly pollMs?: number;
  /** 每次视图变化回调一次；世界把结果排进命令队列。 */
  readonly onChange: (view: LedgerView) => void;
  readonly fetchImpl?: typeof fetch;
  readonly now?: () => number;
  /** 测试注入：页面地址。默认取 window.location。 */
  readonly location?: { readonly protocol: string; readonly host: string };
  /** 测试注入：定时器。默认用全局实现。 */
  readonly timers?: LedgerTimers;
  /** 测试注入：WebSocket 工厂。默认 new WebSocket(url)。 */
  readonly socketFactory?: SocketFactory;
}

const DEFAULT_POLL_MS = 4000;
/** WS 在线时也每隔这么久拉一次 REST 对账：增量可能漏帧（协议 §19 不补发）。 */
const RECONCILE_MS = 30_000;
/** 单次请求的超时；超时按离线处理，别让世界卡在一次挂住的 fetch 上。 */
const REQUEST_TIMEOUT_MS = 5000;
/** 服务端 MAX_LIST=100。取满说明可能还有更多，快照不能当"全量"用。 */
const COMMISSIONS_LIMIT = 100;
/** 重连退避：500ms 起跳，每次 ×2，封顶 5s。 */
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 5000;
/** WebSocket.OPEN 的数值。用常量比较，注入的假连接也能说"我开着"。 */
const WS_OPEN = 1;
const COMMISSIONS_URL = `/api/executive/commissions?limit=${COMMISSIONS_LIMIT}`;
/** 新增的只读花名册端点（带 strengths）；旧服务没有这条，会回退下面的 adapters。 */
const ROSTER_URL = '/api/executive/roster';
const ADAPTERS_URL = '/api/executive/adapters';

/** 第 n 次失败后的重连等待（n 从 0 起）：500 / 1000 / 2000 / 4000 / 5000 / 5000 … */
export function reconnectDelay(failures: number): number {
  const step = Number.isFinite(failures) ? Math.max(0, Math.floor(failures)) : 0;
  return Math.min(RECONNECT_BASE_MS * 2 ** step, RECONNECT_MAX_MS);
}

/** WS 连接只需要这四件事；生产由浏览器 WebSocket 实现，测试可注入假实现。 */
export interface LedgerSocket {
  readonly readyState: number;
  addEventListener(type: string, listener: (event: unknown) => void): void;
  close(): void;
}

export type SocketFactory = (url: string) => LedgerSocket;

/** 定时器的最小面，测试注入假时钟；句柄对调用方不透明。 */
export interface LedgerTimers {
  setTimeout(handler: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(handler: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

const DEFAULT_TIMERS: LedgerTimers = {
  setTimeout: (handler, ms) => globalThis.setTimeout(handler, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
  setInterval: (handler, ms) => globalThis.setInterval(handler, ms),
  clearInterval: (handle) => globalThis.clearInterval(handle as ReturnType<typeof setInterval>),
};

const DEFAULT_SOCKETS: SocketFactory = (url) => new WebSocket(url) as unknown as LedgerSocket;

export class LedgerClient {
  private readonly byId = new Map<string, CommissionRow>();
  private readonly agents: AgentRow[] = [];
  private readonly onChange: (view: LedgerView) => void;
  private readonly pollMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly location: { readonly protocol: string; readonly host: string };
  private readonly timers: LedgerTimers;
  private readonly createSocket: SocketFactory;
  private ws: LedgerSocket | null = null;
  private pollTimer: unknown | null = null;
  /** WS 在线时也生效的慢对账定时器（30s）。 */
  private reconcileTimer: unknown | null = null;
  private reconnectTimer: unknown | null = null;
  /** 连续失败次数，只影响下一次等待时长；连上即清零。 */
  private reconnectFailures = 0;
  private connected = false;
  /** 最近一次 REST 快照是否完整；与 WS 增量无关。 */
  private complete = true;
  /** 同一时刻只允许一次刷新在途，避免慢请求叠成山。 */
  private inFlight: Promise<void> | null = null;
  private disposed = false;

  constructor(options: LedgerClientOptions) {
    this.onChange = options.onChange;
    this.pollMs = options.pollMs ?? DEFAULT_POLL_MS;
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
    this.location = options.location ?? window.location;
    this.timers = options.timers ?? DEFAULT_TIMERS;
    this.createSocket = options.socketFactory ?? DEFAULT_SOCKETS;
  }

  /** 起一条连接：先 REST 全量，再挂 WS 增量。任何一步失败都不抛，只标离线。 */
  async start(): Promise<void> {
    await this.refresh();
    this.openSocket();
    this.pollTimer = this.timers.setInterval(() => {
      // WS 活着时不必轮询；它断了这条兜底会把它拉回来。
      if (!this.wsAlive()) void this.refresh();
    }, this.pollMs);
    // 增量可能漏帧（协议 §19 不补发）：WS 在线也定期拿 REST 对账，以最新快照为准。
    this.reconcileTimer = this.timers.setInterval(() => {
      void this.refresh();
    }, RECONCILE_MS);
  }

  stop(): void {
    this.disposed = true;
    if (this.pollTimer !== null) this.timers.clearInterval(this.pollTimer);
    this.pollTimer = null;
    if (this.reconcileTimer !== null) this.timers.clearInterval(this.reconcileTimer);
    this.reconcileTimer = null;
    if (this.reconnectTimer !== null) this.timers.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.ws?.close();
    this.ws = null;
    this.connected = false;
  }

  /** 全量刷新。失败只标离线，不抛——世界要能照常过日子。 */
  async refresh(): Promise<void> {
    if (this.disposed) return;
    if (this.inFlight !== null) return this.inFlight;
    const task = this.refreshOnce();
    this.inFlight = task;
    try {
      await task;
    } finally {
      this.inFlight = null;
    }
  }

  /** 单次刷新：commissions 决定在线与否，花名册只做尽力而为。 */
  private async refreshOnce(): Promise<void> {
    if (this.disposed) return;
    try {
      const commissions = await this.getJson(COMMISSIONS_URL);
      this.absorbCommissions(commissions);
      this.connected = true;
    } catch {
      this.connected = false;
      this.publish();
      return;
    }
    // 花名册只影响"活该去哪个铺子"，拉不到不能把整个世界判离线。
    await this.refreshAgents();
    this.publish();
  }

  /**
   * Agent 花名册：优先新增的只读 /roster（带 strengths），旧服务 404 时回退
   * /adapters（只有 id/name，没有 strengths）。两者都失败就保留上一份。
   */
  private async refreshAgents(): Promise<void> {
    for (const url of [ROSTER_URL, ADAPTERS_URL]) {
      try {
        if (this.absorbAgents(await this.getJson(url))) return;
      } catch {
        // 404 / 网络失败：换下一条来源。
      }
    }
  }

  /** WS 单条增量。形状不认识就丢掉，绝不半信半疑地写进世界。 */
  applyPush(raw: unknown): void {
    try {
      const row = parseCommissionRow(raw);
      this.byId.set(row.id, row);
      this.connected = true;
      this.publish();
    } catch {
      // 坏帧丢掉，不影响已经在册的督办。
    }
  }

  private wsAlive(): boolean {
    return this.ws !== null && this.ws.readyState === WS_OPEN;
  }

  private openSocket(): void {
    if (this.disposed) return;
    const scheme = this.location.protocol === 'https:' ? 'wss' : 'ws';
    let socket: LedgerSocket;
    try {
      socket = this.createSocket(`${scheme}://${this.location.host}/ws`);
    } catch {
      // 连接都建不出来（非浏览器环境）：交给下一轮退避再试。
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;
    socket.addEventListener('open', () => {
      if (this.ws !== socket) return;
      // 连接成功立即重置退避：下次断线重新从 500ms 起跳。
      this.reconnectFailures = 0;
      // 断线期间的增量不会补发（协议 §19），重连后补一次 REST 对账。
      // 首次连接 start() 已经拉过全量，这里只在还没在线时补，避免重复请求。
      if (!this.connected) void this.refresh();
    });
    socket.addEventListener('message', (event) => {
      const data = (event as { readonly data?: unknown } | null)?.data;
      if (typeof data !== 'string') return;
      let payload: unknown;
      try {
        payload = JSON.parse(data);
      } catch {
        return;
      }
      if (typeof payload !== 'object' || payload === null) return;
      const message = payload as Record<string, unknown>;
      if (message.type !== 'commission_update') return;
      this.applyPush(message.commission);
    });
    socket.addEventListener('close', () => {
      if (this.ws !== socket) return; // 旧连接迟到的 close 不干扰新连接
      this.ws = null;
      this.connected = false;
      this.publish();
      if (this.disposed) return;
      this.scheduleReconnect();
    });
    socket.addEventListener('error', () => {
      if (this.ws !== socket) return;
      this.connected = false;
    });
  }

  /** 指数退避重连：已有一次等待就不叠加，避免定时器泄漏。 */
  private scheduleReconnect(): void {
    if (this.disposed || this.reconnectTimer !== null) return;
    const delay = reconnectDelay(this.reconnectFailures);
    this.reconnectFailures += 1;
    this.reconnectTimer = this.timers.setTimeout(() => {
      this.reconnectTimer = null;
      if (this.disposed) return;
      this.openSocket();
    }, delay);
  }

  /** 带 5s 超时的 JSON 请求：超时按失败处理，交给上层标离线。 */
  private async getJson(url: string): Promise<unknown> {
    const controller = new AbortController();
    const timer = this.timers.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await this.fetchImpl(url, {
        headers: { accept: 'application/json' },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`台账 ${url} 返回 ${response.status}`);
      return response.json();
    } finally {
      this.timers.clearTimeout(timer);
    }
  }

  private absorbCommissions(raw: unknown): void {
    if (typeof raw !== 'object' || raw === null) return;
    const items = (raw as { items?: unknown }).items;
    if (!Array.isArray(items)) return;
    // 条数取满 limit 说明可能还有下一页：这份快照不完整，不能拿它判"撤单"。
    this.complete = items.length < COMMISSIONS_LIMIT;
    // 整份替换：REST 是全量，留着已删的督办会让世界挂着一单不存在的活。
    this.byId.clear();
    for (const item of items) {
      try {
        const row = parseCommissionRow(item);
        this.byId.set(row.id, row);
      } catch {
        // 单条坏数据跳过，不连累其它条。
      }
    }
  }

  /** 认两种信封：/roster 的 items 与 /adapters 的 agents。返回是否吃到了清单。 */
  private absorbAgents(raw: unknown): boolean {
    if (typeof raw !== 'object' || raw === null) return false;
    const envelope = raw as { items?: unknown; agents?: unknown };
    const list = Array.isArray(envelope.items) ? envelope.items : envelope.agents;
    if (!Array.isArray(list)) return false;
    const next: AgentRow[] = [];
    for (const item of list) {
      try {
        next.push(parseAgentRow(item));
      } catch {
        continue;
      }
    }
    this.agents.length = 0;
    this.agents.push(...next);
    return true;
  }

  private publish(): void {
    const projected = project({ commissions: [...this.byId.values()], agents: this.agents });
    this.onChange({
      tasks: projected.tasks.map((task) => ({
        commissionId: task.commissionId,
        kind: task.kind,
        title: task.title,
        agentName: task.agentName,
        status: task.status,
        blockedQuestion: task.blockedQuestion,
        completedAt: task.completedAt,
      })),
      finished: projected.finished.map((task) => task.commissionId),
      finishedAt: projected.finished.map((task) => ({
        commissionId: task.commissionId,
        completedAt: task.completedAt,
      })),
      connected: this.connected,
      complete: this.complete,
      order: projected.ordered.map((task) => task.commissionId),
    });
  }
}
