import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Entirely intercepted browser fixtures: no real DB, LLM, microphone or paid audio.
const assets = new Map(await Promise.all(['notes.html', 'notes.js', 'style.css', 'editor-insights.js', 'editor-insights.css'].map(async name =>
  [`/static/${name}`, await readFile(fileURLToPath(new URL(`../web/${name}`, import.meta.url)), 'utf8')])));
assets.set('/embedded', `<!doctype html><body><iframe title="个人笔记编辑器" src="/static/notes.html?embedded=1" style="width:900px;height:900px;border:0"></iframe><script>
window.editorMessages=[];window.editorActive=false;window.editorWriting=false;
window.sendEditor=message=>document.querySelector('iframe').contentWindow.postMessage(message,location.origin);
addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==document.querySelector('iframe').contentWindow)return;editorMessages.push(event.data);if(event.data.type==='aqi-editor-ready'){sendEditor({type:'aqi-editor-visibility',active:editorActive});sendEditor({type:'aqi-editor-mode',writing:editorWriting});}});
</script></body>`);
const browser = await chromium.launch({ headless: true });
const checks = [], errors = [], allRequests = [];
const check = (ok, label) => { if (!ok) throw Error(label); checks.push(label); };
let serial = 0;
const bodyText = '这份计划想让大家先理解为什么要调整流程，再明确谁来负责。现在我写了一些背景和具体步骤，希望它能解释真正的问题，也给读者一个能够执行的下一步。读者可能没有看过之前的讨论，所以需要把关键条件写得更清楚。';

async function fixture({ embedded = false, available = true, reason = '', unsafePoint = true } = {}) {
  const page = await browser.newPage({ viewport: { width: 1100, height: 950 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install({ time: new Date('2026-10-13T12:00:00Z') });
  await page.addInitScript(() => {
    window.fakeAudio = { plays: 0, pauses: 0 };
    window.Audio = class {
      constructor(src) { this.src = src; this.onended = null; this.onerror = null; window.lastFakeAudio = this; }
      async play() { window.fakeAudio.plays++; }
      pause() { window.fakeAudio.pauses++; }
      removeAttribute() {} load() {}
    };
  });
  const requests = [], insights = new Map();
  let responseMode = 'insight', analyzePause = null, discussionPause = null;
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (assets.has(path)) return route.fulfill({ body: assets.get(path), contentType: path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html' });
    const body = request.postData() ? request.postDataJSON() : {};
    const item = { path, method: request.method(), body }; requests.push(item); allRequests.push(item);
    const reply = (status, json) => route.fulfill({ status, json });
    if (path === '/api/notes') return reply(200, { items: [] });
    if (path === '/api/notes/selection') return reply(200, { note_id: null, revision: 0 });
    if (path === '/api/editor-insights/status') return reply(200, { available, reason: reason || (available ? '' : 'model_unavailable'), pause_ms: 12000, cooldown_ms: 90000, max_checks: 5 });
    if (path === '/api/editor-insights/state') return reply(200, { status: 'quiet' });
    if (path === '/api/editor-insights/analyze') {
      if (analyzePause) { const paused = analyzePause; analyzePause = null; await paused; }
      if (responseMode === 'quiet') return reply(200, { status: 'quiet' });
      const insight = { id: `insight-${++serial}`, session_id: body.session_id, draft_revision: body.draft_revision,
        point: '这段的结论被铺垫盖住了。先交代要改哪件事，再说明为什么。' + (unsafePoint ? '<img src=x onerror=alert(1)>' : ''),
        anchor: body.body.slice(0, 30), teaser: '这段的结论被铺垫盖住了。', expires_at: Date.now() / 1000 + 3600 };
      // Browser virtual time starts here; fixture expiration must use its clock.
      insight.expires_at = await page.evaluate(() => Date.now() / 1000 + 3600);
      insights.set(insight.id, insight);
      try { return await reply(200, { status: 'insight', insight }); } catch { return; }
    }
    if (/^\/api\/editor-insights\/[^/]+\/speak$/.test(path)) {
      const id = path.split('/')[3], insight = insights.get(id);
      if (!insight || body.session_id !== insight.session_id || body.draft_revision !== insight.draft_revision) return reply(409, { detail: 'superseded' });
      return reply(200, { audio: 'ZmFrZQ==', format: 'mp3', text: insight.point });
    }
    if (/^\/api\/editor-insights\/[^/]+\/dismiss$/.test(path)) return reply(200, { status: 'quiet' });
    if (/^\/api\/editor-insights\/[^/]+\/feedback$/.test(path)) {
      const id = path.split('/')[3], insight = insights.get(id);
      if (!insight || body.session_id !== insight.session_id || body.draft_revision !== insight.draft_revision) return reply(409, { detail: 'superseded' });
      return reply(200, { feedback: body.value });
    }
    if (/^\/api\/editor-insights\/[^/]+\/discuss$/.test(path)) {
      const id = path.split('/')[3], insight = insights.get(id);
      if (discussionPause) { const paused = discussionPause; discussionPause = null; await paused; }
      if (!insight || body.session_id !== insight.session_id || body.draft_revision !== insight.draft_revision) return reply(409, { detail: 'superseded' });
      try { return await reply(200, { text: '因为读者还不知道要改哪件事。<img src=x onerror=alert(1)>', insight_id: id, draft_revision: body.draft_revision }); } catch { return; }
    }
    throw Error(`Unexpected request ${path}`);
  });
  await page.goto(`http://editor.test${embedded ? '/embedded' : '/static/notes.html'}`);
  const editor = embedded ? page.frameLocator('iframe') : page;
  await expect(editor.locator('#editorHeading')).toHaveText('新建笔记');
  const count = name => requests.filter(item => item.path.endsWith(`/${name}`)).length;
  const tick = async ms => { await page.clock.fastForward(ms); await page.evaluate(() => Promise.resolve()); };
  return { page, editor, requests, count, tick, setMode: mode => { responseMode = mode; }, pause: () => { let release; analyzePause = new Promise(resolve => { release = resolve; }); return release; },
    pauseDiscussion: () => { let release; discussionPause = new Promise(resolve => { release = resolve; }); return release; } };
}

try {
  if (!process.argv.includes('--short-height')) {
  const f = await fixture();
  await f.editor.locator('#title').fill('尚未保存的流程计划');
  await f.editor.locator('#body').fill(bodyText);
  await f.tick(120000);
  check(f.requests.filter(item => item.path.startsWith('/api/editor-insights')).length === 0, 'Default off sends no insight requests, including state registration');
  await f.editor.locator('#editorInsightToggle').click();
  await expect(f.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true');
  await f.editor.locator('#body').focus();
  await f.editor.locator('#body').evaluate(el => { el.setSelectionRange(25, 25); el.scrollTop = 20; });
  const caret = await f.editor.locator('#body').evaluate(el => ({ start: el.selectionStart, scroll: el.scrollTop }));
  await f.tick(11999); check(f.count('analyze') === 0, 'An enabled draft waits at least 12 seconds after typing');
  await f.tick(2); await expect(f.editor.locator('#editorInsightBubble')).toBeVisible();
  check(f.count('analyze') === 1 && f.count('speak') === 0, 'One paused-draft observation appears silently without generating audio');
  const analyzed = f.requests.find(item => item.path.endsWith('/analyze')).body;
  check(analyzed.title === '尚未保存的流程计划' && analyzed.body === bodyText && analyzed.enabled === true, 'Analysis uses the explicit current unsaved draft');
  check(await f.editor.locator('#body').evaluate((el, value) => document.activeElement === el && el.selectionStart === value.start && el.scrollTop === value.scroll, caret), 'Incoming bubble preserves editor focus, caret and scroll');
  await f.editor.locator('#editorInsightBubble').click();
  await expect(f.editor.locator('#editorInsightStop')).toBeVisible();
  await expect.poll(() => f.count('speak')).toBe(1);
  check((await f.editor.locator('#editorInsightDetail').innerText()).includes('<img src=x') && await f.editor.locator('#editorInsightDetail img').count() === 0, 'Expanded observation and exact anchor use safe plain text');
  await expect(f.editor.locator('#editorInsightBubble')).toBeDisabled();
  check(await f.page.evaluate(() => fakeAudio.plays) === 1, 'Click alone starts one local playback; no audio device is used by fixture');
  await f.editor.locator('#editorInsightStop').click();
  check(await f.page.evaluate(() => fakeAudio.pauses) > 0 && await f.editor.locator('#editorInsightDetail').isVisible(), 'Stop releases local audio and preserves readable observation');
  await f.editor.locator('#editorInsightUseful').click(); await expect(f.editor.locator('#editorInsightUseful')).toHaveAttribute('aria-pressed', 'true');
  await expect(f.editor.locator('#editorInsightRatingStatus')).toContainText('本次看稿');
  await f.editor.locator('#editorInsightMissed').click(); await expect(f.editor.locator('#editorInsightMissed')).toHaveAttribute('aria-pressed', 'true');
  check(await f.editor.locator('#editorInsightUseful').getAttribute('aria-pressed') === 'false' && f.requests.filter(item => item.path.endsWith('/feedback')).map(item => item.body.value).join(',') === 'useful,missed', 'Explicit feedback records only the current insight and visibly replaces the previous rating');
  check(f.count('dismiss') === 0 && f.count('speak') === 1 && await f.editor.locator('#body').inputValue() === bodyText, 'Rating does not dismiss, replay or modify the draft');
  await f.editor.locator('#editorInsightDiscuss').click();
  await expect(f.editor.locator('#editorInsightDiscussionInput')).toBeFocused();
  check(f.count('discuss') === 0 && f.count('speak') === 1, 'Continue discussion opens a standalone text entry without calling a model or replaying audio');
  await f.editor.locator('#editorInsightDiscussionInput').fill('为什么？我想保留这个铺垫。'); await f.editor.locator('#editorInsightDiscussionSend').click();
  await expect(f.editor.locator('#editorInsightDiscussionMessages')).toContainText('因为读者');
  const discussed = f.requests.find(item => item.path.endsWith('/discuss')).body;
  check(discussed.session_id === analyzed.session_id && discussed.draft_revision === analyzed.draft_revision && discussed.text === '为什么？我想保留这个铺垫。', 'A standalone follow-up sends its explicit text scoped to the same insight session and draft revision');
  check(await f.editor.locator('#editorInsightDiscussionMessages img').count() === 0 && await f.editor.locator('#editorInsightDiscussionInput').inputValue() === '' && f.count('speak') === 1, 'Discussion safely displays plain text and never generates follow-up audio automatically');
  await mkdir('eval/ui/editor-insights', { recursive: true });
  await f.page.screenshot({ path: 'eval/ui/editor-insights/standalone-discussion-fixture.png', fullPage: false });
  const nextBody = bodyText + '这是新增的具体目标，我们希望负责人能在下一次评审以前明确风险、时间、范围和验收方式。每一个步骤都需要真实的依据，并且让没有参加讨论的人也能理解。';
  await f.editor.locator('#body').fill(nextBody);
  await expect(f.editor.locator('#editorInsightBubble')).toBeHidden();
  await f.tick(12000); check(f.count('analyze') === 1, 'New input revokes the old bubble and respects the 90 second cooldown');
  await f.tick(78000); await expect.poll(() => f.count('analyze')).toBe(2); await expect(f.editor.locator('#editorInsightBubble')).toBeVisible();
  await f.editor.locator('#editorInsightBubble').click();
  await expect.poll(() => f.count('speak')).toBe(2);
  await f.editor.locator('#editorInsightDismiss').click();
  await expect(f.editor.locator('#editorInsightBubble')).toBeHidden();
  check(f.count('dismiss') === 1 && await f.editor.locator('#body').inputValue() === nextBody, 'Dismiss stops playback and never changes the draft');
  f.setMode('quiet');
  let currentBody = nextBody;
  for (let index = 0; index < 4; index++) { currentBody += `第${index}轮新增：` + '补充读者需要明确理解的限制与具体判断依据。'.repeat(4); await f.editor.locator('#body').fill(currentBody); await f.tick(90001); }
  check(f.count('analyze') === 5, 'One editing session performs at most five analysis rounds');
  await f.editor.locator('#editorInsightToggle').click(); await f.tick(100);
  const latestState = f.requests.filter(item => item.path.endsWith('/state')).at(-1);
  check(latestState.body.enabled === false, 'Turning off revokes the registered server session state');
  check(f.requests.filter(item => item.path.startsWith('/api/notes') && item.method !== 'GET').length === 0, 'Insights never save, associate notes or create tasks');
  await mkdir('eval/ui/editor-insights', { recursive: true });
  await f.page.screenshot({ path: 'eval/ui/editor-insights/quiet-writing.png', fullPage: true });
  await f.page.close();

  const composition = await fixture();
  await composition.editor.locator('#body').fill(bodyText); await composition.editor.locator('#editorInsightToggle').click();
  await expect(composition.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true');
  await composition.editor.locator('#body').dispatchEvent('compositionstart'); await composition.tick(120000);
  check(composition.count('analyze') === 0, 'Chinese IME composition never triggers analysis');
  await composition.editor.locator('#body').dispatchEvent('compositionend'); await composition.tick(12001);
  await expect(composition.editor.locator('#editorInsightBubble')).toBeVisible();
  await composition.editor.locator('#body').fill(bodyText + '短改动'); await composition.tick(120000);
  check(composition.count('analyze') === 1, 'Less than 60 changed characters does not trigger another round');
  await composition.page.close();

  const delayed = await fixture();
  await delayed.editor.locator('#body').fill(bodyText); await delayed.editor.locator('#editorInsightToggle').click();
  await expect(delayed.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true');
  const release = delayed.pause(); await delayed.tick(12001); await expect.poll(() => delayed.count('analyze')).toBe(1);
  await delayed.editor.locator('#body').fill('新草稿还没有写完'); release(); await delayed.tick(100);
  await expect(delayed.editor.locator('#editorInsightBubble')).toBeHidden();
  check(delayed.count('speak') === 0, 'Late old-draft analysis cannot revive a revoked bubble or speak');
  await delayed.page.close();

  const delayedDiscussion = await fixture();
  await delayedDiscussion.editor.locator('#body').fill(bodyText); await delayedDiscussion.editor.locator('#editorInsightToggle').click();
  await expect(delayedDiscussion.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true'); await delayedDiscussion.tick(12001);
  await expect(delayedDiscussion.editor.locator('#editorInsightBubble')).toBeVisible(); await delayedDiscussion.editor.locator('#editorInsightBubble').click();
  await delayedDiscussion.editor.locator('#editorInsightDiscuss').click(); await delayedDiscussion.editor.locator('#editorInsightDiscussionInput').fill('为什么？');
  const releaseDiscussion = delayedDiscussion.pauseDiscussion(); await delayedDiscussion.editor.locator('#editorInsightDiscussionSend').click();
  await expect.poll(() => delayedDiscussion.count('discuss')).toBe(1); await delayedDiscussion.editor.locator('#body').fill('我重新写了开头'); releaseDiscussion(); await delayedDiscussion.tick(100);
  check(await delayedDiscussion.editor.locator('#editorInsightBubble').isHidden() && await delayedDiscussion.editor.locator('#editorInsightDiscussionMessages').innerText() === '' && await delayedDiscussion.editor.locator('#body').inputValue() === '我重新写了开头', 'Editing cancels a pending follow-up and its late reply cannot revive discussion or overwrite the draft');
  await delayedDiscussion.page.close();

  const unavailable = await fixture({ available: false });
  await unavailable.editor.locator('#body').fill(bodyText); await unavailable.editor.locator('#editorInsightToggle').click();
  await expect(unavailable.editor.locator('#editorInsightFeedback')).toContainText('尚不可用'); await unavailable.tick(120000);
  check(unavailable.count('analyze') === 0 && unavailable.count('state') === 0, 'Unavailable models keep participation off and do not register drafts');
  await unavailable.page.close();

  const embedded = await fixture({ embedded: true });
  await embedded.editor.locator('#body').fill(bodyText); await embedded.editor.locator('#editorInsightToggle').click();
  await expect(embedded.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true'); await embedded.tick(120000);
  check(embedded.count('analyze') === 0, 'A hidden or inactive notes tab does not analyze despite participation being enabled');
  await embedded.page.evaluate(() => sendEditor({ type: 'aqi-editor-mode', writing: true }));
  await expect(embedded.editor.locator('#query')).toBeHidden();
  await embedded.page.evaluate(() => sendEditor({ type: 'aqi-editor-mode', writing: false }));
  await expect(embedded.editor.locator('#query')).toBeVisible();
  check(await embedded.editor.locator('#body').inputValue() === bodyText, 'Writing-mode library collapse preserves its DOM and current draft');
  await embedded.page.evaluate(() => sendEditor({ type: 'aqi-editor-visibility', active: true })); await embedded.tick(120000);
  check(embedded.count('analyze') === 0, 'Returning to the notes tab waits for the next edit');
  await embedded.editor.locator('#body').fill(bodyText + '继续编辑'); await embedded.tick(12001);
  await expect(embedded.editor.locator('#editorInsightBubble')).toBeVisible();
  await embedded.editor.locator('#editorInsightBubble').click();
  await expect.poll(async () => embedded.page.evaluate(() => editorMessages.filter(message => message.type === 'aqi-editor-insight-play').length)).toBe(1);
  const play = await embedded.page.evaluate(() => editorMessages.find(message => message.type === 'aqi-editor-insight-play'));
  check(embedded.count('speak') === 0 && typeof play.insight_id === 'string' && typeof play.draft_revision === 'number', 'Embedded click delegates only insight ID and revision to the parent playback queue');
  await embedded.page.evaluate(message => sendEditor({ type: 'aqi-editor-insight-playback', request_id: message.request_id, state: 'playing' }), play);
  await expect(embedded.editor.locator('#editorInsightStop')).toBeVisible();
  await embedded.page.evaluate(() => sendEditor({ type: 'aqi-editor-visibility', active: false }));
  await expect(embedded.editor.locator('#editorInsightBubble')).toBeHidden();
  const cancels = await embedded.page.evaluate(() => editorMessages.filter(message => message.type === 'aqi-editor-insight-cancel'));
  check(cancels.at(-1).draft_revision > play.draft_revision, 'Leaving the notes tab advances revision and cancels parent playback');
  await embedded.page.evaluate(message => sendEditor({ type: 'aqi-editor-insight-playback', request_id: message.request_id, state: 'playing' }), play);
  await expect(embedded.editor.locator('#editorInsightStop')).toBeHidden();
  check(await embedded.editor.locator('#body').inputValue() === bodyText + '继续编辑', 'Late parent feedback does not revive audio controls or overwrite the draft');
  await embedded.page.screenshot({ path: 'eval/ui/editor-insights/embedded-fixture.png', fullPage: true });
  await embedded.page.close();

  const handoff = await fixture({ embedded: true, unsafePoint: false });
  await handoff.page.evaluate(() => sendEditor({ type: 'aqi-editor-visibility', active: true }));
  await handoff.editor.locator('#body').fill(bodyText); await handoff.editor.locator('#editorInsightToggle').click();
  await expect(handoff.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true'); await handoff.tick(12001);
  await expect(handoff.editor.locator('#editorInsightBubble')).toBeVisible(); await handoff.editor.locator('#editorInsightBubble').click();
  await handoff.editor.locator('#editorInsightDiscuss').click();
  await expect.poll(async () => handoff.page.evaluate(() => editorMessages.filter(message => message.type === 'aqi-editor-insight-discuss').length)).toBe(1);
  const transfer = await handoff.page.evaluate(() => editorMessages.find(message => message.type === 'aqi-editor-insight-discuss'));
  const transferCancel = await handoff.page.evaluate(() => editorMessages.filter(message => message.type === 'aqi-editor-insight-cancel').at(-1));
  check(transfer.point.startsWith('这段的结论') && transfer.anchor === bodyText.slice(0, 30) && Number.isFinite(transfer.expires_at) && transferCancel.preserve_discussion === true && transferCancel.reason === 'handoff', 'Embedded continue discussion transfers the visible point and exact anchor while cancelling audio without revoking discussion');
  const acceptTransfer = { type: 'aqi-editor-insight-discussion', active: true, insight_id: transfer.insight_id, session_id: transfer.session_id, draft_revision: transfer.draft_revision };
  await handoff.page.evaluate(message => { sendEditor(message); sendEditor({ type: 'aqi-editor-visibility', active: false }); }, acceptTransfer);
  await handoff.tick(120000);
  const hiddenStates = handoff.requests.filter(item => item.path.endsWith('/state'));
  const hiddenCancel = await handoff.page.evaluate(() => editorMessages.filter(message => message.type === 'aqi-editor-insight-cancel').at(-1));
  check(hiddenStates.at(-1).body.enabled === true && hiddenStates.at(-1).body.draft_revision === transfer.draft_revision && hiddenCancel.draft_revision === transfer.draft_revision && hiddenCancel.preserve_discussion === true && handoff.count('analyze') === 1, 'Discussion handoff hides the editor without advancing revision, disabling server scope or reanalyzing');
  await handoff.page.evaluate(() => sendEditor({ type: 'aqi-editor-visibility', active: true })); await handoff.tick(120000);
  check(await handoff.editor.locator('#editorInsightBubble').isVisible() && await handoff.editor.locator('#editorInsightStop').isHidden() && await handoff.editor.locator('#body').inputValue() === bodyText && handoff.count('analyze') === 1 && handoff.count('speak') === 0 && handoff.count('discuss') === 0, 'Returning from chat preserves the same draft and viewpoint without replaying or starting another model call');
  await handoff.editor.locator('#body').fill(bodyText + '改一处'); await handoff.tick(100);
  const editedCancel = await handoff.page.evaluate(() => editorMessages.filter(message => message.type === 'aqi-editor-insight-cancel').at(-1));
  check(await handoff.editor.locator('#editorInsightBubble').isHidden() && editedCancel.draft_revision > transfer.draft_revision && editedCancel.preserve_discussion !== true, 'An actual edit revokes the transferred discussion with a newer revision');
  await handoff.page.close();

  const mobile = await fixture({ embedded: true, unsafePoint: false });
  await mobile.page.setViewportSize({ width: 390, height: 840 });
  await mobile.page.evaluate(() => { const iframe = document.querySelector('iframe'); iframe.style.width = 'calc(100vw - 16px)'; iframe.style.height = '800px'; sendEditor({ type: 'aqi-editor-mode', writing: true }); sendEditor({ type: 'aqi-editor-visibility', active: true }); });
  await mobile.editor.locator('#body').fill(bodyText.repeat(45));
  await mobile.editor.locator('#editorInsightToggle').click();
  await expect(mobile.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true');
  await mobile.editor.locator('#body').focus();
  await mobile.editor.locator('#body').evaluate(el => { el.setSelectionRange(250, 250); el.scrollTop = 350; document.querySelector('main').scrollTop = 250; });
  const beforeFloat = await mobile.editor.locator('#body').evaluate(el => ({ main: document.querySelector('main').scrollTop, body: el.scrollTop, caret: el.selectionStart }));
  await mobile.tick(12001); await expect(mobile.editor.locator('#editorInsightBubble')).toBeVisible();
  check(await mobile.editor.locator('#body').evaluate((el, previous) => document.activeElement === el && el.selectionStart === previous.caret && el.scrollTop === previous.body && document.querySelector('main').scrollTop === previous.main, beforeFloat), 'A floating observation stays visible beside a scrolled long draft without moving focus or scroll');
  await mobile.editor.locator('#editorInsightBubble').click();
  await expect(mobile.editor.locator('#editorInsightCollapse')).toBeVisible();
  for (const width of [390, 320]) {
    await mobile.page.setViewportSize({ width, height: 840 });
    const geometry = await mobile.editor.locator('.editor-insight-float').evaluate(el => {
      const rect = el.getBoundingClientRect(), main = document.querySelector('main').getBoundingClientRect();
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, mainBottom: main.bottom, width: innerWidth, height: innerHeight, overflow: document.documentElement.scrollWidth > innerWidth };
    });
    check(geometry.left >= 0 && geometry.right <= geometry.width && geometry.bottom <= geometry.height && !geometry.overflow, `${width}px explicitly expanded floating detail fits the viewport`);
    await mobile.page.screenshot({ path: `eval/ui/editor-insights/writing-observation-${width}.png`, fullPage: false });
  }
  await mobile.editor.locator('#editorInsightStop').click(); await mobile.editor.locator('#editorInsightCollapse').click();
  check(await mobile.editor.locator('#editorInsightDetail').isHidden() && await mobile.editor.locator('#editorInsightBubble').isVisible() && mobile.count('dismiss') === 0, 'Collapse keeps the observation available without dismissing or editing it');
  await mobile.editor.locator('#body').evaluate(el => { Object.defineProperty(document, 'hidden', { value: true, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); el.value += '隐藏期间的编辑'.repeat(20); el.dispatchEvent(new Event('input', { bubbles: true })); });
  await mobile.tick(120000);
  check(mobile.count('analyze') === 1 && await mobile.editor.locator('#editorInsightBubble').isHidden(), 'A hidden document revokes the bubble and does not analyze new input');
  await mobile.page.close();
  }

  const short = await fixture({ embedded: true, unsafePoint: false });
  await short.page.setViewportSize({ width: 502, height: 507 });
  await short.page.evaluate(() => { const iframe = document.querySelector('iframe'); iframe.style.width = 'calc(100vw - 16px)'; iframe.style.height = '340px'; sendEditor({ type: 'aqi-editor-mode', writing: true }); sendEditor({ type: 'aqi-editor-visibility', active: true }); });
  const firstScreen = await short.editor.locator('#body').evaluate(el => {
    const body = el.getBoundingClientRect(), title = document.querySelector('#title').getBoundingClientRect();
    return { bodyTop: body.top, titleBottom: title.bottom, height: innerHeight };
  });
  check(firstScreen.titleBottom < firstScreen.height && firstScreen.bodyTop < firstScreen.height - 24, 'First entry at 502x507 shows the title input and beginning of the body without scrolling');
  check(await short.editor.locator('.editor-note-context').evaluate(el => !el.open) && await short.editor.locator('.editor-insight-disclosure').isVisible() && await short.editor.locator('#editorInsightToggle').getAttribute('aria-pressed') === 'false', 'Short writing view folds repeated note context while retaining the off switch and full privacy disclosure');
  await short.editor.locator('.editor-note-context>summary').click();
  check(await short.editor.locator('#source').isVisible() && await short.editor.locator('#noteSelectionStatus').isVisible(), 'Original source and association information remains accessible in its compact details');
  await short.editor.locator('.editor-note-context>summary').click();
  await short.page.screenshot({ path: 'eval/ui/editor-insights/writing-first-screen-short.png', fullPage: false });
  await short.editor.locator('#body').fill(bodyText.repeat(15)); await short.editor.locator('#editorInsightToggle').click();
  await expect(short.editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true');
  const editableHeight = await short.editor.locator('main').evaluate(el => el.getBoundingClientRect().height);
  check(editableHeight >= 200, 'A 340px-high iframe retains at least 200px of editing area while participation is enabled');
  await short.editor.locator('#body').focus();
  await short.editor.locator('#body').evaluate(el => { el.setSelectionRange(220, 220); el.scrollTop = 160; document.querySelector('main').scrollTop = 500; });
  const shortBefore = await short.editor.locator('#body').evaluate(el => ({ main: document.querySelector('main').scrollTop, body: el.scrollTop, caret: el.selectionStart }));
  await short.tick(12001); await expect(short.editor.locator('#editorInsightBubble')).toBeVisible();
  check(await short.editor.locator('#body').evaluate((el, previous) => document.activeElement === el && el.selectionStart === previous.caret && el.scrollTop === previous.body && document.querySelector('main').scrollTop === previous.main, shortBefore), 'Automatic bubble appearance in a short iframe preserves focus, caret and both scroll positions');
  const collapsedShort = await short.editor.locator('.editor-insight-float').evaluate(el => {
    const rect = el.getBoundingClientRect(), main = document.querySelector('main').getBoundingClientRect();
    return rect.top >= main.bottom && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth;
  });
  check(collapsedShort, 'The automatic short-height bubble occupies only the reserved bottom area and covers no input');
  await short.page.screenshot({ path: 'eval/ui/editor-insights/writing-observation-short-collapsed.png', fullPage: false });
  await short.editor.locator('#editorInsightBubble').click();
  await expect(short.editor.locator('#editorInsightStop')).toBeVisible();
  const expandedShort = await short.editor.locator('.editor-insight-float').evaluate(el => {
    const rect = el.getBoundingClientRect(), stop = document.querySelector('#editorInsightStop').getBoundingClientRect(), collapse = document.querySelector('#editorInsightCollapse').getBoundingClientRect(), rating = document.querySelector('.editor-insight-rating').getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= innerHeight && stop.top >= rect.top && stop.bottom <= rect.bottom && collapse.bottom <= rect.bottom && rating.top >= rect.top && rating.bottom <= rect.bottom && document.documentElement.scrollWidth <= innerWidth;
  });
  check(expandedShort, 'Click-expanded short-height detail keeps feedback, stop and collapse visible without horizontal overflow');
  check(await short.editor.locator('main').evaluate((el, previous) => el.getBoundingClientRect().height >= 200 && el.scrollTop === previous.main, shortBefore), 'Explicit detail expansion leaves the editing area height and scroll unchanged');
  await short.page.screenshot({ path: 'eval/ui/editor-insights/writing-observation-short-expanded.png', fullPage: false });
  await short.editor.locator('#editorInsightCollapse').click();
  check(await short.editor.locator('#editorInsightDetail').isHidden() && await short.editor.locator('main').evaluate(el => el.getBoundingClientRect().height >= 200), 'Collapse restores the compact bubble without shrinking the short editing area');
  await short.page.close();
  check(errors.length === 0, 'No browser errors across insight fixtures');
  await writeFile(`eval/ui/editor-insights/${process.argv.includes('--short-height') ? 'short-height-results' : 'results'}.json`, JSON.stringify({ checks, errors, requestCount: allRequests.length, scope: 'Isolated routes, virtual time, fake audio; no real model, audio or production writes' }, null, 2));
  console.log('PASS', checks.length, checks);
} finally { await browser.close(); }
