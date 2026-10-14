/* 星图数据桥：docs/PROTOCOL.md 第十三节契约的纯逻辑层。
 * 取数、检索去抖、narrate 状态机都在这里，不碰 DOM、不 import 任何模块。
 * node 单测经 data: URL 以 ESM 加载（package.json 没有 type:module），
 * 相对导入会解析失败，所以这一层必须自给自足。 */

export const NODE_KINDS = ["memory", "diary", "spark", "taste", "worldview"];
export const EDGE_KINDS = ["evidences", "born", "shaped", "mentions"];
export const LIST_LIMIT = 400;

const clamp01 = (value) => Math.min(1, Math.max(0, value));

/* ---- 图谱数据转渲染模型 ---- */

function normalizeNode(raw) {
  if (!raw || typeof raw !== "object") return null;
  const id = typeof raw.id === "string" && raw.id ? raw.id : null;
  if (!id) return null;
  return {
    id,
    kind: typeof raw.kind === "string" && raw.kind ? raw.kind : "memory",
    label: typeof raw.label === "string" && raw.label ? raw.label.slice(0, 80) : id,
    date: typeof raw.date === "string" && raw.date ? raw.date : null,
    weight: Number.isFinite(raw.weight) ? clamp01(raw.weight) : 0.5,
    group: raw.group === "self" ? "self" : "user",
    refId: raw.ref_id == null ? null : raw.ref_id,
    degree: 0,
  };
}

function normalizeEdge(raw, byId) {
  if (!raw || typeof raw !== "object") return null;
  const source = typeof raw.source === "string" ? raw.source : null;
  const target = typeof raw.target === "string" ? raw.target : null;
  if (!source || !target || source === target) return null;
  if (!byId.has(source) || !byId.has(target)) return null;
  return {
    source,
    target,
    kind: typeof raw.kind === "string" && raw.kind ? raw.kind : "mentions",
    weight: Number.isFinite(raw.weight) ? clamp01(raw.weight) : 0.5,
  };
}

/* 列表接口永远不含正文；这里只整理布局与描边需要的字段。
 * 服务端字段缺失时全部有默认值，前端不假设协议一定给全。 */
export function toRenderModel(payload) {
  const rawNodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
  const rawEdges = Array.isArray(payload?.edges) ? payload.edges : [];
  const nodes = [];
  const byId = new Map();
  for (const raw of rawNodes) {
    const node = normalizeNode(raw);
    if (!node || byId.has(node.id)) continue;
    byId.set(node.id, node);
    nodes.push(node);
  }
  const edges = [];
  for (const raw of rawEdges) {
    const edge = normalizeEdge(raw, byId);
    if (!edge) continue;
    edges.push(edge);
    byId.get(edge.source).degree += 1;
    byId.get(edge.target).degree += 1;
  }
  const stats = { memories: 0, diaries: 0, sparks: 0, taste: 0, worldview: 0 };
  for (const key of Object.keys(stats)) {
    const value = Number(payload?.stats?.[key]);
    stats[key] = Number.isFinite(value) ? value : 0;
  }
  const kindCounts = {};
  const groupCounts = { user: 0, self: 0 };
  for (const node of nodes) {
    kindCounts[node.kind] = (kindCounts[node.kind] || 0) + 1;
    groupCounts[node.group] += 1;
  }
  return { nodes, edges, byId, stats, kindCounts, groupCounts,
    truncated: !!payload?.truncated, empty: nodes.length === 0 };
}

/* 三类边界：记忆关闭 / 空图 / 检索无结果。有内容时返回 null。 */
export function classifyEmpty(model, { memoryEnabled = true, query = "" } = {}) {
  if (memoryEnabled === false) {
    return { kind: "off", message: "记忆已关闭。打开“允许阿栖记住”后，这里会出现你们一起留下的记忆。" };
  }
  if (!model || model.empty) {
    const q = typeof query === "string" ? query.trim() : "";
    if (q) return { kind: "search", message: `没有找到与“${q}”相关的记忆。换个词，或清除检索看看全部。` };
    return { kind: "empty", message: "星图还是空的。多聊几句，记忆就会在这里亮起来。" };
  }
  return null;
}

/* ---- 关键词检索：q 参数服务端过滤，前端只负责去抖与丢弃过期响应 ---- */

export function createGraphSearch({ load, delay = 300, timers = globalThis,
  onResult, onError, onPending } = {}) {
  let timer = null;
  let pendingQuery = null;
  let seq = 0;
  const clearTimer = () => {
    if (timer !== null) { timers.clearTimeout(timer); timer = null; }
  };
  const run = (query) => {
    clearTimer();
    pendingQuery = null;
    const q = typeof query === "string" ? query.trim() : "";
    const mine = ++seq;
    onPending?.(true, q);
    let loading;
    try {
      loading = Promise.resolve(load(q));
    } catch (error) {
      loading = Promise.reject(error);
    }
    loading
      .then((model) => {
        if (mine !== seq) return;
        onPending?.(false, q);
        onResult?.(model, q);
      }, (error) => {
        if (mine !== seq) return;
        onPending?.(false, q);
        onError?.(error, q);
      });
  };
  return {
    run,
    search(query) {
      pendingQuery = typeof query === "string" ? query : "";
      clearTimer();
      timer = timers.setTimeout(() => {
        timer = null;
        const q = pendingQuery;
        pendingQuery = null;
        run(q);
      }, delay);
    },
    flush() {
      if (timer === null) return;
      const q = pendingQuery;
      run(q);
    },
    cancel() {
      clearTimer();
      pendingQuery = null;
      seq += 1;
      onPending?.(false, "");
    },
    get pending() { return timer !== null ? pendingQuery : null; },
  };
}

/* ---- narrate 状态机：idle 与 pending/playing/busy 之间转换 ---- */

let turnCounter = 0;
function defaultNewTurnId() {
  const unique = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${++turnCounter}`;
  return `client-${unique}`;
}

export function createNarration({ send, stopTurn, isIdle = () => true,
  pendingTimeoutMs = 15000, busyCooldownMs = 4000, timers = globalThis,
  newTurnId = defaultNewTurnId } = {}) {
  let state = "idle";
  let turnId = null;
  let nodeId = null;
  let note = "";
  let pendingTimer = null;
  let cooldownTimer = null;
  const listeners = new Set();
  const snapshot = () => ({ state, turnId, nodeId, note,
    canStart: state === "idle" && !!isIdle() });
  const emit = () => { for (const fn of [...listeners]) fn(snapshot()); };
  const clearTimers = () => {
    if (pendingTimer !== null) { timers.clearTimeout(pendingTimer); pendingTimer = null; }
    if (cooldownTimer !== null) { timers.clearTimeout(cooldownTimer); cooldownTimer = null; }
  };
  const settle = (next, message = "") => {
    clearTimers();
    state = next;
    note = message;
    turnId = null;
    emit();
  };
  return {
    get state() { return state; },
    snapshot,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    canStart() { return state === "idle" && !!isIdle(); },
    /* 点击“让小溪讲这个”。进行中或忙碌时不排队，直接拒绝。 */
    start(id) {
      if (state !== "idle" || typeof id !== "string" || !id) return false;
      turnId = newTurnId();
      nodeId = id;
      const sent = send?.({ type: "narrate", node_id: id, turn_id: turnId });
      if (sent === false) {
        state = "idle";
        turnId = null;
        nodeId = null;
        note = "连接未就绪，稍后再试。";
        emit();
        return false;
      }
      state = "pending";
      note = "";
      emit();
      pendingTimer = timers.setTimeout(() => {
        pendingTimer = null;
        if (state === "pending") settle("idle", "讲述没有开始，请再点一次。");
      }, pendingTimeoutMs);
      return true;
    },
    /* 用户主动打断（barge_in）：本地立刻回 idle，不等服务端确认。 */
    stop() {
      if (state !== "pending" && state !== "playing") return false;
      stopTurn?.();
      settle("idle", "已让小溪停下。");
      return true;
    },
    reset() {
      clearTimers();
      state = "idle";
      turnId = null;
      nodeId = null;
      note = "";
      emit();
    },
    /* app.js 在 TurnGate 之前调用：narration 轮按本客户端生成的 turn_id 配对；
     * narrate_busy 是连接级错误、不回显 turn_id（协议第十三节），一律按忙碌处理。 */
    onMessage(msg) {
      if (!msg || typeof msg !== "object" || typeof msg.type !== "string") return;
      if (msg.type === "error" && msg.code === "narrate_busy") {
        if (state === "pending" || state === "playing") {
          clearTimers();
          state = "busy";
          note = "小溪正在讲别的，稍等一下再点。";
          turnId = null;
          emit();
          cooldownTimer = timers.setTimeout(() => {
            cooldownTimer = null;
            if (state === "busy") settle("idle", "");
          }, busyCooldownMs);
        }
        return;
      }
      if (!turnId || msg.turn_id !== turnId) return;
      switch (msg.type) {
        case "turn_started":
        case "thinking":
        case "emotion":
        case "text_delta":
        case "audio":
          if (state === "pending") {
            if (pendingTimer !== null) { timers.clearTimeout(pendingTimer); pendingTimer = null; }
            state = "playing";
            note = "";
            emit();
          }
          break;
        case "done":
          settle("idle", "");
          break;
        case "interrupted":
        case "playback_stopped":
          settle("idle", "讲述已停下。");
          break;
        case "error":
          settle("idle", typeof msg.message === "string" && msg.message
            ? msg.message : "讲述没有完成，请再点一次。");
          break;
        default:
          break;
      }
    },
  };
}

/* ---- REST 客户端 ---- */

export function createStarmapClient({ fetchImpl } = {}) {
  const doFetch = fetchImpl || (typeof fetch === "function" ? fetch : null);
  if (!doFetch) throw new Error("当前环境没有 fetch，无法加载星图");
  return {
    async loadGraph(params = {}) {
      const query = new URLSearchParams();
      const days = Number(params.days);
      if (Number.isFinite(days) && days > 0) query.set("days", String(Math.floor(days)));
      if (typeof params.kinds === "string" && params.kinds) query.set("kinds", params.kinds);
      if (typeof params.q === "string" && params.q.trim()) query.set("q", params.q.trim());
      const limit = Math.min(LIST_LIMIT, Math.max(1, Number(params.limit) || LIST_LIMIT));
      query.set("limit", String(limit));
      const res = await doFetch(`/api/memory/starmap?${query.toString()}`, {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`星图加载失败（${res.status}）`);
      let payload;
      try {
        payload = await res.json();
      } catch (_) {
        throw new Error("星图数据格式异常");
      }
      return toRenderModel(payload);
    },
    async loadNode(nodeId) {
      if (typeof nodeId !== "string" || !nodeId) throw new Error("缺少节点 ID");
      const res = await doFetch(`/api/memory/starmap/node/${encodeURIComponent(nodeId)}`, {
        headers: { Accept: "application/json" },
      });
      if (res.status === 404) return { missing: true, node: null };
      if (!res.ok) throw new Error(`节点加载失败（${res.status}）`);
      let node;
      try {
        node = await res.json();
      } catch (_) {
        throw new Error("节点数据格式异常");
      }
      return { missing: false, node };
    },
  };
}
