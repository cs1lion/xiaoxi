/** 界面壳：把世界快照翻译成顶部负载条与底部队列条。
 *
 * 只读快照，不碰仿真。`#status` 那一行状态归 oracle-panel 管，这里不重复写。
 * 中文一律走 DOM（D5-A）：这个文件在 `src/ui/`，不编码任何 canvas 文本。
 */

import type { WorldSnapshot } from '../world/types.ts';
import { TASK_PROFILES } from '../world/tasks.ts';

export interface HudApi {
  readonly view: () => WorldSnapshot;
  readonly onStatus: (listener: (line: string) => void) => () => void;
}

/** 一单活的一句话。角色 id 换成名字，读的人不认识 `v0`。 */
function taskLine(task: WorldSnapshot['tasks'][number], names: ReadonlyMap<string, string>): string {
  const label = TASK_PROFILES[task.kind]?.label ?? task.kind;
  const who = task.workerId === null ? '尚未认领' : (names.get(task.workerId) ?? task.workerId);
  const state =
    task.status === 'in_progress'
      ? '在做'
      : task.status === 'assigned'
        ? '待开工'
        : task.status === 'done'
          ? '已交付'
          : '失败';
  return `${who} · ${label} · ${state} · ${task.summary}`;
}

export function bindHud(getApi: () => HudApi | undefined): void {
  const load = document.querySelector<HTMLElement>('#hud-load');
  const net = document.querySelector<HTMLElement>('#hud-net');
  const queue = document.querySelector<HTMLElement>('#queue-list');
  const queueBar = document.querySelector<HTMLElement>('#queue');
  if (load === null || queue === null) return;

  const paint = (snapshot: WorldSnapshot): void => {
    const names = new Map(snapshot.villagers.map((item) => [item.id, item.name]));
    const busy = snapshot.villagers.filter((item) => item.mode !== 'idle').length;
    const open = snapshot.tasks.filter((task) => task.status !== 'done').length;
    load.textContent =
      busy === 0 ? `天下太平 · ${snapshot.villagers.length} 人在镇` : `${busy} 人在忙 · ${open} 单在手`;

    // 台账离线是「你不知道发生了什么」，不是「没事发生」。必须显式写出来。
    if (net !== null) {
      const connected = snapshot.ledger.connected;
      net.textContent = connected ? '台账在线' : '台账离线';
      net.classList.toggle('bad', !connected);
      net.title = connected
        ? '督办台账已连接'
        : '督办台账离线：世界照常过日子，但手上的单可能不全';
    }

    const lines = snapshot.tasks
      .filter((task) => task.status !== 'done')
      .map((task) => {
        const item = document.createElement('li');
        item.textContent = taskLine(task, names);
        return item;
      });
    // 排队等派的神谕也进队列：它们确实"在等"，只是还没派出去。
    for (const pending of snapshot.pending) {
      const item = document.createElement('li');
      item.textContent = `排队中 · ${TASK_PROFILES[pending.kind]?.label ?? pending.kind} · ${pending.summary}`;
      lines.push(item);
    }
    queue.replaceChildren(...lines);
    if (queueBar !== null) queueBar.hidden = lines.length === 0;
  };

  const subscribe = (): void => {
    const api = getApi();
    if (api === undefined) {
      requestAnimationFrame(subscribe);
      return;
    }
    api.onStatus(() => paint(api.view()));
    paint(api.view());
  };
  subscribe();
}
