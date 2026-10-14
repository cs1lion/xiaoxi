import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({args:['--enable-unsafe-swiftshader']});
try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8000/?avatar=seed');
 await page.waitForFunction(()=>document.querySelector('#avatarRendererStatus')?.textContent.includes('半身表演已启用'));
 assert.equal(await page.locator('#avatarRenderer').inputValue(),'seed');
 await page.locator('#avatarRenderer').evaluate(el=>{el.value='svg';el.dispatchEvent(new Event('change'));});
 await page.waitForFunction(()=>document.querySelector('#avatarRendererStatus').textContent.includes('切回'));
 assert.equal(await page.locator('#avatarStage canvas').count(),0);
 await page.locator('#avatarRenderer').evaluate(el=>{el.value='seed';el.dispatchEvent(new Event('change'));});
 await page.waitForFunction(()=>document.querySelector('#avatarRendererStatus').textContent.includes('半身表演已启用'));
 await page.screenshot({path:'eval/avatar/seed-chat.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('Seed chat URL, selection, SVG disposal, reload, attribution: passed');
}finally{await browser.close();}
