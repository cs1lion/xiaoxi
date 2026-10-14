import { BASELINE_SCENARIOS, VoiceBaseline } from './baseline.js';

const OPTIONS = {
  interruption: { unrated: '未评', correct: '打断成功', false: '误抢话', missed: '漏检', not_attempted: '未尝试打断' },
  recognition: { unrated: '未评', correct: '正确', needs_correction: '需改', failed: '失败 / 空识别' },
  seam: { unrated: '未评', smooth: '正常', gap: '卡顿 / 空隙', overlap: '重叠', cutoff: '截断' },
};
const STATUS = { active: '记录中', complete: '已结束', invalid: '无效', cancelled: '已取消' };
const INVALID = { mode_changed: '策略变化', device_changed: '采集环境变化', capture_lost: '麦克风采集停止',
  context_changed: '体验模式或端点等待变化',
  disconnected: '连接断开', event_limit: '事件达到上限', turn_limit: '轮数达到上限',
  duration_limit: '时长达到上限', clock_invalid: '时间无效' };
const TIMINGS = { speechLastEnergyToPlaybackScheduledMs: '末次能量→播放调度',
  audioSubmitToPlaybackScheduledMs: '语音提交→播放调度', audioSubmitToAsrReceivedMs: '语音提交→识别返回',
  gateOnsetToPlaybackStopRequestedMs: '抢话能量起点→停止请求',
  gateCandidateToPlaybackStopRequestedMs: '抢话候选→停止请求', decodeStartToEndMs: '音频解码',
  scheduledChunkGapMs: '片段调度间隙' };

/** Mount a local, opt-in experiment panel. Returns its strict metadata collector. */
export function mountVoiceBaseline({ root, getContext = () => ({}), onUpdate = () => {} } = {}) {
  if (!root || typeof root.replaceChildren !== 'function') throw new TypeError('A baseline panel root is required');
  const doc = root.ownerDocument;
  const make = (tag, text, className) => {
    const node = doc.createElement(tag); if (text) node.textContent = text;
    if (className) node.className = className; return node;
  };
  const panel = make('details'); panel.dataset.voiceBaseline = 'panel';
  panel.append(make('summary', '本地语音基线试验'));
  panel.append(make('p', '先开启连续语音或麦克风，再开始一项试验。默认不记录；只保留本页内存中的时长、计数和手动判定。导出不含录音、文字、设备名称或会话 ID。', 'hint'));
  panel.append(make('p', '停口→首声与开口→停止只能记录能量门限和浏览器调度代理；真实声学时延尚未测量。浏览器返回的 AEC 设置也不能证明回声已消除。', 'hint'));
  const row = make('div', null, 'row');
  const scenario = make('select'); scenario.dataset.baseline = 'scenario'; scenario.setAttribute('aria-label', '试验场景');
  for (const [value, text] of Object.entries(BASELINE_SCENARIOS)) { const option = make('option', text); option.value = value; scenario.append(option); }
  const device = make('select'); device.dataset.baseline = 'device'; device.setAttribute('aria-label', '实际播放设备');
  for (const [value, text] of [['speaker', '电脑外放'], ['headset', '耳机对照']]) { const option = make('option', text); option.value = value; device.append(option); }
  row.append(scenario, device); panel.append(row);
  const buttons = make('div', null, 'row');
  const start = make('button', '开始试验'); start.type = 'button'; start.dataset.baseline = 'start';
  const finish = make('button', '结束并保存判定'); finish.type = 'button'; finish.dataset.baseline = 'finish';
  const cancel = make('button', '取消本次'); cancel.type = 'button'; cancel.dataset.baseline = 'cancel';
  buttons.append(start, finish, cancel); panel.append(buttons);
  const judgments = make('div', null, 'row'), selections = {};
  for (const [key, label] of [['interruption', '打断'], ['recognition', '识别'], ['seam', '接缝']]) {
    const holder = make('label', `${label} `), select = make('select'); select.dataset.baseline = key;
    for (const [value, text] of Object.entries(OPTIONS[key])) { const option = make('option', text); option.value = value; select.append(option); }
    holder.append(select); judgments.append(holder); selections[key] = select;
  }
  panel.append(judgments);
  const status = make('p', null, 'hint'); status.dataset.baseline = 'status'; status.setAttribute('role', 'status'); panel.append(status);
  const results = make('p', null, 'hint'); results.dataset.baseline = 'results'; panel.append(results);
  const timings = make('div', null, 'hint'); timings.dataset.baseline = 'timings'; panel.append(timings);
  const exports = make('div', null, 'row');
  const download = make('button', '导出元数据 JSON'); download.type = 'button'; download.dataset.baseline = 'export';
  const clear = make('button', '清空本页试验'); clear.type = 'button'; clear.dataset.baseline = 'clear';
  exports.append(download, clear); panel.append(exports); root.replaceChildren(panel);
  const collector = new VoiceBaseline({ onUpdate: snapshot => { render(snapshot); onUpdate(snapshot); } });
  function render(snapshot) {
    scenario.disabled = device.disabled = snapshot.active;
    start.disabled = snapshot.active;
    finish.disabled = cancel.disabled = !snapshot.active;
    for (const select of Object.values(selections)) select.disabled = !snapshot.active;
    download.disabled = !snapshot.trials.length;
    clear.disabled = snapshot.active || !snapshot.trials.length;
    const latest = snapshot.trials.at(-1);
    status.textContent = latest ? `试验 ${latest.trial} · ${BASELINE_SCENARIOS[latest.scenario]} · ${STATUS[latest.status]} · ${latest.events.length} 个元数据事件` +
      (latest.invalidReason ? `（${INVALID[latest.invalidReason]}）` : '') : '采集关闭 · 尚无试验';
    const summary = collector.exportData().summary;
    results.textContent = `完成 ${summary.trials.complete} / 无效 ${summary.trials.invalid} / 取消 ${summary.trials.cancelled} · 手动判定误抢话 ${summary.interruptionOpportunities.falseTrials} 次、漏检 ${summary.interruptionOpportunities.missedTrials} 次；这些是试验样本数，未计算每小时发生率。`;
    timings.replaceChildren();
    for (const group of summary.groups) {
      const measured = Object.entries(group.metrics).filter(([, metric]) => metric.n > 0);
      if (!measured.length) continue;
      timings.append(make('p', `${BASELINE_SCENARIOS[group.scenario]} · ${group.device === 'speaker' ? '电脑外放' : '耳机'} · ${group.mode} · ${group.activity} · 端点等待 ${group.endpointWaitMs ?? '未知'}ms · ${group.input}`));
      const list = make('ul');
      for (const [key, metric] of measured) list.append(make('li', `${TIMINGS[key]}：p50 ${metric.p50}ms / p95 ${metric.p95}ms（${metric.n} 个代理样本）`));
      timings.append(list);
    }
  }
  start.addEventListener('click', () => {
    collector.setContext(getContext());
    if (!collector.startTrial({ scenario: scenario.value, device: device.value })) {
      status.textContent = '请先开启连续语音或麦克风；采集就绪后再开始试验。'; return;
    }
    for (const select of Object.values(selections)) select.value = 'unrated';
  });
  finish.addEventListener('click', () => collector.finishTrial(Object.fromEntries(Object.entries(selections).map(([key, select]) => [key, select.value]))));
  cancel.addEventListener('click', () => collector.cancel());
  clear.addEventListener('click', () => collector.clear());
  download.addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(collector.exportData(), null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob), link = make('a'); link.href = url; link.download = 'voice-baseline-metadata.json';
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
  });
  render(collector.snapshot()); return collector;
}
