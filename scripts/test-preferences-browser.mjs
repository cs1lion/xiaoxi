import { chromium } from '@playwright/test';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
let items=[{id:'seedExpiry',dimension:'humor',value:'light',scope:{activity:'chat'},source:'explicit_user',valid_until:1800000000,status:'active',evidence_ids:[],created_at:1700000000,supersedes:null,active:true}], evidence={}, serial=0;
const server=createServer(async(req,res)=>{
  if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end('<aside id="mount"></aside><script type="module">import {bootPreferences} from "/module.js";bootPreferences(document.querySelector("#mount"));</script>');return;}
  if(req.url==='/module.js'){res.setHeader('Content-Type','text/javascript');res.end(await readFile('web/interaction-preferences.js','utf8'));return;}
  let raw='';for await(const chunk of req)raw+=chunk;const data=raw?JSON.parse(raw):{};
  const path=req.url.replace('/api/interaction/','');res.setHeader('Content-Type','application/json');let output={};
  if(path==='preferences'&&req.method==='GET') output={items};
  else if(path==='evidence'&&req.method==='POST'){output={id:`e${++serial}`,text:data.text,created_at:1700000000};evidence[output.id]=output;}
  else if(path==='preferences'&&req.method==='POST'){output={...data,id:`p${++serial}`,source:'explicit_user',status:'active'};items.push(output);}
  else if(path.startsWith('evidence/')&&path.endsWith('/delete')){const id=path.split('/')[1];delete evidence[id];items=items.filter(c=>!c.evidence_ids.includes(id));}
  else if(path.startsWith('evidence/')) output=evidence[path.split('/')[1]];
  else if(path.endsWith('/revise')){const old=items.find(c=>c.id===path.split('/')[1]);old.status='superseded';output={...old,...data,id:`p${++serial}`,status:'active'};items.push(output);}
  else if(path.endsWith('/delete'))items=items.filter(c=>c.id!==path.split('/')[1]);
  res.end(JSON.stringify(output));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
try{
  browser=await chromium.launch({headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto(`http://127.0.0.1:${server.address().port}`);await page.getByText('相处偏好',{exact:true}).click();
  await page.getByLabel('偏好类型',{exact:true}).selectOption('detail');
  await page.getByLabel('偏好选择',{exact:true}).selectOption('detailed');
  await page.getByLabel('适用场景',{exact:true}).selectOption('technical');
  await page.getByLabel('我的偏好说明',{exact:true}).fill('技术讨论请详细讲 <img src=x onerror=alert(1)>');
  await page.getByRole('button',{name:'保存偏好',exact:true}).click();
  await page.getByRole('heading',{name:'回复详略：详细'}).waitFor();
  const seedCard=page.locator('section.assistant-card').filter({has:page.getByRole('heading',{name:'幽默方式：允许轻微调侃'})});
  const detailCard=page.locator('section.assistant-card').filter({has:page.getByRole('heading',{name:'回复详略：详细'})});
  assert.match(await seedCard.innerText(), /到期/);
  assert.doesNotMatch(await seedCard.innerText(), /未确认延续至今，未设定过期日/);
  await detailCard.getByRole('button',{name:'查看来源证据',exact:true}).click();
  await page.getByText('技术讨论请详细讲 <img src=x onerror=alert(1)>',{exact:true}).waitFor();assert.equal(await page.locator('img').count(),0);
  await detailCard.getByRole('button',{name:'修订',exact:true}).click();
  await page.getByLabel('偏好选择',{exact:true}).selectOption('short');await page.getByLabel('我的偏好说明',{exact:true}).fill('现在技术讨论也简短些');
  await page.getByRole('button',{name:'确认修订',exact:true}).click();await page.getByRole('heading',{name:'回复详略：简短'}).waitFor();
  const active=page.locator('section.assistant-card').filter({has:page.getByRole('heading',{name:'回复详略：简短'})});
  const superseded=page.locator('section.assistant-card').filter({has:page.getByRole('heading',{name:'回复详略：详细'})});
  assert.match(await superseded.innerText(), /已被明确变化退役/);
  assert.doesNotMatch(await superseded.innerText(), /未确认延续至今/);
  assert.match(await active.innerText(), /未确认延续至今，未设定过期日/);
  await active.getByRole('button',{name:'查看来源证据'}).click();await active.getByRole('button',{name:'删除此证据及关联偏好'}).click();
  await page.getByRole('heading',{name:'回复详略：简短'}).waitFor({state:'detached'});
  assert.deepEqual(errors,[]);assert.equal(items.find(c=>c.id!=='seedExpiry').scope.activity,'technical');
  console.log('PASS preferences browser: create, evidence, safe rendering, scoped revision, dependency deletion');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
