/* A bounded display of supplied context, never a claim about model reasoning. */
const kinds = {memory:'长期记忆',preference:'相处偏好',thread:'共同事项',thread_event:'事项记录',note:'笔记',task:'待办'};
const reasons = {query_retrieval:'按当前问题检索',scene_scope:'符合当前场景范围',selected_matter:'明确关联的事项',matter_recall:'按问题与近期记录规则提供',explicit_note_request:'明确的笔记请求',explicit_material_request:'明确请求参考本事项资料',metadata_only:'只提供资料索引',active_task:'当前待办'};
const purposes = {draft:'当前稿',reference:'参考资料',artifact:'成果'};
const states = {missing:'关联资料已缺失，未自动换用其他资料。',source_changed:'原资料关联已变化，请核对后重新提出请求。',unavailable:'本轮资料详情暂不可用。'};
const node = (tag, text) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; return element; };
const validId = value => (typeof value === 'string' && value.trim() && value.length <= 256 && !value.includes('\0')) || (Number.isSafeInteger(value) && value > 0);
const validCount = value => Number.isSafeInteger(value) && value >= 0;
function link(text, path, query) {
  const element = node('a', text); element.href = `${path}?${new URLSearchParams(query)}`;
  element.target = '_blank'; element.rel = 'noopener noreferrer'; return element;
}
function manage(kind, id) {
  const element = node('button', '查看管理'); element.type = 'button';
  element.addEventListener('click', () => window.dispatchEvent(new CustomEvent('context-reference-manage', {detail:{kind,id:String(id)}})));
  return element;
}
export function appendContextUsage(container, usage) {
  if (!container || !usage || usage.schema_version !== 1 || typeof usage.enabled !== 'boolean') return null;
  const previous = [...container.children].find(child => child.classList.contains('context-usage'));
  const details = node('details'); details.className = 'context-usage'; details.open = previous?.open || false;
  details.append(node('summary', '本轮参考'));
  if (!usage.enabled) {
    details.append(node('p', '本轮已关闭记忆，没有自动带入保存的记忆、历史偏好或关联资料。'));
  } else {
    details.append(node('p', '这里显示系统提供给模型的资料及选择规则，不代表模型实际引用或推理理由。'));
    if (Object.hasOwn(states, usage.status)) details.append(node('p', states[usage.status]));
    const budget = usage.budget;
    if (budget?.scope === 'dynamic_context' && budget.unit === 'characters' && validCount(budget.used_chars) && validCount(budget.max_chars)) {
      const omitted = validCount(budget.omitted_items) ? `；因预算省略 ${budget.omitted_items} 项` : '';
      details.append(node('p', `补充资料：${budget.used_chars} / ${budget.max_chars} 字符${omitted}。`));
    }
    const qualifications = usage.qualifications || {};
    if (qualifications.upstream_note_body_truncated === true) {
      details.append(node('p', '当前稿正文已截取；片段可能缺少条件或否定，请核对完整保存版本。'));
    }
    const materialTruncation = qualifications.upstream_material_bodies_truncated ?? qualifications.material_bodies_truncated;
    if (validCount(materialTruncation) && materialTruncation > 0) {
      details.append(node('p', `${materialTruncation} 份资料正文已截取；片段可能缺少条件或否定，请核对完整保存版本。`));
    }
    const upstreamCounts = [
      ['thread_omitted_event_count','事项记录','条'],
      ['thread_omitted_history_event_count','事项历史记录','份'],
      ['thread_omitted_history_version_count','事项历史版本','版'],
      ['omitted_materials','事项资料','项'],
    ].flatMap(([key, label, unit]) => validCount(qualifications[key]) && qualifications[key] > 0 ? [`${label} ${qualifications[key]} ${unit}`] : []);
    if (upstreamCounts.length) details.append(node('p', `资料视图省略合计（可能含上面的预算省略，不另累加）：${upstreamCounts.join('；')}。`));
    if (usage.items_truncated === true || (validCount(usage.omitted_usage_items) && usage.omitted_usage_items > 0)) {
      const count = validCount(usage.omitted_usage_items) && usage.omitted_usage_items > 0 ? `，还有 ${usage.omitted_usage_items} 项未展示` : '';
      details.append(node('p', `本轮参考清单已缩减${count}；未展示不表示资料未提供给模型。`));
    }
    const list = node('ul');
    for (const item of (Array.isArray(usage.items) ? usage.items : []).slice(0, 32)) {
      if (!item || !Object.hasOwn(kinds, item.kind) || !validId(item.id)) continue;
      const row = node('li');
      const version = Number.isSafeInteger(item.version) && item.version > 0 ? item.version : null;
      const versionLabel = item.kind === 'note' ? '保存版本' : item.kind === 'thread_event' ? '记录版本' : '版本';
      const purpose = Object.hasOwn(purposes, item.purpose) ? ` · ${purposes[item.purpose]}` : '';
      row.append(node('span', `${kinds[item.kind]} ${item.id}${version ? ` · ${versionLabel} ${version}` : ''}${purpose}`));
      row.append(node('small', Object.hasOwn(reasons, item.reason) ? reasons[item.reason] : '按上下文规则选入'));
      if (item.kind === 'note') {
        row.append(node('small', item.body_included === true ? '已提供正文片段' : '只提供索引，未提供正文'));
        if (version) row.append(link('打开保存版本', '/static/notes.html', {note_id:String(item.id),version:String(version)}));
      } else if (item.kind === 'thread' || (item.kind === 'thread_event' && validId(item.thread_id))) {
        row.append(link(item.kind === 'thread_event' ? '查看所属事项' : '查看事项', '/static/threads.html', {thread_id:String(item.kind === 'thread' ? item.id : item.thread_id)}));
      } else if (['memory','preference','task'].includes(item.kind)) row.append(manage(item.kind, item.id));
      if (item.kind === 'memory') {
        const source = {stated:'用户自述',observed:'观察记录',inferred:'推测记录',uncertain:'待核对记录',manual:'手动保存'};
        const evidence = {linked:'有原话证据',manual:'手动保存',legacy:'原话依据未关联',missing:'原话依据缺失'};
        const labels = [Object.hasOwn(source, item.source) ? source[item.source] : null, Object.hasOwn(evidence, item.evidence_state) ? evidence[item.evidence_state] : null].filter(Boolean);
        if (labels.length) row.append(node('small', labels.join(' · ')));
      }
      list.append(row);
    }
    if (list.children.length) details.append(list);
    else if (!usage.status) details.append(node('p', '本轮没有列出的补充资料。'));
    if (Array.isArray(usage.items) && usage.items.length > 32) details.append(node('small', '此处最多展示 32 项资料索引。'));
  }
  if (previous) previous.replaceWith(details); else container.append(details);
  return details;
}
