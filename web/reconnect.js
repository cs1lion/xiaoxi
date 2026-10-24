/* 断线重连的纯调度逻辑（零 DOM、零 WebSocket，可被 node --test 直驱）。
 *
 * 从 app.js 抽出来只有一个原因：退避节奏是要被测试钉住的行为——
 * 指数增长、封顶、成功后归零。app.js 只负责接线（建 socket、绑四个 handler、
 * 断开时调度、连上时复位），节奏计算不散落在浏览器回调里。
 *
 * 上限取 15s：本地助理的服务重启通常在秒级，15s 探活既不狂刷日志，
 * 也不会让"服务回来了"这件事等太久（演练③的回归脚本按 35s 余量断言）。
 */
export function createReconnectScheduler({ baseMs = 1000, maxMs = 15000 } = {}) {
  let attempt = 0;
  return {
    /** 下一次重连要等多久（毫秒）。调用即消耗一次尝试。 */
    next() {
      const delay = Math.min(maxMs, baseMs * 2 ** attempt);
      attempt = Math.min(attempt + 1, 30);
      return delay;
    },
    /** 连接成功后归零，下一次断线从 baseMs 重新起退避。 */
    reset() { attempt = 0; },
    get attempt() { return attempt; },
  };
}
