// Reproduce the legacy unweighted spring world-cache behavior on frozen/current code.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {chromium} from '@playwright/test';
const root='eval/avatar/face-shading';
if (process.argv.length === 2 && !existsSync(`${root}/before/vrm.js`)) {
  console.log('SKIP face-calibration-cache: 缺少 before 源快照夹具（本地证据未入 git；重新捕获后恢复）');
  process.exit(0);
}
const reports=[];
for(const label of ['before','after']) {
  const browser=await chromium.launch({headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1320,height:960}});const errors=[],writes=[],sourceHashes={};
  page.on('pageerror',e=>errors.push(e.message));
  try {
    for(const [name,path] of Object.entries({'vrm.js':'avatar/render/vrm.js','eye-morph.js':'avatar/render/eye-morph.js','calibration.js':'avatar/calibration.js','avatar_lab.js':'avatar_lab.js','avatar_lab.html':'avatar_lab.html'})) {
      const bytes=await readFile(label==='before'?`${root}/before/${name}`:`web/${path}`);
      sourceHashes[`web/${path}`]=createHash('sha256').update(bytes).digest('hex');
      await page.route(`**/static/${path}`,route=>route.fulfill({body:bytes,contentType:name.endsWith('.html')?'text/html':'text/javascript'}));
    }
    await page.route('**/api/**',route=>{
      if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort();}
      return new URL(route.request().url()).pathname==='/api/avatar'?route.continue():route.fulfill({json:{count:0,items:[],context:''}});
    });
    await page.addInitScript(()=>{window.cacheMicRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=()=>{window.cacheMicRequests++;return Promise.reject(Error('No mic'));};});
    await page.goto('http://127.0.0.1:8000/static/avatar_lab.html?model=daily');
    await page.waitForFunction(()=>window.__avatarReady&&window.__avatar.svg._state?.vrm?.meta.name==='Seed-san Daily Study',{}, {timeout:90000});
    const result=await page.evaluate(async()=>{
      const {AvatarCalibration}=await import('/static/avatar/calibration.js');
      const a=window.__avatar,r=a.svg,s=r._state;
      if(a._raf)cancelAnimationFrame(a._raf);a._raf=0;a.performance.stop();a.lip.stop();s.observer?.disconnect();
      r.setPresentation(true);r.setEyeFocus(true);r.setSecondaryMotion(0);s.renderer.setPixelRatio(1);s.renderer.setSize(560,680,false);s.camera.aspect=560/680;r._frameCamera(s);
      s.key.position.set(3,1.6,.8);
      const activeWeights=new Map();
      s.vrm.scene.traverse(mesh=>{
        if(!mesh.isSkinnedMesh)return;const indices=mesh.geometry.getAttribute('skinIndex'),weights=mesh.geometry.getAttribute('skinWeight');
        if(!indices||!weights)return;
        const referenced=mesh.geometry.index?[...new Set(mesh.geometry.index.array)]:Array.from({length:indices.count},(_,i)=>i);
        for(const vertex of referenced)for(const get of ['getX','getY','getZ','getW'])if(weights[get](vertex)>0){const name=mesh.skeleton.bones[indices[get](vertex)]?.name;if(name)activeWeights.set(name,(activeWeights.get(name)||0)+1);}
      });
      const measure=()=>{
        s.vrm.scene.updateMatrixWorld(true);const nodes=[],joints={};
        s.vrm.scene.traverse(node=>nodes.push({name:node.name,type:node.type,matrixAutoUpdate:node.matrixAutoUpdate,p:node.position.toArray(),q:node.quaternion.toArray(),scale:node.scale.toArray(),world:node.matrixWorld.toArray(),morphs:node.morphTargetInfluences?[...node.morphTargetInfluences]:null}));
        for(const name of Object.keys(s.vrm.humanoid.normalizedHumanBones))for(const mode of ['normalized','raw']){const node=mode==='raw'?s.vrm.humanoid.getRawBoneNode(name):s.vrm.humanoid.getNormalizedBoneNode(name);if(node)joints[`${mode}/${name}`]={p:node.position.toArray(),q:node.quaternion.toArray(),world:node.matrixWorld.toArray()};}
        return {nodes,joints};
      };
      const cal=new AvatarCalibration(a);cal.enter();cal.pose({expression:'daily'});const base={...cal.params};
      // Same final 3-second production motion used in the full face report.
      for(let i=0;i<=180;i++){const t=i/60,phase=Math.sin(t*Math.PI*2/3),blink=Math.max(0,1-Math.abs(t-.75)/.1),opening=t>1&&t<2.5?.62*Math.sin((t-1)*Math.PI/1.5)**2:0;
        r.draw({...base,ParamAngleX:.82*phase,ParamAngleY:.12*Math.sin(t*Math.PI*2/3),ParamEyeLOpen:base.ParamEyeLOpen*(1-blink),ParamEyeROpen:base.ParamEyeROpen*(1-blink),ParamEyeBallX:.45*Math.sin(t*Math.PI*2/3+.3),ParamEyeBallY:.10*Math.sin(t*Math.PI*2),ParamMouthOpenY:opening,ParamMouthForm:.18,ParamMouthRound:.25},1/60);
      }
      cal.exit();r.setFaceShading?.(false);const before=measure();
      const next=new AvatarCalibration(a);next.enter();next.pose({expression:'happy',mouthOpen:.4,blink:.3});r.setFaceShading?.(true);next.exit();const after=measure();
      const delta=(x,y)=>Math.max(...x.map((v,i)=>Math.abs(v-y[i])));
      const differences=before.nodes.map((node,i)=>({name:node.name,type:node.type,matrixAutoUpdate:node.matrixAutoUpdate,activeIndexedPositiveWeightRecords:activeWeights.get(node.name)||0,
        worldDelta:delta(node.world,after.nodes[i].world),positionDelta:delta(node.p,after.nodes[i].p),quaternionDelta:delta(node.q,after.nodes[i].q),scaleDelta:delta(node.scale,after.nodes[i].scale),beforeWorld:node.world,afterWorld:after.nodes[i].world})).filter(x=>x.worldDelta>1e-10);
      const jointDifferences=Object.entries(before.joints).map(([name,node])=>({name,worldDelta:delta(node.world,after.joints[name].world),qDelta:delta(node.q,after.joints[name].q)})).filter(x=>x.worldDelta>1e-10||x.qDelta>1e-10);
      const visibleMeshChanges=before.nodes.filter(node=>['Mesh','SkinnedMesh'].includes(node.type)).map(node=>{const i=before.nodes.indexOf(node);return {name:node.name,worldDelta:delta(node.world,after.nodes[i].world)};}).filter(x=>x.worldDelta>1e-10);
      return {differences,jointDifferences,visibleMeshChanges,microphoneRequests:window.cacheMicRequests,activeWeightMethod:'count positive skinWeight slots only on unique vertices referenced by geometry.index, all loaded skinned meshes; absence is zero',before,after};
    });
    assert(result.differences.length>0,'legacy world-cache behavior reproduces');
    assert(result.differences.every(x=>x.activeIndexedPositiveWeightRecords===0&&x.positionDelta===0&&x.quaternionDelta===0&&x.scaleDelta===0&&!['Mesh','SkinnedMesh'].includes(x.type)));
    assert.deepEqual(result.jointDifferences,[]);assert.deepEqual(result.visibleMeshChanges,[]);assert.equal(result.microphoneRequests,0);assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
    reports.push({label,sourceHashes,...result,errors,writes});console.log(`${label}: ${result.differences.length} unweighted legacy cache nodes; no local/mapped/visible mesh changes`);
  } finally {await browser.close();}
}
await writeFile(`${root}/legacy-calibration-cache.json`,JSON.stringify({checks:['same production motion plus calibration exit reproduces legacy cache behavior in frozen-before and current source','all changed world caches are zero-weight non-visible nodes; local transforms and mapped joint/visible-mesh worlds unchanged'],reports},null,2));
console.log(`PASS legacy baseline reproduction -> ${root}/legacy-calibration-cache.json`);
