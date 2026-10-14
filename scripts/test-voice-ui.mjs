import {chromium} from '@playwright/test';
const browser=await chromium.launch({headless:true,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
try {
 const page=await browser.newPage(); const errors=[]; const messages=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.routeWebSocket('**/ws',socket=>socket.onMessage(data=>messages.push(data)));
 await page.addInitScript(()=>{
   const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
   window.testTracks=[];
   navigator.mediaDevices.getUserMedia=async opts=>{
     if(window.denyMic) throw new DOMException('Permission denied','NotAllowedError');
     const stream=await original(opts); window.testTracks.push(...stream.getTracks());
     if(window.delayMic) await new Promise(resolve=>window.releaseMic=resolve);
     return stream;
   };
 });
 await page.goto('http://127.0.0.1:8000/');
 await page.locator('#avatarStage svg, #avatarStage canvas').waitFor();
 await page.waitForFunction(()=>document.querySelector('#status').textContent==='在线');
 await page.locator('#micBtn').focus();
 await page.keyboard.down('Space');
 await page.waitForFunction(()=>document.querySelector('#micBtn').classList.contains('recording'));
 await page.waitForFunction(()=>window.testTracks.length>0);
 await page.keyboard.up('Space');
 await page.waitForFunction(()=>window.testTracks.every(t=>t.readyState==='ended'));
 if(!messages.some(m=>typeof m==='string'&&m.includes('audio_end'))) throw Error('missing audio_end');
 await page.evaluate(()=>window.delayMic=true);
 await page.keyboard.down('Space');
 await page.waitForFunction(()=>!!window.releaseMic);
 await page.keyboard.up('Space');
 await page.evaluate(()=>window.releaseMic());
 await page.waitForFunction(()=>window.testTracks.every(t=>t.readyState==='ended'));
 if(await page.locator('#micBtn').evaluate(el=>el.classList.contains('recording'))) throw Error('late permission started unwanted recording');
 await page.evaluate(()=>{window.delayMic=false;window.denyMic=true;});
 await page.keyboard.down('Space');
 await page.keyboard.up('Space');
 await page.waitForFunction(()=>document.querySelector('#subtitle').textContent.includes('无法使用麦克风'));
 await page.evaluate(()=>window.denyMic=false);
 await page.locator('#bargeToggle').click();
 await page.waitForFunction(()=>window.testTracks.some(t=>t.readyState==='live'));
 if(await page.locator('#bargeToggle').getAttribute('aria-pressed')!=='true') throw Error('continuous state not exposed');
 await page.locator('#bargeToggle').click();
 await page.waitForFunction(()=>window.testTracks.every(t=>t.readyState==='ended'));
 await page.locator('#bargeToggle').click();
 await page.waitForFunction(()=>window.testTracks.some(t=>t.readyState==='live'));
 await page.locator('#stopBtn').click();
 if(await page.locator('#bargeToggle').getAttribute('aria-pressed')!=='true') throw Error('stop should preserve local continuous listening');
 await page.locator('#bargeToggle').click();
 await page.waitForFunction(()=>window.testTracks.every(t=>t.readyState==='ended'));
 // Stop cancels a current turn, but idle local listening has no server turn to cancel.
 if(errors.length) throw Error(errors.join(';'));
 console.log('PASS: keyboard recording, release stops tracks, release during permission cancels, denied permission feedback, no page errors');
} finally {await browser.close();}
