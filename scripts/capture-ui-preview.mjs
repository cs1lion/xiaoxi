import {chromium} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 // Preview fixtures show the intended empty state; live backend availability is tested separately.
 await page.route('**/api/preferences/proactivity',r=>r.fulfill({json:{timezone:'Asia/Shanghai',reminder_enabled:false}}));
 for(const endpoint of ['tasks','reminders','notifications']) await page.route(`**/api/${endpoint}`,r=>r.fulfill({json:{items:[]}}));
 await page.route('**/api/memories',r=>r.fulfill({json:{count:0,items:[]}}));
 await page.routeWebSocket('**/ws',socket=>socket.onMessage(()=>{}));
 await page.goto('http://127.0.0.1:8000/');
 await page.locator('#avatarStage svg, #avatarStage canvas').waitFor();
 await page.emulateMedia({reducedMotion:'reduce'});
 await mkdir('eval/ui',{recursive:true});
 await page.screenshot({path:'eval/ui/preview-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'eval/ui/preview-mobile.png',fullPage:true});
 console.log('Captured clean empty-state previews using explicit service fixtures.');
} finally {await browser.close();}
