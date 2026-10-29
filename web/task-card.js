// A real task object, backed by the existing versioned task and linked-reminder APIs.
const STATUS = { open:'未开始', in_progress:'进行中', waiting:'等待中', done:'已完成', cancelled:'已取消' };
const LINKED_REMINDER_STATUSES = ['scheduled', 'queued', 'notified'];

/** 状态标签：未知 status 原样降级显示，不猜、不吞。 */
export function taskStatusLabel(status) {
  return STATUS[status] || status;
}

/** 这条任务的关联提醒：同 task_id 且仍在生命周期内。
 *  done/cancelled 时服务端会取消尚未投递的关联提醒，所以这里只认这三个状态。 */
export function linkedTaskReminders(reminders, taskId) {
  return (reminders || []).filter(
    reminder => reminder.task_id === taskId && LINKED_REMINDER_STATUSES.includes(reminder.status));
}

/** 保存失败的用户可读文案。服务端 store.update_task 版本不匹配抛的是
 *  "task version conflict"（经 call()→HTTP 400→api() 的 Error.message），
 *  所以这个英文子串是活契约，不是随手写的兜底。 */
export function taskSaveError(message) {
  const text = typeof message === 'string' ? message : String(message);
  return text.includes('version conflict')
    ? '任务已在其他窗口更新。请刷新后重试，当前输入仍保留。'
    : `未保存：${text}`;
}
const element = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
};
export function renderTaskCard(task, { api, refresh, reminders = [] }) {
  const card = element('article', undefined, 'assistant-card task-progress-card');
  card.dataset.taskId = task.id;
  const heading = element('div', undefined, 'task-progress-heading');
  heading.append(element('strong', task.title), element('span', taskStatusLabel(task.status), 'task-status'));
  const message = element('p', '', 'task-feedback'); message.setAttribute('role','status');
  const controls = element('div', undefined, 'assistant-row');
  let pending = false;
  const action = (label, callback) => {
    const button = element('button', label); button.type = 'button';
    button.onclick = async () => {
      if (pending) return;
      pending = true;
      card.setAttribute('aria-busy','true');
      const buttons = [...card.querySelectorAll('button')]; buttons.forEach(el => el.disabled = true);
      try {
        await callback();
        message.textContent = '已保存。';
        try { await refresh(); }
        catch { message.textContent = '已保存，但列表刷新失败，请稍后刷新。'; }
      } catch (error) {
        message.textContent = taskSaveError(error.message);
      } finally {
        pending = false; card.removeAttribute('aria-busy'); buttons.forEach(el => el.disabled = false);
      }
    };
    return button;
  };
  const update = changes => api(`tasks/${encodeURIComponent(task.id)}`, 'PATCH', { ...changes, version: task.version });
  card.append(heading);
  if (task.description) card.append(element('p', task.description, 'task-description'));
  const nextLabel = element('label', undefined, 'assistant-field');
  const next = element('input'); next.value = task.next_action || ''; next.maxLength = 1000;
  next.setAttribute('aria-label', `下一步：${task.title}`); next.placeholder = '把任务变成一个具体的小动作…';
  nextLabel.append(element('span', '下一步行动'), next);
  const edit = element('details', undefined, 'task-editor');
  edit.append(element('summary', task.next_action ? `下一步：${task.next_action}` : '写一个下一步，让任务更容易开始'));
  edit.append(nextLabel, action('保存下一步', () => update({ next_action: next.value.trim() })));
  card.append(edit);
  if (!['done','cancelled'].includes(task.status)) {
    if (task.status !== 'in_progress') controls.append(action('开始做', () => update({ status:'in_progress' })));
    if (task.status !== 'waiting') controls.append(action('先等待', () => update({ status:'waiting' })));
    controls.append(action('完成', () => update({ status:'done' })));
    const reminderDetails = element('details', undefined, 'task-editor');
    reminderDetails.append(element('summary', '给这件事设提醒'));
    const date = element('input'); date.type = 'datetime-local'; date.setAttribute('aria-label', `提醒时间：${task.title}`);
    const dateLabel = element('label', undefined, 'assistant-field'); dateLabel.append(element('span','提醒时间（当前浏览器时区）'), date);
    let requestId = null, lastSignature = null;
    reminderDetails.append(dateLabel, action('保存关联提醒', async () => {
      const scheduled_at = new Date(date.value).getTime()/1000;
      if (!Number.isFinite(scheduled_at) || scheduled_at <= Date.now()/1000) throw new Error('请选择未来的具体时间。');
      // Reuse the same key on uncertain failures; new parameters get a new request key.
      const signature = JSON.stringify([task.id, scheduled_at, task.title]);
      if (signature !== lastSignature) { requestId = crypto.randomUUID(); lastSignature = signature; }
      await api('reminders','POST',{ content:task.next_action || task.title, task_id:task.id, scheduled_at, request_id:requestId });
    }));
    card.append(reminderDetails);
  } else controls.append(action('重新打开', () => update({ status:'open' })));
  const linked = linkedTaskReminders(reminders, task.id);
  if (linked.length) {
    const list = element('ul',undefined,'task-reminder-list');
    for (const reminder of linked) list.append(element('li', `${reminder.status === 'notified' ? '已投递' : '提醒'} · ${new Date(reminder.scheduled_at*1000).toLocaleString()} · ${reminder.content}`));
    card.append(list);
  }
  if (!['done','cancelled'].includes(task.status)) card.append(element('small','完成任务会取消其尚未投递的关联提醒。','assistant-hint'));
  card.append(controls, message);
  return card;
}
