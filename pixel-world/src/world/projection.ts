/** 台账 → 世界事实。这是唯一的映射点，纯函数，可离线断言。
 *
 * 上游是 `GET /api/executive/commissions` 的 items（协议 §19）。世界不认识
 * commission / agent_id 这些词，只认识「哪栋楼在干活、她手上有没有活」。
 * 换台账字段只改这里，世界与渲染一行不动。
 */

import type { TaskKind, WorkshopTask } from './types.ts';

/** 台账里一条督办的最小形状。多余字段一律忽略，不猜。 */
export interface CommissionRow {
  readonly id: string;
  readonly title: string;
  readonly agent_id: string;
  readonly status: string;
  readonly step_idx: number;
  readonly blocked_question: string;
  /** 台账事件时间（epoch 秒）：`closed_at ?? updated_at`；两个字段都缺时为 null。 */
  readonly completedAt: number | null;
}

/** 花名册里的一个 Worker，用来把 agent_id 归到某一类活。 */
export interface AgentRow {
  readonly id: string;
  readonly name: string;
  readonly strengths: readonly string[];
}

export interface ProjectionInput {
  readonly commissions: readonly CommissionRow[];
  readonly agents: readonly AgentRow[];
}

/** 世界侧看到的一条活。 */
export interface WorldTask {
  readonly commissionId: string;
  readonly kind: TaskKind;
  readonly title: string;
  readonly agentId: string;
  readonly agentName: string;
  readonly status: string;
  readonly blockedQuestion: string;
  /**
   * 台账事件时间（epoch 秒，来自 `closed_at ?? updated_at`）。settled 的
   * completedAt 只认它（×1000），缺失时必须 null——不许拿本地时间顶替。
   */
  readonly completedAt: number | null;
  /** 台账已判完成，等她收尾交付；与"还在干"分开，世界才能正确收尾。 */
  readonly finished: boolean;
}

export interface Projection {
  readonly tasks: readonly WorldTask[];
  /** 活动清单按优先级排好的顺序（blocked→running→auditing→其它，同级 id 稳定）。 */
  readonly ordered: readonly WorldTask[];
  /** 需要她立刻去做的那一条（= ordered[0]），保留给老调用方。 */
  readonly active: WorldTask | null;
  /** 台账里已经收工、世界还没交付的那几条。 */
  readonly finished: readonly WorldTask[];
  /**
   * 初审过了、等老板点头的那几条。
   *
   * 与 `finished` 分开放：`finished` 是「成了」，这些是「还差你一句」。
   * 曾经把 ready_to_report 归进 finished，世界于是在老板验收前就走完交付、
   * 加了信仰点。混在一起的代价是错的奖励，所以宁可多一个字段。
   */
  readonly awaiting: readonly WorldTask[];
}

/** 能力关键词 → 三类活。顺序即优先级，先命中的算数。 */
const KIND_RULES: readonly { readonly kind: WorkshopTask; readonly words: readonly string[] }[] = [
  {
    kind: 'code',
    words: ['代码', '实现', '脚本', '重构', '命令', '修复', '测试', '文件', '写入', '多步', '长任务'],
  },
  { kind: 'research', words: ['检索', '查找', '搜索', '调研', '比对', '资料', '汇总', '看看有没有'] },
  { kind: 'writing', words: ['写作', '整理', '标题', '摘要', '便笺', '笔记', '文案', '改写', '润色', '文档', '表格', '汇报', '归纳', '总结'] },
];

/** 台账状态 → 该不该让她动。 */
const ACTIVE_STATUSES = new Set(['briefing', 'running', 'blocked', 'auditing']);
/**
 * 只有台账终态才算「收工」。
 *
 * 曾经这里含 `ready_to_report`，导致世界在老板验收前就走完交付、加信仰点。
 * 这与 ADR_AGENT_BRIDGE §4.3 自己写的「待验收——不是成功」冲突：那一格明确
 * 说 ready_to_report 只是初审过了、等老板点头，而 `done` 才是唯一终态。
 *
 * 所以 `ready_to_report` 现在只算「她不在这单上了」，不算「这单成了」：
 * 它既不在 ACTIVE（不再开工），也不在 FINISHED（不算交付）。
 */
const FINISHED_STATUSES = new Set(['done']);

/**
 * 初审过了、等老板点头。
 *
 * 它既不是「她还在这单上」（不派活），也不是「成了」（不加信仰、不算交付）。
 * 世界要知道它，是因为祭坛上得摆一只宝箱——否则老板根本看不到有东西等他批。
 */
const AWAITING_STATUSES = new Set(['ready_to_report']);

export function project(input: ProjectionInput): Projection {
  const agents = new Map(input.agents.map((agent) => [agent.id, agent]));
  const tasks: WorldTask[] = [];
  const finished: WorldTask[] = [];
  const awaiting: WorldTask[] = [];
  for (const commission of input.commissions) {
    const active = ACTIVE_STATUSES.has(commission.status);
    const done = FINISHED_STATUSES.has(commission.status);
    const waiting = AWAITING_STATUSES.has(commission.status);
    // 三个集合互斥且覆盖全部七种状态：不在任何一个里的状态（如 drafting）世界不认识，
    // 直接跳过。别加第四道兜底——多一层冗余会让单点变异测不出来。
    if (!active && !done && !waiting) continue;
    const agent = agents.get(commission.agent_id);
    const task: WorldTask = {
      commissionId: commission.id,
      kind: kindForAgent(agent, commission),
      title: commission.title,
      agentId: commission.agent_id,
      agentName: agent?.name ?? commission.agent_id,
      status: commission.status,
      blockedQuestion: commission.blocked_question,
      completedAt: commission.completedAt,
      finished: done,
    };
    if (done) finished.push(task);
    else if (waiting) awaiting.push(task);
    else tasks.push(task);
  }
  const ordered = orderActive(tasks);
  return { tasks, ordered, active: ordered[0] ?? null, finished, awaiting };
}

/**
 * 活动清单的优先级顺序：blocked（要请示）→ running → auditing → 其它，
 * 同级按 commissionId 稳定排序。世界按这个顺序派活，不再靠台账返回次序碰运气。
 */
export function orderActive(tasks: readonly WorldTask[]): readonly WorldTask[] {
  const rank = (task: WorldTask): number => {
    if (task.status === 'blocked') return 0;
    if (task.status === 'running') return 1;
    if (task.status === 'auditing') return 2;
    return 3;
  };
  return [...tasks].sort(
    (a, b) => rank(a) - rank(b) || a.commissionId.localeCompare(b.commissionId),
  );
}

/** 优先级最高的那一条；等价于 orderActive(tasks)[0]。 */
export function pickActive(tasks: readonly WorldTask[]): WorldTask | null {
  return orderActive(tasks)[0] ?? null;
}

/** 用花名册的能力关键词归类。认不出的 Agent 落到写作，因为它是唯一不碰代码的默认档。 */
export function kindForAgent(agent: AgentRow | undefined, commission: CommissionRow): TaskKind {
  const haystack = [
    ...(agent?.strengths ?? []),
    agent?.name ?? '',
    commission.title,
  ].join(' ');
  for (const rule of KIND_RULES) {
    if (rule.words.some((word) => haystack.includes(word))) return rule.kind;
  }
  return 'writing';
}

/** 解析上游载荷。字段缺失一律拒绝，不用默认值把坏数据蒙混进来。 */
export function parseCommissionRow(raw: unknown): CommissionRow {
  if (typeof raw !== 'object' || raw === null) throw new Error('督办不是对象');
  const row = raw as Record<string, unknown>;
  const str = (key: string, fallback = ''): string => {
    const value = row[key];
    if (value === undefined || value === null) return fallback;
    if (typeof value !== 'string') throw new Error(`${key} 必须是字符串`);
    return value;
  };
  /** epoch 秒：数字才算数；null/缺失→null（协议 §19 的 REAL 字段）。 */
  const epoch = (key: string): number | null => {
    const value = row[key];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const id = str('id');
  if (id === '') throw new Error('督办缺 id');
  const step = row.step_idx;
  return {
    id,
    title: str('title'),
    agent_id: str('agent_id'),
    status: str('status'),
    step_idx: typeof step === 'number' && Number.isFinite(step) ? step : 0,
    blocked_question: str('blocked_question'),
    // ADR_SAVE §2.2：台账事件时间优先 closed_at，退回 updated_at；都缺则 null。
    completedAt: epoch('closed_at') ?? epoch('updated_at'),
  };
}

/** 解析花名册条目：新 /roster 用 id，旧 /adapters 用 agent_id；strengths 缺了就留空。 */
export function parseAgentRow(raw: unknown): AgentRow {
  if (typeof raw !== 'object' || raw === null) throw new Error('花名册条目不是对象');
  const row = raw as Record<string, unknown>;
  const id = typeof row.id === 'string' && row.id !== ''
    ? row.id
    : typeof row.agent_id === 'string' && row.agent_id !== ''
      ? row.agent_id
      : '';
  if (id === '') throw new Error('花名册条目缺 id');
  const strengths = Array.isArray(row.strengths)
    ? row.strengths.filter((item): item is string => typeof item === 'string')
    : [];
  return { id, name: typeof row.name === 'string' ? row.name : id, strengths };
}
