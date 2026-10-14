import {chromium, expect} from '@playwright/test';
import {mkdir, writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const checks=[];
try {
 const page=await browser.newPage({viewport:{width:1200,height:900}}), errors=[],writes=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.routeWebSocket('**/ws',socket=>socket.onMessage(()=>{}));
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(route.request().method()!=='GET'){writes.push(path);return route.fulfill({status:403,json:{detail:'no_writes_in_writing_view_test'}});}
  if(path==='/api/editor-insights/status')return route.fulfill({json:{available:false,reason:'本次仅检查写作入口',pause_ms:12000,cooldown_ms:90000,max_checks:5}});
  if(path==='/api/notes/selection')return route.fulfill({json:{revision:0,note_id:null,selected_version:null}});
  if(path==='/api/activity')return route.fulfill({json:{selection:{note_id:null},note:null,thread:null,resume:{text:'先选择一份笔记。'}}});
  if(/^\/api\/(notes|tasks|reminders|notifications|threads)$/.test(path))return route.fulfill({json:{items:[]}});
  if(path==='/api/threads/selection')return route.fulfill({json:{thread_id:null}});
  return route.continue();
 });
 await page.goto('http://127.0.0.1:8000/');
 const editor=page.frameLocator('iframe[title="个人笔记编辑器"]');
 await expect(page.locator('#writingToggle')).toBeVisible();
 await page.locator('#work-notes-tab').click();
 await editor.locator('#title').fill('入口保活演示');
 await editor.locator('#body').fill('同一份未保存草稿，进入一起写和回到聊天都完整保留。');
 const frame=page.frames().find(frame=>frame.url().includes('/static/notes.html'));
 await frame.evaluate(()=>{window.writingViewProbe='same-document';});
 await page.locator('#writingToggle').click();
 await expect(page.locator('body')).toHaveClass(/is-writing/);
 await expect(page.getByRole('button',{name:'回到聊天',exact:true})).toBeVisible();
 await expect(editor.locator('#body')).toHaveValue('同一份未保存草稿，进入一起写和回到聊天都完整保留。');
 if(await frame.evaluate(()=>window.writingViewProbe)!=='same-document')throw Error('Opening reloaded the editor document');
 const width=await page.locator('iframe[title="个人笔记编辑器"]').evaluate(element=>element.getBoundingClientRect().width);
 if(width<900)throw Error('Writing view remains a narrow sidebar');
 checks.push('Homepage has a direct writing entrance; writing view is wide');
 checks.push('Opening retains the same iframe document and unsaved title/body');
 await mkdir('eval/ui/writing',{recursive:true});
 await page.screenshot({path:'eval/ui/writing/entrance-desktop.png'});
 await page.getByRole('button',{name:'回到聊天',exact:true}).click();
 await expect(page.locator('#textInput')).toBeVisible();
 await expect(editor.locator('#body')).toHaveValue('同一份未保存草稿，进入一起写和回到聊天都完整保留。');
 if(await frame.evaluate(()=>window.writingViewProbe)!=='same-document')throw Error('Closing reloaded the editor');
 checks.push('Returning to chat preserves the editor without document recreation');
 for(const width of [502,390,320]){
  await page.setViewportSize({width,height:width===502?507:844});
  await page.locator('#writingToggle').click();
  await expect(editor.locator('#body')).toHaveValue('同一份未保存草稿，进入一起写和回到聊天都完整保留。');
  if(!await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw Error(`Page overflow at ${width}`);
  if(!await frame.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw Error(`Editor overflow at ${width}`);
  await expect(page.locator('#stopBtn')).toBeVisible();
  await expect(page.getByRole('button',{name:'回到聊天',exact:true})).toBeVisible();
  if(width===390)await page.screenshot({path:'eval/ui/writing/entrance-mobile.png'});
  await page.getByRole('button',{name:'回到聊天',exact:true}).click();
 }
 checks.push('502, 390 and 320px views preserve the draft, stop button and return action without overflow');
 await page.locator('#writingToggle').click();
 await page.locator('#writingToggle').focus();await page.keyboard.press('Escape');
 await expect(page.locator('body')).not.toHaveClass(/is-writing/);
 checks.push('Escape returns to chat without deleting the draft');
 if(errors.length||writes.length)throw Error(JSON.stringify({errors,writes}));
 checks.push('No runtime errors, model/audio calls or save/association writes');
 await writeFile('eval/ui/writing/entrance-results.json',JSON.stringify({checks,errors,writes,scope:'Isolated browser fixtures; no personal data or paid calls'},null,2));
 console.log(JSON.stringify({result:'PASS',checks},null,2));
} finally {await browser.close();}
