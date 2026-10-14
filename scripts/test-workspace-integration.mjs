import {chromium} from '@playwright/test';
import {spawnSync} from 'node:child_process';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
const dir=await mkdtemp(path.join(os.tmpdir(),'aqi-workspace-'));
const call=data=>{const r=spawnSync(path.resolve('.venv/Scripts/python.exe'),['scripts/task_api_bridge.py',path.join(dir,'assistant.db')],{input:JSON.stringify(data),encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}});if(r.status!==0)throw Error(r.stderr);return JSON.parse(r.stdout);};
const checks=[];const assert=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();
 await page.routeWebSocket('**/ws',s=>s.onMessage(()=>{}));
 await page.route('**/api/**',async route=>{
  const req=route.request(),u=new URL(req.url());
  if(/^\/api\/(notes|tasks|reminders|notifications|preferences\/proactivity)(\/|$)/.test(u.pathname)){
   const data={method:req.method(),path:u.pathname+u.search};if(req.postData())data.body=req.postDataJSON();const r=call(data);return route.fulfill({status:r.status,json:r.body});
  }
  if(req.method()!=='GET')throw Error('Unexpected production mutation');return route.continue();
 });
 await page.goto('http://127.0.0.1:8000/');await page.locator('#avatarStage svg, #avatarStage canvas').waitFor();
 await page.locator('#work-notes-tab').click();const note=page.frameLocator('iframe[title="个人笔记编辑器"]');
 await note.locator('#title').fill('便笺整合测试');await note.locator('#body').fill('整理两个候选地点');
 await page.locator('#work-tasks-tab').click();await page.locator('#work-notes-tab').click();
 assert(await note.locator('#body').inputValue()==='整理两个候选地点','Unsaved note survives tab switch');
 await note.locator('#save').click();await note.locator('#status').filter({hasText:'笔记已保存。'}).waitFor();
 assert(call({method:'GET',path:'/api/notes'}).body.items.length===1,'Embedded editor persists to isolated real SQLite');
 await note.locator('#prepare').click();assert(call({method:'GET',path:'/api/tasks'}).body.items.length===0,'Preparing embedded draft does not create task');
 await note.locator('#taskTitle').fill('比较两个地点');await note.locator('#confirmTask').click();await note.locator('#status').filter({hasText:'任务已创建'}).waitFor();
 const tasks=call({method:'GET',path:'/api/tasks'}).body.items;assert(tasks.length===1&&tasks[0].title==='比较两个地点','Explicit confirmation creates one real task');
 await page.locator('#work-tasks-tab').click();await page.getByText('任务与提醒',{exact:true}).click();
 await page.locator('.task-progress-card').filter({hasText:'比较两个地点'}).waitFor({timeout:15000});
 assert(true,'Confirmed task appears in shared tasks panel');
 await page.locator('#work-notes-tab').click();
 await page.evaluate(()=>document.querySelector('#assistantMount').dispatchEvent(new CustomEvent('assistant-result',{bubbles:true})));
 assert(await page.locator('#work-notes-tab').getAttribute('aria-selected')==='true','Incoming action result never steals editor tab');
 assert((await page.locator('#work-badge').textContent()).includes('新'),'Hidden task result has visible badge');
 await mkdir('eval/ui/workspace',{recursive:true});await writeFile('eval/ui/workspace/integration.json',JSON.stringify({checks,scope:'Main page embedded notes -> real router -> temporary SQLite; no production writes'},null,2));console.log('PASS',checks);
}finally{await browser.close();}
