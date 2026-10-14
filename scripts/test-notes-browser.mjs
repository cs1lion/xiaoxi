import {chromium} from '@playwright/test';
import {spawnSync} from 'node:child_process';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
const dir=await mkdtemp(path.join(os.tmpdir(),'aqi-notes-ui-'));
const call=request=>{const result=spawnSync(path.resolve('.venv/Scripts/python.exe'),['scripts/task_api_bridge.py',path.join(dir,'assistant.db')],{input:JSON.stringify(request),encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}});if(result.status!==0)throw Error(result.stderr);return JSON.parse(result.stdout);};
const browser=await chromium.launch({headless:true});const checks=[];
const assert=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const req=route.request();const u=new URL(req.url());const data={method:req.method(),path:u.pathname+u.search};if(req.postData())data.body=req.postDataJSON();const response=call(data);await route.fulfill({status:response.status,json:response.body});});
 await page.goto('http://127.0.0.1:8000/static/notes.html');
 await page.locator('#title').fill('周末出行');await page.locator('#body').fill('先比较两个目的地。预算不要超过五百。');await page.locator('#save').click();
 await page.waitForFunction(()=>document.querySelector('#status').textContent==='笔记已保存。');
 const note=call({method:'GET',path:'/api/notes'}).body.items[0];assert(!!note,'Explicit save persists note');
 await page.locator('#query').fill('目的地');await page.getByRole('button',{name:'搜索',exact:true}).click();await page.locator('.note').waitFor();assert(await page.locator('.note').count()===1,'Chinese search reaches actual backend');
 await page.locator('#prepare').click();assert(call({method:'GET',path:'/api/tasks'}).body.items.length===0,'Preparing draft never creates task');
 await page.locator('#taskTitle').fill('比较两个出行地点');await page.locator('#confirmTask').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('任务已创建'));
 const task=call({method:'GET',path:'/api/tasks'}).body.items[0];assert(task.title==='比较两个出行地点'&&task.description.includes(note.id),'Confirmed task persists source note reference');
 call({method:'PATCH',path:`/api/notes/${note.id}`,body:{title:'周末出行',body:'另一窗口修改',version:1}});
 await page.locator('#body').fill('我的未保存内容');await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('其他窗口'));
 assert(await page.locator('#body').inputValue()==='我的未保存内容','Conflict preserves unsaved content');
 page.on('dialog',dialog=>dialog.accept());await page.locator('#query').fill('');await page.getByRole('button',{name:'搜索',exact:true}).click();await page.locator('.note').click();await page.waitForFunction(()=>document.querySelector('#source').textContent.includes('版本 2'));
 await page.locator('#delete').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('笔记已删除'));
 assert(call({method:'GET',path:'/api/notes'}).body.items.length===0,'Deletion removes searchable note');
 assert(call({method:'GET',path:'/api/tasks'}).body.items.length===1,'Note deletion does not silently delete confirmed task');
 assert(errors.length===0,'No browser errors');await mkdir('eval/ui/notes',{recursive:true});await writeFile('eval/ui/notes/results.json',JSON.stringify({checks,errors,scope:'Browser -> real FastAPI via test transport -> temporary SQLite; no production writes'},null,2));console.log('PASS',checks);
}finally{await browser.close();}
