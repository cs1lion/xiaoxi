// Read-only/offline model and calibration UI regression for the face preset.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';
const output='eval/avatar/face-shading/ui';
const browser=await chromium.launch({headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1320,height:960}});
page.setDefaultTimeout(20000);
const errors=[],forbiddenRequests=[],checks=[],records=[];
page.on('pageerror',error=>errors.push(error.message));
try {
  await mkdir(output,{recursive:true});
  await page.route('**/api/**',route=>{
    if(route.request().method()!=='GET'){forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`);return route.abort();}
    return new URL(route.request().url()).pathname==='/api/avatar'?route.continue():route.fulfill({json:{count:0,items:[],context:''}});
  });
  await page.addInitScript(()=>{
    window.faceUIMicrophoneRequests=0;
    if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=()=>{window.faceUIMicrophoneRequests++;return Promise.reject(new Error('No mic in face UI QA'));};
  });
  await page.goto('http://127.0.0.1:8000/static/avatar_lab.html?model=daily');
  await page.waitForFunction(()=>window.__avatarReady&&window.__avatar.svg._state?.vrm?.meta.name==='Seed-san Daily Study',{}, {timeout:90000});
  const snapshot=()=>page.evaluate(()=>{
    const r=window.__avatar.svg,s=r._state;
    return {mode:r.getFaceShading(),model:s.vrm.meta.name,checked:document.getElementById('face-shading').checked,
      disabled:document.getElementById('face-shading').disabled,status:document.getElementById('face-shading-status').textContent,
      face:s.faceShading.map(x=>({name:x.material.name,color:x.material.color.toArray(),shade:x.material.shadeColorFactor.toArray(),originalColor:x.color.toArray(),originalShade:x.shadeColorFactor.toArray()}))};
  });
  await page.locator('#face-shading').uncheck();const initial=await snapshot();records.push(initial);
  assert(initial.mode.available&&!initial.mode.enabled&&!initial.checked);
  await page.locator('#seed').click();
  await page.waitForFunction(()=>window.__avatarReady&&window.__avatar.svg._state?.vrm?.meta.name==='Seed-san',{}, {timeout:90000});
  const seed=await snapshot();records.push(seed);assert(!seed.mode.available&&seed.disabled&&!seed.checked);assert.equal(seed.face.length,0);
  await page.locator('#daily').click();
  await page.waitForFunction(()=>window.__avatarReady&&window.__avatar.svg._state?.vrm?.meta.name==='Seed-san Daily Study',{}, {timeout:90000});
  const daily=await snapshot();records.push(daily);assert.deepEqual(daily,initial);
  checks.push('Daily disabled choice survives Seed-san switch and back; unsupported model has no face material registration and UI is disabled');
  for(const enabled of [false,true]) {
    if(enabled)await page.locator('#face-shading').check();else await page.locator('#face-shading').uncheck();
    const before=await snapshot();
    await page.locator('#calibrationEnter').click();
    await page.waitForFunction(()=>window.__avatarCalibration?.active);
    assert(await page.locator('#face-shading').isDisabled());
    await page.selectOption('#calibrationExpression','happy');
    await page.locator('#calibrationBlink').evaluate(el=>{el.value='100';el.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.locator('#calibrationMouth').evaluate(el=>{el.value='65';el.dispatchEvent(new Event('input',{bubbles:true}));});
    assert(await page.evaluate(()=>window.__calibrationSnapshot.registration.compatible));
    await page.locator('#calibrationExit').click();
    await page.waitForFunction(()=>!window.__avatarCalibration?.active);
    const after=await snapshot();records.push({enabled,before,after});assert.deepEqual(after,before);
  }
  checks.push('actual calibration UI locks appearance control, supports happy/blink/mouth, and restores both initial preset choices and exact colors');
  const microphoneRequests=await page.evaluate(()=>window.faceUIMicrophoneRequests);
  assert.equal(microphoneRequests,0);assert.deepEqual(errors,[]);assert.deepEqual(forbiddenRequests,[]);
  checks.push('no browser error, microphone request, live speech/model call, or mutating API');
  const sources={};
  for(const file of ['web/avatar/render/vrm.js','web/avatar/calibration.js','web/avatar_lab.js','web/avatar_lab.html'])sources[file]=createHash('sha256').update(await readFile(file)).digest('hex');
  await writeFile(`${output}/report.json`,JSON.stringify({checks,records,sourceHashes:sources,errors,forbiddenRequests,microphoneRequests},null,2));
  console.log(`Face UI regression: ${checks.length} groups passed; ${output}/report.json`);
} finally{await browser.close();}
