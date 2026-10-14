import {chromium} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});
const glossy=process.argv.includes('--gloss');
const finish=glossy?'gloss':'ceramic';
try{
 const page=await browser.newPage({viewport:{width:1440,height:1080}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8000/static/card_preview.html'+(glossy?'?finish=gloss':''));
 await page.locator('.invitation').first().waitFor();
 await mkdir('eval/ui/cards',{recursive:true});
 await page.screenshot({path:`eval/ui/cards/${finish}-desktop.png`,fullPage:true});
 await page.locator('.invitation').first().click();
 if(!(await page.inputValue('#draft')).includes('开心'))throw Error('draft interaction failed');
 await page.locator('#complete').click();
 if(await page.locator('#complete').getAttribute('aria-pressed')!=='true')throw Error('task interaction failed');
 await page.locator('#complete').click();
 await page.setViewportSize({width:390,height:844});
 if(!await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw Error('mobile overflow');
 await page.screenshot({path:`eval/ui/cards/${finish}-mobile.png`,fullPage:true});
 if(errors.length)throw Error(errors.join(';'));
 console.log('PASS: desktop/mobile screenshots, draft preparation, task toggle, mobile no overflow, no page errors. Demo only; no backend writes.');
}finally{await browser.close();}
