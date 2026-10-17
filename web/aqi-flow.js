/* 协议 §23.6 心流防打扰：aqi_blocks 的待阅队列（纯逻辑，零 DOM，node 可测）。
 *
 * 规矩：用户正在打字（或写作宽屏）时，新到达的 aqi_blocks 不直接弹卡——
 * 原序挂起、点亮战况胶囊呼吸灯并报数；停手（quiet 解除）后按原序补弹到 Holo-Deck。
 * 守卫只延迟"弹卡"：不丢弃、不改内容、不重排。轮次事件本身由 TurnGate 管，
 * 与本模块无关（app.js 在 TurnGate 之后才调到这里）。
 *
 * 与 executive-state.js 的 createFlowGuard 同一条判定（同一个静默窗、同一个
 * is-writing 语义），但那个只管"督办完结要不要出声"，这里管"卡片要不要现在弹"。
 */
export const AQI_QUIET_MS = 1200;

export function createAqiFlow({ quietMs = AQI_QUIET_MS, now = () => Date.now(), isWriting = () => false } = {}) {
  let lastKeyAt = 0;
  let queue = [];
  const moment = (at) => (typeof at === "number" ? at : now());
  return {
    markTyping(at) {
      lastKeyAt = moment(at);
      return lastKeyAt;
    },
    get lastKeyAt() {
      return lastKeyAt;
    },
    /** 此刻该不该安静：写作宽屏一律安静，否则看静默窗。 */
    isQuiet(at) {
      if (isWriting()) return true;
      return moment(at) - lastKeyAt < quietMs;
    },
    /** 安静时把整条消息原序挂起（内容原样，不解析不改写），返回挂起后的待阅数。 */
    hold(message) {
      queue.push(message);
      return queue.length;
    },
    /** 按原序取出全部待阅消息并清空队列。调用方必须先确认 !isQuiet()。 */
    drain() {
      const pending = queue;
      queue = [];
      return pending;
    },
    get count() {
      return queue.length;
    },
    reset() {
      lastKeyAt = 0;
      queue = [];
    },
  };
}

/** 战况胶囊上报数的那句话：安静时只报数不催，停手后才请她翻。 */
export function aqiPendingMessage(count, quiet) {
  const total = Number(count) || 0;
  if (total <= 0) return "";
  return quiet
    ? `她递了 ${total} 张卡。你在忙，我先不出声，灯给你留着。`
    : `${total} 张卡等你翻。`;
}
