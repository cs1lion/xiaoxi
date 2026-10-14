/* 星图视图：主工作台内置视图（AGENT_PLAYBOOK 规则 3，不建独立 HTML）。
 * 本模块自包含：视图显隐、Esc 关闭、焦点归还都在这里；
 * app.js 只负责三行接线（import / mount / onMessage 钩子），见接入 diff。
 * 播放不重复造：narration 轮复用既有 turn_started->...->done 链路与
 * PlaybackQueue，本模块只发 narrate 动作并观察轮次事件驱动自己的状态机。 */

import { createStarmapClient, createGraphSearch, createNarration,
  classifyEmpty, LIST_LIMIT } from "./bridge.js";
import { createGraphView, KIND_LABELS, GROUP_LABELS, EDGE_LABELS } from "./render.js";

const STYLE_ID = "starmap-style";
const SPARK_STATE_LABELS = { latent: "酝酿中", surfaced: "已浮现", spent: "已化作话题" };
const FULLVIEW_EVENT = "aqi-fullview";

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* 详情接口返回“原样结构”，不同 kind 的正文字段名不同；按优先级取第一个非空。 */
function nodeText(node) {
  for (const key of ["body", "text", "value", "stance", "summary", "title"]) {
    const value = node?.[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function detailMeta(node) {
  const rows = [];
  const push = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    rows.push(`${label}：${value}`);
  };
  if (typeof node?.key === "string") push("键", node.key);
  if (typeof node?.mood === "string") push("心境", node.mood);
  if (typeof node?.state === "string") push("状态", SPARK_STATE_LABELS[node.state] || node.state);
  if (typeof node?.topic === "string") push("主题", node.topic);
  if (Number.isFinite(node?.weight)) push("权重", node.weight);
  if (Number.isFinite(node?.energy)) push("能量", node.energy);
  if (Number.isFinite(node?.heat)) push("热度", node.heat);
  if (Array.isArray(node?.topic_keys) && node.topic_keys.length) {
    push("话题", node.topic_keys.filter((key) => typeof key === "string").join("、"));
  }
  return rows;
}

function ensureStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const link = el("link");
  link.id = STYLE_ID;
  link.rel = "stylesheet";
  link.href = new URL("./starmap.css", import.meta.url).href;
  document.head.append(link);
}

export function mountStarmap(container, deps = {}) {
  if (!container) return null;
  const {
    send = null,
    stopTurn = null,
    isIdle = () => true,
    isMemoryEnabled = () => {
      const toggle = document.getElementById("memoryToggle");
      return toggle ? toggle.checked : true;
    },
    fetchImpl = undefined,
    days = 90,
    searchDelay = 300,
  } = deps;

  ensureStyles();
  container.classList.add("starmap-mount");
  if (!container.hasAttribute("role")) container.setAttribute("role", "region");
  if (!container.hasAttribute("aria-label")) container.setAttribute("aria-label", "星图记忆");
  container.hidden = true;

  const controller = new AbortController();
  const on = (node, type, fn, options) =>
    node.addEventListener(type, fn, { signal: controller.signal, ...options });

  /* ---- DOM ---- */
  const root = el("div", "starmap");
  const bar = el("header", "starmap-bar");
  const titleWrap = el("div", "starmap-title");
  titleWrap.append(el("h2", null, "星图"),
    el("p", "hint", "小溪的脑内宇宙。点亮的是共同的记忆、日记与念头。"));
  const searchWrap = el("div", "starmap-search");
  const searchInput = el("input");
  searchInput.type = "search";
  searchInput.id = "starmapSearch";
  searchInput.placeholder = "搜一个词，服务器替你找";
  searchInput.setAttribute("aria-label", "检索记忆星图");
  const clearBtn = el("button", "ghost", "清除");
  clearBtn.type = "button";
  const refreshBtn = el("button", "ghost", "刷新");
  refreshBtn.type = "button";
  searchWrap.append(searchInput, clearBtn, refreshBtn);
  const closeBtn = el("button", "ghost starmap-close", "回到聊天");
  closeBtn.type = "button";
  bar.append(titleWrap, searchWrap, closeBtn);

  const body = el("div", "starmap-body");
  const canvas = el("div", "starmap-canvas");
  const graphTools = el("div", "starmap-tools");
  const zoomIn = el("button", "ghost", "放大");
  const zoomOut = el("button", "ghost", "缩小");
  const zoomReset = el("button", "ghost", "复位");
  for (const button of [zoomIn, zoomOut, zoomReset]) {
    button.type = "button";
    graphTools.append(button);
  }
  canvas.append(graphTools);

  const detail = el("aside", "starmap-detail");
  detail.hidden = true;
  const detailHead = el("div", "starmap-detail-head");
  const detailTags = el("div", "starmap-detail-tags");
  const detailClose = el("button", "ghost starmap-detail-close", "收起");
  detailClose.type = "button";
  detailHead.append(detailTags, detailClose);
  const detailTitle = el("h3", "starmap-detail-title");
  const detailBody = el("p", "starmap-detail-body");
  const detailExtra = el("p", "hint starmap-detail-extra");
  const narrateWrap = el("div", "starmap-narrate");
  const narrateBtn = el("button", "starmap-narrate-btn", "让小溪讲这个");
  narrateBtn.type = "button";
  const narrateStop = el("button", "ghost", "停止讲述");
  narrateStop.type = "button";
  narrateStop.hidden = true;
  const narrateNote = el("p", "hint starmap-narrate-note");
  narrateNote.setAttribute("role", "status");
  narrateNote.setAttribute("aria-live", "polite");
  narrateWrap.append(narrateBtn, narrateStop, narrateNote);
  detail.append(detailHead, detailTitle, detailBody, detailExtra, narrateWrap);
  body.append(canvas, detail);

  const empty = el("div", "starmap-empty");
  empty.hidden = true;
  const emptyText = el("p", "starmap-empty-text");
  const emptyAction = el("button", "ghost", "清除检索");
  emptyAction.type = "button";
  emptyAction.hidden = true;
  empty.append(emptyText, emptyAction);

  const errorBox = el("div", "starmap-error");
  errorBox.hidden = true;
  const errorText = el("p", "starmap-error-text");
  const errorRetry = el("button", "ghost", "重试");
  errorRetry.type = "button";
  errorBox.append(errorText, errorRetry);

  const searchStatus = el("p", "hint starmap-search-status");
  searchStatus.setAttribute("role", "status");

  const legend = el("footer", "starmap-legend");
  const legendNodes = el("div", "starmap-legend-nodes");
  const legendEdges = el("div", "starmap-legend-edges");
  const truncatedHint = el("p", "hint starmap-truncated");
  truncatedHint.hidden = true;
  truncatedHint.textContent = "星点太多，只显示了权重最高的一部分。";
  legend.append(legendNodes, legendEdges, truncatedHint);

  root.append(bar, body, empty, errorBox, searchStatus, legend);
  container.append(root);

  for (const [kind, label] of Object.entries(KIND_LABELS)) {
    const chip = el("span", `legend-chip kind-${kind}`);
    chip.append(el("i", `swatch kind-${kind}`), el("span", "legend-label", label));
    legendNodes.append(chip);
  }
  for (const group of ["user", "self"]) {
    const chip = el("span", `legend-chip group-${group}`);
    chip.append(el("i", `swatch group-${group}`), el("span", "legend-label", GROUP_LABELS[group]));
    legendNodes.append(chip);
  }
  for (const [kind, label] of Object.entries(EDGE_LABELS)) {
    const chip = el("span", `legend-chip edge-${kind}`);
    chip.append(el("i", `swatch edge-${kind}`), el("span", "legend-label", label));
    legendEdges.append(chip);
  }

  /* ---- 逻辑接线 ---- */
  const client = createStarmapClient({ fetchImpl });
  const view = createGraphView(canvas, { onNodeActivate: (id) => selectNode(id) });
  let model = null;
  let selectedId = null;
  let currentQuery = "";
  let opened = false;
  let lastFocus = null;

  const narration = createNarration({
    send: send ? (payload) => send(payload) : null,
    stopTurn,
    isIdle,
  });

  const search = createGraphSearch({
    load: (q) => client.loadGraph({ days, q, limit: LIST_LIMIT }),
    delay: searchDelay,
    onPending: (pending) => {
      searchStatus.textContent = pending ? "检索中…" : "";
    },
    onResult: (next, q) => {
      currentQuery = q;
      errorBox.hidden = true;
      render(next, q);
    },
    onError: (error) => {
      searchStatus.textContent = "";
      errorText.textContent = error?.message || "星图加载失败。";
      errorBox.hidden = false;
    },
  });

  function render(next, query) {
    model = next;
    view.setModel(next);
    if (selectedId && !next.byId.has(selectedId)) clearSelection();
    const memoryEnabled = isMemoryEnabled();
    const emptyState = classifyEmpty(next, { memoryEnabled, query });
    const showGraph = !emptyState;
    canvas.hidden = !showGraph;
    empty.hidden = !emptyState;
    if (emptyState) {
      emptyText.textContent = emptyState.message;
      emptyAction.hidden = emptyState.kind !== "search";
      if (emptyState.kind === "off") clearSelection();
    }
    legend.hidden = !showGraph;
    truncatedHint.hidden = !next.truncated;
    for (const chip of legendNodes.children) {
      const kind = chip.className.match(/kind-(\w+)/)?.[1];
      if (!kind || !(kind in KIND_LABELS)) continue;
      chip.hidden = !next.kindCounts[kind];
      const label = chip.querySelector(".legend-label");
      if (label) label.textContent = `${KIND_LABELS[kind]} ${next.kindCounts[kind] || 0}`;
    }
  }

  function clearSelection() {
    selectedId = null;
    view.setSelected(null);
    detail.hidden = true;
    syncNarrate();
  }

  async function selectNode(id) {
    if (!isMemoryEnabled()) return;
    selectedId = id;
    view.setSelected(id);
    view.focusNode(id);
    const node = model?.byId.get(id);
    detailTags.replaceChildren();
    if (node) {
      detailTags.append(el("span", `tag kind-${node.kind}`, KIND_LABELS[node.kind] || node.kind),
        el("span", `tag group-${node.group}`, GROUP_LABELS[node.group] || node.group));
      if (node.date) detailTags.append(el("span", "tag", node.date));
    }
    detailTitle.textContent = node?.label || id;
    detailBody.textContent = "正在读取…";
    detailExtra.textContent = "";
    detail.hidden = false;
    syncNarrate();
    try {
      const { missing, node: full } = await client.loadNode(id);
      if (selectedId !== id) return;
      if (missing) {
        detailTitle.textContent = node?.label || id;
        detailBody.textContent = "这条记忆已经不在了。可能被清空或合并，刷新星图看看现在的样子。";
        detailExtra.textContent = "";
        narrateBtn.disabled = true;
        return;
      }
      detailTitle.textContent = typeof full?.title === "string" && full.title ? full.title : (node?.label || id);
      detailBody.textContent = nodeText(full) || "这条节点没有可展示的正文。";
      const meta = detailMeta(full);
      detailExtra.textContent = meta.join("　");
    } catch (error) {
      if (selectedId !== id) return;
      detailBody.textContent = error?.message || "节点读取失败。";
    }
    syncNarrate();
  }

  function syncNarrate() {
    const snapshot = narration.snapshot();
    const memoryEnabled = isMemoryEnabled();
    narrateWrap.hidden = !memoryEnabled || !selectedId;
    const busy = snapshot.state === "busy";
    const running = snapshot.state === "pending" || snapshot.state === "playing";
    narrateBtn.disabled = !snapshot.canStart || !selectedId || !memoryEnabled || busy || running;
    narrateBtn.textContent = busy ? "小溪正忙着" : running ? "讲述中…" : "让小溪讲这个";
    narrateStop.hidden = !running;
    narrateNote.textContent = snapshot.note;
    view.setNarrating?.(running || busy ? snapshot.nodeId : null);
    kickSync();
  }

  /* canStart 依赖 app.js 的空闲态，而它会在没有本组件事件的情况下变化
   *（done 之后播放队列排空）。轮次进行中、或按钮因外部忙碌而禁用时，
   * 用轻量轮询补上这次重评；按钮放行后自动停止。 */
  let syncTimer = null;
  const shouldPoll = () => {
    const snapshot = narration.snapshot();
    if (snapshot.state !== "idle") return true;
    return !!selectedId && narrateBtn.disabled;
  };
  const syncLoop = () => {
    syncTimer = null;
    syncNarrate();
    if (syncTimer === null && shouldPoll()) syncTimer = setTimeout(syncLoop, 400);
  };
  function kickSync() {
    if (syncTimer === null && shouldPoll()) syncTimer = setTimeout(syncLoop, 400);
  }

  function loadCurrent() {
    errorBox.hidden = true;
    if (!isMemoryEnabled()) {
      search.cancel();
      render({ nodes: [], edges: [], stats: {}, truncated: false, empty: true }, currentQuery);
      return;
    }
    search.run(currentQuery);
  }

  /* ---- 事件 ---- */
  on(searchInput, "input", () => search.search(searchInput.value));
  on(searchInput, "keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      search.flush();
    }
  });
  on(clearBtn, "click", () => {
    searchInput.value = "";
    search.run("");
  });
  on(emptyAction, "click", () => {
    searchInput.value = "";
    search.run("");
  });
  on(refreshBtn, "click", () => search.run(currentQuery));
  on(errorRetry, "click", () => search.run(currentQuery));
  on(closeBtn, "click", () => close());
  on(detailClose, "click", () => clearSelection());
  on(narrateBtn, "click", () => {
    if (selectedId) narration.start(selectedId);
  });
  on(narrateStop, "click", () => narration.stop());
  on(zoomIn, "click", () => view.zoomBy(1.4));
  on(zoomOut, "click", () => view.zoomBy(1 / 1.4));
  on(zoomReset, "click", () => view.resetView());

  const toggle = document.getElementById("starmapToggle");
  const syncToggle = () => toggle?.setAttribute("aria-expanded", String(opened));
  if (toggle) on(toggle, "click", () => (opened ? close() : open()));

  on(window, "keydown", (event) => {
    if (opened && event.key === "Escape" && !event.isComposing) {
      event.preventDefault();
      close();
    }
  });
  on(window, FULLVIEW_EVENT, (event) => {
    if (opened && event.detail !== "starmap") close();
  });

  function open() {
    if (opened) return;
    opened = true;
    lastFocus = document.activeElement;
    container.hidden = false;
    syncToggle();
    window.dispatchEvent(new CustomEvent(FULLVIEW_EVENT, { detail: "starmap" }));
    loadCurrent();
    closeBtn.focus({ preventScroll: true });
  }

  function close() {
    if (!opened) return;
    opened = false;
    container.hidden = true;
    search.cancel();
    syncToggle();
    const target = toggle && document.contains(toggle) ? toggle : lastFocus;
    if (target && typeof target.focus === "function") target.focus({ preventScroll: true });
  }

  const unsubscribe = narration.subscribe(syncNarrate);
  syncNarrate();

  return {
    get opened() { return opened; },
    open,
    close,
    toggle: () => (opened ? close() : open()),
    bindToggle(button) {
      if (!button) return;
      on(button, "click", () => (opened ? close() : open()));
      syncToggle();
    },
    /* app.js 在 TurnGate 之前调用；narration 之外的消息在这里被忽略。 */
    onMessage: (msg) => narration.onMessage(msg),
    dispose() {
      unsubscribe();
      search.cancel();
      view.dispose();
      controller.abort();
      if (syncTimer !== null) clearTimeout(syncTimer);
      root.remove();
    },
  };
}
