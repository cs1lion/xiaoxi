// Real homepage modules with intercepted assets/APIs and fake WebSocket/audio.
// No application server, user DB, model, microphone, or provider calls.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:1200,height:900}});
const checks = [], errors = [], requests = [], unexpected = [];
let editorLoads = 0;
page.setDefaultTimeout(8000);
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url());
  if (url.origin !== 'http://context-usage.test') { unexpected.push(url.href); return route.abort(); }
  if (url.pathname === '/static/avatar/index.js') return route.fulfill({contentType:'text/javascript',body:`
    const noop=()=>{}; export const avatar=new Proxy({emotion:{set:noop},rendererKind:'svg'}, {get:(o,k)=>o[k]||noop}); export const boot=async()=>{};`});
  if (url.pathname.startsWith('/api/')) {
    requests.push({path:url.pathname,method:request.method()});
    const data = url.pathname === '/api/persona' ? {name:'离线测试',tagline:'本轮参考',voices:['base'],default_voice:'base'}
      : url.pathname === '/api/preferences/proactivity' ? {timezone:'Asia/Shanghai',reminder_enabled:false}
      : url.pathname === '/api/interaction/preferences' ? {items:[{id:'pref-1',dimension:'detail',value:'short',status:'active',scope:{},source:'manual',evidence_ids:[]}]}
      : url.pathname === '/api/memories' ? {count:1,items:[{id:5,kind:'fact',subject:'user',value:'合成阅读习惯',key:'reading',source:'stated',status:'active',evidence_state:'linked',evidence_quote:'合成原话，不属于 trace'}]}
      : url.pathname === '/api/tasks' ? {items:[{id:'task-1',title:'合成任务',status:'open',version:1}]}
      : url.pathname === '/api/threads/selection' ? {thread_id:null}
      : url.pathname === '/api/notes/selection' ? {revision:1,note_id:null,selected_version:null}
      : {count:0,items:[]};
    return route.fulfill({json:data});
  }
  if (url.pathname === '/static/notes.html') editorLoads++;
  const absolute = path.resolve(root, url.pathname === '/' ? 'web/index.html' : `web/${url.pathname.replace(/^\/static\//,'')}`);
  if (!absolute.startsWith(root + path.sep)) return route.abort();
  try { return route.fulfill({body:await readFile(absolute),contentType:/\.(m?js)$/.test(absolute)?'text/javascript':absolute.endsWith('.css')?'text/css':'text/html'}); }
  catch { unexpected.push(url.pathname); return route.fulfill({status:404,body:'fixture not found'}); }
});
await page.addInitScript(() => {
  const h = window.contextHarness = {sent:[]};
  class WS {
    static OPEN=1; static CLOSED=3; static CONNECTING=0;
    constructor(){this.readyState=0;h.ws=this;setTimeout(()=>{this.readyState=1;this.onopen?.({});this.emit({type:'ready',session_id:'synthetic'});},0);}
    send(data){h.sent.push(JSON.parse(data));} emit(data){return this.onmessage?.({data:JSON.stringify(data)});} close(){this.readyState=3;this.onclose?.({});}
  }
  window.WebSocket=WS;
  const node=()=>({connect(){return this;},disconnect(){},gain:{value:1}});
  window.AudioContext=class {constructor(){this.sampleRate=48000;this.currentTime=0;this.state='running';this.destination=node();}resume(){return Promise.resolve();}close(){return Promise.resolve();}createGain(){return node();}createAnalyser(){return {...node(),fftSize:1024,getFloatTimeDomainData(a){a.fill(0);}};}};
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia(){throw Error('No microphone in context fixture');}}});
});
const emit = message => page.evaluate(value => contextHarness.ws.emit(value), message);
const usage = {
  schema_version:1,enabled:true,budget:{scope:'dynamic_context',unit:'characters',max_chars:12000,used_chars:9876,omitted_items:4},
  items:[{kind:'memory',id:5,reason:'query_retrieval',source:'stated',evidence_state:'linked'},
    {kind:'preference',id:'pref-1',reason:'scene_scope'},
    {kind:'thread',id:'matter <&>',version:4,reason:'selected_matter'},
    {kind:'thread_event',id:'event-1',thread_id:'matter <&>',version:2,reason:'matter_recall'},
    {kind:'note',id:'note <&>',version:1,purpose:'draft',reason:'explicit_note_request',body_included:true},
    {kind:'note',id:'note-2',version:2,purpose:'reference',reason:'metadata_only',body_included:false},
    {kind:'task',id:'task-1',reason:'active_task'}],
};
async function textTurn(text) {
  await page.locator('#textInput').fill(text); await page.locator('#sendBtn').click();
  return page.evaluate(() => contextHarness.sent.filter(item=>item.type==='text').at(-1).turn_id);
}
async function showResult(result) { return page.evaluate(async value => (await import('/static/assistant.js')).showAssistantResult(value), result); }
try {
  await page.goto('http://context-usage.test/');
  await page.locator('[data-memory-id="5"]').waitFor();
  await page.locator('[data-claim-id="pref-1"]').waitFor({state:'attached'});
  const editor = page.frameLocator('iframe[title="个人笔记编辑器"]');
  await page.locator('#work-notes-tab').click();
  await editor.locator('#body').fill('独立编辑器里尚未保存的草稿');
  await page.locator('#work-now-tab').click();
  const originalLoads = editorLoads;
  const turn = await textTurn('如何接续阅读计划');
  await emit({type:'thinking',turn_id:turn}); await emit({type:'text_delta',turn_id:turn,delta:'继续阅读。'});
  assert.equal(await page.locator('.context-usage').count(),0);
  await page.locator('#textInput').fill('下一条未发送草稿');
  await page.locator('#textInput').focus();
  await emit({type:'done',turn_id:turn,text:'继续阅读。',context_usage:usage});
  const trace = page.locator('.msg.bot .context-usage').first();
  await expect(trace).toBeAttached(); assert.equal(await trace.getAttribute('open'),null);
  await expect(page.locator('#textInput')).toBeFocused();
  await expect(page.locator('#textInput')).toHaveValue('下一条未发送草稿');
  await trace.locator('summary').click();
  await expect(trace).toContainText('不代表模型实际引用或推理理由');
  await expect(trace).toContainText('9876 / 12000 字符；因预算省略 4 项');
  await expect(trace).toContainText('只提供索引，未提供正文');
  await expect(trace).toContainText('按问题与近期记录规则提供');
  assert.equal(await trace.locator('li').count(),7);
  assert.equal(await trace.getByRole('link',{name:'打开保存版本'}).first().getAttribute('href'),'/static/notes.html?note_id=note+%3C%26%3E&version=1');
  assert.equal(await trace.getByRole('link',{name:'查看所属事项'}).getAttribute('href'),'/static/threads.html?thread_id=matter+%3C%26%3E');
  assert.doesNotMatch(await trace.innerText(),/合成原话|独立编辑器|下一条未发送/);
  checks.push('普通聊天仅在最终结果附加折叠资料索引，保留焦点、聊天草稿和固定版本链接');
  const beforeManage = requests.length;
  await trace.locator('li').filter({hasText:'长期记忆 5'}).getByRole('button',{name:'查看管理'}).click();
  await expect(page.locator('[data-memory-id="5"]')).toBeFocused();
  await trace.locator('li').filter({hasText:'相处偏好 pref-1'}).getByRole('button',{name:'查看管理'}).click();
  await expect(page.locator('[data-claim-id="pref-1"]')).toBeFocused();
  await expect(page.locator('[data-interaction-preferences]')).toHaveAttribute('open','');
  await trace.locator('li').filter({hasText:'待办 task-1'}).getByRole('button',{name:'查看管理'}).click();
  await expect(page.locator('[data-task-id="task-1"]')).toBeFocused();
  assert.equal(requests.length,beforeManage);
  checks.push('查看管理只定位已有记忆与偏好列表，不读取正文、不改写记录');
  await page.evaluate(()=>{document.querySelector('#memoryToggle').checked=false;});
  await trace.locator('li').filter({hasText:'长期记忆 5'}).getByRole('button',{name:'查看管理'}).click();
  await expect(page.locator('#memoryFeedback')).toContainText('当前已关闭记忆');
  assert.equal(requests.length,beforeManage);
  await page.evaluate(()=>{document.querySelector('#memoryToggle').checked=true;});
  checks.push('关闭记忆后旧详情管理入口不重新读取或刷新私有记录');
  const disabledTurn = await textTurn('普通聊天'); await emit({type:'thinking',turn_id:disabledTurn});
  await emit({type:'done',turn_id:disabledTurn,text:'你好。',context_usage:{schema_version:1,enabled:false,items:[{kind:'note',id:'PRIVATE_TRACE_SENTINEL',version:1}]}});
  const disabled = page.locator('.msg.bot .context-usage').last(); await disabled.locator('summary').click();
  await expect(disabled).toContainText('本轮已关闭记忆'); assert.doesNotMatch(await disabled.innerText(),/PRIVATE_TRACE_SENTINEL/);
  assert.equal(await disabled.locator('li,a,button').count(),0);
  checks.push('禁用资料详情只显示关闭状态，即使错误payload带items也不展示');
  const qualifiedUsage = {...usage,items_truncated:true,omitted_usage_items:9,
    qualifications:{upstream_note_body_truncated:true,upstream_material_bodies_truncated:2,thread_omitted_event_count:3,thread_omitted_history_event_count:1,thread_omitted_history_version_count:5,omitted_materials:2},
    items:[{kind:'note',id:'material-version',version:2,purpose:'reference',reason:'explicit_material_request',body_included:true},{kind:'memory',id:7,source:'uncertain',reason:'query_retrieval'}]};
  await showResult({status:'completed',text:'正文截取和参考清单封顶说明',receipts:[],context_usage:qualifiedUsage});
  const qualifiedTrace = page.locator('.assistant-card .context-usage').first();
  await qualifiedTrace.locator('summary').click();
  await expect(qualifiedTrace).toContainText('还有 9 项未展示');
  await expect(qualifiedTrace).toContainText('未展示不表示资料未提供给模型');
  await expect(qualifiedTrace).toContainText('当前稿正文已截取；片段可能缺少条件或否定');
  await expect(qualifiedTrace).toContainText('2 份资料正文已截取');
  await expect(qualifiedTrace).toContainText('资料视图省略合计（可能含上面的预算省略，不另累加）：事项记录 3 条；事项历史记录 1 份；事项历史版本 5 版；事项资料 2 项');
  await expect(qualifiedTrace).toContainText('9876 / 12000 字符；因预算省略 4 项');
  await expect(qualifiedTrace).toContainText('明确请求参考本事项资料');
  await expect(qualifiedTrace).toContainText('待核对记录');
  assert.equal(await qualifiedTrace.locator('li').count(),2);
  checks.push('后端已封顶清单仍披露未展示数，正文截取提示缺条件风险，累计资料视图省略含历史版本数且不另累加');
  await showResult({status:'completed',text:'合成助手结果',receipts:[],context_usage:usage});
  await expect(page.locator('.assistant-card .context-usage').first()).toBeAttached();
  const missing = {...usage,status:'missing',items:[]};
  await showResult({status:'completed',text:'合成接续结果',companion:{},context_usage:missing});
  await expect(page.locator('.companion-result .context-usage')).toContainText('关联资料已缺失');
  const proposal = {id:'p-synthetic',version:1,note_id:'n-fixed',base_note_version:1,original_title:'原稿',original_body:'合成原稿',title:'候选',body:'合成候选',source_text:'合成请求',expires_at:Date.now()/1000+3600,status:'pending'};
  await showResult({status:'awaiting_confirmation',text:'合成候选',note_proposal:proposal,context_usage:usage});
  const candidate = page.locator('[data-note-proposal-id="p-synthetic"]');
  await candidate.locator('textarea').fill('我修改的未保存候选');
  const candidateTrace = candidate.locator('.context-usage'); await candidateTrace.locator('summary').click();
  await candidate.locator('textarea').focus();
  await showResult({status:'awaiting_confirmation',text:'重复到达',note_proposal:proposal,context_usage:usage});
  assert.equal(await candidate.locator('.context-usage').count(),1);
  await expect(candidateTrace).toHaveAttribute('open',''); await expect(candidate.locator('textarea')).toHaveValue('我修改的未保存候选'); await expect(candidate.locator('textarea')).toBeFocused();
  const threadProposal = {id:'thread-proposal-1',thread_id:'original-thread',operation:'add_event',kind:'decision',text:'合成决定',source_text:'合成记录请求',version:1,expected_revision:1,expires_at:Date.now()/1000+3600,object_refs:[]};
  await showResult({status:'awaiting_confirmation',text:'合成事项候选',proposal:threadProposal,context_usage:usage});
  await expect(page.locator('[data-proposal-id="thread-proposal-1"] .context-usage')).toBeAttached();
  checks.push('通用助手、接续、事项与笔记候选接入同一详情；重复结果保持候选草稿、焦点与展开状态');
  await page.evaluate(async () => {
    const {appendContextUsage}=await import('/static/context-usage.js');
    const holder=document.createElement('article');holder.id='malformedTrace';holder.className='msg bot';document.querySelector('#chat').append(holder);
    appendContextUsage(holder,{schema_version:1,enabled:true,items:[{kind:'note',id:'<img src=x onerror=alert(1)>',version:0,reason:'<script>unsafe</script>'},{kind:'unknown',id:'ignored'}]});
    const overflow=document.createElement('article');overflow.id='boundedTrace';overflow.className='msg bot';document.querySelector('#chat').append(overflow);
    appendContextUsage(overflow,{schema_version:1,enabled:true,items:Array.from({length:80},(_,i)=>({kind:'memory',id:i+1}))});
  });
  const hostile = page.locator('#malformedTrace'); await hostile.locator('summary').click();
  assert.equal(await hostile.locator('img,script,a').count(),0); assert.equal(await hostile.locator('li').count(),1);
  assert.equal(await page.locator('#boundedTrace li').count(),32);
  checks.push('陌生类型和无效保存版本不生成链接，恶意标记作为文字，展示最多32项');
  await page.setViewportSize({width:390,height:844});
  await page.locator('#malformedTrace').scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(editorLoads,originalLoads); await expect(editor.locator('#body')).toHaveValue('独立编辑器里尚未保存的草稿');
  assert.deepEqual(errors,[]); assert.deepEqual(unexpected,[]); assert.ok(requests.every(request=>request.method==='GET'));
  checks.push('390px没有横向溢出；独立编辑器没有重载；全流程无API写入、运行错误或外部请求');
  await mkdir(path.join(root,'eval/ui/context-usage'),{recursive:true});
  await page.screenshot({path:path.join(root,'eval/ui/context-usage/mobile.png')});
  await trace.screenshot({path:path.join(root,'eval/ui/context-usage/detail-mobile.png')});
  await writeFile(path.join(root,'eval/ui/context-usage/results.json'),JSON.stringify({result:'PASS',scope:'Offline synthetic APIs and WebSocket; actual homepage modules; no model/audio/mic/production writes',checks,requests,errors,unexpected},null,2));
  console.log(JSON.stringify({result:'PASS',checks},null,2));
} finally {await browser.close();}
