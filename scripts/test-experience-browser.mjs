// Offline browser contract: fake parent, static route, no backend/devices/cloud.
import { chromium } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root = path.resolve(import.meta.dirname, '..');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
page.setDefaultTimeout(8000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let consent = true;
let confirmations = 0;
page.on('dialog', async dialog => { confirmations++; await (consent ? dialog.accept() : dialog.dismiss()); });
await page.route('http://localhost/**', async route => {
  const pathname = new URL(route.request().url()).pathname;
  if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/experience.css"><div id="experienceMount"></div><script type="module">
    import { mountExperience } from '/static/experience.js';
    window.h = { configs: [], texts: [], order: [] };
    window.api = mountExperience({
      sendConfig(config) { h.order.push('config'); if(h.failConfig) throw Error('disconnected'); h.configs.push(config); if(h.mutateConfig && config.experience_source?.context) config.experience_source.context.push({ text: 'parent mutation', translation: '', label: '', source_id: '' }); },
      sendText(text) { h.order.push('text'); h.texts.push(text); },
      onSwitch() { h.order.push('switch'); }
    });
  </script>` });
  if (!['/static/experience.js', '/static/experience.css'].includes(pathname)) return route.abort();
  return route.fulfill({ body: await readFile(path.join(root, 'web', path.basename(pathname))), contentType: pathname.endsWith('.js') ? 'text/javascript' : 'text/css' });
});
const fresh = async () => {
  await page.goto('http://localhost/');
  await page.waitForFunction(() => window.api);
  await page.locator('.experience-panel > summary').click();
};
const state = () => page.evaluate(() => ({ configs: h.configs, texts: h.texts, order: h.order, active: api.activeMode() }));
const ack = (message, correlated = true) => page.evaluate(({ message, correlated }) => {
  const requestId = h.configs.at(-1)?.experience_request_id;
  if (correlated && requestId && !Object.hasOwn(message, 'experience_request_id')) {
    message = { ...message, experience_request_id: requestId };
  }
  return api.onAck(message);
}, { message, correlated });
const click = id => page.locator(`#${id}`).click();
const handoff = async value => page.evaluate(value => {
  sessionStorage.setItem('neurosama:selected-source', JSON.stringify(value));
  return api.importSelection();
}, value);
const checks = [];
try {
  await fresh();
  assert.deepEqual(await state(), { configs: [], texts: [], order: [], active: 'off' });
  assert.equal(await page.locator('#experiencePractice').isDisabled(), true);
  assert.equal(await page.locator('#experienceExplain').isDisabled(), true);
  assert.equal(await page.locator('#experienceRelations').isDisabled(), true);
  assert.equal(await page.locator('#experienceKickoff').isDisabled(), true);
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  assert.match(await page.locator('#experienceMount').innerText(), /不是发音评分/);
  checks.push('mount has no sends, proactive speech, or transcript persistence');

  const xss = '<img src=x onerror="window.pwned=1"><script>bad()</script>';
  assert.equal(await handoff({ text: xss, translation: '<b>译文</b>', label: '<svg onload=bad()>', source_id: 'interpret:1', timestamp: Date.now() }), true);
  assert.equal(await page.locator('#experienceSource').inputValue(), xss);
  assert.equal(await page.locator('#experienceOrigin').inputValue(), 'recognized');
  assert.equal(await page.locator('#experienceMount img, #experienceMount svg, #experienceMount script').count(), 0);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('neurosama:selected-source')), null);
  assert.equal((await state()).configs.length, 0);
  for (const value of [
    { text: 'expired', timestamp: Date.now() - 600001 },
    { text: 'future', timestamp: Date.now() + 60000 },
    { text: 'no timestamp' },
    { text: 'x'.repeat(3001), timestamp: Date.now() },
    { text: 'ok', translation: 'x'.repeat(3001), timestamp: Date.now() },
    { text: 'ok', label: 'x'.repeat(201), timestamp: Date.now() },
    { text: 'ok', source_id: 'x'.repeat(201), timestamp: Date.now() },
    { text: 123, timestamp: Date.now() },
  ]) {
    assert.equal(await handoff(value), false);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('neurosama:selected-source')), null);
  }
  await page.evaluate(() => sessionStorage.setItem('neurosama:selected-source', '{bad json'));
  assert.equal(await page.evaluate(() => api.importSelection()), false);
  assert.equal(await page.locator('#experienceSource').inputValue(), xss);
  checks.push('bounded one-shot handoff, 10-minute expiry, malformed data, and DOM-safe XSS');

  await page.locator('#experienceMode').selectOption('expression');
  consent = false;
  await click('experienceApply');
  assert.deepEqual((await state()).order, []);
  consent = true;
  await click('experienceApply');
  let result = await state();
  assert.deepEqual(result.order, ['switch', 'config']);
  assert.equal(result.configs[0].type, 'config');
  const rejectedRequestId = result.configs[0].experience_request_id;
  assert.match(rejectedRequestId, /^experience-\d+$/);
  assert.deepEqual(result.configs[0].experience, { mode: 'expression', scene: '' });
  assert.deepEqual(result.configs[0].experience_source, { text: xss, translation: '<b>译文</b>', label: '<svg onload=bad()>', source_id: 'interpret:1' });
  assert.equal(result.active, 'off');
  assert.equal(await page.locator('#experiencePractice').isDisabled(), true);
  assert.equal(await ack({ type: 'config_ack', practice: { mode: 'off' } }), false);
  assert.equal(await page.locator('#experienceApply').isDisabled(), true);
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'off' } }, false), false);
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'off' }, experience_request_id: 'experience-0' }), false);
  assert.equal(await ack({ type: 'config_ack', error: 'unrelated settings failure' }, false), false);
  assert.equal(await page.locator('#experienceApply').isDisabled(), true);
  assert.equal((await state()).active, 'off');
  assert.equal(await page.locator('#experiencePractice').isDisabled(), true);
  await ack({ type: 'config_ack', experience_error: '<img src=x onerror=bad()>' });
  assert.equal((await state()).active, 'off');
  assert.equal(await page.locator('#experienceApply').isEnabled(), true);
  assert.equal(await page.locator('#experienceStatus img').count(), 0);
  await click('experienceApply');
  const acceptedRequestId = (await state()).configs.at(-1).experience_request_id;
  assert.notEqual(acceptedRequestId, rejectedRequestId);
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'expression', source: result.configs[0].experience_source }, experience_request_id: rejectedRequestId }), false);
  assert.equal(await page.locator('#experienceApply').isDisabled(), true);
  assert.equal((await state()).active, 'off');
  await ack({ type: 'config_ack', experience: { mode: 'expression', scene: '', source: result.configs[0].experience_source } });
  assert.equal((await state()).active, 'expression');
  assert.equal(await page.locator('#experienceApply').isEnabled(), true);
  const beforeDuplicateAck = await state();
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'off' }, experience_request_id: acceptedRequestId }), false);
  assert.equal(await ack({ type: 'config_ack', experience_error: 'late rejection', experience_request_id: rejectedRequestId }), false);
  assert.deepEqual(await state(), beforeDuplicateAck);
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'expression', source: result.configs[0].experience_source } }, false), true);
  assert.deepEqual(await state(), beforeDuplicateAck);
  await click('experiencePractice');
  assert.equal((await state()).texts[0], '请围绕选中的原句给我一个新场景，让我尝试用出这个表达。');
  assert.equal(await page.locator('#experienceExplain').isDisabled(), true);
  checks.push('explicit confirmation precedes one parent cancellation; only actual successful ack activates source actions');
  checks.push('experience request IDs correlate successful/rejected acknowledgements; ordinary settings snapshots and old request IDs cannot unlock pending apply; duplicate or late correlated acknowledgements are ignored; idle uncorrelated snapshots remain valid');

  await page.locator('#experienceSource').fill('A manually corrected sentence.');
  assert.equal(await page.locator('#experienceOrigin').inputValue(), 'manual');
  await page.locator('#experienceSourceLabel').fill('手动提供原句');
  await page.locator('#experienceMode').selectOption('listen');
  await click('experienceApply');
  result = await state();
  assert.equal(result.configs.at(-1).experience_source.source_id, '');
  await ack({ type: 'config_ack', experience: { mode: 'listen', source: result.configs.at(-1).experience_source } });
  await click('experienceExplain');
  assert.match((await state()).texts.at(-1), /^请解释我选中的这句/);
  assert.equal(await page.locator('#experiencePractice').isDisabled(), true);
  const beforeClear = (await state()).configs.length;
  await click('experienceClearSource');
  assert.equal(await page.locator('#experienceSource').inputValue(), '');
  assert.equal((await state()).configs.length, beforeClear);
  assert.equal((await state()).active, 'listen');
  checks.push('manual editing invalidates recognition id; explain/practice gated by acknowledged mode; local clear sends nothing');

  // Programmatic over-limit values must be rejected, not silently truncated by input maxlength.
  await page.evaluate(() => document.getElementById('experienceSource').value = 'x'.repeat(3001));
  const dialogsBeforeBound = confirmations;
  await click('experienceApply');
  assert.equal((await state()).configs.length, beforeClear);
  assert.equal(confirmations, dialogsBeforeBound);
  await page.locator('#experienceSource').fill('valid');
  await page.evaluate(() => document.getElementById('experienceSourceLabel').value = 'x'.repeat(201));
  await click('experienceApply');
  assert.equal((await state()).configs.length, beforeClear);
  await page.locator('#experienceSourceLabel').fill('manual');
  await page.evaluate(() => document.getElementById('experienceTranslation').value = 'x'.repeat(3001));
  await click('experienceApply');
  assert.equal((await state()).configs.length, beforeClear);
  await page.locator('#experienceTranslation').fill('translation');
  checks.push('apply independently validates source, translation and metadata bounds');

  for (const mode of ['off', 'focus', 'walk', 'winddown', 'vent', 'discuss', 'theatre']) {
    await page.locator('#experienceMode').selectOption(mode);
    await click('experienceApply');
    result = await state();
    assert.equal(result.configs.at(-1).experience.mode, mode);
    assert.equal('experience_source' in result.configs.at(-1), false);
    await ack({ type: 'config_ack', experience: result.configs.at(-1).experience });
    assert.equal((await state()).active, mode);
  }
  assert.equal((await state()).texts.length, 2);
  assert.equal((await state()).configs.at(-1).experience.scene, 'rain-radio');
  await click('experienceKickoff');
  assert.match((await state()).texts.at(-1), /虚构/);
  for (const scene of ['spaceship', 'mystery', 'custom']) {
    await page.locator('#experienceScene').selectOption(scene);
    if (scene === 'custom') await page.locator('#experienceCustomScene').fill('A fictional seaside library.');
    await click('experienceApply');
    result = await state();
    assert.equal(result.configs.at(-1).experience.scene, scene === 'custom' ? 'A fictional seaside library.' : scene);
    await ack({ type: 'config_ack', experience: result.configs.at(-1).experience });
  }
  await ack({ type: 'config_ack', experience: { mode: 'theatre', scene: xss } }, false);
  assert.match(await page.locator('#experienceStatus').innerText(), /<img/);
  assert.equal(await page.locator('#experienceStatus img').count(), 0);
  checks.push('all nine modes, no source leakage into other modes; presets/custom fiction; kickoff only user controlled');

  await fresh();
  await page.locator('#experienceMode').selectOption('listen');
  await click('experienceApply');
  assert.equal('experience_source' in (await state()).configs[0], false);
  await ack({ type: 'config_ack', experience: { mode: 'listen', source: null } });
  assert.equal(await page.locator('#experienceExplain').isDisabled(), true);
  await page.evaluate(() => h.failConfig = true);
  await page.locator('#experienceMode').selectOption('focus');
  await click('experienceApply');
  assert.equal((await state()).active, 'listen');
  assert.match(await page.locator('#experienceStatus').innerText(), /未完成/);
  assert.equal(await page.locator('#experienceApply').isEnabled(), true);
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  // Optional explicit local shelf stores only source fields, never generated chat or source ids.
  await page.locator('.experience-shelf summary').click();
  await page.locator('#experienceSource').fill(xss);
  await page.locator('#experienceTranslation').fill('A translation');
  await page.locator('#experienceSourceLabel').fill('My selected source');
  consent = false;
  await click('experienceSaveSource');
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  consent = true;
  await click('experienceSaveSource');
  let saved = await page.evaluate(() => JSON.parse(localStorage.getItem('neurosama:source-bookmarks')));
  assert.deepEqual(saved, [{ text: xss, translation: 'A translation', label: 'My selected source' }]);
  assert.equal(await page.locator('#experienceShelfList img, #experienceShelfList script').count(), 0);
  const savedConfigCount = (await state()).configs.length;
  await click('experienceClearSource');
  await click('experienceRestore0');
  assert.equal(await page.locator('#experienceSource').inputValue(), xss);
  assert.equal((await state()).configs.length, savedConfigCount);
  await fresh();
  assert.equal((await state()).configs.length, 0);
  assert.equal(await page.locator('#experienceSource').inputValue(), '');
  await page.locator('.experience-shelf summary').click();
  assert.equal(await page.locator('#experienceRestore0').count(), 1);
  await click('experienceDelete0');
  assert.equal(await page.evaluate(() => localStorage.getItem('neurosama:source-bookmarks')), null);
  await page.locator('#experienceSource').fill('A new source');
  await click('experienceSaveSource');
  await click('experienceClearShelf');
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  await page.evaluate(() => localStorage.setItem('neurosama:source-bookmarks', '{bad'));
  await fresh();
  assert.equal(await page.locator('#experienceRestore0').count(), 0);
  await page.evaluate(() => localStorage.removeItem('neurosama:source-bookmarks'));
  checks.push('explicit consent-only local original-source shelf, DOM-safe rendering, restore without apply, delete/clear and malformed storage');
  await fresh();
  const neighbor = { text: xss, translation: '<b>neighbor translation</b>', label: '<svg onload=bad()>', source_id: 'neighbor:1' };
  const selected = { text: 'Selected sentence', translation: 'Selected translation', label: 'Selected label', source_id: 'selected:1', context: [neighbor], timestamp: Date.now() };
  assert.equal(await handoff(selected), true);
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), true);
  assert.match(await page.locator('#experienceContextPreview').innerText(), /邻近原句：1 条/);
  assert.match(await page.locator('#experienceContextPreview').innerText(), /<img/);
  assert.equal(await page.locator('#experienceContextPreview img, #experienceContextPreview svg, #experienceContextPreview script').count(), 0);
  assert.equal(await page.evaluate(() => api.importSelection()), false);
  assert.equal((await state()).configs.length, 0);
  const completeNeighbor = entry => ({ text: '', translation: '', label: '', source_id: '', ...entry });
  const invalidContexts = [
    null, {}, [null], [{ text: ' ' }], [{ text: 1 }],
    [{ text: 'ok', unknown: 'bad' }], [{ text: 'ok', context: [] }],
    [{ text: 'x'.repeat(3001) }], [{ text: 'ok', translation: 'x'.repeat(3001) }],
    [{ text: 'ok', label: 'x'.repeat(201) }], [{ text: 'ok', source_id: 'x'.repeat(201) }],
    Array.from({ length: 5 }, () => ({ text: 'ok' })),
    [{ text: 'ok', source_id: ' selected:1 ' }],
    [{ text: 'ok', source_id: 'same' }, { text: 'ok', source_id: ' same ' }],
    [{ text: 'x'.repeat(3000), translation: 'y'.repeat(3000), label: 'z' }],
  ];
  for (const context of invalidContexts) {
    const completed = Array.isArray(context) ? context.map(entry => entry && typeof entry === 'object' ? completeNeighbor(entry) : entry) : context;
    assert.equal(await handoff({ ...selected, context: completed, timestamp: Date.now() }), false);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('neurosama:selected-source')), null);
  }
  for (const key of ['text', 'translation', 'label', 'source_id']) {
    const incomplete = { ...neighbor };
    delete incomplete[key];
    assert.equal(await handoff({ ...selected, context: [incomplete], timestamp: Date.now() }), false);
  }
  assert.equal(await handoff({ ...selected, unknown: 'bad', timestamp: Date.now() }), false);
  assert.equal(await handoff({ ...selected, timestamp: Date.now() + 60000 }), false);
  assert.equal(await handoff({ ...selected, context: [completeNeighbor({ text: 'x'.repeat(3000), translation: 'y'.repeat(3000) })], timestamp: Date.now() }), true);
  assert.equal(await handoff({ ...selected, context: [completeNeighbor({ text: 'ok', source_id: ' ' }), completeNeighbor({ text: 'also ok', source_id: '' })], timestamp: Date.now() }), true);
  assert.equal(await handoff({ ...selected, context: [], timestamp: Date.now() }), true);
  assert.equal(await handoff({ ...selected, timestamp: Date.now() }), true);
  await page.locator('#experienceMode').selectOption('listen');
  consent = false;
  await click('experienceApply');
  assert.equal((await state()).configs.length, 0);
  consent = true;
  await page.evaluate(() => h.mutateConfig = true);
  await click('experienceApply');
  await ack({ type: 'config_ack', experience: { mode: 'listen' } });
  assert.match(await page.locator('#experienceStatus').innerText(), /邻近原句 1 条/);
  assert.doesNotMatch(await page.locator('#experienceStatus').innerText(), /parent mutation|<img/);
  assert.equal(await page.locator('#experienceExplain').isEnabled(), true);
  await page.evaluate(() => h.mutateConfig = false);
  await click('experienceApply');
  result = await state();
  assert.deepEqual(result.configs.at(-1).experience_source.context, [neighbor]);
  await ack({ type: 'config_ack', experience: { mode: 'listen' } });
  const contextConfigs = (await state()).configs.length;
  await click('experienceDiscardContext');
  assert.equal(await page.locator('#experienceSource').inputValue(), selected.text);
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), false);
  assert.equal((await state()).configs.length, contextConfigs);
  assert.equal(await page.locator('#experienceExplain').isDisabled(), true);
  assert.equal(await page.locator('#experienceRelations').isDisabled(), true);
  await click('experienceApply');
  result = await state();
  assert.equal('context' in result.configs.at(-1).experience_source, false);
  assert.equal(result.configs.at(-1).experience_source.source_id, selected.source_id);
  await ack({ type: 'config_ack', experience: { mode: 'listen' } });
  for (const id of ['experienceSource', 'experienceTranslation', 'experienceSourceLabel']) {
    assert.equal(await handoff({ ...selected, timestamp: Date.now() }), true);
    await page.locator(`#${id}`).fill('Manual correction');
    assert.equal(await page.locator('#experienceContextPreview').isVisible(), false);
    assert.equal(await page.locator('#experienceOrigin').inputValue(), 'manual');
    await click('experienceApply');
    result = await state();
    assert.equal('context' in result.configs.at(-1).experience_source, false);
    assert.equal(result.configs.at(-1).experience_source.source_id, '');
    await ack({ type: 'config_ack', experience: { mode: 'listen' } });
  }
  assert.equal(await handoff({ ...selected, timestamp: Date.now() }), true);
  await page.locator('.experience-shelf summary').click();
  await click('experienceSaveSource');
  saved = await page.evaluate(() => JSON.parse(localStorage.getItem('neurosama:source-bookmarks')));
  assert.deepEqual(saved, [{ text: selected.text, translation: selected.translation, label: selected.label }]);
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), true);
  await click('experienceRestore0');
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), false);
  await click('experienceApply');
  result = await state();
  assert.equal('context' in result.configs.at(-1).experience_source, false);
  assert.equal(result.configs.at(-1).experience_source.source_id, '');
  await ack({ type: 'config_ack', experience: { mode: 'listen' } });
  for (const mode of ['expression', 'off', 'focus', 'theatre']) {
    assert.equal(await handoff({ ...selected, timestamp: Date.now() }), true);
    await page.locator('#experienceMode').selectOption(mode);
    await click('experienceApply');
    result = await state();
    if (mode === 'expression') assert.deepEqual(result.configs.at(-1).experience_source.context, [neighbor]);
    else assert.equal('experience_source' in result.configs.at(-1), false);
    await ack({ type: 'config_ack', experience: result.configs.at(-1).experience });
  }
  await click('experienceClearSource');
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), false);
  checks.push('safe one-shot neighboring preview; strict nested bounds/keys/IDs and 6000-char budget; consent-only source modes; detached apply snapshot; discard/edit/clear/restore never persist or leak neighbors');

  await page.evaluate(() => localStorage.removeItem('neurosama:source-bookmarks'));
  await fresh();
  const sourceA = {
    text: 'It looked impossible. However, she kept trying.',
    translation: '这看起来不可能。不过，她仍在尝试。',
    label: 'Selected A', source_id: 'discussion:2',
    context: [
      { text: 'Mia was trying to fix the old radio.', translation: '米娅在修旧收音机。', label: 'Before A', source_id: 'discussion:1' },
      { text: xss, translation: '<b>After A translation</b>', label: 'After A', source_id: 'discussion:3' },
    ],
  };
  const sourceB = {
    text: 'Nevertheless, the team decided to continue.',
    translation: '尽管如此，团队决定继续。',
    label: 'Selected B', source_id: 'discussion:5',
    context: [{ text: 'The first attempt had failed.', translation: '第一次尝试失败了。', label: 'Before B', source_id: 'discussion:4' }],
  };
  const activeSourceText = () => page.locator('#experienceActiveSource').textContent();
  const disabledSourceActions = async () => {
    for (const id of ['experienceExplain', 'experiencePractice', 'experienceRelations']) {
      assert.equal(await page.locator(`#${id}`).isDisabled(), true, `${id} must wait for the applied source`);
    }
  };
  const attemptDisabledActions = async () => {
    const before = await state();
    await page.evaluate(() => {
      for (const id of ['experienceExplain', 'experiencePractice', 'experienceRelations']) document.getElementById(id).click();
    });
    assert.deepEqual(await state(), before);
  };

  assert.equal(await handoff({ ...sourceA, timestamp: Date.now() }), true);
  await page.locator('#experienceMode').selectOption('listen');
  await disabledSourceActions();
  assert.equal((await state()).configs.length, 0);
  assert.equal((await state()).texts.length, 0);
  consent = false;
  await click('experienceApply');
  assert.deepEqual((await state()).order, []);
  consent = true;
  await click('experienceApply');
  await disabledSourceActions();
  assert.equal((await state()).texts.length, 0);
  const sourceARequestId = (await state()).configs.at(-1).experience_request_id;
  await ack({ type: 'config_ack', experience: { mode: 'listen', source: sourceA } });
  const confirmedA = await activeSourceText();
  for (const text of [sourceA.text, sourceA.translation, sourceA.label, ...sourceA.context.flatMap(entry => [entry.text, entry.translation, entry.label])]) {
    assert.ok(confirmedA.includes(text), `applied source preview must contain ${text}`);
  }
  assert.equal(await page.locator('#experienceActiveSource img, #experienceActiveSource svg, #experienceActiveSource script').count(), 0);
  assert.equal(await page.locator('#experienceExplain').isEnabled(), true);
  assert.equal(await page.locator('#experienceRelations').isEnabled(), true);
  assert.equal(await page.locator('#experiencePractice').isDisabled(), true);
  await page.locator('#experienceActiveSource details > summary').click();
  assert.equal(await page.locator('#experienceActiveSource .experience-quote').last().isVisible(), true);
  assert.equal((await state()).texts.length, 0);
  await click('experienceRelations');
  const relationPrompt = (await state()).texts.at(-1);
  assert.equal(relationPrompt, '请结合已提供的邻近字幕，解释选中原句与前后内容的关系；如有代词或转折，请指出依据，区分原句与邻近字幕。片段不足以确定时请说明，不补写缺失内容。');
  assert.equal((await state()).configs.length, 1);

  const beforeEditing = await state();
  await page.locator('#experienceSource').fill('A correction that is still only a draft.');
  await disabledSourceActions();
  await attemptDisabledActions();
  assert.equal(await activeSourceText(), confirmedA);
  assert.ok((await page.locator('#experienceDraftStatus').innerText()).length > 0);
  assert.deepEqual(await state(), beforeEditing);
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), false);
  assert.equal(await handoff({ ...sourceA, timestamp: Date.now() }), true);
  assert.equal(await page.locator('#experienceRelations').isEnabled(), true);

  assert.equal(await handoff({ ...sourceB, timestamp: Date.now() }), true);
  await disabledSourceActions();
  await attemptDisabledActions();
  assert.equal(await activeSourceText(), confirmedA);
  assert.equal(await page.locator('#experienceSource').inputValue(), sourceB.text);
  consent = false;
  const beforeCancel = await state();
  await click('experienceApply');
  assert.deepEqual(await state(), beforeCancel);
  assert.equal(await activeSourceText(), confirmedA);
  await disabledSourceActions();
  consent = true;
  await click('experienceApply');
  assert.deepEqual((await state()).configs.at(-1).experience_source, sourceB);
  await disabledSourceActions();
  assert.equal(await activeSourceText(), confirmedA);
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'listen', source: sourceA } }, false), false);
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'listen', source: sourceA }, experience_request_id: sourceARequestId }), false);
  assert.equal(await page.locator('#experienceApply').isDisabled(), true);
  assert.equal(await activeSourceText(), confirmedA);
  await disabledSourceActions();
  await ack({ type: 'config_ack', experience_error: 'This isolated test rejected the update.' });
  assert.equal((await state()).active, 'listen');
  assert.equal(await activeSourceText(), confirmedA);
  await disabledSourceActions();
  await page.evaluate(() => h.failConfig = true);
  const beforeTransportFailure = (await state()).configs.length;
  await click('experienceApply');
  assert.equal((await state()).configs.length, beforeTransportFailure);
  assert.equal(await activeSourceText(), confirmedA);
  await disabledSourceActions();
  await page.evaluate(() => h.failConfig = false);
  await click('experienceApply');
  await ack({ type: 'config_ack', experience: { mode: 'listen', source: sourceB } });
  const confirmedB = await activeSourceText();
  assert.ok(confirmedB.includes(sourceB.text));
  assert.ok(confirmedB.includes(sourceB.context[0].text));
  assert.ok(!confirmedB.includes(sourceA.text));
  assert.equal(await page.locator('#experienceRelations').isEnabled(), true);
  assert.equal((await state()).texts.length, 1);
  await page.locator('#experienceActiveSource details > summary').click();
  const captureDirectory = path.join(root, 'eval', 'ui', 'experience');
  await mkdir(captureDirectory, { recursive: true });
  const originalViewport = page.viewportSize();
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, `${name} applied source panel must not overflow horizontally`);
    assert.equal(await page.locator('#experienceActiveSource .experience-quote').last().isVisible(), true);
    await page.screenshot({ path: path.join(captureDirectory, `applied-source-${name}.png`), fullPage: true });
  }
  await page.setViewportSize(originalViewport);
  checks.push('applied source and expanded neighbor preview have no horizontal overflow at 1440px desktop or 390px mobile; screenshots saved for visual review');
  await click('experienceRelations');
  assert.equal((await state()).texts.at(-1), relationPrompt);
  checks.push('applied source and neighboring subtitles have a safe independent preview; A-to-B draft changes, cancelled/failed applications and transport errors cannot send source shortcuts or replace confirmed A; only successful ack confirms B');

  const beforeDiscard = await state();
  await click('experienceDiscardContext');
  await disabledSourceActions();
  await attemptDisabledActions();
  assert.equal(await activeSourceText(), confirmedB);
  assert.deepEqual(await state(), beforeDiscard);
  assert.equal(await handoff({ ...sourceB, timestamp: Date.now() }), true);
  await page.locator('#experienceMode').selectOption('expression');
  await disabledSourceActions();
  await attemptDisabledActions();
  assert.equal((await state()).active, 'listen');
  assert.equal(await activeSourceText(), confirmedB);
  await page.locator('#experienceMode').selectOption('listen');
  assert.equal(await page.locator('#experienceRelations').isEnabled(), true);
  await click('experienceDiscardContext');
  await click('experienceApply');
  const sourceWithoutContext = (await state()).configs.at(-1).experience_source;
  assert.equal('context' in sourceWithoutContext, false);
  await ack({ type: 'config_ack', experience: { mode: 'listen', source: sourceWithoutContext } });
  assert.equal(await page.locator('#experienceExplain').isEnabled(), true);
  assert.equal(await page.locator('#experienceRelations').isDisabled(), true);
  assert.ok(!(await activeSourceText()).includes(sourceB.context[0].text));
  const beforeNoContextRelation = await state();
  await page.locator('#experienceRelations').evaluate(node => node.click());
  assert.deepEqual(await state(), beforeNoContextRelation);
  checks.push('neighbor discard and mode drafts block source shortcuts; restoring the exact applied draft re-enables them; relation questions require actual confirmed neighbors');

  // The submitted source stays A if the user imports B while the ack is pending.
  assert.equal(await handoff({ ...sourceA, timestamp: Date.now() }), true);
  await click('experienceApply');
  assert.equal(await handoff({ ...sourceB, timestamp: Date.now() }), true);
  await ack({ type: 'config_ack', experience: { mode: 'listen' } });
  assert.ok((await activeSourceText()).includes(sourceA.text));
  assert.ok(!(await activeSourceText()).includes(sourceB.text));
  await disabledSourceActions();
  await attemptDisabledActions();
  const beforeBookmark = await state();
  await page.locator('.experience-shelf summary').click();
  await click('experienceSaveSource');
  saved = await page.evaluate(() => JSON.parse(localStorage.getItem('neurosama:source-bookmarks')));
  assert.deepEqual(saved, [{ text: sourceB.text, translation: sourceB.translation, label: sourceB.label }]);
  assert.deepEqual(await state(), beforeBookmark);
  await click('experienceRestore0');
  await disabledSourceActions();
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), false);
  assert.ok((await activeSourceText()).includes(sourceA.text));
  assert.deepEqual(await state(), beforeBookmark);
  await click('experienceClearSource');
  await disabledSourceActions();
  assert.ok((await activeSourceText()).includes(sourceA.text));
  await page.locator('#experienceMode').selectOption('off');
  await click('experienceApply');
  assert.equal('experience_source' in (await state()).configs.at(-1), false);
  await ack({ type: 'config_ack', experience: { mode: 'off' } });
  assert.ok(!(await activeSourceText()).includes(sourceA.text));
  await disabledSourceActions();
  checks.push('late ack uses the submitted source snapshot rather than newer drafts; original-source bookmarks, restore and clear preserve confirmed preview and send nothing; acknowledged off clears it');

  assert.equal(await handoff({ ...sourceA, timestamp: Date.now() }), true);
  await page.locator('#experienceMode').selectOption('listen');
  await click('experienceApply');
  await ack({ type: 'config_ack', experience: { mode: 'listen', source: sourceA } });
  assert.equal(await page.locator('#experienceRelations').isEnabled(), true);
  assert.equal(await handoff({ ...sourceB, timestamp: Date.now() }), true);
  await click('experienceApply');
  assert.equal(await page.locator('#experienceApply').isDisabled(), true);
  const beforeDisconnect = await state();
  const beforeDisconnectStorage = await page.evaluate(() => localStorage.getItem('neurosama:source-bookmarks'));
  await page.evaluate(() => api.onDisconnect());
  assert.equal((await state()).active, 'off');
  assert.equal(await activeSourceText(), '');
  assert.equal(await page.locator('#experienceActiveSource').isVisible(), false);
  assert.equal(await page.locator('#experienceSource').inputValue(), sourceB.text);
  assert.equal(await page.locator('#experienceContextPreview').isVisible(), true);
  assert.equal(await page.locator('#experienceApply').isDisabled(), true);
  await disabledSourceActions();
  await attemptDisabledActions();
  const disconnectedState = await state();
  assert.deepEqual(disconnectedState.configs, beforeDisconnect.configs);
  assert.deepEqual(disconnectedState.texts, beforeDisconnect.texts);
  assert.deepEqual(disconnectedState.order, beforeDisconnect.order);
  assert.equal(await ack({ type: 'config_ack', experience: { mode: 'listen', source: sourceB } }), false);
  assert.deepEqual(await state(), disconnectedState);
  assert.equal(await activeSourceText(), '');
  assert.equal(await page.evaluate(() => localStorage.getItem('neurosama:source-bookmarks')), beforeDisconnectStorage);
  const disconnectDialogs = confirmations;
  await page.evaluate(() => {
    document.getElementById('experienceApply').click();
    document.querySelector('.experience-fields').requestSubmit();
  });
  assert.equal(confirmations, disconnectDialogs);
  assert.deepEqual(await state(), disconnectedState);
  checks.push('disconnect clears applied preview and pending state while retaining draft and local shelf; source actions and apply stay disabled; late acknowledgement and programmatic submission send nothing');
  assert.deepEqual(errors, []);
  checks.push('no-source ack cannot expose source action; transport errors never claim new active mode');
  console.log(JSON.stringify({ ok: true, checks }, null, 2));
} finally { await browser.close(); }
