/** 台账客户端：把主服务的督办台账接进世界。
 *
 * 运输层只做三件事：取数据、校验形状、报告在线状态。它不认识"铺子"，
 * 也不判断"该不该派活"——那是 projection 与 simulation 的事。
 *
 * 两条来源：
 *   - 初次连接用 REST 拉一份全量（GET /api/executive/commissions）；
 *   - 之后靠 WS 的 commission_update 增量推送。断线就退回轮询兜底。
 */

import { parseAgentRow, parseCommissionRow, project, type AgentRow, type CommissionRow } from './projection.ts';

/** 上报给世界的台账视图。 */
export interface LedgerView {
  readonly tasks: readonly {
    readonly commissionId: string;
    readonly kind: import('./types.ts').TaskKind;
    readonly title: string;
    readonly agentName: string;
  }[];
  readonly finished: readonly string[];
  readonly connected: boolean;
}

export interface LedgerClientOptions {
  /** 轮询兜底间隔。WS 断了之后用它续命。 */
  readonly pollMs?: number;
  /** 每次视图变化回调一次；世界把结果排进命令队列。 */
  readonly onChange: (view: LedgerView) => void;
  readonly fetchImpl?: typeof fetch;
  readonly now?: () => number;
}

const DEFAULT_POLL_MS = 4000;
const COMMISSIONS_URL = '/api/executive/commissions';

export class LedgerClient {
  private readonly byId = new Map<string, CommissionRow>();
  private readonly agents: AgentRow[] = [];
  private readonly onChange: (view: LedgerView) => void;
  private readonly pollMs: number;
  private readonly fetchImpl: typeof fetch;
  private ws: WebSocket | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private connected = false;
  private disposed = false;

  constructor(options: LedgerClientOptions) {
    this.onChange = options.onChange;
    this.pollMs = options.pollMs ?? DEFAULT_POLL_MS;
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
  }

  /** 起一条连接：先 REST 全量，再挂 WS 增量。任何一步失败都不抛，只标离线。 */
  async start(): Promise<void> {
    await this.refresh();
    this.openSocket();
    this.timer = setInterval(() => {
      // WS 活着时不必轮询；它断了这条兜底会把它拉回来。
      if (!this.wsAlive()) void this.refresh();
    }, this.pollMs);
  }

  stop(): void {
    this.disposed = true;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.ws?.close();
    this.ws = null;
    this.connected = false;
  }

  /** 全量刷新。失败只标离线，不抛——世界要能照常过日子。 */
  async refresh(): Promise<void> {
    if (this.disposed) return;
    try {
      const [commissions, agents] = await Promise.all([
        this.getJson(COMMISSIONS_URL),
        this.getJson('/api/executive/adapters'),
      ]);
      this.absorbCommissions(commissions);
      this.absorbAgents(agents);
      this.connected = true;
    } catch {
      this.connected = false;
    }
    this.publish();
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
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  private openSocket(): void {
    if (this.disposed || typeof WebSocket === 'undefined') return;
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
    this.ws = socket;
    socket.addEventListener('message', (event) => {
      let payload: unknown;
      try {
        payload = JSON.parse(String(event.data));
      } catch {
        return;
      }
      if (typeof payload !== 'object' || payload === null) return;
      const message = payload as Record<string, unknown>;
      if (message.type !== 'commission_update') return;
      this.applyPush(message.commission);
    });
    socket.addEventListener('close', () => {
      this.connected = false;
      this.publish();
      if (this.disposed) return;
      // 重连交给下一次轮询：refresh 成功就说明 REST 还活着。
      setTimeout(() => {
        if (!this.disposed && !this.wsAlive()) this.openSocket();
      }, this.pollMs);
    });
    socket.addEventListener('error', () => {
      this.connected = false;
    });
  }

  private async getJson(url: string): Promise<unknown> {
    const response = await this.fetchImpl(url, { headers: { accept: 'application/json' } });
    if (!response.ok) throw new Error(`台账 ${url} 返回 ${response.status}`);
    return response.json();
  }

  private absorbCommissions(raw: unknown): void {
    if (typeof raw !== 'object' || raw === null) return;
    const items = (raw as { items?: unknown }).items;
    if (!Array.isArray(items)) return;
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

  private absorbAgents(raw: unknown): void {
    if (typeof raw !== 'object' || raw === null) return;
    const list = (raw as { agents?: unknown }).agents;
    if (!Array.isArray(list)) return;
    this.agents.length = 0;
    for (const item of list) {
      try {
        this.agents.push(parseAgentRow(item));
      } catch {
        continue;
      }
    }
  }

  private publish(): void {
    const projected = project({ commissions: [...this.byId.values()], agents: this.agents });
    this.onChange({
      tasks: projected.tasks.map((task) => ({
        commissionId: task.commissionId,
        kind: task.kind,
        title: task.title,
        agentName: task.agentName,
      })),
      finished: projected.finished.map((task) => task.commissionId),
      connected: this.connected,
    });
  }
}
