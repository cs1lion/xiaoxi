/** 《神谕日报》：把世界发生过的事排成三条短讯。
 *
 * 纯函数，从快照推导，不另存一份真相。报纸只写世界真的发生过的事；
 * 没发生就不写，不用"今天很平静"这种填充句糊过去。
 */

import type { WorldEvent, WorldSnapshot } from './types.ts';

export interface DailyReport {
  readonly headline: string;
  readonly lines: readonly string[];
  readonly date: string;
}

const LINE_LIMIT = 3;

export function composeReport(snapshot: WorldSnapshot): DailyReport {
  const done = snapshot.tasks.filter((task) => task.status === 'done');
  const failed = snapshot.tasks.filter((task) => task.status === 'failed');
  const lines: string[] = [];

  if (done.length > 0) {
    const latest = done[done.length - 1];
    lines.push(`交付 ${done.length} 单，最近一单：${latest.summary}`);
  } else {
    lines.push('还没有交付。祭坛在等第一条神谕。');
  }

  const newBuildings = snapshot.buildings.filter((building) => building.boundTask === null);
  if (newBuildings.length > 0) {
    lines.push(`造物主新起 ${newBuildings.length} 栋楼：${newBuildings.map((b) => b.displayName).join('、')}`);
  }

  if (snapshot.props.length > 0) {
    lines.push(`${snapshot.props.map((prop) => prop.label).join('、')} 已经落在祭坛边。`);
  }

  if (failed.length > 0) {
    lines.push(`有 ${failed.length} 单没办成，记在案上。`);
  }

  const weather = snapshot.weather === 'rain' ? '今天下了雨' : '今天晴';
  lines.push(`${weather}。${snapshot.villager.name}${villagerTail(snapshot)}`);

  // 没有里程碑时不能写"第 1 次神恩"：那是还没发生的事。
  const headline =
    snapshot.faith.milestones > 0
      ? `第 ${snapshot.faith.milestones} 次神恩已落地`
      : done.length > 0
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

function villagerTail(snapshot: WorldSnapshot): string {
  const task = snapshot.tasks.find((item) => item.id === snapshot.villager.taskId);
  if (task === undefined) return '在祭坛边坐着。';
  return `手上是「${task.summary}」。`;
}

export { LINE_LIMIT };
