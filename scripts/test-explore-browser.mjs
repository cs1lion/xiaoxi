import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const browser = await chromium.launch({ headless:true });
const checks=[]; const assert=(condition,label)=>{if(!condition)throw Error(label);checks.push(label);};
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}}); const errors=[]; const sent=[];
 page.on('pageerror',error=>errors.push(error.stack || error.message));
 await page.route('**/api/preferences/proactivity',r=>r.fulfill({json:{timezone:'UTC',reminder_enabled:false}}));
 for(const name of ['tasks','reminders','notifications'])await page.route(`**/api/${name}`,r=>r.fulfill({json:{items:[]}}));
 await page.route('**/api/memories',r=>r.fulfill({json:{count:0,items:[]}}));
 await page.routeWebSocket('**/ws',socket=>socket.onMessage(data=>{if(typeof data==='string')sent.push(JSON.parse(data));}));
 await page.goto('http://127.0.0.1:8000/');
 await page.locator('#avatarStage svg, #avatarStage canvas').waitFor();
 const baseline=await page.locator('.stage').evaluate(el=>getComputedStyle(el).background);
 assert(await page.locator('#exploreLauncher').count()===0,'Normal URL has no experimental controls');
 assert(!await page.evaluate(()=>performance.getEntriesByType('resource').some(e=>e.name.endsWith('/explore.js'))),'Normal URL does not download experimental module');
 await page.goto('http://127.0.0.1:8000/?explore=1');
 await page.locator('#exploreLauncher').waitFor();
 await page.locator('#avatarStage svg, #avatarStage canvas').waitFor();
 await page.locator('#textInput').fill('保留这段草稿');
 await page.locator('#exploreLauncher').click();
 await mkdir('eval/ui/explore',{recursive:true});
 for(const preset of ['desk','companion','future']) {
   await page.locator('#explorePreset').selectOption(preset);
   assert(await page.locator('html').getAttribute('data-explore-tone')===preset,`${preset}: selected tone applied`);
   assert(await page.inputValue('#textInput')==='保留这段草稿',`${preset}: draft preserved`);
   await page.locator('#exploreNotes').fill(`${preset} 候选判断`);
   await page.locator('#exploreSave').click();
   await page.getByRole('button',{name:'收起',exact:true}).click();
   await page.screenshot({path:`eval/ui/explore/${preset}-desktop.png`,fullPage:true});
   await page.locator('#exploreLauncher').click();
 }
 await page.locator('#exploreCompare').focus();
 await page.keyboard.down('Space');
 assert(await page.locator('html').getAttribute('data-explore-tone')===null,'Held comparison temporarily removes tone');
 assert(await page.locator('.stage').evaluate(el=>getComputedStyle(el).background)===baseline,'Held comparison restores exact baseline stage background');
 await page.keyboard.up('Space');
 assert(await page.locator('html').getAttribute('data-explore-tone')==='future','Releasing comparison restores experiment');
 // All 32 independent-effect combinations must remain applicable and removable.
 for(let mask=0;mask<32;mask++) {
   for(const [index,id] of ['atmosphere','texture','glass','halo','spring'].entries()) await page.locator(`[data-effect=${id}]`).setChecked(!!(mask&(1<<index)));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Combination ${mask}: no page overflow`);
 }
 await page.locator('#exploreReset').click();
 assert(await page.locator('html').evaluate(el=>![...el.attributes].some(a=>a.name.startsWith('data-fx-')||a.name==='data-explore-tone')),'Reset removes every experiment attribute');
 assert(await page.locator('.stage').evaluate(el=>getComputedStyle(el).background)===baseline,'Reset restores exact baseline background');
 const downloadPromise=page.waitForEvent('download'); await page.locator('#exploreExport').click();
 const download=await downloadPromise; const path=await download.path();
 const {readFile}=await import('node:fs/promises'); const exported=JSON.parse(await readFile(path,'utf8'));
 assert(exported.records.length===3,'Export contains three comparison records');
 await page.locator('#explorePreset').selectOption('future');
 await page.getByRole('button',{name:'收起',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Future preset fits mobile viewport');
 await page.screenshot({path:'eval/ui/explore/future-mobile.png',fullPage:true});
 await page.locator('#exploreLauncher').click();
 await page.keyboard.press('Escape');
 assert(!await page.locator('.explore-dialog').isVisible(),'Escape closes experiment dialog');
 await page.locator('#sendBtn').click();
 assert(sent.some(m=>m.type==='text'&&m.text==='保留这段草稿'),'Text send remains functional with experiment enabled');
 await page.locator('#exploreLauncher').click(); await page.locator('#exploreExit').click();
 assert(await page.locator('#exploreLauncher').count()===0,'Runtime uninstall removes launcher');
 assert(await page.locator('link[href="/static/explore.css"]').count()===0,'Runtime uninstall removes stylesheet');
 assert(!new URL(page.url()).searchParams.has('explore'),'Runtime uninstall cleans entry URL');
 assert(errors.length===0,`No browser errors: ${errors.join('; ')}`);
 await writeFile('eval/ui/explore/results.json',JSON.stringify({checks,errors,fixture:'Controlled WebSocket and read-only API fixtures; no production writes'},null,2));
 console.log(`PASS: ${checks.length} checks; no browser errors`);
} finally {await browser.close();}
