/* 契约 v2 · §25 事件协议 v2 的前端消费层（纯逻辑，零 DOM）。
 *
 * 三块职责，全部可被 node 单测（web/events-v2.test.mjs）直接驱动：
 *   createEventStream()  连接级信封归约：id 去重、seq 保序、ts 原样透传（服务端时钟）
 *   createOpsState()     九类事件 -> ops 视图状态机的归约（乱序不回退画面）
 *   buildCommand()       §25.3 五个前端命令的构造与必填校验（request_id 幂等键）
 *
 * 名字的单一真相源在 server/contract/events_v2.py，由 check_protocol 第 12 类
 * 与协议文档比对；前端这份常量从本模块取，别处不得再写九类事件字面量。
 *
 * 为什么 seq 保序要带「跳缺口」：§25.2 的 at-most-once（TOOL_EXECUTION /
 * AGENT_PRESENCE）允许服务端丢帧，缺口可能永远等不来。无限等 = 画面卡死在
 * 过去，比按序跳过更违背「画面等于真实状态」。窗口见 REORDER_WINDOW。
 */

/* §25.2 九类事件。顺序与 events_v2.py 的 EVENT_TYPES 一致，便于对读。 */
export const V2_EVENT_TYPES = Object.freeze([
  "TASK_STATE_CHANGE",
  "STEP_STATE_CHANGE",
  "CONTRACT_PROPOSED",
  "ASK_USER",
  "TOOL_EXECUTION",
  "VERIFICATION_REPORT",
  "DELIVERY_EFFECT",
  "MEMORY_UPDATE",
  "AGENT_PRESENCE",
]);

/* §25.2 标「至多一次（可丢）」的两类：丢了不追补，是缺口跳过的正当理由。 */
export const AT_MOST_ONCE_TYPES = Object.freeze(["TOOL_EXECUTION", "AGENT_PRESENCE"]);

/* §25.3 五个前端命令。与事件成对：只发事件不接命令，前端按钮点了没反应。 */
export const V2_COMMAND_TYPES = Object.freeze([
  "CONFIRM_CONTRACT",
  "ANSWER",
  "CANCEL_TASK",
  "APPROVE_SIDE_EFFECT",
  "RETRY_STEP",
]);

/* §25.3 每个命令的必填字段（request_id 由 buildCommand 自己生成/复用）。 */
const COMMAND_REQUIRED = Object.freeze({
  CONFIRM_CONTRACT: ["task_id", "contract_id", "version", "confirmed_slots"],
  ANSWER: ["task_id", "answer"],
  CANCEL_TASK: ["task_id"],
  APPROVE_SIDE_EFFECT: ["task_id", "effect_id"],
  RETRY_STEP: ["task_id", "step_id"],
});

/* §25.2 AGENT_PRESENCE 的四态。与既有 turn_started/thinking/done 词汇对齐。 */
export const PRESENCE_STATES = Object.freeze(["idle", "listening", "thinking", "speaking"]);

/* id 去重表的 FIFO 上限：单连接一次任务的生命周期远用不满，防的是长跑页面。 */
const MAX_SEEN_IDS = 512;
/* 乱序重排窗口：超过即认定缺口里的帧已被丢（at-most-once），按序跳过。 */
const REORDER_WINDOW = 32;
/* 各展示列表的上限：ops 视图是给老板看的，不是日志面板。 */
const MAX_ASKS = 24;
const MAX_TOOLS_PER_TASK = 12;
const MAX_MEMORY = 24;

const EMPTY = Object.freeze([]);

/* §25.1 信封校验。可选字段（task_id/step_id/session_id/turn_id）不在此查——
 * 缺它们只影响归属展示，不该把整条事件扔掉（「未知字段忽略」的另一半：
 * 已知字段的形状必须够硬，否则去重与保序都会建立在垃圾上）。 */
export function isV2Frame(value) {
  return !!value && typeof value === "object" && value.v === 2
    && typeof value.type === "string" && V2_EVENT_TYPES.includes(value.type)
    && typeof value.id === "string" && value.id.length > 0 && value.id.length <= 256
    && Number.isInteger(value.seq) && value.seq >= 0
    && typeof value.ts === "number" && Number.isFinite(value.ts);
}

/* ---------- 连接级信封归约 ---------- */

export function createEventStream({ maxSeenIds = MAX_SEEN_IDS, reorderWindow = REORDER_WINDOW } = {}) {
  const seen = new Set();
  const pending = new Map(); // seq -> frame，等缺口补上后按序释放
  let lastSeq = null;        // 已按序交付的最大 seq；null = 这条连接还没交付过

  function deliverNextRuns(out) {
    while (pending.has(lastSeq + 1)) {
      lastSeq += 1;
      out.push({ frame: pending.get(lastSeq), late: false });
      pending.delete(lastSeq);
    }
  }

  return {
    /** 收一条原始帧，返回本次按序交付的 {frame, late} 列表。
     *  重复 id / 未知 type / 形状不合 §25.1 的帧：整条忽略，返回空。 */
    accept(raw) {
      if (!isV2Frame(raw)) return EMPTY;
      if (seen.has(raw.id)) return EMPTY; // §25.5 至少一次投递 -> 按 id 去重
      seen.add(raw.id);
      if (seen.size > maxSeenIds) seen.delete(seen.values().next().value);

      if (lastSeq === null) { // 首帧：接进来时流可能已在半路，从它起算
        lastSeq = raw.seq;
        return [{ frame: raw, late: false }];
      }
      if (raw.seq <= lastSeq) {
        // 迟到帧（重连补齐/乱序回放）：槽位已过，无法再插回原位；
        // 按 id 已验非重复，照常交付并标 late，由 opsState 的 ts 守卫拒绝画面回退。
        return [{ frame: raw, late: true }];
      }
      pending.set(raw.seq, raw);
      const out = [];
      deliverNextRuns(out);
      if (pending.size > reorderWindow) {
        // 窗口溢出：缺口里的帧按 at-most-once 语义视为已被丢，
        // 从最小待交付 seq 起按序继续，别让画面停在 past。
        const smallest = Math.min(...pending.keys());
        lastSeq = smallest - 1;
        deliverNextRuns(out);
      }
      return out;
    },
    /** 断线重连：seq 是连接级单调（§25.1 取舍 1），新连接重新起算；
     *  seen 保留——补齐会重放已交付的事件，id 去重必须跨连接活着。 */
    resetConnection() {
      pending.clear();
      lastSeq = null;
    },
    get lastSeq() { return lastSeq; },
    get pendingCount() { return pending.size; },
    get seenCount() { return seen.size; },
  };
}

/* ---------- 九类事件 -> ops 视图状态 ---------- */

function taskOf(tasks, frame) {
  const key = typeof frame.task_id === "string" && frame.task_id ? frame.task_id : "";
  if (!tasks.has(key)) {
    tasks.set(key, {
      id: key, status: "", from: "", attempt: null, risk: "", reason: "", resumeTo: "",
      lastTs: 0, steps: new Map(),
    });
  }
  return tasks.get(key);
}

export function createOpsState() {
  const state = {
    presence: null,        // { state, ts }
    tasks: new Map(),      // task_id -> 任务卡（含 steps）
    contracts: new Map(),  // contract_id -> 契约提案（最新版本胜出）
    contractOrder: [],     // 渲染顺序
    asks: [],              // 每条 ASK_USER 一项（信封 id 已去重，这里不重）
    tools: new Map(),      // task_id -> 工具执行流水（尾进）
    verifications: new Map(), // task_id -> 最新验收报告
    effects: new Map(),    // task_id -> Map(effect_id -> 副作用)
    memory: [],            // MEMORY_UPDATE 流水（尾进）
  };

  /* ts 守卫：迟到帧不得把画面拉回过去（§25.1 ts 是服务端时钟，可作全序依据）。
   * 只对「状态型」事件启用（TASK/STEP/VERIFICATION/PRESENCE/CONTRACT）；
   * 流水型（ASK/TOOL/MEMORY/EFFECT 首见）是追加事实，晚到也是事实。 */
  function fresh(entity, ts) {
    return !entity.lastTs || ts >= entity.lastTs;
  }

  function applyTask(frame) {
    const p = frame.payload || {};
    const task = taskOf(state.tasks, frame);
    if (!fresh(task, frame.ts)) return false;
    const changed = task.status !== (p.to || "");
    task.from = typeof p.from === "string" ? p.from : task.from;
    task.status = typeof p.to === "string" ? p.to : task.status;
    task.attempt = Number.isFinite(p.attempt) ? p.attempt : task.attempt;
    task.risk = typeof p.risk === "string" ? p.risk : task.risk;
    task.reason = typeof p.reason === "string" ? p.reason : task.reason;
    task.resumeTo = typeof p.resume_to === "string" ? p.resume_to : task.resumeTo;
    task.lastTs = frame.ts;
    return changed || task.status === "";
  }

  function applyStep(frame) {
    const stepId = typeof frame.step_id === "string" && frame.step_id ? frame.step_id : "";
    const p = frame.payload || {};
    const task = taskOf(state.tasks, frame);
    if (!task.steps.has(stepId)) task.steps.set(stepId, { id: stepId, status: "", agent: "", station: "", attempt: null, lastTs: 0 });
    const step = task.steps.get(stepId);
    if (!fresh(step, frame.ts)) return false;
    const changed = step.status !== (p.to || "");
    step.status = typeof p.to === "string" ? p.to : step.status;
    step.agent = typeof p.agent === "string" ? p.agent : step.agent;
    step.station = typeof p.station === "string" ? p.station : step.station;
    step.attempt = Number.isFinite(p.attempt) ? p.attempt : step.attempt;
    step.lastTs = frame.ts;
    return changed || step.status === "";
  }

  function applyContract(frame) {
    const p = frame.payload || {};
    const id = typeof p.contract_id === "string" && p.contract_id ? p.contract_id : "";
    const prev = state.contracts.get(id);
    if (prev && !fresh(prev, frame.ts)) return false;
    const contract = {
      contractId: id, taskId: typeof frame.task_id === "string" ? frame.task_id : "",
      version: p.version ?? null, goal: p.goal ?? "", artifact: p.artifact ?? null,
      inputsKind: Array.isArray(p.inputs_kind) ? p.inputs_kind.slice() : [],
      deliverToShown: p.deliver_to_shown ?? null, risk: typeof p.risk === "string" ? p.risk : "",
      highlightedSlots: Array.isArray(p.highlighted_slots) ? p.highlighted_slots.slice() : [],
      criteria: Array.isArray(p.criteria) ? p.criteria.slice() : [],
      budget: p.budget ?? null, lastTs: frame.ts,
    };
    const added = !state.contracts.has(id);
    state.contracts.set(id, contract);
    if (added) state.contractOrder.push(id);
    return true;
  }

  function applyAsk(frame) {
    const p = frame.payload || {};
    state.asks.push({
      id: frame.id, taskId: typeof frame.task_id === "string" ? frame.task_id : "",
      question: typeof p.question === "string" ? p.question : "",
      options: Array.isArray(p.options) ? p.options.slice() : [],
      missing: Array.isArray(p.missing) ? p.missing.slice() : [],
      deadline: p.deadline ?? null, ts: frame.ts, answered: false,
    });
    if (state.asks.length > MAX_ASKS) state.asks.splice(0, state.asks.length - MAX_ASKS);
    return true;
  }

  function applyTool(frame) {
    const key = typeof frame.task_id === "string" ? frame.task_id : "";
    if (!state.tools.has(key)) state.tools.set(key, []);
    const rows = state.tools.get(key);
    const p = frame.payload || {};
    rows.push({
      tool: typeof p.tool === "string" ? p.tool : "", status: typeof p.status === "string" ? p.status : "",
      stepsDone: Number.isFinite(p.steps_done) ? p.steps_done : null,
      stepsTotal: Number.isFinite(p.steps_total) ? p.steps_total : null,
      detail: typeof p.detail === "string" ? p.detail : "", ts: frame.ts,
    });
    if (rows.length > MAX_TOOLS_PER_TASK) rows.splice(0, rows.length - MAX_TOOLS_PER_TASK);
    return true;
  }

  function applyVerification(frame) {
    const key = typeof frame.task_id === "string" ? frame.task_id : "";
    const prev = state.verifications.get(key);
    if (prev && !fresh(prev, frame.ts)) return false;
    const p = frame.payload || {};
    state.verifications.set(key, {
      passed: p.passed === true, findings: Array.isArray(p.findings) ? p.findings.slice() : [],
      criteria: Array.isArray(p.criteria) ? p.criteria.slice() : [], lastTs: frame.ts,
    });
    return true;
  }

  function applyEffect(frame) {
    const key = typeof frame.task_id === "string" ? frame.task_id : "";
    if (!state.effects.has(key)) state.effects.set(key, new Map());
    const p = frame.payload || {};
    const effectId = typeof p.effect_id === "string" && p.effect_id ? p.effect_id : "";
    const rows = state.effects.get(key);
    const prev = rows.get(effectId);
    if (prev && !fresh(prev, frame.ts)) return false;
    const changed = !prev || prev.status !== p.status;
    rows.set(effectId, {
      effectId, type: typeof p.type === "string" ? p.type : "",
      targetClass: typeof p.target_class === "string" ? p.target_class : "",
      status: typeof p.status === "string" ? p.status : "",
      idempotencyKey: typeof p.idempotency_key === "string" ? p.idempotency_key : "",
      lastTs: frame.ts,
    });
    return changed;
  }

  function applyMemory(frame) {
    // 流水型：MEMORY_UPDATE 每条信封都是一次真实的档案室增删，晚到也照记。
    const p = frame.payload || {};
    state.memory.push({
      action: typeof p.action === "string" ? p.action : "",
      memoryRef: typeof p.memory_ref === "string" ? p.memory_ref : "",
      source: typeof p.source === "string" ? p.source : "",
      confidence: Number.isFinite(p.confidence) ? p.confidence : null,
      provenance: typeof p.provenance === "string" ? p.provenance : "",
      ts: frame.ts,
    });
    if (state.memory.length > MAX_MEMORY) state.memory.splice(0, state.memory.length - MAX_MEMORY);
    return true;
  }

  function applyPresence(frame) {
    const value = frame.payload && frame.payload.state;
    if (!PRESENCE_STATES.includes(value)) return false; // 四态之外安全忽略
    if (state.presence && !fresh(state.presence, frame.ts)) return false;
    const changed = !state.presence || state.presence.state !== value;
    state.presence = { state: value, ts: frame.ts };
    return changed;
  }

  const handlers = {
    TASK_STATE_CHANGE: applyTask,
    STEP_STATE_CHANGE: applyStep,
    CONTRACT_PROPOSED: applyContract,
    ASK_USER: applyAsk,
    TOOL_EXECUTION: applyTool,
    VERIFICATION_REPORT: applyVerification,
    DELIVERY_EFFECT: applyEffect,
    MEMORY_UPDATE: applyMemory,
    AGENT_PRESENCE: applyPresence,
  };

  return {
    /** 交付一条（已经过信封层去重保序的）帧。返回是否产生可见变化。 */
    apply(frame) {
      if (!isV2Frame(frame)) return false;
      return handlers[frame.type](frame);
    },
    /** UI 把某任务的最新提问标记为已回答（发过 ANSWER 后的本地反馈）。 */
    markAnswered(taskId) {
      for (let i = state.asks.length - 1; i >= 0; i -= 1) {
        if (state.asks[i].taskId === taskId && !state.asks[i].answered) {
          state.asks[i].answered = true;
          return true;
        }
      }
      return false;
    },
    /** 给测试与渲染层看的纯数据快照（Map 展开为数组/对象，顺序保持插入序）。 */
    snapshot() {
      return {
        presence: state.presence,
        tasks: [...state.tasks.values()].map((task) => ({ ...task, steps: [...task.steps.values()].map((step) => ({ ...step })) })),
        contracts: state.contractOrder.map((id) => state.contracts.get(id)),
        asks: state.asks.map((ask) => ({ ...ask })),
        tools: [...state.tools].map(([taskId, rows]) => ({ taskId, rows: rows.map((row) => ({ ...row })) })),
        verifications: [...state.verifications].map(([taskId, report]) => ({ taskId, ...report })),
        effects: [...state.effects].map(([taskId, rows]) => ({ taskId, rows: [...rows.values()].map((row) => ({ ...row })) })),
        memory: state.memory.map((item) => ({ ...item })),
      };
    },
  };
}

/* ---------- §25.3 前端命令 ---------- */

let fallbackCounter = 0;

export function newRequestId() {
  const cryptoRef = typeof globalThis !== "undefined" && globalThis.crypto;
  if (cryptoRef && typeof cryptoRef.randomUUID === "function") return cryptoRef.randomUUID();
  fallbackCounter += 1;
  return `req-${Date.now().toString(36)}-${fallbackCounter.toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/* 构造一条 §25.3 命令。必填字段缺失/类型不合返回 { ok:false, error }，
 * 不发半个命令——后端按语义接收的前提是字段齐。
 * fields.request_id 允许外部传入：网络重发复用同一个幂等键（§25.3）。 */
export function buildCommand(type, fields = {}, { newId = newRequestId } = {}) {
  if (!V2_COMMAND_TYPES.includes(type)) {
    return { ok: false, error: `未知的 v2 命令 ${String(type)}` };
  }
  const missing = [];
  for (const name of COMMAND_REQUIRED[type]) {
    const value = fields[name];
    if (value === undefined || value === null || value === "") missing.push(name);
  }
  if (missing.length) return { ok: false, error: `${type} 缺必填字段 ${missing.join(", ")}` };
  if (type === "CONFIRM_CONTRACT") {
    if (!Array.isArray(fields.confirmed_slots) || fields.confirmed_slots.some((slot) => typeof slot !== "string")) {
      return { ok: false, error: "CONFIRM_CONTRACT 的 confirmed_slots 必须是字符串数组" };
    }
  }
  if (type === "ANSWER" && typeof fields.answer !== "string") {
    return { ok: false, error: "ANSWER 的 answer 必须是字符串" };
  }
  if (type === "CANCEL_TASK" && fields.reason !== undefined
    && !["user_abort", "wrong_task"].includes(fields.reason)) {
    return { ok: false, error: "CANCEL_TASK 的 reason 只能是 user_abort 或 wrong_task" };
  }
  const command = { type };
  for (const name of COMMAND_REQUIRED[type]) command[name] = fields[name];
  if (type === "CANCEL_TASK" && fields.reason !== undefined) command.reason = fields.reason;
  if (type === "ANSWER" && Number.isInteger(fields.option_index)) command.option_index = fields.option_index;
  command.request_id = typeof fields.request_id === "string" && fields.request_id
    ? fields.request_id : newId();
  return { ok: true, command };
}

/* ---------- 模块级 hub：app.js 喂帧 + 发命令，executive.js 订阅渲染 ---------- */

export function createEventHub() {
  const stream = createEventStream();
  const ops = createOpsState();
  const listeners = new Set();
  let sender = null;

  function notify() {
    const snapshot = ops.snapshot();
    for (const listener of listeners) {
      try { listener(snapshot); } catch (_) { /* 一个渲染挂了不该断了流 */ }
    }
  }

  return {
    /** app.js 的 ws.onmessage 调：v2 帧在此归约并广播快照。
     *  返回 true 表示这是 v2 帧（无论重复/乱序/被忽略），调用方应短路后续 v1 处理。 */
    handleFrame(raw) {
      if (!raw || typeof raw !== "object" || raw.v !== 2 || typeof raw.type !== "string") return false;
      const delivered = stream.accept(raw);
      let changed = false;
      for (const item of delivered) changed = ops.apply(item.frame) || changed;
      if (changed) notify();
      return true;
    },
    /** 渲染层订阅快照（只在可见变化时触发）。返回退订函数。 */
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** app.js 注入发送通道（ws.send 的包装，返回是否真的发出）。 */
    attachSender(send) { sender = typeof send === "function" ? send : null; },
    /** UI 动作 -> 命令发出。sent=false 表示通道未就绪（连接断开），
     *  命令对象照常返回，调用方可以留待重发（复用 request_id 保证幂等）。 */
    sendCommand(type, fields = {}) {
      const built = buildCommand(type, fields);
      if (!built.ok) return built;
      let sent = false;
      if (sender) {
        try { sent = sender(built.command) === true; } catch (_) { sent = false; }
      }
      if (sent && type === "ANSWER" && built.command.task_id) {
        ops.markAnswered(built.command.task_id);
        notify();
      }
      return { ok: true, sent, command: built.command };
    },
    /** 断线重连时由外层调用（seq 连接级重算，id 去重跨连接保留）。 */
    resetConnection() { stream.resetConnection(); },
    /** 渲染层初次挂载/抽屉切回 ops 时取当前快照。 */
    snapshot() { return ops.snapshot(); },
    get stream() { return stream; },
  };
}

export const v2Hub = createEventHub();
