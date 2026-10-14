// Offline pose authoring. Resolves desired limb/palm directions into static
// rest-relative XYZ offsets; it does not add an IK solver to the runtime.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const pathAuthoring = process.argv.includes('--paths');
const out = pathAuthoring ? 'eval/avatar/gesture-path-authoring' : 'eval/avatar/gesture-authoring';
const directions = {
  wave: { upper: [-.55, -.52, .65], lower: [-.04, .96, .28], fingers: [-.08, .98, .18], palm: [0, -.18, .98] },
  deny: { upper: [-.35, -.88, .30], lower: [-.15, .70, .70], fingers: [-.04, .88, .46], palm: [0, -.46, .88] },
  self: { upper: [-.62, -.73, .29], lower: [.70, .50, .51], fingers: [.95, .16, 0], palm: [0, 0, -1] },
  palm: { upper: [-.40, -.80, .45], lower: [.08, .18, .98], fingers: [-.50, -.295, .814], palm: [0, .94, .342] },
};
const softDirections = {
  wave: { upper: [-.35, -.70, .62], lower: [-.10, .95, .29], fingers: [-.08, .98, .18], palm: [0, -.18, .98] },
  deny: { upper: [-.30, -.90, .30], lower: [-.12, .62, .77], fingers: [-.04, .88, .46], palm: [0, -.46, .88] },
  self: { upper: [-.40, -.85, .34], lower: [.45, .57, .68], fingers: [.95, .16, 0], palm: [0, 0, -1] },
  palm: { upper: [-.24, -.91, .34], lower: [.04, .25, .97], fingers: [-.42, -.31, .848], palm: [0, .94, .342] },
};
const prepareDirections = {
  wave: { upper: [-.36, -.81, .46], lower: [-.12, .55, .826], fingers: [-.12, .55, .826], palm: [1, 0, 0] },
  deny: { upper: [-.25, -.91, .32], lower: [-.08, .33, .94], fingers: [-.08, .33, .94], palm: [1, 0, 0] },
  self: { upper: [-.25, -.93, .27], lower: [-.10, .34, .935], fingers: [-.10, .34, .935], palm: [1, 0, 0] },
  palm: { upper: [-.27, -.91, .31], lower: [-.05, .15, .987], fingers: [-.05, .15, .987], palm: [1, 0, 0] },
};
const prepareSoftDirections = {
  wave: { upper: [-.26, -.86, .44], lower: [-.05, .46, .88], fingers: [-.05, .46, .88], palm: [1, 0, 0] },
  deny: { upper: [-.20, -.94, .28], lower: [-.05, .29, .957], fingers: [-.05, .29, .957], palm: [1, 0, 0] },
  self: { upper: [-.19, -.945, .25], lower: [-.05, .28, .96], fingers: [-.05, .28, .96], palm: [1, 0, 0] },
  palm: { upper: [-.19, -.95, .25], lower: [-.03, .19, .981], fingers: [-.03, .19, .981], palm: [1, 0, 0] },
};
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] });
try {
  await mkdir(out, { recursive: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  const authored = await page.evaluate(async ({directions,softDirections,prepareDirections,prepareSoftDirections,pathAuthoring}) => {
    const T = await import('three');
    const avatar = window.__avatar, rig = avatar.svg, state = rig._state;
    cancelAnimationFrame(avatar._raf); avatar._raf = 0;
    avatar.stopSpeaking(); avatar.setEmotion('base'); avatar.setGazeMode('forward');
    rig.setEyeFocus(false); rig.setSecondaryMotion(0); rig.setPerformance({weight:0});
    const neutral = {ParamEyeLOpen:1, ParamEyeROpen:1};
    const humanoid = state.vrm.humanoid;
    const bones = Object.fromEntries(['rightUpperArm','rightLowerArm','rightHand','rightIndexProximal','rightMiddleProximal','rightLittleProximal'].map(n => [n, humanoid.getNormalizedBoneNode(n)]));
    const localFinger = bones.rightMiddleProximal.position.clone().normalize();
    const localPalm = bones.rightIndexProximal.position.clone().sub(bones.rightLittleProximal.position).cross(bones.rightMiddleProximal.position).normalize();
    const basis = (forward, normal) => {
      const f = forward.clone().normalize(), n = normal.clone().addScaledVector(f, -normal.dot(f)).normalize();
      return new T.Quaternion().setFromRotationMatrix(new T.Matrix4().makeBasis(f, n, f.clone().cross(n)));
    };
    const rest = () => { for(const b of Object.values(state.arms)) b.node.quaternion.copy(b.rest); rig.draw(neutral,0); };
    const sync = () => { humanoid.update(); state.scene.updateMatrixWorld(true); };
    const fit = d => {
      rest();
      const upper = bones.rightUpperArm, lower = bones.rightLowerArm, hand = bones.rightHand;
      const upperDirection = new T.Vector3(...d.upper).normalize().applyQuaternion(upper.parent.getWorldQuaternion(new T.Quaternion()).invert());
      upper.quaternion.setFromUnitVectors(lower.position.clone().normalize(), upperDirection); sync();
      const lowerWorld = basis(new T.Vector3(...d.lower), new T.Vector3(...d.palm))
        .multiply(basis(hand.position.clone(), localPalm).invert());
      lower.quaternion.copy(lower.parent.getWorldQuaternion(new T.Quaternion()).invert().multiply(lowerWorld)); sync();
      const handWorld = basis(new T.Vector3(...d.fingers), new T.Vector3(...d.palm))
        .multiply(basis(localFinger,localPalm).invert());
      hand.quaternion.copy(hand.parent.getWorldQuaternion(new T.Quaternion()).invert().multiply(handWorld)); sync();
      const offsets = Object.fromEntries(['rightUpperArm','rightLowerArm','rightHand'].map(name => {
        const delta = state.arms[name].rest.clone().invert().multiply(bones[name].quaternion);
        return [name,new T.Euler().setFromQuaternion(delta,'XYZ').toArray().slice(0,3)];
      }));
      const points = Object.fromEntries(Object.entries(bones).map(([name,node]) => [name,node.getWorldPosition(new T.Vector3()).toArray()]));
      return {offsets,points,palmNormal:localPalm.clone().applyQuaternion(hand.getWorldQuaternion(new T.Quaternion())).toArray()};
    };
    const result = Object.fromEntries(Object.entries(directions).map(([name,d]) => [name,fit(d)]));
    const softResult = Object.fromEntries(Object.entries(softDirections).map(([name,d]) => [name,fit(d)]));
    const prepared = Object.fromEntries(Object.entries(prepareDirections).map(([name,d]) => [name,fit(d)]));
    const preparedSoft = Object.fromEntries(Object.entries(prepareSoftDirections).map(([name,d]) => [name,fit(d)]));
    window.authorPose = (name, strength = 1, yaw = 0) => {
      rest();
      const fullSet = pathAuthoring ? prepared : result, softSet = pathAuthoring ? preparedSoft : softResult;
      for(const [key,angles] of Object.entries(fullSet[name].offsets)) {
        const mix=Math.max(0,Math.min(1,(strength-.15)/.85));
        const b=state.arms[key], soft=softSet[name].offsets[key];
        b.node.quaternion.copy(b.rest).multiply(new T.Quaternion().setFromEuler(new T.Euler(...angles.map((v,i)=>soft[i]+(v-soft[i])*mix))));
      }
      const center=state.framing.center, distance=state.camera.position.distanceTo(center);
      state.camera.position.copy(center).add(new T.Vector3(Math.sin(yaw)*distance,0,Math.cos(yaw)*distance));
      state.camera.lookAt(center); state.camera.updateProjectionMatrix();
      rig.draw(neutral,0); document.querySelector('#caption').textContent=`${name} · ${pathAuthoring ? 'preparation key' : 'target'} · strength ${strength}`;
    };
    return {poses:result,softPoses:softResult,preparedPoses:prepared,preparedSoftPoses:preparedSoft};
  }, {directions,softDirections,prepareDirections,prepareSoftDirections,pathAuthoring});
  await writeFile(`${out}/poses.json`, JSON.stringify({ directions,softDirections,prepareDirections,prepareSoftDirections,...authored },null,2));
  for(const name of process.argv.includes('--palm-only') ? ['palm'] : Object.keys(directions)) {
    for(const [view,yaw] of [['front',0],['left',-.4363],['right',.4363]]) {
      await page.evaluate(([name,yaw]) => window.authorPose(name,1,yaw),[name,yaw]);
      await page.locator('#viewport').screenshot({path:`${out}/${name}-${view}.png`});
    }
    await page.evaluate(name=>window.authorPose(name,.4,0),name);
    await page.locator('#viewport').screenshot({path:`${out}/${name}-soft.png`});
  }
  console.log(`Authored ${Object.keys(authored.poses).length} full and restrained poses: ${out}/poses.json`);
} finally { await browser.close(); }
