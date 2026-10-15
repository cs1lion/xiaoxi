/* 工作台抽屉（打磨轮 U-3）：六个副玩法视图的统一导航，用户不再离开角色。
 * 一起写 / 共同事项 / 星图保持既有集成深度——调用既有入口，不复制编辑器或图谱实例；
 * 音乐 / 实时字幕用 iframe 内嵌既有页面（不新建 HTML），原整页入口保留为宽屏模式；
 * 英语练习按 U-4 落在设置面板，这里负责跳转。
 * 星图入口 (#starmapToggle) 的点击由 starmap/index.js 自己绑定，这里只靠
 * aqi-fullview 广播收起抽屉，避免同一个按钮被两个模块重复开关。 */

const el = (id) => document.getElementById(id);
const FULLVIEW_EVENT = "aqi-fullview";

export function mountWorkDrawer(root, deps = {}) {
  if (!root) return null;
  const { onWriting, onCompanion, onPractice } = deps;
  const toggle = el("drawerToggle");
  const closeBtn = el("drawerClose");
  const nav = [...root.querySelectorAll(".drawer-nav button")];
  const views = {
    writing: el("drawerViewWriting"),
    companion: el("drawerViewCompanion"),
    starmap: el("drawerViewStarmap"),
    music: el("drawerViewMusic"),
    interpret: el("drawerViewInterpret"),
    practice: el("drawerViewPractice"),
    ops: el("drawerViewOps"),
  };
  let opened = false;
  let lastFocus = null;
  let current = "writing";

  const lazyEmbed = (view) => {
    const frame = view.querySelector("iframe[data-src]");
    if (frame && !frame.getAttribute("src")) frame.src = frame.dataset.src;
  };

  function selectView(name) {
    if (!views[name]) return;
    current = name;
    // V3：通知外部模块（督办与战况视图）该刷新了；不知道这个事件的模块不受影响。
    window.dispatchEvent(new CustomEvent("aqi-drawer-view", { detail: name }));
    for (const [key, view] of Object.entries(views)) view.hidden = key !== name;
    for (const button of nav) {
      if (!button.hasAttribute("aria-pressed")) continue;
      button.setAttribute("aria-pressed", String(button.id === `drawer${name[0].toUpperCase()}${name.slice(1)}`));
    }
    lazyEmbed(views[name]);
  }

  function open(focusFirst = true) {
    if (opened) return;
    opened = true;
    lastFocus = document.activeElement;
    root.hidden = false;
    toggle?.setAttribute("aria-expanded", "true");
    if (focusFirst) (nav[0] || closeBtn)?.focus({ preventScroll: true });
  }

  function close(restoreFocus = true) {
    if (!opened) return;
    opened = false;
    root.hidden = true;
    toggle?.setAttribute("aria-expanded", "false");
    if (restoreFocus) {
      const target = toggle && document.contains(toggle) ? toggle : lastFocus;
      if (target && typeof target.focus === "function") target.focus({ preventScroll: true });
    }
  }

  toggle?.addEventListener("click", () => (opened ? close() : open()));
  closeBtn?.addEventListener("click", () => close());

  el("drawerWriting")?.addEventListener("click", () => {
    if (!document.body.classList.contains("is-writing")) onWriting?.();
    close(false);
  });
  el("drawerCompanion")?.addEventListener("click", () => {
    close(false);
    onCompanion?.();
  });
  el("drawerPractice")?.addEventListener("click", () => {
    close(false);
    onPractice?.();
  });
  el("drawerMusic")?.addEventListener("click", () => selectView("music"));
  el("drawerInterpret")?.addEventListener("click", () => selectView("interpret"));
  el("drawerOps")?.addEventListener("click", () => selectView("ops"));
  el("drawerWritingOpen")?.addEventListener("click", () => {
    if (!document.body.classList.contains("is-writing")) onWriting?.();
    close(false);
  });
  el("drawerCompanionOpen")?.addEventListener("click", () => {
    close(false);
    onCompanion?.();
  });
  el("drawerStarmapOpen")?.addEventListener("click", () => {
    // 星图全屏视图由 starmap/index.js 的入口绑定打开；这里只收起抽屉。
    close(false);
    el("starmapToggle")?.click();
  });
  el("drawerPracticeOpen")?.addEventListener("click", () => {
    close(false);
    onPractice?.();
  });

  window.addEventListener("keydown", (event) => {
    if (opened && event.key === "Escape" && !event.isComposing) {
      event.preventDefault();
      close();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!opened) return;
    if (root.contains(event.target) || toggle?.contains(event.target)) return;
    close(false);
  });
  window.addEventListener(FULLVIEW_EVENT, () => close(false));

  selectView("writing");
  return {
    get opened() { return opened; },
    get current() { return current; },
    open,
    close,
    toggle: () => (opened ? close() : open()),
    selectView,
  };
}
