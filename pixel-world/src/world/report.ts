/** 《神谕日报》：把世界发生过的事排成三条短讯。
 *
 * 纯函数，从快照推导，不另存一份真相。报纸只写世界真的发生过的事；
 * 没发生就不写，不用"今天很平静"这种填充句糊过去。
 */

import type { WorldEvent, WorldSnapshot } from './types.ts';
import { describePropSource } from './faith.ts';

export interface DailyReport {
  readonly headline: string;
  readonly lines: readonly string[];
  readonly date: string;
}

const LINE_LIMIT = 3;

export function composeReport(snapshot: WorldSnapshot): DailyReport {
  const done = snapshot.tasks.filter((task) => task.status === 'done');
  const failed = snapshot.tasks.filter((task) => task.status === 'failed');
  const { ledger: ledgerDelivered, mock: mockDelivered } = snapshot.deliveries;
  const delivered = ledgerDelivered + mockDelivered;
  const lines: string[] = [];

  if (delivered > 0) {
    // 先按角色分账，再在每个人名下把台账与 Mock 分开；不合并成一个总数。
    const workers =
      snapshot.deliveriesByVillager.length > 0
        ? snapshot.deliveriesByVillager
        : [
            {
              villagerId: snapshot.villager.id,
              name: snapshot.villager.name,
              ledger: ledgerDelivered,
              mock: mockDelivered,
            },
          ];
    const mockLabel = snapshot.ledger.mock ? '离线 Mock' : 'Mock';
    const parts = workers.map((worker) => {
      const total = worker.ledger + worker.mock;
      if (total === 0) return `${worker.name}交付 0 单`;
      const sources: string[] = [];
      if (worker.ledger > 0) sources.push(`台账 ${worker.ledger}`);
      if (worker.mock > 0) sources.push(`${mockLabel} ${worker.mock}`);
      return `${worker.name}交付 ${total} 单（${sources.join(' / ')}）`;
    });
    const latest = done[done.length - 1];
    lines.push(
      latest === undefined
        ? parts.join(' / ')
        : `${parts.join(' / ')}，最近一单：${latest.summary}`,
    );
  } else {
    lines.push('还没有交付。祭坛在等第一条神谕。');
  }

  const newBuildings = snapshot.buildings.filter((building) => building.boundTask === null);
  if (newBuildings.length > 0) {
    // 有来由的楼写清来由：那是"被记住"在建筑上的落点。
    const described = newBuildings.map((building) =>
      building.sourceSummary === null || building.sourceSummary === undefined
        ? building.displayName
        : `${building.displayName}（${building.sourceSummary}）`,
    );
    lines.push(`造物主新起 ${newBuildings.length} 栋楼：${described.join('、')}`);
  }

  if (snapshot.props.length > 0) {
    // 最新那件写清来路，早先的只列名字：一行里挤不下全部来源。
    const latest = snapshot.props[snapshot.props.length - 1];
    const earlier = snapshot.props.slice(0, -1).map((prop) => prop.label);
    const latestLine = latest === undefined ? '' : describePropSource(latest);
    const head = earlier.length > 0 ? `${earlier.join('、')}、${latestLine}` : latestLine;
    lines.push(`${head} 已经落在祭坛边。`);
  }

  if (failed.length > 0) {
    lines.push(`有 ${failed.length} 单没办成，记在案上。`);
  }

  const weather = snapshot.weather === 'rain' ? '今天下了雨' : '今天晴';
  lines.push(`${weather}。${villagerTail(snapshot)}`);

  // 没有里程碑时不能写"第 1 次神恩"：那是还没发生的事。
  const headline =
    snapshot.faith.milestones > 0
      ? `第 ${snapshot.faith.milestones} 次神恩已落地`
      : delivered > 0
        ? '今日有交付'
        : '世界刚醒来';

  return {
    headline,
    lines: lines.slice(0, LINE_LIMIT),
    date: formatDate(snapshot.now),
  };
}

/** 夜里写日记：从当天事件里挑最有信息量的三条，不编造心情。 */
export function composeDiary(events: readonly WorldEvent[]): readonly string[] {
  const picks = events.filter((event) => event.tone !== 'info').slice(-3);
  if (picks.length === 0) return ['今天没什么事。'];
  return picks.map((event, index) =>
    index === 0 ? `今天，${event.text}` : event.text,
  );
}

export function formatDate(nowMs: number): string {
  const totalSeconds = Math.floor(nowMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `第 ${minutes} 分 ${String(seconds).padStart(2, '0')} 秒`;
}

/** 多角色收尾：谁在忙、谁在祭坛，都按名字写清；全闲/全忙各有可读措辞。 */
function villagerTail(snapshot: WorldSnapshot): string {
  const states = snapshot.villagers.map((villager) => ({
    villager,
    task: snapshot.tasks.find((item) => item.id === villager.taskId),
  }));
  const busy = states.filter((item) => item.task !== undefined);
  const idleNames = states
    .filter((item) => item.task === undefined)
    .map((item) => item.villager.name);
  if (busy.length === 0) {
    return snapshot.villagers.length > 1
      ? `${idleNames.join('、')}都在祭坛边坐着。`
      : '在祭坛边坐着。';
  }
  const parts = busy.map(
    ({ villager, task }) => `${villager.name}手上是「${task?.summary ?? ''}」`,
  );
  if (idleNames.length > 0) parts.push(`${idleNames.join('、')}在祭坛边坐着`);
  return `${parts.join('，')}。`;
}

export { LINE_LIMIT };
