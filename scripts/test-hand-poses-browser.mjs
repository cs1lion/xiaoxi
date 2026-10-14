// Actual Daily hand geometry/renderer QA. Fixed source routes and renderer dt;
// no live microphone, LLM/TTS or mutating APIs. Run --before once, then default.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { chromium } from '@playwright/test';

const root = 'eval/avatar/hand-poses', before = process.argv.includes('--before');
if (process.argv.length === 2 && !existsSync(`${root}/before/report.json`)) {
  console.log('SKIP hand-poses: 缺少 before 夹具（本地证据未入 git；先跑一次 --before 捕获后恢复）');
  process.exit(0);
}
const requested = process.argv.find(v => v.startsWith('--label='))?.slice(8);
if (requested) assert(/^[a-z0-9-]+$/.test(requested));
const label = before ? 'before' : requested || 'after', out = `${root}/${label}`;
const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const hash = async path => createHash('sha256').update(await readFile(path)).digest('hex');
const angle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.reduce((s,v,i) => s+v*b[i],0))/(Math.hypot(...a)*Math.hypot(...b))));
const dist = (a,b) => Math.hypot(...a.map((v,i)=>v-b[i]));
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'] });
const page = await browser.newPage({ viewport: { width: 1320, height: 1000 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(20000);
const checks=[], errors=[], forbiddenRequests=[], captures=[], dynamics=[], movies={}, sourceHashes={};
page.on('pageerror', error=>errors.push(error.message));
try {
  await mkdir(out,{recursive:true});
  const files=['avatar/render/vrm.js','avatar/render/gesture-poses.js','avatar/render/finger-poses.js','avatar/render/body-poses.js','avatar_lab.js','avatar_lab.html'];
  if(!before)files.push('avatar/render/hand-poses.js');
  for(const file of files){
    const name=file.split('/').at(-1), body=await readFile(before?`${out}/${name}`:`web/${file}`,'utf8');
    if(!before)await writeFile(`${out}/${name}`,body);
    sourceHashes[file]=createHash('sha256').update(body).digest('hex');
    await page.route(`**/static/${file}`,route=>route.fulfill({contentType:name.endsWith('.html')?'text/html':'text/javascript',body}));
  }
  await page.route('**/api/**',route=>{
    if(route.request().method()!=='GET'){forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`);return route.abort();}
    return new URL(route.request().url()).pathname==='/api/avatar'?route.continue():route.fulfill({json:{count:0,items:[],context:''}});
  });
  await page.addInitScript(()=>{
    const h=window.handHarness={microphoneRequests:0}; const Native=window.AudioContext||window.webkitAudioContext;
    window.AudioContext=class extends Native {constructor(...args){super(...args);h.nativeCtx=this;}};
    if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=()=>{h.microphoneRequests++;return Promise.reject(new Error('No mic in hand QA'));};
  });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(()=>window.__avatarReady&&window.__avatar.svg._state?.vrm?.meta.name==='Seed-san Daily Study',{}, {timeout:90000});
  await page.evaluate(async()=>{
    const THREE=await import('three'), {DEFAULT_NEUTRAL}=await import('/static/avatar/params.js');
    const a=window.__avatar,r=a.svg,s=r._state,h=window.handHarness;
    cancelAnimationFrame(a._raf);a._raf=0;a.performance.stop();a.lip.stop();r.setEyeFocus(false);r.setPresentation(false);r.setSecondaryMotion(0);
    s.observer?.disconnect();s.renderer.setPixelRatio(1);s.renderer.setSize(600,720,false);s.camera.aspect=600/720;r._frameCamera(s);
    h.neutral={...DEFAULT_NEUTRAL};h.clock={currentTime:0};h.sequence=0;h.modelHeight=s.modelHeight;
    h.digits=['Thumb','Index','Middle','Ring','Little'];h.fingerNames=['left','right'].flatMap(side=>h.digits.flatMap(digit=>(digit==='Thumb'?['Metacarpal','Proximal','Distal']:['Proximal','Intermediate','Distal']).map(j=>side+digit+j)));
    h.otherNames=[...Object.keys(s.arms),'hips','spine','chest','leftShoulder','rightShoulder','neck','head'];
    const normalized=name=>s.vrm.humanoid.getNormalizedBoneNode(name),raw=name=>s.vrm.humanoid.getRawBoneNode(name);
    h.bind=Object.fromEntries([...h.fingerNames,...h.otherNames].map(name=>[name,{node:normalized(name),raw:raw(name),rest:s.bones?.[name]?.rest.clone()??s.arms?.[name]?.rest.clone()??s.fingers?.[name]?.rest.clone()??s.bodyCoordination?.[name]?.rest.clone()??normalized(name)?.quaternion.clone()}]).filter(([,v])=>v.node));
    for(const v of Object.values(h.bind))v.node.quaternion.copy(v.rest);s.vrm.humanoid.update();s.scene.updateMatrixWorld(true);
    h.meshes=[];s.vrm.scene.traverse(node=>{if(node.isMesh)h.meshes.push(node);});
    const vertexWorld=({mesh,index})=>{const p=new THREE.Vector3().fromBufferAttribute(mesh.geometry.getAttribute('position'),index);mesh.applyBoneTransform(index,p);return p.applyMatrix4(mesh.matrixWorld);};
    const handVertices={},palmVertices={},digitVertices={},tipVertices={};
    for(const side of ['left','right']){
      handVertices[side]=[];palmVertices[side]=[];digitVertices[side]={};tipVertices[side]={};
      const descendants=new Set();raw(side+'Hand').traverse(n=>descendants.add(n));
      const digitBones={};for(const digit of h.digits){digitBones[digit]=new Set();raw(side+digit+(digit==='Thumb'?'Metacarpal':'Proximal')).traverse(n=>digitBones[digit].add(n));digitVertices[side][digit]=[];tipVertices[side][digit]=[];}
      for(const mesh of h.meshes){
        if(!mesh.isSkinnedMesh)continue;const indices=mesh.geometry.getAttribute('skinIndex'),weights=mesh.geometry.getAttribute('skinWeight');if(!indices||!weights)continue;
        const referenced=mesh.geometry.index?[...new Set(mesh.geometry.index.array)]:Array.from({length:indices.count},(_,i)=>i);
        const weighted=(index,set)=>['getX','getY','getZ','getW'].reduce((sum,get)=>sum+(set.has(mesh.skeleton.bones[indices[get](index)])?weights[get](index):0),0);
        for(const index of referenced){
          const v={mesh,index};if(weighted(index,descendants)>=.35)handVertices[side].push(v);
          if(weighted(index,new Set([raw(side+'Hand')]))>=.65)palmVertices[side].push(v);
          for(const digit of h.digits){if(weighted(index,digitBones[digit])>=.35)digitVertices[side][digit].push(v);if(weighted(index,new Set([raw(side+digit+'Distal')]))>=.35)tipVertices[side][digit].push(v);}
        }
      }
      for(const digit of h.digits){
        const distal=normalized(side+digit+'Distal').getWorldPosition(new THREE.Vector3()),previous=normalized(side+digit+(digit==='Thumb'?'Proximal':'Intermediate')).getWorldPosition(new THREE.Vector3()),direction=distal.clone().sub(previous).normalize();
        tipVertices[side][digit].sort((x,y)=>vertexWorld(y).sub(distal).dot(direction)-vertexWorld(x).sub(distal).dot(direction));tipVertices[side][digit]=tipVertices[side][digit].slice(0,Math.max(3,Math.ceil(tipVertices[side][digit].length*.12)));
      }
    }
    h.geometry={method:'unique POSITION indices actually referenced by geometry.index; digit descendants weight >=.35; hand-only palm weight >=.65; distal tips furthest 12% at bind',sides:Object.fromEntries(['left','right'].map(side=>[side,{handCount:handVertices[side].length,palmCount:palmVertices[side].length,digitCounts:Object.fromEntries(h.digits.map(d=>[d,digitVertices[side][d].length])),tipCounts:Object.fromEntries(h.digits.map(d=>[d,tipVertices[side][d].length]))}]))};
    h.refreshBounds=()=>{for(const mesh of h.meshes)if(mesh.isSkinnedMesh){mesh.computeBoundingSphere();mesh.computeBoundingBox();}};
    h.quietDraw=(params,dt)=>{const render=s.renderer.render;s.renderer.render=()=>{};try{r.draw(params,dt);}finally{s.renderer.render=render;}s.scene.updateMatrixWorld(true);};
    h.camera={position:s.camera.position.clone(),quaternion:s.camera.quaternion.clone(),fov:s.camera.fov,near:s.camera.near,far:s.camera.far,aspect:s.camera.aspect};h.cameraCenter=s.framing.center.clone();
    const modelBounds=new THREE.Box3().setFromObject(s.vrm.scene);h.fullCenter=modelBounds.getCenter(new THREE.Vector3());h.fullDistance=modelBounds.getSize(new THREE.Vector3()).y*1.9;
    h.restoreCamera=()=>{s.renderer.setSize(600,720,false);for(const k of ['position','quaternion'])s.camera[k].copy(h.camera[k]);for(const k of ['fov','near','far','aspect'])s.camera[k]=h.camera[k];s.camera.updateProjectionMatrix();};
    h.image=(width=600,height=720)=>{const c=document.createElement('canvas');c.width=width;c.height=height;const ctx=c.getContext('2d');ctx.fillStyle='#e7eaf0';ctx.fillRect(0,0,width,height);ctx.drawImage(s.renderer.domElement,0,0,width,height);return c.toDataURL('image/jpeg',.93);};
    const world=node=>node.getWorldPosition(new THREE.Vector3());
    const cloudMean=cloud=>{const p=new THREE.Vector3();for(const v of cloud)p.add(vertexWorld(v));return p.divideScalar(cloud.length);};
    const nearest=(cloudA,cloudB)=>{let min=Infinity;const b=cloudB.map(vertexWorld);for(const v of cloudA){const p=vertexWorld(v);for(const q of b)min=Math.min(min,p.distanceTo(q));}return min;};
    h.measure=(frame,geometry=false)=>{
      s.scene.updateMatrixWorld(true);const joints=Object.fromEntries(Object.entries(h.bind).map(([name,v])=>[name,{local:v.node.quaternion.toArray(),world:world(v.node).toArray(),worldQ:v.node.getWorldQuaternion(new THREE.Quaternion()).toArray(),rawLocal:v.raw?.quaternion.toArray(),rawWorld:v.raw?world(v.raw).toArray():null,restAngle:v.node.quaternion.angleTo(v.rest)}]));
      const hands=Object.fromEntries(['left','right'].map(side=>{
        const wrist=world(normalized(side+'Hand')),index=world(normalized(side+'IndexProximal')),little=world(normalized(side+'LittleProximal')),basis=index.clone().sub(wrist).cross(little.clone().sub(wrist)).normalize(),tips=Object.fromEntries(h.digits.map(d=>[d,cloudMean(tipVertices[side][d])]));
        return [side,{tips:Object.fromEntries(h.digits.map(d=>[d,tips[d].toArray()])),thumbDirection:tips.Thumb.clone().sub(world(normalized(side+'ThumbMetacarpal'))).normalize().toArray(),thumbPalmPlane:tips.Thumb.clone().sub(wrist).dot(basis),thumbIndexTipDistance:tips.Thumb.distanceTo(tips.Index),thumbPalmVertexDistance:geometry?nearest(tipVertices[side].Thumb,palmVertices[side]):null,thumbDigitDistances:geometry?Object.fromEntries(h.digits.filter(d=>d!=='Thumb').map(d=>[d,nearest(tipVertices[side].Thumb,digitVertices[side][d])])):null}];
      }));
      return {at:h.clock.currentTime,frame,joints,hands,finite:Object.values(joints).every(j=>[...j.local,...j.world,...j.worldQ,...(j.rawLocal||[]),...(j.rawWorld||[])].every(Number.isFinite)),preview:r.getHandPosePreview?.()??null,camera:{position:s.camera.position.toArray(),quaternion:s.camera.quaternion.toArray(),fov:s.camera.fov,aspect:s.camera.aspect}};
    };
    h.restFrame=()=>({name:null,weight:0,phase:0,strength:1,source:'preview',transition:{id:++h.sequence,reason:'start',from:'rest',to:'rest'}});
    h.reset=(enabled=true)=>{a.performance.stop();a.performance.sample();h.clock.currentTime=0;r.setHandPosePreview?.(null);r.setFingerRelaxation(enabled);for(const v of Object.values(h.bind))v.node.quaternion.copy(v.rest);for(const v of s.springBones)v.node.quaternion.copy(v.rest);r.poseTransition.reset();h.restoreCamera();const f=h.restFrame();for(let i=0;i<120;i++){r.setPerformance(f,false);h.quietDraw(h.neutral,1/60);}r.poseTransition.reset();};
    h.render=(frame,dt,params=h.neutral,visible=true)=>{r.setPerformance(frame,!!frame.reduced);h.quietDraw(params,dt);if(visible){h.refreshBounds();r.draw(params,0);}return h.measure(frame);};
    // Same bind-derived close cameras for all modes and stages; no per-pose fit.
    h.closeCameras={};for(const side of ['left','right']){const center=cloudMean(handVertices[side]);h.closeCameras[side]={center,position:center.clone().add(new THREE.Vector3(0,.07,.32))};}
    h.close=(side,view,params)=>{const c=h.closeCameras[side],dir=c.position.clone().sub(c.center);if(view==='side')dir.applyAxisAngle(new THREE.Vector3(0,1,0),Math.PI/3);s.renderer.setSize(560,480,false);s.camera.aspect=560/480;s.camera.fov=35;s.camera.position.copy(c.center).add(dir);s.camera.lookAt(c.center);s.camera.updateProjectionMatrix();h.refreshBounds();r.draw(params,0);const points=handVertices[side].map(v=>vertexWorld(v).project(s.camera));return {image:h.image(560,480),outside:points.filter(p=>Math.abs(p.x)>1||Math.abs(p.y)>1||p.z<-1||p.z>1).length,camera:{position:s.camera.position.toArray(),quaternion:s.camera.quaternion.toArray(),fov:s.camera.fov,aspect:s.camera.aspect}};};
    h.fixed=({name=null,strength=1,reduced=false,enabled=true,preview=null,side='right',overlay=false})=>{
      h.reset(enabled);const f={name,strength,reduced,source:'preview',phase:.5,weight:name?1:0,transition:{id:++h.sequence,reason:'start',from:'rest',to:name||'rest'}};
      const params=overlay?{...h.neutral,ParamAngleX:.25,ParamAngleY:-.2,ParamAngleZ:.1,ParamBodyAngleX:.15,ParamBodyAngleZ:-.1,ParamBodyLeanX:.15,ParamEyeBallX:.35,ParamEyeBallY:-.25}:h.neutral;
      r.setPerformance(f,reduced);if(preview)r.setHandPosePreview?.(preview,side);for(let i=0;i<120;i++)h.quietDraw(params,1/60);h.refreshBounds();r.draw(params,0);const sample=h.measure(f,true),images={front:h.image()},views={};
      for(const v of ['left60','right60']){s.renderer.setSize(840,720,false);s.camera.aspect=840/720;s.camera.position.copy(h.camera.position).sub(h.cameraCenter).multiplyScalar(1.3).applyAxisAngle(new THREE.Vector3(0,1,0),(v==='left60'?-1:1)*Math.PI/3).add(h.cameraCenter);s.camera.lookAt(h.cameraCenter);s.camera.updateProjectionMatrix();h.refreshBounds();r.draw(params,0);images[v]=h.image(840,720);}
      s.renderer.setSize(600,900,false);s.camera.aspect=600/900;s.camera.fov=35;s.camera.position.copy(h.fullCenter).add(new THREE.Vector3(0,0,h.fullDistance));s.camera.lookAt(h.fullCenter);s.camera.updateProjectionMatrix();h.refreshBounds();r.draw(params,0);images.full=h.image(600,900);
      // Hand-close cameras follow only the already-fixed hand bone placement for
      // this arm pose, not the finger silhouette. This anchor is unchanged by fingers.
      for(const handSide of ['left','right']){const anchor=world(normalized(handSide+'Hand'));const center=anchor.clone().add(world(normalized(handSide+'IndexProximal')).sub(anchor).add(world(normalized(handSide+'LittleProximal')).sub(anchor)).multiplyScalar(.65));h.closeCameras[handSide]={center,position:center.clone().add(new THREE.Vector3(0,.06,.40))};for(const view of ['front','side']){const close=h.close(handSide,view,params);images[`${handSide}-${view}`]=close.image;delete close.image;views[`${handSide}-${view}`]=close;}}
      h.restoreCamera();return {input:{name,strength,reduced,enabled,preview,side,overlay},sample,images,views};
    };
    h.dynamic=name=>{h.reset();const frames=[],images=[];a.performance.preview(name==='self-palm-rest'?'self':'wave',h.clock);const last=name==='self-palm-rest'?288:180;for(let i=0;i<=last;i++){h.clock.currentTime=name==='self-palm-rest'?i/60:Math.min(i,60)/60;if(name==='self-palm-rest'&&i===66)a.performance.preview('palm',h.clock);if(name==='wave-interrupt'&&i===60)a.performance.stop('interrupted');const f=a.performance.sample();frames.push(h.render(f,i?1/60:0,h.neutral,i%3===0));if(i%3===0)images.push(h.image());}return {name,frames,images};};
    h.switches=()=>{h.fixed({name:'palm',preview:'softGrip'});const f=r.performance,initial=h.measure(f);r.setHandPosePreview('open');r.draw(h.neutral,0);const atZero=h.measure(f);const frames=[];for(let i=0;i<60;i++)frames.push(h.render(f,1/60));r.setFingerRelaxation(false);r.draw(h.neutral,0);const disableZero=h.measure(f);for(let i=0;i<120;i++)h.render(f,1/60,h.neutral,false);const disabled=h.measure(f);r.setFingerRelaxation(true);r.draw(h.neutral,0);const enableZero=h.measure(f);for(let i=0;i<120;i++)h.render(f,1/60,h.neutral,false);return {initial,atZero,frames,disableZero,disabled,enableZero,enabled:h.measure(f)};};
    h.previewMovie=()=>{h.fixed({name:'palm',preview:'relaxed'});const f=r.performance,frames=[],images=[];for(let i=0;i<=240;i++){if(i===60)r.setHandPosePreview('open');if(i===120)r.setHandPosePreview('softGrip');if(i===180)r.setHandPosePreview(null);h.render(f,i?1/60:0,h.neutral,false);frames.push(h.measure(f));if(i%3===0)images.push(h.close('right','side',h.neutral).image);}h.restoreCamera();return {name:'relaxed-open-softGrip-auto',frames,images};};
  });
  const inputs=[{name:null}];for(const name of ['wave','deny','palm','self'])inputs.push({name},{name,strength:.4},{name,reduced:true});inputs.push({name:'palm',overlay:true});
  for(const input of inputs){const c=await page.evaluate(input=>window.handHarness.fixed(input),input);const stem=`${input.name||'rest'}-strength${c.input.strength}${input.reduced?'-reduced':''}${input.overlay?'-overlay':''}`;for(const [v,img]of Object.entries(c.images))await writeFile(`${out}/${stem}-${v}.jpg`,Buffer.from(img.split(',')[1],'base64'));delete c.images;assert(c.sample.finite);for(const [view,data]of Object.entries(c.views))assert.equal(data.outside,0,`${stem}/${view}: all actual hand-influenced vertices in frame`);captures.push({stem,...c});console.log(`${label}: ${stem}`);}
  for(const name of ['self-palm-rest','wave-interrupt']){const d=await page.evaluate(name=>window.handHarness.dynamic(name),name);assert(d.frames.every(f=>f.finite));movies[name]=d.images;delete d.images;dynamics.push(d);}
  await writeFile(`${out}/movies.json`,JSON.stringify(movies));checks.push('14 fixed actual auto hand renders with front/side hand close views; 470 finite dynamic pose samples at 60 Hz with actual 20 fps rasterization');
  let comparisons,previews,switches,nativeLab;
  if(!before){
    const baseline=JSON.parse(await readFile(`${root}/before/report.json`,'utf8'));assert.equal(await hash('web/models/seed-daily.vrm'),baseline.assetHash);comparisons=[];
    const unchanged=await page.evaluate(()=>window.handHarness.otherNames);
    const compare=(sample,old,stem)=>{for(const name of unchanged)if(sample.joints[name])assert(angle(sample.joints[name].local,old.joints[name].local)<1e-6,`${stem}/${name}: local arm/body/head unchanged`);assert(dist(sample.camera.position,old.camera.position)<1e-9&&angle(sample.camera.quaternion,old.camera.quaternion)<1e-7,'fixed main camera');return {stem,thumbAngles:Object.fromEntries(['left','right'].map(side=>[side,['Metacarpal','Proximal','Distal'].map(j=>angle(sample.joints[side+'Thumb'+j].local,old.joints[side+'Thumb'+j].local))])),regularMax:Math.max(...Object.keys(sample.joints).filter(n=>/Index|Middle|Ring|Little/.test(n)).map(n=>angle(sample.joints[n].local,old.joints[n].local)))};};
    for(const c of captures){const old=baseline.captures.find(v=>v.stem===c.stem);assert(old);comparisons.push(compare(c.sample,old.sample,c.stem));}
    assert(comparisons.every(c=>c.regularMax<1e-6),'automatic existing 24 ordinary-finger local poses unchanged');
    const restSample=captures[0].sample;for(const c of captures){for(const joint of ['Metacarpal','Proximal','Distal'])assert(angle(c.sample.joints['leftThumb'+joint].local,restSample.joints['leftThumb'+joint].local)<1e-6,'left thumb automatic relaxation is stable');}
    const thumbExpression=sample=>['Metacarpal','Proximal','Distal'].reduce((sum,j)=>sum+angle(sample.joints['rightThumb'+j].local,restSample.joints['rightThumb'+j].local),0);
    for(const name of ['wave','deny','palm']){const full=captures.find(c=>c.stem===`${name}-strength1`).sample,soft=captures.find(c=>c.stem===`${name}-strength0.4`).sample,reduced=captures.find(c=>c.stem===`${name}-strength1-reduced`).sample;assert(thumbExpression(full)>.04,'automatic right thumb follows open gesture');assert(thumbExpression(soft)<thumbExpression(full),'low strength reduces thumb expression');assert(thumbExpression(reduced)<thumbExpression(full)*.55,'reduced mode limits automatic thumb expression');}
    assert(thumbExpression(captures.find(c=>c.stem==='self-strength1').sample)<1e-6,'self thumb stays relaxed');
    for(const d of dynamics){const old=baseline.dynamics.find(v=>v.name===d.name);assert.equal(d.frames.length,old.frames.length);d.comparisons=d.frames.map((f,i)=>compare(f,old.frames[i],`${d.name}/${i}`));for(let i=1;i<d.frames.length;i++)for(const name of Object.keys(d.frames[i].joints).filter(n=>/Thumb|Index|Middle|Ring|Little/.test(n)))assert(angle(d.frames[i].joints[name].local,d.frames[i-1].joints[name].local)<.08,`${d.name}/${i}/${name}: bounded actual frame step`);const end=d.frames.at(-1),rest=captures[0].sample;for(const name of Object.keys(end.joints).filter(n=>/Thumb|Index|Middle|Ring|Little/.test(n)))assert(angle(end.joints[name].local,rest.joints[name].local)<.003,`${name}: rest recovery`);if(d.name==='wave-interrupt')assert(d.frames.slice(60).every(f=>f.at===1),'renderer-dt recovery under frozen audio');}
    assert(comparisons.some(c=>c.thumbAngles.right.some(v=>v>.005)),'actual right thumb moves');assert(comparisons.some(c=>c.thumbAngles.left.some(v=>v>.005)),'actual left thumb relaxes');checks.push('auto layer reaches both thumb chains; matched local arm/body/head behavior preserved; actual finite hand continuity and frozen-clock recovery');
    previews=[];for(const side of ['right','left'])for(const preview of ['relaxed','open','softGrip']){const input={name:'palm',preview,side};const c=await page.evaluate(input=>window.handHarness.fixed(input),input),stem=`preview-${side}-${preview}`;for(const [v,img]of Object.entries(c.images))await writeFile(`${out}/${stem}-${v}.jpg`,Buffer.from(img.split(',')[1],'base64'));delete c.images;assert(c.sample.finite);for(const [view,data]of Object.entries(c.views))assert.equal(data.outside,0,`${stem}/${view}: all hand vertices in frame`);assert.equal(c.sample.preview.name,preview);assert.equal(c.sample.preview.side,side);const automatic=captures.find(v=>v.stem==='palm-strength1').sample;for(const name of unchanged)if(c.sample.joints[name])assert(angle(c.sample.joints[name].local,automatic.joints[name].local)<1e-6,`${stem}/${name}: preview preserves local parent animation`);previews.push({stem,...c});console.log(`${label}: ${stem}`);}
    for(const side of ['left','right']){const a=previews.find(v=>v.input.side===side&&v.input.preview==='open').sample,b=previews.find(v=>v.input.side===side&&v.input.preview==='softGrip').sample;assert(Object.keys(a.joints).filter(n=>n.startsWith(side)&&/Index|Middle|Ring|Little/.test(n)).some(n=>angle(a.joints[n].local,b.joints[n].local)>.15),'open versus softGrip reaches actual regular fingers');assert(['Metacarpal','Proximal','Distal'].some(j=>angle(a.joints[side+'Thumb'+j].local,b.joints[side+'Thumb'+j].local)>.005),'preview thumb variation reaches actual model');}
    for(const c of previews){const automatic=captures.find(v=>v.stem==='palm-strength1').sample;for(const name of Object.keys(c.sample.joints).filter(n=>n.startsWith(c.input.side==='right'?'left':'right')&&/Thumb|Index|Middle|Ring|Little/.test(n)))assert(angle(c.sample.joints[name].local,automatic.joints[name].local)<1e-6,'preview targets only selected hand');}
    const reducedPreview=await page.evaluate(()=>window.handHarness.fixed({name:'palm',preview:'softGrip',reduced:true}));for(const name of Object.keys(reducedPreview.sample.joints).filter(n=>n.startsWith('right')&&/Thumb|Index|Middle|Ring|Little/.test(n)))assert(angle(reducedPreview.sample.joints[name].local,previews.find(c=>c.input.side==='right'&&c.input.preview==='softGrip').sample.joints[name].local)<1e-6,'manual inspection target remains fixed in reduced mode');
    checks.push('six right/left actual relaxed/open/softGrip poses and skinned tip/digit/palm spacing observations; previews preserve local arms and torso');
    switches=await page.evaluate(()=>window.handHarness.switches());for(const name of Object.keys(switches.initial.joints))assert.deepEqual(switches.initial.joints[name].local,switches.atZero.joints[name].local,'preview change dt0 preserves actual joint');for(const name of Object.keys(switches.disabled.joints)){assert.deepEqual(switches.disableZero.joints[name].local,switches.frames.at(-1).joints[name].local,'disable dt0 preserves actual joint');assert.deepEqual(switches.enableZero.joints[name].local,switches.disabled.joints[name].local,'enable dt0 preserves actual joint');if(/Thumb|Index|Middle|Ring|Little/.test(name))assert(switches.disabled.joints[name].restAngle<1e-6,'master disable recovers bind');}assert.equal(switches.disabled.preview.name,null);for(let i=1;i<switches.frames.length;i++)for(const name of Object.keys(switches.frames[i].joints).filter(n=>/Thumb|Index|Middle|Ring|Little/.test(n)))assert(angle(switches.frames[i].joints[name].local,switches.frames[i-1].joints[name].local)<.20,'hand preview switch bounded actual frame steps');checks.push('preview switch and master disable/enable preserve dt0; disabling all 30 joints recovers bind and clears preview');
    await page.evaluate(()=>{const a=window.__avatar;a.onBargeIn();a.svg.setFingerRelaxation(true);if(!a._raf)a._start();});
    const manualMovie=await page.evaluate(()=>window.handHarness.previewMovie());movies[manualMovie.name]=manualMovie.images;delete manualMovie.images;dynamics.push(manualMovie);await writeFile(`${out}/movies.json`,JSON.stringify(movies));
    await page.locator('#handPoseSoftGrip').click();await page.waitForFunction(()=>{const a=window.__avatar;return a.svg.getHandPosePreview().name==='softGrip'&&window.handHarness.measure(a.performance.sample()).joints.rightIndexProximal.restAngle>.20;});
    const interruption=await page.evaluate(async()=>{const a=window.__avatar,h=window.handHarness;await h.nativeCtx.suspend();const before=h.measure(a.performance.sample()),at=h.nativeCtx.currentTime;a.onBargeIn();a.svg.setPerformance(a.performance.sample(),false);a.svg.draw(h.neutral,0);return {before,at,zero:h.measure(a.performance.sample()),queueLength:a.performance.queue.length};});
    const physical=await page.evaluate(()=>[...window.handHarness.fingerNames,...Object.keys(window.__avatar.svg._state.arms),'chest','rightShoulder']);for(const name of physical)assert.deepEqual(interruption.before.joints[name].local,interruption.zero.joints[name].local,`${name}: native interruption dt0`);assert.equal(interruption.zero.preview.name,null);assert.equal(interruption.queueLength,0);
    const rest=captures[0].sample;await page.waitForFunction(rest=>{const h=window.handHarness,s=h.measure(window.__avatar.performance.sample());return h.fingerNames.every(n=>s.joints[n].local.every((v,i)=>Math.abs(v-rest.joints[n].local[i])<.002));},rest);
    const recovered=await page.evaluate(()=>({...window.handHarness.measure(window.__avatar.performance.sample()),audioAt:window.handHarness.nativeCtx.currentTime}));assert.equal(recovered.audioAt,interruption.at);
    await page.locator('#handPoseOpen').click();assert.equal(await page.evaluate(()=>window.__avatar.svg.getHandPosePreview().name),'open');await page.locator('#handPoseRelaxed').click();assert.equal(await page.evaluate(()=>window.__avatar.svg.getHandPosePreview().name),'relaxed');await page.locator('#handPoseAuto').click();assert.equal(await page.evaluate(()=>window.__avatar.svg.getHandPosePreview().name),null);
    await page.locator('#handPoseOpen').click();await page.locator('#fingerRelaxation').uncheck();assert.equal(await page.evaluate(()=>window.__avatar.svg.getHandPosePreview().name),null);assert(await page.locator('#handPoseOpen').isDisabled());await page.locator('#fingerRelaxation').check();assert(!(await page.locator('#handPoseOpen').isDisabled()));
    await page.locator('#handPoseOpen').click();await page.locator('#stopScenario').click();assert.equal(await page.evaluate(()=>window.__avatar.svg.getHandPosePreview().name),null);
    await page.evaluate(()=>{const h=window.handHarness;h.resumeOriginal=h.nativeCtx.resume.bind(h.nativeCtx);h.nativeCtx.resume=()=>{h.resumePending=true;return new Promise(resolve=>{h.resumeRelease=async()=>{await h.resumeOriginal();resolve();};});};});
    await page.locator('#handPoseSoftGrip').click();await page.waitForFunction(()=>window.handHarness.resumePending);await page.locator('#stopScenario').click();
    await page.evaluate(async()=>{const h=window.handHarness;await h.resumeRelease();h.nativeCtx.resume=h.resumeOriginal;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
    const deferredResume=await page.evaluate(()=>({preview:window.__avatar.svg.getHandPosePreview(),queueLength:window.__avatar.performance.queue.length}));assert.equal(deferredResume.preview.name,null,'pending AudioContext.resume callback does not resurrect a stopped preview');assert.equal(deferredResume.queueLength,0);
    await page.locator('#handPoseStatus').screenshot({path:`${out}/lab-status.png`});nativeLab={interruption,recovered,deferredResume};checks.push('actual lab buttons/master switch/stop, native suspended AudioContext interruption and deferred resume cancellation');
  }
  assert.deepEqual(errors,[]);assert.deepEqual(forbiddenRequests,[]);assert.equal(await page.evaluate(()=>window.handHarness.microphoneRequests),0);checks.push('no browser errors, mic, speech/model calls or API mutation');
  const report={label,createdAt:new Date().toISOString(),assetHash:await hash('web/models/seed-daily.vrm'),sourceHashes,geometry:await page.evaluate(()=>window.handHarness.geometry),modelHeight:await page.evaluate(()=>window.handHarness.modelHeight),captures,dynamics,comparisons,previews,switches,nativeLab,checks,errors,forbiddenRequests,method:'production draw and VRM pipeline at 60 Hz, actual captured dynamic images at 20 fps; fixed main/oblique cameras and hand-bone-anchored close cameras; active indexed vertices and refreshed skinned bounds',limits:'Nearest vertex distances and palm-plane directions are geometric observations, not surface collision proof or a contact solver; low-poly fingers/mesh can retain authored visual limitations.'};
  await writeFile(`${out}/report.json`,JSON.stringify(report,null,2));console.log(`Hand poses ${label}: ${checks.length} groups passed; ${out}/report.json`);
}finally{await browser.close();}
