/* 动态积木渲染器（协议 §23）：原生 Vanilla JS，零依赖。
 *
 * 小溪的日常交流是"简短口语语音 + 积木"：她开口只说一两句，状态、选项、
 * 卡片数据由服务端组装成 aqi_blocks 消息随路下发，落在这里渲染。
 *
 * 铁律：
 * 1. 只用 textContent，不碰 innerHTML——积木内容是不可信资料，不是 HTML。
 * 2. 未知 kind / 坏字段安全降级：不渲染、不抛错、不用占位卡充数。
 * 3. actions 的 cmd 只认白名单动词（parseCmd），未知 verb 原样抛给上层，
 *    由上层决定忽略——渲染器自己不猜业务语义。
 * 4. 卡片浮现的视线联动由上层（app.js）调 avatar，这里只报 direction。
 */

/* 协议 §23.2 七种标准积木。与下方 RENDERERS 必须一一对应：这里少一个，
 * 前端就会把那种积木静默丢成空卡。check_protocol.py 第 7 条三方认账。 */
export const AQI_BLOCK_KINDS = [
  "choice_pills",
  "spotlight_card",
  "alert_card",
  "swimlane_strip",
  "metric_flame",
  "note_snippet",
  "memo_note",
];

const LAYOUTS = ["stage_holo", "drawer_ops"];
const NAV_TARGETS = ["ops", "notes", "threads", "starmap"];
const GAZE_TARGETS = ["holo_right", "drawer", "user"];
const SOURCES = ["chat", "executive", "ambient", "loop"];
const TONES = ["gold", "red", "mint", "neutral"];
const LEVELS = ["warn", "critical"];
const COMMISSION_STATUSES = [
  "drafting", "briefing", "running", "blocked", "auditing", "ready_to_report", "done",
];
const MAX_CARDS = 6;
const MAX_OPTIONS = 6;
const MAX_LANES = 12;
const MAX_METRICS = 8;
const MAX_ACTIONS = 4;
const MAX_ARTIFACTS = 12;
const MAX_META_PAIRS = 6;

const STATUS_LABELS = {
  drafting: "组密件",
  briefing: "已派将",
  running: "干活中",
  blocked: "等你拍板",
  auditing: "初审中",
  ready_to_report: "等你验收",
  done: "已归档",
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function text(value, limit = 400) {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, limit);
}

function list(value) {
  return Array.isArray(value) ? value : [];
}

/** cmd 白名单解析：<verb>:<payload>。未知 verb 返回 {verb:"", payload:""} 交由上层忽略。 */
export function parseCmd(cmd) {
  if (typeof cmd !== "string") return { verb: "", payload: "" };
  const index = cmd.indexOf(":");
  if (index <= 0) return { verb: "", payload: "" };
  return { verb: cmd.slice(0, index), payload: cmd.slice(index + 1) };
}

function actionsOf(block) {
  return list(block?.actions)
    .filter((action) => action && typeof action === "object")
    .map((action) => ({ label: text(action.label, 40), cmd: text(action.cmd, 200) }))
    .filter((action) => action.label && action.cmd)
    .slice(0, MAX_ACTIONS);
}

function actionButton(action, block, handlers) {
  const button = el("button", "aqi-action", action.label);
  button.type = "button";
  button.dataset.cmd = action.cmd;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    if (typeof handlers?.onAction === "function") handlers.onAction(action.cmd, block);
  });
  return button;
}

function actionsRow(block, handlers) {
  const actions = actionsOf(block);
  if (!actions.length) return null;
  const row = el("div", "aqi-actions");
  for (const action of actions) row.append(actionButton(action, block, handlers));
  return row;
}

function head(kind, title, glyph, subtitle) {
  const head = el("div", "aqi-block-head");
  head.append(el("span", "aqi-block-glyph", glyph));
  head.append(el("span", "aqi-block-title", title || kind));
  if (subtitle) head.append(el("span", "aqi-block-subtitle", subtitle));
  return head;
}

/** 通用字段装饰（协议 §23.2）：id → data-target-id（preview 高亮定位）、
 * tone → data-tone（覆盖 kind 默认强调色）、subtitle → 副标题行。
 * 七个 renderer 共用，谁也不用单独记这些字段。 */
function decorate(card, block) {
  if (!card || !block || typeof block !== "object") return card;
  const targetId = text(block.id, 100);
  if (targetId) card.dataset.targetId = targetId;
  if (TONES.includes(block.tone)) card.dataset.tone = block.tone;
  const subtitle = text(block.subtitle, 80);
  if (subtitle && !card.querySelector(".aqi-block-subtitle")) {
    const line = el("p", "aqi-subtitle", subtitle);
    if (card.children[1]) card.insertBefore(line, card.children[1]);
    else card.append(line);
  }
  return card;
}

function renderMemoNote(block, handlers) {
  const title = text(block?.title, 80);
  const body = text(block?.body, 400);
  if (!title && !body) return null;
  const card = el("article", "aqi-block aqi-memo");
  card.dataset.kind = "memo_note";
  card.append(head("memo_note", title || "安心便签", "✧"));
  if (body) card.append(el("p", "aqi-line", body));
  const meta = block?.meta && typeof block.meta === "object" && !Array.isArray(block.meta)
    ? block.meta : {};
  const pairs = Object.entries(meta)
    .slice(0, MAX_META_PAIRS)
    .map(([key, value]) => [text(key, 40),
      typeof value === "number" && Number.isFinite(value) ? String(value) : text(value, 80)])
    .filter(([key, value]) => key && value);
  if (pairs.length) {
    const box = el("div", "aqi-meta");
    for (const [key, value] of pairs) {
      const chip = el("span", "aqi-meta-chip");
      chip.append(el("b", null, key));
      chip.append(document.createTextNode(` ${value}`));
      box.append(chip);
    }
    card.append(box);
  }
  const row = actionsRow(block, handlers);
  if (row) card.append(row);
  return card;
}

function renderChoicePills(block, handlers) {
  const question = text(block?.question, 200);
  const options = list(block?.options)
    .filter((option) => option && typeof option === "object")
    .map((option) => ({ label: text(option.label, 40), cmd: text(option.cmd, 200) }))
    .filter((option) => option.label && option.cmd)
    .slice(0, MAX_OPTIONS);
  if (!question && !options.length) return null;
  const card = el("article", "aqi-block aqi-choice");
  card.dataset.kind = "choice_pills";
  card.append(head("choice_pills", question, "◈"));
  const pills = el("div", "aqi-pills");
  for (const option of options) {
    const pill = el("button", "aqi-pill", option.label);
    pill.type = "button";
    pill.dataset.cmd = option.cmd;
    pill.addEventListener("click", (event) => {
      event.stopPropagation();
      if (typeof handlers?.onAction === "function") handlers.onAction(option.cmd, block);
    });
    pills.append(pill);
  }
  card.append(pills);
  const row = actionsRow(block, handlers);
  if (row) card.append(row);
  return card;
}

function renderSpotlightCard(block, handlers) {
  const title = text(block?.title, 80);
  if (!title) return null;
  const card = el("article", "aqi-block aqi-spotlight");
  card.dataset.kind = "spotlight_card";
  card.append(head("spotlight_card", title, "✦"));
  if (text(block?.summary, 400)) card.append(el("p", "aqi-line", text(block.summary, 400)));
  if (text(block?.intercepted, 400)) {
    card.append(el("p", "aqi-line aqi-intercepted", `拦了：${text(block.intercepted, 400)}`));
  }
  const artifacts = list(block?.artifacts)
    .filter((item) => item && typeof item === "object" && text(item.ref, 200))
    .slice(0, MAX_ARTIFACTS);
  if (artifacts.length) {
    const box = el("ul", "aqi-artifacts");
    for (const artifact of artifacts) {
      const kind = text(artifact.kind, 40) || "成果";
      box.append(el("li", "aqi-artifact", `${kind} · ${text(artifact.ref, 200)}`));
    }
    card.append(box);
  }
  const row = actionsRow(block, handlers);
  if (row) card.append(row);
  return card;
}

function renderAlertCard(block, handlers) {
  const title = text(block?.title, 80);
  if (!title) return null;
  const level = LEVELS.includes(block?.level) ? block.level : "warn";
  const card = el("article", `aqi-block aqi-alert is-${level}`);
  card.dataset.kind = "alert_card";
  card.dataset.level = level;
  card.append(head("alert_card", title, level === "critical" ? "⚠" : "!"));
  if (text(block?.detail, 400)) card.append(el("p", "aqi-line", text(block.detail, 400)));
  const row = actionsRow(block, handlers);
  if (row) card.append(row);
  return card;
}

function renderSwimlaneStrip(block, handlers) {
  const lanes = list(block?.lanes)
    .filter((lane) => lane && typeof lane === "object" && text(lane.agent_id, 60))
    .slice(0, MAX_LANES);
  if (!lanes.length) return null;
  const card = el("article", "aqi-block aqi-swimlane");
  card.dataset.kind = "swimlane_strip";
  card.append(head("swimlane_strip", "手下正在干活", "≋"));
  const box = el("div", "aqi-lanes");
  for (const lane of lanes) {
    const status = COMMISSION_STATUSES.includes(lane.status) ? lane.status : "running";
    const row = el("div", "aqi-lane");
    row.dataset.status = status;
    row.append(el("span", "aqi-lane-agent", text(lane.agent_name, 60) || text(lane.agent_id, 60)));
    const title = text(lane.title, 80);
    if (title) row.append(el("span", "aqi-lane-title", title));
    row.append(el("span", "aqi-lane-status", STATUS_LABELS[status] || status));
    box.append(row);
  }
  card.append(box);
  const row = actionsRow(block, handlers);
  if (row) card.append(row);
  return card;
}

function renderMetricFlame(block, handlers) {
  const metrics = list(block?.metrics)
    .filter((metric) => metric && typeof metric === "object" && text(metric.label, 40))
    .slice(0, MAX_METRICS);
  if (!metrics.length) return null;
  const card = el("article", "aqi-block aqi-flame");
  card.dataset.kind = "metric_flame";
  card.append(head("metric_flame", "这一把烧掉的", "♨"));
  const numbers = metrics
    .map((metric) => (typeof metric.value === "number" && Number.isFinite(metric.value) ? metric.value : null))
    .filter((value) => value !== null);
  const peak = numbers.length ? Math.max(...numbers) : 0;
  const box = el("div", "aqi-metrics");
  for (const metric of metrics) {
    const row = el("div", "aqi-metric");
    row.append(el("span", "aqi-metric-label", text(metric.label, 40)));
    const value = typeof metric.value === "number" && Number.isFinite(metric.value)
      ? String(metric.value)
      : text(metric.value, 40);
    row.append(el("span", "aqi-metric-value", value || "—"));
    if (typeof metric.value === "number" && Number.isFinite(metric.value) && peak > 0) {
      const bar = el("span", "aqi-metric-bar");
      const fill = el("span", "aqi-metric-fill");
      fill.style.width = `${Math.max(4, (metric.value / peak) * 100)}%`;
      bar.append(fill);
      row.append(bar);
    }
    box.append(row);
  }
  card.append(box);
  if (text(block?.note, 400)) card.append(el("p", "aqi-line aqi-note", text(block.note, 400)));
  const row = actionsRow(block, handlers);
  if (row) card.append(row);
  return card;
}

function renderNoteSnippet(block, handlers) {
  const body = text(block?.text, 400);
  if (!body) return null;
  const card = el("article", "aqi-block aqi-note");
  card.dataset.kind = "note_snippet";
  card.append(head("note_snippet", text(block?.title, 80) || "她翻到一段", "❋"));
  card.append(el("p", "aqi-line aqi-note-body", body));
  if (text(block?.ref, 200)) card.append(el("small", "aqi-ref", text(block.ref, 200)));
  const row = actionsRow(block, handlers);
  if (row) card.append(row);
  return card;
}

const RENDERERS = {
  choice_pills: renderChoicePills,
  spotlight_card: renderSpotlightCard,
  alert_card: renderAlertCard,
  swimlane_strip: renderSwimlaneStrip,
  metric_flame: renderMetricFlame,
  note_snippet: renderNoteSnippet,
  memo_note: renderMemoNote,
};

/** 渲染单块；未知 kind 返回 null（安全降级，不抛错）。 */
export function renderAqiBlock(block, handlers = {}) {
  if (!block || typeof block !== "object") return null;
  const render = RENDERERS[block.kind];
  if (!render) return null;
  try {
    return decorate(render(block, handlers), block);
  } catch (_) {
    return null;
  }
}

/** 消息级摘要：校验信封 + 统计可渲染块数。纯函数，node 可测。 */
export function summarizeAqiBlocks(payload) {
  if (!payload || typeof payload !== "object") return null;
  const blocks = list(payload.blocks).filter((block) => block && typeof block === "object");
  const renderable = blocks.filter((block) => RENDERERS[block.kind]).length;
  if (!renderable) return null;
  return {
    count: renderable,
    layout: LAYOUTS.includes(payload.layout) ? payload.layout : "stage_holo",
    nav: NAV_TARGETS.includes(payload.nav) ? payload.nav : null,
    gaze: GAZE_TARGETS.includes(payload.gaze) ? payload.gaze : null,
    source: SOURCES.includes(payload.source) ? payload.source : "chat",
  };
}

/** 全息卡片区：积木在这里堆叠，超过 MAX_CARDS 收最旧的；空消息不渲染。 */
export function createHoloDeck(mount, handlers = {}) {
  if (!mount) return null;
  let count = 0;
  function render(payload) {
    const summary = summarizeAqiBlocks(payload);
    if (!summary) return 0;
    /* 曾经打算做"阶梯入场"：给每张卡写 --aqi-i，由 CSS 的
     * animation-delay: calc(var(--aqi-i)*45ms) 消费（设计记录见
     * web/archive/design-preview/NOTES.md，当时实测第 3 张 delay 90ms）。
     * 但那条 CSS 从未落进 companion.css / almanac.css——变量写了没人读，
     * 错峰一次都没发生过。2026-10-18 删掉这段死接线，免得下一个人按注释
     * 去找一条不存在的规则。真要恢复错峰，补 CSS 时把这两行一起加回来。 */
    for (const block of list(payload.blocks)) {
      const card = renderAqiBlock(block, handlers);
      if (!card) continue;
      mount.append(card);
      count += 1;
    }
    while (mount.firstChild && count > MAX_CARDS) {
      mount.removeChild(mount.firstChild);
      count -= 1;
    }
    mount.hidden = count === 0;
    mount.scrollTop = mount.scrollHeight;
    return count;
  }
  function clear() {
    mount.replaceChildren();
    mount.hidden = true;
    count = 0;
  }
  mount.hidden = true;
  return {
    render,
    clear,
    get count() { return count; },
  };
}
