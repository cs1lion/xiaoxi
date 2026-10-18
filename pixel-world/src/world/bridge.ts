/** 神谕桥接：外部 Agent 与本世界之间的唯一接口。
 *
 * 两个方向：
 *   下行  feed(message)  —— 外部把任务、工具进度、世界法则送进来
 *   上行  drain()        —— 外部取走世界发生的事（交付、开工、卡住）
 *
 * 这里只做形状转换，不判断业务。运输层（WebSocket / postMessage / 终端）
 * 在别处，换运输层不动这个文件。
 */

import type { TaskKind, Weather } from './types.ts';

export type AgentToWorld =
  | {
      readonly type: 'task.assigned';
      readonly taskId: string;
      readonly taskKind: TaskKind;
      readonly summary: string;
    }
  | {
      readonly type: 'task.completed';
      readonly taskId: string;
    }
  | {
      readonly type: 'world.weather';
      readonly weather: Weather;
    };

export type WorldToAgent =
  | { readonly type: 'task.accepted'; readonly taskId: string }
  | { readonly type: 'task.refused'; readonly taskId: string; readonly reason: string }
  | { readonly type: 'task.delivered'; readonly taskId: string; readonly summary: string }
  | { readonly type: 'world.rained'; readonly at: number };

export class Bridge {
  private readonly inbox: AgentToWorld[] = [];
  private readonly outbox: WorldToAgent[] = [];

  feed(message: AgentToWorld): void {
    this.inbox.push(message);
  }

  drain(): readonly WorldToAgent[] {
    const out = [...this.outbox];
    this.outbox.length = 0;
    return out;
  }

  /** 世界侧每拍调用：取出所有待办，返回本拍处理完的结果。 */
  takeInbox(): readonly AgentToWorld[] {
    const items = [...this.inbox];
    this.inbox.length = 0;
    return items;
  }

  emit(message: WorldToAgent): void {
    this.outbox.push(message);
  }
}

/** 把外部消息翻成世界命令。认不出的类型直接丢弃，不猜。 */
export function toWorldCommand(message: AgentToWorld):
  | { kind: 'dispatch'; taskKind: TaskKind; summary: string }
  | { kind: 'complete' }
  | { kind: 'setWeather'; weather: Weather } {
  switch (message.type) {
    case 'task.assigned':
      return { kind: 'dispatch', taskKind: message.taskKind, summary: message.summary };
    case 'task.completed':
      return { kind: 'complete' };
    case 'world.weather':
      return { kind: 'setWeather', weather: message.weather };
  }
}
