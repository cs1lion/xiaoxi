// Real browser AudioWorklet/AudioContext graph with synthetic local oscillators.
// No microphone permission, server, model, user data, or acoustic-AEC claim.
import { chromium } from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(import.meta.dirname,'..');
const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://localhost:19876/**',async route=>{
  const pathname=new URL(route.request().url()).pathname;
  if(pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><title>Local AudioWorklet verification</title>'});
  const p=path.resolve(root,'web',pathname.replace(/^\//,''));
  if(!p.startsWith(path.join(root,'web')+path.sep))return route.abort();
  try {return route.fulfill({contentType:'text/javascript',body:await readFile(p)});}catch{return route.fulfill({status:404,body:''});}
 });
 await page.goto('http://localhost:19876/');
 const result=await page.evaluate(async()=>{
  const {createCapture}=await import('/voice/capture.js');
  const ctx=new AudioContext({sampleRate:48000});await ctx.resume();
  // Chromium's audio-thread fetch bypasses Playwright page routing. Supply the
  // exact routed source through a Blob, still compiled by the real worklet VM.
  const source=await (await fetch('/voice/capture-worklet.js')).text();
  const blob=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));
  const addModule=ctx.audioWorklet.addModule.bind(ctx.audioWorklet);
  ctx.audioWorklet.addModule=()=>addModule(blob);
  const micDestination=ctx.createMediaStreamDestination();
  const mic=ctx.createOscillator();mic.frequency.value=437;mic.connect(micDestination);mic.start();
  const ref=ctx.createOscillator();ref.frequency.value=997;const gain=ctx.createGain();gain.gain.value=.2;ref.connect(gain);ref.start();
  const chunks=[];let runtimeError=null;
  const handle=await createCapture({context:ctx,stream:micDestination.stream,referenceNode:gain,onChunk:c=>chunks.push(c),onError:e=>runtimeError=e.message});
  const until=Date.now()+3000;
  while(chunks.length<12&&Date.now()<until)await new Promise(r=>setTimeout(r,20));
  handle.stop();const count=chunks.length;await new Promise(r=>setTimeout(r,100));
  const energy=a=>a.reduce((s,x)=>s+x*x,0)/Math.max(1,a.length);
  const summary={kind:handle.kind,reference:handle.hasReference,count,late:chunks.length-count,runtimeError,
   aligned:chunks.every(c=>c.microphone.length===960&&c.reference.length===960),
   micEnergy:Math.max(...chunks.map(c=>energy(c.microphone))),refEnergy:Math.max(...chunks.map(c=>energy(c.reference))),
   increasingTime:chunks.every((c,i)=>!i||c.audioTime>chunks[i-1].audioTime)};
  mic.stop();ref.stop();micDestination.stream.getTracks().forEach(t=>t.stop());await ctx.close();URL.revokeObjectURL(blob);return summary;
 });
 console.log('Worklet diagnostic',JSON.stringify(result));
 assert.equal(result.kind,'worklet');assert.equal(result.reference,true);assert.ok(result.count>=12);
 assert.ok(result.aligned);assert.ok(result.increasingTime);assert.ok(result.micEnergy>.01);assert.ok(result.refEnergy>.001);
 assert.equal(result.late,0);assert.equal(result.runtimeError,null);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,...result},null,2));
} finally {await browser.close();}
