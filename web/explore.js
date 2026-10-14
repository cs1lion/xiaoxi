// Opt-in design sandbox. No backend writes, audio access, or global persistent preferences.
export const effects = [
  { id: 'atmosphere', title: '环境光', note: '大范围色光；观察长时间阅读是否抢眼。' },
  { id: 'texture', title: '纸感纹理', note: '给空间增加材质；观察文字区域是否干净。' },
  { id: 'glass', title: '玻璃层次', note: '半透明面板；与纸感可叠加，用于判断冲突。' },
  { id: 'halo', title: '角色光环', note: '角色背后增加空间标记，不改变角色动作。' },
  { id: 'spring', title: '弹性反馈', note: '按钮触碰轻弹；不延迟发送，尊重减少动态。' },
];
export const presets = {
  baseline: { title: '已确认基线', tone: 'baseline', effects: [] },
  desk: { title: '安静书桌', tone: 'desk', effects: ['texture', 'atmosphere'] },
  companion: { title: '灵动伙伴', tone: 'companion', effects: ['atmosphere', 'halo', 'spring'] },
  future: { title: '未来空间', tone: 'future', effects: ['atmosphere', 'glass', 'halo', 'spring'] },
};
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
};
export function mountExploration() {
  const root = document.documentElement;
  const controller = new AbortController();
  const on = (el, type, fn) => el.addEventListener(type, fn, { signal: controller.signal });
  const sheet = node('link'); sheet.rel = 'stylesheet'; sheet.href = '/static/explore.css';
  document.head.append(sheet);
  const dialog = node('dialog', undefined, 'explore-dialog');
  dialog.setAttribute('aria-labelledby', 'exploreTitle');
  const heading = node('h2', '设计探索 · 先堆，再选'); heading.id = 'exploreTitle';
  const header = node('div', undefined, 'explore-header');
  const close = node('button', '收起'); close.type = 'button';
  header.append(heading, close);
  const intro = node('p', '这是实验层，不是最终方案。切换不重载、不丢草稿，不修改聊天和语音权限。', 'explore-description');
  const selectLabel = node('label', '组合预设');
  const select = node('select'); select.id = 'explorePreset';
  for (const [id, preset] of Object.entries(presets)) { const option = node('option', preset.title); option.value = id; select.append(option); }
  selectLabel.append(select);
  const fieldset = node('fieldset'); fieldset.append(node('legend', '独立材料 · 可以刻意混搭'));
  const inputs = new Map();
  for (const effect of effects) {
    const label = node('label', undefined, 'explore-effect');
    const input = node('input'); input.type = 'checkbox'; input.dataset.effect = effect.id;
    input.setAttribute('aria-label', effect.title);
    const words = node('span'); words.append(node('strong', effect.title), node('small', effect.note));
    label.append(input, words); fieldset.append(label); inputs.set(effect.id, input);
  }
  const compare = node('button', '按住看基线（也可按空格）'); compare.type = 'button'; compare.id = 'exploreCompare';
  const reset = node('button', '全部撤下，回到基线'); reset.type = 'button'; reset.id = 'exploreReset';
  const actions = node('div', undefined, 'explore-actions'); actions.append(compare, reset);
  const noteLabel = node('label', '体验判断（切换预设后仍保留）');
  const notes = node('textarea'); notes.id = 'exploreNotes'; notes.rows = 3;
  notes.placeholder = '例：玻璃首屏好看，但聊天十分钟后分散注意力；希望保留角色光环。';
  noteLabel.append(notes);
  const save = node('button', '记录当前判断'); save.type = 'button'; save.id = 'exploreSave';
  const download = node('button', '导出实验记录'); download.type = 'button'; download.id = 'exploreExport';
  const count = node('p', '尚未记录；内容只保存在本页内存中。', 'explore-description');
  const status = node('p', '', 'explore-status'); status.role = 'status'; status.setAttribute('aria-live', 'polite');
  const exit = node('button', '卸载实验层（不刷新）'); exit.type = 'button'; exit.id = 'exploreExit';
  dialog.append(header, intro, selectLabel, fieldset, actions, noteLabel, save, download, count, status, exit);
  const launcher = node('button', '✳ 设计探索', 'explore-launcher'); launcher.type = 'button'; launcher.id = 'exploreLauncher';
  launcher.setAttribute('aria-haspopup', 'dialog');
  document.body.append(dialog, launcher);
  let state = { preset: 'baseline', tone: 'baseline', effects: [] };
  let comparing = false;
  const records = [];
  const clear = () => {
    root.removeAttribute('data-explore-tone');
    for (const effect of effects) root.removeAttribute(`data-fx-${effect.id}`);
  };
  const apply = () => {
    clear();
    if (!comparing && state.tone !== 'baseline') root.dataset.exploreTone = state.tone;
    if (!comparing) for (const id of state.effects) root.setAttribute(`data-fx-${id}`, 'on');
    for (const [id, input] of inputs) input.checked = state.effects.includes(id);
    select.value = state.preset;
    status.textContent = comparing ? '正在对照基线；松开恢复当前组合。' : `${presets[state.preset].title} · ${state.effects.length} 项效果${state.effects.length ? '（可单独撤下）' : ''}`;
  };
  const stopCompare = () => { if (comparing) { comparing = false; apply(); } };
  on(select, 'change', () => {
    const preset = presets[select.value];
    state = { preset: select.value, tone: preset.tone, effects: [...preset.effects] }; apply();
  });
  for (const [id, input] of inputs) on(input, 'change', () => {
    state.effects = input.checked ? [...new Set([...state.effects, id])] : state.effects.filter(value => value !== id); apply();
  });
  on(compare, 'pointerdown', event => { compare.setPointerCapture(event.pointerId); comparing = true; apply(); });
  on(compare, 'pointerup', stopCompare); on(compare, 'pointercancel', stopCompare);
  on(compare, 'keydown', event => { if (['Space', 'Enter'].includes(event.code)) { event.preventDefault(); comparing = true; apply(); } });
  on(compare, 'keyup', event => { if (['Space', 'Enter'].includes(event.code)) { event.preventDefault(); stopCompare(); } });
  on(compare, 'blur', stopCompare); on(window, 'blur', stopCompare);
  on(reset, 'click', () => { comparing = false; state = { preset: 'baseline', tone: 'baseline', effects: [] }; apply(); });
  const snapshot = () => ({ preset: state.preset, tone: state.tone, effects: [...state.effects], judgment: notes.value.trim(), time: new Date().toISOString() });
  on(save, 'click', () => { records.push(snapshot()); count.textContent = `已记录 ${records.length} 条判断。导出后才会保存为文件。`; status.textContent = '已记录当前组合与判断；可继续切换比较。'; });
  on(download, 'click', () => {
    const payload = { version: 1, purpose: 'Design exploration, not production preferences', records, current: snapshot() };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
    const anchor = node('a'); anchor.href = url; anchor.download = 'aqi-design-exploration.json'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = '已请求下载；请确认浏览器保存了记录。';
  });
  on(launcher, 'click', () => dialog.showModal());
  on(close, 'click', () => { stopCompare(); dialog.close(); launcher.focus(); });
  on(dialog, 'cancel', stopCompare);
  on(exit, 'click', () => {
    clear(); controller.abort(); dialog.close(); dialog.remove(); launcher.remove(); sheet.remove();
    const url = new URL(location.href); url.searchParams.delete('explore'); history.replaceState(null, '', url);
    document.querySelector('#textInput')?.focus({ preventScroll: true });
  });
  apply();
  // Keep the canvas visible on entry; the drawer is opened explicitly by the user.
}
