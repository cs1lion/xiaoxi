import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const browser = await chromium.launch({headless:true});
const errors=[]; const checks=[];
const assert=(ok,label)=>{if(!ok) throw new Error(label); checks.push(label);};
await mkdir('eval/ui',{recursive:true});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',error=>errors.push(error.message));
 // The existing process may predate assistant routes; isolate UI from that backend version.
 await page.route('**/api/preferences/proactivity', route=>route.fulfill({json:{timezone:'Asia/Shanghai',reminder_enabled:false}}));
 for(const endpoint of ['tasks','reminders','notifications']) await page.route(`**/api/${endpoint}`,route=>route.fulfill({json:{items:[]}}));
 await page.route('**/api/threads', route=>route.fulfill({json:{items:[]}}));
 await page.route('**/api/threads/selection', route=>route.fulfill({json:{thread_id:null}}));
 // Controlled transport verifies UI without sending prompts to a paid model or changing user data.
 const sent=[];
 await page.routeWebSocket('**/ws', socket=>{
   socket.onMessage(message=>{if(typeof message==='string') { const value=JSON.parse(message); sent.push(value); if(value.type==='text') { socket.send(JSON.stringify({type:'asr',turn_id:value.turn_id,text:value.text})); socket.send(JSON.stringify({type:'thinking',turn_id:value.turn_id})); socket.send(JSON.stringify({type:'text_delta',turn_id:value.turn_id,delta:'那种心里终于轻了一点的感觉，真好。想聊聊是什么事吗？'})); socket.send(JSON.stringify({type:'done',turn_id:value.turn_id})); } }});
 });
 await page.goto(process.env.BASE_URL || 'http://127.0.0.1:8000/');
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
 await page.route('**/api/memories',route=>route.fulfill({json:{count:1,items:[{id:'fixture',value:'测试记忆',kind:'fact',source:'stated',key:'<b>literal</b>'}]}}));
 await page.locator('#memRefresh').click();
 await page.getByRole('button',{name:'编辑记忆：测试记忆'}).waitFor();
 await page.getByRole('button',{name:'编辑记忆：测试记忆'}).focus();
 assert(await page.getByRole('button',{name:'编辑记忆：测试记忆'}).evaluate(el=>document.activeElement===el),'Memory edit uses keyboard-focusable button');
 assert(await page.locator('.mem .meta b').count()===0,'Memory metadata rendered as text, not HTML');
 await page.route('**/api/memories/fixture',route=>route.fulfill({status:503,json:{}}));
 await page.getByRole('button',{name:'忘掉',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#memoryFeedback').textContent.includes('操作未完成'));
 assert(await page.locator('.mem').count()===1,'Failed deletion keeps memory visible');
 await page.route('**/api/memories',route=>route.fulfill({status:503,json:{}}));
 await page.locator('#memRefresh').click();
 await page.waitForFunction(()=>document.querySelector('#memoryFeedback').textContent.includes('记忆未刷新'));
 assert(await page.locator('.mem').count()===1,'Failed refresh preserves last loaded memories');
 assert(errors.length===0,`No page errors: ${errors.join('; ')}`);
 await writeFile('eval/ui/results.json',JSON.stringify({checks,errors,transport:'Mock WebSocket and assistant preference/list APIs; other existing read-only APIs; no model request',screenshots:['desktop.png','conversation.png','mobile.png','mobile-conversation.png','mobile-preferences.png']},null,2));
 console.log(JSON.stringify({checks,errors},null,2));
} finally { await browser.close(); }
