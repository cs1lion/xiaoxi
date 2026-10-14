/* Task/reminder inbox and parameter-bound action confirmation. No innerHTML. */
import { renderTaskCard } from './task-card.js';
import { showCompanionResult } from './companion.js';
import { showNoteProposalResult } from './note-proposals.js';
import { appendContextUsage } from './context-usage.js';
const api = async (path, method = 'GET', body) => {
  const response = await fetch(`/api/${path}`, {
    method, headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  let value;
  try { value = await response.json(); }
  catch { throw new Error('服务返回了无法识别的内容，请稍后重试。'); }
  if (!response.ok) {
    if (response.status === 404) throw new Error('任务服务暂不可用，请检查服务版本后重试。聊天仍可继续。');
    throw new Error(typeof value.detail === 'string' ? value.detail : `请求失败（${response.status}），请稍后重试。`);
  }
  return value;
};
const node = (tag, text, className) => {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  if (className) value.className = className;
  return value;
};
let panel, tasks, reminders, inbox, actionArea, errorArea;
const seen = new Set();
const busy = new Set();
function button(label, callback) {
  const value = node('button', label);
  value.type = 'button';
  value.onclick = async () => {
    value.disabled = true;
    try { await callback(); errorArea.textContent = ''; }
    catch (error) { errorArea.textContent = error.message; }
    finally { value.disabled = false; }
  };
  return value;
}
function labeledInput(title, type = 'text') {
  const label = node('label', undefined, 'assistant-field');
  const input = node('input');
  input.type = type;
  input.setAttribute('aria-label', title);
  label.append(node('span', title), input);
  return { label, input };
}
function section(title) {
  const value = node('section', undefined, 'assistant-section');
  value.setAttribute('aria-label', title);
  value.append(node('h4', title));
  panel.append(value);
  return value;
}
function empty(area, message) {
  if (!area.children.length) area.append(node('p', message, 'assistant-empty'));
}
export function showAssistantResult(result, { confirm } = {}) {
  if (showNoteProposalResult(result)) return;
  if (showCompanionResult(result)) return;
  if (!actionArea) return;
  panel.open = true;
  actionArea.querySelector('.assistant-empty')?.remove();
  const card = node('div', undefined, 'assistant-card');
  card.append(node('p', result.text));
  for (const receipt of result.receipts || []) {
    card.append(node('pre', JSON.stringify(receipt, null, 2)));
  }
  if (result.confirmation) {
    const confirmation = result.confirmation;
    card.append(node('pre', `${confirmation.tool}\n${JSON.stringify(confirmation.arguments, null, 2)}`));
    const controls = node('div', undefined, 'assistant-row');
    let submitting = false;
    for (const approve of [true, false]) {
      controls.append(button(approve ? '确认执行' : '不执行', async () => {
        if (submitting) return;
        submitting = true;
        for (const control of controls.children) control.disabled = true;
        try {
          const next = confirm ? await confirm(result, approve)
            : await api(`assistant/runs/${result.run_id}/confirm`, 'POST', {
              confirmation_id: confirmation.id, approve,
            });
          card.remove(); showAssistantResult(next, { confirm }); await refresh();
        } finally {
          submitting = false;
          for (const control of controls.children) control.disabled = false;
        }
      }));
    }
    card.append(controls);
  }
  appendContextUsage(card, result.context_usage);
  actionArea.prepend(card);
  // Every result is announced, even if details was already open but the mobile sidebar closed.
  panel.dispatchEvent(new CustomEvent('assistant-result', { bubbles: true, detail: { card } }));
}
async function refresh() {
  if (busy.has('refresh')) return;
  busy.add('refresh');
  try {
    const [taskData, reminderData, notificationData] = await Promise.all([
      api('tasks'), api('reminders'), api('notifications'),
    ]);
    tasks.replaceChildren(); reminders.replaceChildren(); inbox.replaceChildren();
    for (const task of taskData.items) {
      tasks.append(renderTaskCard(task, { api, refresh, reminders: reminderData.items }));
    }
    empty(tasks, '还没有任务，记下一件想做的事吧。');
    for (const reminder of reminderData.items) {
      const row = node('div', undefined, 'assistant-card');
      row.append(node('p', `${reminder.content} · ${new Date(reminder.scheduled_at * 1000).toLocaleString()} · ${reminder.status}`));
      if (['scheduled', 'queued'].includes(reminder.status)) {
        const controls = node('div', undefined, 'assistant-row');
        controls.append(button('取消', async () => { await api(`reminders/${reminder.id}/cancel`, 'POST', {}); await refresh(); }));
        controls.append(button('延后10分钟', async () => { await api(`reminders/${reminder.id}/snooze`, 'POST', { scheduled_at: Date.now() / 1000 + 600 }); await refresh(); }));
        row.append(controls);
      }
      reminders.append(row);
    }
    empty(reminders, '暂无提醒，重要的事可以在这里安排。');
    for (const notification of notificationData.items) {
      if (['dismissed', 'expired'].includes(notification.status)) continue;
      const row = node('div', undefined, 'assistant-card');
      row.append(node('p', `${notification.missed ? '错过预定时间：' : ''}${notification.content}`));
      const controls = node('div', undefined, 'assistant-row');
      controls.append(button('关闭', async () => { await api(`notifications/${notification.id}/dismiss`, 'POST', {}); await refresh(); }));
      row.append(controls);
      inbox.append(row);
      if (!seen.has(notification.id)) {
        await api(`notifications/${notification.id}/ack`, 'POST', {});
        seen.add(notification.id);
      }
    }
    empty(inbox, '暂无新通知。');
  } finally { busy.delete('refresh'); }
}
export async function bootAssistant() {
  const preferenceMount = document.querySelector('#assistantMount') || document.querySelector('.side');
  if (preferenceMount) {
    import('./interaction-preferences.js').then(module => module.bootPreferences(preferenceMount))
      .catch(error => console.warn('相处偏好面板启动失败', error));
  }
  panel = node('details', undefined, 'panel assistant-panel');
  panel.append(node('summary', '任务与提醒'));
  errorArea = node('p', undefined, 'assistant-error');
  errorArea.setAttribute('role', 'alert');
  panel.append(errorArea);
  const mount = document.querySelector('#assistantMount') || document.querySelector('.side');
  if (!mount) throw new Error('找不到任务与提醒侧栏');
  mount.append(panel);
  let prefs;
  try { prefs = await api('preferences/proactivity'); }
  catch (error) {
    panel.open = true;
    errorArea.textContent = error.message;
    const retry = node('button', '重新连接任务服务');
    retry.type = 'button';
    retry.onclick = async () => {
      retry.disabled = true;
      panel.remove();
      await bootAssistant();
    };
    panel.append(retry);
    return;
  }
  const settings = section('提醒偏好');
  settings.append(node('p', '服务在线时为你调度提醒。', 'assistant-hint'));
  const { label: timezoneLabel, input: timezone } = labeledInput('提醒时区');
  timezone.placeholder = '如 Asia/Shanghai'; timezone.value = prefs.timezone;
  const timezoneRow = node('div', undefined, 'assistant-row');
  timezoneRow.append(timezoneLabel, button('保存时区', async () => api('preferences/proactivity', 'PATCH', { timezone: timezone.value })));
  settings.append(timezoneRow);
  for (const [key, title] of [['reminder_enabled', '允许任务提醒']]) {
    const label = node('label', title, 'assistant-row');
    const input = node('input'); input.type = 'checkbox'; input.checked = prefs[key];
    input.setAttribute('aria-label', title);
    input.onchange = async () => {
      input.disabled = true;
      try { await api('preferences/proactivity', 'PATCH', { [key]: input.checked }); errorArea.textContent = ''; }
      catch (error) { errorArea.textContent = error.message; input.checked = !input.checked; }
      finally { input.disabled = false; }
    };
    label.prepend(input); settings.append(label);
  }
  const quietRow = node('div', undefined, 'assistant-row');
  quietRow.append(button('夜间免打扰 22:00–08:00', async () => api('preferences/proactivity', 'PATCH', { quiet_hours: { start: '22:00', end: '08:00' } })),
                  button('清除免打扰', async () => api('preferences/proactivity', 'PATCH', { quiet_hours: null })));
  settings.append(quietRow);
  const newTask = section('记下任务');
  const { label: titleLabel, input: title } = labeledInput('任务名称');
  title.placeholder = '想完成什么？';
  const taskRow = node('div', undefined, 'assistant-row');
  taskRow.append(titleLabel, button('新增任务', async () => { await api('tasks', 'POST', { title: title.value, request_id: crypto.randomUUID() }); title.value = ''; await refresh(); }));
  newTask.append(taskRow);
  const newReminder = section('安排提醒');
  const { label: contentLabel, input: content } = labeledInput('提醒内容');
  content.placeholder = '想在什么时候提醒你？';
  const { label: dateLabel, input: date } = labeledInput('提醒时间', 'datetime-local');
  const timeHint = node('small', '手动时间按当前浏览器时区；自然语言时间按上方设置。', 'assistant-hint');
  timeHint.id = 'assistant-time-hint';
  date.setAttribute('aria-describedby', timeHint.id);
  const reminderRow = node('div', undefined, 'assistant-row');
  reminderRow.append(contentLabel, dateLabel);
  newReminder.append(reminderRow, timeHint, button('设置提醒', async () => {
    const scheduled_at = new Date(date.value).getTime() / 1000;
    if (!Number.isFinite(scheduled_at)) throw new Error('请选择具体时间');
    await api('reminders', 'POST', { content: content.value, scheduled_at, request_id: crypto.randomUUID() }); await refresh();
  }));
  for (const [heading, assign] of [['行动确认与结果', value => actionArea = value], ['任务', value => tasks = value], ['提醒', value => reminders = value], ['通知收件箱', value => inbox = value]]) {
    const group = section(heading);
    const area = node('div', undefined, 'assistant-list'); assign(area); group.append(area);
  }
  empty(actionArea, '需要你确认的行动与执行结果会显示在这里。');
  try { await refresh(); }
  catch (error) { errorArea.textContent = error.message; }
  setInterval(() => {
    // Replacing list nodes during keyboard navigation would silently discard focus.
    if (document.hidden || tasks.querySelector('.task-editor[open]') || [tasks, reminders, inbox].some(area => area.contains(document.activeElement))) return;
    refresh().catch(error => { errorArea.textContent = error.message; });
  }, 5000);
}
