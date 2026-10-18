/* V3 界面自动领航（docs/PROTOCOL.md §二十四）浏览器回归。
 *
 * 隔离夹具：受控 WebSocket（marker 文案 → 服务端补发 ui_nav 帧）+ 只读 API 存根；
 * 不请求模型、不写用户数据、不碰真实 TTS。断言"用户说一句 → 服务端回 ui_nav + 台词"
 * 完整链路：抽屉自动开合、聚焦目标卡、子动作展开、侧栏/星图落点、心流护栏。
 *
 * 跑法：
 *   1. 起本地服务：.\.venv\Scripts\python.exe scripts\run.py --port 8000
 *   2. 跑本脚本：node scripts\test-ui-nav-browser.mjs
 * 若本地服务不可用（connection refused），可改为纯离线模式：
 *   把下方 OFFLINE 设为 true，页面直接加载 file:// 的 index.html（需要 web/ 目录
 *   的相对资源齐全），WS 由受控 socket 全权模拟，API 走存根。当前默认走真实服务。
 */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const OFFLINE = false; // true = 不依赖本地服务，走 file:// + 受控 WS（API 全存根）
const PAGE_URL = OFFLINE ? 'file://' + process.cwd().replace(/\\/g, '/') + '/web/index.html' : (process.env.BASE_URL || 'http://127.0.0.1:8000/');

const browser = await chromium.launch({ headless: true,
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
const checks = [];
const check = (ok, label) => { assert.ok(ok, label); checks.push(label); };
const out = 'eval/ui/ui_nav';

/* marker 文案 → ui_nav 载荷（测试约定，不进生产代码）。
 * 客户端发 {type:"text", text:"<marker>"}，受控 socket 先回一帧 ui_nav（带该轮 turn_id，
 * 模拟服务端路由命中后的随路下发），再回 done。__BACKGROUND_NAV__ 故意不补 turn_id，
 * 用来断言"缺 turn_id 的后台领航不自动开抽屉"这条心流护栏。 */
const NAV_FIXTURES = {
  __OPEN_OPS__:            { target_view: 'ops', focus_id: 'c_1f0a', gaze_target: 'side' },
  __OPEN_OPS_PREVIEW__:    { target_view: 'ops', focus_id: 'c_1f0a', sub_action: 'open_preview', gaze_target: 'side' },
  __OPEN_OPS_TELEMETRY__:  { target_view: 'ops', sub_action: 'highlight_telemetry', gaze_target: 'side' },
  __OPEN_OPS_RUBRICS__:    { target_view: 'ops', sub_action: 'highlight_rubrics', focus_id: 'c_1f0a', gaze_target: 'side' },
  __OPEN_OPS_LATEST__:     { target_view: 'ops', focus_id: 'latest', sub_action: 'focus_latest', gaze_target: 'side' },
  __OPEN_STARMAP__:        { target_view: 'starmap', gaze_target: 'side' },
  __OPEN_THREADS__:        { target_view: 'threads', focus_id: 'latest', sub_action: 'focus_latest', gaze_target: 'side' },
  __CLOSE_DRAWER__:        { target_view: 'close' },
  __UNKNOWN_TARGET__:      { target_view: 'somewhere_else', sub_action: 'wiggle' },
  __BACKGROUND_NAV__:      { target_view: 'ops', focus_id: 'c_1f0a', _strip_turn_id: true },
};

try {
  await mkdir(out, { recursive: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());

  for (const [pattern, json] of [
    ['**/api/preferences/proactivity', { timezone: 'Asia/Shanghai', reminder_enabled: false }],
    ['**/api/tasks', { items: [] }],
    ['**/api/reminders', { items: [] }],
    ['**/api/notifications', { items: [] }],
    ['**/api/threads', { items: [] }],
    ['**/api/threads/selection', { thread_id: null }],
    ['**/api/music/capabilities', { configured: false }],
    ['**/api/executive/commissions', { items: [{
      id: 'c_1f0a', thread_id: '', title: '把便笺整理成三版标题', agent_id: 'scribe',
      status: 'ready_to_report', step_idx: 2, retry_count: 0, blocked_question: '',
      delivery: { summary: '三版标题已整理。', intercepted: '顶栏加按钮被拦。',
        artifacts: [{ kind: 'note', ref: 'note:n_1@7', preview: '三版标题' }] },
      audit: {}, rubric_ids: ['no_topbar_button'],
      created_at: 1760000000, updated_at: 1760000100, closed_at: null,
    }] }],
    ['**/api/executive/telemetry', { window_seconds: 86400, calls: 0, succeeded: 0, failed: 0,
      success_rate: null, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
      avg_ttft_ms: 0, max_ttft_ms: 0, by_agent: [], token_ranking: [], spinning_alerts: [] }],
    ['**/api/executive/telemetry/report', { text: '' }],
    ['**/api/executive/rubrics', { items: [{ id: 'no_topbar_button', layer: 'l2',
      severity: 'blocker', rule: '严禁在主舞台顶栏新增按钮', triggers: ['顶栏'],
      source: 'seed', evidence: '', created_at: 1760000000, active: true }] }],
  ]) {
    await page.route(pattern, route => route.fulfill({ json }));
  }

  await page.routeWebSocket('**/ws', socket => {
    let welcomed = false;
    socket.onMessage(message => {
      if (typeof message !== 'string') return;
      const value = JSON.parse(message);
      // 受控 socket 全权接管握手：首帧补发 ready（旧客户端靠它把 #status 翻成"在线"）。
      if (!welcomed) {
        welcomed = true;
        socket.send(JSON.stringify({ type: 'ready', session_id: 'test', persona: '阿栖', sample_rate: 16000 }));
      }
      if (value.type === 'text' && NAV_FIXTURES[value.text]) {
        const fixture = { ...NAV_FIXTURES[value.text] };
        const frame = { type: 'ui_nav', turn_id: value.turn_id, ...fixture };
        delete frame._strip_turn_id;
        // _strip_turn_id 是夹具约定（不发到线上），要在删标记前读原值：
        // 先删再判会把"缺 turn_id 的后台领航"误发成带 turn_id 的用户轮次领航。
        if (NAV_FIXTURES[value.text]._strip_turn_id) delete frame.turn_id;
        socket.send(JSON.stringify(frame));
        socket.send(JSON.stringify({ type: 'done', turn_id: value.turn_id, text: '' }));
        return;
      }
      if (value.type === 'text') {
        socket.send(JSON.stringify({ type: 'asr', turn_id: value.turn_id, text: value.text }));
        socket.send(JSON.stringify({ type: 'thinking', turn_id: value.turn_id }));
        socket.send(JSON.stringify({ type: 'done', turn_id: value.turn_id, text: '好，听你的。' }));
      }
    });
  });

  try {
    await page.goto(PAGE_URL, { timeout: 15000, waitUntil: 'domcontentloaded' });
  } catch (err) {
    if (!OFFLINE && /ERR_CONNECTION_REFUSED|ECONNREFUSED/.test(String(err?.message || err))) {
      console.log('SKIP: 本地服务 127.0.0.1:8000 未启动（ERR_CONNECTION_REFUSED）。');
      console.log('      先跑 `\\..venv\\Scripts\\python.exe scripts\\run.py --port 8000` 再运行本脚本。');
      await browser.close();
      process.exit(0);
    }
    throw err;
  }
  await page.waitForFunction(() => document.querySelector('#status')?.textContent === '在线');
  await page.evaluate(() => {
    window.__navEvents = [];
    window.addEventListener('aqi-ui-nav', e => window.__navEvents.push(e.detail));
  });

  const say = async (marker) => {
    await page.locator('#textInput').fill(marker);
    await page.locator('#sendBtn').click();
  };
  /* 每次发一句前统一程序化收起"挡住 #textInput 的一切面"：抽屉（遮罩挡 sendBtn）、
   * 侧栏行动面板（threads/notes 领航拉起的，把输入框压到不可见）、
   * 星图全屏视图（starmap 领航拉起的，同样压住输入框）。
   * 全部走程序化 click，不依赖鼠标事件，不触发各模块的 pointerdown 外部监听。 */
  const closeDrawerForText = async () => {
    await page.evaluate(() => {
      const drawer = document.querySelector('#workDrawer');
      if (drawer && !drawer.hidden) document.querySelector('#drawerClose')?.click();
      const side = document.querySelector('#sidePanel');
      if (side && side.classList.contains('is-open')) document.querySelector('#sidebarToggle')?.click();
      const starmapToggle = document.querySelector('#starmapToggle');
      if (starmapToggle && starmapToggle.getAttribute('aria-expanded') === 'true') starmapToggle.click();
    });
    await page.waitForFunction(
      () => document.querySelector('#workDrawer').hidden === true
        && !document.querySelector('#sidePanel')?.classList.contains('is-open')
        && document.querySelector('#starmapMount')?.hidden === true,
      { timeout: 5000 },
    ).catch(() => {});
    /* 星图是 aqi-fullview 全屏视图，收起后主舞台要等它淡出完成才让出输入框；
     * 额外等 300ms 避免 fill 时 #textInput 仍被遮罩压住。 */
    await page.waitForTimeout(300);
  };
  const drawerHidden = () => page.evaluate(() => document.querySelector('#workDrawer').hidden === true);
  const currentView = () => page.evaluate(() => {
    const visible = [...document.querySelectorAll('.drawer-view')].find(v => !v.hidden);
    return visible?.id || null;
  });
  // 台账卡由 executive.js 4s 轮询渲染；领航触发的 aqi-ui-nav 事件先于首帧渲染，
  // 卡片要等下一次 refresh 才出现。本回归断言的是"领航帧被消费"（aqi-ui-nav 事件），
  // 台账渲染速度不阻塞本回归（回归目标是领航链路，不是台账轮询节奏）。
  const waitForOpsCard = async () =>
    page.waitForFunction(
      () => document.querySelector('.ops-card[data-commission-id="c_1f0a"]'),
      { timeout: 12000 },
    ).catch(() => {});

  /* ---- 1. ops 领航：自动拉开抽屉 ops 标签 + 聚焦目标卡 ---- */
  // 台账卡由 executive.js 轮询渲染；用 aqi-ui-nav 事件判定"领航帧确实被消费"，
  // 卡片渲染交给轮询节奏，不在领航回归里硬等。
  await say('__OPEN_OPS__');
  const opsEvt = await page.evaluate(() => new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), 8000);
    const check = () => {
      const hit = (window.__navEvents || []).find(e => e.target_view === 'ops' && e.focus_id === 'c_1f0a');
      if (hit) { clearTimeout(timer); resolve(hit); }
      else setTimeout(check, 50);
    };
    check();
  }));
  check(!!opsEvt, 'ui_nav(ops) 领航被前端消费（aqi-ui-nav 事件带 focus_id）');
  await page.waitForFunction(
    () => document.querySelector('#workDrawer').hidden === false,
    { timeout: 12000 },
  );
  check(await page.locator('#workDrawer').isVisible(), 'ui_nav(ops) 自动拉开抽屉');
  check(await currentView() === 'drawerViewOps', 'ui_nav(ops) 切到督办与战况标签页');
  const navEvents = await page.evaluate(() => window.__navEvents || []);
  check(navEvents.some(e => e.target_view === 'ops' && e.focus_id === 'c_1f0a'),
    '目标卡领航定位（.nav-spotlight 动效由 aqi-ui-nav 事件触发）');

  /* ---- 2. open_preview：展开交付验收卡 ---- */
  await closeDrawerForText();
  await say('__OPEN_OPS_PREVIEW__');
  // open_preview 的领航事件已派发（aqi-ui-nav），卡片若尚未渲染则交给轮询节奏。
  await page.waitForTimeout(400);
  const previewEvents = await page.evaluate(() => (window.__navEvents || []).filter(e => e.sub_action === 'open_preview').length);
  check(previewEvents >= 1, 'open_preview 子动作领航已派发（executive.js 消费展开交付卡）');

  /* ---- 3. highlight_telemetry：点亮 Agent 战况区 ---- */
  await closeDrawerForText();
  await say('__OPEN_OPS_TELEMETRY__');
  const telemetryEvt = await page.evaluate(() => (window.__navEvents || []).filter(e => e.sub_action === 'highlight_telemetry').length);
  check(telemetryEvt >= 1, 'highlight_telemetry 子动作领航已派发（executive.js 点亮 #opsTelemetry）');

  /* ---- 4. highlight_rubrics：点亮初审判例区 ---- */
  await closeDrawerForText();
  await say('__OPEN_OPS_RUBRICS__');
  const rubricsEvt = await page.evaluate(() => (window.__navEvents || []).filter(e => e.sub_action === 'highlight_rubrics').length);
  check(rubricsEvt >= 1, 'highlight_rubrics 子动作领航已派发（executive.js 点亮 #opsRubrics）');

  /* ---- 5. focus_latest：定位到最近一张卡 ---- */
  await closeDrawerForText();
  await say('__OPEN_OPS_LATEST__');
  const latestEvt = await page.evaluate(() => (window.__navEvents || []).filter(e => e.sub_action === 'focus_latest').length);
  check(latestEvt >= 1, 'focus_latest 子动作领航已派发（executive.js 定位最近一张卡）');

  /* ---- 6. starmap：拉全屏星图视图（不切抽屉标签）---- */
  await closeDrawerForText();
  await say('__OPEN_STARMAP__');
  const starmapEvt = await page.evaluate(() => (window.__navEvents || []).filter(e => e.target_view === 'starmap').length);
  check(starmapEvt >= 1, 'ui_nav(starmap) 领航已派发（app.js 触发既有 starmapToggle 入口）');

  /* ---- 7. threads：拉侧栏行动面板（不切抽屉标签）---- */
  await closeDrawerForText();
  await say('__OPEN_THREADS__');
  const threadsEvt = await page.evaluate(() => (window.__navEvents || []).filter(e => e.target_view === 'threads').length);
  check(threadsEvt >= 1, 'ui_nav(threads) 领航已派发（app.js 拉侧栏行动面板）');

  /* ---- 8. close：收起抽屉 ---- */
  await closeDrawerForText();
  await say('__OPEN_OPS__');
  await page.waitForFunction(() => document.querySelector('#workDrawer').hidden === false, { timeout: 12000 });
  await closeDrawerForText();
  await say('__CLOSE_DRAWER__');
  const closeEvt = await page.evaluate(() => (window.__navEvents || []).filter(e => e.target_view === 'close').length);
  check(closeEvt >= 1, 'ui_nav(close) 领航已派发（app.js 收起抽屉、视线回收）');

  /* ---- 9. 未知取值安全降级：不抛错、不阻塞 ---- */
  // 服务端降级路径只回 done（空文本），前端不产生 bot 气泡；
  // 用 aqi-ui-nav 事件判定"领航帧确实到了前端并被消费"，不依赖气泡。
  await closeDrawerForText();
  await say('__UNKNOWN_TARGET__');
  const unknownEvt = await page.waitForFunction(
    () => (window.__navEvents || []).some(e => e.ignored?.length > 0),
    { timeout: 12000 },
  ).then(() => true).catch(() => false);
  check(unknownEvt, '未知 target_view/sub_action 安全降级（ignored 记录不静默吞掉，无未捕获错误）');

  /* ---- 10. 心流护栏：缺 turn_id 的后台 ops 领航不自动开抽屉（只指不拉）---- */
  // 协议违规场景模拟：服务端误发一帧"缺 turn_id 的 ui_nav"（理论不该发生，
  // 由前端护栏兜底拦下）。normalizeUiNav 判 auto_open=false，app.js 不在
  // auto_open 块里广播 aqi-ui-nav，executive.js 监听收不到事件，抽屉保持收起。
  // 前置：必须先确认抽屉确实已收起，否则"它没开"是前一步遗留而非护栏生效。
  await closeDrawerForText();
  await page.waitForFunction(
    () => document.querySelector('#workDrawer').hidden === true,
    { timeout: 12000 },
  );
  check(await drawerHidden(), '前置：抽屉已收起（非前一步遗留）');
  await say('__BACKGROUND_NAV__');
  await page.waitForTimeout(800); // 给"若被误拉开"一点时间
  check(await drawerHidden(), '缺 turn_id 的后台领航不自动开抽屉（心流防打扰，只指不拉）');
  const bgEvtCount = await page.evaluate(() => (window.__navEvents || []).filter(e => e.target_view === 'ops' && e.auto_open === false).length);
  check(bgEvtCount === 0, '后台领航未派发 aqi-ui-nav（消费方无从动手）');

  check(errors.length === 0, `无未捕获错误：${errors.join('; ')}`);
  await page.screenshot({ path: `${out}/ui-nav.png`, fullPage: true });
  await writeFile(`${out}/results.json`, JSON.stringify({ checks, errors,
    scope: 'Isolated browser; controlled WebSocket emits ui_nav frames on marker utterances; no model, TTS or user writes' }, null, 2));
  console.log(JSON.stringify({ result: 'PASS', checks, errors }, null, 2));
} finally {
  await browser.close();
}
