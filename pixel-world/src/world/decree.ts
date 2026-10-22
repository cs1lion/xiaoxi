/** 急件请旨：台账 blocked 单在世界里的具身化。
 *
 * 真实的个人助理在删文件、跑未知脚本这类操作前要人确认。台账已经把这单标成
 * `blocked` 并带一个问题，世界现在只是把它写成状态行的一行字——用户很容易
 * 完全没注意到。
 *
 * 这里做的事很有限，是故意的：
 *   - 把「哪一单、问什么」整理成一张可以呈给用户看的急件；
 *   - **不替用户回答，也不自己写台账**。世界是纯投影，改状态是主产品的事。
 *
 * 世界能提供的只是一次**呈现**：让传令官举着它跑到祭坛，让用户点开看全文。
 * 回答的动作走外部通道（主产品的 dispatch 接口），不是世界。
 */

import type { WorldSnapshot } from './types.ts';

export interface Decree {
  readonly commissionId: string;
  /** 完整问题原文。**不截断**：用户要看着它做决定。 */
  readonly question: string;
  /** 这单在做什么，让用户知道上下文。 */
  readonly title: string;
}

/**
 * 当前该呈给用户的那张急件。
 *
 * 一次只呈一张：同时摆三张羊皮卷会把祭坛变成收件箱。
 * 取快照里第一张带问题的单——顺序即台账给的顺序，稳定可复现。
 */
export function awaitingDecree(snapshot: WorldSnapshot): Decree | null {
  for (const task of snapshot.tasks) {
    if (task.blockedQuestion === null) continue;
    const question = task.blockedQuestion.trim();
    if (question === '') continue;
    return {
      commissionId: task.id,
      question,
      title: task.summary,
    };
  }
  return null;
}
