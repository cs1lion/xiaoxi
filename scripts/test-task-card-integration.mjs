// Browser -> real FastAPI TestClient -> persistent temporary SQLite, no test HTTP server.
import {chromium} from '@playwright/test';
import {spawnSync} from 'node:child_process';
import {mkdtemp,readFile,writeFile,mkdir} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
const directory=await mkdtemp(path.join(os.tmpdir(),'aqi-task-integration-'));
const database=path.join(directory,'assistant.db');
const call=request=>{
 const result=spawnSync(path.resolve('.venv/Scripts/python.exe'),['scripts/task_api_bridge.py',database],{input:JSON.stringify(request),encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}});
 if(result.status!==0)throw Error(result.stderr||`bridge failed ${result.status}`);
 return JSON.parse(result.stdout);
};
const task=call({method:'POST',path:'/api/tasks',body:{title:'隔离联调任务',request_id:'integration-task'}}).body;
const browser=await chromium.launch({headless:true});
const checks=[];const assert=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const req=route.request();const data={method:req.method(),path:new URL(req.url()).pathname};
  if(req.postData())data.body=req.postDataJSON();
  const value=call(data);await route.fulfill({status:value.status,json:value.body});
 });
 await page.goto('http://127.0.0.1:8000/static/archive/card_preview.html');
 await page.evaluate(async()=>{
  document.body.replaceChildren();
  const {renderTaskCard}=await import('/static/task-card.js');
  const mount=document.createElement('section');mount.className='assistant-panel';document.body.append(mount);
  const api=async(p,method='GET',body)=>{const r=await fetch(`/api/${p}`,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});const value=await r.json();if(!r.ok)throw Error(value.detail);return value;};
  const refresh=async()=>{const [tasks,reminders]=await Promise.all([api('tasks'),api('reminders')]);mount.replaceChildren(...tasks.items.map(task=>renderTaskCard(task,{api,refresh,reminders:reminders.items})));};
  await refresh();
 });
 const card=page.locator('.task-progress-card');
 await card.locator('.task-editor summary').first().click();
 await card.getByRole('textbox',{name:'下一步：隔离联调任务'}).fill('查两个候选地点');
 await card.getByRole('button',{name:'保存下一步'}).click();
 await page.waitForFunction(()=>document.querySelector('.task-editor summary').textContent.includes('查两个'));
 assert(call({method:'GET',path:'/api/tasks'}).body.items[0].next_action==='查两个候选地点','Browser next action reaches actual SQLite');
 await card.getByRole('button',{name:'开始做'}).click();await page.waitForFunction(()=>document.querySelector('.task-status').textContent==='进行中');
 await card.getByText('给这件事设提醒',{exact:true}).click();
 const date=new Date(Date.now()+3600000);await card.locator('input[type=datetime-local]').fill(new Date(date-date.getTimezoneOffset()*60000).toISOString().slice(0,16));
 await card.getByRole('button',{name:'保存关联提醒'}).click();await card.locator('.task-reminder-list').waitFor();
 const reminder=call({method:'GET',path:'/api/reminders'}).body.items[0];assert(reminder.task_id===task.id,'Actual reminder linked to actual task');
 await card.getByRole('button',{name:'完成',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.task-status').textContent==='已完成');
 assert(call({method:'GET',path:'/api/reminders'}).body.items[0].status==='cancelled','Actual completion cancels actual reminder');
 await card.getByRole('button',{name:'重新打开'}).click();await page.waitForFunction(()=>document.querySelector('.task-status').textContent==='未开始');
 assert(call({method:'GET',path:'/api/reminders'}).body.items[0].status==='cancelled','Actual reopen does not resurrect reminder');
 // Concurrent write after card rendered: browser version is now stale.
 const current=call({method:'GET',path:'/api/tasks'}).body.items[0];call({method:'PATCH',path:`/api/tasks/${task.id}`,body:{version:current.version,next_action:'另一窗口更新'}});
 await card.locator('.task-editor summary').first().click();await card.getByRole('textbox',{name:'下一步：隔离联调任务'}).fill('我的未保存草稿');
 await card.getByRole('button',{name:'保存下一步'}).click();await page.waitForFunction(()=>document.querySelector('.task-feedback').textContent.includes('其他窗口'));
 assert(call({method:'GET',path:'/api/tasks'}).body.items[0].next_action==='另一窗口更新','Real conflict rejects stale browser overwrite');
 assert(await card.getByRole('textbox',{name:'下一步：隔离联调任务'}).inputValue()==='我的未保存草稿','Conflict retains browser draft');
 assert(errors.length===0,'No page errors');
 await mkdir('eval/ui/tasks',{recursive:true});await writeFile('eval/ui/tasks/integration-results.json',JSON.stringify({checks,errors,scope:'Actual task-card browser module -> transport bridge -> real FastAPI router -> temporary SQLite. Existing service not restarted; no live model.',temporaryDatabase:database},null,2));
 console.log('PASS',checks);
}finally{await browser.close();}
