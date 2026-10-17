/* V3 界面自动领航（docs/PROTOCOL.md §二十四）回归：领航纯逻辑（零 DOM、零网络）。
 *
 * 跑法：node web/ui_nav.test.mjs
 * 覆盖：
 *   - 白名单取值域与协议§二十四逐项对齐（前后端不漂移）
 *   - 未知/缺省取值全部安全降级（旧客户端拿到新字段不炸）
 *   - 心流护栏：缺 turn_id 的后台事件一律降级为"只指不拉"（auto_open=false）
 *   - 抽屉/侧栏/星图/收起四种落点互斥、不错切
 */
import assert from "node:assert/strict";

import {
  UI_NAV_TARGETS,
  UI_NAV_SUB_ACTIONS,
  UI_NAV_GAZE_TARGETS,
  normalizeUiNav,
  drawerViewFor,
  shouldCloseDrawer,
  shouldOpenSidePanel,
  shouldOpenStarmap,
  gazeAfter,
} from "./ui_nav.js";

const checks = [];
const check = (ok, label) => {
  assert.ok(ok, label);
  checks.push(label);
};

/* ---- 取值域与协议 §二十四 对齐 ---- */
check(
  JSON.stringify([...UI_NAV_TARGETS].sort()) === JSON.stringify(["close", "notes", "ops", "starmap", "threads"]),
  "target_view 白名单 = 协议登记的 5 个取值"
);
check(
  JSON.stringify([...UI_NAV_SUB_ACTIONS].sort()) === JSON.stringify(["focus_latest", "highlight_rubrics", "highlight_telemetry", "open_preview"]),
  "sub_action 白名单 = 协议登记的 4 个取值"
);
check(
  UI_NAV_GAZE_TARGETS.includes("viewer") && UI_NAV_GAZE_TARGETS.includes("side"),
  "gaze_target 白名单含 viewer 与 side（avatar.yaml 的 gaze.targets）"
);

/* ---- 正常领航：委托派单 ---- */
{
  const nav = normalizeUiNav({
    type: "ui_nav",
    turn_id: "client-1",
    target_view: "ops",
    focus_id: "latest",
    sub_action: "focus_latest",
    gaze_target: "side",
  });
  check(nav.auto_open === true, "带 turn_id 的用户轮次领航允许立即拉开抽屉");
  check(nav.target_view === "ops", "target_view 原样透传");
  check(nav.focus_id === "latest", "focus_id 原样透传");
  check(nav.sub_action === "focus_latest", "sub_action 原样透传");
  check(nav.gaze_target === "side", "gaze_target 原样透传");
  check(drawerViewFor(nav) === "ops", "ops 领航切抽屉 ops 标签");
  check(shouldOpenSidePanel(nav) === false && shouldOpenStarmap(nav) === false && shouldCloseDrawer(nav) === false,
    "ops 领航不越权拉侧栏/星图/收起");
  check(gazeAfter(nav) === "side", "领航后视线留在 side");
}

/* ---- 正常领航：进度追问（具体 commission_id）---- */
{
  const nav = normalizeUiNav({
    type: "ui_nav",
    turn_id: "client-2",
    target_view: "ops",
    focus_id: "c_1f0a",
    gaze_target: "side",
  });
  check(nav.focus_id === "c_1f0a" && nav.sub_action === null,
    "进度领航带具体 focus_id、无 sub_action 也放行");
  check(drawerViewFor(nav) === "ops", "进度领航也切 ops 标签");
}

/* ---- 正常领航：验收收工 open_preview ---- */
{
  const nav = normalizeUiNav({
    type: "ui_nav",
    turn_id: "client-3",
    target_view: "ops",
    focus_id: "c_1f0a",
    sub_action: "open_preview",
    gaze_target: "side",
  });
  check(nav.sub_action === "open_preview", "open_preview 子动作放行");
}

/* ---- 正常领航：看星图 ---- */
{
  const nav = normalizeUiNav({
    type: "ui_nav",
    turn_id: "client-4",
    target_view: "starmap",
    gaze_target: "side",
  });
  check(shouldOpenStarmap(nav) === true, "星图领航拉全屏视图");
  check(drawerViewFor(nav) === null, "星图领航不切抽屉标签（starmap 是全屏视图，非抽屉标签）");
  check(shouldOpenSidePanel(nav) === false, "星图领航不越权拉侧栏");
}

/* ---- 正常领航：看笔记/便笺 → 侧栏行动面板 ---- */
{
  const nav = normalizeUiNav({
    type: "ui_nav",
    turn_id: "client-5",
    target_view: "threads",
    focus_id: "latest",
    sub_action: "focus_latest",
    gaze_target: "side",
  });
  check(shouldOpenSidePanel(nav) === true, "threads 领航拉侧栏行动面板");
  check(drawerViewFor(nav) === null, "threads 领航不切抽屉标签");
  const notes = normalizeUiNav({
    type: "ui_nav",
    turn_id: "client-6",
    target_view: "notes",
    sub_action: "focus_latest",
  });
  check(shouldOpenSidePanel(notes) === true, "notes 与 threads 同路拉侧栏（未实现视图安全降级）");
}

/* ---- 正常领航：close（视线回收）---- */
{
  const nav = normalizeUiNav({ type: "ui_nav", turn_id: "client-7", target_view: "close" });
  check(shouldCloseDrawer(nav) === true, "close 领航收起抽屉");
  check(gazeAfter(nav) === "viewer", "close 领航把视线收回 viewer");
}

/* ---- 安全降级：未知取值 ---- */
{
  const nav = normalizeUiNav({
    type: "ui_nav",
    turn_id: "client-8",
    target_view: "somewhere_else",
    sub_action: "wiggle",
    focus_id: 42,
    gaze_target: "teleport",
  });
  check(nav.target_view === "close", "未知 target_view 降级为 close（收手）");
  check(nav.sub_action === null, "未知 sub_action 置 null（不展开子视图）");
  check(nav.focus_id === null, "非字符串 focus_id 置 null");
  check(nav.gaze_target === "viewer", "未知 gaze_target 回落 viewer");
  check(nav.ignored.includes("target_view:somewhere_else"), "被降级的 target_view 记进 ignored，不静默吞掉");
  check(nav.ignored.includes("sub_action:wiggle"), "被降级的 sub_action 记进 ignored，不静默吞掉");
}

/* ---- 安全降级：缺省字段 ---- */
{
  const nav = normalizeUiNav({ type: "ui_nav", turn_id: "client-9" });
  check(nav.target_view === "close", "缺 target_view 降级为 close");
  check(nav.gaze_target === "viewer", "缺 gaze_target 回落 viewer");
}

/* ---- 心流护栏：后台事件（缺 turn_id）只指不拉 ---- */
{
  const nav = normalizeUiNav({
    type: "ui_nav",
    target_view: "ops",
    sub_action: "focus_latest",
    gaze_target: "side",
  });
  check(nav.auto_open === false, "缺 turn_id 的后台 ops 领航不立即拉开抽屉（心流防打扰）");
  check(nav.target_view === "ops" && nav.sub_action === "focus_latest",
    "降级时其它领航字段仍透传（只影响 auto_open，不连累）");
}

/* ---- 降噪放行：close 类领航缺 turn_id 也放行（收手不是打扰）---- */
{
  const closeNav = normalizeUiNav({ type: "ui_nav", target_view: "close" });
  check(closeNav.auto_open === true, "close 领航缺 turn_id 也放行（降噪方向，无害）");
  const unknownNav = normalizeUiNav({ type: "ui_nav", target_view: "somewhere" });
  check(unknownNav.auto_open === true && unknownNav.target_view === "close",
    "未知 target_view 降级为 close 后同样放行（降噪方向兜底）");
}

/* ---- 空输入安全降级 ---- */
{
  const nav = normalizeUiNav(null);
  check(nav.target_view === "close", "空输入 target_view 降级为 close");
  // close 类领航按降噪语义放行（消费方判断"已开才收"），
  // 但空输入没有真实来源，保留 auto_open=true 也无副作用——
  // 消费方对 close 的唯一动作是"抽屉当前开着才收起"，不会误开任何面板。
  check(nav.target_view === "close" && nav.ignored.length === 0, "空输入安全降级，不静默吞掉");
}

console.log(`PASS  ${checks.length} checks`);
