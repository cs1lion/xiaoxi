/* 打磨轮 U-1~U-6 的界面契约回归：胶囊输入栏、模式状态条、工作台抽屉、设置面板。
 * 隔离夹具：受控 WebSocket + 只读 API 存根；不请求模型、不写用户数据、不碰麦克风权限之外的真实设备。
 * 截图写入 eval/ui/polish/（桌面 + 390px 各三张：纯净舞台、设置面板、抽屉导航）。 */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const browser = await chromium.launch({ headless: true,
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
const checks = [];
const check = (ok, label) => { assert.ok(ok, label); checks.push(label); };
const out = 'eval/ui/polish';
try {
  await mkdir(out, { recursive: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/api/preferences/proactivity', route => route.fulfill({ json: { timezone: 'Asia/Shanghai', reminder_enabled: false } }));
  for (const endpoint of ['tasks', 'reminders', 'notifications']) {
    await page.route(`**/api/${endpoint}`, route => route.fulfill({ json: { items: [] } }));
  }
  await page.route('**/api/threads', route => route.fulfill({ json: { items: [] } }));
  await page.route('**/api/threads/selection', route => route.fulfill({ json: { thread_id: null } }));
  await page.route('**/api/music/capabilities', route => route.fulfill({ json: { configured: false } }));
  const sent = [];
  await page.routeWebSocket('**/ws', socket => {
    socket.onMessage(message => {
      if (typeof message !== 'string') return;
      const value = JSON.parse(message);
      sent.push(value);
      if (value.type === 'text') {
        socket.send(JSON.stringify({ type: 'asr', turn_id: value.turn_id, text: value.text }));
        socket.send(JSON.stringify({ type: 'thinking', turn_id: value.turn_id }));
        socket.send(JSON.stringify({ type: 'text_delta', turn_id: value.turn_id, delta: '好，听你的。' }));
        socket.send(JSON.stringify({ type: 'done', turn_id: value.turn_id, text: '好，听你的。' }));
      }
    });
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.waitForFunction(() => document.querySelector('#status')?.textContent === '在线');

  /* ---- U-1 胶囊输入栏：三个核心元素，次级按钮迁位不丢失 ---- */
  check(await page.locator('.capsule #micBtn').count() === 1, '胶囊保留语音通话/麦克风');
  check(await page.locator('.capsule #textInput').count() === 1, '胶囊保留文本输入');
  check(await page.locator('.capsule #drawerToggle').count() === 1, '胶囊保留工作台抽屉开关');
  check(await page.locator('.capsule .input-wrap #sendBtn').count() === 1, '发送收进输入框槽位，未新增独立按钮');
  check(await page.locator('#modeStrip #bargeToggle').count() === 1, '连续对话开关迁到模式状态条');
  check(await page.locator('#modeStrip #resumeSpeechBtn').count() === 1, '续播被打断句迁到模式状态条');
  check(await page.locator('.capsule #stopBtn').count() === 1, '停止回复迁到胶囊上下文位');
  const topbarIds = await page.locator('.topbar button, .topbar summary').evaluateAll(
    nodes => nodes.map(node => node.id).filter(Boolean));
  for (const gone of ['starmapToggle', 'bargeToggle', 'stopBtn', 'resumeSpeechBtn']) {
    check(!topbarIds.includes(gone), `顶栏不再平铺 ${gone}`);
  }
  check(topbarIds.includes('writingToggle') && topbarIds.includes('settingsToggle'), '顶栏只留记忆面板、一起写与设置入口');
  await page.screenshot({ path: `${out}/desktop-stage.png`, fullPage: true });

  /* ---- U-2 设置面板：开合、焦点归还、收纳清单 ---- */
  check(await page.locator('#settingsPanel').evaluate(node => node.open) === false, '设置面板默认收起');
  await page.locator('#settingsToggle').click();
  check(await page.locator('#settingsPanel').evaluate(node => node.open) === true, '右上角设置入口打开面板');
  for (const id of ['avatarRenderer', 'externalModelUrl', 'externalModelLoad', 'followToggle', 'faceToggle',
    'emotionSelect', 'duplexMode', 'voiceDiagnostics', 'resetVoiceMetrics', 'practiceMode', 'practiceLevel',
    'practiceCorrection', 'practicePause', 'practiceApply', 'practiceReview', 'practiceStatus',
    'contextBudgetStatus', 'voiceBaselineMount', 'dialogueReviewMount']) {
    check(await page.locator(`#settingsBody #${id}`).count() === 1, `设置面板收纳 ${id}`);
  }
  check(await page.locator('#voiceTasteFrame').getAttribute('src') === '/static/voice_taste.html', '语气试音台收编为设置内嵌面板');
  await page.locator('#settingsToggle').focus();
  await page.keyboard.press('Escape');
  check(await page.locator('#settingsPanel').evaluate(node => node.open) === false, 'Escape 关闭设置面板');
  check(await page.locator('#settingsToggle').evaluate(node => document.activeElement === node), '关闭后焦点归还设置入口');
  await page.locator('#settingsToggle').click();
  await page.screenshot({ path: `${out}/desktop-settings.png`, fullPage: true });

  /* ---- U-3 工作台抽屉：视图统一导航 ---- */
  check(await page.locator('#workDrawer').evaluate(node => node.hidden) === true, '抽屉默认收起');
  await page.locator('#drawerToggle').click();
  check(await page.locator('#workDrawer').isVisible(), '抽屉开关打开工作台');
  check(await page.locator('#drawerToggle').getAttribute('aria-expanded') === 'true', '抽屉开关状态同步');
  const navLabels = await page.locator('.drawer-nav button').allInnerTexts();
  // V3 追加「督办与战况」：铁律是主舞台 0 新增按钮，新入口只能落在抽屉里。
  assert.deepEqual(navLabels, ['一起写', '共同事项', '星图', '音乐', '实时字幕', '英语练习', '督办与战况'], '抽屉七视图导航齐全');
  checks.push('抽屉七视图导航齐全');
  check(topbarIds.every(id => id !== 'drawerOps') && !await page.locator('.topbar #opsToggle').count(), '督办与战况不占顶栏');
  check(await page.locator('#drawerViewMusic iframe').getAttribute('src') === null, '音乐视图按需加载，不预连接');
  await page.locator('#drawerMusic').click();
  check(await page.locator('#drawerViewMusic').isVisible() && await page.locator('#drawerViewMusic iframe').getAttribute('src') === '/static/music.html', '音乐内嵌视图加载既有页面');
  await page.locator('#drawerInterpret').click();
  check(await page.locator('#drawerViewInterpret').isVisible() && await page.locator('#drawerViewInterpret iframe').getAttribute('src') === '/static/interpret.html', '实时字幕内嵌视图加载既有页面');
  await page.locator('#drawerPractice').click();
  check(await page.locator('#workDrawer').evaluate(node => node.hidden) === true, '练习入口跳转后收起抽屉');
  check(await page.locator('#settingsPanel').evaluate(node => node.open) === true, '英语练习落在设置面板');
  check(await page.locator('#practiceSection').evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0;
  }), '练习分区被滚动到可见区域');
  await page.locator('#drawerToggle').click();
  await page.locator('#drawerWriting').click();
  check(await page.locator('body').evaluate(node => node.classList.contains('is-writing')), '一起写保持既有宽屏视图');
  check(await page.locator('#workDrawer').evaluate(node => node.hidden) === true, '进入一起写后收起抽屉');
  check(await page.locator('#stopBtn').isVisible(), '写作视图仍可停止回复');
  await page.getByRole('button', { name: '回到聊天', exact: true }).click();
  await page.locator('#drawerToggle').click();
  await page.locator('#starmapToggle').click();
  check(await page.locator('#starmapMount').evaluate(node => !node.hidden), '星图保持既有全屏视图');
  await page.locator('.starmap-close').click();
  await page.locator('#drawerToggle').click();
  await page.screenshot({ path: `${out}/desktop-drawer.png`, fullPage: true });

  /* ---- V3 督办与战况：抽屉视图、三句话验收卡、API 战况 ---- */
  await page.locator('#drawerOps').click();
  check(await page.locator('#drawerViewOps').isVisible(), '督办与战况视图可打开');
  await page.waitForFunction(() => document.querySelector('#opsCommissions')?.children.length > 0);
  check((await page.locator('#opsCommissions').allInnerTexts()).join('').length > 0, '督办清单有内容');
  check(await page.locator('#opsTelemetry').isVisible(), 'API 观测简报卡在位');
  check(await page.locator('#opsRubrics').isVisible(), '初审判例卡在位');
  check((await page.locator('#opsRubrics').allInnerTexts()).join('').includes('no_topbar_button'), '种子铁律可见');
  await page.screenshot({ path: `${out}/desktop-ops.png`, fullPage: true });
  await page.locator('#drawerClose').click();
  check(await page.locator('#workDrawer').evaluate(node => node.hidden) === true, '督办视图可收起');

  /* ---- U-4/U-5 练习入口与模式状态可视化 ---- */
  check(await page.locator('#bargeToggle').textContent() === '连续对话：关', '连续对话模式常驻可见');
  await page.locator('#bargeToggle').click();
  await page.waitForFunction(() => document.querySelector('#bargeToggle').getAttribute('aria-pressed') === 'true');
  check(await page.locator('#bargeToggle').textContent() === '连续对话：开', '连续对话可开启且状态可见');
  await page.locator('#bargeToggle').click();
  await page.waitForFunction(() => document.querySelector('#bargeToggle').getAttribute('aria-pressed') === 'false');
  await page.locator('#settingsToggle').click();
  await page.selectOption('#practiceMode', 'cafe');
  await page.locator('#practiceApply').click();
  check(await page.locator('#modePractice').isVisible() && (await page.locator('#modePractice').textContent()).includes('咖啡店'), '练习模式在状态条可见');
  await page.selectOption('#practiceMode', 'off');
  await page.locator('#practiceApply').click();
  check(await page.locator('#modePractice').isHidden(), '关闭练习后状态片消失');
  await page.keyboard.press('Escape');

  /* ---- U-6 移动端：胶囊不溢出、抽屉可开合、无横向滚动 ---- */
  await page.setViewportSize({ width: 390, height: 844 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '390px 无横向滚动');
  check(await page.locator('.capsule').evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.left >= 0 && rect.right <= window.innerWidth && rect.height <= 90;
  }), '390px 胶囊不溢出且保持单层高度');
  check(await page.locator('#textInput').evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= window.innerHeight && rect.width > 80;
  }), '390px 输入框仍在视口内且可用');
  await page.locator('#drawerToggle').click();
  check(await page.locator('#workDrawer').isVisible(), '390px 抽屉可打开');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '390px 抽屉打开后无横向滚动');
  await page.keyboard.press('Escape');
  check(await page.locator('#workDrawer').evaluate(node => node.hidden), 'Escape 收起抽屉并归还焦点');
  check(await page.locator('#drawerToggle').evaluate(node => document.activeElement === node), '抽屉关闭后焦点归还开关');
  await page.locator('#settingsToggle').click();
  check(await page.locator('#settingsBody').isVisible(), '390px 设置面板可打开');
  check(await page.locator('#settingsBody').evaluate(node => node.scrollHeight > node.clientHeight), '390px 设置面板内部滚动');
  await page.keyboard.press('Escape');
  await page.screenshot({ path: `${out}/mobile-stage.png`, fullPage: true });
  await page.locator('#drawerToggle').click();
  await page.screenshot({ path: `${out}/mobile-drawer.png`, fullPage: true });
  await page.keyboard.press('Escape');
  await page.locator('#settingsToggle').click();
  await page.screenshot({ path: `${out}/mobile-settings.png`, fullPage: true });

  check(errors.length === 0, `无未捕获错误：${errors.join('; ')}`);
  await writeFile(`${out}/results.json`, JSON.stringify({ checks, errors,
    scope: 'Isolated browser fixtures; controlled WebSocket and read-only API stubs; no model, audio or user writes' }, null, 2));
  console.log(JSON.stringify({ result: 'PASS', checks, errors }, null, 2));
} finally {
  await browser.close();
}
