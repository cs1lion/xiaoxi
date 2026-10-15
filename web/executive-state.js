/* V3 心流防打扰的纯决策逻辑（EXECUTIVE_SECRETARY_V3.md §四）。
 *
 * 规矩只有一条：用户正在打字时，新完结的督办只亮胶囊呼吸灯 + 一条静音气泡，
 * 不自动开抽屉、不发声音、不抢焦点。零 DOM、零导入，便于 node 单测——
 * 这是用户体验的主观闭环，不能靠肉眼盲测。
 */

export const QUIET_MS = 1200;

/** 建一个流量守卫。`now` / `isWriting` 都可注入，测试里不必等真实时间。 */
export function createFlowGuard({ quietMs = QUIET_MS, now = () => Date.now(), isWriting = () => false } = {}) {
  let lastKeyAt = 0;
  const moment = (at) => (typeof at === "number" ? at : now());
  return {
    markTyping(at) {
      lastKeyAt = moment(at);
      return lastKeyAt;
    },
    get lastKeyAt() {
      return lastKeyAt;
    },
    /** 此刻是否该安静。写作宽屏视图里一律安静——她占着整个屏幕。 */
    isQuiet(at) {
      if (isWriting()) return true;
      return moment(at) - lastKeyAt < quietMs;
    },
    reset() {
      lastKeyAt = 0;
    },
  };
}

/** 待验收胶囊上那句话。安静时不出声，只报数。 */
export function pendingMessage(count, quiet) {
  const total = Number(count) || 0;
  if (total <= 0) return "";
  return quiet
    ? `${total} 单结了。你在忙，我先不出声，灯给你留着。`
    : `${total} 单等你验收。`;
}

/** 卡片是否该高亮。安静时不高亮，避免视觉上也在催。 */
export function shouldHighlight(quiet) {
  return quiet !== true;
}
