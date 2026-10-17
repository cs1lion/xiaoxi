/* V3 界面自动领航（docs/PROTOCOL.md §二十四）：把服务端 ui_nav 载荷翻译成
 * 主舞台上的一次"她亲手拉开面板指给你看"。
 *
 * 铁律照旧：主舞台 0 新增按钮——领航只是替老板点掉他本来要点的那几下。
 * 心流护栏：本模块只处理**用户主动输入的轮次**发下来的领航（该路径天然
 * 带 turn_id）；后台被动事件根本不会携带 ui_nav，若真混进来（缺 turn_id），
 * 一律降级为"只指不拉"——视线动一下，抽屉不开。
 *
 * 零 DOM、零导入，便于 node 单测；DOM 落地在 web/app.js 的 case "ui_nav"，
 * ops 视图内的定位器留在 web/executive.js（谁 owns DOM 谁管选择器）。
 */

export const UI_NAV_TARGETS = ["ops", "threads", "starmap", "close", "notes"];
export const UI_NAV_SUB_ACTIONS = [
  "open_preview",
  "highlight_telemetry",
  "highlight_rubrics",
  "focus_latest",
];

/** 注视点名白名单取自 config/avatar.yaml 的 gaze.targets（check_protocol 静态核对）。 */
export const UI_NAV_GAZE_TARGETS = [
  "viewer", "mic", "think", "down", "side",
];

const FALLBACK_GAZE = "viewer";

/**
 * 归一化一条 ui_nav 载荷：未知取值安全降级，绝不抛错、绝不静默吞掉。
 *
 * @returns {{target_view: string, focus_id: string|null, sub_action: string|null,
 *            gaze_target: string, auto_open: boolean, ignored: string[]}}
 *
 * 规则：
 * - target_view 不在白名单 → close（收手，视线回收），原值记进 ignored。
 * - sub_action 不在白名单 → 置 null，原值记进 ignored（抽屉照开，只是不展开子视图）。
 * - focus_id 只接受非空字符串，否则置 null。
 * - gaze_target 未在白名单 → 回落 viewer。
 * - auto_open：只有"看起来是用户这一轮"的领航才允许前端立即动手。
 *   判定：载荷带 turn_id（服务端轮次事件才会附加）。close 类领航是降噪方向
 *   （收手不是打扰），缺 turn_id 也放行；open 类（ops/threads/starmap/notes）
 *   缺 turn_id 一律降级为"只指不拉"，沿用 executive-state.js 的心流防打扰语义。
 */
export function normalizeUiNav(msg) {
  const ignored = [];
  const hasTurn = !!(msg && typeof msg === "object" && msg.turn_id != null && msg.type === "ui_nav");

  let targetView = msg?.target_view;
  if (typeof targetView !== "string" || !UI_NAV_TARGETS.includes(targetView)) {
    if (targetView !== undefined && targetView !== null) ignored.push(`target_view:${targetView}`);
    targetView = "close";
  }

  let subAction = msg?.sub_action;
  if (subAction !== undefined && subAction !== null) {
    if (typeof subAction !== "string" || !UI_NAV_SUB_ACTIONS.includes(subAction)) {
      ignored.push(`sub_action:${subAction}`);
      subAction = null;
    }
  } else {
    subAction = null;
  }

  let focusId = msg?.focus_id;
  if (typeof focusId !== "string" || !focusId) focusId = null;

  let gaze = msg?.gaze_target;
  if (typeof gaze !== "string" || !UI_NAV_GAZE_TARGETS.includes(gaze)) {
    gaze = FALLBACK_GAZE;
  }

  // close 类领航是降噪方向，允许后台事件（缺 turn_id）放行；open 类一律要用户轮次。
  const autoOpen = hasTurn || targetView === "close";
  return { target_view: targetView, focus_id: focusId, sub_action: subAction,
    gaze_target: gaze, auto_open: autoOpen, ignored };
}

/**
 * 抽屉该切到哪张标签页。领航的 target_view 里只有 ops 是抽屉内视图；
 * starmap 走全屏视图（starmap/index.js owns），threads/notes 走侧栏行动面板，
 * close 收起。
 *
 * @returns {null | "ops"} null 表示不切抽屉标签页（交给对应视图模块）。
 */
export function drawerViewFor(nav) {
  if (nav.target_view === "ops") return "ops";
  return null;
}

/** 是否要收起抽屉。 */
export function shouldCloseDrawer(nav) {
  return nav.target_view === "close";
}

/** 是否要拉侧栏行动面板（threads / notes 领航共用一条路）。 */
export function shouldOpenSidePanel(nav) {
  return nav.target_view === "threads" || nav.target_view === "notes";
}

/** 是否要开星图全屏视图。 */
export function shouldOpenStarmap(nav) {
  return nav.target_view === "starmap";
}

/** 领航完成后形象该看哪。close 时回 viewer，其它保持 gaze_target。 */
export function gazeAfter(nav) {
  return nav.target_view === "close" ? "viewer" : nav.gaze_target;
}
