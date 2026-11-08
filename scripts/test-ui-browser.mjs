import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { existsSync, rmSync } from 'node:fs';
import path from 'node:path';

/* 自起独立沙盘服务，验的一定是本 worktree 的 web/：
 * 默认 8022（UI_PORT 可覆盖），拒绝 8000/8010；DB_PATH + TEST_SENTINEL
 * 把记忆/线程/台账落到 tmp/ui-browser-<stamp>/，不碰用户正在用的 data/。 */
const root = path.resolve(import.meta.dirname, '..');
const PORT = Number(process.env.UI_PORT || 8022);
if (PORT === 8000 || PORT === 8010) { console.error(`端口 ${PORT} 是用户在用的，拒绝`); process.exit(2); }
const BASE = `http://127.0.0.1:${PORT}`;
const localVenv = path.join(root, '.venv', 'Scripts', 'python.exe');
const PY = process.env.UI_PYTHON
  || (existsSync(localVenv) ? localVenv : 'D:\\project\\neurosama\\.venv\\Scripts\\python.exe');
const stamp = Date.now().toString(36);
const SANDBOX = path.join(root, 'tmp', `ui-browser-${stamp}`);
const server = spawn(PY, ['-m', 'uvicorn', 'server.app:app',
  '--host', '127.0.0.1', '--port', String(PORT), '--log-level', 'warning'],
  { cwd: root, env: {
    ...process.env,
    DB_PATH: path.join(SANDBOX, 'memory.db'),
    TEST_SENTINEL: '1',
  }, stdio: ['ignore', 'pipe', 'pipe'] });
let serverErr = '';
server.stderr.on('data', chunk => { serverErr += String(chunk); });
async function waitHealth(deadlineMs = 45000) {
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${BASE}/api/health`);
      if (response.ok) return true;
    } catch { /* not up yet */ }
    await new Promise(resolve => setTimeout(resolve, 400));
  }
  return false;
}
const killTree = () => new Promise(resolve => {
  if (server.exitCode !== null) { resolve(); return; }
  spawn('taskkill', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' })
    .on('exit', resolve).on('error', resolve);
});

const errors=[]; const checks=[];
const assert=(ok,label)=>{if(!ok) throw new Error(label); checks.push(label);};
await mkdir('eval/ui',{recursive:true});
let browser;
try {
 if (!(await waitHealth())) throw new Error(`独立沙盘服务未就绪（${BASE}）：${serverErr.slice(-400)}`);
 // 证明确实打的是本 worktree 的代码，而不是 8000 上的旧前端。
 const [servedApp, servedPanel] = await Promise.all([
   fetch(`${BASE}/static/app.js`).then(r => r.text()),
   fetch(`${BASE}/static/memory-panel.js`).then(r => r.text()),
 ]);
 assert(servedApp.includes('confirmBeforeDelete') && servedPanel.includes('删除后不可恢复'),
   'Sandbox serves this worktree JS (hard-delete confirmation present)');
 console.log(`sandbox ${BASE} (DB_PATH=${path.join(SANDBOX, 'memory.db')})`);
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>errors.push(error.message));
 // 沙盘服务来自当前代码；偏好/任务列表仍用固定夹具，避免依赖运行环境状态。
 await page.route('**/api/preferences/proactivity', route=>route.fulfill({json:{timezone:'Asia/Shanghai',reminder_enabled:false}}));
 for(const endpoint of ['tasks','reminders','notifications']) await page.route(`**/api/${endpoint}`,route=>route.fulfill({json:{items:[]}}));
 await page.route('**/api/threads', route=>route.fulfill({json:{items:[]}}));
 await page.route('**/api/threads/selection', route=>route.fulfill({json:{thread_id:null}}));
 // Controlled transport verifies UI without sending prompts to a paid model or changing user data.
 const sent=[];
 await page.routeWebSocket('**/ws', socket=>{
   socket.onMessage(message=>{if(typeof message==='string') { const value=JSON.parse(message); sent.push(value); if(value.type==='text') { socket.send(JSON.stringify({type:'asr',turn_id:value.turn_id,text:value.text})); socket.send(JSON.stringify({type:'thinking',turn_id:value.turn_id})); socket.send(JSON.stringify({type:'text_delta',turn_id:value.turn_id,delta:'那种心里终于轻了一点的感觉，真好。想聊聊是什么事吗？'})); socket.send(JSON.stringify({type:'done',turn_id:value.turn_id})); } }});
 });
 // 聊天页在 /chat：`/` 从 2026-10-24 起是像素世界（world_entry_response）。
 await page.goto(`${BASE}/chat`);
 // The workspace deliberately hides the task tab at boot; attachment proves
 // bootstrap, while later task checks explicitly open its tab.
 await page.locator('.assistant-panel:not([data-interaction-preferences])').waitFor({state:'attached'});
 await page.screenshot({path:'eval/ui/desktop.png',fullPage:true});
 assert(await page.locator('#welcome').isVisible(),'Opening guidance visible');
 await page.locator('[data-prompt]').first().click();
 assert((await page.inputValue('#textInput')).includes('小事'),'Suggestion prepares editable draft');
 assert(!sent.some(x=>x.type==='text'),'Suggestion never auto-sends');
 assert((await page.locator('#draftFeedback').textContent()).includes('按发送后'),'Draft feedback makes send boundary explicit');
 assert(await page.locator('.draft-picked').count()===1,'Picked suggestion has one visual confirmation');
 await page.locator('#textInput').press('End');
 await page.locator('#textInput').press('Space');
 assert(await page.locator('.draft-picked').count()===0,'Editing draft clears stale suggestion confirmation');
 await page.locator('#textInput').dispatchEvent('keydown',{key:'Enter',isComposing:true});
 assert(!sent.some(x=>x.type==='text'),'IME confirmation never sends');
 await page.locator('#textInput').press('Enter');
 assert(sent.some(x=>x.type==='text'),'Enter sends explicit draft');
 // 去文字框化（协议 §23）：小溪的口头台词住进头顶微光气泡，不再产生 bot 文字气泡
 await page.locator('.glimmer-bubble').waitFor();
 assert((await page.locator('.glimmer-bubble').textContent()).includes('心里终于'),'Streamed reply lands in the head glimmer bubble');
 assert(await page.locator('.msg.bot').count()===0,'Voice-First streamed reply produces no bot text bubble');
 await page.locator('#welcome').waitFor({state:'hidden'});
 assert(await page.locator('.stage').evaluate(el=>el.classList.contains('has-messages')),'Welcome yields to conversation');
 await page.screenshot({path:'eval/ui/conversation.png',fullPage:true});
 await page.evaluate(()=>{
   const chat=document.querySelector('#chat');
   for(let i=0;i<40;i++){const message=document.createElement('div');message.className='msg bot';message.textContent='长内容布局检查。'.repeat(30);chat.append(message);}
 });
 await page.waitForFunction(()=>{const c=document.querySelector('#chat');return c.scrollHeight-c.clientHeight-c.scrollTop<64;});
 await page.locator('#chat').evaluate(c=>c.scrollTo({top:100,behavior:'instant'}));
 await page.locator('#latestMessage').waitFor({state:'visible'});
 const oldTop=await page.locator('#chat').evaluate(c=>c.scrollTop);
 await page.locator('#chat').evaluate(c=>{const m=document.createElement('div');m.className='msg bot';m.textContent='新的消息，不打断阅读。';c.append(m);});
 await page.evaluate(()=>new Promise(requestAnimationFrame));
 assert(Math.abs(await page.locator('#chat').evaluate(c=>c.scrollTop)-oldTop)<2,'New message preserves history reading position');
 await page.locator('#latestMessage').click();
 await page.waitForFunction(()=>{const c=document.querySelector('#chat');return c.scrollHeight-c.clientHeight-c.scrollTop<64;});
 assert(await page.locator('#latestMessage').isHidden(),'Return to latest restores follow mode');
 await page.locator('#subtitle').evaluate(el=>el.textContent='长字幕不会挤走输入框。'.repeat(300));
 for(const width of [390,320,768]) {
   await page.setViewportSize({width,height:844});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`No horizontal overflow at ${width}px`);
   assert(await page.locator('#textInput').evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}),`Composer remains on screen with long history at ${width}px`);
   assert(await page.locator('#chat').evaluate(el=>el.scrollHeight>el.clientHeight),`Long history scrolls inside chat at ${width}px`);
   assert(await page.locator('#subtitle').evaluate(el=>el.getBoundingClientRect().height<=64),`Long subtitle stays bounded at ${width}px`);
   if(width<=760) assert(await page.locator('#textInput').evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=16),`Mobile input font avoids small-field zoom trigger at ${width}px`);
 }
 await page.setViewportSize({width:390,height:844});
 await page.locator('#sidebarToggle').click();
 assert(await page.locator('#sidePanel').isVisible(),'Mobile preferences open');
 await page.keyboard.press('Escape');
 assert(!await page.locator('#sidePanel').isVisible(),'Escape closes mobile preferences');
 assert(await page.locator('#sidebarToggle').evaluate(el=>document.activeElement===el),'Escape restores trigger focus');
 await page.screenshot({path:'eval/ui/mobile-conversation.png',fullPage:true});
 await page.reload();
 await page.locator('#welcome').waitFor();
 await page.screenshot({path:'eval/ui/mobile.png',fullPage:true});
 await page.emulateMedia({reducedMotion:'reduce'});
 assert(await page.locator('#welcome').evaluate(el=>getComputedStyle(el).animationName)==='none','Reduced motion removes entrance animation');
 await page.locator('#sidebarToggle').click();
 await page.locator('#presenceCapsule').focus();
 assert(await page.locator('#presenceCapsule').evaluate(el=>document.activeElement===el),'Presence capsule is keyboard focusable');
 await page.locator('#work-tasks-tab').click();
 await page.locator('.assistant-panel:not([data-interaction-preferences]) summary').click();
 await page.screenshot({path:'eval/ui/mobile-preferences.png',fullPage:true});
 await page.keyboard.press('Escape');
 await page.locator('.assistant-section[aria-label="行动确认与结果"]').waitFor({state:'attached'});
 for (const iteration of [1,2]) {
   await page.evaluate(async iteration => {
     const {showAssistantResult}=await import('/static/assistant.js');
     showAssistantResult({run_id:`fixture-${iteration}`,text:'请确认本次操作',confirmation:{id:'fixture',tool:'test_only',arguments:{note:'这是测试，不会执行'}}});
   }, iteration);
   await page.waitForFunction(()=>document.activeElement?.classList.contains('assistant-card'));
   assert(await page.locator('#sidePanel').isVisible(),`Result ${iteration} opens mobile sidebar even with details already open`);
   assert(await page.locator('.assistant-card').first().evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=68 && r.bottom<=innerHeight;}),`Confirmation ${iteration} scrolls into view`);
   await page.keyboard.press('Escape');
 }
 await page.route('**/api/preferences/proactivity',route=>route.fulfill({status:404,json:{detail:'Not Found'}}));
 await page.reload();
 await page.locator('#sidebarToggle').click();
 await page.locator('#work-tasks-tab').click();
 await page.getByRole('button',{name:'重新连接任务服务'}).waitFor();
 assert((await page.locator('.assistant-panel:not([data-interaction-preferences]) .assistant-error').textContent()).includes('聊天仍可继续'),'Unavailable service explains recovery without blocking chat');
 await page.getByRole('button',{name:'重新连接任务服务'}).click();
 await page.getByRole('button',{name:'重新连接任务服务'}).waitFor();
 assert((await page.locator('.assistant-panel:not([data-interaction-preferences]) .assistant-error').textContent()).includes('任务服务暂不可用'),'Failed retry preserves helpful error');
 await page.route('**/api/preferences/proactivity',route=>route.fulfill({json:{timezone:'Asia/Shanghai',reminder_enabled:false}}));
 await page.getByRole('button',{name:'重新连接任务服务'}).click();
 await page.locator('.assistant-section[aria-label="行动确认与结果"]').waitFor({state:'attached'});
 assert(await page.locator('.assistant-panel:not([data-interaction-preferences])').count()===1,'Retry recovers one assistant panel without duplicates');
 await page.route('**/api/memory/manual',route=>route.fulfill({status:503,json:{detail:'Unavailable'}}));
 await page.locator('#manualInput').fill('测试草稿，不写入真实记忆');
 await page.locator('#manualAdd').click();
 await page.waitForFunction(()=>document.querySelector('#memoryFeedback').textContent.includes('没有记下'));
 assert((await page.inputValue('#manualInput')).includes('测试草稿'),'Failed memory save retains draft');
 await page.route('**/api/memory/toggle',route=>route.fulfill({status:503,json:{detail:'Unavailable'}}));
 // 三态胶囊：灵动伴随 →（点一次）静默旁观 →（再点一次）无痕闲聊；后者要关记忆，REST 503 保存失败。
 await page.keyboard.press('Escape');   // 先收起移动侧栏，胶囊在主舞台才可点
 await page.locator('#presenceCapsule').click();
 await page.locator('#presenceCapsule').click();
 await page.waitForFunction(()=>document.querySelector('#memoryFeedback').textContent.includes('设置未保存'));
 assert((await page.locator('#presenceCapsule .presence-label').innerText())==='静默旁观','Failed preference save rolls the capsule back');
 assert(await page.evaluate(()=>document.getElementById('memoryToggle').checked)===true,'Failed save rolls the memory bit back');
 await page.locator('#sidebarToggle').click();   // 后续记忆列表断言需要侧栏展开
 // status:'active' 是 0f87efa（valid-time）之后的硬要求：memoryForgetControl 对
 // 非 active 行返回 null（防整链误删），fixture 不给 status 就没有「忘掉」按钮，
 // :133 永远超时——ui_browser 门禁红的真身是 fixture 过期，不是环境问题。
 await page.route('**/api/memories',route=>route.fulfill({json:{count:1,items:[{id:'fixture',value:'测试记忆',kind:'fact',source:'stated',status:'active',key:'<b>literal</b>'}]}}));
 await page.locator('#memRefresh').click();
 await page.getByRole('button',{name:'编辑记忆：测试记忆'}).waitFor();
 await page.getByRole('button',{name:'编辑记忆：测试记忆'}).focus();
 assert(await page.getByRole('button',{name:'编辑记忆：测试记忆'}).evaluate(el=>document.activeElement===el),'Memory edit uses keyboard-focusable button');
 assert(await page.locator('.mem .meta b').count()===0,'Memory metadata rendered as text, not HTML');
 await page.route('**/api/memories/fixture',route=>route.fulfill({status:503,json:{}}));
 // 无障碍名来自 app.js:1567 的 aria-label（`${label}记忆：${value}`），不是按钮
 // 可见文字。0f87efa 同时引入了这条 aria-label 与「非 active 不渲染忘掉」的闸门，
 // 门禁自那天起红在两处：fixture 缺 status，选择器名也过期。与 :131 的编辑记忆同一写法。
 await page.getByRole('button',{name:'忘掉记忆：测试记忆'}).click();
 // 物理硬删除要先展开确认条：第一下不发请求，第二下才 DELETE（此处 503）。
 await page.getByRole('button',{name:'确认删除'}).waitFor();
 assert(await page.locator('.mem').count()===1,'First click keeps memory until deletion is confirmed');
 await page.getByRole('button',{name:'确认删除'}).click();
 await page.waitForFunction(()=>document.querySelector('#memoryFeedback').textContent.includes('操作未完成'));
 assert(await page.locator('.mem').count()===1,'Failed deletion keeps memory visible');
 await page.route('**/api/memories',route=>route.fulfill({status:503,json:{}}));
 await page.locator('#memRefresh').click();
 await page.waitForFunction(()=>document.querySelector('#memoryFeedback').textContent.includes('记忆未刷新'));
 assert(await page.locator('.mem').count()===1,'Failed refresh preserves last loaded memories');
 // 视觉状态类必须真的有样式消费；只断言 classList 会让"类加了、CSS 没有"恒绿。
 const visual = await page.evaluate(() => {
   const card = document.createElement('article'); card.className = 'ops-card';
   const highlighted = document.createElement('article'); highlighted.className = 'ops-card is-highlighted';
   const nav = document.createElement('article'); nav.className = 'ops-card nav-spotlight';
   const bot = document.createElement('div'); bot.className = 'msg bot';
   const discussion = document.createElement('div'); discussion.className = 'msg bot editor-discussion-message';
   document.body.append(card, highlighted, nav, bot, discussion);
   const style = (el) => getComputedStyle(el);
   return {
     highlightBorder: style(highlighted).borderColor,
     baseBorder: style(card).borderColor,
     highlightShadow: style(highlighted).boxShadow,
     navAnimation: style(nav).animationName,
     navOutline: style(nav).outlineStyle,
     discussionBg: style(discussion).backgroundColor,
     botBg: style(bot).backgroundColor,
     discussionBorder: style(discussion).borderLeftColor,
     botBorder: style(bot).borderLeftColor,
   };
 });
 assert(visual.highlightShadow !== 'none' || visual.highlightBorder !== visual.baseBorder,
   'is-highlighted has a visible style');
 assert(visual.navAnimation !== 'none' || visual.navOutline !== 'none',
   'nav-spotlight has a visible style');
 assert(visual.discussionBg !== visual.botBg || visual.discussionBorder !== visual.botBorder,
   'editor-discussion-message differs from a normal bot bubble');
 assert(errors.length===0,`No page errors: ${errors.join('; ')}`);
 await writeFile('eval/ui/results.json',JSON.stringify({checks,errors,sandbox:BASE,transport:'Self-hosted sandbox server from this worktree (DB_PATH in tmp/); Mock WebSocket and assistant preference/list APIs; no model request; does not touch 8000/8010',screenshots:['desktop.png','conversation.png','mobile.png','mobile-conversation.png','mobile-preferences.png']},null,2));
 console.log(JSON.stringify({checks,errors},null,2));
} finally {
 await browser?.close();
 await killTree();
 rmSync(SANDBOX,{recursive:true,force:true});
}
