/** 上帝调试面板：只读实体列表 + 事件流（S2-3）。
 *
 * 纯函数负责组数据（可离线单测）；DOM 部分只负责开关、暂停/过滤和节流渲染。
 * 面板不改变仿真：所有数据都来自快照 / 只读事件 / 只读存档元信息。
 */

import type { WorldEvent, WorldSnapshot } from '../world/types.ts';
import { summarizeWorld } from '../world/world-state.ts';

export const DEBUG_EVENT_LIMIT = 50;
/** 面板刷新节流 4Hz；10Hz 的逻辑步长不受影响（纯 DOM，不参与仿真）。 */
export const DEBUG_REFRESH_MS = 250;

export type EventTone = WorldEvent['tone'];
export type ToneFilter = EventTone | 'all';

export interface SaveStatusView {
  readonly schemaVersion: number;
  readonly savedAtWallClock: number | null;
  readonly autoSavePaused: boolean;
}

export interface DebugFilters {
  readonly tone: ToneFilter;
  readonly limit: number;
}

export interface DebugVillagerRow {
  readonly id: string;
  readonly name: string;
  readonly mode: string;
  readonly taskId: string;
}

export interface DebugBuildingRow {
  readonly instanceId: string;
  readonly displayName: string;
  readonly activity: string;
  readonly currentTask: string;
  readonly constructionRemainingMs: number | null;
}

export interface DebugTaskRow {
  readonly id: string;
  readonly kind: string;
  readonly summary: string;
  readonly source: string;
  readonly status: string;
  readonly workerId: string;
  readonly buildingId: string;
  readonly blockedQuestion: string;
}

export interface DebugPendingRow {
  readonly villagerId: string;
  readonly villagerName: string;
  readonly items: readonly { readonly id: string; readonly kind: string; readonly summary: string }[];
}

export interface DebugModel {
  readonly villagers: readonly DebugVillagerRow[];
  readonly buildings: readonly DebugBuildingRow[];
  readonly tasks: readonly DebugTaskRow[];
  readonly pending: readonly DebugPendingRow[];
  readonly ledger: WorldSnapshot['ledger'];
  readonly save: SaveStatusView | null;
  readonly events: readonly WorldEvent[];
  readonly counts: {
    readonly villagers: number;
    readonly buildings: number;
    readonly tasks: number;
    readonly pending: number;
    readonly events: number;
  };
}

export function selectEvents(
  events: readonly WorldEvent[],
  filters: DebugFilters,
): readonly WorldEvent[] {
  const filtered =
    filters.tone === 'all' ? [...events] : events.filter((event) => event.tone === filters.tone);
  const limit = Math.max(0, Math.floor(filters.limit));
  return filtered.slice(-limit);
}

export function buildDebugModel(
  snapshot: WorldSnapshot,
  events: readonly WorldEvent[],
  save: SaveStatusView | null,
  filters: DebugFilters,
): DebugModel {
  const buildings: DebugBuildingRow[] = snapshot.workstations.map((workstation) => {
    const building = snapshot.buildings.find(
      (item) => item.instanceId === workstation.buildingId,
    );
    const activity = workstation.activity;
    const currentTask = 'taskId' in activity ? activity.taskId : '';
    const construction = snapshot.constructions.find(
      (item) => item.buildingId === workstation.buildingId,
    );
    return {
      instanceId: workstation.buildingId,
      displayName: building?.displayName ?? workstation.buildingId,
      activity: activity.kind,
      currentTask,
      constructionRemainingMs: construction?.remainingMs ?? null,
    };
  });
  const pending: DebugPendingRow[] = snapshot.villagers.map((villager) => ({
    villagerId: villager.id,
    villagerName: villager.name,
    items: snapshot.pending
      .filter((item) => item.villagerId === villager.id)
      .map((item) => ({ id: item.id, kind: item.kind, summary: item.summary })),
  }));
  const selectedEvents = selectEvents(events, filters);
  return {
    villagers: snapshot.villagers.map((villager) => ({
      id: villager.id,
      name: villager.name,
      mode: villager.mode,
      taskId: villager.taskId ?? '—',
    })),
    buildings,
    tasks: snapshot.tasks.map((task) => ({
      id: task.id,
      kind: task.kind,
      summary: task.summary,
      source: task.source,
      status: task.status,
      workerId: task.workerId ?? '—',
      buildingId: task.buildingId ?? '—',
      blockedQuestion: task.blockedQuestion ?? '—',
    })),
    pending,
    ledger: snapshot.ledger,
    save,
    events: selectedEvents,
    counts: {
      villagers: snapshot.villagers.length,
      buildings: snapshot.workstations.length,
      tasks: snapshot.tasks.length,
      pending: snapshot.pending.length,
      events: selectedEvents.length,
    },
  };
}

/** 存档状态一行字（调试用，不参与逻辑）。 */
export function saveStatusLine(save: SaveStatusView | null): string {
  if (save === null) return '存档：未写入';
  const savedAt =
    save.savedAtWallClock === null
      ? '未记录'
      : new Date(save.savedAtWallClock).toISOString().slice(11, 19);
  return `存档：v${save.schemaVersion} · 保存于 ${savedAt} UTC · 自动存档${save.autoSavePaused ? '已暂停' : '开启'}`;
}

const LEDGER_FIELDS: readonly (keyof WorldSnapshot['ledger'])[] = [
  'connected',
  'complete',
  'reconciled',
  'mock',
];

export function ledgerSummary(ledger: WorldSnapshot['ledger']): string {
  const parts = LEDGER_FIELDS.map((key) => `${key}=${String(ledger[key])}`);
  return `台账：${parts.join(' · ')}`;
}

export interface DebugPanelSources {
  readonly getSnapshot: () => WorldSnapshot | undefined;
  readonly getEvents: (limit: number) => readonly WorldEvent[];
  readonly getSaveStatus: () => SaveStatusView | null;
  readonly feed?: (message: {
    readonly type: 'task.assigned';
    readonly taskId: string;
    readonly taskKind: 'code' | 'research' | 'writing' | 'quick';
    readonly summary: string;
  }) => { readonly ok: boolean; readonly message: string };
  readonly pushLedger?: (commission: unknown) => void;
}

export interface DebugPanelHandle {
  readonly dispose: () => void;
  readonly isOpen: () => boolean;
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className !== undefined) node.className = className;
  return node;
}

function row(label: string, value: string, kind?: string): HTMLElement {
  const node = element('div', 'dbg-row');
  if (kind !== undefined) node.dataset.debugRow = kind;
  const key = element('span', 'dbg-key');
  key.textContent = label;
  const val = element('span', 'dbg-val');
  val.textContent = value;
  node.append(key, val);
  return node;
}

function section(title: string, count: number): HTMLElement {
  const wrapper = element('section', 'dbg-section');
  const heading = element('h3');
  heading.textContent = `${title}（${count}）`;
  wrapper.append(heading);
  return wrapper;
}

/** 绑定面板；返回句柄可用于测试/卸载。不影响仿真。 */
export function bindDebugPanel(sources: DebugPanelSources): DebugPanelHandle {
  const panel = document.querySelector<HTMLElement>('#debug-panel');
  const toggle = document.querySelector<HTMLButtonElement>('#debug-toggle');
  const close = document.querySelector<HTMLButtonElement>('#debug-close');
  const pause = document.querySelector<HTMLInputElement>('#debug-pause');
  const tone = document.querySelector<HTMLSelectElement>('#debug-tone');
  const content = document.querySelector<HTMLDivElement>('#debug-content');
  const stats = document.querySelector<HTMLDivElement>('#debug-stats');
  const injectSlot = document.querySelector<HTMLDivElement>('#debug-inject-slot');
  if (!panel || !toggle || !content || !tone || !pause) {
    return { dispose: () => {}, isOpen: () => false };
  }

  let open = false;
  let paused = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  // 注入区随面板创建一次，放在 #debug-content 之外：repaint 只清 content，
  // details 的 open 状态与按钮事件绑定都不会丢（task-19 P2 修复）。
  if (injectSlot !== null && (sources.feed !== undefined || sources.pushLedger !== undefined)) {
    const details = element('details', 'dbg-inject');
    details.id = 'debug-inject';
    const summary = element('summary');
    summary.textContent = '调试注入（仅调试、不写台账；默认收起）';
    details.append(summary);
    if (sources.feed !== undefined) {
      const feedButton = element('button');
      feedButton.type = 'button';
      feedButton.dataset.debugAction = 'feed';
      feedButton.textContent = '注入一条 quick 外部单';
      feedButton.addEventListener('click', () => {
        sources.feed?.({
          type: 'task.assigned',
          taskId: `dbg-${Date.now()}`,
          taskKind: 'quick',
          summary: '调试注入的小事（不写台账）',
        });
      });
      details.append(feedButton);
    }
    if (sources.pushLedger !== undefined) {
      const ledgerButton = element('button');
      ledgerButton.type = 'button';
      ledgerButton.dataset.debugAction = 'pushLedger';
      ledgerButton.textContent = '注入一帧台账推送';
      ledgerButton.addEventListener('click', () => {
        sources.pushLedger?.({
          id: `dbg_c_${Date.now()}`,
          title: '调试注入的督办（不写台账）',
          agent_id: 'scribe',
          status: 'running',
          step_idx: 0,
          blocked_question: '',
        });
      });
      details.append(ledgerButton);
    }
    injectSlot.append(details);
  }

  const filters = (): DebugFilters => ({
    tone: (tone.value as ToneFilter) ?? 'all',
    limit: DEBUG_EVENT_LIMIT,
  });

  const paint = (): void => {
    const snapshot = sources.getSnapshot();
    if (snapshot === undefined) return;
    const model = buildDebugModel(
      snapshot,
      sources.getEvents(DEBUG_EVENT_LIMIT),
      sources.getSaveStatus(),
      filters(),
    );
    content.replaceChildren();

    const villagers = section('角色', model.villagers.length);
    for (const villager of model.villagers) {
      villagers.append(
        row(
          `${villager.id} ${villager.name}`,
          `${villager.mode} · task=${villager.taskId}`,
          'villager',
        ),
      );
    }
    content.append(villagers);

    const buildings = section('铺子', model.buildings.length);
    for (const building of model.buildings) {
      const extra =
        building.constructionRemainingMs === null
          ? ''
          : ` · 施工剩 ${Math.round(building.constructionRemainingMs)}ms`;
      buildings.append(
        row(
          building.displayName,
          `${building.activity}${building.currentTask === '' ? '' : ` · ${building.currentTask}`}${extra}`,
          'building',
        ),
      );
    }
    content.append(buildings);

    // 世界状态：归纳结果，不是原始数据。造物主做选择时读的就是这份。
    const state = summarizeWorld(snapshot);
    const summary = section('世界状态', state.buildings.total);
    summary.append(
      row('镇内 / 郊野', `${state.buildings.town} / ${state.buildings.wild}`, 'state'),
    );
    for (const entry of state.taskKinds) {
      summary.append(
        row(
          `活：${entry.kind}`,
          `镇内 ${entry.town} · 郊野 ${entry.wild}`,
          'state',
        ),
      );
    }
    summary.append(
      row('郊野空地', `约 ${state.space.wildFree} 栋`, 'state'),
    );
    summary.append(
      row('角色', `忙 ${state.activity.busy} · 闲 ${state.activity.idle}`, 'state'),
    );
    if (state.gaps.missingWild.length > 0) {
      summary.append(
        row('缺口', `缺郊野铺子：${state.gaps.missingWild.join('、')}`, 'state'),
      );
    }
    content.append(summary);

    const tasks = section('任务', model.tasks.length);
    for (const task of model.tasks) {
      tasks.append(
        row(
          `${task.id} ${task.status}`,
          `${task.source} · worker=${task.workerId} · ${task.kind}：${task.summary}${
            task.blockedQuestion === '—' ? '' : ` · 请示：${task.blockedQuestion}`
          }`,
          'task',
        ),
      );
    }
    content.append(tasks);

    const pending = section('等待队列', model.counts.pending);
    for (const queue of model.pending) {
      pending.append(
        row(`${queue.villagerId} ${queue.villagerName}`, `${queue.items.length} 条`, 'pending'),
      );
      for (const item of queue.items) {
        pending.append(row(`  ${item.id}`, `${item.kind}：${item.summary}`, 'pending-item'));
      }
    }
    content.append(pending);

    const status = element('section', 'dbg-section');
    const heading = element('h3');
    heading.textContent = '连接与存档';
    status.append(heading, row('连接', ledgerSummary(model.ledger), 'ledger'));
    status.append(row('存档', saveStatusLine(model.save), 'save'));
    content.append(status);

    const events = section('事件流', model.events.length);
    for (const event of model.events) {
      const node = row(`#${Math.round(event.at)}`, `${event.tone}：${event.text}`, 'event');
      node.dataset.tone = event.tone;
      events.append(node);
    }
    content.append(events);

    if (stats !== null) {
      stats.textContent = `角色 ${model.counts.villagers} · 铺子 ${model.counts.buildings} · 任务 ${model.counts.tasks} · 队列 ${model.counts.pending} · 事件 ${model.counts.events}${paused ? ' · 已暂停' : ''}`;
    }
  };

  const setOpen = (next: boolean): void => {
    open = next;
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
    if (open) {
      paint();
      timer = setInterval(() => {
        if (!paused) paint();
      }, DEBUG_REFRESH_MS);
    }
  };

  const onToggle = (): void => setOpen(!open);
  const onClose = (): void => setOpen(false);
  const onPause = (): void => {
    paused = pause.checked;
    if (!paused) {
      paint();
      return;
    }
    // 暂停时不重绘列表，只在状态行补标记；世界继续跑，面板冻结。
    if (stats !== null) {
      const base = (stats.textContent ?? '').replace(' · 已暂停', '');
      stats.textContent = `${base} · 已暂停`;
    }
  };
  const onTone = (): void => paint();
  const onKeydown = (event: KeyboardEvent): void => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target as HTMLElement | null;
    const tag = target?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (event.key === 'd' || event.key === 'D') onToggle();
  };

  toggle.addEventListener('click', onToggle);
  close?.addEventListener('click', onClose);
  pause.addEventListener('change', onPause);
  tone.addEventListener('change', onTone);
  window.addEventListener('keydown', onKeydown);

  return {
    dispose: () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
      toggle.removeEventListener('click', onToggle);
      close?.removeEventListener('click', onClose);
      pause.removeEventListener('change', onPause);
      tone.removeEventListener('change', onTone);
      window.removeEventListener('keydown', onKeydown);
    },
    isOpen: () => open,
  };
}
