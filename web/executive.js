/* V3 督办与战况抽屉视图（docs/EXECUTIVE_SECRETARY_V3.md §四）。
 *
 * 铁律：主舞台 0 新增按钮。这里只渲染侧滑工作台抽屉里的内容，
 * 复用既有入口（#textInput / #drawerClose），不新建页面、不复制编辑器实例。
 *
 * 心流防打扰：用户在高频打字（笔记编辑器 / 聊天输入 / 一起写）时，
 * 新完结的督办只亮胶囊呼吸灯 + 一条静音气泡，不自动开抽屉、不发声音、不抢焦点。
 */

import { createFlowGuard, pendingMessage, shouldHighlight } from "./executive-state.js";

const el = (id) => document.getElementById(id);

const STATUS_LABELS = {
  drafting: "组密件",
  briefing: "已派将",
  running: "干活中",
  blocked: "等你拍板",
  auditing: "初审中",
  ready_to_report: "等你验收",
  done: "已归档",
};
const POLL_MS = 4000;
const MAX_CARDS = 20;

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text != null) item.textContent = text;
  return item;
}

function readJson(response) {
  if (!response.ok) throw new Error(`http_${response.status}`);
  return response.json();
}

function post(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  }).then(readJson);
}

function line(label, value) {
  const row = node("p", "ops-line");
  row.append(node("span", "ops-line-label", label));
  row.append(node("span", "ops-line-value", value || "（这一栏它没填）"));
  return row;
}

/* ops 视图专属的定位器：ui_nav.js 保持零 DOM 纯逻辑，落点映射留在这里。
 * focus_id 命中 .ops-card[data-commission-id]；"latest" / 无 focus 退到最近一张卡。 */
function opsFocusSelector(nav) {
  const id = nav.focus_id && nav.focus_id !== "latest"
    ? String(nav.focus_id).replace(/[^a-zA-Z0-9_-]/g, "") : "";
  if (id) return `.ops-card[data-commission-id="${id}"]`;
  if (nav.sub_action === "focus_latest" || nav.focus_id === "latest") {
    return ".ops-list .ops-card:last-child";
  }
  return null;
}

function opsSubActionSelector(nav) {
  switch (nav.sub_action) {
    case "highlight_telemetry": return "#opsTelemetry";
    case "highlight_rubrics": return "#opsRubrics";
    case "open_preview": {
      const id = nav.focus_id && nav.focus_id !== "latest"
        ? String(nav.focus_id).replace(/[^a-zA-Z0-9_-]/g, "") : "";
      const cardSel = id ? `.ops-card[data-commission-id="${id}"] .ops-delivery`
        : ".ops-list .ops-card:last-child .ops-delivery";
      return cardSel;
    }
    default: return null;
  }
}

export function mountExecutive() {
  const root = el("drawerViewOps");
  const list = el("opsCommissions");
  const pending = el("opsPending");
  const telemetryBox = el("opsTelemetry");
  const rubricBox = el("opsRubrics");
  if (!root || !list || !telemetryBox || !rubricBox) return null;

  const state = { seen: new Set(), lastKeyAt: 0, timer: null, active: false, revision: 0, highlight: "" };
  const flow = createFlowGuard({ isWriting: () => document.body.classList.contains("is-writing") });

  // 抽屉内部的操作是"正在办这件事"，不算打扰；其余地方的按键才算。
  document.addEventListener(
    "keydown",
    (event) => {
      if (root.contains(event.target)) return;
      flow.markTyping();
    },
    true,
  );
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    if (event.data && event.data.type === "aqi-notes-typing") flow.markTyping();
  });

  function agentName(item) {
    return item.agent_id ? `${item.agent_id}` : "还没选将";
  }

  function deliveryCard(item, quiet) {
    const delivery = item.delivery || {};
    const box = node("div", "ops-delivery");
    box.dataset.quiet = quiet ? "1" : "0";
    box.append(node("h6", "ops-delivery-title", "三句话交付验收卡"));
    box.append(line("做了什么", delivery.summary));
    box.append(line("小溪替你拦了什么", delivery.intercepted));
    const artifacts = node("ul", "ops-artifacts");
    artifacts.hidden = true;
    const items = delivery.artifacts || [];
    if (!items.length) artifacts.append(node("li", "ops-hint", "没有列出任何成果 ref。"));
    for (const artifact of items) {
      artifacts.append(
        node("li", "ops-artifact", `${artifact.kind || "成果"} · ${artifact.ref || "（无 ref）"}`),
      );
    }
    const actions = node("div", "ops-actions");
    const preview = node("button", "ghost", "预览");
    preview.type = "button";
    preview.setAttribute("aria-expanded", "false");
    preview.addEventListener("click", () => {
      artifacts.hidden = !artifacts.hidden;
      preview.setAttribute("aria-expanded", String(!artifacts.hidden));
    });
    const rework = node("button", "ghost", "语音返修");
    rework.type = "button";
    rework.addEventListener("click", () => {
      // 返修理由交给语音快环：把话说一半，老板接着说完就是打回依据。
      const input = el("textInput");
      if (input) {
        input.value = "这单不行：";
        input.focus({ preventScroll: true });
      }
      el("drawerClose")?.click();
    });
    const done = node("button", "primary", "收工");
    done.type = "button";
    done.addEventListener("click", async () => {
      done.disabled = true;
      try {
        await post(`/api/executive/commissions/${encodeURIComponent(item.id)}/accept`, {});
      } catch (_) {
        done.disabled = false;
        return;
      }
      refresh();
    });
    actions.append(preview, rework, done);
    box.append(artifacts, actions);
    return box;
  }

  function commissionCard(item, quiet) {
    const card = node("article", "ops-card");
    card.dataset.status = item.status;
    card.dataset.commissionId = item.id;
    card.dataset.quiet = String(!shouldHighlight(quiet));
    // 协议 §23：积木 ui:preview:<ref> 的高亮联动（ref 命中这一单时描金边）。
    if (state.highlight && item.id === state.highlight) card.classList.add("is-highlighted");
    const head = node("div", "ops-card-head");
    head.append(node("h5", "ops-card-title", item.title || "未命名督办"));
    head.append(node("span", "ops-pill", STATUS_LABELS[item.status] || item.status));
    card.append(head);
    card.append(
      node(
        "p",
        "ops-meta",
        `${agentName(item)} · 第 ${item.step_idx || 0} 步 · 已打回 ${item.retry_count || 0} 次`,
      ),
    );
    if (item.blocked_question) {
      const ask = node("div", "ops-ask");
      ask.append(node("p", "ops-ask-text", item.blocked_question));
      const field = node("input", "ops-ask-input");
      field.type = "text";
      field.placeholder = "不回就按琐事代答处理";
      field.setAttribute("aria-label", "回答小溪的请示");
      const send = node("button", "ghost", "答她");
      send.type = "button";
      const submit = async () => {
        send.disabled = true;
        try {
          await post(`/api/executive/commissions/${encodeURIComponent(item.id)}/answer`, {
            answer: field.value,
          });
        } catch (_) {
          send.disabled = false;
          return;
        }
        refresh();
      };
      send.addEventListener("click", submit);
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter") submit();
      });
      ask.append(field, send);
      card.append(ask);
    }
    const audit = item.audit || {};
    const findings = audit.findings || [];
    if (findings.length) {
      const box = node("div", "ops-audit");
      box.append(node("h6", "ops-audit-title", `初审打回（${audit.layer || "?"}）`));
      for (const finding of findings) {
        box.append(node("li", "ops-finding", `[${finding.severity}] ${finding.detail}`));
      }
      if (audit.scolding) box.append(node("p", "ops-scolding", audit.scolding));
      if (audit.learned_rubric) {
        box.append(node("p", "ops-learned", `这条已进判例库：${audit.learned_rubric}`));
      }
      card.append(box);
    }
    if (item.status === "ready_to_report") card.append(deliveryCard(item, quiet));
    return card;
  }

  function renderCommissions(items) {
    const quiet = flow.isQuiet();
    const fresh = items.filter((item) => item.status === "ready_to_report" && !state.seen.has(item.id));
    for (const item of fresh) state.seen.add(item.id);
    pending.replaceChildren();
    pending.hidden = fresh.length === 0;
    if (fresh.length) {
      pending.dataset.quiet = String(quiet);
      pending.append(node("span", "ops-breathe"));
      pending.append(
        node("span", "ops-pending-text", pendingMessage(fresh.length, quiet)),
      );
    }
    list.replaceChildren();
    if (!items.length) {
      list.append(node("p", "ops-hint", "还没有派出去的单。说一句「去把这个整理一下」就有人接了。"));
      return;
    }
    for (const item of items.slice(0, MAX_CARDS)) list.append(commissionCard(item, quiet));
  }

  function renderTelemetry(summary, report) {
    telemetryBox.replaceChildren();
    if (!summary || !summary.calls) {
      telemetryBox.append(node("p", "ops-hint", "今天还没开过工，没有可报的战况。"));
      return;
    }
    const rate = summary.success_rate == null ? "—" : `${(summary.success_rate * 100).toFixed(1)}%`;
    const grid = node("div", "ops-stat-grid");
    for (const [label, value] of [
      ["调用次数", String(summary.calls)],
      ["成功率", rate],
      ["Prompt Tokens", String(summary.prompt_tokens)],
      ["Completion Tokens", String(summary.completion_tokens)],
      ["首包平均", summary.avg_ttft_ms == null ? "—" : `${Math.round(summary.avg_ttft_ms)}ms`],
      ["最慢首包", summary.max_ttft_ms == null ? "—" : `${summary.max_ttft_ms}ms`],
    ]) {
      const cell = node("div", "ops-stat");
      cell.append(node("span", "ops-stat-label", label));
      cell.append(node("span", "ops-stat-value", value));
      grid.append(cell);
    }
    telemetryBox.append(grid);
    const ranking = node("ol", "ops-ranking");
    for (const item of summary.token_ranking || []) {
      ranking.append(node("li", "ops-rank-item", `${item.agent_id} · ${item.total_tokens}`));
    }
    if ((summary.token_ranking || []).length) telemetryBox.append(ranking);
    const alerts = summary.spinning_alerts || [];
    if (alerts.length) {
      telemetryBox.append(
        node("p", "ops-alert", `鬼打墙熔断 ${alerts.length} 次：${alerts.map((a) => a.agent_id).join("、")}`),
      );
    }
    if (report) telemetryBox.append(node("p", "ops-note-text", report));
  }

  function renderRubrics(items) {
    rubricBox.replaceChildren();
    if (!items.length) {
      rubricBox.append(node("p", "ops-hint", "判例库是空的。"));
      return;
    }
    for (const rubric of items) {
      const card = node("div", "ops-rubric");
      card.append(
        node(
          "p",
          "ops-rubric-head",
          `${rubric.id} · ${rubric.layer} · ${rubric.severity} · ${rubric.source === "boss_rejection" ? "你教的" : "种子"}`,
        ),
      );
      card.append(node("p", "ops-rubric-rule", rubric.rule));
      if (rubric.evidence) card.append(node("p", "ops-rubric-evidence", rubric.evidence));
      if (rubric.source === "boss_rejection") {
        const remove = node("button", "ghost", "删掉这条");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          remove.disabled = true;
          try {
            await post(`/api/executive/rubrics/${encodeURIComponent(rubric.id)}/delete`, {});
          } catch (_) {
            remove.disabled = false;
            return;
          }
          refresh();
        });
        card.append(remove);
      }
      rubricBox.append(card);
    }
  }

  async function refresh() {
    const revision = ++state.revision;
    try {
      const [commissions, telemetry, rubrics] = await Promise.all([
        fetch("/api/executive/commissions?limit=20").then(readJson),
        fetch("/api/executive/telemetry").then(readJson),
        fetch("/api/executive/rubrics").then(readJson),
      ]);
      if (revision !== state.revision) return;
      renderCommissions(commissions.items || []);
      renderTelemetry(telemetry, "");
      renderRubrics(rubrics.items || []);
      fetch("/api/executive/telemetry/report")
        .then(readJson)
        .then((data) => {
          if (revision === state.revision && data && data.text) {
            telemetryBox.append(node("p", "ops-note-text", data.text));
          }
        })
        .catch(() => {});
    } catch (_) {
      if (revision !== state.revision) return;
      list.replaceChildren(node("p", "ops-hint", "台账暂时读不到。语音和笔记都不受影响。"));
    }
  }

  function start() {
    if (state.timer) return;
    state.timer = setInterval(() => {
      if (state.active) refresh();
    }, POLL_MS);
  }
  function stop() {
    if (!state.timer) return;
    clearInterval(state.timer);
    state.timer = null;
  }

  window.addEventListener("aqi-drawer-view", (event) => {
    state.active = event.detail === "ops";
    if (state.active) {
      refresh();
      start();
    } else {
      stop();
    }
  });
  // 协议 §23.3：积木 ui:preview:<ref> —— 打开战况抽屉并高亮对应成果 ref。
  window.addEventListener("aqi-block-preview", (event) => {
    const ref = event.detail?.ref;
    if (typeof ref !== "string" || !ref || ref.length > 200) return;
    state.highlight = ref;
    refresh();
  });

  /* §二十四 界面自动领航：app.js 收到 ui_nav 后广播 aqi-ui-nav，
   * 该视图在 active 时消费——定位目标卡、展开子动作、加 .nav-spotlight 动效。
   * 抽屉标签页切换与视线微转（gaze_target）由 app.js 直接做，这里只管"指给谁看"。
   * 心流护栏：领航只在 aqi-ui-nav 的 detail.auto_open 为 true 时才立即动手；
   * 后台被动事件（缺 turn_id）在 app.js 那一层就被降级为 auto_open=false。
   *
   * 竞态说明：refresh() 会 replaceChildren 重渲染卡片列表，抹掉刚加的
   * .nav-spotlight 与展开态。所以把"应用领航"提取成可重放函数，refresh()
   * 完成后再回放一次当前 nav，保证高亮与子动作稳定可见。 */
  let lastNav = null;
  function applyNav(nav) {
    if (!nav || nav.target_view !== "ops" || !state.active || !nav.auto_open) return;
    lastNav = nav;
    const focusSel = opsFocusSelector(nav);
    if (focusSel) {
      const card = root.querySelector(focusSel);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.classList.add("nav-spotlight");
        setTimeout(() => card.classList.remove("nav-spotlight"), 1600);
      }
    }
    const subSel = opsSubActionSelector(nav);
    if (subSel) {
      const target = root.querySelector(subSel);
      if (target) {
        if (nav.sub_action === "open_preview") {
          const box = target.closest(".ops-delivery") || target;
          const artifacts = box.querySelector(".ops-artifacts");
          if (artifacts) artifacts.hidden = false;
          const previewBtn = box.querySelector("button[aria-expanded]");
          if (previewBtn && artifacts) previewBtn.setAttribute("aria-expanded", "true");
          box.classList.add("nav-spotlight");
          setTimeout(() => box.classList.remove("nav-spotlight"), 1600);
        } else {
          target.scrollIntoView({ behavior: "smooth", block: "nearest" });
          target.classList.add("nav-spotlight");
          setTimeout(() => target.classList.remove("nav-spotlight"), 1600);
        }
      }
    }
  }

  window.addEventListener("aqi-ui-nav", (event) => {
    const nav = event.detail;
    applyNav(nav);
    // 列表若是旧的，refresh() 重渲染后再回放一次领航，保证定位落到真实数据。
    refresh().then(() => { if (lastNav === nav) applyNav(nav); });
  });
  refresh();
  return { refresh, get active() { return state.active; } };
}

mountExecutive();
